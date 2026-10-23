package scheduler

import (
	"context"
	"encoding/json"
	"errors"
	"regexp"
	"sync"
	"testing"
	"time"

	"github.com/go-logr/logr"
	"k8s.io/utils/clock"
	clocktesting "k8s.io/utils/clock/testing"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
)

// published is one message a fakePublisher kept.
type published struct {
	Subject string
	ID      string
	Job     messages.Job
	Data    []byte
}

// fakePublisher keeps what's published, or refuses it while refusing.
type fakePublisher struct {
	mu       sync.Mutex
	kept     []published
	refusing bool
	attempts int
	// expiring, if set, is called with the next attempt's job in place of
	// the attempt, which then fails, as one that waits until its job
	// expires: the function stands for that wait, and moves the clock on to
	// the job's expiry.
	expiring func(messages.Job)
}

func (p *fakePublisher) Publish(_ context.Context, subject string, data []byte, id string) error {
	var job messages.Job
	if err := json.Unmarshal(data, &job); err != nil {
		return err
	}
	p.mu.Lock()
	p.attempts++
	expiring := p.expiring
	p.expiring = nil
	p.mu.Unlock()
	if expiring != nil {
		expiring(job)
		return context.DeadlineExceeded
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.refusing {
		return errors.New("refused")
	}
	p.kept = append(p.kept, published{Subject: subject, ID: id, Job: job, Data: data})
	return nil
}

// expire has the next attempt wait, as wait does, and fail.
func (p *fakePublisher) expire(wait func(messages.Job)) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.expiring = wait
}

func (p *fakePublisher) jobs() []published {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]published(nil), p.kept...)
}

func (p *fakePublisher) refuse(refusing bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.refusing = refusing
}

var start = time.Date(2026, 10, 22, 14, 0, 0, 0, time.UTC)

func at(seconds float64) string {
	return start.Add(time.Duration(seconds * float64(time.Second))).Format(v1alpha1.TimestampLayout)
}

// signalingClock is a fake clock whose timers say each time they're reset:
// each time a loop has acted and begins to wait.
type signalingClock struct {
	*clocktesting.FakeClock
	waits chan struct{}
}

func (c *signalingClock) NewTimer(d time.Duration) clock.Timer {
	return &signalingTimer{Timer: c.FakeClock.NewTimer(d), waits: c.waits}
}

type signalingTimer struct {
	clock.Timer
	waits chan struct{}
}

func (t *signalingTimer) Reset(d time.Duration) bool {
	reset := t.Timer.Reset(d)
	t.waits <- struct{}{}
	return reset
}

// pollerTest runs a Poller with one Source on a fake clock, which a test
// steps, waiting after each step until the Source's loop has acted on it.
type pollerTest struct {
	t         *testing.T
	clock     *signalingClock
	publisher *fakePublisher
	poller    *Poller
	cancel    context.CancelFunc
	stopped   chan struct{}
}

func newPollerTest(t *testing.T) *pollerTest {
	pt := &pollerTest{
		t:         t,
		clock:     &signalingClock{FakeClock: clocktesting.NewFakeClock(start), waits: make(chan struct{}, 100)},
		publisher: &fakePublisher{},
	}
	pt.poller = &Poller{Publisher: pt.publisher, Clock: pt.clock, Retry: time.Second, Log: logr.Discard()}
	t.Cleanup(pt.stop)
	return pt
}

// acted waits until the loop has acted and begins to wait.
func (pt *pollerTest) acted() {
	pt.t.Helper()
	select {
	case <-pt.clock.waits:
	case <-time.After(5 * time.Second):
		pt.t.Fatal("the loop didn't act")
	}
}

func (pt *pollerTest) start() {
	ctx, cancel := context.WithCancel(context.Background())
	pt.cancel, pt.stopped = cancel, make(chan struct{})
	pt.poller.mu.Lock()
	loops := len(pt.poller.desired)
	pt.poller.mu.Unlock()
	go func() {
		defer close(pt.stopped)
		_ = pt.poller.Start(ctx)
	}()
	for range loops {
		pt.acted()
	}
	for !pt.running() {
		time.Sleep(time.Millisecond)
	}
}

func (pt *pollerTest) running() bool {
	pt.poller.mu.Lock()
	defer pt.poller.mu.Unlock()
	return pt.poller.ctx != nil
}

func (pt *pollerTest) stop() {
	if pt.cancel != nil {
		pt.cancel()
		<-pt.stopped
		pt.cancel = nil
	}
}

// step moves the clock on, and waits for the loop if its timer fired.
func (pt *pollerTest) step(d time.Duration) {
	pt.t.Helper()
	waiting := pt.clock.HasWaiters()
	pt.clock.Step(d)
	if waiting && !pt.clock.HasWaiters() {
		pt.acted()
	}
}

