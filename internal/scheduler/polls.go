package scheduler

import (
	"context"
	"encoding/json"
	"sync"
	"time"

	"github.com/go-logr/logr"
	"github.com/google/uuid"
	"k8s.io/utils/clock"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
	"github.com/nslaughter/edgar-data-pipeline/internal/messages"
)

// Poll is what a Source's polls are queued from: whether it's polling, and
// its spec.
type Poll struct {
	Polling bool
	Feed    string
	URL     string
	Cadence time.Duration
}

// Publisher queues a message: it returns once the stream has kept it, and an
// error if it didn't, as JetStream's publish does. A message published again
// with the same id is kept once.
type Publisher interface {
	Publish(ctx context.Context, subject string, data []byte, id string) error
}

// Poller queues each polling Source's polls, each as a job of kind poll
// (SOURCE-2, JOB-2). It's a runnable that runs only on the active replica,
// so it starts when the replica becomes the active one (SCHED-4).
type Poller struct {
	Publisher Publisher
	Clock     clock.WithTicker
	// Retry is the time between attempts to queue a poll that wasn't kept.
	Retry time.Duration
	Log   logr.Logger

	mu      sync.Mutex
	ctx     context.Context
	desired map[string]Poll
	loops   map[string]*loop
}

// A loop queues one Source's polls.
type loop struct {
	updates chan Poll
	cancel  context.CancelFunc
	done    chan struct{}
}

// Set gives the Poller a Source's Poll. A Source begins polling, and its
// first poll is queued, when it's set polling while it wasn't, or when the
// Poller starts. A Source set not polling queues nothing from when Set
// returns.
func (p *Poller) Set(name string, poll Poll) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.desired == nil {
		p.desired = map[string]Poll{}
		p.loops = map[string]*loop{}
	}
	if !poll.Polling {
		delete(p.desired, name)
		p.stop(name)
		return
	}
	p.desired[name] = poll
	if l, ok := p.loops[name]; ok {
		select {
		case <-l.updates:
		default:
		}
		l.updates <- poll
	} else if p.ctx != nil {
		p.begin(name, poll)
	}
}

// Retain stops the polls of each Source not named.
func (p *Poller) Retain(names map[string]bool) {
	p.mu.Lock()
	defer p.mu.Unlock()
	for name := range p.desired {
		if !names[name] {
			delete(p.desired, name)
			p.stop(name)
		}
	}
}

// Start queues each polling Source's polls until ctx is done, then waits
// until none is being queued.
func (p *Poller) Start(ctx context.Context) error {
	p.mu.Lock()
	if p.desired == nil {
		p.desired = map[string]Poll{}
		p.loops = map[string]*loop{}
	}
	p.ctx = ctx
	for name, poll := range p.desired {
		p.begin(name, poll)
	}
	p.mu.Unlock()

	<-ctx.Done()

	p.mu.Lock()
	defer p.mu.Unlock()
	p.ctx = nil
	for name := range p.loops {
		p.stop(name)
	}
	return nil
}

func (p *Poller) begin(name string, poll Poll) {
	ctx, cancel := context.WithCancel(p.ctx)
	l := &loop{updates: make(chan Poll, 1), cancel: cancel, done: make(chan struct{})}
	p.loops[name] = l
	go func() {
		defer close(l.done)
		p.run(ctx, name, poll, l.updates)
	}()
}

// stop ends a Source's loop, and waits until it can queue nothing more.
func (p *Poller) stop(name string) {
	if l, ok := p.loops[name]; ok {
		l.cancel()
		<-l.done
		delete(p.loops, name)
	}
}

// run queues a Source's polls: the first when it begins, and each later one
// due its cadence after the one before it was due, queued no earlier, and
// skipped if it can't be queued before the one after it is due. A new
// cadence applies from the next poll, which is due the new cadence after the
// last poll queued was due (SOURCE-2).
func (p *Poller) run(ctx context.Context, name string, poll Poll, updates <-chan Poll) {
	log := p.Log.WithValues("source", name)
	due := p.Clock.Now()
	var lastQueued time.Time
	var id string
	timer := p.Clock.NewTimer(time.Hour)
	defer timer.Stop()
	for {
		// Act on what's due, then wait until the next poll is due, or the
		// next attempt to queue one.
		now := p.Clock.Now()
		for !now.Before(due.Add(poll.Cadence)) {
			log.Info("Skipped a poll that couldn't be queued before the next was due", "due", due)
			due = due.Add(poll.Cadence)
			id = ""
		}
		wait := due.Sub(now)
		if wait <= 0 {
			if id == "" {
				id = uuid.NewString()
			}
			next := due.Add(poll.Cadence)
			err := p.queue(ctx, name, poll, id, now, next)
			if err == nil {
				lastQueued, due, id = due, next, ""
				continue
			}
			if ctx.Err() != nil {
				return
			}
			log.Error(err, "Couldn't queue a poll; trying again", "due", due)
			if wait = min(p.retry(), next.Sub(p.Clock.Now())); wait <= 0 {
				continue
			}
		}
		select {
		case <-timer.C():
		default:
		}
		timer.Reset(wait)
		select {
		case <-ctx.Done():
			return
		case next := <-updates:
			if next.Cadence != poll.Cadence && !lastQueued.IsZero() {
				due = lastQueued.Add(next.Cadence)
				id = ""
			}
			poll = next
		case <-timer.C():
		}
	}
}

func (p *Poller) retry() time.Duration {
	if p.Retry > 0 {
		return p.Retry
	}
	return time.Second
}

// queue publishes a poll's job, trying until its expiry, when the next poll
// is due (JOB-2).
func (p *Poller) queue(ctx context.Context, name string, poll Poll, id string, now, expires time.Time) error {
	data, err := json.Marshal(messages.Job{
		ID:        id,
		Kind:      messages.KindPoll,
		Feed:      poll.Feed,
		URL:       poll.URL,
		Source:    name,
		QueuedAt:  now.UTC().Format(v1alpha1.TimestampLayout),
		ExpiresAt: expires.UTC().Format(v1alpha1.TimestampLayout),
	})
	if err != nil {
		return err
	}
	ctx, cancel := context.WithDeadline(ctx, expires)
	defer cancel()
	return p.Publisher.Publish(ctx, messages.SubjectPollJob, data, id)
}
