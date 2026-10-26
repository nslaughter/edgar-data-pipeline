package messages

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"
)

// spec: JOB-7
func TestAJobIsDecodedByItsSchemasNamesExactly(t *testing.T) {
	schemas, err := LoadSchemas()
	if err != nil {
		t.Fatal(err)
	}
	poll := Job{ID: "0b6f2b4e-2f0e-4c4b-9d55-8f1c2f3a4b5c", Kind: KindPoll, Feed: "submissions",
		URL: "https://data.sec.gov/submissions/CIK0000320193.json", Source: "apple-submissions",
		QueuedAt: "2026-10-26T09:30:00.123Z", ExpiresAt: "2026-10-26T09:30:10.123Z"}
	backfill := Job{ID: "7d1f0c2a-5b3e-4f6a-8c9d-0e1f2a3b4c5d", Kind: KindBackfill, Feed: "filings",
		URL: "https://www.sec.gov/Archives/edgar/data/320193/index.json", Backfill: "apple-2025",
		QueuedAt: "2026-10-26T09:30:00.123Z"}
	// Between them, the two jobs set every field, so a field added to Job
	// and not to DecodeJob fails here.
	for i := range reflect.TypeFor[Job]().NumField() {
		if reflect.ValueOf(poll).Field(i).IsZero() && reflect.ValueOf(backfill).Field(i).IsZero() {
			t.Fatalf("neither job sets %s", reflect.TypeFor[Job]().Field(i).Name)
		}
	}
	for _, job := range []Job{poll, backfill} {
		data, err := json.Marshal(job)
		if err != nil {
			t.Fatal(err)
		}
		// Each field again, after it, under a name that differs only in case.
		variants := `,"ID":"not-a-uuid","Kind":"filing","FEED":"other","Url":"https://example.com/",` +
			`"Source":"other","BACKFILL":"other","Queued_At":"later","expires_AT":"later"}`
		data = []byte(strings.TrimSuffix(string(data), "}") + variants)
		got, err := schemas.DecodeJob(data)
		if err != nil {
			t.Fatalf("%s: %v", data, err)
		}
		if got != job {
			t.Errorf("decoded as %+v, not %+v", got, job)
		}
	}
	if _, err := schemas.DecodeJob([]byte(`{"id":"not-a-uuid"}`)); err == nil {
		t.Error("a job that doesn't validate decoded")
	}
}
