package fetcher

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/go-logr/logr"
	"github.com/nats-io/nats.go/jetstream"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
)

// Consumers are the fetcher's durable consumers of JOBS, one for each kind of
// job, in the order it asks them for a job (FETCHER-9, FETCHER-10).
var Consumers = []string{
	messages.ConsumerFetcherPolls,
	messages.ConsumerFetcherFilings,
	messages.ConsumerFetcherBackfills,
}

// Worker is the active replica's work (spec/snapshots.md, section 10, jobs
// and events): it takes each job and fetches it, records the fetch, and
// announces the record, until its context is done.
type Worker struct {
	JetStream jetstream.JetStream
	Schemas   *messages.Schemas
	Records   Records
	Objects   Objects
	Access    Access
	Client    *Client
	Tool      string
	// Permit says whether a request may be sent now, as the Client's
	// does: the Worker takes no job while it refuses.
	Permit func() error
	// Shutdown is done once Kubernetes stops the replica's pod, when the
	// Worker asks JetStream to deliver again the job it's at work on.
	Shutdown context.Context
	Log      logr.Logger

	// Idle is the wait before it asks for jobs again when none is waiting,
	// or before it looks again whether it may take one; Hold the wait
	// between tries of a step that failed, such as a write; InProgress how
	// often it tells JetStream it's at work on a job; and Wait the wait
	// between looks for its consumers. The tests give shorter ones.
	Idle       time.Duration
	Hold       time.Duration
	InProgress time.Duration
	Wait       time.Duration
	// StepTimeout bounds each try of a step that reaches the database,
	// Garage, the API server, or NATS.
	StepTimeout time.Duration
	Clock       func() time.Time
}

func (w *Worker) defaults() {
	if w.Idle == 0 {
		w.Idle = 200 * time.Millisecond
	}
	if w.Hold == 0 {
		w.Hold = 2 * time.Second
	}
	if w.InProgress == 0 {
		// JetStream delivers a job again 30 seconds after it last heard of
		// it, by default (FETCHER-10).
		w.InProgress = 10 * time.Second
	}
	if w.Wait == 0 {
		w.Wait = 2 * time.Second
	}
	if w.StepTimeout == 0 {
		w.StepTimeout = 30 * time.Second
	}
	if w.Clock == nil {
		w.Clock = time.Now
	}
	if w.Permit == nil {
		w.Permit = w.Access.Allowed
	}
	if w.Shutdown == nil {
		w.Shutdown = context.Background()
	}
}

// Run takes jobs until ctx is done. It makes no stream and no consumer: until
// its consumers exist, it waits for them (FETCHER-11). It takes no job while
// a request may not be sent, as while requests aren't allowed, so jobs wait
// in JOBS, as the owner decided. Before each request, it takes the job
// queued first of the first kind that has one waiting: it asks fetcher-polls
// for a job, then fetcher-filings, then fetcher-backfills (JOB-4,
// BACKFILL-2, FETCHER-10).
func (w *Worker) Run(ctx context.Context) {
	w.defaults()
	var consumers []jetstream.Consumer
	for ctx.Err() == nil {
		if consumers == nil {
			if consumers = w.consumers(ctx); consumers == nil {
				return
			}
		}
		if w.Permit() != nil {
			_ = sleep(ctx, w.Idle)
			continue
		}
		msg, err := next(consumers)
		if err != nil {
			if ctx.Err() == nil {
				w.Log.Error(err, "Couldn't ask the consumers for a job")
				consumers = nil
				_ = sleep(ctx, w.Wait)
			}
			continue
		}
		if msg == nil {
			_ = sleep(ctx, w.Idle)
			continue
		}
		w.handle(ctx, msg)
	}
}

// consumers waits until each of the fetcher's consumers exists, and gives
// them, or nil once ctx is done.
func (w *Worker) consumers(ctx context.Context) []jetstream.Consumer {
	waiting := false
	for ctx.Err() == nil {
		found, err := w.lookUp(ctx)
		if err == nil {
			w.Log.Info("Reading jobs", "stream", messages.StreamJobs, "consumers", Consumers)
			return found
		}
		if !waiting && ctx.Err() == nil {
			w.Log.Info("Waiting for the consumers of jobs", "stream", messages.StreamJobs, "reason", err.Error())
			waiting = true
		}
		_ = sleep(ctx, w.Wait)
	}
	return nil
}

func (w *Worker) lookUp(ctx context.Context) ([]jetstream.Consumer, error) {
	ctx, cancel := context.WithTimeout(ctx, w.StepTimeout)
	defer cancel()
	found := make([]jetstream.Consumer, 0, len(Consumers))
	for _, name := range Consumers {
		consumer, err := w.JetStream.Consumer(ctx, messages.StreamJobs, name)
		if err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
		found = append(found, consumer)
	}
	return found, nil
}

