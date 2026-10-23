// Package scheduler is the controller that reconciles the pipeline's controls
// (spec/controls.md): it queues each Source's polls at its cadence, and keeps
// each Source's status. Of its two replicas, only the one that holds its
// Lease acts (spec/deployment.md, SCHED-4).
package scheduler

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/go-logr/logr"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/leaderelection/resourcelock"
	"k8s.io/client-go/util/retry"
	"k8s.io/utils/clock"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/cache"
	"sigs.k8s.io/controller-runtime/pkg/config"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
)

// Options configures the scheduler.
type Options struct {
	// Namespace is the release namespace: the Sources' and the Lease's.
	Namespace string
	// LeaseName names its Lease, and the Lease's timings are client-go's
	// leader election's (SCHED-3).
	LeaseName     string
	LeaseDuration time.Duration
	RenewDeadline time.Duration
	RetryPeriod   time.Duration
	// Identity is this replica's in the Lease.
	Identity string
	// MetricsAddress and HealthAddress are where it serves its metrics and
	// its health checks; "0" serves neither.
	MetricsAddress string
	HealthAddress  string
	// GracefulShutdownTimeout bounds how long it waits for its work to stop
	// before it releases the Lease, within its pod's grace period; if the
	// work hasn't stopped by then, it leaves the Lease to expire (SCHED-4).
	GracefulShutdownTimeout time.Duration
	// JetStream is NATS's, through which it reads poll events, and queues
	// polls unless Publisher is given.
	JetStream jetstream.JetStream
	Publisher Publisher
	Clock     clock.WithTicker
	Log       logr.Logger
	// SkipNameValidation lets more than one scheduler run in a process, as
	// its tests run them.
	SkipNameValidation bool
}

// NewManager returns the scheduler as a controller-runtime manager. Its
// reconciler, its Poller, and its PollEvents run only while it holds the
// Lease, and stop once the renewal deadline has passed since it last renewed
// the Lease, before another replica can take the Lease over. When its
// context is done, as when Kubernetes stops its pod, it stops them, then
// releases the Lease, so another replica takes it over without waiting out
// the Lease's duration (SCHED-4). It records no Kubernetes Events, which it
// isn't granted (SCHED-6).
func NewManager(restConfig *rest.Config, opts Options) (ctrl.Manager, error) {
	scheme := runtime.NewScheme()
	if err := v1alpha1.AddToScheme(scheme); err != nil {
		return nil, err
	}
	clients, err := kubernetes.NewForConfig(restConfig)
	if err != nil {
		return nil, err
	}
	leaseLock, err := resourcelock.New(resourcelock.LeasesResourceLock, opts.Namespace, opts.LeaseName,
		clients.CoreV1(), clients.CoordinationV1(), resourcelock.ResourceLockConfig{Identity: opts.Identity})
	if err != nil {
		return nil, err
	}
	lock := &renewals{Interface: leaseLock, acquired: make(chan struct{})}
	mgr, err := ctrl.NewManager(restConfig, ctrl.Options{
		Scheme: scheme,
		Logger: opts.Log,
		// Sources are watched in the release namespace alone; the SecAccess
		// is cluster-scoped.
		Cache:                               cache.Options{DefaultNamespaces: map[string]cache.Config{opts.Namespace: {}}},
		Metrics:                             metricsserver.Options{BindAddress: opts.MetricsAddress},
		HealthProbeBindAddress:              opts.HealthAddress,
		LeaderElection:                      true,
		LeaderElectionID:                    opts.LeaseName,
		LeaderElectionNamespace:             opts.Namespace,
		LeaderElectionResourceLockInterface: lock,
		// client-go's leader election doesn't release the Lease: the
		// manager below does, once the work has stopped. Asked to release
		// it, client-go would also do so when it can't renew the Lease,
		// before controller-runtime stops the work, and the release can
		// take up to the renewal deadline when the API server doesn't
		// answer, so the work would go on after another replica could take
		// the Lease over.
		LeaderElectionReleaseOnCancel: false,
		LeaseDuration:                 &opts.LeaseDuration,
		RenewDeadline:                 &opts.RenewDeadline,
		RetryPeriod:                   &opts.RetryPeriod,
		GracefulShutdownTimeout:       &opts.GracefulShutdownTimeout,
		Controller:                    config.Controller{SkipNameValidation: &opts.SkipNameValidation},
	})
	if err != nil {
		return nil, err
	}

	schemas, err := messages.LoadSchemas()
	if err != nil {
		return nil, fmt.Errorf("loading the schemas: %w", err)
	}
	publisher := opts.Publisher
	if publisher == nil {
		publisher = JetStreamPublisher{JetStream: opts.JetStream, Stream: messages.StreamJobs}
	}
	if opts.Clock == nil {
		opts.Clock = clock.RealClock{}
	}
	poller := &Poller{Publisher: publisher, Clock: opts.Clock, Retry: time.Second, Log: opts.Log.WithName("polls")}
	if err := mgr.Add(poller); err != nil {
		return nil, err
	}
	reconciler := &SourceReconciler{Client: mgr.GetClient(), Namespace: opts.Namespace, Poller: poller}
	if err := reconciler.SetupWithManager(mgr); err != nil {
		return nil, err
	}
	events := &PollEvents{
		JetStream: opts.JetStream,
		Schemas:   schemas,
		Reader:    mgr.GetAPIReader(),
		Client:    mgr.GetClient(),
		Namespace: opts.Namespace,
		Log:       opts.Log.WithName("poll-events"),
	}
	if err := mgr.Add(events); err != nil {
		return nil, err
	}
	if err := mgr.AddHealthzCheck("ping", healthz.Ping); err != nil {
		return nil, err
	}
	if err := mgr.AddReadyzCheck("ping", healthz.Ping); err != nil {
		return nil, err
	}
	return &manager{Manager: mgr, lock: lock, identity: opts.Identity, renewDeadline: opts.RenewDeadline,
		log: opts.Log}, nil
}

