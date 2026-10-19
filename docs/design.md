# Design: the EDGAR data pipeline

**Status:** Draft, October 18, 2026. This document explains the pipeline's
architecture and the reasons for it. It makes no rules: the specifications
under [`spec/`](../spec/) govern, the records under
[`docs/decisions/`](decisions/) record each significant choice, and
[`investigation/edgar.md`](../investigation/edgar.md) records what EDGAR
does. Data here comes from the U.S. Securities and Exchange Commission's
EDGAR system.

## What the pipeline keeps

The SEC's own feeds show the present: `submissions` and `companyfacts` are
summaries it rewrites in place. The pipeline keeps a better record in three
ways, and claims no more than these.

1. **Every filing in scope, with its acceptance time from the filing
   itself.** A filing is an event: each has its own accession number and
   its own files, and an amendment is a new filing, not an edit. What was
   known on a date follows from the filings accepted by then, however often
   the pipeline looked. The acceptance time comes from the filing's header
   ([SRC-1](../investigation/edgar.md#findings-so-far)), not from
   `submissions`, whose times disagree with the header for one company
   (SRC-2).
2. **Every version it observed, never overwritten.** Each fetch is a
   record, and each distinct body is stored once by its SHA-256
   ([`spec/snapshots.md`](../spec/snapshots.md)). The SEC's summaries are
   kept as evidence of what it published, not as the source of the facts:
   the pipeline parses facts from each filing's XBRL instance.
3. **Where it was looking, and where it wasn't.** Each poll is recorded.
   The time between successful polls is coverage; a missed poll or an
   outage is a gap. A version the SEC served and replaced during a gap is
   lost to everyone, and the record says the gap was there instead of
   implying that nothing changed.

The pipeline doesn't recover what it didn't observe, and it holds a small
scope, not all of EDGAR. One assumption holds this record up and is still to
be shown: that a filing's archived files never change after acceptance
(investigation question Q1).

## Times

| Time | Where it comes from | After an outage |
| --- | --- | --- |
| Acceptance | The filing's header, `ACCEPTANCE-DATETIME` (SRC-1), with `submissions`' `acceptanceDateTime` beside it (SRC-2); both kept as given, since the header's time zone is still to be established | Unaffected: it is read from the filing whenever the filing is fetched |
| Public availability | Not stated by the SEC. It lies between acceptance and first seen, and the record measures the delay (Q4, [0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)) | Estimated from acceptance and the measured delay |
| First seen | For each source, the pipeline's first poll that showed the filing, after the last that didn't | Bounded: after the last successful poll before the gap, and at or before the first poll after it; flagged as a gap |

The record keeps every time raw, as its source gives it, and interprets
time zones in queries
([0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)):
for each fetch, when it was sent and when the response arrived, with the
response's `Date` and `Last-Modified`; for each filing, both acceptance
times as given; and for each source, the polls that bound when it first
showed the filing. Each lag, from acceptance to each source and on to the
facts, is then a query, and its distribution a dashboard.

A fact also has a valid time: the fiscal period or instant it describes.

An "as of T" query uses acceptance as transaction time. For each fact's key
(company, concept, unit, period, and dimensions), it returns the value from
the latest filing accepted at or before T. A restated value appears from its
filing's acceptance on, and earlier answers stay reproducible. First-seen
time answers a different question, what this system held at T, and its gap
flag says when that answer is approximate. The time of recovery is never
put in place of the SEC's.

## Components

```text
Source, Backfill, SecAccess: custom resources
    │ reconciled by
    ▼
scheduler ──jobs──▶ NATS JetStream ──▶ fetcher (one active, by Lease) ──▶ SEC
                         │                ├─ bodies by SHA-256 ──▶ Garage
                         │                └─ a record per fetch ──▶ PostgreSQL
                         ├──▶ discoverer: filings, first seen, gaps ─▶ PostgreSQL
                         └──▶ parse workers: XBRL instance → facts ──▶ PostgreSQL

exporter: facts ──▶ Parquet in Garage      query API: "as of T" ──▶ PostgreSQL
```

| Component | Role | Language | On the cluster |
| --- | --- | --- | --- |
| Scheduler | A controller: queues each `Source`'s polls at its cadence, hands each `Backfill` to the discoverer, and keeps their status ([0007](decisions/0007-control-the-pipeline-through-custom-resources.md), [`spec/notices.md`](../spec/notices.md#8-backfills)) | Go | A Deployment of two, one active through leader election |
| Fetcher | The only component that talks to the SEC: the user agent, the rate limit, retries, and the stop on a `403`, kept in `SecAccess`; stores each body and appends each record | Go | A Deployment of two, one active through a Lease ([0005](decisions/0005-send-sec-requests-through-one-active-fetcher.md)); the only pod with the `SEC_USER_AGENT` Secret |
| Discoverer | Compares each new version of a notice source with the last; records each new filing with its first-seen time and any gap's bounds, and queues fetches of its header, index, and XBRL instance; lists each `Backfill`'s filings and queues their fetches ([`spec/notices.md`](../spec/notices.md)) | Python | A Deployment |
| Parse workers | Read a stored XBRL instance and write its facts with the filing's acceptance time, under an import manifest ([§11](../spec/snapshots.md#11-imports)) | Python | A Deployment, scaled by queue depth |
| Exporter | Writes the facts to Parquet in Garage, deterministically, and records each file's SHA-256 | Python | A CronJob |
| Query API | Answers "as of T" | Go | A stateless Deployment |
| NATS JetStream | Durable queues and events ([0003](decisions/0003-queue-work-in-nats-jetstream.md)) | — | A StatefulSet of three |
| Garage | Bodies by SHA-256, exports, and database backups ([0002](decisions/0002-store-bodies-in-garage.md)) | — | A StatefulSet of three, with a post-install Job that sets its layout |
| PostgreSQL 18 | Records, filings, coverage and gaps, and facts ([0004](decisions/0004-keep-the-record-in-postgresql-18.md)) | — | A CloudNativePG cluster of three, pinned to 18 |
| Monitoring | Metrics, dashboards, and alerts, then traces and logs ([0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)) | — | kube-prometheus-stack, then Tempo, then Loki with Alloy |

Services that read what EDGAR publishes are written in Python, and services
that move bytes or keep the rules for talking to the SEC in Go
([0006](decisions/0006-write-the-services-in-go-and-python.md)).

Everything is installed with Helm on kind
([0001](decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md)), by four
charts in order: the Prometheus operator's definitions, cert-manager, the
operators with the other definitions, and last the umbrella chart, with
everything else
([0011](decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md)).
Each definition is on the cluster before any resource of its kind, and Helm
upgrades it with its chart.

## One filing, end to end

1. The scheduler queues a fetch of each company's `submissions`, at its
   `Source`'s cadence.
2. The fetcher holding the Lease sends the request within the rate limit,
   stores the body in Garage if it is new, and appends the fetch's record.
3. The discoverer finds a new accession number in the new version. It
   records the filing, with this poll as its first-seen time and the
   previous successful poll as the lower bound, flagging a gap if polls were
   missed between them, and queues fetches of the filing's header and index
   page.
4. The fetcher fetches them, and the discoverer reads the acceptance time
   from the header, and from the index page the XBRL instance, whose fetch
   it queues.
5. A parse worker writes an import manifest, reads the instance from
   Garage, and inserts the facts in one transaction with the filing's
   acceptance time.
6. "As of" queries for any time at or after the acceptance now include the
   facts.

A message delivered twice repeats its step harmlessly: bodies are named by
their SHA-256 and filings by their accession numbers. One trace follows the
filing through every step, across both languages
([0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)).

## Two modes, one pipeline

A mode is a cadence on each `Source`, not a different deployment
([0007](decisions/0007-control-the-pipeline-through-custom-resources.md)).

- **Continuous.** Sources are polled at the cadence each `Source` holds,
  which the owner sets in the chart's values. Each company's `submissions`
  starts at 10 seconds
  ([0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)).
  Three companies at that cadence is 0.3 requests a second against a limit
  of 5, but each poll downloads the whole body, since `submissions` sends no
  validators (SRC-4). The cadence changes as the record shows how soon each
  source shows a new filing.
- **Daily.** Sources are polled once a day, and the same workers drain each
  round.

Both write the same records. A Source polled once a day is covered between
its polls, as one polled every 10 seconds is, so a day in daily mode reads
as continuous operation whose filings' first-seen times are each bounded
within a day.

## Controls

The pipeline is controlled through custom resources, never through the
dashboard
([0007](decisions/0007-control-the-pipeline-through-custom-resources.md)):

- **`Source`:** pause one feed or change its cadence. A new `Source` widens
  the scope.
- **`Backfill`:** fetch a company's past filings over a date range, with
  progress in its status.
- **`SecAccess`:** pause every request to the SEC. After a `403`, the
  fetcher records the stop here, and fetching resumes only when the owner
  acknowledges it.

The owner changes them through the umbrella chart's values with
`helm upgrade`, as everything on the cluster is changed. The chart refuses a
configuration whose `Source` resources together would poll faster than half
the fetcher's limit, before anything reaches the cluster
([0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)).
RBAC lets the owner change the resources and the services change only their
status, so each control is kept by the API server, survives restarts, and
shows in `kubectl get` and in Grafana. While fetching is paused or stopped,
nothing is queued, and the time is recorded as a gap.

## Outages and recovery

- Queued work survives restarts, and work not acknowledged is delivered
  again.
- The sources are cumulative. Each `submissions` file in the baseline held
  about 1,000 recent filings, and published daily indexes stay in EDGAR's
  archive, so after an outage the pipeline catches up from them. Each
  filing first seen after the outage carries the gap's bounds.
- A fetcher handover is a short gap, and a stop after a `403` carries over
  to the standby through `SecAccess`. A database failover pauses parsing,
  not fetching ([0003](decisions/0003-queue-work-in-nats-jetstream.md)).
- On kind, outages are caused on purpose: deleting pods, scaling the
  scheduler to zero, pausing a `Source`, blocking the SEC with a
  NetworkPolicy. Each drill shows the pipeline recovering and the gaps it
  recorded.

## Monitoring

kube-prometheus-stack collects the pipeline's metrics, and Grafana shows
them read-only
([0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)).
The pipeline reports:

- for each source, polls that succeeded and failed, and the time since the
  last success;
- for each filing and each source, the delay from acceptance to first seen;
- open gaps and their lengths;
- queue depth, redeliveries, and consumer lag;
- the fetcher's request rate, retries, `403`s, and which pod holds the
  Lease;
- the database's replication lag, failovers, and the age of its last
  backup;
- each `Source`'s status and each `Backfill`'s progress, through
  kube-state-metrics.

Grafana also reads the record from PostgreSQL, through views made for it:
coverage over time with gaps as bands, each source's lag from acceptance
as a distribution, and each filing's times from acceptance to facts
available. Alerts fire while requests are stopped after a `403`, while a
source that's polling has a gap longer than its cadence, or has gone as
long since its first poll without a successful one, and while the latest
backup is more than a day old, and `promtool` tests them in CI.
[`spec/telemetry.md`](../spec/telemetry.md) gives the metrics, the views,
and the alerts.

Tempo comes next, with one trace per filing from the poll that first saw it
to its facts, or for a Backfill's filing from its listing, carried through
NATS in message headers. A Backfill's filing whose jobs are published again,
when its list's event is delivered again, gets another trace, linked to the
same list ([TRACE-4](../spec/telemetry.md#9-traces)). Loki, fed by Alloy,
follows, with logs that carry the accession number.

## Testing on kind

- Unit tests still never reach the network.
- A fake EDGAR in the cluster serves stored versions from the git store, so
  end-to-end tests install the whole chart on kind in CI without the SEC.
  It records every request to its server, so the same checks of the
  fetching rules run against `snapshot.py` and the Go fetcher
  ([0006](decisions/0006-write-the-services-in-go-and-python.md)).
  [`spec/fake-edgar.md`](../spec/fake-edgar.md) specifies it, and
  [`spec/deployment.md`](../spec/deployment.md#the-fake-edgar) how CI
  installs it.
- The scheduler's controller is tested against a real API server with
  envtest, and the alert rules with `promtool test rules`.
- Drills delete the active fetcher, fail over the database, and block the
  SEC, then check that the record shows each gap and lost nothing.
- A rebuild drops the facts and imports them again from stored bodies; the
  "as of" answers and the export's hashes are then compared with the
  originals.

## What the specifications must say

The normative parts become specifications, one per component or boundary:

- **`spec/snapshots.md`, a new major version:** the store's guarantees on
  Garage and PostgreSQL as well as git; the rate limit and the one-process
  rule across the cluster, through the Lease (FETCH-6, FETCH-7); the
  user agent from a Secret (FETCH-2, ID-1); how `data/scope.json`
  relates to `Source` resources; each response's `Date` and arrival time
  ([0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md));
  and how the records of the git store's scope reach `data/`, so findings
  can cite them: the owner decided that the pipeline writes them there in
  the existing format, with their bodies.
- **Notices:** the discoverer's messages; each filing's times, raw, with
  the polls that bound when each source first showed it; coverage; gaps;
  and the queries that give each lag.
- **Facts:** the fact table, its times, and what "as of T" returns.
- **The export:** its format, its determinism, and its hashes, answering the
  store's open question 3.
- **Controls:** the `Source`, `Backfill`, and `SecAccess` resources, their
  schemas, including each `Source`'s cadence and its bounds, which the
  chart checks before it applies them, and who may change what.
- **Telemetry:** the metrics and traces each service emits, which the
  dashboards and alerts rely on, and the alerts themselves.
- **Deployment:** the charts and the order they're installed in; pinned
  versions, such as PostgreSQL's major version 18; each component's
  replicas, storage, credentials, and placement; and the cluster store's
  bucket, database, table, and streams, and the fetcher's Lease; checked by
  chart tests.

## What waits on the investigation

- Which acceptance time to trust, and its time zone (SRC-1, SRC-2).
- Whether to keep each company's `submissions` as the notice source, add
  EDGAR's latest-filings feed, or change the cadence
  ([0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)):
  how soon each shows a new filing (Q4), which the pipeline's record
  measures once it runs.
- How fiscal periods map to facts (Q2, SRC-3), and how restatements appear
  (Q3).
- Whether archived files ever change (Q1).
- Sizes, and how long a backfill takes (Q5).

## Decisions

See [`docs/decisions/`](decisions/) for each record and its status.
Decisions still to come: scaling workers by queue depth, and the XBRL
parser. How the documents are published as a site is decided in
[0012](decisions/0012-publish-the-documents-as-a-site-built-with-zensical.md).
