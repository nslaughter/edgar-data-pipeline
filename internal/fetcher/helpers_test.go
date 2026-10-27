package fetcher

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/nats-io/nats.go/jetstream"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/envtest"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
	"github.com/nslaughter/edgar-data-pipeline/internal/testenv"
)

// testUserAgent is the tests' user agent, a name and a contact address no
// one reads.
const testUserAgent = "edgar-data-pipeline tests tests@example.invalid"

// testCommit is the commit the tests' records' tool names.
const testCommit = "0123456789abcdef0123456789abcdef01234567"

// The API server, which envtest starts for the first test that needs it.
var (
	kube       sync.Once
	kubeEnv    *envtest.Environment
	restConfig *rest.Config
	kubeErr    error
)

func TestMain(m *testing.M) {
	code := m.Run()
	if kubeEnv != nil {
		if err := kubeEnv.Stop(); err != nil {
			fmt.Fprintln(os.Stderr, err)
		}
	}
	os.Exit(code)
}

// apiServer gives the configuration of envtest's API server, which it
// starts the first time it's called. The tests of the fetching rules and of
// the Worker don't need it.
func apiServer(t *testing.T) *rest.Config {
	t.Helper()
	kube.Do(func() { kubeEnv, restConfig, kubeErr = testenv.StartKubernetes() })
	if kubeErr != nil {
		t.Fatal(kubeErr)
	}
	return restConfig
}

// authority is a certificate authority of the tests' own, which issues
// certificates for the SEC's hosts.
type authority struct {
	cert *x509.Certificate
	key  *ecdsa.PrivateKey
	pool *x509.CertPool
}

func newAuthority(t *testing.T) *authority {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "The tests' certificate authority"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(time.Hour),
		IsCA:                  true,
		KeyUsage:              x509.KeyUsageCertSign,
		BasicConstraintsValid: true,
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatal(err)
	}
	pool := x509.NewCertPool()
	pool.AddCert(cert)
	return &authority{cert: cert, key: key, pool: pool}
}

// issue gives a certificate for the names, issued by the authority.
func (a *authority) issue(t *testing.T, names ...string) tls.Certificate {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: names[0]},
		DNSNames:     names,
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	der, err := x509.CreateCertificate(rand.Reader, template, a.cert, &key.PublicKey, a.key)
	if err != nil {
		t.Fatal(err)
	}
	return tls.Certificate{Certificate: [][]byte{der}, PrivateKey: key}
}

// request is a request the test server received: when, its URL as the
// fetcher asked for it, and its headers.
type request struct {
	at     time.Time
	url    string
	header http.Header
}

// secServer answers as the SEC's hosts, over HTTPS with a certificate for
// them that its authority issued, on a local port, which its dial reaches
// whatever address it's asked for. It notes each request when its headers
// have been read, as the fake EDGAR does (spec/fake-edgar.md, REQ-1).
type secServer struct {
	*httptest.Server
	authority *authority
	mu        sync.Mutex
	handler   http.HandlerFunc
	requests  []request
}

func newSECServer(t *testing.T, handler http.HandlerFunc) *secServer {
	t.Helper()
	s := &secServer{authority: newAuthority(t), handler: handler}
	s.Server = httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		s.mu.Lock()
		s.requests = append(s.requests, request{at: time.Now(), url: "https://" + r.Host + r.URL.RequestURI(),
			header: r.Header.Clone()})
		handle := s.handler
		s.mu.Unlock()
		handle(w, r)
	}))
	s.TLS = &tls.Config{Certificates: []tls.Certificate{s.authority.issue(t, "www.sec.gov", "data.sec.gov")}}
	s.StartTLS()
	t.Cleanup(s.Close)
	return s
}

func (s *secServer) dial(ctx context.Context, network, _ string) (net.Conn, error) {
	return (&net.Dialer{}).DialContext(ctx, network, s.Listener.Addr().String())
}

func (s *secServer) seen() []request {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]request(nil), s.requests...)
}

func (s *secServer) handle(handler http.HandlerFunc) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.handler = handler
}

// options gives a Client that reaches the server, trusting its authority
// alone, with waits short enough for the tests.
func (s *secServer) options() ClientOptions {
	return ClientOptions{
		UserAgent:   testUserAgent,
		Roots:       s.authority.pool,
		Dial:        s.dial,
		Timeout:     2 * time.Second,
		MinInterval: time.Millisecond,
		BackoffUnit: time.Millisecond,
		PermitPoll:  10 * time.Millisecond,
	}
}

