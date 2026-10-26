package fetcher

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/go-logr/logr"
	"github.com/google/go-cmp/cmp"
	"github.com/nats-io/nats.go/jetstream"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
	"github.com/nslaughter/edgar-data-pipeline/internal/testenv"
)

// workerTest runs a Worker, as the active replica runs it, against NATS
// with the chart's streams and consumers, a server that answers as the SEC,
// and the store and the SecAccess in memory.
type workerTest struct {
	t       *testing.T
	ctx     context.Context
	js      jetstream.JetStream
	events  jetstream.Consumer
	server  *secServer
	records *fakeRecords
	objects *fakeObjects
	access  *fakeAccess
	schemas *messages.Schemas
	worker  *Worker
	cancel  context.CancelFunc
	stopped context.CancelFunc
	done    chan struct{}
}

// pathBody answers each request with a body of its own path.
func pathBody(w http.ResponseWriter, r *http.Request) {
	_, _ = io.WriteString(w, "the body of "+r.URL.Path)
}

func newWorkerTest(t *testing.T, handler http.HandlerFunc, ackWait time.Duration) *workerTest {
	t.Helper()
	ctx := context.Background()
	_, js := testenv.NATS(t)
	schemas, err := messages.LoadSchemas()
	if err != nil {
		t.Fatal(err)
	}
	wt := &workerTest{
		t: t, ctx: ctx, js: js, server: newSECServer(t, handler), objects: &fakeObjects{}, access: &fakeAccess{},
		schemas: schemas,
	}
	wt.records = &fakeRecords{}
	wt.events = makeStreams(ctx, t, js, ackWait)
	return wt
}

func (wt *workerTest) start() {
	wt.t.Helper()
	opts := wt.server.options()
	opts.Permit = wt.access.Allowed
	opts.Timeout = 10 * time.Second
	wt.worker = &Worker{
		JetStream: wt.js, Schemas: wt.schemas, Records: wt.records, Objects: wt.objects, Access: wt.access,
		Client: NewClient(opts), Tool: Tool(testCommit), Log: logr.Discard(),
		Idle: 10 * time.Millisecond, Hold: 20 * time.Millisecond, InProgress: 200 * time.Millisecond,
		Wait: 50 * time.Millisecond,
	}
	shutdown, stopped := context.WithCancel(context.Background())
	ctx, cancel := context.WithCancel(shutdown)
	wt.worker.Shutdown, wt.stopped, wt.cancel = shutdown, stopped, cancel
	wt.done = make(chan struct{})
	go func() {
		defer close(wt.done)
		wt.worker.Run(ctx)
	}()
	wt.t.Cleanup(wt.stop)
}

// stop stops the Worker as Kubernetes stops its pod.
func (wt *workerTest) stop() {
	wt.stopped()
	<-wt.done
}

// lose stops the Worker as when its replica loses the Lease.
func (wt *workerTest) lose() {
	wt.cancel()
	<-wt.done
}

func (wt *workerTest) queue(job messages.Job) {
	wt.t.Helper()
	queue(wt.ctx, wt.t, wt.js, job)
}

func (wt *workerTest) urls() []string {
	var found []string
	for _, r := range wt.server.seen() {
		found = append(found, strings.TrimPrefix(r.url, "https://www.sec.gov"))
	}
	return found
}

// spec: JOB-4, BACKFILL-2, FETCHER-10
func TestPollsAreTakenFirstThenFilingsThenBackfills(t *testing.T) {
	release := make(chan struct{})
	wt := newWorkerTest(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/backfill-slow" {
			<-release
		}
		pathBody(w, r)
	}, 0)
	for _, job := range []messages.Job{
		newJob(messages.KindBackfill, "https://www.sec.gov/backfill-1"),
		newJob(messages.KindFiling, "https://www.sec.gov/filing-1"),
		newJob(messages.KindPoll, "https://www.sec.gov/poll-1"),
		newJob(messages.KindBackfill, "https://www.sec.gov/backfill-2"),
		newJob(messages.KindFiling, "https://www.sec.gov/filing-2"),
		newJob(messages.KindPoll, "https://www.sec.gov/poll-2"),
		newJob(messages.KindBackfill, "https://www.sec.gov/backfill-slow"),
	} {
		wt.queue(job)
	}
	wt.start()
	testenv.Eventually(t, 10*time.Second, func() error {
		if len(wt.server.seen()) < 7 {
			return fmt.Errorf("%d requests", len(wt.server.seen()))
		}
		return nil
	})
	// While a Backfill's fetch is in progress, a filing's job and a poll's
	// are queued: the poll's comes next, before the request after it.
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/filing-3"))
	wt.queue(newJob(messages.KindPoll, "https://www.sec.gov/poll-3"))
	time.Sleep(200 * time.Millisecond)
	close(release)
	settled(wt.ctx, t, wt.js, 10*time.Second)

	want := []string{"/poll-1", "/poll-2", "/filing-1", "/filing-2", "/backfill-1", "/backfill-2", "/backfill-slow",
		"/poll-3", "/filing-3"}
	if diff := cmp.Diff(want, wt.urls()); diff != "" {
		t.Errorf("the order of the requests (-want +got):\n%s", diff)
	}
}

