// Package fetcher is the fetcher, the only service that sends requests to the
// SEC, and the only one that writes the cluster store (spec/snapshots.md,
// section 10, the fetcher). Of its two replicas, only the one that holds its
// Lease sends requests and reads jobs (FETCH-16, FETCHER-10).
package fetcher

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/go-logr/logr"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/rest"
	"k8s.io/client-go/tools/leaderelection"
	"k8s.io/client-go/tools/leaderelection/resourcelock"

	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
)

// Options configures a fetcher replica.
type Options struct {
	// Namespace is the release namespace, the Lease's.
	Namespace string
	// LeaseName names its Lease, and the Lease's timings are client-go's
	// leader election's (LEASE-1).
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

	JetStream jetstream.JetStream
	Records   Records
	Objects   Objects
	// Client configures its requests to the SEC, the user agent among them.
	// Run gives it Permit.
	Client ClientOptions
	// Commit is the commit its image was built from, which its records'
	// tool names (FETCHER-1).
	Commit string
	Log    logr.Logger
	// Worker gives the tests' shorter waits; Run fills in the rest.
	Worker Worker
}

// CheckUserAgent says whether a user agent is one the fetcher may send: the
// owner's, which must contain an @ (FETCH-2, FETCH-17).
func CheckUserAgent(agent string) error {
	if !strings.Contains(agent, "@") {
		return errors.New("SEC_USER_AGENT must be set to the owner's name and a contact address, " +
			"such as \"Name contact@example.com\"")
	}
	return nil
}

// Run runs a fetcher replica until ctx is done, as when Kubernetes stops its
// pod, or until it can't renew the Lease within the renewal deadline, when
// it fails. It sends nothing without a user agent that contains an @
// (FETCH-17). It serves its metrics and its health checks from when it
// starts, whether or not it's the active replica (METRIC-1).
//
// Through client-go's leader election, it waits to hold the Lease, and sends
// requests and reads jobs only while it does (FETCH-16, FETCHER-10). It
// stops its work once the renewal deadline has passed since it last renewed
// the Lease, which is before another replica can take the Lease over, and
// then fails. When ctx is done, it stops its work too, but never releases
// the Lease, which another replica takes over only once its duration has
// passed without a renewal, so the two never send at once
// (spec/deployment.md, the fetcher).
func Run(ctx context.Context, config *rest.Config, opts Options) error {
	if err := CheckUserAgent(opts.Client.UserAgent); err != nil {
		return err
	}
	schemas, err := messages.LoadSchemas()
	if err != nil {
		return fmt.Errorf("loading the schemas: %w", err)
	}
	clients, err := kubernetes.NewForConfig(config)
	if err != nil {
		return err
	}
	leaseLock, err := resourcelock.New(resourcelock.LeasesResourceLock, opts.Namespace, opts.LeaseName,
		clients.CoreV1(), clients.CoordinationV1(), resourcelock.ResourceLockConfig{Identity: opts.Identity})
	if err != nil {
		return err
	}
	lock := &renewals{Interface: leaseLock, acquired: make(chan struct{})}
	stopServing := serve(opts)
	defer stopServing()

	running, stop := context.WithCancel(ctx)
	defer stop()
	expired := make(chan struct{})
	var mu sync.Mutex
	var led chan struct{}
	elector, err := leaderelection.NewLeaderElector(leaderelection.LeaderElectionConfig{
		Lock:          lock,
		LeaseDuration: opts.LeaseDuration,
		RenewDeadline: opts.RenewDeadline,
		RetryPeriod:   opts.RetryPeriod,
		// Never released: see Run.
		ReleaseOnCancel: false,
		Name:            opts.LeaseName,
		Callbacks: leaderelection.LeaderCallbacks{
			OnStartedLeading: func(leading context.Context) {
				done := make(chan struct{})
				mu.Lock()
				led = done
				mu.Unlock()
				defer close(done)
				opts.Log.Info("Became the active replica", "lease", lock.Describe())
				lead(ctx, leading, config, opts, schemas, lock)
			},
			OnStoppedLeading: func() {},
		},
	})
	if err != nil {
		return err
	}
	go watch(running, stop, expired, lock, opts.RenewDeadline, opts.Log)
	elector.Run(running)
	stop()
	mu.Lock()
	done := led
	mu.Unlock()
	if done != nil {
		<-done
	}
	select {
	case <-expired:
		return fmt.Errorf("couldn't renew the Lease %s within its renewal deadline, %s", lock.Describe(),
			opts.RenewDeadline)
	default:
	}
	if ctx.Err() != nil {
		return nil
	}
	return fmt.Errorf("lost the Lease %s", lock.Describe())
}

