package fetcher

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"sync/atomic"
	"testing"
	"time"

	"github.com/go-logr/logr"
	"github.com/nats-io/nats.go/jetstream"
	coordinationv1 "k8s.io/api/coordination/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
	"github.com/nslaughter/edgar-data-pipeline/internal/testenv"
)

func newKubeClient(t *testing.T) client.Client {
	t.Helper()
	scheme := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{
		v1alpha1.AddToScheme, corev1.AddToScheme, coordinationv1.AddToScheme,
	} {
		if err := add(scheme); err != nil {
			t.Fatal(err)
		}
	}
	c, err := client.New(apiServer(t), client.Options{Scheme: scheme})
	if err != nil {
		t.Fatal(err)
	}
	return c
}

var namespaces atomic.Int32

// newNamespace makes a namespace of the test's own, the release namespace of
// the replicas it runs.
func newNamespace(t *testing.T, c client.Client) string {
	t.Helper()
	name := fmt.Sprintf("release-%d", namespaces.Add(1))
	if err := c.Create(context.Background(), &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: name}}); err != nil {
		t.Fatal(err)
	}
	return name
}

// setSecAccess makes the SecAccess cluster, or changes it, to the spec and
// stop given. The test's cleanup removes it.
func setSecAccess(t *testing.T, c client.Client, spec v1alpha1.SecAccessSpec, stop *v1alpha1.Stop) {
	t.Helper()
	ctx := context.Background()
	access := &v1alpha1.SecAccess{}
	err := c.Get(ctx, client.ObjectKey{Name: v1alpha1.SecAccessName}, access)
	switch {
	case apierrors.IsNotFound(err):
		access = &v1alpha1.SecAccess{ObjectMeta: metav1.ObjectMeta{Name: v1alpha1.SecAccessName}, Spec: spec}
		if err := c.Create(ctx, access); err != nil {
			t.Fatal(err)
		}
		t.Cleanup(func() { deleteSecAccess(t, c) })
	case err != nil:
		t.Fatal(err)
	default:
		access.Spec = spec
		if err := c.Update(ctx, access); err != nil {
			t.Fatal(err)
		}
	}
	access.Status.Stop = stop
	if err := c.Status().Update(ctx, access); err != nil {
		t.Fatal(err)
	}
}

func deleteSecAccess(t *testing.T, c client.Client) {
	t.Helper()
	access := &v1alpha1.SecAccess{ObjectMeta: metav1.ObjectMeta{Name: v1alpha1.SecAccessName}}
	if err := client.IgnoreNotFound(c.Delete(context.Background(), access)); err != nil {
		t.Fatal(err)
	}
	testenv.Eventually(t, 10*time.Second, func() error {
		err := c.Get(context.Background(), client.ObjectKey{Name: v1alpha1.SecAccessName}, &v1alpha1.SecAccess{})
		if !apierrors.IsNotFound(err) {
			return fmt.Errorf("the SecAccess is still there: %w", err)
		}
		return nil
	})
}

func allowRequests(t *testing.T, c client.Client) {
	t.Helper()
	setSecAccess(t, c, v1alpha1.SecAccessSpec{}, nil)
}

type lease struct{ duration, renew, retry time.Duration }

// replica is one fetcher, run as one of its pods would run it, sending its
// requests to a server of its own.
type replica struct {
	server    *secServer
	identity  string
	cancel    context.CancelFunc
	done      chan struct{}
	err       error
	stoppedAt time.Time
}

// stores is what the replicas of one test share: NATS, with the chart's
// streams and consumers, and the store.
type stores struct {
	js      jetstream.JetStream
	events  jetstream.Consumer
	records *fakeRecords
	objects *fakeObjects
}

func newStores(t *testing.T) stores {
	t.Helper()
	_, js := testenv.NATS(t)
	return stores{js: js, events: makeStreams(context.Background(), t, js, 0), records: &fakeRecords{},
		objects: &fakeObjects{}}
}

