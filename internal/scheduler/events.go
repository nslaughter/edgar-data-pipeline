package scheduler

import (
	"context"
	"errors"
	"time"

	"github.com/go-logr/logr"
	"github.com/nats-io/nats.go/jetstream"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/client-go/util/retry"
	"sigs.k8s.io/controller-runtime/pkg/client"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
)

// PollEvents sets each Source's lastPollTime and lastSuccessfulPollTime from
// the records of its polls, which it reads in their events through the
// durable consumer scheduler-polls (SOURCE-4, SCHED-8). It's a runnable that
// runs only on the active replica (SCHED-4). It makes no stream and no
// consumer: until the consumer exists, it waits for it (SCHED-9).
type PollEvents struct {
	JetStream jetstream.JetStream
	Schemas   *messages.Schemas
	// Reader reads each Source from the API server, not the cache, so the
	// times it compares an event's with are the latest.
	Reader    client.Reader
	Client    client.Client
	Namespace string
	// Wait is the time between looks for the consumer, and before an event
	// whose status couldn't be set is delivered again.
	Wait time.Duration
	Log  logr.Logger
}

// Start reads the events until ctx is done.
func (e *PollEvents) Start(ctx context.Context) error {
	waiting := false
	for ctx.Err() == nil {
		consumer, err := e.JetStream.Consumer(ctx, messages.StreamRecords, messages.ConsumerSchedulerPolls)
		if err != nil {
			if !waiting && ctx.Err() == nil {
				e.Log.Info("Waiting for the consumer of poll events",
					"stream", messages.StreamRecords, "consumer", messages.ConsumerSchedulerPolls, "reason", err.Error())
				waiting = true
			}
			e.sleep(ctx)
			continue
		}
		waiting = false
		e.Log.Info("Reading poll events", "stream", messages.StreamRecords, "consumer", messages.ConsumerSchedulerPolls)
		if err := e.read(ctx, consumer); err != nil && ctx.Err() == nil {
			e.Log.Error(err, "Stopped reading poll events")
			e.sleep(ctx)
		}
	}
	return nil
}

func (e *PollEvents) sleep(ctx context.Context) {
	select {
	case <-ctx.Done():
	case <-time.After(e.wait()):
	}
}

func (e *PollEvents) wait() time.Duration {
	if e.Wait > 0 {
		return e.Wait
	}
	return 2 * time.Second
}

// read handles the consumer's events, one at a time, until ctx is done or
// the consumer can't deliver.
func (e *PollEvents) read(ctx context.Context, consumer jetstream.Consumer) error {
	events, err := consumer.Messages()
	if err != nil {
		return err
	}
	stopped := make(chan struct{})
	defer close(stopped)
	go func() {
		select {
		case <-ctx.Done():
		case <-stopped:
		}
		events.Stop()
	}()
	for {
		msg, err := events.Next()
		if err != nil {
			if errors.Is(err, jetstream.ErrMsgIteratorClosed) {
				return nil
			}
			return err
		}
		e.handle(ctx, msg)
	}
}

// handle sets a Source's poll times from an event, then acknowledges it. An
// event it can't use, one that doesn't validate against event.schema.json,
// isn't of a poll, or names no Source in the release namespace, it
// acknowledges without acting on it, as the owner decided. One whose status
// it couldn't set is delivered again.
func (e *PollEvents) handle(ctx context.Context, msg jetstream.Msg) {
	event, err := e.Schemas.DecodeEvent(msg.Data())
	if err != nil {
		e.Log.Info("Acknowledged a poll event that doesn't validate, without acting on it", "reason", err.Error())
		e.ack(msg)
		return
	}
	log := e.Log.WithValues("event", event.ID, "source", event.Job.Source)
	if event.Job.Kind != messages.KindPoll {
		log.Info("Acknowledged an event that isn't of a poll, without acting on it", "kind", event.Job.Kind)
		e.ack(msg)
		return
	}
	found, err := e.setPollTimes(ctx, event)
	if err != nil {
		if ctx.Err() == nil {
			log.Error(err, "Couldn't set the Source's poll times; the event will be delivered again")
			if err := msg.NakWithDelay(e.wait()); err != nil {
				log.Error(err, "Couldn't ask for the event again")
			}
		}
		return
	}
	if !found {
		log.Info("Acknowledged a poll event of no Source in the release namespace, without acting on it")
	}
	e.ack(msg)
}

func (e *PollEvents) ack(msg jetstream.Msg) {
	if err := msg.Ack(); err != nil {
		e.Log.Error(err, "Couldn't acknowledge a poll event")
	}
}

// setPollTimes sets the Source's lastPollTime to the event's requested_at,
// unless it's later, and so its lastSuccessfulPollTime, if the record names
// a version. Each Timestamp has one form, so the later of two is the greater
// string. It says whether the Source exists.
func (e *PollEvents) setPollTimes(ctx context.Context, event messages.Event) (bool, error) {
	at := v1alpha1.Timestamp(event.Record.RequestedAt)
	found := true
	err := retry.RetryOnConflict(retry.DefaultRetry, func() error {
		source := &v1alpha1.Source{}
		err := e.Reader.Get(ctx, client.ObjectKey{Namespace: e.Namespace, Name: event.Job.Source}, source)
		if apierrors.IsNotFound(err) {
			found = false
			return nil
		} else if err != nil {
			return err
		}
		original := source.DeepCopy()
		if at > source.Status.LastPollTime {
			source.Status.LastPollTime = at
		}
		if event.Record.NamesVersion() && at > source.Status.LastSuccessfulPollTime {
			source.Status.LastSuccessfulPollTime = at
		}
		if source.Status.LastPollTime == original.Status.LastPollTime &&
			source.Status.LastSuccessfulPollTime == original.Status.LastSuccessfulPollTime {
			return nil
		}
		err = e.Client.Status().Patch(ctx, source,
			client.MergeFromWithOptions(original, client.MergeFromWithOptimisticLock{}))
		if apierrors.IsNotFound(err) {
			found = false
			return nil
		}
		return err
	})
	return found, err
}
