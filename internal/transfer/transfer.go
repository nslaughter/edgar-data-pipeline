// Package transfer copies the records of the git store's scope that the
// cluster made in a range of time the owner names, with their objects, from
// the cluster store into data/ (spec/snapshots.md, section 9, records the
// cluster made). It reads the cluster store with credentials of its own,
// which may only read it (LOG-5), through the API server of the cluster the
// owner names (spec/deployment.md, section 5, the transfer).
package transfer

import (
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"github.com/nslaughter/edgar-data-pipeline/api/v1alpha1"
)

// Source is the cluster store as the transfer reads it.
type Source interface {
	// Records gives the records whose requested_at is in the range, in the
	// order of their ids (XFER-1, XFER-2).
	Records(ctx context.Context, r Range) ([]Stored, error)
	// Object gives an object as the bucket holds it.
	Object(ctx context.Context, name string) ([]byte, error)
}

// Range is a range of time the owner names: from its start up to and not
// including its end (XFER-1).
type Range struct {
	From, Until time.Time
}

func (r Range) String() string {
	return fmt.Sprintf("from %s up to %s", stamp(r.From), stamp(r.Until))
}

// stamp gives a time in the store's form, in UTC to the millisecond
// (spec/snapshots.md, section 2).
func stamp(t time.Time) string {
	return t.UTC().Format(v1alpha1.TimestampLayout)
}

// Refusal says why the transfer copies nothing of a range it could read: a
// malformed scope, records that conflict, a budget the copy would break
// (XFER-1, XFER-3), or a cluster that holds the fake EDGAR (TRANSFER-6).
type Refusal struct {
	Reasons []string
}

func (r *Refusal) Error() string {
	return "copied nothing: " + strings.Join(r.Reasons, "; ")
}

// Copy is what copying a range adds to data/: the records to append, in the
// order of their ids, and the objects they name that data/ doesn't hold.
type Copy struct {
	// Read is the number of records of the range, and InScope the number
	// of them the scope covers.
	Read, InScope int
	// Held is the number of the scope's records that data/ holds already,
	// which the copy skips (XFER-3).
	Held    int
	records []Record
	objects map[string][]byte
	store   GitStore
}

// Records is the number of records the copy appends.
func (c *Copy) Records() int { return len(c.records) }

// Objects is the number of objects the copy places.
func (c *Copy) Objects() int { return len(c.objects) }

// Plan reads a range from the cluster store and gives what copying it adds
// to data/, without writing anything. It refuses if scope.json is malformed
// or a record of the range conflicts with another, and if data/ with the
// range copied would break the budget, saying which limit (XFER-1, XFER-3).
func Plan(ctx context.Context, source Source, store GitStore, r Range) (*Copy, error) {
	if !r.From.Before(r.Until) {
		return nil, fmt.Errorf("the range %s is empty: its start must come before its end", r)
	}
	scope, err := store.Scope()
	if err != nil {
		return nil, &Refusal{Reasons: []string{err.Error() + "; run scripts/verify.py"}}
	}
	stored, err := source.Records(ctx, r)
	if err != nil {
		return nil, fmt.Errorf("reading the range's records: %w", err)
	}
	held, err := store.records()
	if err != nil {
		return nil, err
	}
	c := &Copy{Read: len(stored), objects: map[string][]byte{}, store: store}
	var conflicts []string
	inRange := map[key]Stored{}
	for _, s := range stored {
		record := s.Record
		if !scope.Covers(record.URL) {
			continue
		}
		c.InScope++
		if err := record.check(); err != nil {
			return nil, fmt.Errorf("the cluster store's record %d can't be copied: %w", s.ID, err)
		}
		k := record.key()
		if h, ok := held[k]; ok {
			if h.record.Line() == record.Line() {
				c.Held++
			} else {
				conflicts = append(conflicts, fmt.Sprintf("the cluster store's record %d and %s", s.ID, h.where))
			}
			continue
		}
		if earlier, ok := inRange[k]; ok {
			if earlier.Record.Line() != record.Line() {
				conflicts = append(conflicts, fmt.Sprintf("the cluster store's records %d and %d", earlier.ID, s.ID))
			}
			continue
		}
		inRange[k] = s
		c.records = append(c.records, record)
	}
	if len(conflicts) > 0 {
		reasons := make([]string, len(conflicts))
		for i, conflict := range conflicts {
			reasons[i] = conflict + " share a url, requested_at, and tool, but differ (REC-12)"
		}
		return nil, &Refusal{Reasons: reasons}
	}
	if err := c.fetchObjects(ctx, source); err != nil {
		return nil, err
	}
	if reasons, err := c.overBudget(); err != nil {
		return nil, err
	} else if len(reasons) > 0 {
		return nil, &Refusal{Reasons: reasons}
	}
	return c, nil
}