// runReplica runs a fetcher in the namespace, with its Lease at the timings
// given, through config.
func runReplica(t *testing.T, namespace string, timings lease, config *rest.Config, s stores, server *secServer,
	client func(*ClientOptions)) *replica {
	t.Helper()
	r := &replica{server: server, identity: fmt.Sprintf("%s-%d", t.Name(), time.Now().UnixNano()),
		done: make(chan struct{})}
	opts := Options{
		Namespace:      namespace,
		LeaseName:      "fetcher",
		LeaseDuration:  timings.duration,
		RenewDeadline:  timings.renew,
		RetryPeriod:    timings.retry,
		Identity:       r.identity,
		MetricsAddress: "0",
		HealthAddress:  "0",
		JetStream:      s.js,
		Records:        s.records,
		Objects:        s.objects,
		Client:         server.options(),
		Commit:         testCommit,
		Log:            logr.Discard(),
		Worker: Worker{
			Idle: 10 * time.Millisecond, Hold: 20 * time.Millisecond, InProgress: 200 * time.Millisecond,
			Wait: 50 * time.Millisecond,
		},
	}
	if client != nil {
		client(&opts.Client)
	}
	ctx, cancel := context.WithCancel(context.Background())
	r.cancel = cancel
	go func() {
		defer close(r.done)
		r.err = Run(ctx, config, opts)
		r.stoppedAt = time.Now()
	}()
	t.Cleanup(r.stop)
	return r
}

// stop stops the replica as Kubernetes stops its pod, and waits until it
// has.
func (r *replica) stop() {
	r.cancel()
	<-r.done
}

func getLease(t *testing.T, c client.Client, namespace string) *coordinationv1.Lease {
	t.Helper()
	held := &coordinationv1.Lease{}
	if err := c.Get(context.Background(), client.ObjectKey{Namespace: namespace, Name: "fetcher"}, held); err != nil {
		t.Fatal(err)
	}
	return held
}

// holds waits until the replica holds the Lease, and gives it.
func holds(t *testing.T, c client.Client, namespace string, r *replica, within time.Duration) *coordinationv1.Lease {
	t.Helper()
	var held *coordinationv1.Lease
	testenv.Eventually(t, within, func() error {
		found := &coordinationv1.Lease{}
		err := c.Get(context.Background(), client.ObjectKey{Namespace: namespace, Name: "fetcher"}, found)
		if err != nil {
			return err
		}
		if found.Spec.HolderIdentity == nil || *found.Spec.HolderIdentity != r.identity {
			return fmt.Errorf("the Lease's holder is %v", found.Spec.HolderIdentity)
		}
		held = found
		return nil
	})
	return held
}

// sends waits until the server has received n requests in all.
func sends(t *testing.T, s *secServer, n int, within time.Duration) {
	t.Helper()
	testenv.Eventually(t, within, func() error {
		if got := len(s.seen()); got < n {
			return fmt.Errorf("%d requests, not %d", got, n)
		}
		return nil
	})
}

// sendsNoMore checks that the server receives no more than n requests in
// the time given.
func sendsNoMore(t *testing.T, s *secServer, n int, over time.Duration) {
	t.Helper()
	time.Sleep(over)
	if got := len(s.seen()); got != n {
		t.Fatalf("%d requests, not %d", got, n)
	}
}

