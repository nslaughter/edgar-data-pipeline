package scheduler

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/go-logr/logr"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	coordinationv1 "k8s.io/api/coordination/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/rest"
	"k8s.io/utils/clock"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
	"github.com/nslaughter/edgar-data-pipeline/internal/testenv"
)

var restConfig *rest.Config

func TestMain(m *testing.M) {
	env, cfg, err := testenv.StartKubernetes()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	restConfig = cfg
	code := m.Run()
	if err := env.Stop(); err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
	os.Exit(code)
}

func newClient(t *testing.T) client.Client {
	t.Helper()
	scheme := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{
		v1alpha1.AddToScheme, corev1.AddToScheme, coordinationv1.AddToScheme,
	} {
		if err := add(scheme); err != nil {
			t.Fatal(err)
		}
	}
	c, err := client.New(restConfig, client.Options{Scheme: scheme})
	if err != nil {
		t.Fatal(err)
	}
	return c
}

var namespaces atomic.Int32

// newNamespace makes a namespace of the test's own, the release namespace of
// the schedulers it runs.
func newNamespace(t *testing.T, c client.Client) string {
	t.Helper()
	name := fmt.Sprintf("release-%d", namespaces.Add(1))
	if err := c.Create(context.Background(), &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: name}}); err != nil {
		t.Fatal(err)
	}
	return name
}

func newSource(t *testing.T, c client.Client, namespace, name string, cadence int32, paused bool) {
	t.Helper()
	createSource(t, c, namespace, name, v1alpha1.SourceSpec{
		Feed:           "submissions",
		URL:            "https://data.sec.gov/submissions/CIK0000320193.json",
		CadenceSeconds: cadence,
		Paused:         paused,
	})
}

func createSource(t *testing.T, c client.Client, namespace, name string, spec v1alpha1.SourceSpec) {
	t.Helper()
	source := &v1alpha1.Source{ObjectMeta: metav1.ObjectMeta{Namespace: namespace, Name: name}, Spec: spec}
	if err := c.Create(context.Background(), source); err != nil {
		t.Fatal(err)
	}
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
}

func allowRequests(t *testing.T, c client.Client) {
	t.Helper()
	setSecAccess(t, c, v1alpha1.SecAccessSpec{}, nil)
}

// replica is one scheduler, run as one of its pods would run it.
type replica struct {
	mgr       ctrl.Manager
	identity  string
	publisher *fakePublisher
	cancel    context.CancelFunc
	done      chan struct{}
	err       error
	stoppedAt time.Time
}

type lease struct{ duration, renew, retry time.Duration }

var defaultLease = lease{15 * time.Second, 10 * time.Second, 2 * time.Second}

