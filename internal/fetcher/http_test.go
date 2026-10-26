package fetcher

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"slices"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const (
	submissions = "https://data.sec.gov/submissions/CIK0000320193.json"
	appleBody   = `{"cik": "0000320193", "name": "Apple Inc."}`
)

// encoded gives data with a gzip encoding, as a server sends it.
func encoded(t *testing.T, data string) []byte {
	t.Helper()
	var out bytes.Buffer
	writer := gzip.NewWriter(&out)
	if _, err := writer.Write([]byte(data)); err != nil {
		t.Fatal(err)
	}
	if err := writer.Close(); err != nil {
		t.Fatal(err)
	}
	return out.Bytes()
}

func digest(data string) string {
	sum := sha256.Sum256([]byte(data))
	return hex.EncodeToString(sum[:])
}

// serves answers each request with the body, and its validators.
func serves(content string) http.HandlerFunc {
	return func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("ETag", `"v1"`)
		w.Header().Set("Last-Modified", "Mon, 05 Oct 2026 10:00:00 GMT")
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, content)
	}
}

func fetchOnce(t *testing.T, c *Client, url string, previous *Version) Result {
	t.Helper()
	result, err := c.Fetch(context.Background(), "submissions", url, Tool(testCommit), previous, nil)
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func value[T any](p *T) any {
	if p == nil {
		return nil
	}
	return *p
}

// noBody checks that a record names no body, and that nothing's kept.
func noBody(t *testing.T, result Result) {
	t.Helper()
	if r := result.Record; r.SHA256 != nil || r.Bytes != nil || r.Object != nil || result.Object != nil {
		t.Errorf("it kept a body: sha256 %v, bytes %v, object %v", value(r.SHA256), value(r.Bytes), value(r.Object))
	}
}

// names checks that a record names the body given, and that its object
// holds it (OBJ-1, REC-3, REC-4, REC-8).
func names(t *testing.T, result Result, content string) {
	t.Helper()
	r := result.Record
	if r.SHA256 == nil || *r.SHA256 != digest(content) {
		t.Fatalf("its sha256 is %v, not the body's", value(r.SHA256))
	}
	if value(r.Bytes) != int64(len(content)) || value(r.Object) != ObjectName(digest(content)) {
		t.Errorf("bytes %v, object %v", value(r.Bytes), value(r.Object))
	}
	reader, err := gzip.NewReader(bytes.NewReader(result.Object))
	if err != nil {
		t.Fatal(err)
	}
	if stored, err := io.ReadAll(reader); err != nil || string(stored) != content {
		t.Errorf("the object holds %q: %v", stored, err)
	}
	if r.Error != nil {
		t.Errorf("its error is %q", *r.Error)
	}
}

// spec: FETCH-2, FETCH-3, ID-1, REC-10
func TestEachRequestCarriesTheUserAgentAndAsksForGzip(t *testing.T) {
	s := newSECServer(t, serves(appleBody))
	result := fetchOnce(t, NewClient(s.options()), submissions, nil)

	seen := s.seen()
	if len(seen) != 1 {
		t.Fatalf("%d requests", len(seen))
	}
	if got := seen[0].header.Get("User-Agent"); got != testUserAgent {
		t.Errorf("User-Agent: %q", got)
	}
	if got := seen[0].header.Values("Accept-Encoding"); len(got) != 1 || got[0] != "gzip" {
		t.Errorf("Accept-Encoding: %q", got)
	}
	if seen[0].url != submissions {
		t.Errorf("it asked for %s", seen[0].url)
	}
	names(t, result, appleBody)
	r := result.Record
	if r.Feed != "submissions" || r.URL != submissions || r.Attempts != 1 || value(r.Status) != 200 ||
		r.Tool != "fetcher@"+testCommit || r.FinalURL != nil {
		t.Errorf("the record: %+v", r)
	}
	if value(r.ETag) != `"v1"` || value(r.LastModified) != "Mon, 05 Oct 2026 10:00:00 GMT" ||
		value(r.ContentType) != "application/json" || r.Date == nil || r.ReceivedAt == nil {
		t.Errorf("the record's headers and times: %+v", r)
	}
	// The record has every field of the store's table, in its order, and no
	// request header (REC-9, REC-10, ID-1).
	data, err := json.Marshal(r)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(data), testUserAgent) {
		t.Errorf("the record holds the user agent: %s", data)
	}
	fields := []string{"feed", "url", "requested_at", "received_at", "attempts", "status", "error", "final_url",
		"date", "etag", "last_modified", "content_type", "sha256", "bytes", "object", "tool"}
	at := 0
	for _, field := range fields {
		next := strings.Index(string(data), `"`+field+`":`)
		if next < at {
			t.Fatalf("%s isn't in the record in the table's order: %s", field, data)
		}
		at = next
	}
}