// unavailable answers the first request for its path with a 503 and a
// Retry-After, a temporary failure that's retried (FETCH-8), and closes
// failed once it has.
type unavailable struct {
	path       string
	retryAfter string
	once       sync.Once
	failed     chan struct{}
}

func newUnavailable(path, retryAfter string) *unavailable {
	return &unavailable{path: path, retryAfter: retryAfter, failed: make(chan struct{})}
}

// answer answers a request if it's the first for the path, and says
// whether it did.
func (u *unavailable) answer(w http.ResponseWriter, r *http.Request) bool {
	first := false
	if r.URL.Path == u.path {
		u.once.Do(func() { first = true })
	}
	if !first {
		return false
	}
	w.Header().Set("Retry-After", u.retryAfter)
	w.WriteHeader(http.StatusServiceUnavailable)
	close(u.failed)
	return true
}

// await waits until ch is closed, or fails the test once the time given has
// passed.
func await(t *testing.T, ch <-chan struct{}, within time.Duration, what string) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(within):
		t.Fatalf("no %s within %s", what, within)
	}
}

// paths gives each record's URL's path, in the order they were added.
func paths(rows []fakeRow) []string {
	var found []string
	for _, row := range rows {
		found = append(found, strings.TrimPrefix(row.record.URL, "https://www.sec.gov"))
	}
	return found
}

// announced gives the paths of the URLs of the next n events' jobs, in the
// order they were published.
func announced(t *testing.T, events jetstream.Consumer, n int) []string {
	t.Helper()
	var found []string
	for range n {
		e := nextEvent(t, events, 5*time.Second)
		found = append(found, strings.TrimPrefix(e.event.Job.URL, "https://www.sec.gov"))
	}
	return found
}

// spec: JOB-4, BACKFILL-2, FETCHER-10, FETCH-8
func TestBetweenAFetchsAttemptsTheJobsOfEarlierKindsAreTakenFirst(t *testing.T) {
	kinds := []string{messages.KindPoll, messages.KindFiling, messages.KindBackfill}
	for k, kind := range kinds {
		t.Run("a "+kind+"'s retry", func(t *testing.T) {
			// JetStream would deliver a job again a second after it last
			// heard of it; the fetch waits three before its retry.
			retried := newUnavailable("/retried", "3")
			wt := newWorkerTest(t, func(w http.ResponseWriter, r *http.Request) {
				if !retried.answer(w, r) {
					pathBody(w, r)
				}
			}, time.Second)
			job := newJob(kind, "https://www.sec.gov/retried")
			wt.queue(job)
			wt.start()
			await(t, retried.failed, 10*time.Second, "first attempt")

			// During its wait, a job of each kind before its own.
			for j := range k {
				wt.queue(newJob(kinds[j], "https://www.sec.gov/"+kinds[j]))
			}
			// Meanwhile another asks the job's consumer for a job, as another
			// replica would once it held the Lease: none comes.
			consumer, err := wt.js.Consumer(wt.ctx, messages.StreamJobs, Consumers[k])
			if err != nil {
				t.Fatal(err)
			}
			for deadline := time.Now().Add(2 * time.Second); time.Now().Before(deadline); {
				batch, err := consumer.FetchNoWait(1)
				if err != nil {
					t.Fatal(err)
				}
				for msg := range batch.Messages() {
					t.Fatalf("delivered again during its fetch's wait: %s", msg.Data())
				}
				time.Sleep(250 * time.Millisecond)
			}
			// Then, still during its wait, a job of its own kind and of each
			// kind after it, the later kinds queued first.
			for j := len(kinds) - 1; j >= k; j-- {
				wt.queue(newJob(kinds[j], "https://www.sec.gov/"+kinds[j]))
			}
			settled(wt.ctx, t, wt.js, 15*time.Second)

			// Those of the earlier kinds go before the retry, polls first,
			// and the others after it.
			want := []string{"/retried"}
			for j := range k {
				want = append(want, "/"+kinds[j])
			}
			want = append(want, "/retried")
			for j := k; j < len(kinds); j++ {
				want = append(want, "/"+kinds[j])
			}
			if diff := cmp.Diff(want, wt.urls()); diff != "" {
				t.Fatalf("the order of the requests (-want +got):\n%s", diff)
			}
			// The retry waited its wait in full (FETCH-8).
			seen := wt.server.seen()
			if gap := seen[k+1].at.Sub(seen[0].at); gap < 3*time.Second {
				t.Errorf("the retry came %s after the first attempt, within its wait of 3 seconds", gap)
			}
			// Each job's record, then its event, in that order, and the
			// retried job's once, of its second attempt.
			rows := wt.records.all()
			if diff := cmp.Diff(want[1:], paths(rows)); diff != "" {
				t.Errorf("the order of the records (-want +got):\n%s", diff)
			}
			if diff := cmp.Diff(want[1:], announced(t, wt.events, len(rows))); diff != "" {
				t.Errorf("the order of the events (-want +got):\n%s", diff)
			}
			if r := rows[k]; r.job != job.ID || r.record.Attempts != 2 || value(r.record.Status) != http.StatusOK {
				t.Errorf("the retried job's record: %+v", r)
			}
			info := consumerState(wt.ctx, t, wt.js, Consumers[k])
			if info.NumRedelivered != 0 || info.Delivered.Consumer != 2 {
				t.Errorf("%s delivered %d jobs, %d of them again", Consumers[k], info.Delivered.Consumer,
					info.NumRedelivered)
			}
		})
	}
}