// fakeRecords is the table records, in memory, whose steps fail on the
// tests' cue.
type fakeRecords struct {
	mu    sync.Mutex
	rows  []fakeRow
	fails map[string]int
	tried map[string]int
}

type fakeRow struct {
	job    string
	record Record
}

var errCued = errors.New("cued to fail")

// fail makes the next n tries of a step fail: ByJob, LatestVersion, or Add.
func (f *fakeRecords) fail(step string, n int) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.fails == nil {
		f.fails = map[string]int{}
	}
	f.fails[step] = n
}

func (f *fakeRecords) try(step string) error {
	if f.tried == nil {
		f.tried = map[string]int{}
	}
	f.tried[step]++
	if f.fails[step] > 0 {
		f.fails[step]--
		return errCued
	}
	return nil
}

func (f *fakeRecords) tries(step string) int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.tried[step]
}

func (f *fakeRecords) ByJob(_ context.Context, job string) (*Stored, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.try("ByJob"); err != nil {
		return nil, err
	}
	for i, row := range f.rows {
		if row.job == job {
			return &Stored{ID: int64(i + 1), Record: row.record}, nil
		}
	}
	return nil, nil
}

func (f *fakeRecords) LatestVersion(_ context.Context, url string) (*Version, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.try("LatestVersion"); err != nil {
		return nil, err
	}
	for i := len(f.rows) - 1; i >= 0; i-- {
		if r := f.rows[i].record; r.URL == url && r.SHA256 != nil {
			return &Version{ETag: r.ETag, LastModified: r.LastModified, SHA256: *r.SHA256, Bytes: *r.Bytes,
				Object: *r.Object}, nil
		}
	}
	return nil, nil
}

func (f *fakeRecords) Add(_ context.Context, job string, record Record) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if err := f.try("Add"); err != nil {
		return 0, err
	}
	for _, row := range f.rows {
		if row.job == job {
			return 0, ErrRecorded
		}
	}
	f.rows = append(f.rows, fakeRow{job: job, record: record})
	return int64(len(f.rows)), nil
}

func (f *fakeRecords) all() []fakeRow {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]fakeRow(nil), f.rows...)
}

// fakeObjects is the bucket, in memory, whose writes fail on the tests' cue.
type fakeObjects struct {
	mu      sync.Mutex
	objects map[string][]byte
	puts    int
	fails   int
}

func (f *fakeObjects) Put(_ context.Context, name string, data []byte) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.puts++
	if f.fails > 0 {
		f.fails--
		return errCued
	}
	if f.objects == nil {
		f.objects = map[string][]byte{}
	}
	if _, ok := f.objects[name]; !ok {
		f.objects[name] = data
	}
	return nil
}

func (f *fakeObjects) tries() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.puts
}

func (f *fakeObjects) get(name string) ([]byte, bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	data, ok := f.objects[name]
	return data, ok
}

// fakeAccess is the SecAccess, in memory: requests are allowed but while the
// tests pause them, or while a stop isn't acknowledged.
type fakeAccess struct {
	mu           sync.Mutex
	paused       bool
	stops        []v1alpha1.Stop
	acknowledged v1alpha1.Timestamp
}

func (f *fakeAccess) Allowed() error {
	f.mu.Lock()
	defer f.mu.Unlock()
	if len(f.stops) > 0 && f.stops[len(f.stops)-1].At != f.acknowledged {
		return ErrRequestsStopped
	}
	if f.paused {
		return ErrRequestsPaused
	}
	return nil
}

func (f *fakeAccess) Stop(_ context.Context, stop v1alpha1.Stop) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.stops = append(f.stops, stop)
	return nil
}

func (f *fakeAccess) pause(paused bool) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.paused = paused
}

func (f *fakeAccess) acknowledge(at v1alpha1.Timestamp) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.acknowledged = at
}

func (f *fakeAccess) stopped() []v1alpha1.Stop {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]v1alpha1.Stop(nil), f.stops...)
}