// runScheduler runs a scheduler in the namespace, with its Lease at the
// timings given, through restConfig, queuing its polls to a publisher of its
// own unless js is given.
func runScheduler(t *testing.T, namespace string, timings lease, config *rest.Config, js jetstream.JetStream) *replica {
	t.Helper()
	r := &replica{done: make(chan struct{})}
	opts := Options{
		Namespace:               namespace,
		LeaseName:               "scheduler",
		LeaseDuration:           timings.duration,
		RenewDeadline:           timings.renew,
		RetryPeriod:             timings.retry,
		Identity:                fmt.Sprintf("%s-%d", t.Name(), time.Now().UnixNano()),
		MetricsAddress:          "0",
		HealthAddress:           "0",
		GracefulShutdownTimeout: 5 * time.Second,
		JetStream:               js,
		Log:                     logr.Discard(),
		SkipNameValidation:      true,
	}
	if js == nil {
		_, opts.JetStream = testenv.NATS(t)
		r.publisher = &fakePublisher{}
		opts.Publisher = r.publisher
	}
	mgr, err := NewManager(config, opts)
	if err != nil {
		t.Fatal(err)
	}
	r.mgr, r.identity = mgr, opts.Identity
	ctx, cancel := context.WithCancel(context.Background())
	r.cancel = cancel
	go func() {
		defer close(r.done)
		r.err = mgr.Start(ctx)
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

func (r *replica) elected() bool {
	select {
	case <-r.mgr.Elected():
		return true
	default:
		return false
	}
}

// polls gives the times at which the replica queued the Source's polls.
func (r *replica) polls(source string) []time.Time {
	var times []time.Time
	for _, p := range r.publisher.jobs() {
		if p.Job.Source == source {
			queued, _ := time.Parse(v1alpha1.TimestampLayout, p.Job.QueuedAt)
			times = append(times, queued)
		}
	}
	return times
}

func getSource(t *testing.T, c client.Client, namespace, name string) *v1alpha1.Source {
	t.Helper()
	source := &v1alpha1.Source{}
	if err := c.Get(context.Background(), client.ObjectKey{Namespace: namespace, Name: name}, source); err != nil {
		t.Fatal(err)
	}
	return source
}

// hasCondition waits until the Source's condition has the status and
// reason given, and returns it.
func hasCondition(t *testing.T, c client.Client, namespace, name, kind string, status metav1.ConditionStatus,
	reason string) metav1.Condition {
	t.Helper()
	var found metav1.Condition
	testenv.Eventually(t, 10*time.Second, func() error {
		condition := meta.FindStatusCondition(getSource(t, c, namespace, name).Status.Conditions, kind)
		if condition == nil {
			return fmt.Errorf("%s has no %s condition", name, kind)
		}
		if condition.Status != status || condition.Reason != reason {
			return fmt.Errorf("%s's %s is %s, %s: %s", name, kind, condition.Status, condition.Reason, condition.Message)
		}
		found = *condition
		return nil
	})
	return found
}

// queuesNoMore checks that the replica queues no more of the Source's polls
// over three of its 1-second cadences.
func queuesNoMore(t *testing.T, r *replica, source string) {
	t.Helper()
	before := len(r.polls(source))
	time.Sleep(3 * time.Second)
	if after := len(r.polls(source)); after != before {
		t.Fatalf("queued %d more polls of %s", after-before, source)
	}
}

func queuesAgain(t *testing.T, r *replica, source string) {
	t.Helper()
	before := len(r.polls(source))
	testenv.Eventually(t, 5*time.Second, func() error {
		if len(r.polls(source)) <= before {
			return fmt.Errorf("no poll of %s queued", source)
		}
		return nil
	})
}

// spec: SOURCE-1, ACCESS-2
func TestOnlyASourceThatsPollingIsPolledAndItsConditionSaysWhyNot(t *testing.T) {
	c := newClient(t)
	release := newNamespace(t, c)
	other := newNamespace(t, c)
	newSource(t, c, release, "polled", 1, false)
	newSource(t, c, release, "paused", 1, true)
	newSource(t, c, other, "elsewhere", 1, false)
	deleteSecAccess(t, c)
	r := runScheduler(t, release, defaultLease, restConfig, nil)

	t.Run("with no SecAccess", func(t *testing.T) {
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionFalse, v1alpha1.ReasonNoSecAccess)
		hasCondition(t, c, release, "paused", v1alpha1.ConditionPolling, metav1.ConditionFalse, v1alpha1.ReasonNoSecAccess)
		queuesNoMore(t, r, "polled")
	})
	t.Run("with requests stopped and paused", func(t *testing.T) {
		setSecAccess(t, c, v1alpha1.SecAccessSpec{Paused: true, AcknowledgedStopAt: "2026-10-20T09:00:00.000Z"},
			&v1alpha1.Stop{At: "2026-10-22T09:30:00.123Z", URL: "https://data.sec.gov/submissions/CIK0000320193.json"})
		polled := hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionFalse,
			v1alpha1.ReasonRequestsStopped)
		if !strings.Contains(polled.Message, "2026-10-22T09:30:00.123Z") {
			t.Errorf("the message doesn't give the stop: %s", polled.Message)
		}
		hasCondition(t, c, release, "paused", v1alpha1.ConditionPolling, metav1.ConditionFalse,
			v1alpha1.ReasonRequestsStopped)
		queuesNoMore(t, r, "polled")
	})
	t.Run("with requests paused, the stop acknowledged", func(t *testing.T) {
		setSecAccess(t, c, v1alpha1.SecAccessSpec{Paused: true, AcknowledgedStopAt: "2026-10-22T09:30:00.123Z"},
			&v1alpha1.Stop{At: "2026-10-22T09:30:00.123Z", URL: "https://data.sec.gov/submissions/CIK0000320193.json"})
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionFalse, v1alpha1.ReasonRequestsPaused)
		hasCondition(t, c, release, "paused", v1alpha1.ConditionPolling, metav1.ConditionFalse, v1alpha1.ReasonRequestsPaused)
		queuesNoMore(t, r, "polled")
	})
	t.Run("with requests allowed", func(t *testing.T) {
		setSecAccess(t, c, v1alpha1.SecAccessSpec{AcknowledgedStopAt: "2026-10-22T09:30:00.123Z"},
			&v1alpha1.Stop{At: "2026-10-22T09:30:00.123Z", URL: "https://data.sec.gov/submissions/CIK0000320193.json"})
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionTrue, v1alpha1.ReasonPolling)
		hasCondition(t, c, release, "paused", v1alpha1.ConditionPolling, metav1.ConditionFalse, v1alpha1.ReasonPaused)
		queuesAgain(t, r, "polled")
		queuesNoMore(t, r, "paused")
	})
	t.Run("with a new stop", func(t *testing.T) {
		setSecAccess(t, c, v1alpha1.SecAccessSpec{AcknowledgedStopAt: "2026-10-22T09:30:00.123Z"},
			&v1alpha1.Stop{At: "2026-10-22T10:00:00.000Z", URL: "https://data.sec.gov/submissions/CIK0000320193.json"})
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionFalse,
			v1alpha1.ReasonRequestsStopped)
		queuesNoMore(t, r, "polled")
	})
	t.Run("with requests allowed again, then paused", func(t *testing.T) {
		allowRequests(t, c)
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionTrue, v1alpha1.ReasonPolling)
		queuesAgain(t, r, "polled")
		setSecAccess(t, c, v1alpha1.SecAccessSpec{Paused: true}, nil)
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionFalse, v1alpha1.ReasonRequestsPaused)
		queuesNoMore(t, r, "polled")
	})
	t.Run("with the SecAccess removed", func(t *testing.T) {
		allowRequests(t, c)
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionTrue, v1alpha1.ReasonPolling)
		deleteSecAccess(t, c)
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionFalse, v1alpha1.ReasonNoSecAccess)
		queuesNoMore(t, r, "polled")
	})
	t.Run("only in the release namespace", func(t *testing.T) {
		if polls := r.polls("elsewhere"); len(polls) != 0 {
			t.Errorf("polled a Source in another namespace %d times", len(polls))
		}
		if conditions := getSource(t, c, other, "elsewhere").Status.Conditions; len(conditions) != 0 {
			t.Errorf("set the conditions of a Source in another namespace: %v", conditions)
		}
	})
}

