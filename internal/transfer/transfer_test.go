package transfer

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
)

// The URLs the tests' scope covers, and one it doesn't.
const (
	submissions = "https://data.sec.gov/submissions/CIK0000320193.json"
	filings     = "https://www.sec.gov/Archives/edgar/data/320193/"
	outside     = "https://data.sec.gov/submissions/CIK0000789019.json"
)

// testTool is the tool of the fetcher's records in the tests.
const testTool = "fetcher@0123456789abcdef0123456789abcdef01234567"

// fakeSource is a cluster store that holds the records and objects given.
type fakeSource struct {
	stored  []Stored
	objects map[string][]byte
	reads   int
	fetched []string
}

func (f *fakeSource) Records(_ context.Context, r Range) ([]Stored, error) {
	f.reads++
	var in []Stored
	for _, s := range f.stored {
		at, err := time.Parse(v1alpha1.TimestampLayout, s.Record.RequestedAt)
		if err != nil {
			return nil, err
		}
		if !at.Before(r.From) && at.Before(r.Until) {
			in = append(in, s)
		}
	}
	return in, nil
}

func (f *fakeSource) Object(_ context.Context, name string) ([]byte, error) {
	f.fetched = append(f.fetched, name)
	data, ok := f.objects[name]
	if !ok {
		return nil, fmt.Errorf("no object %s", name)
	}
	return data, nil
}

// object gives a body as the bucket holds it, its object's name, and its
// record's fields.
type object struct {
	name, sha256 string
	bytes        int64
	data         []byte
}

func newObject(t *testing.T, body string) object {
	t.Helper()
	var compressed bytes.Buffer
	w := gzip.NewWriter(&compressed)
	if _, err := w.Write([]byte(body)); err != nil {
		t.Fatal(err)
	}
	if err := w.Close(); err != nil {
		t.Fatal(err)
	}
	sum := sha256.Sum256([]byte(body))
	digest := hex.EncodeToString(sum[:])
	return object{name: objectPath(digest), sha256: digest, bytes: int64(len(body)), data: compressed.Bytes()}
}

func ptr[T any](v T) *T { return &v }

// at is a time in the tests' range, the minutes after its start.
func at(minutes int) string {
	return stamp(start.Add(time.Duration(minutes) * time.Minute))
}

var (
	start     = time.Date(2026, 10, 29, 14, 0, 0, 0, time.UTC)
	testRange = Range{From: start, Until: start.Add(time.Hour)}
)

// ok is a 200 that names a body.
func ok(url, requestedAt string, o object) Record {
	return Record{
		Feed: "submissions", URL: url, RequestedAt: requestedAt, ReceivedAt: ptr(requestedAt), Attempts: 1,
		Status: ptr(200), Date: ptr("Thu, 29 Oct 2026 14:00:00 GMT"), ETag: ptr(`"e1"`),
		ContentType: ptr("application/json"), SHA256: ptr(o.sha256), Bytes: ptr(o.bytes), Object: ptr(o.name),
		Tool: testTool,
	}
}

// notModified is a 304 that names the version its request asked about.
func notModified(url, requestedAt string, o object) Record {
	r := ok(url, requestedAt, o)
	r.Status = ptr(304)
	return r
}

// notFound is a 404, which names nothing.
func notFound(feed, url, requestedAt string) Record {
	return Record{
		Feed: feed, URL: url, RequestedAt: requestedAt, ReceivedAt: ptr(requestedAt), Attempts: 1,
		Status: ptr(404), Tool: testTool,
	}
}

// newStore gives a git store whose scope covers the tests' URLs.
func newStore(t *testing.T) GitStore {
	t.Helper()
	dir := filepath.Join(t.TempDir(), "data")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	write(t, filepath.Join(dir, "scope.json"), `{"urls": [
  {"url": "`+submissions+`", "reason": "One company's filing history."},
  {"url": "`+filings+`", "reason": "That company's filings."}
]}`)
	return GitStore{Dir: dir}
}

func write(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

func read(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		t.Fatal(err)
	}
	return string(data)
}

