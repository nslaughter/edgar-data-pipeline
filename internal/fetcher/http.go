package fetcher

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"math"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"golang.org/x/time/rate"
)

// The fetching rules both writers keep (spec/snapshots.md, section 10).
const (
	// MinInterval is the least time from the start of one request to the
	// start of the next: at most 5 a second (FETCH-6).
	MinInterval = 200 * time.Millisecond
	// Attempts is the most attempts of one fetch, retries included
	// (FETCH-8).
	Attempts = 4
	// MaxBackoff caps the wait before a retry (FETCH-8).
	MaxBackoff = 60 * time.Second
	// Timeout is how long an attempt waits for its connection, for its
	// response, and for each read of its body (FETCH-5), as a timeout on
	// snapshot.py's socket does. Without a response by then, it has none;
	// with a body that stalls that long, the body is cut short (FETCH-13).
	Timeout = 60 * time.Second
	// maxRedirects bounds the redirects one attempt follows, as Go's client
	// does by default.
	maxRedirects = 10
)

// SECHosts are the only hosts the fetcher sends requests to (FETCH-1).
var SECHosts = map[string]bool{"www.sec.gov": true, "data.sec.gov": true}

// retried are the statuses that are retried, with no response and a body cut
// short (FETCH-8).
var retried = map[int]bool{429: true, 500: true, 502: true, 503: true, 504: true}

// IsSECURL says whether a URL is one the fetcher may fetch: https, on one of
// the SEC's hosts (FETCH-1).
func IsSECURL(u *url.URL) bool {
	return u.Scheme == "https" && SECHosts[u.Hostname()] && (u.Port() == "" || u.Port() == "443") && u.User == nil
}

// ErrNotFetchable says that a URL isn't one the fetcher may fetch.
var ErrNotFetchable = errors.New("not a URL the fetcher may fetch")

// ParseURL parses a URL the fetcher may fetch (FETCH-1). Any other gives an
// error that wraps ErrNotFetchable: one that isn't a URL at all among them,
// such as one with an escape that isn't one, or a control character, which
// can begin as one of the SEC's hosts' URLs does, as a job's must.
func ParseURL(raw string) (*url.URL, error) {
	target, err := url.Parse(raw)
	if err != nil {
		return nil, fmt.Errorf("%w: %w", ErrNotFetchable, err)
	}
	if !IsSECURL(target) {
		return nil, fmt.Errorf("%w: %q isn't an https URL on one of the SEC's hosts", ErrNotFetchable, raw)
	}
	return target, nil
}

// ClientOptions configures a Client.
type ClientOptions struct {
	// UserAgent is the owner's, which every request carries (FETCH-2).
	UserAgent string
	// Roots, when given, are the only certificates the SEC's hosts'
	// certificates may chain to, as where CI installs the fake EDGAR. Without
	// them, they must chain to the system's, those the fetcher's image holds
	// (FETCHER-13).
	Roots *x509.CertPool
	// Permit says whether a request may be sent now, as it's about to be
	// written: while this replica holds the Lease, and requests are allowed
	// (FETCH-16, ACCESS-1). Without it, every request may be.
	Permit func() error
	// Dial makes each connection's TCP connection, with a net.Dialer by
	// default. The tests give one that reaches a local server.
	Dial func(ctx context.Context, network, address string) (net.Conn, error)
	// Timeout, MinInterval, and Attempts are FETCH-5's, FETCH-6's, and
	// FETCH-8's unless the tests give others, and BackoffUnit is the unit of
	// FETCH-8's wait before a retry without a Retry-After, a second.
	Timeout     time.Duration
	MinInterval time.Duration
	Attempts    int
	BackoffUnit time.Duration
	// Clock gives the times each record keeps: time.Now by default.
	Clock func() time.Time
	// PermitPoll is how often a fetch that may not send looks again: 100
	// milliseconds by default.
	PermitPoll time.Duration
}

