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
	// A field it doesn't know, it ignores, and fetches the job.
	known, err := json.Marshal(newJob(messages.KindBackfill, "https://www.sec.gov/known"))
	if err != nil {
		t.Fatal(err)
	}
	publish(ctx, t, wt.js, "edgar.jobs.backfill", []byte(strings.Replace(string(known), "{", `{"later":1,`, 1)))
	wt.start()
	settled(ctx, t, wt.js, 10*time.Second)

	if diff := cmp.Diff([]string{"/known"}, wt.urls()); diff != "" {
		t.Errorf("the requests (-want +got):\n%s", diff)
	}
	if rows := wt.records.all(); len(rows) != 1 {
		t.Errorf("%d records", len(rows))
	}
}

// spec: FETCHER-10
func TestAJobTheWorkerIsAtWorkOnIsntDeliveredAgainHoweverLongItsFetchTakes(t *testing.T) {
	// JetStream would deliver the job again a second after it last heard of
	// it; the fetch takes three.
	wt := newWorkerTest(t, func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(3 * time.Second)
		pathBody(w, r)
	}, time.Second)
	wt.queue(newJob(messages.KindFiling, "https://www.sec.gov/slow"))
	wt.start()
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