// snapshot gives every file under a directory, with its content.
func snapshot(t *testing.T, dir string) map[string]string {
	t.Helper()
	files := map[string]string{}
	err := filepath.WalkDir(dir, func(path string, entry os.DirEntry, err error) error {
		if err != nil || entry.IsDir() {
			return err
		}
		files[path] = read(t, path)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	return files
}

func copyAll(t *testing.T, source Source, store GitStore) *Copy {
	t.Helper()
	c, err := Plan(context.Background(), source, store, testRange)
	if err != nil {
		t.Fatal(err)
	}
	if err := c.Write(); err != nil {
		t.Fatal(err)
	}
	return c
}

func lines(records ...Record) string {
	var b strings.Builder
	for _, r := range records {
		b.WriteString(r.Line())
	}
	return b.String()
}

func refusal(t *testing.T, err error) *Refusal {
	t.Helper()
	var r *Refusal
	if !errors.As(err, &r) {
		t.Fatalf("not a refusal: %v", err)
	}
	return r
}

// spec: XFER-1
func TestTheTransferCopiesTheScopesRecordsOfTheRangeWithTheirObjectsAndNothingElse(t *testing.T) {
	store := newStore(t)
	first, other := newObject(t, `{"filings": 1}`), newObject(t, `{"filings": 2}`)
	atStart := ok(submissions, stamp(testRange.From), first)
	inside := notFound("filings", filings+"000032019326000001/", at(10))
	source := &fakeSource{
		stored: []Stored{
			{ID: 1, Record: ok(submissions, stamp(testRange.From.Add(-time.Millisecond)), other)},
			{ID: 2, Record: atStart},
			{ID: 3, Record: ok(outside, at(5), other)},
			{ID: 4, Record: inside},
			{ID: 5, Record: ok(submissions, stamp(testRange.Until), other)},
		},
		objects: map[string][]byte{first.name: first.data, other.name: other.data},
	}

	c := copyAll(t, source, store)

	if got := read(t, filepath.Join(store.Dir, "feeds", "submissions", "versions.jsonl")); got != lines(atStart) {
		t.Errorf("submissions' log:\n%s", got)
	}
	if got := read(t, filepath.Join(store.Dir, "feeds", "filings", "versions.jsonl")); got != lines(inside) {
		t.Errorf("filings' log:\n%s", got)
	}
	if got := read(t, filepath.Join(store.Dir, filepath.FromSlash(first.name))); got != string(first.data) {
		t.Errorf("the object isn't the bucket's")
	}
	if _, err := os.Stat(filepath.Join(store.Dir, filepath.FromSlash(other.name))); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("an object of no record copied was placed: %v", err)
	}
	if c.Read != 3 || c.InScope != 2 || c.Records() != 2 || c.Objects() != 1 {
		t.Errorf("read %d, in scope %d, copied %d records and %d objects", c.Read, c.InScope, c.Records(), c.Objects())
	}
	if len(source.fetched) != 1 {
		t.Errorf("objects read: %v", source.fetched)
	}
}

// spec: XFER-1
func TestAMalformedScopeCopiesNothingAndReadsNothing(t *testing.T) {
	for name, scope := range map[string]*string{
		"missing":                 nil,
		"not JSON":                ptr(`{"urls": [`),
		"not an object":           ptr(`["` + submissions + `"]`),
		"urls not a list":         ptr(`{"urls": "` + submissions + `"}`),
		"an entry without reason": ptr(`{"urls": [{"url": "` + submissions + `"}]}`),
		"an empty reason":         ptr(`{"urls": [{"url": "` + submissions + `", "reason": ""}]}`),
		"a url not a string":      ptr(`{"urls": [{"url": 1, "reason": "A reason."}]}`),
	} {
		t.Run(name, func(t *testing.T) {
			store := newStore(t)
			path := filepath.Join(store.Dir, "scope.json")
			if scope == nil {
				if err := os.Remove(path); err != nil {
					t.Fatal(err)
				}
			} else {
				write(t, path, *scope)
			}
			before := snapshot(t, store.Dir)
			source := &fakeSource{stored: []Stored{{ID: 1, Record: notFound("submissions", submissions, at(1))}}}

			_, err := Plan(context.Background(), source, store, testRange)

			if r := refusal(t, err); !strings.Contains(r.Error(), "scope.json") {
				t.Errorf("the refusal doesn't name scope.json: %v", r)
			}
			if source.reads != 0 {
				t.Errorf("the cluster store was read %d times", source.reads)
			}
			if after := snapshot(t, store.Dir); fmt.Sprint(after) != fmt.Sprint(before) {
				t.Errorf("data/ changed")
			}
		})
	}
}

// sparse makes a file of the size given that takes no room on disk.
func sparse(t *testing.T, path string, size int64) {
	t.Helper()
	write(t, path, "")
	if err := os.Truncate(path, size); err != nil {
		t.Fatal(err)
	}
}

// spec: XFER-1
func TestACopyThatWouldPutAFileOverFiftyMiBCopiesNothingAndSaysSo(t *testing.T) {
	store := newStore(t)
	log := filepath.Join(store.Dir, "feeds", "submissions", "versions.jsonl")
	padded := notFound("submissions", submissions, at(0))
	padded.Status, padded.ReceivedAt, padded.Error = nil, nil, ptr("")
	// An error long enough to bring the log to 10 bytes under the limit.
	padded.Error = ptr(strings.Repeat("x", FileLimit-10-len(padded.Line())))
	write(t, log, padded.Line())
	o := newObject(t, "{}")
	source := &fakeSource{
		stored:  []Stored{{ID: 1, Record: ok(submissions, at(1), o)}},
		objects: map[string][]byte{o.name: o.data},
	}

	_, err := Plan(context.Background(), source, store, testRange)

	r := refusal(t, err)
	if len(r.Reasons) != 1 || !strings.HasPrefix(r.Reasons[0], "SCOPE-4: feeds/submissions/versions.jsonl would be") {
		t.Errorf("reasons: %q", r.Reasons)
	}
	if info, err := os.Stat(log); err != nil || info.Size() != FileLimit-10 {
		t.Errorf("the log changed: %v", err)
	}
	if _, err := os.Stat(filepath.Join(store.Dir, filepath.FromSlash(o.name))); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("an object was placed")
	}
}