// spec: JOB-4, BACKFILL-2
func TestAJobTakenDuringAWaitTakesOnlyEarlierKindsAndTheFetchGoesOnOnceItHasEnded(t *testing.T) {
	backfill := newUnavailable("/backfill", "1")
	filing := newUnavailable("/filing-1", "1")
	wt := newWorkerTest(t, func(w http.ResponseWriter, r *http.Request) {
		if !backfill.answer(w, r) && !filing.answer(w, r) {
			pathBody(w, r)
		}
	}, 0)
	wt.queue(newJob(messages.KindBackfill, "https://www.sec.gov/backfill"))
	wt.start()
	await(t, backfill.failed, 10*time.Second, "first attempt of the Backfill's fetch")
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/filing-1"))
	await(t, filing.failed, 10*time.Second, "first attempt of the filing's fetch")
	// During the filing's wait, which ends after the Backfill's, another
	// filing's job and a poll's.
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/filing-2"))
	wt.queue(newJob(messages.KindPoll, "https://www.sec.gov/poll"))
	settled(wt.ctx, t, wt.js, 10*time.Second)

	// The filing takes the poll's job, and not the other filing's, between
	// its attempts. Once it has ended, the Backfill's wait is over, and it
	// takes the other filing's job before its retry.
	want := []string{"/backfill", "/filing-1", "/poll", "/filing-1", "/filing-2", "/backfill"}
	if diff := cmp.Diff(want, wt.urls()); diff != "" {
		t.Fatalf("the order of the requests (-want +got):\n%s", diff)
	}
	recorded := []string{"/poll", "/filing-1", "/filing-2", "/backfill"}
	rows := wt.records.all()
	if diff := cmp.Diff(recorded, paths(rows)); diff != "" {
		t.Errorf("the order of the records (-want +got):\n%s", diff)
	}
	if diff := cmp.Diff(recorded, announced(t, wt.events, len(rows))); diff != "" {
		t.Errorf("the order of the events (-want +got):\n%s", diff)
	}
}