// renewals is the Lease's lock, through which client-go's leader election
// takes and renews the Lease, and which notes when this replica last sent a
// write of the Lease that the API server accepted. The API server applied it
// no sooner, and another replica counts the Lease's duration from when it
// saw the write, later still, so none can take the Lease over until the
// duration has passed since then.
type renewals struct {
	resourcelock.Interface
	// acquired is closed once this replica first takes the Lease.
	acquired chan struct{}
	once     sync.Once
	mu       sync.Mutex
	last     time.Time
}

func (r *renewals) Create(ctx context.Context, record resourcelock.LeaderElectionRecord) error {
	sent := time.Now()
	err := r.Interface.Create(ctx, record)
	r.note(record, sent, err)
	return err
}

func (r *renewals) Update(ctx context.Context, record resourcelock.LeaderElectionRecord) error {
	sent := time.Now()
	err := r.Interface.Update(ctx, record)
	r.note(record, sent, err)
	return err
}

// note notes a write sent when given, if it made this replica the Lease's
// holder.
func (r *renewals) note(record resourcelock.LeaderElectionRecord, sent time.Time, err error) {
	if err != nil || record.HolderIdentity != r.Identity() {
		return
	}
	r.mu.Lock()
	r.last = sent
	r.mu.Unlock()
	r.once.Do(func() { close(r.acquired) })
}

// renewed is when this replica last sent a write of the Lease, as its holder,
// that the API server accepted.
func (r *renewals) renewed() time.Time {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.last
}

// manager is the scheduler's controller-runtime manager, which stops its
// work once the renewal deadline has passed since it last renewed the Lease,
// and releases the Lease once its work has stopped.
type manager struct {
	ctrl.Manager
	lock     *renewals
	identity string
	// renewDeadline bounds how long the work goes on after the last renewal,
	// and the release, as client-go bounds its own.
	renewDeadline time.Duration
	log           logr.Logger
}