// to steps the clock to seconds after the start, through each whole second
// between.
func (pt *pollerTest) to(seconds float64) {
	pt.t.Helper()
	target := start.Add(time.Duration(seconds * float64(time.Second)))
	for now := pt.clock.Now(); now.Before(target); now = pt.clock.Now() {
		next := now.Truncate(time.Second).Add(time.Second)
		if target.Before(next) {
			next = target
		}
		pt.step(next.Sub(now))
	}
}

// set gives the Poller the Source's Poll, and waits until its loop, if it
// has one, has acted on it.
func (pt *pollerTest) set(polling bool, cadence time.Duration) {
	pt.t.Helper()
	running := pt.running()
	pt.poller.Set("apple-submissions", Poll{
		Polling: polling,
		Feed:    "submissions",
		URL:     "https://data.sec.gov/submissions/CIK0000320193.json",
		Cadence: cadence,
	})
	if polling && running {
		pt.acted()
	}
}

// queuedAt gives each kept job's queued_at, and expiresAt each one's
// expires_at.
func (pt *pollerTest) queued() (queuedAt, expiresAt []string) {
	for _, p := range pt.publisher.jobs() {
		queuedAt = append(queuedAt, p.Job.QueuedAt)
		expiresAt = append(expiresAt, p.Job.ExpiresAt)
	}
	return queuedAt, expiresAt
}

func equal(t *testing.T, what string, got, want []string) {
	t.Helper()
	if len(got) != len(want) {
		t.Fatalf("%s: got %v, want %v", what, got, want)
	}
	for i := range got {
		if got[i] != want[i] {
			t.Fatalf("%s: got %v, want %v", what, got, want)
		}
	}
}

// spec: SOURCE-2
func TestAPollIsQueuedWhenTheSourceBeginsPollingThenEachCadenceAfterTheOneBeforeWasDue(t *testing.T) {
	pt := newPollerTest(t)
	pt.start()
	pt.set(true, 10*time.Second)
	pt.to(9.999)
	queuedAt, _ := pt.queued()
	equal(t, "before the second is due", queuedAt, []string{at(0)})
	pt.to(30)
	queuedAt, expiresAt := pt.queued()
	equal(t, "queued", queuedAt, []string{at(0), at(10), at(20), at(30)})
	equal(t, "expiring", expiresAt, []string{at(10), at(20), at(30), at(40)})
}

// spec: SOURCE-2
func TestPollsBeginOnlyWhenTheReplicaBecomesTheActiveOne(t *testing.T) {
	pt := newPollerTest(t)
	pt.set(true, 10*time.Second)
	pt.step(25 * time.Second)
	if jobs := pt.publisher.jobs(); len(jobs) != 0 {
		t.Fatalf("queued before it started: %v", jobs)
	}
	pt.start()
	queuedAt, _ := pt.queued()
	equal(t, "when it started", queuedAt, []string{at(25)})
}

// spec: SOURCE-2
func TestAPollThatCantBeQueuedBeforeTheNextIsDueIsSkipped(t *testing.T) {
	pt := newPollerTest(t)
	pt.start()
	pt.publisher.refuse(true)
	pt.set(true, 10*time.Second)
	pt.to(12.5)
	pt.publisher.mu.Lock()
	attempts := pt.publisher.attempts
	pt.publisher.mu.Unlock()
	// The first poll was tried each second until the second was due.
	if attempts < 10 {
		t.Fatalf("tried %d times", attempts)
	}
	pt.publisher.refuse(false)
	pt.to(13)
	queuedAt, expiresAt := pt.queued()
	// The poll due at 0 was skipped; the one due at 10 was queued late, but
	// still expires when the one after it is due.
	equal(t, "queued", queuedAt, []string{at(13)})
	equal(t, "expiring", expiresAt, []string{at(20)})

	// Polls a pause in the clock passes are skipped too.
	pt.step(25 * time.Second)
	queuedAt, expiresAt = pt.queued()
	equal(t, "after a pause", queuedAt, []string{at(13), at(38)})
	equal(t, "after a pause, expiring", expiresAt, []string{at(20), at(40)})
}

// spec: SOURCE-2
func TestAPollRetriedIsOneJob(t *testing.T) {
	pt := newPollerTest(t)
	pt.start()
	pt.publisher.refuse(true)
	pt.set(true, 10*time.Second)
	pt.to(2)
	pt.publisher.refuse(false)
	pt.to(3)
	jobs := pt.publisher.jobs()
	if len(jobs) != 1 || jobs[0].Job.QueuedAt != at(3) || jobs[0].Job.ExpiresAt != at(10) {
		t.Fatalf("got %+v", jobs)
	}
}