// spec: JOB-4, BACKFILL-2, ACCESS-1
func TestDuringAFetchsWaitNoJobIsTakenWhileRequestsArentAllowed(t *testing.T) {
	access := &fakeAccess{}
	failed := make(chan struct{})
	var first sync.Once
	wt := newWorkerTest(t, func(w http.ResponseWriter, r *http.Request) {
		fail := false
		if r.URL.Path == "/backfill" {
			first.Do(func() { fail = true })
		}
		if !fail {
			pathBody(w, r)
			return
		}
		// Paused as the Backfill's first attempt fails.
		access.pause(true)
		w.Header().Set("Retry-After", "0")
		w.WriteHeader(http.StatusServiceUnavailable)
		close(failed)
	}, 0)
	wt.access = access
	wt.queue(newJob(messages.KindBackfill, "https://www.sec.gov/backfill"))
	wt.start()
	await(t, failed, 10*time.Second, "first attempt")
	wt.queue(newJob(messages.KindPoll, "https://www.sec.gov/poll"))
	time.Sleep(time.Second)

	if info := consumerState(wt.ctx, t, wt.js, messages.ConsumerFetcherPolls); info.Delivered.Consumer != 0 {
		t.Errorf("%d polls' jobs taken while requests were paused", info.Delivered.Consumer)
	}
	if diff := cmp.Diff([]string{"/backfill"}, wt.urls()); diff != "" {
		t.Fatalf("the requests while they were paused (-want +got):\n%s", diff)
	}
	// Once they're allowed, the poll's job, which waited, goes before the
	// Backfill's retry.
	access.pause(false)
	settled(wt.ctx, t, wt.js, 10*time.Second)
	if diff := cmp.Diff([]string{"/backfill", "/poll", "/backfill"}, wt.urls()); diff != "" {
		t.Errorf("the requests (-want +got):\n%s", diff)
	}
}

// spec: JOB-5
func TestAJobThatHasExpiredIsAcknowledgedWithoutARequestOrARecord(t *testing.T) {
	wt := newWorkerTest(t, pathBody, 0)
	expired := newJob(messages.KindPoll, "https://www.sec.gov/expired")
	expired.ExpiresAt = timestamp(time.Now().Add(-time.Second))
	wt.queue(expired)
	wt.queue(newJob(messages.KindPoll, "https://www.sec.gov/current"))
	wt.start()
	settled(wt.ctx, t, wt.js, 10*time.Second)

	if diff := cmp.Diff([]string{"/current"}, wt.urls()); diff != "" {
		t.Errorf("the requests (-want +got):\n%s", diff)
	}
	if rows := wt.records.all(); len(rows) != 1 || rows[0].record.URL != "https://www.sec.gov/current" {
		t.Errorf("the records: %+v", rows)
	}
	nextEvent(t, wt.events, 5*time.Second)
	noEvent(t, wt.events, 500*time.Millisecond)
}

// spec: JOB-6, FETCH-14, JOB-1
func TestEachFetchIsRecordedAfterItsObjectThenAnnouncedThenAcknowledged(t *testing.T) {
	wt := newWorkerTest(t, pathBody, 0)
	// Each write fails at first, and is tried again until it succeeds, as
	// the owner decided; until the step before it succeeds, none is tried.
	wt.objects.fails = 3
	wt.records.fail("Add", 3)
	stream, err := wt.js.Stream(wt.ctx, messages.StreamRecords)
	if err != nil {
		t.Fatal(err)
	}
	config := stream.CachedInfo().Config
	subjects := config.Subjects
	config.Subjects = []string{"elsewhere"}
	if _, err := wt.js.UpdateStream(wt.ctx, config); err != nil {
		t.Fatal(err)
	}
	job := newJob(messages.KindFiling, "https://www.sec.gov/filing")
	wt.queue(job)
	wt.start()

	testenv.Eventually(t, 10*time.Second, func() error {
		if tries := wt.records.tries("Add"); tries < 4 {
			return fmt.Errorf("%d tries to add the record", tries)
		}
		return nil
	})
	if puts := wt.objects.tries(); puts != 4 {
		t.Errorf("%d tries to store the object, before the record", puts)
	}
	// RECORDS refuses the event, so the job isn't acknowledged yet.
	time.Sleep(300 * time.Millisecond)
	if info := consumerState(wt.ctx, t, wt.js, messages.ConsumerFetcherFilings); info.NumAckPending != 1 {
		t.Errorf("%d jobs unacknowledged before the event was published", info.NumAckPending)
	}
	config.Subjects = subjects
	if _, err := wt.js.UpdateStream(wt.ctx, config); err != nil {
		t.Fatal(err)
	}
	got := nextEvent(t, wt.events, 10*time.Second)
	settled(wt.ctx, t, wt.js, 10*time.Second)

	rows := wt.records.all()
	if len(rows) != 1 || rows[0].job != job.ID {
		t.Fatalf("the records: %+v", rows)
	}
	if _, ok := wt.objects.get(*rows[0].record.Object); !ok {
		t.Error("the object the record names isn't in the bucket")
	}
	if got.subject != "edgar.records.filing" || got.event.ID != 1 {
		t.Errorf("the event's subject %s, id %d", got.subject, got.event.ID)
	}
	if diff := cmp.Diff(job, got.event.Job); diff != "" {
		t.Errorf("the event's job (-queued +announced):\n%s", diff)
	}
	recorded, err := json.Marshal(rows[0].record)
	if err != nil {
		t.Fatal(err)
	}
	if string(recorded) != string(got.event.Record) {
		t.Errorf("the event's record isn't the one added:\n%s\n%s", got.event.Record, recorded)
	}
	if _, err := wt.schemas.DecodeEvent(got.data); err != nil {
		t.Errorf("the event doesn't validate: %v", err)
	}
	if len(wt.server.seen()) != 1 {
		t.Errorf("%d requests, for one job", len(wt.server.seen()))
	}
}