// Start runs the scheduler until ctx is done, or until it fails, as when it
// can't renew the Lease. Once the renewal deadline has passed since it last
// renewed the Lease, it stops its work, and fails. If ctx is done, and its
// work stopped within the shutdown timeout, it then releases the Lease, if it
// held it. One whose work hadn't stopped, or that couldn't renew the Lease,
// leaves the Lease to expire.
func (m *manager) Start(ctx context.Context) error {
	ctx, stop := context.WithCancel(ctx)
	defer stop()
	expired := make(chan struct{})
	watched := make(chan struct{})
	go func() {
		defer close(watched)
		m.watch(ctx, stop, expired)
	}()
	err := m.Manager.Start(ctx)
	stop()
	<-watched
	select {
	case <-expired:
		return errors.Join(fmt.Errorf("couldn't renew the Lease %s within its renewal deadline, %s",
			m.lock.Describe(), m.renewDeadline), err)
	default:
	}
	if err != nil {
		return err
	}
	select {
	case <-m.Elected():
	default:
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), m.renewDeadline)
	defer cancel()
	if err := m.release(ctx); err != nil {
		return fmt.Errorf("releasing the Lease: %w", err)
	}
	return nil
}

// watch stops the work, by calling stop, once the renewal deadline has passed
// since this replica last renewed the Lease, and closes expired, until ctx is
// done. client-go's leader election stops the work only once a try to renew
// the Lease, begun a retry period after the last renewal, has gone on for the
// renewal deadline: with a retry period and a renewal deadline that together
// pass the Lease's duration, which client-go accepts, and so the chart does
// (SCHED-3), that's after another replica could take the Lease over
// (SCHED-4).
func (m *manager) watch(ctx context.Context, stop context.CancelFunc, expired chan<- struct{}) {
	select {
	case <-ctx.Done():
		return
	case <-m.lock.acquired:
	}
	for ctx.Err() == nil {
		wait := time.Until(m.lock.renewed().Add(m.renewDeadline))
		if wait <= 0 {
			m.log.Info("Couldn't renew the Lease within its renewal deadline, so stopping", "lease", m.lock.Describe())
			close(expired)
			stop()
			return
		}
		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
		case <-timer.C:
		}
		timer.Stop()
	}
}

// release gives up the Lease, if this replica still holds it, as client-go's
// leader election does: it leaves the Lease with no holder and a duration of
// 1 second, so another replica takes it over at its next try. The leader
// election has stopped by now, so nothing else uses the lock.
func (m *manager) release(ctx context.Context) error {
	return retry.RetryOnConflict(retry.DefaultRetry, func() error {
		record, _, err := m.lock.Get(ctx)
		if err != nil {
			return err
		}
		if record.HolderIdentity != m.identity {
			return nil
		}
		now := metav1.NewTime(time.Now())
		if err := m.lock.Update(ctx, resourcelock.LeaderElectionRecord{
			LeaderTransitions:    record.LeaderTransitions,
			LeaseDurationSeconds: 1,
			RenewTime:            now,
			AcquireTime:          now,
		}); err != nil {
			return err
		}
		m.log.Info("Released the Lease", "lease", m.lock.Describe())
		return nil
	})
}

// ConnectNATS connects to NATS at url, waiting for it if it isn't up yet, as
// when it starts after the scheduler, and reconnecting whenever it's lost.
// While it's reconnecting, a message published fails at once, as a poll's
// job refused is, and is tried again until the next is due. nats.go would
// otherwise hold it, to send on reconnecting, even after the scheduler had
// given it up: after its Source was paused, or once it had expired
// (SOURCE-1, SOURCE-2).
func ConnectNATS(url string) (*nats.Conn, error) {
	return nats.Connect(url, nats.Name("scheduler"), nats.RetryOnFailedConnect(true),
		nats.MaxReconnects(-1), nats.ReconnectWait(2*time.Second), nats.ReconnectBufSize(-1))
}

// JetStreamPublisher publishes to a stream through JetStream, which
// acknowledges a message once the stream keeps it. The message's id is its
// Nats-Msg-Id, so a message published again within the stream's duplicate
// window is kept once.
type JetStreamPublisher struct {
	JetStream jetstream.JetStream
	Stream    string
}

// Publish publishes a message, and returns once the stream has kept it.
func (p JetStreamPublisher) Publish(ctx context.Context, subject string, data []byte, id string) error {
	_, err := p.JetStream.Publish(ctx, subject, data, jetstream.WithMsgID(id), jetstream.WithExpectStream(p.Stream))
	return err
}