// spec: XFER-1
func TestACopyThatWouldPutDataOverOneGBCopiesNothingAndSaysSo(t *testing.T) {
	store := newStore(t)
	sizes, err := store.Sizes()
	if err != nil {
		t.Fatal(err)
	}
	var total int64
	for _, size := range sizes {
		total += size
	}
	for i := 0; i < 19; i++ {
		sparse(t, filepath.Join(store.Dir, "objects", fmt.Sprintf("%02x", i), "filler"), FileLimit)
		total += FileLimit
	}
	sparse(t, filepath.Join(store.Dir, "objects", "zz", "filler"), TotalLimit-total-5)
	source := &fakeSource{stored: []Stored{{ID: 1, Record: notFound("submissions", submissions, at(1))}}}

	_, err = Plan(context.Background(), source, store, testRange)

	r := refusal(t, err)
	if len(r.Reasons) != 1 || !strings.HasPrefix(r.Reasons[0], "SCOPE-5: data/'s files would total") {
		t.Errorf("reasons: %q", r.Reasons)
	}
}

// recorder writes down what a copy writes, in order.
type recorder struct {
	writes []string
}

func (r *recorder) PlaceObject(name string, _ []byte) error {
	r.writes = append(r.writes, "object "+name)
	return nil
}

func (r *recorder) Append(feed, line string) error {
	r.writes = append(r.writes, feed+" "+line)
	return nil
}

// spec: XFER-2
func TestEachRecordIsAppendedAfterItsObjectInTheOrderOfTheirIDs(t *testing.T) {
	store := newStore(t)
	first, second, held := newObject(t, "1"), newObject(t, "2"), newObject(t, "held")
	write(t, filepath.Join(store.Dir, filepath.FromSlash(held.name)), string(held.data))
	records := []Record{
		ok(submissions, at(3), first),
		notFound("filings", filings+"x/", at(1)),
		notModified(submissions, at(4), first),
		ok(submissions, at(2), second),
		ok(submissions, at(5), held),
	}
	var stored []Stored
	for i, r := range records {
		stored = append(stored, Stored{ID: int64(10 + i), Record: r})
	}
	source := &fakeSource{stored: stored, objects: map[string][]byte{first.name: first.data, second.name: second.data}}
	c, err := Plan(context.Background(), source, store, testRange)
	if err != nil {
		t.Fatal(err)
	}
	w := &recorder{}

	if err := c.writeTo(w); err != nil {
		t.Fatal(err)
	}

	want := []string{
		"object " + first.name,
		"submissions " + records[0].Line(),
		"filings " + records[1].Line(),
		"submissions " + records[2].Line(),
		"object " + second.name,
		"submissions " + records[3].Line(),
		"submissions " + records[4].Line(),
	}
	if strings.Join(w.writes, "") != strings.Join(want, "") {
		t.Errorf("writes:\n%s", strings.Join(w.writes, "\n"))
	}
	if strings.Contains(strings.Join(source.fetched, " "), held.name) {
		t.Errorf("an object data/ holds was read: %v", source.fetched)
	}
}