// next asks each consumer in turn for a job, and gives the first it gets, or
// nil if none is waiting.
func next(consumers []jetstream.Consumer) (jetstream.Msg, error) {
	for _, consumer := range consumers {
		batch, err := consumer.FetchNoWait(1)
		if err != nil {
			return nil, err
		}
		for msg := range batch.Messages() {
			return msg, nil
		}
		if err := batch.Error(); err != nil {
			return nil, err
		}
	}
	return nil, nil
}

// handle takes one job through to its acknowledgement, telling JetStream
// meanwhile that it's at work on it (FETCHER-10). A job that doesn't
// validate, or whose kind isn't its subject's, it acknowledges without a
// request or a record (JOB-7), as it does one whose URL isn't a URL it may
// fetch (FETCH-1). For a job whose record the table holds, it
// publishes that record's event again (JOB-6); one that has expired it
// acknowledges without a request (JOB-5). It fetches any other, then
// records the stop if the fetch got a 403 (ACCESS-3), stores the object,
// adds the record, and publishes the event, each once the step before has
// succeeded, and only then acknowledges the job (FETCH-14, JOB-6). If ctx
// is done first, it abandons the job, without a record, and the job is
// delivered again (abandon).
func (w *Worker) handle(ctx context.Context, msg jetstream.Msg) {
	defer w.working(msg)()
	log := w.Log.WithValues("subject", msg.Subject())
	// Decoded from the values the schema checked, by their names exactly,
	// so a field it doesn't know, whatever its case, is ignored (JOB-7).
	job, err := w.Schemas.DecodeJob(msg.Data())
	if err != nil {
		log.Info("Acknowledged a job that doesn't validate, without a request", "reason", err.Error())
		w.ack(ctx, msg, log)
		return
	}
	if kind := strings.TrimPrefix(msg.Subject(), messages.SubjectJobs); job.Kind != kind {
		log.Info("Acknowledged a job whose kind isn't its subject's, without a request", "kind", job.Kind)
		w.ack(ctx, msg, log)
		return
	}
	log = log.WithValues("job", job.ID, "feed", job.Feed, "url", job.URL)
	if _, err := ParseURL(job.URL); err != nil {
		// Its URL begins as an https URL on one of the SEC's hosts does, as
		// the schema has it, but isn't a URL, so no request can be made for
		// it, then or after it's delivered again (FETCH-1).
		log.Info("Acknowledged a job whose URL can't be requested, without a request", "reason", err.Error())
		w.ack(ctx, msg, log)
		return
	}

	var stored *Stored
	if err := w.hold(ctx, log, "read the job's record", func(ctx context.Context) (err error) {
		stored, err = w.Records.ByJob(ctx, job.ID)
		return err
	}); err != nil {
		w.abandon(msg, log)
		return
	}
	if stored != nil {
		if err := w.announce(ctx, log, msg.Data(), job, *stored); err != nil {
			w.abandon(msg, log)
			return
		}
		log.Info("Announced the job's record again, without a request", "record", stored.ID)
		w.ack(ctx, msg, log)
		return
	}
	if job.ExpiresAt != "" {
		expires, err := parseTimestamp(job.ExpiresAt)
		if err == nil && !w.Clock().Before(expires) {
			log.Info("Acknowledged a job that has expired, without a request", "expiresAt", job.ExpiresAt)
			w.ack(ctx, msg, log)
			return
		}
	}

	var previous *Version
	if err := w.hold(ctx, log, "read the URL's latest version", func(ctx context.Context) (err error) {
		previous, err = w.Records.LatestVersion(ctx, job.URL)
		return err
	}); err != nil {
		w.abandon(msg, log)
		return
	}
	result, err := w.Client.Fetch(ctx, job.Feed, job.URL, w.Tool, previous)
	if err != nil {
		w.abandon(msg, log)
		return
	}
	record := result.Record
	if record.Status != nil && *record.Status == http.StatusForbidden {
		// Recorded before the fetch's record, so a replica that takes the
		// Lease over, should this one stop between the two, reads it, and
		// sends nothing (ACCESS-3).
		stop := v1alpha1.Stop{At: v1alpha1.Timestamp(record.RequestedAt), URL: job.URL}
		log.Info("Got a 403, so stopping requests until the owner acknowledges the stop", "at", stop.At)
		if err := w.hold(ctx, log, "record the stop in the SecAccess", func(ctx context.Context) error {
			return w.Access.Stop(ctx, stop)
		}); err != nil {
			w.abandon(msg, log)
			return
		}
	}
	if result.Object != nil {
		if err := w.hold(ctx, log, "store the object", func(ctx context.Context) error {
			return w.Objects.Put(ctx, *record.Object, result.Object)
		}); err != nil {
			w.abandon(msg, log)
			return
		}
	}
	added := Stored{Record: record}
	if err := w.hold(ctx, log, "add the record", func(ctx context.Context) error {
		id, err := w.Records.Add(ctx, job.ID, record)
		if errors.Is(err, ErrRecorded) {
			// The replica before this one recorded the job's fetch since
			// this one read the table: that record is the job's.
			found, err := w.Records.ByJob(ctx, job.ID)
			if err == nil && found == nil {
				err = errors.New("the table refused the record as the job's, but holds none")
			}
			if err != nil {
				return err
			}
			added = *found
			return nil
		}
		added.ID = id
		return err
	}); err != nil {
		w.abandon(msg, log)
		return
	}
	if err := w.announce(ctx, log, msg.Data(), job, added); err != nil {
		w.abandon(msg, log)
		return
	}
	w.ack(ctx, msg, log)
}