// Client sends the fetcher's requests to the SEC's hosts, as the fetching
// rules have them. Each request carries the user agent and asks for gzip
// (FETCH-2, FETCH-3), and goes over a connection of its own, whose
// certificate it has checked (FETCHER-13). Each is written at least
// MinInterval after the one before was, once its connection's TLS handshake
// is done, so a handshake that takes longer brings no two requests closer
// as they arrive, and nor does a wait that ends late (FETCH-6). And each is
// sent only if Permit allows it then.
type Client struct {
	userAgent  string
	roots      *x509.CertPool
	permit     func() error
	dial       func(ctx context.Context, network, address string) (net.Conn, error)
	timeout    time.Duration
	attempts   int
	backoff    time.Duration
	clock      func() time.Time
	permitPoll time.Duration
	interval   time.Duration
	limiter    *rate.Limiter
	http       *http.Client
	// now and pause are the clock and the waits that space requests
	// (FETCH-6): time.Now and sleep, but in a test of a wait that ends late.
	now   func() time.Time
	pause func(ctx context.Context, d time.Duration) error
}

// NewClient returns a Client.
func NewClient(opts ClientOptions) *Client {
	c := &Client{
		userAgent:  opts.UserAgent,
		roots:      opts.Roots,
		permit:     opts.Permit,
		dial:       opts.Dial,
		timeout:    opts.Timeout,
		attempts:   opts.Attempts,
		backoff:    opts.BackoffUnit,
		clock:      opts.Clock,
		permitPoll: opts.PermitPoll,
		interval:   opts.MinInterval,
		now:        time.Now,
		pause:      sleep,
	}
	if c.permit == nil {
		c.permit = func() error { return nil }
	}
	if c.dial == nil {
		c.dial = (&net.Dialer{}).DialContext
	}
	if c.timeout == 0 {
		c.timeout = Timeout
	}
	if c.attempts == 0 {
		c.attempts = Attempts
	}
	if c.backoff == 0 {
		c.backoff = time.Second
	}
	if c.clock == nil {
		c.clock = time.Now
	}
	if c.permitPoll == 0 {
		c.permitPoll = 100 * time.Millisecond
	}
	if c.interval == 0 {
		c.interval = MinInterval
	}
	c.limiter = rate.NewLimiter(rate.Every(c.interval), 1)
	c.http = &http.Client{
		Transport: &http.Transport{
			Proxy: nil,
			DialContext: func(context.Context, string, string) (net.Conn, error) {
				return nil, errors.New("only https URLs are fetched")
			},
			DialTLSContext:        c.dialTLS,
			DisableKeepAlives:     true,
			DisableCompression:    true,
			ResponseHeaderTimeout: c.timeout,
		},
		// A redirect is followed only to a URL that may be fetched, and only
		// while a request may be sent; otherwise the redirect is the
		// response.
		CheckRedirect: func(req *http.Request, via []*http.Request) error {
			if len(via) >= maxRedirects || !IsSECURL(req.URL) || c.permit() != nil {
				return http.ErrUseLastResponse
			}
			return nil
		},
	}
	return c
}

// attempt is one attempt of a fetch, which the dials of its requests share
// through their context: its own, and when its first request was written.
// The transport dials apart from a request's cancellation, so a dial reads
// the attempt's context for it.
type attempt struct {
	ctx     context.Context
	mu      sync.Mutex
	sent    time.Time
	refused bool
}

type attemptKey struct{}

func (a *attempt) state() (sent time.Time, refused bool) {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.sent, a.refused
}