// spec: XFER-2
func TestARecordIsWrittenAsSnapshotPyWritesOneWithEveryFieldInTheTablesOrder(t *testing.T) {
	full := Record{
		Feed: "submissions", URL: submissions, RequestedAt: "2026-10-29T14:00:00.123Z",
		ReceivedAt: ptr("2026-10-29T14:00:00.456Z"), Attempts: 2, Status: ptr(200),
		FinalURL: ptr(submissions + "?x=1&y=<2>"), Date: ptr("Thu, 29 Oct 2026 14:00:00 GMT"),
		ETag: ptr(`"a\b"`), LastModified: ptr("Thu,\t29 Oct\r\n\x01\x7f\b\f"),
		ContentType: ptr("application/json; charset=utf-8 é \U0001F600"),
		SHA256:      ptr("ab" + strings.Repeat("0", 62)), Bytes: ptr(int64(1234)),
		Object: ptr("objects/ab/ab" + strings.Repeat("0", 62) + ".gz"), Tool: testTool,
	}
	// As Python's json.dumps(record, ensure_ascii=False) gives it.
	want := `{"feed": "submissions", "url": "https://data.sec.gov/submissions/CIK0000320193.json", ` +
		`"requested_at": "2026-10-29T14:00:00.123Z", "received_at": "2026-10-29T14:00:00.456Z", ` +
		`"attempts": 2, "status": 200, "error": null, ` +
		`"final_url": "https://data.sec.gov/submissions/CIK0000320193.json?x=1&y=<2>", ` +
		`"date": "Thu, 29 Oct 2026 14:00:00 GMT", "etag": "\"a\\b\"", ` +
		`"last_modified": "Thu,\t29 Oct\r\n\u0001` + "\x7f" + `\b\f", ` +
		`"content_type": "application/json; charset=utf-8 é` + " \U0001F600" + `", ` +
		`"sha256": "ab00000000000000000000000000000000000000000000000000000000000000", "bytes": 1234, ` +
		`"object": "objects/ab/ab00000000000000000000000000000000000000000000000000000000000000.gz", ` +
		`"tool": "fetcher@0123456789abcdef0123456789abcdef01234567"}` + "\n"
	empty := Record{
		Feed: "submissions", URL: submissions, RequestedAt: "2026-10-29T14:00:00.000Z", Attempts: 3,
		Error: ptr("no response: connection reset"), Tool: testTool,
	}
	wantEmpty := `{"feed": "submissions", "url": "https://data.sec.gov/submissions/CIK0000320193.json", ` +
		`"requested_at": "2026-10-29T14:00:00.000Z", "received_at": null, "attempts": 3, "status": null, ` +
		`"error": "no response: connection reset", "final_url": null, "date": null, "etag": null, ` +
		`"last_modified": null, "content_type": null, "sha256": null, "bytes": null, "object": null, ` +
		`"tool": "fetcher@0123456789abcdef0123456789abcdef01234567"}` + "\n"

	for _, c := range []struct {
		record Record
		want   string
	}{{full, want}, {empty, wantEmpty}} {
		if got := c.record.Line(); got != c.want {
			t.Errorf("line:\n%s\nnot:\n%s", got, c.want)
		}
		if again, err := parseLine([]byte(c.want)); err != nil || again.Line() != c.want {
			t.Errorf("read back: %v", err)
		}
	}
}

