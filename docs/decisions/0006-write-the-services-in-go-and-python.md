# 6. Write the services in Go and Python, divided by whether they read EDGAR's content

**Status:** Accepted, October 9, 2026.

## Context

The owner requires at least one service in Go and at least one in Python.
The design has six services: the scheduler, the fetcher, the discoverer, the
parse workers, the exporter, and the query API, plus a fake EDGAR for tests.
The scripts that snapshot and verify the git store are Python, with their
tests. Each language should get the services it suits, and the line between
them should follow a rule, so a new service has an obvious home.

## Options

- **Divide by whether a service reads EDGAR's content.** Go for the services
  that move bytes and keep the rules for talking to the SEC: the scheduler,
  the fetcher, and the query API. Python for the services that read what
  EDGAR publishes: the discoverer, the parse workers, and the exporter,
  beside the scripts and the investigation's findings.
- **Go only for the scheduler and the query API,** with a Python fetcher
  built from `snapshot.py`. The fetching rules are implemented once, but Go
  gets only the two thinnest services, and the Lease and the rate limit,
  the parts a platform team looks at first, are in Python, where leader
  election by Lease is much less used.
- **Divide by stage:** Go up to storing a body and recording the filing,
  Python after it. The discoverer would read `submissions` and filing
  headers in Go, so knowledge of EDGAR's formats would live in both
  languages.

## Decision

Divide by whether a service reads EDGAR's content.

| Service | Language | Main libraries |
| --- | --- | --- |
| Scheduler | Go | controller-runtime ([0007](0007-control-the-pipeline-through-custom-resources.md)) |
| Fetcher | Go | client-go's leader election for the Lease ([0005](0005-send-sec-requests-through-one-active-fetcher.md)), `golang.org/x/time/rate`, nats.go, the AWS SDK for Go v2 for Garage, pgx |
| Query API | Go | `net/http`, pgx |
| Discoverer | Python | nats-py, psycopg, boto3 |
| Parse workers | Python | nats-py, psycopg, boto3, and an XBRL parser still to choose |
| Exporter | Python | pyarrow, psycopg, boto3 |
| Fake EDGAR, for tests | Python | the standard library, serving the git store under `data/` |

Every service exposes Prometheus metrics and sends traces with OpenTelemetry
([0008](0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)),
through each language's official client libraries.

## Consequences

- Code that reads what EDGAR publishes is all in Python, so a change in
  EDGAR's formats touches one language.
- The fetching rules have two implementations: `snapshot.py` for the git
  store and the investigation, and the Go fetcher for the cluster. The fake
  EDGAR records every request it receives, and one set of checks on those
  requests (the user agent, at most 5 a second, nothing after a `403`) runs
  against `snapshot.py` in its tests and against the Go fetcher on kind.
- Go tests carry the spec IDs they check, as Python tests carry markers. The
  pull request that adds the first Go test sets the form, such as a
  `// spec: FETCH-6` comment above the test, and extends
  `tests/test_spec_coverage.py` and `AGENTS.md` to match.
- The two languages meet only at NATS messages, PostgreSQL tables, and Garage
  objects. Each is a schema in `spec/schemas/`, and tests on both sides
  validate against it.
- CI runs a second toolchain: `go vet`, golangci-lint, and `go test`, beside
  ruff, mypy, and pytest. The repository holds one Go module and one Python
  project; the first pull request with service code sets their layout.
- pyarrow is pinned, since the export's bytes, and so its hashes, depend on
  the writer's version.
- The XBRL parser is chosen with the facts specification: a narrow parser on
  lxml, or Arelle, an open-source XBRL processor whose plugins include the
  SEC's EDGAR Renderer. Both are Python.

## Sources

- [client-go: package leaderelection](https://pkg.go.dev/k8s.io/client-go/tools/leaderelection)
- [nats.go](https://github.com/nats-io/nats.go) and [nats.py](https://github.com/nats-io/nats.py)
- [Arelle](https://github.com/Arelle/Arelle) and its [EDGAR plugins](https://github.com/Arelle/EDGAR)
