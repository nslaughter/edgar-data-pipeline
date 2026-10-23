package v1alpha1_test

import (
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"github.com/google/go-cmp/cmp"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/client-go/rest"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/yaml"

	"github.com/nslaughter/edgar-data-pipeline/internal/testenv"
)

var config *rest.Config

func TestMain(m *testing.M) {
	env, cfg, err := testenv.StartKubernetes()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	config = cfg
	code := m.Run()
	if err := env.Stop(); err != nil {
		fmt.Fprintln(os.Stderr, err)
	}
	os.Exit(code)
}

// definition reads a definition as a generic object, without its
// description fields or its annotations, which API-2 sets aside.
func definition(t *testing.T, path string) map[string]any {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var crd map[string]any
	if err := yaml.Unmarshal(data, &crd); err != nil {
		t.Fatal(err)
	}
	delete(crd["metadata"].(map[string]any), "annotations")
	return withoutDescriptions(crd).(map[string]any)
}

func withoutDescriptions(value any) any {
	switch value := value.(type) {
	case map[string]any:
		kept := map[string]any{}
		for key, item := range value {
			if key != "description" {
				kept[key] = withoutDescriptions(item)
			}
		}
		return kept
	case []any:
		kept := make([]any, len(value))
		for i, item := range value {
			kept[i] = withoutDescriptions(item)
		}
		return kept
	default:
		return value
	}
}