// makeStreams makes JOBS with the fetcher's consumers, and RECORDS with a
// consumer of every event, as the chart's Job makes them: each consumer
// before RECORDS takes its subjects (NATS-5). It gives the consumer of
// events.
func makeStreams(ctx context.Context, t *testing.T, js jetstream.JetStream, ackWait time.Duration) jetstream.Consumer {
	t.Helper()
	testenv.MakeRecordsAlone(ctx, t, js)
	events, err := js.CreateConsumer(ctx, messages.StreamRecords, jetstream.ConsumerConfig{
		Durable:        "events",
		FilterSubjects: []string{"edgar.records.poll", "edgar.records.filing", "edgar.records.backfill"},
		AckPolicy:      jetstream.AckExplicitPolicy,
	})
	if err != nil {
		t.Fatal(err)
	}
	testenv.GiveRecordsSubjects(ctx, t, js)
	testenv.MakeJobs(ctx, t, js)
	testenv.MakeFetcherConsumers(ctx, t, js, ackWait)
	return events
}

// newJob gives a job of the kind, for the URL, queued now, with the fields
// its kind requires, and a poll's expiring in an hour.
func newJob(kind, url string) messages.Job {
	now := time.Now()
	job := messages.Job{ID: uuid.NewString(), Kind: kind, Feed: "submissions", URL: url, QueuedAt: timestamp(now)}
	switch kind {
	case messages.KindPoll:
		job.Source = "apple-submissions"
		job.ExpiresAt = timestamp(now.Add(time.Hour))
	case messages.KindBackfill:
		job.Backfill = "apple-2025"
	}
	return job
}

// queue publishes a job on its kind's subject, which JOBS takes.
func queue(ctx context.Context, t *testing.T, js jetstream.JetStream, job messages.Job) {
	t.Helper()
	data, err := json.Marshal(job)
	if err != nil {
		t.Fatal(err)
	}
	publish(ctx, t, js, messages.SubjectJobs+job.Kind, data)
}

func publish(ctx context.Context, t *testing.T, js jetstream.JetStream, subject string, data []byte) {
	t.Helper()
	if _, err := js.Publish(ctx, subject, data); err != nil {
		t.Fatal(err)
	}
}

// received is an event as its consumer received it, with its subject.
type received struct {
	subject string
	data    []byte
	event   struct {
		ID     int64           `json:"id"`
		Job    messages.Job    `json:"job"`
		Record json.RawMessage `json:"record"`
	}
}

// nextEvent waits for the next event, and acknowledges it.
func nextEvent(t *testing.T, events jetstream.Consumer, within time.Duration) received {
	t.Helper()
	msg, err := events.Next(jetstream.FetchMaxWait(within))
	if err != nil {
		t.Fatalf("no event within %s: %v", within, err)
	}
	if err := msg.Ack(); err != nil {
		t.Fatal(err)
	}
	got := received{subject: msg.Subject(), data: msg.Data()}
	if err := json.Unmarshal(msg.Data(), &got.event); err != nil {
		t.Fatal(err)
	}
	return got
}

// noEvent checks that no event comes within the time given.
func noEvent(t *testing.T, events jetstream.Consumer, within time.Duration) {
	t.Helper()
	if msg, err := events.Next(jetstream.FetchMaxWait(within)); err == nil {
		t.Fatalf("an event came: %s", msg.Data())
	}
}

// consumerState is what a consumer of JOBS has delivered and been
// acknowledged.
func consumerState(ctx context.Context, t *testing.T, js jetstream.JetStream, name string) *jetstream.ConsumerInfo {
	t.Helper()
	consumer, err := js.Consumer(ctx, messages.StreamJobs, name)
	if err != nil {
		t.Fatal(err)
	}
	info, err := consumer.Info(ctx)
	if err != nil {
		t.Fatal(err)
	}
	return info
}

// settled waits until JOBS holds no job: each has been acknowledged.
func settled(ctx context.Context, t *testing.T, js jetstream.JetStream, within time.Duration) {
	t.Helper()
	stream, err := js.Stream(ctx, messages.StreamJobs)
	if err != nil {
		t.Fatal(err)
	}
	testenv.Eventually(t, within, func() error {
		info, err := stream.Info(ctx)
		if err != nil {
			return err
		}
		if info.State.Msgs != 0 {
			return fmt.Errorf("JOBS holds %d jobs", info.State.Msgs)
		}
		return nil
	})
}