// spec: SOURCE-2
func TestASourcesPollsAreQueuedACadenceApartFromWhenItBeginsPolling(t *testing.T) {
	c := newClient(t)
	release := newNamespace(t, c)
	allowRequests(t, c)
	r := runScheduler(t, release, defaultLease, restConfig, nil)
	testenv.Eventually(t, 10*time.Second, func() error {
		if !r.elected() {
			return errors.New("not elected")
		}
		return nil
	})
	created := time.Now()
	newSource(t, c, release, "polled", 2, false)
	testenv.Eventually(t, 10*time.Second, func() error {
		if len(r.polls("polled")) < 3 {
			return errors.New("fewer than 3 polls")
		}
		return nil
	})
	polls := r.polls("polled")
	if first := polls[0].Sub(created); first > time.Second {
		t.Errorf("the first poll was queued %s after the Source was created", first)
	}
	for i := 1; i < 3; i++ {
		if gap := polls[i].Sub(polls[i-1]); gap < 1900*time.Millisecond || gap > 2100*time.Millisecond {
			t.Errorf("polls %d and %d were %s apart", i-1, i, gap)
		}
	}
}

// spec: SOURCE-3
func TestEachSourcesConditionSaysWhetherTheSourcesAreWithinTheBudget(t *testing.T) {
	c := newClient(t)
	release := newNamespace(t, c)
	allowRequests(t, c)
	newSource(t, c, release, "first", 1, false)
	newSource(t, c, release, "second", 1, false)
	newSource(t, c, release, "third", 2, false)
	newSource(t, c, release, "paused", 1, true)
	r := runScheduler(t, release, defaultLease, restConfig, nil)
	for _, name := range []string{"first", "second", "third", "paused"} {
		within := hasCondition(t, c, release, name, v1alpha1.ConditionWithinBudget, metav1.ConditionTrue,
			v1alpha1.ReasonWithinBudget)
		if !strings.Contains(within.Message, " 2.5 requests a second") {
			t.Errorf("%s: the message doesn't give the total: %s", name, within.Message)
		}
	}

	newSource(t, c, release, "fourth", 1000, false)
	for _, name := range []string{"first", "second", "third", "paused", "fourth"} {
		over := hasCondition(t, c, release, name, v1alpha1.ConditionWithinBudget, metav1.ConditionFalse,
			v1alpha1.ReasonOverBudget)
		if !strings.Contains(over.Message, " 2.501 requests a second") {
			t.Errorf("%s: the message doesn't give the total: %s", name, over.Message)
		}
	}
	// Over the budget, it polls them as it would otherwise.
	queuesAgain(t, r, "first")
	testenv.Eventually(t, 5*time.Second, func() error {
		if len(r.polls("fourth")) == 0 {
			return errors.New("no poll of fourth queued")
		}
		return nil
	})

	fourth := getSource(t, c, release, "fourth")
	fourth.Spec.Paused = true
	if err := c.Update(context.Background(), fourth); err != nil {
		t.Fatal(err)
	}
	hasCondition(t, c, release, "first", v1alpha1.ConditionWithinBudget, metav1.ConditionTrue, v1alpha1.ReasonWithinBudget)
}