// spec: FETCH-1
func TestOnlyHTTPSURLsOnTheSECsHostsAreFetched(t *testing.T) {
	s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/elsewhere":
			http.Redirect(w, r, "https://example.com/data", http.StatusFound)
		case "/plain":
			http.Redirect(w, r, "http://www.sec.gov/final", http.StatusFound)
		case "/redirected":
			http.Redirect(w, r, "https://www.sec.gov/final", http.StatusMovedPermanently)
		default:
			_, _ = io.WriteString(w, appleBody)
		}
	})
	c := NewClient(s.options())

	for _, url := range []string{
		"http://www.sec.gov/final",
		"https://example.com/final",
		"https://www.sec.gov.example.com/final",
		"https://someone@www.sec.gov/final",
		"https://www.sec.gov:8443/final",
		"https://www.sec.gov/%ZZ",
	} {
		if _, err := c.Fetch(context.Background(), "submissions", url, Tool(testCommit), nil, nil); !errors.Is(
			err, ErrNotFetchable) {
			t.Errorf("%s: %v", url, err)
		}
	}
	if seen := s.seen(); len(seen) != 0 {
		t.Fatalf("%d requests for URLs it may not fetch", len(seen))
	}

	t.Run("a redirect elsewhere isn't followed", func(t *testing.T) {
		for _, url := range []string{"https://www.sec.gov/elsewhere", "https://www.sec.gov/plain"} {
			result := fetchOnce(t, c, url, nil)
			if value(result.Record.Status) != http.StatusFound || result.Record.FinalURL != nil {
				t.Errorf("%s: %+v", url, result.Record)
			}
			noBody(t, result)
		}
		if seen := s.seen(); len(seen) != 2 {
			t.Errorf("%d requests, for 2 URLs", len(seen))
		}
	})
	t.Run("a redirect to the SEC's hosts is followed, within the rules", func(t *testing.T) {
		before := len(s.seen())
		result := fetchOnce(t, c, "https://www.sec.gov/redirected", nil)
		names(t, result, appleBody)
		if value(result.Record.FinalURL) != "https://www.sec.gov/final" {
			t.Errorf("final_url: %v", value(result.Record.FinalURL))
		}
		seen := s.seen()[before:]
		if len(seen) != 2 || seen[1].url != "https://www.sec.gov/final" {
			t.Fatalf("the requests: %+v", seen)
		}
		for _, r := range seen {
			if r.header.Get("User-Agent") != testUserAgent || r.header.Get("Accept-Encoding") != "gzip" {
				t.Errorf("%s: %v", r.url, r.header)
			}
		}
	})
}

// spec: FETCH-4, REC-5
func TestTheLatestVersionsValidatorsAreSentAndA304NamesThatVersion(t *testing.T) {
	s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("If-None-Match") == `"v1"` || r.Header.Get("If-Modified-Since") != "" {
			w.Header().Set("ETag", `"v1"`)
			w.WriteHeader(http.StatusNotModified)
			return
		}
		serves(appleBody)(w, r)
	})
	c := NewClient(s.options())
	previous := &Version{ETag: ptr(`"v1"`), LastModified: ptr("Mon, 05 Oct 2026 10:00:00 GMT"),
		SHA256: digest(appleBody), Bytes: int64(len(appleBody)), Object: ObjectName(digest(appleBody))}

	result := fetchOnce(t, c, submissions, previous)

	sent := s.seen()[0].header
	if sent.Get("If-None-Match") != `"v1"` || sent.Get("If-Modified-Since") != "Mon, 05 Oct 2026 10:00:00 GMT" {
		t.Errorf("the validators sent: %v", sent)
	}
	r := result.Record
	if value(r.Status) != http.StatusNotModified || r.Error != nil || result.Object != nil {
		t.Fatalf("the record: %+v", r)
	}
	if value(r.SHA256) != previous.SHA256 || value(r.Bytes) != previous.Bytes || value(r.Object) != previous.Object {
		t.Errorf("it names %v, not the version asked about", value(r.SHA256))
	}
	// The 304 gave an ETag but no Last-Modified, which the version gives.
	if value(r.ETag) != `"v1"` || value(r.LastModified) != *previous.LastModified {
		t.Errorf("the validators recorded: %v, %v", value(r.ETag), value(r.LastModified))
	}

	t.Run("with only one validator, only it is sent", func(t *testing.T) {
		before := len(s.seen())
		fetchOnce(t, c, submissions, &Version{ETag: ptr(`"v2"`), SHA256: previous.SHA256, Bytes: previous.Bytes,
			Object: previous.Object})
		sent := s.seen()[before].header
		if sent.Get("If-None-Match") != `"v2"` || len(sent.Values("If-Modified-Since")) != 0 {
			t.Errorf("the validators sent: %v", sent)
		}
	})
	t.Run("a 304 to a request without validators names nothing", func(t *testing.T) {
		s.handle(func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNotModified) })
		for _, previous := range []*Version{nil, {SHA256: previous.SHA256, Bytes: 1, Object: previous.Object}} {
			before := len(s.seen())
			result := fetchOnce(t, c, submissions, previous)
			sent := s.seen()[before].header
			if len(sent.Values("If-None-Match")) != 0 || len(sent.Values("If-Modified-Since")) != 0 {
				t.Errorf("validators were sent: %v", sent)
			}
			noBody(t, result)
			if result.Record.Error == nil {
				t.Error("a 304 that names nothing has no error")
			}
		}
	})
}