// dialTLS makes a connection for one request: a TCP connection to one of
// the SEC's hosts, then a TLS handshake that checks its certificate against
// the Client's roots. The connection's first write, its request's, waits
// until the request may start (FETCH-6) and may be sent at all (Permit):
// only then does the request go out.
func (c *Client) dialTLS(ctx context.Context, network, address string) (net.Conn, error) {
	a, _ := ctx.Value(attemptKey{}).(*attempt)
	if a == nil {
		return nil, errors.New("a request outside a fetch")
	}
	host, port, err := net.SplitHostPort(address)
	if err != nil {
		return nil, err
	}
	if !SECHosts[host] || port != "443" {
		return nil, fmt.Errorf("%s isn't one of the SEC's hosts", address)
	}
	dialing, cancel := context.WithTimeout(a.ctx, c.timeout)
	defer cancel()
	raw, err := c.dial(dialing, network, address)
	if err != nil {
		return nil, err
	}
	conn := tls.Client(raw, &tls.Config{ServerName: host, RootCAs: c.roots, MinVersion: tls.VersionTLS12})
	if err := conn.HandshakeContext(dialing); err != nil {
		_ = raw.Close()
		return nil, err
	}
	return &requestConn{Conn: &timeoutConn{Conn: conn, timeout: c.timeout}, client: c, attempt: a}, nil
}

// start lets a request go as it's about to be written: once it may start,
// MinInterval after the one before (FETCH-6), and only if Permit allows it
// then. Only then does it take its turn from the limiter, as the clock reads
// then, so the next request's interval counts from when this one is
// written. A turn reserved before the wait, as the limiter's Wait reserves
// one, would count from when it was due instead, so that a wait that ended
// late, or a request written late after it, would bring the next request
// closer.
func (c *Client) start(a *attempt) error {
	for {
		if err := c.ready(a.ctx); err != nil {
			return err
		}
		a.mu.Lock()
		if err := c.permit(); err != nil {
			a.refused = true
			a.mu.Unlock()
			return err
		}
		// Another request, made at the same time, may have taken the turn
		// since; then this one waits for the next.
		taken := c.limiter.AllowN(c.now(), 1)
		if taken && a.sent.IsZero() {
			a.sent = c.clock()
		}
		a.mu.Unlock()
		if taken {
			return nil
		}
	}
}

// ready waits until a request may start, without taking its turn, or until
// ctx is done.
func (c *Client) ready(ctx context.Context) error {
	for {
		missing := 1 - c.limiter.TokensAt(c.now())
		if missing <= 0 {
			return nil
		}
		if err := c.pause(ctx, time.Duration(math.Ceil(missing*float64(c.interval)))); err != nil {
			return err
		}
	}
}

// requestConn holds back its connection's first write, its request's,
// until the Client's start lets the request go; if it doesn't, nothing is
// written.
type requestConn struct {
	net.Conn
	client  *Client
	attempt *attempt
	once    sync.Once
	err     error
}

func (c *requestConn) Write(b []byte) (int, error) {
	c.once.Do(func() { c.err = c.client.start(c.attempt) })
	if c.err != nil {
		return 0, c.err
	}
	return c.Conn.Write(b)
}

// timeoutConn gives each read and each write of a connection the Client's
// timeout (FETCH-5).
type timeoutConn struct {
	net.Conn
	timeout time.Duration
}

func (c *timeoutConn) Read(b []byte) (int, error) {
	if err := c.SetReadDeadline(time.Now().Add(c.timeout)); err != nil {
		return 0, err
	}
	return c.Conn.Read(b)
}

func (c *timeoutConn) Write(b []byte) (int, error) {
	if err := c.SetWriteDeadline(time.Now().Add(c.timeout)); err != nil {
		return 0, err
	}
	return c.Conn.Write(b)
}

// Result is what a fetch made: its record, and the object that holds its
// body when the record names a body the fetch received.
type Result struct {
	Record Record
	// Object is the body as gzip data, which the store keeps as the object
	// the record names (OBJ-1, OBJ-4). It's nil for a record that names no
	// body received, a 304's among them.
	Object []byte
}