// errNotLeading refuses a request from a replica that isn't the active one.
var errNotLeading = errors.New("this replica doesn't hold the Lease")

// lead does the active replica's work, until leading is done. It reads the
// SecAccess from when it begins, so it sees any stop the replica before it
// recorded (ACCESS-3), and permits each request only while it holds the
// Lease, within the renewal deadline of its last renewal, and requests are
// allowed (FETCH-16, ACCESS-1).
func lead(shutdown, leading context.Context, config *rest.Config, opts Options, schemas *messages.Schemas,
	lock *renewals) {
	access, err := NewSecAccess(config)
	if err != nil {
		opts.Log.Error(err, "Couldn't read the SecAccess")
		return
	}
	if err := access.Start(leading); err != nil {
		if leading.Err() == nil {
			opts.Log.Error(err, "Couldn't read the SecAccess")
		}
		return
	}
	permit := func() error {
		if leading.Err() != nil || !time.Now().Before(lock.renewed().Add(opts.RenewDeadline)) {
			return errNotLeading
		}
		return access.Allowed()
	}
	clientOptions := opts.Client
	clientOptions.Permit = permit
	worker := opts.Worker
	worker.JetStream = opts.JetStream
	worker.Schemas = schemas
	worker.Records = opts.Records
	worker.Objects = opts.Objects
	worker.Access = access
	worker.Client = NewClient(clientOptions)
	worker.Tool = Tool(opts.Commit)
	worker.Permit = permit
	worker.Shutdown = shutdown
	worker.Log = opts.Log.WithName("jobs")
	worker.Run(leading)
}

// renewals is the Lease's lock, through which client-go's leader election
// takes and renews the Lease, and which notes when this replica last sent a
// write of the Lease that the API server accepted. The API server applied it
// no sooner, and another replica counts the Lease's duration from when it
// saw the write, later still, so none can take the Lease over until the
// duration has passed since then. The scheduler keeps the same note
// (internal/scheduler).
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

// watch stops the replica, by calling stop, once the renewal deadline has
// passed since it last renewed the Lease, and closes expired, until ctx is
// done. client-go's leader election stops leading only once a try to renew
// the Lease, begun a retry period after the last renewal, has gone on for
// the renewal deadline: with a retry period and a renewal deadline that
// together pass the Lease's duration, which client-go accepts, and so the
// chart does (LEASE-1), that's after another replica could take the Lease
// over (FETCH-16).
func watch(ctx context.Context, stop context.CancelFunc, expired chan<- struct{}, lock *renewals,
	renewDeadline time.Duration, log logr.Logger) {
	select {
	case <-ctx.Done():
		return
	case <-lock.acquired:
	}
	for ctx.Err() == nil {
		wait := time.Until(lock.renewed().Add(renewDeadline))
		if wait <= 0 {
			log.Info("Couldn't renew the Lease within its renewal deadline, so stopping", "lease", lock.Describe())
			close(expired)
			stop()
			return
		}
		_ = sleep(ctx, wait)
	}
}

// serve serves the replica's metrics, those of the Go client library for
// now, at /metrics, and its health checks at /healthz and /readyz, and gives
// the function that stops both.
func serve(opts Options) func() {
	registry := prometheus.NewRegistry()
	registry.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	metrics := http.NewServeMux()
	metrics.Handle("/metrics", promhttp.HandlerFor(registry, promhttp.HandlerOpts{}))
	health := http.NewServeMux()
	ok := http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write([]byte("ok")) })
	health.Handle("/healthz", ok)
	health.Handle("/readyz", ok)
	var servers []*http.Server
	for _, s := range []struct {
		address string
		handler http.Handler
	}{{opts.MetricsAddress, metrics}, {opts.HealthAddress, health}} {
		if s.address == "" || s.address == "0" {
			continue
		}
		server := &http.Server{Addr: s.address, Handler: s.handler, ReadHeaderTimeout: 10 * time.Second}
		servers = append(servers, server)
		go func() {
			if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
				opts.Log.Error(err, "Couldn't serve", "address", server.Addr)
			}
		}()
	}
	return func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		for _, server := range servers {
			_ = server.Shutdown(ctx)
		}
	}
}

// ConnectNATS connects to NATS at url, waiting for it if it isn't up yet, as
// when it starts after the fetcher, and reconnecting whenever it's lost.
// While it's reconnecting, a message published fails at once, and its step
// is tried again (Worker.hold), rather than held by nats.go to send on
// reconnecting.
func ConnectNATS(url string) (*nats.Conn, error) {
	return nats.Connect(url, nats.Name("fetcher"), nats.RetryOnFailedConnect(true),
		nats.MaxReconnects(-1), nats.ReconnectWait(2*time.Second), nats.ReconnectBufSize(-1))
}