// spec: FETCH-5
func TestAnAttemptWithoutAResponseInTimeHasNone(t *testing.T) {
	stall := make(chan struct{})
	t.Cleanup(func() { close(stall) })
	s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/body" {
			w.Header().Set("Content-Length", "100")
			_, _ = io.WriteString(w, "the first bytes")
			w.(http.Flusher).Flush()
		}
		select {
		case <-stall:
		case <-r.Context().Done():
		}
	})
	opts := s.options()
	opts.Timeout = 300 * time.Millisecond
	opts.Attempts = 1
	c := NewClient(opts)

	started := time.Now()
	result := fetchOnce(t, c, "https://www.sec.gov/headers", nil)
	if took := time.Since(started); took < opts.Timeout || took > 5*opts.Timeout {
		t.Errorf("it gave up after %s", took)
	}
	if r := result.Record; r.Status != nil || r.ReceivedAt != nil || r.Error == nil ||
		!strings.HasPrefix(*r.Error, "no response") {
		t.Errorf("the record: %+v", r)
	}
	noBody(t, result)

	// A body that stalls as long is cut short (FETCH-13).
	result = fetchOnce(t, c, "https://www.sec.gov/body", nil)
	if r := result.Record; value(r.Status) != 200 || r.Error == nil || !strings.HasPrefix(*r.Error, "body cut short") {
		t.Errorf("the record: %+v", r)
	}
	noBody(t, result)
}

// spec: FETCH-6
func TestRequestsStartAtLeastAFifthOfASecondApart(t *testing.T) {
	var n atomic.Int32
	s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
		// Every other request fails, and is retried at once.
		if n.Add(1)%2 == 1 {
			w.Header().Set("Retry-After", "0")
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		serves(appleBody)(w, r)
	})
	opts := s.options()
	opts.MinInterval = 0
	c := NewClient(opts)
	for range 4 {
		fetchOnce(t, c, submissions, nil)
	}

	seen := s.seen()
	if len(seen) != 8 {
		t.Fatalf("%d requests", len(seen))
	}
	for i := 1; i < len(seen); i++ {
		// Each is written MinInterval or more after the one before; the
		// server reads it a little later, but sooner than the one before
		// by a few microseconds at most.
		if gap := seen[i].at.Sub(seen[i-1].at); gap < MinInterval-2*time.Millisecond {
			t.Errorf("requests %d and %d arrived %s apart", i-1, i, gap)
		}
	}
}