func elected(t *testing.T, r *replica, within time.Duration) {
	t.Helper()
	testenv.Eventually(t, within, func() error {
		if !r.elected() {
			return errors.New("not the active replica")
		}
		return nil
	})
}

// spec: SCHED-4
func TestOnlyTheReplicaThatHoldsTheLeaseActsAndOneStoppedReleasesIt(t *testing.T) {
	c := newClient(t)
	release := newNamespace(t, c)
	allowRequests(t, c)
	newSource(t, c, release, "polled", 1, false)
	timings := lease{duration: 10 * time.Second, renew: 8 * time.Second, retry: time.Second}
	first := runScheduler(t, release, timings, restConfig, nil)
	elected(t, first, 10*time.Second)
	second := runScheduler(t, release, timings, restConfig, nil)
	queuesAgain(t, first, "polled")
	hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionTrue, v1alpha1.ReasonPolling)
	time.Sleep(3 * time.Second)
	if second.elected() || len(second.polls("polled")) != 0 {
		t.Fatalf("the standby acted: elected %v, %d polls", second.elected(), len(second.polls("polled")))
	}

	first.stop()
	stopped := time.Now()
	polled := len(first.polls("polled"))
	// The other replica takes the Lease over at its next try, well before
	// the Lease's duration since its last renewal.
	elected(t, second, 5*time.Second)
	if took := time.Since(stopped); took >= timings.duration-timings.retry {
		t.Errorf("the other replica took %s to take the Lease over", took)
	}
	queuesAgain(t, second, "polled")
	if after := len(first.polls("polled")); after != polled {
		t.Errorf("the stopped replica queued %d more polls", after-polled)
	}
	held := &coordinationv1.Lease{}
	if err := c.Get(context.Background(), client.ObjectKey{Namespace: release, Name: "scheduler"}, held); err != nil {
		t.Fatal(err)
	}
	if held.Spec.HolderIdentity == nil || *held.Spec.LeaseDurationSeconds != int32(timings.duration.Seconds()) {
		t.Errorf("the Lease: %+v", held.Spec)
	}
}

