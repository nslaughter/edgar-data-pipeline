// Package messages holds the messages the Go services exchange through NATS
// JetStream, and validates them against their schemas in spec/schemas/: a job
// and an event, as the store's specification gives them (spec/snapshots.md,
// section 10). The fetcher, which writes events, gives each record's every
// field (internal/fetcher); the readers here decode only the fields they
// read.
package messages

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io/fs"
	"path"

	"github.com/santhosh-tekuri/jsonschema/v6"

	edgar "github.com/nslaughter/edgar-data-pipeline"
)

// The subjects jobs and events are published on: each ends in its job's kind.
const (
	SubjectPollJob   = "edgar.jobs.poll"
	SubjectPollEvent = "edgar.records.poll"
	// SubjectJobs and SubjectEvents begin the subjects of each kind's jobs
	// and events.
	SubjectJobs   = "edgar.jobs."
	SubjectEvents = "edgar.records."
)

// The streams and the consumers the scheduler and the fetcher use, which the
// umbrella chart's JetStream Job makes (spec/deployment.md, NATS-3, SCHED-8,
// FETCHER-9).
const (
	StreamJobs               = "JOBS"
	StreamRecords            = "RECORDS"
	ConsumerSchedulerPolls   = "scheduler-polls"
	ConsumerFetcherPolls     = "fetcher-polls"
	ConsumerFetcherFilings   = "fetcher-filings"
	ConsumerFetcherBackfills = "fetcher-backfills"
)

// The kinds of job: a Source's poll; a filing the discoverer found; and a
// Backfill's fetch.
const (
	KindPoll     = "poll"
	KindFiling   = "filing"
	KindBackfill = "backfill"
)

// Job is one fetch the fetcher is asked to make (job.schema.json).
type Job struct {
	ID        string `json:"id"`
	Kind      string `json:"kind"`
	Feed      string `json:"feed"`
	URL       string `json:"url"`
	Source    string `json:"source,omitempty"`
	Backfill  string `json:"backfill,omitempty"`
	QueuedAt  string `json:"queued_at"`
	ExpiresAt string `json:"expires_at,omitempty"`
}

// Event announces the record of a fetch (event.schema.json). It holds only
// the fields the services read; a reader ignores the others (JOB-7).
type Event struct {
	ID     int64  `json:"id"`
	Job    Job    `json:"job"`
	Record Record `json:"record"`
}

// Record is the part of an event's record the services read.
type Record struct {
	Feed        string  `json:"feed"`
	URL         string  `json:"url"`
	RequestedAt string  `json:"requested_at"`
	SHA256      *string `json:"sha256"`
}

// NamesVersion says whether the record names a version, as a successful
// poll's does: whether it holds the SHA-256 of the body it returned or
// confirmed (spec/controls.md, section 2; spec/snapshots.md, section 6).
func (r Record) NamesVersion() bool {
	return r.SHA256 != nil
}

// Schemas validates messages against the schemas in spec/schemas/.
type Schemas struct {
	job, event *jsonschema.Schema
}

// LoadSchemas compiles the schemas in spec/schemas/, each under its $id, so
// a reference from one to another resolves without the network. A format is
// asserted, as the schemas' comments say a validator does.
func LoadSchemas() (*Schemas, error) {
	compiler := jsonschema.NewCompiler()
	compiler.AssertFormat()
	ids := map[string]string{}
	names, err := fs.Glob(edgar.Schemas, "spec/schemas/*.schema.json")
	if err != nil {
		return nil, err
	}
	for _, name := range names {
		data, err := fs.ReadFile(edgar.Schemas, name)
		if err != nil {
			return nil, err
		}
		doc, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
		if err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
		object, ok := doc.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s: not an object", name)
		}
		id, ok := object["$id"].(string)
		if !ok {
			return nil, fmt.Errorf("%s: no $id", name)
		}
		if err := compiler.AddResource(id, doc); err != nil {
			return nil, fmt.Errorf("%s: %w", name, err)
		}
		ids[path.Base(name)] = id
	}
	var s Schemas
	if s.job, err = compiler.Compile(ids["job.schema.json"]); err != nil {
		return nil, err
	}
	if s.event, err = compiler.Compile(ids["event.schema.json"]); err != nil {
		return nil, err
	}
	return &s, nil
}

// ValidateJob checks a job's JSON against job.schema.json.
func (s *Schemas) ValidateJob(data []byte) error {
	return validate(s.job, data)
}

// DecodeJob checks a job's JSON against job.schema.json, and decodes it from
// the values the schema checked: each property by its name exactly as the
// schema spells it. A field whose name differs from one of them, if only in
// case, is one it doesn't know, and ignores (JOB-7), where encoding/json
// alone would take that field's value in place of the property's.
func (s *Schemas) DecodeJob(data []byte) (Job, error) {
	instance, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
	if err != nil {
		return Job{}, err
	}
	if err := s.job.Validate(instance); err != nil {
		return Job{}, err
	}
	// The schema holds a job to an object, and each of these to a string.
	object, _ := instance.(map[string]any)
	field := func(name string) string {
		value, _ := object[name].(string)
		return value
	}
	return Job{
		ID:        field("id"),
		Kind:      field("kind"),
		Feed:      field("feed"),
		URL:       field("url"),
		Source:    field("source"),
		Backfill:  field("backfill"),
		QueuedAt:  field("queued_at"),
		ExpiresAt: field("expires_at"),
	}, nil
}

// DecodeEvent checks an event's JSON against event.schema.json, and decodes
// it.
func (s *Schemas) DecodeEvent(data []byte) (Event, error) {
	var event Event
	if err := validate(s.event, data); err != nil {
		return event, err
	}
	err := json.Unmarshal(data, &event)
	return event, err
}

func validate(schema *jsonschema.Schema, data []byte) error {
	instance, err := jsonschema.UnmarshalJSON(bytes.NewReader(data))
	if err != nil {
		return err
	}
	return schema.Validate(instance)
}