// spec: FETCH-6
func TestARequestWrittenLateLendsTheNextNoneOfItsTime(t *testing.T) {
	s := newSECServer(t, serves(appleBody))
	opts := s.options()
	opts.MinInterval = 0
	// While late is set, a request goes out 150 milliseconds after it may
	// start, as when a goroutine resumes late on a busy node: here, as
	// Permit is asked whether it may be sent.
	var late atomic.Bool
	opts.Permit = func() error {
		if late.Load() {
			time.Sleep(150 * time.Millisecond)
		}
		return nil
	}
	c := NewClient(opts)
	fetchOnce(t, c, submissions, nil)
	late.Store(true)
	fetchOnce(t, c, submissions, nil)
	late.Store(false)
	// The third follows the second's response at once, and still starts
	// MinInterval after the second did, not after the second's turn came.
	fetchOnce(t, c, submissions, nil)

	seen := s.seen()
	if len(seen) != 3 {
		t.Fatalf("%d requests", len(seen))
	}
	for i := 1; i < len(seen); i++ {
		if gap := seen[i].at.Sub(seen[i-1].at); gap < MinInterval-2*time.Millisecond {
			t.Errorf("requests %d and %d arrived %s apart", i-1, i, gap)
		}
	}
}

// spec: FETCH-6
func TestAWaitThatEndsLateLendsTheNextRequestNoneOfItsTime(t *testing.T) {
	c := NewClient(ClientOptions{UserAgent: testUserAgent})
	// The Client's clock is the test's own, and each of its waits ends as
	// late as the test has it, as a goroutine's can on a busy node.
	now := time.Date(2026, 10, 26, 12, 0, 0, 0, time.UTC)
	begin := now
	var late time.Duration
	c.now = func() time.Time { return now }
	c.pause = func(_ context.Context, d time.Duration) error {
		now = now.Add(d + late)
		return nil
	}
	// start has a request, asked for after the time given, start, and says
	// when it did.
	start := func(after time.Duration) time.Duration {
		t.Helper()
		now = now.Add(after)
		if err := c.start(&attempt{ctx: context.Background()}); err != nil {
			t.Fatal(err)
		}
		return now.Sub(begin)
	}

	first := start(0)
	// The second, asked for 10 milliseconds on, may start at 200, but its
	// wait ends at 350.
	late = 150 * time.Millisecond
	second := start(10 * time.Millisecond)
	if second != 350*time.Millisecond {
		t.Fatalf("the second started at %s", second)
	}
	// The third, asked for at 400, after a quick response, waits until 550.
	late = 0
	third := start(50 * time.Millisecond)
	for _, gap := range []time.Duration{second - first, third - second} {
		if gap < MinInterval {
			t.Errorf("requests started at %s, %s, and %s", first, second, third)
		}
	}
}

// failure is a way an attempt fails that's retried (FETCH-8).
type failure struct {
	name string
	fail http.HandlerFunc
}

func failures(t *testing.T) []failure {
	var found []failure
	for _, status := range []int{429, 500, 502, 503, 504} {
		found = append(found, failure{fmt.Sprintf("a %d", status), func(w http.ResponseWriter, _ *http.Request) {
			w.WriteHeader(status)
		}})
	}
	return append(found,
		failure{"no response", func(w http.ResponseWriter, _ *http.Request) {
			conn, _, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			_ = conn.Close()
		}},
		failure{"a body cut short", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Length", fmt.Sprint(len(appleBody)+10))
			_, _ = io.WriteString(w, appleBody)
		}},
	)
}

// spec: FETCH-8, FETCH-14
func TestTemporaryFailuresAreRetriedUpToFourAttemptsInAll(t *testing.T) {
	for _, f := range failures(t) {
		t.Run(f.name, func(t *testing.T) {
			var n atomic.Int32
			failing := atomic.Int32{}
			failing.Store(3)
			s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
				if n.Add(1) <= failing.Load() {
					f.fail(w, r)
					return
				}
				serves(appleBody)(w, r)
			})
			opts := s.options()
			opts.BackoffUnit = time.Millisecond
			c := NewClient(opts)

			result := fetchOnce(t, c, submissions, nil)
			if len(s.seen()) != 4 || result.Record.Attempts != 4 {
				t.Fatalf("%d requests, %d attempts", len(s.seen()), result.Record.Attempts)
			}
			names(t, result, appleBody)

			// One that fails each time ends after 4 attempts, with one
			// record, of the last (FETCH-14).
			n.Store(0)
			failing.Store(100)
			before := len(s.seen())
			result = fetchOnce(t, c, submissions, nil)
			if sent := len(s.seen()) - before; sent != 4 || result.Record.Attempts != 4 {
				t.Fatalf("%d requests, %d attempts", sent, result.Record.Attempts)
			}
			noBody(t, result)
			if result.Record.Status == nil && result.Record.Error == nil {
				t.Errorf("the record: %+v", result.Record)
			}
		})
	}
}