// spec: JOB-6
func TestAJobWhoseRecordTheTableHoldsIsAnnouncedAgainWithoutARequest(t *testing.T) {
	wt := newWorkerTest(t, pathBody, 0)
	job := newJob(messages.KindPoll, "https://www.sec.gov/poll")
	wt.queue(job)
	wt.start()
	first := nextEvent(t, wt.events, 10*time.Second)
	settled(wt.ctx, t, wt.js, 10*time.Second)

	// The same job, as JetStream delivers it again, and again once it has
	// expired (JOB-5).
	wt.queue(job)
	again := nextEvent(t, wt.events, 10*time.Second)
	expired := job
	expired.ExpiresAt = timestamp(time.Now().Add(-time.Minute))
	wt.queue(expired)
	late := nextEvent(t, wt.events, 10*time.Second)
	settled(wt.ctx, t, wt.js, 10*time.Second)

	if len(wt.server.seen()) != 1 || len(wt.records.all()) != 1 {
		t.Fatalf("%d requests and %d records, for one job", len(wt.server.seen()), len(wt.records.all()))
	}
	for _, e := range []received{again, late} {
		if e.event.ID != first.event.ID || string(e.event.Record) != string(first.event.Record) {
			t.Errorf("announced again as %d, %s; first as %d, %s", e.event.ID, e.event.Record, first.event.ID,
				first.event.Record)
		}
	}
}

// spec: JOB-7
func TestAJobThatDoesntValidateOrWhoseKindIsntItsSubjectsIsAcknowledgedWithoutARequest(t *testing.T) {
	wt := newWorkerTest(t, pathBody, 0)
	ctx := wt.ctx
	filing := newJob(messages.KindFiling, "https://www.sec.gov/mismatched")
	data, err := json.Marshal(filing)
	if err != nil {
		t.Fatal(err)
	}
	publish(ctx, t, wt.js, "edgar.jobs.poll", data)
	publish(ctx, t, wt.js, "edgar.jobs.filing", []byte("not json"))
	publish(ctx, t, wt.js, "edgar.jobs.filing", []byte("{}"))
	noFeed := newJob(messages.KindFiling, "https://www.sec.gov/no-feed")
	noFeed.Feed = ""
	wt.queue(noFeed)
	offHost := newJob(messages.KindFiling, "https://www.example.com/elsewhere")
	wt.queue(offHost)
	pollWithoutSource := newJob(messages.KindPoll, "https://www.sec.gov/no-source")
	pollWithoutSource.Source = ""
	wt.queue(pollWithoutSource)
	// A field it doesn't know, it ignores, and fetches the job: one whose
	// name differs from a known one's only in case among them, after it.
	job := newJob(messages.KindBackfill, "https://www.sec.gov/known")
	known, err := json.Marshal(job)
	if err != nil {
		t.Fatal(err)
	}
	unknown := strings.Replace(string(known), "{", `{"later":1,`, 1)
	unknown = strings.TrimSuffix(unknown, "}") + `,"ID":"not-a-uuid","URL":"https://www.sec.gov/elsewhere","Feed":"other"}`
	if err := wt.schemas.ValidateJob([]byte(unknown)); err != nil {
		t.Fatalf("the job with fields it doesn't know doesn't validate: %v", err)
	}
	publish(ctx, t, wt.js, "edgar.jobs.backfill", []byte(unknown))
	wt.start()
	settled(ctx, t, wt.js, 10*time.Second)

	if diff := cmp.Diff([]string{"/known"}, wt.urls()); diff != "" {
		t.Errorf("the requests (-want +got):\n%s", diff)
	}
	rows := wt.records.all()
	if len(rows) != 1 {
		t.Fatalf("%d records", len(rows))
	}
	if rows[0].job != job.ID || rows[0].record.Feed != job.Feed || rows[0].record.URL != job.URL {
		t.Errorf("the record of job %s, feed %s, URL %s, not the job's", rows[0].job, rows[0].record.Feed,
			rows[0].record.URL)
	}
}

