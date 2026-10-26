// Package testenv starts what the Go tests run against, without the network:
// a Kubernetes API server, through envtest, with the definitions in
// spec/schemas/ installed; a NATS server with JetStream, on a local port,
// with the streams and the consumers the scheduler and the fetcher use,
// configured as the umbrella chart's values configure them
// (spec/deployment.md, NATS-3, NATS-5, SCHED-8, FETCHER-9); and a proxy that
// can cut a service off from a server it reaches.
package testenv

import (
	"context"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"

	"github.com/nats-io/nats-server/v2/server"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

// EnvtestVersion is the Kubernetes version of envtest's API server, of the
// minor version kind's nodes run (deploy/kind.yaml).
const EnvtestVersion = "1.36.2"

// Root is the repository's root: the directory that holds go.mod.
func Root() (string, error) {
	dir, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", errors.New("no go.mod above the working directory")
		}
		dir = parent
	}
}

// StartKubernetes starts an API server and etcd with envtest, from the
// binaries KUBEBUILDER_ASSETS names, and installs the definitions in
// spec/schemas/. Its caller stops it.
func StartKubernetes() (*envtest.Environment, *rest.Config, error) {
	if os.Getenv("KUBEBUILDER_ASSETS") == "" {
		return nil, nil, fmt.Errorf("the Go tests need envtest's binaries: set KUBEBUILDER_ASSETS to "+
			"\"$(go tool setup-envtest use %s -p path)\"", EnvtestVersion)
	}
	root, err := Root()
	if err != nil {
		return nil, nil, err
	}
	schemas := filepath.Join(root, "spec", "schemas")
	env := &envtest.Environment{
		CRDInstallOptions: envtest.CRDInstallOptions{
			Paths: []string{
				filepath.Join(schemas, "sources.yaml"),
				filepath.Join(schemas, "backfills.yaml"),
				filepath.Join(schemas, "secaccesses.yaml"),
			},
			ErrorIfPathMissing: true,
		},
	}
	config, err := env.Start()
	if err != nil {
		return nil, nil, err
	}
	return env, config, nil
}

// NATS starts a NATS server with JetStream on a free local port, keeping its
// store in the test's temporary directory, and connects to it. The test's
// cleanup stops both.
func NATS(t testing.TB) (*server.Server, jetstream.JetStream) {
	t.Helper()
	srv := StartNATS(t, -1, t.TempDir())
	conn, err := nats.Connect(srv.ClientURL())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(conn.Close)
	js, err := jetstream.New(conn)
	if err != nil {
		t.Fatal(err)
	}
	return srv, js
}

// StartNATS starts a NATS server with JetStream on a local port, a free one
// if port is -1, keeping its store in the directory given, so a server
// started again on its port and store has its streams. The test's cleanup
// stops it.
func StartNATS(t testing.TB, port int, store string) *server.Server {
	t.Helper()
	srv, err := server.NewServer(&server.Options{
		Host:      "127.0.0.1",
		Port:      port,
		JetStream: true,
		StoreDir:  store,
		NoLog:     true,
		NoSigs:    true,
	})
	if err != nil {
		t.Fatal(err)
	}
	srv.Start()
	if !srv.ReadyForConnections(10 * time.Second) {
		t.Fatal("NATS didn't start")
	}
	t.Cleanup(func() {
		srv.Shutdown()
		srv.WaitForShutdown()
	})
	return srv
}

// MakeJobs makes the stream JOBS, a work queue, as the chart's values have it.
func MakeJobs(ctx context.Context, t testing.TB, js jetstream.JetStream) jetstream.Stream {
	t.Helper()
	stream, err := js.CreateStream(ctx, jetstream.StreamConfig{
		Name:      "JOBS",
		Subjects:  []string{"edgar.jobs.poll", "edgar.jobs.filing", "edgar.jobs.backfill"},
		Retention: jetstream.WorkQueuePolicy,
		Storage:   jetstream.FileStorage,
	})
	if err != nil {
		t.Fatal(err)
	}
	return stream
}

// MakeRecords makes the stream RECORDS, kept by interest, with the
// scheduler's consumer scheduler-polls, as the chart's values have them: the
// consumer first, then the stream's subjects (NATS-5, SCHED-8).
func MakeRecords(ctx context.Context, t testing.TB, js jetstream.JetStream) jetstream.Stream {
	t.Helper()
	MakeRecordsAlone(ctx, t, js)
	MakeSchedulerPolls(ctx, t, js)
	return GiveRecordsSubjects(ctx, t, js)
}

// MakeRecordsAlone makes the stream RECORDS, kept by interest, with no
// consumer, and no subjects until GiveRecordsSubjects gives them.
func MakeRecordsAlone(ctx context.Context, t testing.TB, js jetstream.JetStream) jetstream.Stream {
	t.Helper()
	stream, err := js.CreateStream(ctx, jetstream.StreamConfig{
		Name:      "RECORDS",
		Retention: jetstream.InterestPolicy,
		Storage:   jetstream.FileStorage,
	})
	if err != nil {
		t.Fatal(err)
	}
	return stream
}