// spec: FETCH-8
func TestTheWaitBeforeARetryIsItsRetryAfterOrElseAPowerOfTwoCappedAtAMinute(t *testing.T) {
	now := time.Date(2026, 10, 25, 12, 0, 0, 0, time.UTC)
	for _, c := range []struct {
		retryAfter string
		attempt    int
		want       time.Duration
	}{
		{"3", 1, 3 * time.Second},
		{" 1.5 ", 1, 1500 * time.Millisecond},
		{"0", 3, 0},
		{"120", 1, time.Minute},
		{"-5", 1, 0},
		{"inf", 1, time.Minute},
		{"Sun, 25 Oct 2026 12:00:10 GMT", 1, 10 * time.Second},
		{"Sun, 25 Oct 2026 12:05:00 GMT", 1, time.Minute},
		{"Sun, 25 Oct 2026 11:59:00 GMT", 1, 0},
		{"", 1, 2 * time.Second},
		{"", 2, 4 * time.Second},
		{"", 3, 8 * time.Second},
		{"soon", 3, 8 * time.Second},
		{"nan", 2, 4 * time.Second},
		{"", 6, time.Minute},
	} {
		if got := retryDelay(c.retryAfter, c.attempt, now, time.Second); got != c.want {
			t.Errorf("Retry-After %q after attempt %d: %s, not %s", c.retryAfter, c.attempt, got, c.want)
		}
	}

	// A fetch waits as long.
	var n atomic.Int32
	s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
		if n.Add(1) == 1 {
			w.Header().Set("Retry-After", "1")
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		serves(appleBody)(w, r)
	})
	fetchOnce(t, NewClient(s.options()), submissions, nil)
	seen := s.seen()
	if gap := seen[1].at.Sub(seen[0].at); gap < time.Second || gap > 2*time.Second {
		t.Errorf("it waited %s, after a Retry-After of 1", gap)
	}
}

// spec: FETCH-9, FETCH-10
func TestA403AndAnyOtherStatusAreRecordedWithoutARetry(t *testing.T) {
	for _, status := range []int{403, 400, 404, 410, 301, 501} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			s := newSECServer(t, func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(status) })
			result := fetchOnce(t, NewClient(s.options()), submissions, nil)
			if len(s.seen()) != 1 || result.Record.Attempts != 1 || value(result.Record.Status) != status {
				t.Fatalf("%d requests: %+v", len(s.seen()), result.Record)
			}
			noBody(t, result)
			if result.Record.Error != nil {
				t.Errorf("its error is %q", *result.Record.Error)
			}
		})
	}
}

// spec: FETCH-11, REC-4, OBJ-1
func TestAGzipEncodingIsDecodedBeforeHashingAndNoEncodingIsKeptAsReceived(t *testing.T) {
	for _, encoding := range []string{"gzip", "x-gzip", " GZIP ", "identity", ""} {
		t.Run(fmt.Sprintf("%q", encoding), func(t *testing.T) {
			s := newSECServer(t, func(w http.ResponseWriter, _ *http.Request) {
				sent := []byte(appleBody)
				if strings.Contains(strings.ToLower(encoding), "gzip") {
					sent = encoded(t, appleBody)
				}
				if encoding != "" {
					w.Header().Set("Content-Encoding", encoding)
				}
				w.Header().Set("Content-Length", fmt.Sprint(len(sent)))
				_, _ = w.Write(sent)
			})
			names(t, fetchOnce(t, NewClient(s.options()), submissions, nil), appleBody)
		})
	}
}

// spec: FETCH-12
func TestAnyOtherEncodingIsRecordedWithAnErrorAndNothingKept(t *testing.T) {
	s := newSECServer(t, func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Encoding", "br")
		_, _ = io.WriteString(w, "not decoded")
	})
	result := fetchOnce(t, NewClient(s.options()), submissions, nil)

	if len(s.seen()) != 1 || value(result.Record.Status) != 200 || value(result.Record.Error) !=
		"unsupported Content-Encoding: br" {
		t.Fatalf("%d requests: %+v", len(s.seen()), result.Record)
	}
	noBody(t, result)
}