// spec: FETCH-16, FETCHER-10
func TestOnlyTheReplicaThatHoldsTheLeaseSendsAndReadsJobsAndOneStoppedLeavesTheLeaseToExpire(t *testing.T) {
	ctx := context.Background()
	c := newKubeClient(t)
	release := newNamespace(t, c)
	allowRequests(t, c)
	s := newStores(t)
	timings := lease{duration: 4 * time.Second, renew: 3 * time.Second, retry: time.Second}
	first := runReplica(t, release, timings, apiServer(t), s, newSECServer(t, pathBody), nil)
	holds(t, c, release, first, 10*time.Second)
	second := runReplica(t, release, timings, apiServer(t), s, newSECServer(t, pathBody), nil)
	for i := range 3 {
		queue(ctx, t, s.js, newJob(messages.KindFiling, fmt.Sprintf("https://www.sec.gov/before-%d", i)))
	}
	settled(ctx, t, s.js, 10*time.Second)
	sendsNoMore(t, second.server, 0, 2*time.Second)
	if got := len(first.server.seen()); got != 3 {
		t.Fatalf("the active replica sent %d requests, for 3 jobs", got)
	}

	first.stop()
	if first.err != nil {
		t.Errorf("the replica stopped with %v", first.err)
	}
	// It didn't release the Lease, which the other takes over only once its
	// duration has passed since its last renewal.
	left := getLease(t, c, release)
	if *left.Spec.HolderIdentity != first.identity {
		t.Fatalf("the stopped replica released the Lease: %+v", left.Spec)
	}
	for i := range 2 {
		queue(ctx, t, s.js, newJob(messages.KindFiling, fmt.Sprintf("https://www.sec.gov/after-%d", i)))
	}
	taken := holds(t, c, release, second, timings.duration+3*timings.retry)
	if acquired, renewed := taken.Spec.AcquireTime.Time, left.Spec.RenewTime.Time; acquired.Before(
		renewed.Add(timings.duration)) {
		t.Errorf("the other replica took the Lease over %s after the last renewal, within its duration",
			acquired.Sub(renewed))
	}
	sends(t, second.server, 2, 10*time.Second)
	if got := len(first.server.seen()); got != 3 {
		t.Errorf("the stopped replica sent %d more requests", got-3)
	}
	if *taken.Spec.LeaseDurationSeconds != int32(timings.duration.Seconds()) {
		t.Errorf("the Lease's duration: %d", *taken.Spec.LeaseDurationSeconds)
	}
}

// spec: FETCH-16
func TestAReplicaThatCantRenewTheLeaseStopsSendingBeforeAnotherCanTakeItOver(t *testing.T) {
	for _, loss := range []struct {
		name string
		lose func(*testenv.Proxy)
	}{
		{"its connections to the API server closed", (*testenv.Proxy).CutOff},
		{"its requests to the API server unanswered", (*testenv.Proxy).Hang},
	} {
		t.Run(loss.name, func(t *testing.T) {
			ctx := context.Background()
			c := newKubeClient(t)
			release := newNamespace(t, c)
			allowRequests(t, c)
			s := newStores(t)
			server, err := url.Parse(apiServer(t).Host)
			if err != nil {
				t.Fatal(err)
			}
			relay := testenv.NewProxy(t, server.Host)
			relayed := rest.CopyConfig(apiServer(t))
			relayed.Host = "https://" + relay.Addr()
			// Timings the chart accepts (LEASE-1), whose retry period and
			// renewal deadline together pass the Lease's duration: client-go
			// tries to renew the Lease a retry period after its last renewal,
			// and stops leading once that try has gone on for the renewal
			// deadline, after another replica could take the Lease over.
			timings := lease{duration: 6 * time.Second, renew: 4 * time.Second, retry: 3 * time.Second}
			steady := func(o *ClientOptions) { o.MinInterval = 50 * time.Millisecond }
			lost := runReplica(t, release, timings, relayed, s, newSECServer(t, pathBody), steady)
			holds(t, c, release, lost, 10*time.Second)
			other := runReplica(t, release, timings, apiServer(t), s, newSECServer(t, pathBody), steady)
			for i := range 600 {
				queue(ctx, t, s.js, newJob(messages.KindFiling, fmt.Sprintf("https://www.sec.gov/filing-%d", i)))
			}
			sends(t, lost.server, 20, 10*time.Second)

			loss.lose(relay)
			// The Lease as the replica last renewed it: another replica can
			// take it over once its duration has passed since.
			time.Sleep(500 * time.Millisecond)
			held := getLease(t, c, release)
			if *held.Spec.HolderIdentity != lost.identity {
				t.Fatalf("the Lease isn't the replica's: %+v", held.Spec)
			}
			takeable := held.Spec.RenewTime.Add(timings.duration)
			select {
			case <-lost.done:
			case <-time.After(30 * time.Second):
				t.Fatal("the replica that lost the API server didn't stop")
			}
			if lost.err == nil {
				t.Error("the replica that lost the API server stopped without an error")
			}
			if !lost.stoppedAt.Before(takeable) {
				t.Errorf("it stopped at %s, once another replica could take the Lease over, at %s",
					lost.stoppedAt.Format(time.StampMilli), takeable.Format(time.StampMilli))
			}
			sends(t, other.server, 1, timings.duration+5*timings.retry)
			sent, taken := lost.server.seen(), other.server.seen()
			if last := sent[len(sent)-1].at; !last.Before(takeable) || last.After(lost.stoppedAt) {
				t.Errorf("it sent a request at %s, after it stopped at %s, or once another could take the Lease"+
					" over, at %s", last.Format(time.StampMilli), lost.stoppedAt.Format(time.StampMilli),
					takeable.Format(time.StampMilli))
			}
			if first := taken[0].at; !first.After(lost.stoppedAt) || first.Before(takeable) {
				t.Errorf("the other replica sent its first request at %s, before the first stopped at %s, or"+
					" before it could take the Lease over, at %s", first.Format(time.StampMilli),
					lost.stoppedAt.Format(time.StampMilli), takeable.Format(time.StampMilli))
			}
		})
	}
}