// spec: SCHED-4
func TestAStandbyReadsNoPollEventsSetsNoStatusAndQueuesNoPolls(t *testing.T) {
	c := newClient(t)
	ctx := context.Background()
	release := newNamespace(t, c)
	allowRequests(t, c)
	newSource(t, c, release, "polled", 1, false)
	// Another replica holds the Lease, for an hour from now.
	holder, duration, now := "another", int32(3600), metav1.NewMicroTime(time.Now())
	held := &coordinationv1.Lease{
		ObjectMeta: metav1.ObjectMeta{Namespace: release, Name: "scheduler"},
		Spec: coordinationv1.LeaseSpec{
			HolderIdentity: &holder, LeaseDurationSeconds: &duration, AcquireTime: &now, RenewTime: &now,
		},
	}
	if err := c.Create(ctx, held); err != nil {
		t.Fatal(err)
	}
	_, js := testenv.NATS(t)
	jobs := testenv.MakeJobs(ctx, t, js)
	testenv.MakeRecords(ctx, t, js)
	consumer, err := js.Consumer(ctx, "RECORDS", "scheduler-polls")
	if err != nil {
		t.Fatal(err)
	}
	event := pollEvent(t, 1, "polled", "2026-10-22T14:00:10.000Z", strings.Repeat("ab", 32))
	if _, err := js.Publish(ctx, "edgar.records.poll", event); err != nil {
		t.Fatal(err)
	}
	standby := runScheduler(t, release, defaultLease, restConfig, js)
	queued := func() (uint64, error) {
		info, err := jobs.Info(ctx)
		if err != nil {
			return 0, err
		}
		return info.State.Msgs, nil
	}

	t.Run("while another holds the Lease, it does none of what the active replica does", func(t *testing.T) {
		// Long enough for an active replica to have done each.
		time.Sleep(4 * time.Second)
		if standby.elected() {
			t.Fatal("it became the active replica while another held the Lease")
		}
		if status := getSource(t, c, release, "polled").Status; len(status.Conditions) != 0 || status.LastPollTime != "" {
			t.Errorf("it set the Source's status: %+v", status)
		}
		info, err := consumer.Info(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if info.NumPending != 1 || info.NumAckPending != 0 || info.Delivered.Consumer != 0 {
			t.Errorf("it read from scheduler-polls: %d pending, %d unacknowledged, %d delivered",
				info.NumPending, info.NumAckPending, info.Delivered.Consumer)
		}
		if polls, err := queued(); err != nil {
			t.Error(err)
		} else if polls != 0 {
			t.Errorf("it queued %d polls", polls)
		}
	})
	t.Run("once the other releases the Lease, it does each", func(t *testing.T) {
		if err := c.Get(ctx, client.ObjectKeyFromObject(held), held); err != nil {
			t.Fatal(err)
		}
		none, second := "", int32(1)
		held.Spec.HolderIdentity, held.Spec.LeaseDurationSeconds = &none, &second
		if err := c.Update(ctx, held); err != nil {
			t.Fatal(err)
		}
		elected(t, standby, 10*time.Second)
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionTrue, v1alpha1.ReasonPolling)
		testenv.Eventually(t, 10*time.Second, func() error {
			if last, _ := pollTimes(t, c, release, "polled"); last != "2026-10-22T14:00:10.000Z" {
				return fmt.Errorf("the last poll's time is %q", last)
			}
			info, err := consumer.Info(ctx)
			if err != nil {
				return err
			}
			if info.NumPending != 0 || info.NumAckPending != 0 {
				return fmt.Errorf("%d pending, %d unacknowledged", info.NumPending, info.NumAckPending)
			}
			polls, err := queued()
			if err != nil {
				return err
			}
			if polls == 0 {
				return errors.New("no poll queued")
			}
			return nil
		})
	})
}

// proxy relays TCP connections to the API server until it's cut, when it
// closes them and refuses new ones, or until it hangs, when it keeps them
// open, and accepts new ones, but relays nothing more, as a network that
// drops what it's sent.
type proxy struct {
	listener net.Listener
	mu       sync.Mutex
	conns    []net.Conn
	cut      bool
	hung     bool
	closed   chan struct{}
}

func newProxy(t *testing.T, target string) *proxy {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	p := &proxy{listener: listener, closed: make(chan struct{})}
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
	t.Cleanup(p.cutOff)
	return p
}