// spec: FETCH-13, FETCH-8
func TestABodyCutShortKeepsNothingAndIsRetried(t *testing.T) {
	whole := encoded(t, appleBody)
	for _, c := range []struct {
		name string
		send http.HandlerFunc
	}{
		{"fewer bytes than its Content-Length", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Length", fmt.Sprint(len(appleBody)*2))
			_, _ = io.WriteString(w, appleBody)
		}},
		{"a connection that fails mid-stream", func(w http.ResponseWriter, _ *http.Request) {
			conn, buf, err := w.(http.Hijacker).Hijack()
			if err != nil {
				t.Error(err)
				return
			}
			_, _ = buf.WriteString("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n10\r\nthe first bytes!\r\n")
			_ = buf.Flush()
			_ = conn.Close()
		}},
		{"a gzip encoding that ends early", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Encoding", "gzip")
			_, _ = w.Write(whole[:len(whole)/2])
		}},
		{"a gzip encoding that doesn't decode", func(w http.ResponseWriter, _ *http.Request) {
			w.Header().Set("Content-Encoding", "gzip")
			_, _ = io.WriteString(w, "this isn't gzip data")
		}},
	} {
		t.Run(c.name, func(t *testing.T) {
			var n atomic.Int32
			s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
				if n.Add(1) == 1 {
					serves(appleBody)(w, r)
					return
				}
				c.send(w, r)
			})
			opts := s.options()
			opts.BackoffUnit = time.Millisecond
			client := NewClient(opts)
			fetchOnce(t, client, submissions, nil)

			result := fetchOnce(t, client, submissions, nil)
			if sent := len(s.seen()) - 1; sent != 4 || result.Record.Attempts != 4 {
				t.Fatalf("%d requests, %d attempts", sent, result.Record.Attempts)
			}
			r := result.Record
			if value(r.Status) != 200 || r.Error == nil {
				t.Errorf("the record: %+v", r)
			}
			noBody(t, result)
		})
	}
}

// spec: FETCHER-13
func TestTheSECsCertificatesMustChainToTheCertificateGiven(t *testing.T) {
	s := newSECServer(t, serves(appleBody))
	another := newAuthority(t)

	opts := s.options()
	opts.Attempts = 1
	names(t, fetchOnce(t, NewClient(opts), submissions, nil), appleBody)

	for _, c := range []struct {
		name  string
		given func(*ClientOptions)
	}{
		{"another authority's", func(o *ClientOptions) { o.Roots = another.pool }},
		{"the system's, which don't hold the server's authority", func(o *ClientOptions) { o.Roots = nil }},
	} {
		t.Run("given "+c.name, func(t *testing.T) {
			before := len(s.seen())
			opts := s.options()
			opts.Attempts = 1
			c.given(&opts)
			result := fetchOnce(t, NewClient(opts), submissions, nil)
			if sent := len(s.seen()) - before; sent != 0 {
				t.Errorf("%d requests over a connection whose certificate it doesn't accept", sent)
			}
			if r := result.Record; r.Status != nil || r.Error == nil || !strings.Contains(*r.Error, "certificate") {
				t.Errorf("the record: %+v", r)
			}
		})
	}
	t.Run("for another host's name", func(t *testing.T) {
		other := newSECServer(t, serves(appleBody))
		other.TLS.Certificates[0] = other.authority.issue(t, "example.com")
		opts := other.options()
		opts.Attempts = 1
		result := fetchOnce(t, NewClient(opts), submissions, nil)
		if len(other.seen()) != 0 || result.Record.Error == nil {
			t.Errorf("%d requests: %+v", len(other.seen()), result.Record)
		}
	})
}

// The system's roots are those the fetcher's image holds, which a process
// on Linux reads from SSL_CERT_FILE, once.
const (
	helperAddress = "FETCHER_TEST_SERVER"
	helperOther   = "FETCHER_TEST_OTHER_AUTHORITY"
)

// spec: FETCHER-13
func TestGivenNoCertificateTheSECsCertificatesMustChainToTheImagesRoots(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("only on Linux, as the fetcher's image is, does a process read its roots from SSL_CERT_FILE")
	}
	s := newSECServer(t, serves(appleBody))
	roots := t.TempDir() + "/roots.pem"
	if err := os.WriteFile(roots, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: s.authority.cert.Raw}),
		0o600); err != nil {
		t.Fatal(err)
	}
	other := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: newAuthority(t).cert.Raw})
	cmd := exec.Command(os.Args[0], "-test.run=^TestRootsInAProcessOfItsOwn$", "-test.v")
	cmd.Env = append(os.Environ(), "SSL_CERT_FILE="+roots, "SSL_CERT_DIR="+t.TempDir(),
		helperAddress+"="+s.Listener.Addr().String(), helperOther+"="+string(other))
	output, err := cmd.CombinedOutput()
	if err != nil || !strings.Contains(string(output), "--- PASS: TestRootsInAProcessOfItsOwn") {
		t.Fatalf("%v:\n%s", err, output)
	}
	// Given no certificate, it accepted the image's roots; given another, it
	// sent nothing, whatever the roots its image holds.
	if seen := s.seen(); len(seen) != 1 {
		t.Errorf("%d requests, not 1", len(seen))
	}
}

