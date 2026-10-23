// The scheduler: the controller that reconciles the pipeline's controls
// (spec/controls.md; spec/deployment.md, section 5, the scheduler). The
// umbrella chart gives it the release namespace, NATS's address, and its
// Lease's name and timings as arguments (SCHED-3, SCHED-7).
package main

import (
	"flag"
	"fmt"
	"os"
	"time"

	"github.com/google/uuid"
	"github.com/nats-io/nats.go/jetstream"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"

	"github.com/nslaughter/edgar-data-pipeline/internal/scheduler"
)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	var opts scheduler.Options
	var natsURL string
	flag.StringVar(&opts.Namespace, "namespace", "", "the release namespace, of the Sources and the Lease")
	flag.StringVar(&natsURL, "nats-url", "nats://nats:4222", "NATS's address")
	flag.StringVar(&opts.LeaseName, "lease-name", "scheduler", "the Lease's name")
	flag.DurationVar(&opts.LeaseDuration, "lease-duration", 15*time.Second, "the Lease's duration")
	flag.DurationVar(&opts.RenewDeadline, "lease-renew-deadline", 10*time.Second, "the Lease's renewal deadline")
	flag.DurationVar(&opts.RetryPeriod, "lease-retry-period", 2*time.Second, "the Lease's retry period")
	flag.StringVar(&opts.MetricsAddress, "metrics-address", ":8080", "where it serves its metrics")
	flag.StringVar(&opts.HealthAddress, "health-address", ":8081", "where it serves its health checks")
	flag.DurationVar(&opts.GracefulShutdownTimeout, "shutdown-timeout", 10*time.Second,
		"how long it waits for its work to stop before it releases the Lease")
	logging := zap.Options{}
	logging.BindFlags(flag.CommandLine)
	flag.Parse()
	if opts.Namespace == "" {
		return fmt.Errorf("--namespace is required")
	}
	opts.Log = zap.New(zap.UseFlagOptions(&logging))
	ctrl.SetLogger(opts.Log)

	hostname, err := os.Hostname()
	if err != nil {
		return err
	}
	opts.Identity = hostname + "_" + uuid.NewString()

	// It waits for NATS, which may start after it, and reconnects to it
	// whenever it's lost.
	conn, err := scheduler.ConnectNATS(natsURL)
	if err != nil {
		return err
	}
	defer conn.Close()
	if opts.JetStream, err = jetstream.New(conn); err != nil {
		return err
	}

	mgr, err := scheduler.NewManager(ctrl.GetConfigOrDie(), opts)
	if err != nil {
		return err
	}
	return mgr.Start(ctrl.SetupSignalHandler())
}