// GiveRecordsSubjects gives RECORDS its subjects.
func GiveRecordsSubjects(ctx context.Context, t testing.TB, js jetstream.JetStream) jetstream.Stream {
	t.Helper()
	stream, err := js.Stream(ctx, "RECORDS")
	if err != nil {
		t.Fatal(err)
	}
	config := stream.CachedInfo().Config
	config.Subjects = []string{"edgar.records.poll", "edgar.records.filing", "edgar.records.backfill"}
	if stream, err = js.UpdateStream(ctx, config); err != nil {
		t.Fatal(err)
	}
	return stream
}

// MakeFetcherConsumers makes the fetcher's durable consumers on JOBS, one for
// each kind of job, as the chart's values have them (FETCHER-9), but that a
// job is delivered again ackWait after the consumer last heard of it, or
// JetStream's default of 30 seconds if ackWait is 0.
func MakeFetcherConsumers(ctx context.Context, t testing.TB, js jetstream.JetStream, ackWait time.Duration) {
	t.Helper()
	for _, kind := range []string{"poll", "filing", "backfill"} {
		if _, err := js.CreateConsumer(ctx, "JOBS", jetstream.ConsumerConfig{
			Durable:        "fetcher-" + kind + "s",
			FilterSubjects: []string{"edgar.jobs." + kind},
			AckPolicy:      jetstream.AckExplicitPolicy,
			DeliverPolicy:  jetstream.DeliverAllPolicy,
			MaxDeliver:     -1,
			AckWait:        ackWait,
		}); err != nil {
			t.Fatal(err)
		}
	}
}

// MakeSchedulerPolls makes the durable consumer scheduler-polls on RECORDS.
func MakeSchedulerPolls(ctx context.Context, t testing.TB, js jetstream.JetStream) jetstream.Consumer {
	t.Helper()
	consumer, err := js.CreateConsumer(ctx, "RECORDS", jetstream.ConsumerConfig{
		Durable:        "scheduler-polls",
		FilterSubjects: []string{"edgar.records.poll"},
		AckPolicy:      jetstream.AckExplicitPolicy,
		DeliverPolicy:  jetstream.DeliverAllPolicy,
		MaxDeliver:     -1,
	})
	if err != nil {
		t.Fatal(err)
	}
	return consumer
}

// Proxy relays TCP connections to a server, such as the API server, until
// it's cut, when it closes them and refuses new ones, or until it hangs,
// when it keeps them open, and accepts new ones, but relays nothing more, as
// a network that drops what it's sent.
type Proxy struct {
	listener net.Listener
	mu       sync.Mutex
	conns    []net.Conn
	cut      bool
	hung     bool
	closed   chan struct{}
}

// NewProxy starts a Proxy to the target, an address, on a free local port.
// The test's cleanup cuts it.
func NewProxy(t testing.TB, target string) *Proxy {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	p := &Proxy{listener: listener, closed: make(chan struct{})}
	go func() {
		for {
			conn, err := listener.Accept()
			if err != nil {
				return
			}
			upstream, err := net.Dial("tcp", target)
			p.mu.Lock()
			if err != nil || p.cut {
				p.mu.Unlock()
				_ = conn.Close()
				if upstream != nil {
					_ = upstream.Close()
				}
				continue
			}
			p.conns = append(p.conns, conn, upstream)
			p.mu.Unlock()
			go p.relay(upstream, conn)
			go p.relay(conn, upstream)
		}
	}()
	t.Cleanup(p.CutOff)
	return p
}

// Addr is the Proxy's address.
func (p *Proxy) Addr() string {
	return p.listener.Addr().String()
}

// relay copies what src sends to dst until either is closed. Once the proxy
// hangs, it holds what it reads, and sends nothing more.
func (p *Proxy) relay(dst, src net.Conn) {
	defer func() { _ = dst.Close() }()
	buf := make([]byte, 32<<10)
	for {
		n, err := src.Read(buf)
		if n > 0 {
			p.mu.Lock()
			hung := p.hung
			p.mu.Unlock()
			if hung {
				<-p.closed
				return
			}
			if _, err := dst.Write(buf[:n]); err != nil {
				return
			}
		}
		if err != nil {
			return
		}
	}
}

// CutOff closes the Proxy's connections, and refuses new ones.
func (p *Proxy) CutOff() {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.cut {
		return
	}
	p.cut = true
	close(p.closed)
	_ = p.listener.Close()
	for _, conn := range p.conns {
		_ = conn.Close()
	}
}

// Hang keeps the Proxy's connections open, but relays nothing more.
func (p *Proxy) Hang() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.hung = true
}

// Eventually calls check every 50 milliseconds until it returns nil, and
// fails the test with check's last error if that takes longer than within.
func Eventually(t testing.TB, within time.Duration, check func() error) {
	t.Helper()
	deadline := time.Now().Add(within)
	for {
		err := check()
		if err == nil {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("after %s: %v", within, err)
		}
		time.Sleep(50 * time.Millisecond)
	}
}
