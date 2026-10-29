package transfer

import (
	"encoding/json"
	"fmt"
	"regexp"
	"strconv"
	"strings"
)

// Record is the record of one fetch, with every field of the store's table of
// a record's fields, in that table's order (spec/snapshots.md, section 6;
// REC-9). A null field is a nil pointer. The transfer copies each from the
// cluster store unchanged (XFER-2).
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

// Stored is a record the cluster store's table holds, with its id, which
// orders the records as they were added (STORE-1).
type Stored struct {
	ID     int64
	Record Record
}

// key is what no two records in the git store share (REC-12).
type key struct {
	url, requestedAt, tool string
}

func (r Record) key() key {
	return key{r.URL, r.RequestedAt, r.Tool}
}

// The forms of a feed's name and of an object's name, which the transfer
// checks before it makes a path of either (spec/snapshots.md, sections 2
// and 5).
var (
	feedName   = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*$`)
	objectName = regexp.MustCompile(`^objects/[0-9a-f]{2}/[0-9a-f]{64}\.gz$`)
)

// check says how a record can't be written to the git store as it is: a
// feed or an object that isn't named in its form, or an object that isn't
// its version's (REC-3).
func (r Record) check() error {
	if !feedName.MatchString(r.Feed) {
		return fmt.Errorf("its feed %q isn't a name", r.Feed)
	}
	if r.Object == nil {
		return nil
	}
	if r.SHA256 == nil || r.Bytes == nil || *r.Object != objectPath(*r.SHA256) || !objectName.MatchString(*r.Object) {
		return fmt.Errorf("its object %q isn't its version's", *r.Object)
	}
	return nil
}

// objectPath is the name of the object of a body with the SHA-256 given
// (OBJ-1).
func objectPath(sha256 string) string {
	if len(sha256) < 2 {
		return ""
	}
	return "objects/" + sha256[:2] + "/" + sha256 + ".gz"
}

// Line is the record as a line of its feed's log, as scripts/snapshot.py
// writes one: a JSON object with the fields in the table's order, ", " and
// ": " between them, and characters beyond ASCII as they are, so a log's
// lines read alike whichever program wrote them (REC-9).
func (r Record) Line() string {
	var b strings.Builder
	b.WriteByte('{')
	first := true
	field := func(name, value string) {
		if !first {
			b.WriteString(", ")
		}
		first = false
		b.WriteString(quote(name))
		b.WriteString(": ")
		b.WriteString(value)
	}
	field("feed", quote(r.Feed))
	field("url", quote(r.URL))
	field("requested_at", quote(r.RequestedAt))
	field("received_at", nullable(r.ReceivedAt))
	field("attempts", strconv.Itoa(r.Attempts))
	field("status", nullableInt(r.Status))
	field("error", nullable(r.Error))
	field("final_url", nullable(r.FinalURL))
	field("date", nullable(r.Date))
	field("etag", nullable(r.ETag))
	field("last_modified", nullable(r.LastModified))
	field("content_type", nullable(r.ContentType))
	field("sha256", nullable(r.SHA256))
	if r.Bytes == nil {
		field("bytes", "null")
	} else {
		field("bytes", strconv.FormatInt(*r.Bytes, 10))
	}
	field("object", nullable(r.Object))
	field("tool", quote(r.Tool))
	b.WriteString("}\n")
	return b.String()
}

func nullable(s *string) string {
	if s == nil {
		return "null"
	}
	return quote(*s)
}

func nullableInt(n *int) string {
	if n == nil {
		return "null"
	}
	return strconv.Itoa(*n)
}

// quote gives a string as Python's json.dumps does with ensure_ascii off:
// a quotation mark, a backslash, and each control character escaped, and
// every other character as it is.
func quote(s string) string {
	var b strings.Builder
	b.WriteByte('"')
	for _, c := range s {
		switch c {
		case '"':
			b.WriteString(`\"`)
		case '\\':
			b.WriteString(`\\`)
		case '\n':
			b.WriteString(`\n`)
		case '\r':
			b.WriteString(`\r`)
		case '\t':
			b.WriteString(`\t`)
		case '\b':
			b.WriteString(`\b`)
		case '\f':
			b.WriteString(`\f`)
		default:
			if c < 0x20 {
				fmt.Fprintf(&b, `\u%04x`, c)
			} else {
				b.WriteRune(c)
			}
		}
	}
	b.WriteByte('"')
	return b.String()
}

// parseLine reads a line of a log. A field it doesn't know is ignored, and
// received_at or date, absent from a record written under version 0.2.0, is
// read as null (REC-1, REC-9).
func parseLine(line []byte) (Record, error) {
	var r Record
	err := json.Unmarshal(line, &r)
	return r, err
}