// spec: ACCESS-1
func TestRequestsAreSentOnlyWhileTheyreAllowed(t *testing.T) {
	ctx := context.Background()
	c := newKubeClient(t)
	release := newNamespace(t, c)
	deleteSecAccess(t, c)
	s := newStores(t)
	r := runReplica(t, release, lease{duration: 4 * time.Second, renew: 3 * time.Second, retry: time.Second},
		apiServer(t), s, newSECServer(t, pathBody), nil)
	holds(t, c, release, r, 10*time.Second)
	queue(ctx, t, s.js, newJob(messages.KindFiling, "https://www.sec.gov/first"))

	t.Run("with no SecAccess", func(t *testing.T) {
		sendsNoMore(t, r.server, 0, 1500*time.Millisecond)
	})
	t.Run("with the SecAccess paused", func(t *testing.T) {
		setSecAccess(t, c, v1alpha1.SecAccessSpec{Paused: true}, nil)
		sendsNoMore(t, r.server, 0, 1500*time.Millisecond)
	})
	t.Run("with a stop the owner hasn't acknowledged", func(t *testing.T) {
		setSecAccess(t, c, v1alpha1.SecAccessSpec{AcknowledgedStopAt: "2026-10-20T09:00:00.000Z"},
			&v1alpha1.Stop{At: "2026-10-22T09:30:00.123Z", URL: "https://www.sec.gov/earlier"})
		sendsNoMore(t, r.server, 0, 1500*time.Millisecond)
	})
	t.Run("once the owner acknowledges it", func(t *testing.T) {
		setSecAccess(t, c, v1alpha1.SecAccessSpec{AcknowledgedStopAt: "2026-10-22T09:30:00.123Z"},
			&v1alpha1.Stop{At: "2026-10-22T09:30:00.123Z", URL: "https://www.sec.gov/earlier"})
		sends(t, r.server, 1, 10*time.Second)
	})
	t.Run("paused again", func(t *testing.T) {
		setSecAccess(t, c, v1alpha1.SecAccessSpec{Paused: true}, nil)
		time.Sleep(500 * time.Millisecond)
		queue(ctx, t, s.js, newJob(messages.KindFiling, "https://www.sec.gov/second"))
		sendsNoMore(t, r.server, 1, 1500*time.Millisecond)
	})
	t.Run("allowed again", func(t *testing.T) {
		allowRequests(t, c)
		sends(t, r.server, 2, 10*time.Second)
	})
}