// Wait is what a fetch does between its attempts, in place of FETCH-8's
// wait before a retry: it waits at least d, and returns an error only once
// ctx is done. It's given the URL's latest version in the store as the
// fetch last had it, and gives the latest as the wait ends, whose
// validators the next attempt sends (FETCH-4), and which a 304 to it names
// (REC-5). The Worker's takes the jobs of earlier kinds meanwhile
// (Worker.between), one of which may record a later version of the URL.
type Wait func(ctx context.Context, d time.Duration, previous *Version) (*Version, error)

// Fetch makes one fetch of a URL, recorded under its feed: up to Attempts
// attempts, each a request, and the record of the last (FETCH-8,
// FETCH-14). previous is the URL's latest version in the store, if it has
// one, whose validators make each request conditional (FETCH-4). Before
// each retry, it calls wait with FETCH-8's wait, or else sleeps that long,
// and makes the retry once wait returns, with the validators of the
// version wait gives. It calls wait only between attempts, never between
// the requests of one, such as a redirect's.
// Before each attempt, it waits until a request may be sent: while requests
// aren't allowed, a fetch between attempts waits, and makes its next
// attempt once they are, as the owner decided. A retry refused as it was
// about to be written calls wait again, with no time to wait, before it's
// made. It returns an error that wraps ErrNotFetchable, without a request,
// for a URL it may not fetch, which the Worker never gives it (ParseURL).
// Otherwise it returns an error only if ctx is done before the fetch ends,
// when the fetch is abandoned, without a record, as the owner decided.
func (c *Client) Fetch(ctx context.Context, feed, rawURL, tool string, previous *Version, wait Wait) (Result,
	error) {
	target, err := ParseURL(rawURL)
	if err != nil {
		return Result{}, err
	}
	if wait == nil {
		wait = func(ctx context.Context, d time.Duration, previous *Version) (*Version, error) {
			return previous, sleep(ctx, d)
		}
	}
	// delay is FETCH-8's wait before the next attempt, a retry.
	var delay time.Duration
	for n := 1; ; {
		if n > 1 {
			if previous, err = wait(ctx, delay, previous); err != nil {
				return Result{}, err
			}
		}
		headers := validators(previous)
		if err := c.permitted(ctx); err != nil {
			return Result{}, err
		}
		a := &attempt{ctx: ctx}
		started := c.clock()
		resp, err := c.do(ctx, a, target, headers)
		var received time.Time
		if err == nil {
			received = c.clock()
		}
		sent, refused := a.state()
		if sent.IsZero() && refused && ctx.Err() == nil {
			// Nothing was sent, since Permit refused the request as it was
			// about to be: it's no attempt, and waits until one may be. A
			// retry's wait is over, so it waits for none.
			closeBody(resp)
			delay = 0
			continue
		}
		if sent.IsZero() {
			// No connection was made, so no request went out; the attempt
			// began when it tried to make one.
			sent = started
		}
		result := Result{Record: Record{Feed: feed, URL: rawURL, RequestedAt: timestamp(sent), Attempts: n, Tool: tool}}
		retry := false
		var retryAfter string
		if err != nil {
			result.Record.Error = ptr("no response: " + err.Error())
			retry = true
		} else {
			retryAfter = resp.Header.Get("Retry-After")
			retry = c.read(resp, target, received, previous, len(headers) > 0, &result)
		}
		if ctx.Err() != nil {
			return Result{}, ctx.Err()
		}
		if !retry || n >= c.attempts {
			return result, nil
		}
		delay = retryDelay(retryAfter, n, c.clock(), c.backoff)
		n++
	}
}

// validators gives the headers that make a request conditional on a
// version, with its validators, if it has any (FETCH-4). Each goes back in
// the bytes it came in (fromLatin1).
func validators(previous *Version) map[string]string {
	headers := map[string]string{}
	if previous != nil && previous.ETag != nil {
		headers["If-None-Match"] = toLatin1(*previous.ETag)
	}
	if previous != nil && previous.LastModified != nil {
		headers["If-Modified-Since"] = toLatin1(*previous.LastModified)
	}
	return headers
}

