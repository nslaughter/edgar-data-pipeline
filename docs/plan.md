# Plan: the EDGAR data pipeline

**Status:** October 10, 2026. No task is done yet; the next is T1.

The tasks that build the pipeline, in the order they're to be done, and
which are done. When the owner asks for the next item, an agent takes the
first task that isn't done, isn't taken, and is ready to start, as
[`AGENTS.md`](../AGENTS.md#doing-the-next-item) says. The plan makes no
rules: each task names the documents that govern it. Data here comes from
the U.S. Securities and Exchange Commission's EDGAR system.

## How the plan works

- **Each task is one pull request,** whose title ends with the task's ID.
  IDs run from T1 and are never reused or renumbered; a task split later
  gets new IDs.
- **A task is ready when what it depends on is done.** "Run" means the task
  sends requests to the SEC, so it's ready only when the owner asks for that
  run. "Scope" means it widens the scope, which the owner must ask for too
  ([`AGENTS.md`](../AGENTS.md#rules)).
- **A task's pull request marks it done,** with the date in its row, and
  names the next task in the status line.
- **Some tasks wait on `edgar-asof`,** the public library the Python
  services read EDGAR through
  ([0009](decisions/0009-read-edgar-through-a-public-point-in-time-library.md)).
  Such a dependency names a release, and is met when that release is
  published. The library's own tasks are planned in its repository.
- **The owner adds, removes, and reorders tasks.**
- **The investigation's daily refetches aren't tasks.** Each is a run the
  owner asks for ([method, step 2](../investigation/edgar.md#method)),
  committed after `uv run scripts/verify.py` like any other change to the
  store. T8 reads what they stored.

## Investigation

Each task answers part of the investigation's
[questions](../investigation/edgar.md#questions), and records its findings
there, from SRC-6 on, with the stored versions that show them.

| ID | Task | Depends on | Done |
| --- | --- | --- | --- |
| T1 | **Fiscal periods and joins, from the baseline (Q2).** From the stored `companyfacts` and `submissions`: whether `fy` and `fp` describe a value's period or its filing's, how each company's fiscal year appears, and whether each value's accession number joins it to a filing, and so to an acceptance time. The answer also decides `edgar-asof`'s first release, which joins `companyfacts` values to acceptance times this way. | — | |
| T2 | **Restated and repeated values, from the baseline (Q3).** From the stored `companyfacts`: whether a value a later filing reports for the same concept and period is kept beside the earlier one, how a restated value differs from one reported again unchanged, and how amendments appear. `edgar-asof`'s specification cites these findings too. | — | |
| T3 | **Sizes and the pace of a backfill, from the baseline (Q5).** Bytes per URL and per filing, and the requests and time a backfill of each company's filings takes at 5 requests a second, from the filings its `submissions` lists. Growth per refetch waits for T8. | — | |
| T4 | **The first refetch (Q1, Q4).** The [method's](../investigation/edgar.md#method) step 2 for the first time: refetch `submissions`, `companyfacts`, and the daily index listing; fetch the master indexes not yet stored; and refetch the stored ones. Shows whether a conditional request gets a `304` (SRC-4), and whether a published index changes. | Run | |
| T5 | **The latest-filings feed (Q4).** Once the owner confirms its URL, widen the scope to it in its own commit, fetch it, and record what it holds, so T6 and T7 can poll it too. | Scope, run | |
| T6 | **Watch Microsoft's next 10-Q arrive (Q2, Q3, Q4).** The method's steps 3 and 4, around the report expected in late October or early November: from before the time of day its earlier filings were accepted, poll `submissions` and `companyfacts` every minute, and the latest-filings feed if T5 is done, until both show the filing; then fetch its index, header, and XBRL instance. Records how long after acceptance each feed shows it, whether its header and `submissions` agree (SRC-2), and how its values appear in `companyfacts`. | Run | |
| T7 | **Watch Apple's next 10-K arrive (Q2, Q3, Q4).** The same as T6, for Apple's report. It also shows whether Apple's times still differ by 8 hours (SRC-2), its fiscal year end (SRC-3), and how a 10-K reports earlier years' values again. | Run | |
| T8 | **What the refetches show (Q1, Q4, Q5).** From the daily refetches stored through T6 and T7: how often each feed changed, whether a URL returned to an earlier body, when each day's index appeared, and growth per refetch. Also refetches the baseline's eight filings, to see whether any archived file changed (Q1). | T4, T6, T7, run | |
| T9 | **Walmart's next 10-Q (Q2, Q3, Q4).** The method's step 4, after Walmart's quarter ends October 31: fetch the filing's index, header, and XBRL instance, and note when it first appears in each feed. | Run | |

## Decisions

Each task writes a decision record in [`docs/decisions/`](decisions/),
Proposed until the owner accepts it.

| ID | Task | Depends on | Done |
| --- | --- | --- | --- |
| T10 | **How workers scale by queue depth.** How the parse workers scale with their queue's depth on kind ([0003](decisions/0003-queue-work-in-nats-jetstream.md)). | — | |
| T11 | **The notice source and its cadence.** `submissions`, the daily indexes, or the latest-filings feed if T5 is done, and how often to poll it, from Q4's findings ([what waits on the investigation](design.md#what-waits-on-the-investigation)). | T6, T7, T8 | |
| T12 | **The XBRL parser.** A narrow parser on lxml, or Arelle ([0006](decisions/0006-write-the-services-in-go-and-python.md)), for what the facts need, from Q2's and Q3's findings. `edgar-asof` reads XBRL instances with the same parser once it's chosen. | T1, T2 | |

## Specifications

Each task writes one specification, as
[what the specifications must say](design.md#what-the-specifications-must-say)
lists them, in its own pull request and versioned as its document says.

| ID | Task | Depends on | Done |
| --- | --- | --- | --- |
| T13 | **Deployment, `spec/deployment.md`.** The pinned charts, operators, and images, PostgreSQL's major version 18 among them, and the chart tests that check them ([0001](decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md), [0004](decisions/0004-keep-the-record-in-postgresql-18.md)). As the second specification, it extends `tests/test_spec_coverage.py` to every `spec/*.md`. | — | |
| T14 | **Controls, `spec/controls.md`.** The `Source`, `Backfill`, and `SecAccess` resources, their `v1alpha1` schemas in `spec/schemas/`, and who may change what ([0007](decisions/0007-control-the-pipeline-through-custom-resources.md)). | — | |
| T15 | **The snapshot store's version 1.0.0.** A major version of [`spec/snapshots.md`](../spec/snapshots.md): its guarantees on Garage and PostgreSQL as well as git, the rate limit and one process through the Lease (FETCH-6, FETCH-7), the user agent from a Secret (FETCH-2, ID-1), how `data/scope.json` relates to `Source` resources, how `edgar-asof`'s cache relates to the store, and how data written under version 0.2.0 is read. | T14 | |
| T16 | **Notices, `spec/notices.md`.** The discoverer's messages, each filing's acceptance and first-seen times, coverage, and gaps. | T11, T15 | |
| T17 | **Facts, `spec/facts.md`.** How facts are stored, and their times. It cites a version of `edgar-asof`'s specification for the fact model and what "as of T" returns, and adds the fact table. | T12, T16, an `edgar-asof` release that specifies the fact model | |
| T18 | **The export, `spec/export.md`.** Its format, its determinism, and its hashes, answering the store's [open question 3](../spec/snapshots.md#13-open-questions). | T17 | |
| T19 | **Telemetry, `spec/telemetry.md`.** The metrics and traces each service emits, and the alerts on a `403`, on a gap longer than its source's cadence, and on a backup more than a day old ([0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)). | T16, T17 | |

## Building

Each task builds one part of the [design](design.md#components) as its
specifications say, with tests that carry their IDs. A task may be split
into smaller ones once its specifications are written.

| ID | Task | Depends on | Done |
| --- | --- | --- | --- |
| T20 | **The platform on kind.** The umbrella chart, with NATS JetStream, Garage and its layout Job, CloudNativePG with PostgreSQL 18 and backups to Garage, and kube-prometheus-stack; its chart tests; and a CI job that installs it on kind ([0001](decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md) to [0004](decisions/0004-keep-the-record-in-postgresql-18.md), [0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)). | T13 | |
| T21 | **The fake EDGAR.** Serves the stored versions under `data/` in the cluster and records every request, and the checks on those requests run against `snapshot.py` ([testing on kind](design.md#testing-on-kind)). As the first service code, it sets where the services live in the Python project ([0006](decisions/0006-write-the-services-in-go-and-python.md)). | T20 | |
| T22 | **The resources and the scheduler.** The custom resource definitions, checked against T14's schemas, and the scheduler as a Go controller, tested with envtest ([0007](decisions/0007-control-the-pipeline-through-custom-resources.md)). As the first Go code, it sets the Go module's layout and how Go tests carry spec IDs, and extends `tests/test_spec_coverage.py` and `AGENTS.md` to match ([0006](decisions/0006-write-the-services-in-go-and-python.md)). | T14, T20 | |
| T23 | **The fetcher.** One active fetcher through the Lease, with the rate limit, the retries, and the stop on a `403` kept in `SecAccess`; each body to Garage and a record per fetch to PostgreSQL; and the checks on its requests, against the fake EDGAR on kind ([0005](decisions/0005-send-sec-requests-through-one-active-fetcher.md)). | T15, T21, T22 | |
| T24 | **The discoverer.** New filings from each new version of the notice source, with their first-seen times and any gap's bounds, and the fetches of their headers, indexes, and XBRL instances. It reads `submissions` and headers through a pinned `edgar-asof`. | T16, T23, `edgar-asof`'s first release | |
| T25 | **The parse workers.** Each stored XBRL instance's facts, written with the filing's acceptance time under an import manifest, scaled by queue depth. It builds facts through a pinned `edgar-asof`. | T10, T17, T24, an `edgar-asof` release that reads XBRL instances | |
| T26 | **The query API.** Answers "as of T", tested against the cases `edgar-asof` publishes, so both answer the same way. | T17, T25 | |
| T27 | **The exporter.** The facts as Parquet in Garage, with each file's SHA-256, through a pinned `edgar-asof`. | T18, T25 | |
| T28 | **Monitoring.** The services' metrics, Grafana's dashboards and the views of the record they read, and the alert rules, tested with `promtool`. | T19, T26, T27 | |
| T29 | **Traces in Tempo.** One trace per filing, from the poll that first saw it to its facts, carried through NATS. | T28 | |
| T30 | **Logs in Loki, fed by Alloy.** | T29 | |
| T31 | **Drills.** Delete the active fetcher, fail over the database, block the SEC, rebuild the facts from stored bodies, and restore the database from its backups in Garage; each drill shows the gaps recorded and nothing lost ([outages and recovery](design.md#outages-and-recovery)). | T28 | |
