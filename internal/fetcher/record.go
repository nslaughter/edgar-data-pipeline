package fetcher

import (
	"encoding/json"
	"fmt"
	"time"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
)

// Record is the record of one fetch, with every field of the store's table of
// a record's fields, in that table's order, so each event gives them as the
// git store's logs do (spec/snapshots.md, section 6; REC-9, REC-10). A null
// field is a nil pointer.
type Record struct {
	Feed         string  `json:"feed"`
	URL          string  `json:"url"`
	RequestedAt  string  `json:"requested_at"`
	ReceivedAt   *string `json:"received_at"`
	Attempts     int     `json:"attempts"`
	Status       *int    `json:"status"`
	Error        *string `json:"error"`
	FinalURL     *string `json:"final_url"`
	Date         *string `json:"date"`
	ETag         *string `json:"etag"`
	LastModified *string `json:"last_modified"`
	ContentType  *string `json:"content_type"`
	SHA256       *string `json:"sha256"`
	Bytes        *int64  `json:"bytes"`
	Object       *string `json:"object"`
	Tool         string  `json:"tool"`
}

// NamesVersion says whether the record names a version: the body its fetch
// returned or confirmed (REC-3).
func (r Record) NamesVersion() bool {
	return r.SHA256 != nil
}

// Version is the latest record of a URL that names a version: its
// validators, which a conditional request sends, and the version a 304 to it
// names (FETCH-4, REC-5).
type Version struct {
	ETag         *string
	LastModified *string
	SHA256       string
	Bytes        int64
	Object       string
}

// Stored is a record the table holds, with its id.
type Stored struct {
	ID     int64
	Record Record
}

// event announces a record (event.schema.json): its id, the job as it was
// queued, and the record.
type event struct {
	ID     int64           `json:"id"`
	Job    json.RawMessage `json:"job"`
	Record Record          `json:"record"`
}

// Tool is the fetcher's tool, which names the commit its image was built
// from (spec/snapshots.md, section 6).
func Tool(commit string) string {
	return "fetcher@" + commit
}

// timestamp gives a time in the store's form, in UTC to the millisecond, as
// its table keeps it (spec/snapshots.md, section 2). The time is cut to the
// millisecond, not rounded, so the record its event gives is the row the
// table holds.
func timestamp(t time.Time) string {
	return t.UTC().Truncate(time.Millisecond).Format(v1alpha1.TimestampLayout)
}

// parseTimestamp reads a time in the store's form.
func parseTimestamp(s string) (time.Time, error) {
	t, err := time.Parse(v1alpha1.TimestampLayout, s)
	if err != nil {
		return time.Time{}, fmt.Errorf("not a timestamp: %q", s)
	}
	return t, nil
}

// ObjectName is the name of the object that holds the body whose SHA-256 is
// given, in either store (OBJ-1).
func ObjectName(sha256 string) string {
	return "objects/" + sha256[:2] + "/" + sha256 + ".gz"
}

func ptr[T any](v T) *T {
	return &v
}