// spec: FETCH-1
func TestAJobWhoseURLCantBeRequestedIsAcknowledgedWithoutARequest(t *testing.T) {
	wt := newWorkerTest(t, pathBody, 0)
	// Each begins as an https URL on one of the SEC's hosts does, so its job
	// validates, but isn't a URL: no request can be made for it.
	for _, url := range []string{"https://www.sec.gov/%ZZ", "https://www.sec.gov/\x00", "https://data.sec.gov/a#%"} {
		job := newJob(messages.KindFiling, url)
		data, err := json.Marshal(job)
		if err != nil {
			t.Fatal(err)
		}
		if err := wt.schemas.ValidateJob(data); err != nil {
			t.Fatalf("the job for %q doesn't validate: %v", url, err)
		}
		wt.queue(job)
	}
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/after"))
	wt.start()
	settled(wt.ctx, t, wt.js, 10*time.Second)

	if diff := cmp.Diff([]string{"/after"}, wt.urls()); diff != "" {
		t.Errorf("the requests (-want +got):\n%s", diff)
	}
	if rows := wt.records.all(); len(rows) != 1 || rows[0].record.URL != "https://www.sec.gov/after" {
		t.Errorf("the records: %+v", rows)
	}
	nextEvent(t, wt.events, 5*time.Second)
	noEvent(t, wt.events, 500*time.Millisecond)
}

// spec: FETCHER-10
func TestAJobTheWorkerIsAtWorkOnIsntDeliveredAgainHoweverLongItsFetchTakes(t *testing.T) {
	// JetStream would deliver the job again a second after it last heard of
	// it; the fetch takes three.
	arrived := make(chan struct{})
	wt := newWorkerTest(t, func(w http.ResponseWriter, r *http.Request) {
		close(arrived)
		time.Sleep(3 * time.Second)
		pathBody(w, r)
	}, time.Second)
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/slow"))
	wt.start()
	<-arrived
	// Meanwhile another asks the consumer for a job, as another replica
	// would once it held the Lease: none comes.
	consumer, err := wt.js.Consumer(wt.ctx, messages.StreamJobs, messages.ConsumerFetcherFilings)
	if err != nil {
		t.Fatal(err)
	}
	for deadline := time.Now().Add(2500 * time.Millisecond); time.Now().Before(deadline); {
		batch, err := consumer.FetchNoWait(1)
		if err != nil {
			t.Fatal(err)
		}
		for msg := range batch.Messages() {
			t.Fatalf("delivered again while the worker was at work on it: %s", msg.Data())
		}
		time.Sleep(250 * time.Millisecond)
	}
	settled(wt.ctx, t, wt.js, 15*time.Second)

	info := consumerState(wt.ctx, t, wt.js, messages.ConsumerFetcherFilings)
	if info.NumRedelivered != 0 || info.Delivered.Consumer != 1 || len(wt.server.seen()) != 1 {
		t.Errorf("delivered %d times, %d of them again; %d requests", info.Delivered.Consumer, info.NumRedelivered,
			len(wt.server.seen()))
	}
}

// spec: FETCHER-11
func TestTheWorkerWaitsForItsConsumersAndMakesNoStreamOrConsumer(t *testing.T) {
	ctx := context.Background()
	_, js := testenv.NATS(t)
	schemas, err := messages.LoadSchemas()
	if err != nil {
		t.Fatal(err)
	}
	server := newSECServer(t, pathBody)
	records := &fakeRecords{}
	worker := &Worker{
		JetStream: js, Schemas: schemas, Records: records, Objects: &fakeObjects{}, Access: &fakeAccess{},
		Client: NewClient(server.options()), Tool: Tool(testCommit), Log: logr.Discard(), Idle: 10 * time.Millisecond,
		Wait: 50 * time.Millisecond,
	}
	run, cancel := context.WithCancel(ctx)
	done := make(chan struct{})
	go func() {
		defer close(done)
		worker.Run(run)
	}()
	t.Cleanup(func() {
		cancel()
		<-done
	})
	time.Sleep(500 * time.Millisecond)
	if names := streamNames(ctx, t, js); len(names) != 0 {
		t.Fatalf("it made streams: %v", names)
	}

	// The Job makes JOBS last, with its consumers (NATS-5).
	events := makeStreams(ctx, t, js, 0)
	queue(ctx, t, js, newJob(messages.KindFiling, "https://www.sec.gov/filing"))
	nextEvent(t, events, 10*time.Second)
	settled(ctx, t, js, 10*time.Second)
	if diff := cmp.Diff([]string{"JOBS", "RECORDS"}, streamNames(ctx, t, js)); diff != "" {
		t.Errorf("the streams (-want +got):\n%s", diff)
	}
	stream, err := js.Stream(ctx, messages.StreamJobs)
	if err != nil {
		t.Fatal(err)
	}
	var consumers []string
	for name := range stream.ConsumerNames(ctx).Name() {
		consumers = append(consumers, name)
	}
	if diff := cmp.Diff([]string{"fetcher-backfills", "fetcher-filings", "fetcher-polls"}, sorted(consumers)); diff != "" {
		t.Errorf("the consumers of JOBS (-want +got):\n%s", diff)
	}
}

