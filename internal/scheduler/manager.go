// Package scheduler is the controller that reconciles the pipeline's controls
// (spec/controls.md): it queues each Source's polls at its cadence, and keeps
// each Source's status. Of its two replicas, only the one that holds its
// Lease acts (spec/deployment.md, SCHED-4).
package scheduler

import (
	"context"
	"fmt"
	"time"

	"github.com/go-logr/logr"
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
// Lease, and stop when it can't renew the Lease within the renewal deadline,
// before another replica can take the Lease over. When its context is done,
// as when Kubernetes stops its pod, it stops them, then releases the Lease,
// so another replica takes it over without waiting out the Lease's duration
// (SCHED-4). It records no Kubernetes Events, which it isn't granted
// (SCHED-6).
func NewManager(restConfig *rest.Config, opts Options) (ctrl.Manager, error) {
	scheme := runtime.NewScheme()
	if err := v1alpha1.AddToScheme(scheme); err != nil {
		return nil, err
	}
	clients, err := kubernetes.NewForConfig(restConfig)
	if err != nil {
		return nil, err
	}
	lock, err := resourcelock.New(resourcelock.LeasesResourceLock, opts.Namespace, opts.LeaseName,
		clients.CoreV1(), clients.CoordinationV1(), resourcelock.ResourceLockConfig{Identity: opts.Identity})
	if err != nil {
		return nil, err
	}
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
	return &manager{Manager: mgr, lock: lock, identity: opts.Identity, timeout: opts.RenewDeadline,
		log: opts.Log}, nil
}

// manager is the scheduler's controller-runtime manager, which releases the
// Lease once its work has stopped.
type manager struct {
	ctrl.Manager
	lock     resourcelock.Interface
	identity string
	// timeout bounds the release, as client-go bounds its own: the renewal
	// deadline.
	timeout time.Duration
	log     logr.Logger
}

// Start runs the scheduler until ctx is done, or until it fails, as when it
// can't renew the Lease. If ctx is done, and its work stopped within the
// shutdown timeout, it then releases the Lease, if it held it. One whose
// work hadn't stopped, or that couldn't renew the Lease, leaves the Lease to
// expire.
func (m *manager) Start(ctx context.Context) error {
	if err := m.Manager.Start(ctx); err != nil {
		return err
	}
	select {
	case <-m.Elected():
	default:
		return nil
	}
	ctx, cancel := context.WithTimeout(context.Background(), m.timeout)
	defer cancel()
	if err := m.release(ctx); err != nil {
		return fmt.Errorf("releasing the Lease: %w", err)
	}
	return nil
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