// permitted waits until a request may be sent, or until ctx is done.
func (c *Client) permitted(ctx context.Context) error {
	for c.permit() != nil {
		if err := sleep(ctx, c.permitPoll); err != nil {
			return err
		}
	}
	return ctx.Err()
}

func (c *Client) do(ctx context.Context, a *attempt, target *url.URL, headers map[string]string) (*http.Response,
	error) {
	req, err := http.NewRequestWithContext(context.WithValue(ctx, attemptKey{}, a), http.MethodGet, target.String(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", c.userAgent)
	req.Header.Set("Accept-Encoding", "gzip")
	for name, value := range headers {
		req.Header.Set(name, value)
	}
	return c.http.Do(req)
}

// read fills in the record of an attempt that got a response, and says
// whether the fetch is retried. It closes the response's body.
func (c *Client) read(resp *http.Response, target *url.URL, received time.Time, previous *Version, conditional bool,
	result *Result) bool {
	defer closeBody(resp)
	record := &result.Record
	record.ReceivedAt = ptr(timestamp(received))
	record.Status = ptr(resp.StatusCode)
	if final := resp.Request.URL.String(); final != target.String() {
		record.FinalURL = &final
	}
	record.Date = header(resp.Header, "Date")
	record.ETag = header(resp.Header, "ETag")
	record.LastModified = header(resp.Header, "Last-Modified")
	record.ContentType = header(resp.Header, "Content-Type")
	switch resp.StatusCode {
	case http.StatusOK:
		var retry bool
		result.Object, retry = body(resp, record)
		return retry
	case http.StatusNotModified:
		confirm(previous, conditional, record)
		return false
	default:
		return retried[resp.StatusCode]
	}
}

// body stores a 200's body in the record: its SHA-256, its length, and its
// object's name, and returns it as the object, gzip data (OBJ-1). A gzip
// encoding is decoded first, and no encoding, or identity, is kept as
// received (FETCH-11); any other is recorded with an error and nothing kept,
// without a retry (FETCH-12). A body cut short keeps nothing, and is retried
// (FETCH-13).
func body(resp *http.Response, record *Record) (object []byte, retry bool) {
	encoding := strings.ToLower(strings.TrimSpace(fromLatin1(resp.Header.Get("Content-Encoding"))))
	gzipped := encoding == "gzip" || encoding == "x-gzip"
	if !gzipped && encoding != "" && encoding != "identity" {
		record.Error = ptr("unsupported Content-Encoding: " + encoding)
		return nil, false
	}
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		record.Error = ptr("body cut short: " + err.Error())
		return nil, true
	}
	if resp.ContentLength >= 0 && int64(len(raw)) != resp.ContentLength {
		record.Error = ptr(fmt.Sprintf("body cut short: %d of %d bytes arrived", len(raw), resp.ContentLength))
		return nil, true
	}
	data := raw
	if gzipped {
		if data, err = gunzip(raw); err != nil {
			record.Error = ptr(err.Error())
			return nil, true
		}
	}
	sum := sha256.Sum256(data)
	digest := hex.EncodeToString(sum[:])
	record.SHA256 = &digest
	record.Bytes = ptr(int64(len(data)))
	record.Object = ptr(ObjectName(digest))
	return compress(data), false
}

// confirm points a 304 at the version its conditional request asked about,
// with that version's validators where the 304 gives none, or records an
// error if the request sent no validators (REC-5).
func confirm(previous *Version, conditional bool, record *Record) {
	if previous == nil || !conditional {
		record.Error = ptr("304 to a request that sent no validators")
		return
	}
	if record.ETag == nil || *record.ETag == "" {
		record.ETag = previous.ETag
	}
	if record.LastModified == nil || *record.LastModified == "" {
		record.LastModified = previous.LastModified
	}
	record.SHA256 = ptr(previous.SHA256)
	record.Bytes = ptr(previous.Bytes)
	record.Object = ptr(previous.Object)
}

// gunzip decodes a gzip encoding, or says how it's cut short (FETCH-13).
func gunzip(raw []byte) ([]byte, error) {
	reader, err := gzip.NewReader(bytes.NewReader(raw))
	if err == nil {
		var data []byte
		if data, err = io.ReadAll(reader); err == nil {
			return data, nil
		}
	}
	if errors.Is(err, io.EOF) || errors.Is(err, io.ErrUnexpectedEOF) {
		return nil, errors.New("body cut short: the gzip stream ended early")
	}
	return nil, fmt.Errorf("gzip encoding doesn't decode: %w", err)
}

// compress gives a body as gzip data, without a name or a time in its
// header, so the same body gives the same bytes.
func compress(data []byte) []byte {
	var out bytes.Buffer
	writer := gzip.NewWriter(&out)
	// Writes to a bytes.Buffer don't fail.
	_, _ = writer.Write(data)
	_ = writer.Close()
	return out.Bytes()
}

// header gives a header's value as the response gave it, as text
// (fromLatin1), or nil if it gave none.
func header(h http.Header, name string) *string {
	values := h.Values(name)
	if len(values) == 0 {
		return nil
	}
	return ptr(fromLatin1(values[0]))
}

// fromLatin1 gives a header's value as text: each of its bytes as the
// character of that code point, as ISO-8859-1 has it, and as snapshot.py's
// http.client reads a header. A value in ASCII, as the SEC's are, is
// unchanged. One with a byte above 0x7F, which HTTP allows (RFC 9110,
// section 5.5), becomes UTF-8, the only text a record's string and an
// event's JSON can hold, where the byte alone isn't.
func fromLatin1(raw string) string {
	if isASCII(raw) {
		return raw
	}
	text := make([]rune, len(raw))
	for i := range len(raw) {
		text[i] = rune(raw[i])
	}
	return string(text)
}

// toLatin1 gives text that fromLatin1 gave back in the bytes it was read
// from, as http.client sends a header. Text with a character beyond
// ISO-8859-1, which fromLatin1 never gives, is sent as it is.
func toLatin1(text string) string {
	if isASCII(text) {
		return text
	}
	raw := make([]byte, 0, len(text))
	for _, r := range text {
		if r > 0xFF {
			return text
		}
		raw = append(raw, byte(r))
	}
	return string(raw)
}

func isASCII(s string) bool {
	for i := range len(s) {
		if s[i] >= utf8.RuneSelf {
			return false
		}
	}
	return true
}

// retryDelay is the wait before the attempt after attempt n: the response's
// Retry-After, in seconds or as an HTTP date, or else 2 to the power of n,
// in seconds, the unit given; either capped at MaxBackoff (FETCH-8).
func retryDelay(retryAfter string, n int, now time.Time, unit time.Duration) time.Duration {
	if value := strings.TrimSpace(retryAfter); value != "" {
		if seconds, err := strconv.ParseFloat(value, 64); err == nil && !math.IsNaN(seconds) {
			seconds = math.Max(0, math.Min(seconds, MaxBackoff.Seconds()))
			return time.Duration(seconds * float64(time.Second))
		}
		if when, err := http.ParseTime(value); err == nil {
			return clamp(when.Sub(now))
		}
	}
	return clamp(time.Duration(math.Pow(2, float64(n)) * float64(unit)))
}

func clamp(d time.Duration) time.Duration {
	return max(0, min(d, MaxBackoff))
}

// sleep waits for d, or until ctx is done.
func sleep(ctx context.Context, d time.Duration) error {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func closeBody(resp *http.Response) {
	if resp != nil {
		_ = resp.Body.Close()
	}
}
