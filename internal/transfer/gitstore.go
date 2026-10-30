package transfer

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// The git store's budget, as scripts/verify.py checks it: no file over the
// size at which GitHub warns, and all of them under the size GitHub
// recommends a repository stay under (SCOPE-4, SCOPE-5).
const (
	FileLimit  = 50 * 1024 * 1024
	TotalLimit = 1_000_000_000
)

// GitStore is the git store, the directory data/, as the transfer reads it
// and adds to it (spec/snapshots.md, section 4).
type GitStore struct {
	Dir string
}

// Scope is the URLs the git store may hold: each entry of scope.json
// (spec/snapshots.md, section 9).
type Scope []string

// Covers says whether the scope covers a URL: an entry ending in / covers
// every URL that starts with it, and any other only the URL equal to it.
func (s Scope) Covers(url string) bool {
	for _, entry := range s {
		if url == entry || strings.HasSuffix(entry, "/") && strings.HasPrefix(url, entry) {
			return true
		}
	}
	return false
}

// Scope reads scope.json, or says how it's malformed (SCOPE-1), as
// scripts/verify.py reads it: each key by its exact name, as Python's
// dictionaries match them, and not as Go's decoding of a struct does,
// which takes "URL" for "url".
func (g GitStore) Scope() (Scope, error) {
	data, err := os.ReadFile(filepath.Join(g.Dir, "scope.json"))
	if errors.Is(err, fs.ErrNotExist) {
		return nil, errors.New("scope.json is missing")
	}
	if err != nil {
		return nil, err
	}
	var file map[string]json.RawMessage
	if err := json.Unmarshal(data, &file); err != nil {
		return nil, errors.New("scope.json isn't a JSON object with a list of urls")
	}
	var entries []json.RawMessage
	if err := json.Unmarshal(file["urls"], &entries); err != nil || entries == nil {
		return nil, errors.New("scope.json isn't a JSON object with a list of urls")
	}
	var scope Scope
	for number, raw := range entries {
		var entry map[string]json.RawMessage
		_ = json.Unmarshal(raw, &entry)
		url, isURL := stringField(entry, "url")
		reason, isReason := stringField(entry, "reason")
		if !isURL || !isReason || reason == "" {
			return nil, fmt.Errorf("scope.json's entry %d needs a url and a reason", number+1)
		}
		scope = append(scope, url)
	}
	return scope, nil
}

// stringField gives the string a JSON object's field of that exact name
// holds, or false if the object has no such field or it holds no string.
func stringField(object map[string]json.RawMessage, name string) (string, bool) {
	var value *string
	raw, ok := object[name]
	if !ok || json.Unmarshal(raw, &value) != nil || value == nil {
		return "", false
	}
	return *value, true
}

// held is a record a log holds, and where.
type held struct {
	record Record
	where  string
}

// records gives each record the logs hold, by what no two may share
// (REC-12), and where each is: its log and its line.
func (g GitStore) records() (map[key]held, error) {
	logs, err := filepath.Glob(filepath.Join(g.Dir, "feeds", "*", "versions.jsonl"))
	if err != nil {
		return nil, err
	}
	sort.Strings(logs)
	records := map[key]held{}
	for _, log := range logs {
		if err := g.readLog(log, records); err != nil {
			return nil, err
		}
	}
	return records, nil
}

func (g GitStore) readLog(log string, records map[key]held) error {
	file, err := os.Open(log)
	if err != nil {
		return err
	}
	defer func() { _ = file.Close() }()
	relative, err := filepath.Rel(g.Dir, log)
	if err != nil {
		return err
	}
	scanner := bufio.NewScanner(file)
	scanner.Buffer(nil, FileLimit)
	for number := 1; scanner.Scan(); number++ {
		record, err := parseLine(scanner.Bytes())
		if err != nil {
			return fmt.Errorf("%s, line %d, isn't a record; run scripts/verify.py: %w", relative, number, err)
		}
		where := fmt.Sprintf("%s, line %d", filepath.ToSlash(relative), number)
		if _, ok := records[record.key()]; !ok {
			records[record.key()] = held{record: record, where: where}
		}
	}
	return scanner.Err()
}

// HasObject says whether the git store holds an object.
func (g GitStore) HasObject(name string) (bool, error) {
	_, err := os.Stat(filepath.Join(g.Dir, filepath.FromSlash(name)))
	if errors.Is(err, fs.ErrNotExist) {
		return false, nil
	}
	return err == nil, err
}

// Sizes gives the size of every file under data/, hidden ones too, by its
// path relative to data/, as scripts/verify.py measures them for the budget.
func (g GitStore) Sizes() (map[string]int64, error) {
	sizes := map[string]int64{}
	err := filepath.WalkDir(g.Dir, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || !entry.Type().IsRegular() {
			return err
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		relative, err := filepath.Rel(g.Dir, path)
		if err != nil {
			return err
		}
		sizes[filepath.ToSlash(relative)] = info.Size()
		return nil
	})
	return sizes, err
}

// PlaceObject writes an object once: to a temporary file named *.tmp in
// objects/, flushed to disk, then renamed into place, or deleted instead if
// an object of that name is there already, since the same name means the
// same body (OBJ-2).
func (g GitStore) PlaceObject(name string, data []byte) error {
	objects := filepath.Join(g.Dir, "objects")
	if err := os.MkdirAll(objects, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(objects, "*.tmp")
	if err != nil {
		return err
	}
	// Gone once renamed into place.
	defer func() { _ = os.Remove(tmp.Name()) }()
	if _, err := tmp.Write(data); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Sync(); err != nil {
		_ = tmp.Close()
		return err
	}
	if err := tmp.Close(); err != nil {
		return err
	}
	final := filepath.Join(g.Dir, filepath.FromSlash(name))
	if _, err := os.Stat(final); err == nil {
		return nil
	}
	if err := os.MkdirAll(filepath.Dir(final), 0o755); err != nil {
		return err
	}
	return os.Rename(tmp.Name(), final)
}

// Append adds a line to the end of a feed's log, and flushes it to disk.
func (g GitStore) Append(feed, line string) error {
	log := filepath.Join(g.Dir, "feeds", feed, "versions.jsonl")
	if err := os.MkdirAll(filepath.Dir(log), 0o755); err != nil {
		return err
	}
	file, err := os.OpenFile(log, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o644)
	if err != nil {
		return err
	}
	if _, err := file.Write([]byte(line)); err != nil {
		_ = file.Close()
		return err
	}
	if err := file.Sync(); err != nil {
		_ = file.Close()
		return err
	}
	return file.Close()
}

// logPath is a feed's log, relative to data/.
func logPath(feed string) string {
	return "feeds/" + feed + "/versions.jsonl"
}