// TestRootsInAProcessOfItsOwn runs only as the process of the test above,
// whose roots it reads from SSL_CERT_FILE.
func TestRootsInAProcessOfItsOwn(t *testing.T) {
	address := os.Getenv(helperAddress)
	if address == "" {
		t.Skip("only as the process of TestGivenNoCertificateTheSECsCertificatesMustChainToTheImagesRoots")
	}
	dial := func(ctx context.Context, network, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, network, address)
	}
	opts := ClientOptions{UserAgent: testUserAgent, Dial: dial, Attempts: 1, Timeout: 2 * time.Second}
	names(t, fetchOnce(t, NewClient(opts), submissions, nil), appleBody)

	opts.Roots = x509.NewCertPool()
	if !opts.Roots.AppendCertsFromPEM([]byte(os.Getenv(helperOther))) {
		t.Fatal("no other authority")
	}
	if result := fetchOnce(t, NewClient(opts), submissions, nil); result.Record.Error == nil {
		t.Errorf("it accepted the image's roots in place of the certificate given: %+v", result.Record)
	}
}

// spec: ACCESS-1, FETCH-8
func TestAFetchWaitsWhileRequestsArentAllowedThenMakesItsNextAttempt(t *testing.T) {
	var mu sync.Mutex
	allowed := true
	permit := func() error {
		mu.Lock()
		defer mu.Unlock()
		if !allowed {
			return ErrRequestsPaused
		}
		return nil
	}
	allow := func(to bool) {
		mu.Lock()
		allowed = to
		mu.Unlock()
	}
	var n atomic.Int32
	s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
		if n.Add(1) == 1 {
			// Paused as the first attempt fails.
			allow(false)
			w.Header().Set("Retry-After", "0")
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		serves(appleBody)(w, r)
	})
	opts := s.options()
	opts.Permit = permit
	// Its connections, each a TLS handshake with the SEC's host before its
	// request may be refused.
	var dials atomic.Int32
	dial := opts.Dial
	opts.Dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		dials.Add(1)
		return dial(ctx, network, address)
	}
	c := NewClient(opts)

	done := make(chan Result)
	go func() {
		result, err := c.Fetch(context.Background(), "submissions", submissions, Tool(testCommit), nil, nil)
		if err != nil {
			t.Error(err)
		}
		done <- result
	}()
	time.Sleep(time.Second)
	// It made no connection meanwhile, either.
	if seen := len(s.seen()); seen != 1 || dials.Load() != 1 {
		t.Fatalf("%d requests, over %d connections, while requests weren't allowed", seen, dials.Load())
	}
	allow(true)
	result := <-done
	if len(s.seen()) != 2 || dials.Load() != 2 || result.Record.Attempts != 2 {
		t.Fatalf("%d requests, over %d connections, %d attempts", len(s.seen()), dials.Load(), result.Record.Attempts)
	}
	names(t, result, appleBody)
}

// spec: FETCH-8, JOB-4
func TestBetweenItsAttemptsAFetchWaitsAsTheWaitItsGivenDoes(t *testing.T) {
	var n atomic.Int32
	s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
		if n.Add(1) == 1 {
			w.Header().Set("Retry-After", "30")
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		serves(appleBody)(w, r)
	})
	opts := s.options()
	// The retry is refused once, as it's about to be written.
	var refuse atomic.Bool
	var dials atomic.Int32
	dial := opts.Dial
	opts.Dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if dials.Add(1) == 2 {
			refuse.Store(true)
		}
		return dial(ctx, network, address)
	}
	opts.Permit = func() error {
		if refuse.CompareAndSwap(true, false) {
			return ErrRequestsPaused
		}
		return nil
	}
	c := NewClient(opts)
	// Each call of the wait: how long it was given, and the requests the
	// server had received by then.
	type call struct {
		d        time.Duration
		requests int
	}
	var calls []call
	wait := func(_ context.Context, d time.Duration) error {
		calls = append(calls, call{d, len(s.seen())})
		return nil
	}

	began := time.Now()
	result, err := c.Fetch(context.Background(), "submissions", submissions, Tool(testCommit), nil, wait)
	if err != nil {
		t.Fatal(err)
	}
	// In place of FETCH-8's 30 seconds after the first attempt, and again,
	// with none, once the retry was refused; never before the first attempt.
	if want := []call{{30 * time.Second, 1}, {0, 1}}; !slices.Equal(want, calls) {
		t.Errorf("the waits: %+v, not %+v", calls, want)
	}
	if took := time.Since(began); took > 10*time.Second {
		t.Errorf("the fetch took %s, as though it waited itself", took)
	}
	if len(s.seen()) != 2 || dials.Load() != 3 || result.Record.Attempts != 2 {
		t.Fatalf("%d requests, over %d connections, %d attempts", len(s.seen()), dials.Load(), result.Record.Attempts)
	}
	names(t, result, appleBody)
}

