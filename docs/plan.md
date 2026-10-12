# Plan: the EDGAR data pipeline

**Status:** October 13, 2026. T1 to T3, T13 to T15, and T34 are done. The
next is T35; T4 to T9 and the investigation's daily refetches wait for it.

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
- **The order is the rows' order,** through the sections in turn. A task
  that moves keeps its ID, so IDs needn't be in order.
- **A task is ready when what it depends on is done.** "Run" means the task
  sends requests to the SEC, so it's ready only when the owner asks for that
  run. "Scope" means it widens the scope, which the owner must ask for too
  ([`AGENTS.md`](../AGENTS.md#rules)). "Owner" means the owner asks for the
  task when it's time. A decision record, such as 0010, must be Accepted.
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
  store. They wait for T35, as T4 to T9 do, so that their records keep each
  response's arrival and `Date` (REC-10). T8 reads what they stored.

## Investigation

Each task answers part of the investigation's
[questions](../investigation/edgar.md#questions), and records its findings
there, from SRC-6 on, with the stored versions that show them. How soon each
feed shows a new filing (Q4) comes from the pipeline's record once it runs
([0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)),
not from these tasks.

| ID | Task | Depends on | Done |
| --- | --- | --- | --- |
| T1 | **Fiscal periods and joins, from the baseline (Q2).** From the stored `companyfacts` and `submissions`: whether `fy` and `fp` describe a value's period or its filing's, how each company's fiscal year appears, and whether each value's accession number joins it to a filing, and so to an acceptance time. The answer also decides `edgar-asof`'s first release, which joins `companyfacts` values to acceptance times this way. | — | October 10, 2026: SRC-6 to SRC-11 |
| T2 | **Restated and repeated values, from the baseline (Q3).** From the stored `companyfacts`: whether a value a later filing reports for the same concept and period is kept beside the earlier one, how a restated value differs from one reported again unchanged, and how amendments appear. `edgar-asof`'s specification cites these findings too. | — | October 11, 2026: SRC-14 to SRC-17 |
| T3 | **Sizes and the pace of a backfill, from the baseline (Q5).** Bytes per URL and per filing, and the requests and time a backfill of each company's filings takes at 5 requests a second, from the filings its `submissions` lists. Growth per refetch waits for T8. | — | October 11, 2026: SRC-18 to SRC-20 |
| T4 | **The first refetch (Q1, Q4).** The [method's](../investigation/edgar.md#method) step 2 for the first time: refetch `submissions`, `companyfacts`, and the daily index listing; fetch the master indexes not yet stored; and refetch the stored ones. Shows whether a conditional request gets a `304` (SRC-4), and whether a published index changes. | T35, run | |
| T5 | **The latest-filings feed (Q4).** Once the owner confirms its URL, widen the scope to it in its own commit, fetch it, and record what it holds, so T11 can weigh it as a second notice source. | T35, scope, run | |
| T6 | **Microsoft's next 10-Q (Q2, Q3).** The method's step 4, after the report expected in late October or early November: fetch its index, header, and XBRL instance, and refetch `submissions` and `companyfacts`. Records whether its header and `submissions` agree (SRC-2), and how its values appear in `companyfacts`. | T35, run | |
| T7 | **Apple's next 10-K (Q2, Q3).** The same as T6, for Apple's report. It also shows whether Apple's times still differ by 8 hours (SRC-2), its fiscal year end (SRC-3), and how a 10-K reports earlier years' values again. | T35, run | |
| T8 | **What the refetches show (Q1, Q4, Q5).** From the daily refetches stored through T6 and T7: how often each feed changed, whether a URL returned to an earlier body, when each day's index appeared, and growth per refetch. Also refetches the baseline's eight filings, to see whether any archived file changed (Q1). | T4, T6, T7, T35, run | |
| T9 | **Walmart's next 10-Q (Q2, Q3).** The same as T6, for Walmart's report after its quarter ends October 31. | T35, run | |

## Observing filings

The first part of the pipeline: enough to poll each company's `submissions`,
record every time raw, and show each lag
([0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)).
Each specification is its own pull request, versioned as its document says
([what the specifications must say](design.md#what-the-specifications-must-say)).
Each building task builds one part of the [design](design.md#components) as
its specifications say, with tests that carry their IDs, and may be split
into smaller tasks once its specifications are written.

| ID | Task | Depends on | Done |
| --- | --- | --- | --- |
| T13 | **Deployment, `spec/deployment.md`.** The pinned charts, operators, and images, PostgreSQL's major version 18 among them, and the chart tests that check them ([0001](decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md), [0004](decisions/0004-keep-the-record-in-postgresql-18.md)). As the second specification, it extends `tests/test_spec_coverage.py` to every `spec/*.md`. | — | October 11, 2026: version 0.1.0, whose tests T20 writes |
| T14 | **Controls, `spec/controls.md`.** The `Source`, `Backfill`, and `SecAccess` resources, their `v1alpha1` schemas in `spec/schemas/`, including each `Source`'s cadence and its bounds, which the chart checks before it applies them, and who may change what ([0007](decisions/0007-control-the-pipeline-through-custom-resources.md), [0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)). | 0010 | October 12, 2026: version 0.1.0, whose tests T22, T23, T24, and T28 write, apart from API-1's |
| T15 | **The snapshot store's version 1.0.0.** A major version of [`spec/snapshots.md`](../spec/snapshots.md): its guarantees on Garage and PostgreSQL as well as git, the rate limit and one process through the Lease (FETCH-6, FETCH-7), the user agent from a Secret (FETCH-2, ID-1), how `data/scope.json` relates to `Source` resources, each response's `Date` and arrival time, how the records of the git store's scope reach `data/` so findings can cite them, how `edgar-asof`'s cache relates to the store, and how data written under version 0.2.0 is read. | T14, 0010 | October 12, 2026: version 1.0.0, whose tests T22, T23, T24, T35, and T36 write, apart from STORE-1's, REC-13's, and JOB-1's |
| T35 | **The scripts at the store's version 1.0.0.** `snapshot.py` records each response's arrival as `received_at` and its `Date` as `date` (REC-10), and `verify.py` checks them (REC-1, REC-11), accepts the fetcher's `tool` (REC-1), leaves a `304` the transfer copied to the cluster store's check (REC-5), finds a record held twice (REC-12), validates every record in `data/` against the record's schema (REC-14), and holds `data/` to its budget (SCOPE-4, SCOPE-5) ([`spec/snapshots.md`](../spec/snapshots.md)). | T15 | |
| T16 | **Notices, `spec/notices.md`.** The discoverer's messages; each filing's times, raw, with the polls that bound when each source first showed it; coverage and gaps; and the queries that give each lag. | T15, 0010 | |
| T19 | **Telemetry for observing filings, `spec/telemetry.md`.** The metrics and traces the scheduler, the fetcher, and the discoverer emit; the views of the record that show coverage, gaps, and each source's lag; and the alerts on a `403`, on a gap longer than its source's cadence, and on a backup more than a day old ([0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)). | T16 | |
| T34 | **The platform's configuration and the pipeline's images, a version of `spec/deployment.md`.** Each component's replicas, storage, Secrets, and placement, as the decision records describe them, such as three Garage nodes with a replication factor of 3 and three PostgreSQL instances, as statements checked like the pins. The owner decided on October 11, 2026 that they are specified before the platform is built ([0001](decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md) to [0004](decisions/0004-keep-the-record-in-postgresql-18.md), [0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)). It also states how the pipeline's own images are tagged and loaded into kind ([section 9](../spec/deployment.md#9-later-versions)), which the owner decided on October 12, 2026 to specify here, before T21, the first service. It also states how the chart installs and upgrades custom resource definitions, its operators' and the pipeline's own ([`spec/controls.md`, section 11](../spec/controls.md#11-other-specifications)), since Helm installs a chart's `crds/` directory but never upgrades it, which the owner decided on October 12, 2026 to specify here, before T20 and T22. Helm can't create a resource in the release that installs its definition, or before an operator's webhook is ready, so the owner decided on October 12, 2026 to install the definitions and the operators in three releases before the umbrella chart ([0011](decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md)), which makes this a major version. | T13 | October 12, 2026: version 1.0.0, whose tests T20 and T21 write |
| T20 | **The platform on kind.** The four charts, in order: the Prometheus operator's definitions; cert-manager; the operators, CloudNativePG and its Barman Cloud plugin; and the umbrella chart, with NATS JetStream, Garage and its layout Job, PostgreSQL 18 with backups to Garage, and kube-prometheus-stack. Their chart tests, and a CI job that installs them on kind ([0001](decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md) to [0004](decisions/0004-keep-the-record-in-postgresql-18.md), [0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md), [0011](decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md)). | T13, T34, 0011 | |
| T21 | **The fake EDGAR.** Serves the stored versions under `data/` in the cluster and records every request, and the checks on those requests run against `snapshot.py` ([testing on kind](design.md#testing-on-kind)). As the first service code, it sets where the services live in the Python project ([0006](decisions/0006-write-the-services-in-go-and-python.md)). As the first of the pipeline's images, it adds the command that builds them and loads them into kind, and the umbrella chart's `images.commit` ([IMG-1 to IMG-4](../spec/deployment.md#the-pipelines-own-images)). | T20 | |
| T22 | **The resources and the scheduler.** The custom resource definitions, in the operators' chart, checked against T14's schemas; the chart's `Source` resources for the three companies, from its values, with the `values.schema.json` and template check that refuse a configuration over the cadence budget; and the scheduler as a Go controller, tested with envtest ([0007](decisions/0007-control-the-pipeline-through-custom-resources.md)). As the first Go code, it sets the Go module's layout and how Go tests carry spec IDs, and extends `tests/test_spec_coverage.py` and `AGENTS.md` to match ([0006](decisions/0006-write-the-services-in-go-and-python.md)). | T14, T15, T20 | |
| T23 | **The fetcher.** One active fetcher through the Lease, with the rate limit, the retries, and the stop on a `403` kept in `SecAccess`; each body to Garage and a record per fetch to PostgreSQL, with every time T15 names; and the checks on its requests, against the fake EDGAR on kind ([0005](decisions/0005-send-sec-requests-through-one-active-fetcher.md)). | T15, T21, T22 | |
| T36 | **The transfer.** Copies the records of the git store's scope that the cluster made in a range of time the owner names, with their objects, into `data/`, so findings can cite them (XFER-1 to XFER-3, [0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)). | T23, T35 | |
| T24 | **The discoverer.** New filings from each new version of each company's `submissions`, with their raw times and the polls that bound when each source first showed them, and the fetches of their headers, indexes, and XBRL instances. It reads `submissions` and headers through a pinned `edgar-asof`. | T16, T23, `edgar-asof`'s first release | |
| T28 | **Monitoring for observing filings.** Those services' metrics, Grafana's dashboards of coverage, gaps, and each source's lag from acceptance, and the alert rules, tested with `promtool`. | T19, T24 | |
| T11 | **Revisit the notice source and its cadence.** From the lags the record shows: whether to keep each company's `submissions`, add the latest-filings feed if T5 is done, or change the cadence. A decision record that supersedes 0010 if anything changes. | T28, owner | |

## Facts

Facts from each filing, the "as of T" answers, and the export, on top of the
first part.

| ID | Task | Depends on | Done |
| --- | --- | --- | --- |
| T10 | **How workers scale by queue depth.** A decision record: how the parse workers scale with their queue's depth on kind ([0003](decisions/0003-queue-work-in-nats-jetstream.md)). | — | |
| T12 | **The XBRL parser.** A decision record: a narrow parser on lxml, or Arelle ([0006](decisions/0006-write-the-services-in-go-and-python.md)), for what the facts need, from Q2's and Q3's findings. `edgar-asof` reads XBRL instances with the same parser once it's chosen. | T1, T2 | |
| T17 | **Facts, `spec/facts.md`.** How facts are stored, and their times. It cites a version of `edgar-asof`'s specification for the fact model and what "as of T" returns, and adds the fact table. | T12, T16, an `edgar-asof` release that specifies the fact model | |
| T18 | **The export, `spec/export.md`.** Its format, its determinism, and its hashes, answering the store's [open question 3](../spec/snapshots.md#13-open-questions). | T17 | |
| T25 | **The parse workers.** Each stored XBRL instance's facts, written with the filing's acceptance time under an import manifest, scaled by queue depth. It builds facts through a pinned `edgar-asof`. | T10, T17, T24, an `edgar-asof` release that reads XBRL instances | |
| T26 | **The query API.** Answers "as of T", tested against the cases `edgar-asof` publishes, so both answer the same way. | T17, T25 | |
| T27 | **The exporter.** The facts as Parquet in Garage, with each file's SHA-256, through a pinned `edgar-asof`. | T18, T25 | |
| T32 | **Telemetry for facts.** A minor version of `spec/telemetry.md` for the parse workers, the query API, and the exporter, and the view of each filing's times from acceptance to facts available. | T17, T18, T19 | |
| T33 | **Monitoring for facts.** Their metrics, the dashboard of each filing's times from acceptance to facts available, and their alert rules, tested with `promtool`. | T26, T27, T28, T32 | |

## Traces, logs, and drills

| ID | Task | Depends on | Done |
| --- | --- | --- | --- |
| T29 | **Traces in Tempo.** One trace per filing, from the poll that first saw it to its facts, carried through NATS. | T33 | |
| T30 | **Logs in Loki, fed by Alloy.** | T29 | |
| T31 | **Drills.** Delete the active fetcher, fail over the database, block the SEC, rebuild the facts from stored bodies, and restore the database from its backups in Garage; each drill shows the gaps recorded and nothing lost ([outages and recovery](design.md#outages-and-recovery)). | T33 | |