// fetchObjects reads each object the copy's records name that data/
// doesn't hold, and checks that it's the body its name and its record give
// (OBJ-1, REC-8), so the copy places none that isn't.
func (c *Copy) fetchObjects(ctx context.Context, source Source) error {
	for _, record := range c.records {
		if record.Object == nil {
			continue
		}
		name := *record.Object
		if _, ok := c.objects[name]; ok {
			continue
		}
		there, err := c.store.HasObject(name)
		if err != nil {
			return err
		}
		if there {
			continue
		}
		data, err := source.Object(ctx, name)
		if err != nil {
			return fmt.Errorf("reading the object %s: %w", name, err)
		}
		if err := checkObject(name, data, *record.Bytes); err != nil {
			return err
		}
		c.objects[name] = data
	}
	return nil
}

func checkObject(name string, data []byte, size int64) error {
	reader, err := gzip.NewReader(bytes.NewReader(data))
	if err != nil {
		return fmt.Errorf("the cluster store's object %s isn't gzip data (OBJ-1)", name)
	}
	digest := sha256.New()
	n, err := io.Copy(digest, reader)
	if err != nil {
		return fmt.Errorf("the cluster store's object %s isn't gzip data (OBJ-1)", name)
	}
	if objectPath(hex.EncodeToString(digest.Sum(nil))) != name {
		return fmt.Errorf("the cluster store's object %s isn't the body its name gives (OBJ-1)", name)
	}
	if n != size {
		return fmt.Errorf("the cluster store's object %s is %d bytes, not the %d its record gives (REC-8)", name, n, size)
	}
	return nil
}

// overBudget says which limit data/ would break with the copy made: a file
// over 50 MiB, or files over 1 GB in all (SCOPE-4, SCOPE-5).
func (c *Copy) overBudget() ([]string, error) {
	sizes, err := c.store.Sizes()
	if err != nil {
		return nil, err
	}
	for _, record := range c.records {
		sizes[logPath(record.Feed)] += int64(len(record.Line()))
	}
	for name, data := range c.objects {
		sizes[name] = int64(len(data))
	}
	paths := make([]string, 0, len(sizes))
	var total int64
	for path, size := range sizes {
		paths = append(paths, path)
		total += size
	}
	sort.Strings(paths)
	var reasons []string
	for _, path := range paths {
		if sizes[path] > FileLimit {
			reasons = append(reasons, fmt.Sprintf("SCOPE-4: %s would be %s bytes, over %s",
				path, thousands(sizes[path]), thousands(FileLimit)))
		}
	}
	if total > TotalLimit {
		reasons = append(reasons, fmt.Sprintf("SCOPE-5: data/'s files would total %s bytes, over %s",
			thousands(total), thousands(TotalLimit)))
	}
	return reasons, nil
}

// thousands gives a number with commas between its thousands, as
// scripts/verify.py reports sizes.
func thousands(n int64) string {
	digits := fmt.Sprint(n)
	var b strings.Builder
	for i, d := range digits {
		if i > 0 && (len(digits)-i)%3 == 0 {
			b.WriteByte(',')
		}
		b.WriteRune(d)
	}
	return b.String()
}

// writer is what the copy writes to: the git store.
type writer interface {
	PlaceObject(name string, data []byte) error
	Append(feed, line string) error
}

// Write makes the copy: for each record, in the order of their ids, it
// places the record's object, unless data/ holds it, then appends the record
// to its feed's log (XFER-2, OBJ-2).
func (c *Copy) Write() error {
	return c.writeTo(c.store)
}

func (c *Copy) writeTo(w writer) error {
	placed := map[string]bool{}
	for _, record := range c.records {
		if record.Object != nil && !placed[*record.Object] {
			if data, ok := c.objects[*record.Object]; ok {
				if err := w.PlaceObject(*record.Object, data); err != nil {
					return err
				}
				placed[*record.Object] = true
			}
		}
		if err := w.Append(record.Feed, record.Line()); err != nil {
			return err
		}
	}
	return nil
}