// announce publishes the event of a job's record on the subject of the job's
// kind, which RECORDS takes, with the job as it was queued (JOB-6).
func (w *Worker) announce(ctx context.Context, log logr.Logger, queued []byte, job messages.Job, stored Stored) error {
	data, err := json.Marshal(event{ID: stored.ID, Job: json.RawMessage(queued), Record: stored.Record})
	if err != nil {
		return err
	}
	subject := messages.SubjectEvents + job.Kind
	return w.hold(ctx, log, "publish the event", func(ctx context.Context) error {
		_, err := w.JetStream.Publish(ctx, subject, data, jetstream.WithExpectStream(messages.StreamRecords))
		return err
	})
}

// hold tries a step until it succeeds, waiting Hold between tries, so a
// fetch's writes wait out a database, Garage, API server, or NATS that can't
// take them, and the Worker sends no other request meanwhile, as the owner
// decided. It gives up only once ctx is done.
func (w *Worker) hold(ctx context.Context, log logr.Logger, what string, step func(context.Context) error) error {
	for tries := 1; ; tries++ {
		try, cancel := context.WithTimeout(ctx, w.StepTimeout)
		err := step(try)
		cancel()
		if err == nil {
			if tries > 1 {
				log.Info("Done, after trying again: "+what, "tries", tries)
			}
			return nil
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		// The first failure, and one each minute or so after it.
		if tries == 1 || tries%30 == 0 {
			log.Error(err, "Couldn't "+what+", so trying again", "tries", tries)
		}
		if err := sleep(ctx, w.Hold); err != nil {
			return err
		}
	}
}

// working tells JetStream that the Worker is at work on the job, each
// InProgress, until the function it returns is called, so the job isn't
// delivered again however long its fetch takes (FETCHER-10).
func (w *Worker) working(msg jetstream.Msg) func() {
	done := make(chan struct{})
	stopped := make(chan struct{})
	go func() {
		defer close(stopped)
		ticker := time.NewTicker(w.InProgress)
		defer ticker.Stop()
		for {
			select {
			case <-done:
				return
			case <-ticker.C:
				if err := msg.InProgress(); err != nil {
					w.Log.Error(err, "Couldn't tell JetStream the job is in progress")
				}
			}
		}
	}()
	return func() {
		close(done)
		<-stopped
	}
}

func (w *Worker) ack(ctx context.Context, msg jetstream.Msg, log logr.Logger) {
	ctx, cancel := context.WithTimeout(ctx, w.StepTimeout)
	defer cancel()
	if err := msg.DoubleAck(ctx); err != nil && ctx.Err() == nil {
		// The job is delivered again, and its record, if it has one, is
		// announced again without a request (JOB-6).
		log.Error(err, "Couldn't acknowledge the job")
	}
}

// abandon leaves a job the Worker was at work on when its context ended:
// without a record of a fetch it didn't finish, or with what it had written,
// and unacknowledged, so the job is delivered again, as after a replica
// that's lost, as the owner decided. Once Kubernetes has stopped the pod, it
// asks JetStream to deliver the job again at once.
func (w *Worker) abandon(msg jetstream.Msg, log logr.Logger) {
	if w.Shutdown.Err() == nil {
		log.Info("Left the job to be delivered again, since this replica is no longer the active one")
		return
	}
	if err := msg.Nak(); err != nil {
		log.Error(err, "Couldn't ask JetStream to deliver the job again")
		return
	}
	log.Info("Asked JetStream to deliver the job again, since this replica is stopping")
}