// spec: API-2
func TestTheDefinitionsGeneratedFromTheTypesAreThoseInSpecSchemas(t *testing.T) {
	root, err := testenv.Root()
	if err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	generate := exec.Command("go", "tool", "controller-gen", "crd", "paths=.", "output:crd:dir="+dir)
	if out, err := generate.CombinedOutput(); err != nil {
		t.Fatalf("controller-gen: %v\n%s", err, out)
	}
	generated, err := filepath.Glob(filepath.Join(dir, "*.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	var plurals []string
	for _, path := range generated {
		crd := definition(t, path)
		plural := crd["spec"].(map[string]any)["names"].(map[string]any)["plural"].(string)
		plurals = append(plurals, plural)
		want := definition(t, filepath.Join(root, "spec", "schemas", plural+".yaml"))
		if !reflect.DeepEqual(crd, want) {
			t.Errorf("%s differs from spec/schemas/%s.yaml (-spec +generated):\n%s",
				filepath.Base(path), plural, cmp.Diff(want, crd))
		}
	}
	if got := strings.Join(plurals, " "); got != "backfills secaccesses sources" {
		t.Errorf("generated %q, not the three definitions", got)
	}
}

func object(kind, name string, spec any) *unstructured.Unstructured {
	content := map[string]any{
		"apiVersion": "edgar.nathanslaughter.com/v1alpha1",
		"kind":       kind,
		"metadata":   map[string]any{"name": name},
	}
	if kind != "SecAccess" {
		content["metadata"].(map[string]any)["namespace"] = "default"
	}
	if spec != nil {
		content["spec"] = spec
	}
	return &unstructured.Unstructured{Object: content}
}

func source(spec map[string]any) *unstructured.Unstructured {
	return object("Source", "apple-submissions", spec)
}

func validSource() map[string]any {
	return map[string]any{
		"feed":           "submissions",
		"url":            "https://data.sec.gov/submissions/CIK0000320193.json",
		"cadenceSeconds": int64(10),
	}
}

func validBackfill() map[string]any {
	return map[string]any{"cik": int64(320193), "filingDateFrom": "2025-01-01", "filingDateTo": "2025-12-31"}
}

func with(spec map[string]any, key string, value any) map[string]any {
	changed := map[string]any{}
	for k, v := range spec {
		changed[k] = v
	}
	if value == nil {
		delete(changed, key)
	} else {
		changed[key] = value
	}
	return changed
}

func newClient(t *testing.T) client.Client {
	t.Helper()
	c, err := client.New(config, client.Options{})
	if err != nil {
		t.Fatal(err)
	}
	return c
}

// spec: API-3
func TestTheAPIServerRefusesWhatTheDefinitionsRefuse(t *testing.T) {
	c := newClient(t)
	ctx := context.Background()
	refused := map[string]*unstructured.Unstructured{
		"a Source without a spec":                 source(nil),
		"a Source without a feed":                 source(with(validSource(), "feed", nil)),
		"a Source without a url":                  source(with(validSource(), "url", nil)),
		"a Source without a cadence":              source(with(validSource(), "cadenceSeconds", nil)),
		"a Source whose feed isn't a name":        source(with(validSource(), "feed", "Submissions")),
		"a Source whose url isn't the SEC's":      source(with(validSource(), "url", "https://example.com/x.json")),
		"a Source whose url isn't https":          source(with(validSource(), "url", "http://data.sec.gov/x.json")),
		"a Source whose cadence is 0":             source(with(validSource(), "cadenceSeconds", int64(0))),
		"a Source whose cadence is negative":      source(with(validSource(), "cadenceSeconds", int64(-10))),
		"a Source whose cadence isn't an integer": source(with(validSource(), "cadenceSeconds", "10")),
		"a Source whose paused isn't a boolean":   source(with(validSource(), "paused", "yes")),
		"a Backfill without a spec":               object("Backfill", "apple-2025", nil),
		"a Backfill without a cik":                object("Backfill", "apple-2025", with(validBackfill(), "cik", nil)),
		"a Backfill without its first date": object("Backfill", "apple-2025",
			with(validBackfill(), "filingDateFrom", nil)),
		"a Backfill without its last date": object("Backfill", "apple-2025",
			with(validBackfill(), "filingDateTo", nil)),
		"a Backfill whose cik is 0": object("Backfill", "apple-2025", with(validBackfill(), "cik", int64(0))),
		"a Backfill whose cik has 11 digits": object("Backfill", "apple-2025",
			with(validBackfill(), "cik", int64(10000000000))),
		"a Backfill whose date isn't a date": object("Backfill", "apple-2025",
			with(validBackfill(), "filingDateFrom", "2025-02-30")),
		"a Backfill whose date isn't in its form": object("Backfill", "apple-2025",
			with(validBackfill(), "filingDateFrom", "20250101")),
		"a Backfill whose range ends before it starts": object("Backfill", "apple-2025",
			with(validBackfill(), "filingDateTo", "2024-12-31")),
		"a SecAccess named anything but cluster": object("SecAccess", "other", map[string]any{}),
		"a SecAccess without a spec":             object("SecAccess", "cluster", nil),
		"a SecAccess whose paused isn't a boolean": object("SecAccess", "cluster",
			map[string]any{"paused": "no"}),
		"a SecAccess whose acknowledgement has no milliseconds": object("SecAccess", "cluster",
			map[string]any{"acknowledgedStopAt": "2026-10-06T18:04:12Z"}),
		"a SecAccess whose acknowledgement isn't a time": object("SecAccess", "cluster",
			map[string]any{"acknowledgedStopAt": "2026-02-30T18:04:12.345Z"}),
	}
	for name, obj := range refused {
		t.Run(name, func(t *testing.T) {
			err := c.Create(ctx, obj)
			if err == nil {
				_ = c.Delete(ctx, obj)
				t.Fatal("the API server accepted it")
			}
			if !apierrors.IsInvalid(err) && !apierrors.IsBadRequest(err) {
				t.Fatalf("refused for another reason: %v", err)
			}
		})
	}
}

// spec: API-3
func TestTheAPIServerRefusesAChangeToABackfillsSpec(t *testing.T) {
	c := newClient(t)
	ctx := context.Background()
	backfill := object("Backfill", "apple-2025-change", validBackfill())
	if err := c.Create(ctx, backfill); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = c.Delete(ctx, backfill) })
	if err := unstructured.SetNestedField(backfill.Object, "2025-06-30", "spec", "filingDateTo"); err != nil {
		t.Fatal(err)
	}
	if err := c.Update(ctx, backfill); !apierrors.IsInvalid(err) {
		t.Fatalf("a changed spec: %v", err)
	}
}

// spec: API-3
func TestTheAPIServerAcceptsWhatTheDefinitionsAllow(t *testing.T) {
	c := newClient(t)
	ctx := context.Background()
	for _, obj := range []*unstructured.Unstructured{
		object("Source", "accepted-source", with(validSource(), "paused", true)),
		object("Backfill", "accepted-backfill", with(validBackfill(), "filingDateTo", "2025-01-01")),
		object("SecAccess", "cluster", map[string]any{"acknowledgedStopAt": "2026-10-06T18:04:12.345Z"}),
	} {
		if err := c.Create(ctx, obj); err != nil {
			t.Errorf("%s: %v", obj.GetKind(), err)
			continue
		}
		_ = c.Delete(ctx, obj)
	}
}