// spec: XFER-2
func TestATimeIsCopiedInTheStoresFormToTheMillisecond(t *testing.T) {
	if got := stamp(time.Date(2026, 10, 29, 9, 4, 5, 120_000_000, time.FixedZone("EST", -5*3600))); got !=
		"2026-10-29T14:04:05.120Z" {
		t.Errorf("stamp: %s", got)
	}
}

// spec: XFER-3
func TestARangeCopiedTwiceAddsNothingTheSecondTime(t *testing.T) {
	store := newStore(t)
	o := newObject(t, "body")
	source := &fakeSource{
		stored: []Stored{
			{ID: 1, Record: ok(submissions, at(1), o)},
			{ID: 2, Record: notModified(submissions, at(2), o)},
		},
		objects: map[string][]byte{o.name: o.data},
	}
	copyAll(t, source, store)
	before := snapshot(t, store.Dir)

	again := copyAll(t, source, store)

	if again.Held != 2 || again.Records() != 0 || again.Objects() != 0 {
		t.Errorf("held %d, copied %d records and %d objects", again.Held, again.Records(), again.Objects())
	}
	if after := snapshot(t, store.Dir); fmt.Sprint(after) != fmt.Sprint(before) {
		t.Errorf("data/ changed")
	}
}

// spec: XFER-3
func TestARecordThatConflictsWithOneDataHoldsCopiesNothingAndSaysWhich(t *testing.T) {
	store := newStore(t)
	o := newObject(t, "body")
	heldRecord := notFound("submissions", submissions, at(2))
	write(t, filepath.Join(store.Dir, "feeds", "submissions", "versions.jsonl"),
		notFound("submissions", submissions, at(1)).Line()+heldRecord.Line())
	different := heldRecord
	different.Status = ptr(410)
	before := snapshot(t, store.Dir)
	source := &fakeSource{
		stored:  []Stored{{ID: 6, Record: ok(submissions, at(0), o)}, {ID: 7, Record: different}},
		objects: map[string][]byte{o.name: o.data},
	}

	_, err := Plan(context.Background(), source, store, testRange)

	r := refusal(t, err)
	if len(r.Reasons) != 1 || !strings.Contains(r.Reasons[0], "record 7 and feeds/submissions/versions.jsonl, line 2") {
		t.Errorf("reasons: %q", r.Reasons)
	}
	if after := snapshot(t, store.Dir); fmt.Sprint(after) != fmt.Sprint(before) {
		t.Errorf("data/ changed")
	}
}

// spec: XFER-3
func TestTwoRecordsOfTheRangeThatConflictCopyNothingAndSaySo(t *testing.T) {
	store := newStore(t)
	first := notFound("submissions", submissions, at(1))
	second := first
	second.Attempts = 2
	source := &fakeSource{stored: []Stored{
		{ID: 3, Record: notFound("submissions", submissions, at(0))},
		{ID: 4, Record: first},
		{ID: 5, Record: second},
	}}

	_, err := Plan(context.Background(), source, store, testRange)

	r := refusal(t, err)
	if len(r.Reasons) != 1 || !strings.Contains(r.Reasons[0], "records 4 and 5") {
		t.Errorf("reasons: %q", r.Reasons)
	}
	if _, err := os.Stat(filepath.Join(store.Dir, "feeds")); !errors.Is(err, os.ErrNotExist) {
		t.Errorf("a log was written")
	}
}

// spec: XFER-3
func TestARecordEqualToOneDataHoldsIsSkippedAndTheOthersCopied(t *testing.T) {
	store := newStore(t)
	held := notFound("submissions", submissions, at(1))
	log := filepath.Join(store.Dir, "feeds", "submissions", "versions.jsonl")
	write(t, log, held.Line())
	added := notFound("submissions", submissions, at(2))
	source := &fakeSource{stored: []Stored{{ID: 1, Record: held}, {ID: 2, Record: added}}}

	c := copyAll(t, source, store)

	if got := read(t, log); got != lines(held, added) {
		t.Errorf("log:\n%s", got)
	}
	if c.Held != 1 || c.Records() != 1 {
		t.Errorf("held %d, copied %d", c.Held, c.Records())
	}
}

