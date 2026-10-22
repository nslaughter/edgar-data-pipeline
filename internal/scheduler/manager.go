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
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/leaderelection/resourcelock"
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
	// before it releases the Lease, within its pod's grace period (SCHED-4).
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
// Lease, and stop when it can't renew the Lease within the renewal deadline.
// When its context is done, as when Kubernetes stops its pod, it stops them,
// then releases the Lease, so another replica takes it over without waiting
// out the Lease's duration (SCHED-4). It records no Kubernetes Events, which
// it isn't granted (SCHED-6).
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
		LeaderElectionReleaseOnCancel:       true,
		LeaseDuration:                       &opts.LeaseDuration,
		RenewDeadline:                       &opts.RenewDeadline,
		RetryPeriod:                         &opts.RetryPeriod,
		GracefulShutdownTimeout:             &opts.GracefulShutdownTimeout,
		Controller:                          config.Controller{SkipNameValidation: &opts.SkipNameValidation},
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
	return mgr, nil
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