// spec: SOURCE-2
func TestANewCadenceAppliesFromTheNextPollDueTheNewCadenceAfterTheLastQueuedWasDue(t *testing.T) {
	pt := newPollerTest(t)
	pt.start()
	pt.set(true, 10*time.Second)
	pt.to(5)
	pt.set(true, 30*time.Second)
	pt.to(29.999)
	queuedAt, _ := pt.queued()
	equal(t, "a longer cadence", queuedAt, []string{at(0)})
	pt.to(30)
	pt.to(35)
	// The next poll is due 3 seconds after the last queued was due, at 33,
	// which has passed, so it's queued at once.
	pt.set(true, 3*time.Second)
	pt.to(40)
	queuedAt, expiresAt := pt.queued()
	equal(t, "a shorter cadence", queuedAt, []string{at(0), at(30), at(35), at(36), at(39)})
	equal(t, "a shorter cadence, expiring", expiresAt, []string{at(10), at(60), at(36), at(39), at(42)})
}

// spec: SOURCE-2
func TestANewCadenceAppliesBeforeTheNextTryAfterOneThatFailedWhenItsPollExpired(t *testing.T) {
	pt := newPollerTest(t)
	pt.start()
	pt.set(true, 10*time.Second)
	// The poll due at 10 waits to be queued until it expires, at 20, and
	// fails; meanwhile, at 12, the owner changes the cadence to 100.
	pt.publisher.expire(func(job messages.Job) {
		pt.clock.Step(2 * time.Second)
		pt.poller.Set("apple-submissions", Poll{
			Polling: true,
			Feed:    "submissions",
			URL:     "https://data.sec.gov/submissions/CIK0000320193.json",
			Cadence: 100 * time.Second,
		})
		expires, err := time.Parse(v1alpha1.TimestampLayout, job.ExpiresAt)
		if err != nil {
			t.Error(err)
		}
		pt.clock.SetTime(expires)
	})
	pt.to(10)
	// The next poll is due 100 seconds after the last queued was due, at
	// 100, so none is tried at 20 at the old cadence.
	queuedAt, _ := pt.queued()
	equal(t, "when the poll due at 10 expired", queuedAt, []string{at(0)})
	pt.to(100)
	queuedAt, expiresAt := pt.queued()
	equal(t, "queued", queuedAt, []string{at(0), at(100)})
	equal(t, "expiring", expiresAt, []string{at(10), at(200)})
}

// spec: SOURCE-2
func TestASourceUnpausedBeginsPollingAgainWithAPollAtOnce(t *testing.T) {
	pt := newPollerTest(t)
	pt.start()
	pt.set(true, 10*time.Second)
	pt.to(4)
	pt.set(false, 10*time.Second)
	pt.to(24)
	pt.set(true, 10*time.Second)
	pt.to(34)
	queuedAt, _ := pt.queued()
	equal(t, "queued", queuedAt, []string{at(0), at(24), at(34)})
}

// spec: JOB-2
func TestEachPollIsQueuedAsAJobOfKindPollWithTheSourcesNameFeedAndURL(t *testing.T) {
	schemas, err := messages.LoadSchemas()
	if err != nil {
		t.Fatal(err)
	}
	pt := newPollerTest(t)
	pt.start()
	pt.set(true, 10*time.Second)
	pt.to(10)
	uuid := regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)
	jobs := pt.publisher.jobs()
	if len(jobs) != 2 {
		t.Fatalf("got %d jobs", len(jobs))
	}
	for i, p := range jobs {
		if err := schemas.ValidateJob(p.Data); err != nil {
			t.Errorf("job %d doesn't validate: %v", i, err)
		}
		want := messages.Job{
			ID:        p.Job.ID,
			Kind:      "poll",
			Feed:      "submissions",
			URL:       "https://data.sec.gov/submissions/CIK0000320193.json",
			Source:    "apple-submissions",
			QueuedAt:  at(float64(10 * i)),
			ExpiresAt: at(float64(10 * (i + 1))),
		}
		if p.Job != want {
			t.Errorf("job %d: got %+v, want %+v", i, p.Job, want)
		}
		if !uuid.MatchString(p.Job.ID) || p.ID != p.Job.ID || p.Subject != "edgar.jobs.poll" {
			t.Errorf("job %d: id %q, message id %q, subject %q", i, p.Job.ID, p.ID, p.Subject)
		}
	}
	if jobs[0].Job.ID == jobs[1].Job.ID {
		t.Error("two polls with one id")
	}
}