// spec: XFER-3
func TestARecordWrittenUnderVersion020IsEqualToOneWithItsTimesNull(t *testing.T) {
	store := newStore(t)
	record := notFound("submissions", submissions, at(1))
	record.ReceivedAt, record.Status, record.Error = nil, nil, ptr("no response")
	old := strings.Replace(strings.Replace(record.Line(), `"received_at": null, `, "", 1), `"date": null, `, "", 1)
	log := filepath.Join(store.Dir, "feeds", "submissions", "versions.jsonl")
	write(t, log, old)
	source := &fakeSource{stored: []Stored{{ID: 1, Record: record}}}

	c := copyAll(t, source, store)

	if c.Held != 1 || read(t, log) != old {
		t.Errorf("held %d; log:\n%s", c.Held, read(t, log))
	}
}

func TestAnObjectThatIsntTheBodyItsNameGivesCopiesNothing(t *testing.T) {
	store := newStore(t)
	o, other := newObject(t, "body"), newObject(t, "another body")
	for name, data := range map[string][]byte{
		"another body":   other.data,
		"not gzip":       []byte("body"),
		"cut short":      o.data[:len(o.data)-4],
		"another length": o.data,
	} {
		t.Run(name, func(t *testing.T) {
			record := ok(submissions, at(1), o)
			if name == "another length" {
				record.Bytes = ptr(o.bytes + 1)
			}
			source := &fakeSource{stored: []Stored{{ID: 1, Record: record}}, objects: map[string][]byte{o.name: data}}

			_, err := Plan(context.Background(), source, store, testRange)

			if err == nil || !strings.Contains(err.Error(), o.name) {
				t.Errorf("error: %v", err)
			}
			if _, err := os.Stat(filepath.Join(store.Dir, "objects")); !errors.Is(err, os.ErrNotExist) {
				t.Errorf("an object was placed")
			}
		})
	}
}

func TestARecordWhoseFeedOrObjectIsntNamedInItsFormIsntCopied(t *testing.T) {
	o := newObject(t, "body")
	badFeed := ok(submissions, at(1), o)
	badFeed.Feed = "../feeds"
	badObject := ok(submissions, at(1), o)
	badObject.Object = ptr("../" + o.name)
	for _, record := range []Record{badFeed, badObject} {
		store := newStore(t)
		source := &fakeSource{stored: []Stored{{ID: 1, Record: record}}, objects: map[string][]byte{o.name: o.data}}

		if _, err := Plan(context.Background(), source, store, testRange); err == nil {
			t.Errorf("planned %s", record.Line())
		}
	}
}

// spec: TRANSFER-6
func TestNothingIsCopiedFromAClusterThatHoldsTheFakeEDGARThoughTheRangeIsRead(t *testing.T) {
	store := newStore(t)
	o := newObject(t, "body")
	source := &fakeSource{
		stored:  []Stored{{ID: 1, Record: ok(submissions, at(1), o)}},
		objects: map[string][]byte{o.name: o.data},
	}
	before := snapshot(t, store.Dir)
	var out bytes.Buffer

	err := copyRange(context.Background(), source, store, testRange, true, &out)

	if r := refusal(t, err); !strings.Contains(r.Error(), "fake EDGAR is installed") {
		t.Errorf("refusal: %v", r)
	}
	if source.reads != 1 || len(source.fetched) != 1 {
		t.Errorf("read the range %d times and %d objects", source.reads, len(source.fetched))
	}
	if !strings.Contains(out.String(), "To copy: 1 records, with 1 objects") {
		t.Errorf("output:\n%s", out.String())
	}
	if after := snapshot(t, store.Dir); fmt.Sprint(after) != fmt.Sprint(before) {
		t.Errorf("data/ changed")
	}
}

func TestACopyFromAClusterWithoutTheFakeEDGARIsWrittenAndReported(t *testing.T) {
	store := newStore(t)
	source := &fakeSource{stored: []Stored{{ID: 1, Record: notFound("submissions", submissions, at(1))}}}
	var out bytes.Buffer

	if err := copyRange(context.Background(), source, store, testRange, false, &out); err != nil {
		t.Fatal(err)
	}

	if !strings.Contains(out.String(), "Copied 1 records, with 0 objects") {
		t.Errorf("output:\n%s", out.String())
	}
	if read(t, filepath.Join(store.Dir, "feeds", "submissions", "versions.jsonl")) == "" {
		t.Errorf("nothing was written")
	}
}