// spec: ACCESS-1, FETCH-16
func TestARequestRefusedAsItsAboutToBeWrittenIsNotSentAndIsNoAttempt(t *testing.T) {
	s := newSECServer(t, serves(appleBody))
	opts := s.options()
	// Requests stop being allowed once the first connection is made, before
	// its request is written, for one look.
	var refuse atomic.Bool
	var dials atomic.Int32
	dial := opts.Dial
	opts.Dial = func(ctx context.Context, network, address string) (net.Conn, error) {
		if dials.Add(1) == 1 {
			refuse.Store(true)
		}
		return dial(ctx, network, address)
	}
	opts.Permit = func() error {
		if refuse.CompareAndSwap(true, false) {
			return ErrRequestsPaused
		}
		return nil
	}
	c := NewClient(opts)

	result := fetchOnce(t, c, submissions, nil)
	// The first connection carried no request, and its attempt was none.
	if len(s.seen()) != 1 || dials.Load() != 2 || result.Record.Attempts != 1 {
		t.Fatalf("%d requests, over %d connections, %d attempts", len(s.seen()), dials.Load(), result.Record.Attempts)
	}
	names(t, result, appleBody)
}

// spec: FETCH-4
func TestAHeadersBytesAreKeptAsISO88591TextAndAValidatorIsSentBackAsItCame(t *testing.T) {
	// HTTP allows a header's value bytes above 0x7F (RFC 9110, section 5.5),
	// which on their own aren't UTF-8, the only text a record's string and an
	// event's JSON can hold.
	const etag = "\"caf\xe9\""
	s := newSECServer(t, func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("If-None-Match") == etag {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		conn, buf, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		defer func() { _ = conn.Close() }()
		_, _ = fmt.Fprintf(buf, "HTTP/1.1 200 OK\r\nETag: %s\r\nContent-Type: text/plain; name=caf\xe9\r\n"+
			"Content-Length: %d\r\nConnection: close\r\n\r\n%s", etag, len(appleBody), appleBody)
		_ = buf.Flush()
	})
	c := NewClient(s.options())

	result := fetchOnce(t, c, submissions, nil)
	names(t, result, appleBody)
	r := result.Record
	// Each byte is the character of its code point, as ISO-8859-1 has it, and
	// as snapshot.py's http.client reads a header.
	if value(r.ETag) != "\"café\"" || value(r.ContentType) != "text/plain; name=café" {
		t.Errorf("the headers recorded: %q, %q", value(r.ETag), value(r.ContentType))
	}

	again := fetchOnce(t, c, submissions, &Version{ETag: r.ETag, SHA256: *r.SHA256, Bytes: *r.Bytes, Object: *r.Object})
	if sent := s.seen()[1].header.Get("If-None-Match"); sent != etag {
		t.Errorf("If-None-Match: %q, not the ETag's bytes as they came, %q", sent, etag)
	}
	if value(again.Record.Status) != http.StatusNotModified || value(again.Record.SHA256) != *r.SHA256 {
		t.Errorf("the record: %+v", again.Record)
	}
}

func TestAFetchWhoseContextEndsIsAbandonedWithoutARecord(t *testing.T) {
	arrived := make(chan struct{}, 1)
	s := newSECServer(t, func(_ http.ResponseWriter, r *http.Request) {
		arrived <- struct{}{}
		<-r.Context().Done()
	})
	c := NewClient(s.options())
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		<-arrived
		cancel()
	}()

	result, err := c.Fetch(ctx, "submissions", submissions, Tool(testCommit), nil, nil)
	if !errors.Is(err, context.Canceled) || result.Record.RequestedAt != "" {
		t.Errorf("%v: %+v", err, result.Record)
	}
}