func streamNames(ctx context.Context, t *testing.T, js jetstream.JetStream) []string {
	t.Helper()
	var names []string
	lister := js.StreamNames(ctx)
	for name := range lister.Name() {
		names = append(names, name)
	}
	if err := lister.Err(); err != nil {
		t.Fatal(err)
	}
	return sorted(names)
}

func sorted(names []string) []string {
	slices.Sort(names)
	return names
}

// spec: FETCH-9, ACCESS-3, ACCESS-1
func TestAfterA403NoRequestIsSentUntilTheOwnerAcknowledgesTheStop(t *testing.T) {
	wt := newWorkerTest(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/forbidden" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		pathBody(w, r)
	}, 0)
	wt.queue(newJob(messages.KindPoll, "https://www.sec.gov/forbidden"))
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/after-1"))
	wt.queue(newJob(messages.KindPoll, "https://www.sec.gov/after-2"))
	wt.start()

	got := nextEvent(t, wt.events, 10*time.Second)
	var record Record
	if err := json.Unmarshal(got.event.Record, &record); err != nil {
		t.Fatal(err)
	}
	if value(record.Status) != http.StatusForbidden || record.Attempts != 1 {
		t.Fatalf("the record: %+v", record)
	}
	want := []v1alpha1.Stop{{At: v1alpha1.Timestamp(record.RequestedAt), URL: "https://www.sec.gov/forbidden"}}
	if diff := cmp.Diff(want, wt.access.stopped()); diff != "" {
		t.Errorf("the stops recorded (-want +got):\n%s", diff)
	}
	time.Sleep(time.Second)
	if diff := cmp.Diff([]string{"/forbidden"}, wt.urls()); diff != "" {
		t.Fatalf("the requests before the acknowledgement (-want +got):\n%s", diff)
	}

	wt.access.acknowledge(want[0].At)
	settled(wt.ctx, t, wt.js, 10*time.Second)
	if diff := cmp.Diff([]string{"/forbidden", "/after-2", "/after-1"}, wt.urls()); diff != "" {
		t.Errorf("the requests (-want +got):\n%s", diff)
	}
}

// spec: ACCESS-1
func TestNoJobIsTakenWhileRequestsArentAllowed(t *testing.T) {
	wt := newWorkerTest(t, pathBody, 0)
	wt.access.pause(true)
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/filing"))
	expired := newJob(messages.KindPoll, "https://www.sec.gov/expired")
	expired.ExpiresAt = timestamp(time.Now().Add(time.Second))
	wt.queue(expired)
	wt.start()
	time.Sleep(1500 * time.Millisecond)

	for _, name := range Consumers {
		if info := consumerState(wt.ctx, t, wt.js, name); info.Delivered.Consumer != 0 {
			t.Errorf("%s delivered %d jobs while requests were paused", name, info.Delivered.Consumer)
		}
	}
	wt.access.pause(false)
	settled(wt.ctx, t, wt.js, 10*time.Second)
	// The poll expired while it waited, so it's acknowledged without a
	// request (JOB-5).
	if diff := cmp.Diff([]string{"/filing"}, wt.urls()); diff != "" {
		t.Errorf("the requests (-want +got):\n%s", diff)
	}
}

func TestWhileAWriteFailsNoOtherRequestIsSent(t *testing.T) {
	wt := newWorkerTest(t, pathBody, 0)
	wt.records.fail("Add", 1_000_000)
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/first"))
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/second"))
	wt.start()
	testenv.Eventually(t, 10*time.Second, func() error {
		if tries := wt.records.tries("Add"); tries < 20 {
			return fmt.Errorf("%d tries", tries)
		}
		return nil
	})
	if diff := cmp.Diff([]string{"/first"}, wt.urls()); diff != "" {
		t.Errorf("the requests while the record couldn't be added (-want +got):\n%s", diff)
	}
	wt.records.fail("Add", 0)
	settled(wt.ctx, t, wt.js, 10*time.Second)
	if diff := cmp.Diff([]string{"/first", "/second"}, wt.urls()); diff != "" {
		t.Errorf("the requests (-want +got):\n%s", diff)
	}
	if rows := wt.records.all(); len(rows) != 2 {
		t.Errorf("%d records", len(rows))
	}
}