// spec: ACCESS-3, FETCH-9
func TestA403IsRecordedAsAStopThatHoldsUntilTheOwnerAcknowledgesItEvenAfterAHandover(t *testing.T) {
	ctx := context.Background()
	c := newKubeClient(t)
	release := newNamespace(t, c)
	allowRequests(t, c)
	s := newStores(t)
	timings := lease{duration: 3 * time.Second, renew: 2 * time.Second, retry: time.Second}
	first := runReplica(t, release, timings, apiServer(t), s, newSECServer(t, func(w http.ResponseWriter,
		r *http.Request) {
		if r.URL.Path == "/forbidden" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		pathBody(w, r)
	}), nil)
	holds(t, c, release, first, 10*time.Second)
	queue(ctx, t, s.js, newJob(messages.KindPoll, "https://www.sec.gov/forbidden"))
	queue(ctx, t, s.js, newJob(messages.KindFiling, "https://www.sec.gov/after"))

	got := nextEvent(t, s.events, 10*time.Second)
	var record Record
	if err := json.Unmarshal(got.event.Record, &record); err != nil {
		t.Fatal(err)
	}
	if value(record.Status) != http.StatusForbidden {
		t.Fatalf("the first record: %+v", record)
	}
	want := v1alpha1.Stop{At: v1alpha1.Timestamp(record.RequestedAt), URL: "https://www.sec.gov/forbidden"}
	access := &v1alpha1.SecAccess{}
	if err := c.Get(ctx, client.ObjectKey{Name: v1alpha1.SecAccessName}, access); err != nil {
		t.Fatal(err)
	}
	if access.Status.Stop == nil || *access.Status.Stop != want {
		t.Fatalf("the SecAccess's stop: %+v, not %+v", access.Status.Stop, want)
	}
	sendsNoMore(t, first.server, 1, 1500*time.Millisecond)

	// A replica that takes the Lease over reads the stop, and sends nothing.
	first.stop()
	second := runReplica(t, release, timings, apiServer(t), s, newSECServer(t, pathBody), nil)
	holds(t, c, release, second, timings.duration+5*timings.retry)
	sendsNoMore(t, second.server, 0, 1500*time.Millisecond)

	setSecAccess(t, c, v1alpha1.SecAccessSpec{AcknowledgedStopAt: want.At}, &want)
	sends(t, second.server, 1, 10*time.Second)
	if seen := second.server.seen(); seen[0].url != "https://www.sec.gov/after" {
		t.Errorf("it fetched %s", seen[0].url)
	}
}

// spec: FETCH-17, FETCH-2
func TestWithoutTheOwnersUserAgentTheFetcherSendsNothing(t *testing.T) {
	c := newKubeClient(t)
	release := newNamespace(t, c)
	allowRequests(t, c)
	s := newStores(t)
	queue(context.Background(), t, s.js, newJob(messages.KindFiling, "https://www.sec.gov/filing"))
	for _, agent := range []string{"", "a name without a contact address"} {
		server := newSECServer(t, pathBody)
		r := runReplica(t, release, lease{duration: 4 * time.Second, renew: 3 * time.Second, retry: time.Second},
			apiServer(t), s, server, func(o *ClientOptions) { o.UserAgent = agent })
		select {
		case <-r.done:
		case <-time.After(5 * time.Second):
			t.Fatalf("with the user agent %q, it ran", agent)
		}
		if r.err == nil {
			t.Errorf("with the user agent %q, it stopped without an error", agent)
		}
		if len(server.seen()) != 0 {
			t.Errorf("with the user agent %q, it sent %d requests", agent, len(server.seen()))
		}
	}
	err := c.Get(context.Background(), client.ObjectKey{Namespace: release, Name: "fetcher"}, &coordinationv1.Lease{})
	if !apierrors.IsNotFound(err) {
		t.Errorf("it took the Lease: %v", err)
	}
}