// relay copies what src sends to dst until either is closed. Once the proxy
// hangs, it holds what it reads, and sends nothing more.
func (p *proxy) relay(dst, src net.Conn) {
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

func (p *proxy) cutOff() {
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

func (p *proxy) hang() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.hung = true
}

// spec: SCHED-4
func TestAReplicaThatCantRenewTheLeaseStopsActingBeforeAnotherTakesItOver(t *testing.T) {
	for _, loss := range []struct {
		name string
		lose func(*proxy)
	}{
		{"its connections to the API server closed", (*proxy).cutOff},
		{"its requests to the API server unanswered", (*proxy).hang},
	} {
		t.Run(loss.name, func(t *testing.T) {
			c := newClient(t)
			release := newNamespace(t, c)
			allowRequests(t, c)
			newSource(t, c, release, "polled", 1, false)
			server, err := url.Parse(restConfig.Host)
			if err != nil {
				t.Fatal(err)
			}
			relay := newProxy(t, server.Host)
			relayed := rest.CopyConfig(restConfig)
			relayed.Host = "https://" + relay.listener.Addr().String()
			// Timings the chart accepts (SCHED-3), whose retry period and
			// renewal deadline together pass the Lease's duration: client-go
			// tries to renew the Lease a retry period after its last renewal,
			// and stops leading once that try has gone on for the renewal
			// deadline, after another replica could take the Lease over.
			timings := lease{duration: 6 * time.Second, renew: 4 * time.Second, retry: 3 * time.Second}
			lost := runScheduler(t, release, timings, relayed, nil)
			elected(t, lost, 10*time.Second)
			other := runScheduler(t, release, timings, restConfig, nil)
			queuesAgain(t, lost, "polled")
			// While it renews the Lease, it acts past the renewal deadline.
			time.Sleep(timings.renew + timings.retry)
			select {
			case <-lost.done:
				t.Fatalf("it stopped while it could renew the Lease: %v", lost.err)
			default:
			}
			queuesAgain(t, lost, "polled")

			loss.lose(relay)
			// The Lease as the replica last renewed it: another replica can
			// take it over once its duration has passed since.
			time.Sleep(500 * time.Millisecond)
			held := &coordinationv1.Lease{}
			if err := c.Get(context.Background(), client.ObjectKey{Namespace: release, Name: "scheduler"}, held); err != nil {
				t.Fatal(err)
			}
			if held.Spec.HolderIdentity == nil || *held.Spec.HolderIdentity != lost.identity || held.Spec.RenewTime == nil {
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
			elected(t, other, timings.duration+5*timings.retry)
			queuesAgain(t, other, "polled")
			polls, taken := lost.polls("polled"), other.polls("polled")
			// It stopped acting before the other could take the Lease over,
			// so each of its polls came before the other's first.
			if last := polls[len(polls)-1]; !last.Before(taken[0]) {
				t.Errorf("it queued a poll at %s, after the other replica's first, at %s",
					last.Format(time.StampMilli), taken[0].Format(time.StampMilli))
			}
			if last := polls[len(polls)-1]; last.After(lost.stoppedAt) {
				t.Errorf("queued a poll at %s, after it stopped at %s", last, lost.stoppedAt)
			}
			if !taken[0].After(lost.stoppedAt) {
				t.Errorf("the other replica queued a poll at %s, before the first stopped at %s", taken[0], lost.stoppedAt)
			}
		})
	}
}

// pollEvent is an event of a poll of the Source, sent at requestedAt, whose
// record names a version if sha256 isn't empty.
func pollEvent(t *testing.T, id int, source, requestedAt, sha256 string) []byte {
	t.Helper()
	record := map[string]any{
		"feed": "submissions", "url": "https://data.sec.gov/submissions/CIK0000320193.json",
		"requested_at": requestedAt, "received_at": requestedAt, "attempts": 1, "status": 200,
		"error": nil, "final_url": nil, "date": "Thu, 22 Oct 2026 14:00:00 GMT", "etag": nil,
		"last_modified": nil, "content_type": "application/json", "sha256": nil, "bytes": nil,
		"object": nil, "tool": "fetcher@" + strings.Repeat("0", 40),
	}
	if sha256 != "" {
		record["sha256"], record["bytes"], record["object"] = sha256, 100, "objects/"+sha256[:2]+"/"+sha256+".gz"
	} else {
		record["status"], record["error"], record["received_at"], record["date"] = nil, "timed out", nil, nil
	}
	data, err := json.Marshal(map[string]any{
		"id": id,
		"job": map[string]any{
			"id": "0a5c0f3e-6d1b-4e8f-9c2a-7b4d3e2f1a0" + fmt.Sprint(id%10), "kind": "poll", "feed": "submissions",
			"url": "https://data.sec.gov/submissions/CIK0000320193.json", "source": source,
			"queued_at": requestedAt, "expires_at": "2026-10-22T15:00:00.000Z",
		},
		"record": record,
	})
	if err != nil {
		t.Fatal(err)
	}
	return data
}

func pollTimes(t *testing.T, c client.Client, namespace, name string) (last, lastSuccessful v1alpha1.Timestamp) {
	t.Helper()
	status := getSource(t, c, namespace, name).Status
	return status.LastPollTime, status.LastSuccessfulPollTime
}

// spec: SOURCE-4, SCHED-9, JOB-2
func TestPollEventsThroughTheConsumerSetEachSourcesPollTimes(t *testing.T) {
	c := newClient(t)
	release := newNamespace(t, c)
	allowRequests(t, c)
	// A feed and URL apart from the other tests', so a job's can't match
	// them by chance.
	spec := v1alpha1.SourceSpec{
		Feed: "latest-filings",
		URL: "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=&company=&dateb=&owner=include" +
			"&start=0&count=100&output=atom",
		CadenceSeconds: 1,
	}
	createSource(t, c, release, "polled", spec)
	_, js := testenv.NATS(t)
	ctx := context.Background()
	runScheduler(t, release, defaultLease, restConfig, js)

	t.Run("it waits for the streams and the consumer, and makes none", func(t *testing.T) {
		hasCondition(t, c, release, "polled", v1alpha1.ConditionPolling, metav1.ConditionTrue, v1alpha1.ReasonPolling)
		time.Sleep(3 * time.Second)
		streams := js.ListStreams(ctx)
		for stream := range streams.Info() {
			t.Errorf("made the stream %s", stream.Config.Name)
		}
		if err := streams.Err(); err != nil {
			t.Fatal(err)
		}
		// With RECORDS made but not its consumer, it waits longer than it
		// waits between looks for the consumer, and doesn't make it.
		records := testenv.MakeRecordsAlone(ctx, t, js)
		time.Sleep(4 * time.Second)
		var consumers []string
		for name := range records.ConsumerNames(ctx).Name() {
			consumers = append(consumers, name)
		}
		if len(consumers) != 0 {
			t.Errorf("made the consumers %v of RECORDS", consumers)
		}
	})

	jobs := testenv.MakeJobs(ctx, t, js)
	testenv.MakeSchedulerPolls(ctx, t, js)
	records := testenv.GiveRecordsSubjects(ctx, t, js)
	consumer, err := js.Consumer(ctx, "RECORDS", "scheduler-polls")
	if err != nil {
		t.Fatal(err)
	}
	made := consumer.CachedInfo().Config
	publish := func(data []byte) {
		t.Helper()
		if _, err := js.Publish(ctx, "edgar.records.poll", data); err != nil {
			t.Fatal(err)
		}
	}
	allAcknowledged := func(t *testing.T) {
		t.Helper()
		testenv.Eventually(t, 10*time.Second, func() error {
			info, err := consumer.Info(ctx)
			if err != nil {
				return err
			}
			if info.NumPending != 0 || info.NumAckPending != 0 {
				return fmt.Errorf("%d pending, %d unacknowledged", info.NumPending, info.NumAckPending)
			}
			return nil
		})
	}
	version := strings.Repeat("ab", 32)

	t.Run("once the consumer exists, it reads from it", func(t *testing.T) {
		publish(pollEvent(t, 1, "polled", "2026-10-22T14:00:10.000Z", version))
		testenv.Eventually(t, 10*time.Second, func() error {
			if last, ok := pollTimes(t, c, release, "polled"); last != "2026-10-22T14:00:10.000Z" ||
				ok != "2026-10-22T14:00:10.000Z" {
				return fmt.Errorf("poll times %s, %s", last, ok)
			}
			return nil
		})
	})
	t.Run("a poll that didn't succeed sets only the last poll's time", func(t *testing.T) {
		publish(pollEvent(t, 2, "polled", "2026-10-22T14:00:20.000Z", ""))
		testenv.Eventually(t, 10*time.Second, func() error {
			if last, ok := pollTimes(t, c, release, "polled"); last != "2026-10-22T14:00:20.000Z" ||
				ok != "2026-10-22T14:00:10.000Z" {
				return fmt.Errorf("poll times %s, %s", last, ok)
			}
			return nil
		})
	})
	t.Run("an earlier poll's event, delivered late, changes nothing", func(t *testing.T) {
		publish(pollEvent(t, 3, "polled", "2026-10-22T14:00:05.000Z", version))
		allAcknowledged(t)
		if last, ok := pollTimes(t, c, release, "polled"); last != "2026-10-22T14:00:20.000Z" ||
			ok != "2026-10-22T14:00:10.000Z" {
			t.Errorf("poll times %s, %s", last, ok)
		}
	})
	t.Run("an event it can't use is acknowledged without acting on it", func(t *testing.T) {
		publish([]byte(`{"id": 4, "job": {"kind": "poll"}}`))
		publish([]byte(`not JSON`))
		publish(pollEvent(t, 5, "removed", "2026-10-22T14:00:30.000Z", version))
		invalid := pollEvent(t, 6, "polled", "2026-10-22T14:00:40.000Z", version)
		publish([]byte(strings.Replace(string(invalid), `"kind":"poll"`, `"kind":"filing"`, 1)))
		allAcknowledged(t)
		if last, ok := pollTimes(t, c, release, "polled"); last != "2026-10-22T14:00:20.000Z" ||
			ok != "2026-10-22T14:00:10.000Z" {
			t.Errorf("poll times %s, %s", last, ok)
		}
	})
	t.Run("its polls are jobs in JOBS", func(t *testing.T) {
		schemas, err := messages.LoadSchemas()
		if err != nil {
			t.Fatal(err)
		}
		testenv.Eventually(t, 10*time.Second, func() error {
			msg, err := jobs.GetLastMsgForSubject(ctx, "edgar.jobs.poll")
			if err != nil {
				return err
			}
			if err := schemas.ValidateJob(msg.Data); err != nil {
				return err
			}
			var job messages.Job
			if err := json.Unmarshal(msg.Data, &job); err != nil {
				return err
			}
			if job.Kind != "poll" || job.Source != "polled" || job.Feed != spec.Feed || job.URL != spec.URL ||
				msg.Header.Get("Nats-Msg-Id") != job.ID {
				return fmt.Errorf("job %+v, message id %q", job, msg.Header.Get("Nats-Msg-Id"))
			}
			return nil
		})
	})
	t.Run("it changes no stream or consumer, and makes no other", func(t *testing.T) {
		info, err := consumer.Info(ctx)
		if err != nil {
			t.Fatal(err)
		}
		info.Config.Metadata, made.Metadata = nil, nil
		if fmt.Sprintf("%+v", info.Config) != fmt.Sprintf("%+v", made) {
			t.Errorf("the consumer changed: %+v", info.Config)
		}
		var names []string
		for name := range js.StreamNames(ctx).Name() {
			names = append(names, name)
		}
		if strings.Join(names, " ") != "JOBS RECORDS" && strings.Join(names, " ") != "RECORDS JOBS" {
			t.Errorf("streams %v", names)
		}
		var consumers []string
		for name := range records.ConsumerNames(ctx).Name() {
			consumers = append(consumers, name)
		}
		if strings.Join(consumers, " ") != "scheduler-polls" {
			t.Errorf("consumers of RECORDS: %v", consumers)
		}
	})
}

// countingPublisher counts the attempts to publish through it.
type countingPublisher struct {
	Publisher
	attempts atomic.Int32
}

func (p *countingPublisher) Publish(ctx context.Context, subject string, data []byte, id string) error {
	p.attempts.Add(1)
	return p.Publisher.Publish(ctx, subject, data, id)
}

// spec: SOURCE-1, SOURCE-2
func TestAPollGivenUpWhileNATSIsLostIsNeverQueued(t *testing.T) {
	ctx := context.Background()
	store := t.TempDir()
	srv := testenv.StartNATS(t, -1, store)
	port := srv.Addr().(*net.TCPAddr).Port
	conn, err := ConnectNATS(srv.ClientURL())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(conn.Close)
	js, err := jetstream.New(conn)
	if err != nil {
		t.Fatal(err)
	}
	jobs := testenv.MakeJobs(ctx, t, js)
	connection := func(want nats.Status) func() error {
		return func() error {
			if status := conn.Status(); status != want {
				return fmt.Errorf("the connection is %s", status)
			}
			return nil
		}
	}

	srv.Shutdown()
	srv.WaitForShutdown()
	testenv.Eventually(t, 10*time.Second, connection(nats.RECONNECTING))
	publisher := &countingPublisher{Publisher: JetStreamPublisher{JetStream: js, Stream: messages.StreamJobs}}
	poller := &Poller{Publisher: publisher, Clock: clock.RealClock{}, Retry: 100 * time.Millisecond, Log: logr.Discard()}
	running, stop := context.WithCancel(ctx)
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		_ = poller.Start(running)
	}()
	t.Cleanup(func() {
		stop()
		<-stopped
	})
	poll := Poll{Polling: true, Feed: "submissions", URL: "https://data.sec.gov/submissions/CIK0000320193.json",
		Cadence: time.Second}
	poller.Set("polled", poll)
	// Its first two polls expire, each when the next is due, while NATS is
	// lost, and the owner pauses the Source while the third is being tried.
	time.Sleep(2500 * time.Millisecond)
	poll.Polling = false
	poller.Set("polled", poll)
	tried := publisher.attempts.Load()
	if tried < 3 {
		t.Fatalf("tried %d times to queue its polls", tried)
	}

	testenv.StartNATS(t, port, store)
	testenv.Eventually(t, 10*time.Second, connection(nats.CONNECTED))
	if err := conn.Flush(); err != nil {
		t.Fatal(err)
	}
	for deadline := time.Now().Add(2 * time.Second); time.Now().Before(deadline); time.Sleep(100 * time.Millisecond) {
		info, err := jobs.Info(ctx)
		if err != nil {
			t.Fatal(err)
		}
		if info.State.Msgs != 0 {
			t.Fatalf("once NATS was back, it kept %d of the %d tries made while it was lost", info.State.Msgs, tried)
		}
	}
}
