package fetcher

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/rest"
	toolscache "k8s.io/client-go/tools/cache"
	"k8s.io/client-go/util/retry"
	"sigs.k8s.io/controller-runtime/pkg/cache"
	"sigs.k8s.io/controller-runtime/pkg/client"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
)

// Access is whether requests to the SEC are allowed, as the SecAccess
// cluster says (spec/controls.md, section 8), and where the fetcher records
// a stop after a 403 (ACCESS-3).
type Access interface {
	// Allowed says whether requests are allowed: while the SecAccess
	// exists, isn't paused, and has no stop the owner hasn't acknowledged
	// (ACCESS-1).
	Allowed() error
	// Stop records a stop in the SecAccess's status, in place of any earlier
	// one. From when it's called, Allowed refuses requests, until the
	// SecAccess shows the stop acknowledged (ACCESS-3).
	Stop(ctx context.Context, stop v1alpha1.Stop) error
}

// Refusals by Allowed, which say why requests aren't allowed.
var (
	ErrNoSecAccess     = errors.New("there is no SecAccess cluster")
	ErrRequestsStopped = errors.New("the SecAccess has a stop the owner hasn't acknowledged")
	ErrRequestsPaused  = errors.New("the SecAccess is paused")
	ErrNotRead         = errors.New("the SecAccess hasn't been read yet")
)

// SecAccess is the SecAccess cluster as this replica reads it from the API
// server, through an informer of its own, and writes its status. Its
// informer starts when the replica becomes the active one, so what it reads
// first is the SecAccess as it is then, a stop that the replica before it
// recorded included (ACCESS-3). It's granted RBAC-2's verbs on the
// SecAccess, and no others (FETCHER-4).
type SecAccess struct {
	cache  cache.Cache
	client client.Client

	mu sync.Mutex
	// synced is whether the informer has read the SecAccess, and fail, until
	// it has, stops the informer with the error of a request that failed to
	// read it (Start).
	synced bool
	fail   context.CancelCauseFunc
	// stop is a stop this replica has begun to record, which its informer
	// doesn't show yet.
	stop *v1alpha1.Stop
}

// NewSecAccess gives the SecAccess, read through an informer that Start
// starts.
func NewSecAccess(config *rest.Config) (*SecAccess, error) {
	scheme := runtime.NewScheme()
	if err := v1alpha1.AddToScheme(scheme); err != nil {
		return nil, err
	}
	s := &SecAccess{}
	informers, err := cache.New(config, cache.Options{
		Scheme: scheme,
		ByObject: map[client.Object]cache.ByObject{
			&v1alpha1.SecAccess{}: {Field: fields.OneTermEqualSelector("metadata.name", v1alpha1.SecAccessName)},
		},
		DefaultWatchErrorHandler: s.failed,
	})
	if err != nil {
		return nil, err
	}
	writer, err := client.New(config, client.Options{Scheme: scheme})
	if err != nil {
		return nil, err
	}
	s.cache, s.client = informers, writer
	return s, nil
}

// Start runs the informer until ctx is done, and returns once it has read
// the SecAccess, or ctx is done first. If a request to read it fails first,
// or the informer still hasn't read it once the time within has passed, it
// stops the informer and returns why, rather than try again while the
// replica holds the Lease, as the owner confirmed (T23-D17). The bound
// (T23-D24) ends what the handler of failed requests can't: client-go's
// informer, which streams its first read by default, tries again a read
// that gets a 429, or whose connection is refused, without end and without
// calling the handler (failed).
func (s *SecAccess) Start(ctx context.Context, within time.Duration) error {
	if _, err := s.cache.GetInformer(ctx, &v1alpha1.SecAccess{}); err != nil {
		return err
	}
	running, fail := context.WithCancelCause(ctx)
	s.mu.Lock()
	s.fail = fail
	s.mu.Unlock()
	go func() {
		// It stops only once running is done.
		_ = s.cache.Start(running)
	}()
	reading, cancel := context.WithTimeoutCause(running, within,
		fmt.Errorf("the informer hadn't read the SecAccess within %s", within))
	defer cancel()
	read := s.cache.WaitForCacheSync(reading)
	s.mu.Lock()
	defer s.mu.Unlock()
	if !read || running.Err() != nil {
		// Why reading ended: running's cause, if it's done, or the bound.
		fail(context.Cause(reading))
		return context.Cause(running)
	}
	s.synced = true
	return nil
}

// failed is the informer's handler of a request to list or watch the
// SecAccess that failed. Until the informer has read the SecAccess, it
// stops the informer with the request's error, so Start returns it; after,
// the informer tries again, as it does by default.
func (s *SecAccess) failed(ctx context.Context, r *toolscache.Reflector, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.synced && s.fail != nil {
		s.fail(err)
		return
	}
	toolscache.DefaultWatchErrorHandler(ctx, r, err)
}

// Allowed says whether requests are allowed, as the informer last read the
// SecAccess.
func (s *SecAccess) Allowed() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if !s.synced {
		return ErrNotRead
	}
	access := &v1alpha1.SecAccess{}
	// The informer has read the SecAccess, so Get reads what it holds.
	err := s.cache.Get(context.Background(), client.ObjectKey{Name: v1alpha1.SecAccessName}, access)
	if apierrors.IsNotFound(err) {
		return ErrNoSecAccess
	}
	if err != nil {
		return err
	}
	if s.stop != nil {
		if access.Status.Stop == nil || access.Status.Stop.At != s.stop.At {
			return ErrRequestsStopped
		}
		// The informer shows the stop now, so it says from here on
		// whether the owner has acknowledged it.
		s.stop = nil
	}
	return allowed(access)
}

// allowed says whether a SecAccess allows requests, and if not, why not, in
// the order the scheduler gives its reasons (SOURCE-1).
func allowed(access *v1alpha1.SecAccess) error {
	switch {
	case access.Stopped():
		return ErrRequestsStopped
	case access.Spec.Paused:
		return ErrRequestsPaused
	default:
		return nil
	}
}

// Stop records the stop in the SecAccess's status, which it reads from the
// API server, not the informer, so its update is of the latest.
func (s *SecAccess) Stop(ctx context.Context, stop v1alpha1.Stop) error {
	s.mu.Lock()
	s.stop = &stop
	s.mu.Unlock()
	return retry.RetryOnConflict(retry.DefaultRetry, func() error {
		access := &v1alpha1.SecAccess{}
		if err := s.client.Get(ctx, client.ObjectKey{Name: v1alpha1.SecAccessName}, access); err != nil {
			return err
		}
		if access.Status.Stop != nil && *access.Status.Stop == stop {
			return nil
		}
		access.Status.Stop = &stop
		return s.client.Status().Update(ctx, access)
	})
}
