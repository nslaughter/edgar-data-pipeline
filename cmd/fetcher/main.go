// The fetcher: the only service that sends requests to the SEC, and the only
// one that writes the cluster store (spec/snapshots.md, section 10, the
// fetcher; spec/deployment.md, section 5, the fetcher). The umbrella chart
// gives it the release namespace, its Lease's name and timings, NATS's,
// Garage's, and the database's addresses, and the commit as arguments, and
// its user agent, its Garage key, and its database role's password in its
// environment, each from its Secret (LEASE-1, FETCHER-5 to FETCHER-8,
// FETCHER-12).
package main

import (
	"crypto/x509"
	"flag"
	"fmt"
	"os"
	"regexp"
	"time"

	"github.com/google/uuid"
	"github.com/nats-io/nats.go/jetstream"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"

	"github.com/nslaughter/edgar-data-pipeline/internal/fetcher"
)

var commitForm = regexp.MustCompile(`^[0-9a-f]{40}$`)

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func run() error {
	var opts fetcher.Options
	var natsURL, s3Endpoint, s3Region, bucket, dbHost, dbName, dbUser, dbCA, secCA string
	var dbPort int
	flag.StringVar(&opts.Namespace, "namespace", "", "the release namespace, the Lease's")
	flag.StringVar(&opts.LeaseName, "lease-name", "fetcher", "the Lease's name")
	flag.DurationVar(&opts.LeaseDuration, "lease-duration", 15*time.Second, "the Lease's duration")
	flag.DurationVar(&opts.RenewDeadline, "lease-renew-deadline", 10*time.Second, "the Lease's renewal deadline")
	flag.DurationVar(&opts.RetryPeriod, "lease-retry-period", 2*time.Second, "the Lease's retry period")
	flag.StringVar(&opts.Commit, "commit", "", "the commit its image was built from, which its records' tool names")
	flag.StringVar(&natsURL, "nats-url", "nats://nats:4222", "NATS's address")
	flag.StringVar(&s3Endpoint, "s3-endpoint", "http://garage:3900", "Garage's S3 API")
	flag.StringVar(&s3Region, "s3-region", "garage", "the region of Garage's S3 API")
	flag.StringVar(&bucket, "bucket", "snapshots", "the cluster store's bucket")
	flag.StringVar(&dbHost, "database-host", "postgres-rw", "the database's primary")
	flag.IntVar(&dbPort, "database-port", 5432, "the database's port")
	flag.StringVar(&dbName, "database-name", "edgar", "the pipeline's database")
	flag.StringVar(&dbUser, "database-user", "fetcher", "the fetcher's database role")
	flag.StringVar(&dbCA, "database-ca", "", "the certificate of the authority the database's certificate chains to")
	flag.StringVar(&secCA, "sec-ca", "",
		"the certificate the SEC's hosts' certificates must chain to, in place of the system's")
	flag.StringVar(&opts.MetricsAddress, "metrics-address", ":8080", "where it serves its metrics")
	flag.StringVar(&opts.HealthAddress, "health-address", ":8081", "where it serves its health checks")
	logging := zap.Options{}
	logging.BindFlags(flag.CommandLine)
	flag.Parse()
	// FETCH-17: nothing is sent without the owner's user agent.
	opts.Client.UserAgent = os.Getenv("SEC_USER_AGENT")
	if err := fetcher.CheckUserAgent(opts.Client.UserAgent); err != nil {
		return err
	}
	switch {
	case opts.Namespace == "":
		return fmt.Errorf("--namespace is required")
	case !commitForm.MatchString(opts.Commit):
		return fmt.Errorf("--commit must be a commit's 40 lower-case hexadecimal digits")
	case dbCA == "":
		return fmt.Errorf("--database-ca is required")
	}
	opts.Log = zap.New(zap.UseFlagOptions(&logging))
	ctrl.SetLogger(opts.Log)
	if secCA != "" {
		pem, err := os.ReadFile(secCA)
		if err != nil {
			return err
		}
		opts.Client.Roots = x509.NewCertPool()
		if !opts.Client.Roots.AppendCertsFromPEM(pem) {
			return fmt.Errorf("%s holds no certificate", secCA)
		}
	}

	hostname, err := os.Hostname()
	if err != nil {
		return err
	}
	opts.Identity = hostname + "_" + uuid.NewString()

	ctx := ctrl.SetupSignalHandler()
	pool, err := fetcher.ConnectPostgres(ctx, dbHost, dbPort, dbName, dbUser, os.Getenv("PGPASSWORD"), dbCA)
	if err != nil {
		return err
	}
	defer pool.Close()
	opts.Records = fetcher.PostgresRecords{Pool: pool}
	opts.Objects = fetcher.NewS3Objects(s3Endpoint, s3Region, bucket, os.Getenv("AWS_ACCESS_KEY_ID"),
		os.Getenv("AWS_SECRET_ACCESS_KEY"))
	// It waits for NATS, which may start after it, and reconnects to it
	// whenever it's lost.
	conn, err := fetcher.ConnectNATS(natsURL)
	if err != nil {
		return err
	}
	defer conn.Close()
	if opts.JetStream, err = jetstream.New(conn); err != nil {
		return err
	}
	return fetcher.Run(ctx, ctrl.GetConfigOrDie(), opts)
}