func TestAFetchInProgressWhenTheWorkerStopsIsAbandonedWithoutARecord(t *testing.T) {
	for _, c := range []struct {
		name string
		stop func(*workerTest)
		// again is whether the job is delivered again at once, rather than
		// once JetStream stops waiting for its acknowledgement, after 30
		// seconds.
		again bool
	}{
		{"as Kubernetes stops the pod", (*workerTest).stop, true},
		{"as the replica loses the Lease", (*workerTest).lose, false},
	} {
		t.Run(c.name, func(t *testing.T) {
			arrived := make(chan struct{})
			var first sync.Once
			wt := newWorkerTest(t, func(w http.ResponseWriter, r *http.Request) {
				held := false
				first.Do(func() { held = true })
				if held {
					close(arrived)
					<-r.Context().Done()
					return
				}
				pathBody(w, r)
			}, 0)
			wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/filing"))
			wt.start()
			<-arrived
			c.stop(wt)
			if rows := wt.records.all(); len(rows) != 0 {
				t.Errorf("%d records of a fetch abandoned", len(rows))
			}

			// The next active replica.
			wt.start()
			if !c.again {
				noEvent(t, wt.events, 3*time.Second)
				return
			}
			nextEvent(t, wt.events, 5*time.Second)
			settled(wt.ctx, t, wt.js, 10*time.Second)
			if rows := wt.records.all(); len(rows) != 1 {
				t.Errorf("%d records", len(rows))
			}
		})
	}
}

// spec: JOB-4, BACKFILL-2, FETCHER-10
func TestFetchesInProgressDuringAWaitWhenTheWorkerStopsAreAbandonedWithoutARecord(t *testing.T) {
	for _, c := range []struct {
		name string
		stop func(*workerTest)
		// ackWait is how long JetStream waits for a job's acknowledgement
		// before it delivers the job again: past the test, but for a job
		// asked to be delivered again at once.
		ackWait time.Duration
	}{
		{"as Kubernetes stops the pod", (*workerTest).stop, 0},
		{"as the replica loses the Lease", (*workerTest).lose, time.Second},
	} {
		t.Run(c.name, func(t *testing.T) {
			backfill := newUnavailable("/backfill", "60")
			arrived := make(chan struct{})
			var held sync.Once
			wt := newWorkerTest(t, func(w http.ResponseWriter, r *http.Request) {
				if backfill.answer(w, r) {
					return
				}
				hold := false
				if r.URL.Path == "/poll" {
					held.Do(func() { hold = true })
				}
				if hold {
					close(arrived)
					<-r.Context().Done()
					return
				}
				pathBody(w, r)
			}, c.ackWait)
			jobs := []messages.Job{
				newJob(messages.KindPoll, "https://www.sec.gov/poll"),
				newJob(messages.KindBackfill, "https://www.sec.gov/backfill"),
			}
			wt.queue(jobs[1])
			wt.start()
			await(t, backfill.failed, 10*time.Second, "first attempt of the Backfill's fetch")
			// The Backfill's fetch waits a minute before its retry, and a
			// poll's job, queued meanwhile, is fetched.
			wt.queue(jobs[0])
			await(t, arrived, 10*time.Second, "request of the poll's fetch during the Backfill's wait")
			c.stop(wt)
			if rows := wt.records.all(); len(rows) != 0 {
				t.Fatalf("%d records of fetches abandoned", len(rows))
			}

			// The next active replica fetches both again.
			wt.start()
			got := announced(t, wt.events, 2)
			settled(wt.ctx, t, wt.js, 10*time.Second)
			rows := wt.records.all()
			if len(rows) != 2 {
				t.Fatalf("%d records", len(rows))
			}
			for _, job := range jobs {
				if found, err := wt.records.ByJob(wt.ctx, job.ID); err != nil || found == nil {
					t.Errorf("no record of the job for %s: %v", job.URL, err)
				}
			}
			slices.Sort(got)
			if diff := cmp.Diff([]string{"/backfill", "/poll"}, got); diff != "" {
				t.Errorf("the events (-want +got):\n%s", diff)
			}
		})
	}
}
