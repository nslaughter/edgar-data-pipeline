package main

import (
	"bytes"
	"context"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
)

// apiServer is a server that counts the requests it receives, and the
// kubeconfig of a context of it, the current one.
func apiServer(t *testing.T) (*atomic.Int32, string) {
	t.Helper()
	var requests atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		requests.Add(1)
		http.Error(w, "the tests' API server answers nothing", http.StatusForbidden)
	}))
	t.Cleanup(server.Close)
	path := filepath.Join(t.TempDir(), "config")
	config := `apiVersion: v1
kind: Config
current-context: current
clusters:
- name: current
  cluster: {server: "` + server.URL + `"}
users:
- name: owner
  user: {token: owner}
contexts:
- name: current
  context: {cluster: current, user: owner}
`
	if err := os.WriteFile(path, []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	return &requests, path
}

// spec: TRANSFER-3
func TestWithoutAContextTheTransferReadsNoClusterNotEvenTheCurrentContexts(t *testing.T) {
	requests, path := apiServer(t)
	t.Setenv("KUBECONFIG", path)
	data := t.TempDir()
	var stdout, stderr bytes.Buffer

	status := run(context.Background(),
		[]string{"--from", "2026-10-29T14:00:00Z", "--until", "2026-10-29T15:00:00Z", "--data", data},
		&stdout, &stderr)

	if status != misused {
		t.Errorf("exit %d", status)
	}
	if !strings.Contains(stderr.String(), "--context") {
		t.Errorf("stderr:\n%s", stderr.String())
	}
	if requests.Load() != 0 {
		t.Errorf("%d requests reached the current context's API server", requests.Load())
	}
}

// spec: TRANSFER-3
func TestWithTheCurrentContextNamedTheTransferReachesItsAPIServer(t *testing.T) {
	requests, path := apiServer(t)
	var stdout, stderr bytes.Buffer

	status := run(context.Background(), []string{
		"--context", "current", "--kubeconfig", path, "--data", t.TempDir(),
		"--from", "2026-10-29T14:00:00Z", "--until", "2026-10-29T15:00:00Z",
	}, &stdout, &stderr)

	if status != failed {
		t.Errorf("exit %d: %s", status, stderr.String())
	}
	if requests.Load() == 0 {
		t.Errorf("no request reached the named context's API server")
	}
}

func TestTheRangeIsTwoTimesTheFirstBeforeTheSecond(t *testing.T) {
	for _, args := range [][]string{
		{"--context", "c"},
		{"--context", "c", "--from", "2026-10-29T14:00:00Z"},
		{"--context", "c", "--from", "yesterday", "--until", "2026-10-29T15:00:00Z"},
		{"--context", "c", "--from", "2026-10-29T15:00:00Z", "--until", "2026-10-29T15:00:00Z"},
		{"--context", "c", "--from", "2026-10-29T14:00:00Z", "--until", "2026-10-29T15:00:00Z", "extra"},
	} {
		var stdout, stderr bytes.Buffer

		if status := run(context.Background(), args, &stdout, &stderr); status != misused {
			t.Errorf("%v: exit %d", args, status)
		}
	}
}
