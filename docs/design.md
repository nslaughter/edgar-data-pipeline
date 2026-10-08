# Design: the EDGAR data pipeline

**Status:** Draft, October 8, 2026. This document explains the pipeline's
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
| Acceptance | The filing's header, `ACCEPTANCE-DATETIME` (SRC-1); its time zone is still to be established | Unaffected: it is read from the filing whenever the filing is fetched |
| Public availability | Not stated by the SEC; the investigation measures it (Q4) | Estimated from acceptance and the measured delay |
| First seen | The pipeline's first poll that showed the filing | Bounded: after the last successful poll before the gap, and at or before the first poll after it; flagged as a gap |

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
scheduler ──jobs──▶ NATS JetStream ──▶ fetcher (one active, by Lease) ──▶ SEC
                         │                ├─ bodies by SHA-256 ──▶ Garage
                         │                └─ a record per fetch ──▶ PostgreSQL
                         ├──▶ discoverer: filings, first seen, gaps ─▶ PostgreSQL
                         └──▶ parse workers: XBRL instance → facts ──▶ PostgreSQL

exporter: facts ──▶ Parquet in Garage      query API: "as of T" ──▶ PostgreSQL
```

| Component | Role | On the cluster |
| --- | --- | --- |
| Scheduler | Queues fetches of the notice sources at a cadence | A Deployment in continuous mode, a CronJob in daily mode; the same image |
| Fetcher | The only component that talks to the SEC: the user agent, the rate limit, retries, and the stop on a `403`; stores each body and appends each record | A Deployment of two, one active through a Lease ([0005](decisions/0005-send-sec-requests-through-one-active-fetcher.md)); the only pod with the `SEC_USER_AGENT` Secret |
| Discoverer | Compares each new version of a notice source with the last; records each new filing with its first-seen time and any gap's bounds, and queues fetches of its header, index, and XBRL instance | A Deployment |
| Parse workers | Read a stored XBRL instance and write its facts with the filing's acceptance time, under an import manifest ([§11](../spec/snapshots.md#11-imports)) | A Deployment, scaled by queue depth |
| Exporter | Writes the facts to Parquet in Garage, deterministically, and records each file's SHA-256 | A CronJob |
| Query API | Answers "as of T" | A stateless Deployment |
| NATS JetStream | Durable queues and events ([0003](decisions/0003-queue-work-in-nats-jetstream.md)) | A StatefulSet of three |
| Garage | Bodies by SHA-256, exports, and database backups ([0002](decisions/0002-store-bodies-in-garage.md)) | A StatefulSet of three, with a post-install Job that sets its layout |
| PostgreSQL 18 | Records, filings, coverage and gaps, and facts ([0004](decisions/0004-keep-the-record-in-postgresql-18.md)) | A CloudNativePG cluster of three, pinned to 18 |
| Monitoring | Metrics, dashboards, and alerts | A decision to come |

Everything is installed by one umbrella Helm chart on kind
([0001](decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md)).

## One filing, end to end

1. The scheduler queues a fetch of each company's `submissions`.
2. The fetcher holding the Lease sends the request within the rate limit,
   stores the body in Garage if it is new, and appends the fetch's record.
3. The discoverer finds a new accession number in the new version. It
   records the filing, with this poll as its first-seen time and the
   previous successful poll as the lower bound, flagging a gap if polls were
   missed between them, and queues fetches of the filing's header, index,
   and XBRL instance.
4. The fetcher fetches them, and the discoverer reads the acceptance time
   from the header.
5. A parse worker writes an import manifest, reads the instance from
   Garage, and inserts the facts in one transaction with the filing's
   acceptance time.
6. "As of" queries for any time at or after the acceptance now include the
   facts.

A message delivered twice repeats its step harmlessly: bodies are named by
their SHA-256 and filings by their accession numbers.

## Two modes, one pipeline

- **Continuous.** The scheduler is a Deployment that polls at a cadence the
  investigation will set (Q4). Polling three companies every minute is 3
  requests a minute against a limit of 5 a second, but each poll downloads
  the whole body, since `submissions` sends no validators (SRC-4).
- **Daily.** The scheduler is a CronJob that queues one round, and the same
  workers drain it.

Both write the same records, so a day in daily mode reads as continuous
operation with a long gap, which is what it is.

## Outages and recovery

- Queued work survives restarts, and work not acknowledged is delivered
  again.
- The sources are cumulative. Each `submissions` file in the baseline held
  about 1,000 recent filings, and published daily indexes stay in EDGAR's
  archive, so after an outage the pipeline catches up from them. Each
  filing first seen after the outage carries the gap's bounds.
- A fetcher handover is a short gap. A database failover pauses parsing,
  not fetching ([0003](decisions/0003-queue-work-in-nats-jetstream.md)).
- On kind, outages are caused on purpose: deleting pods, scaling the
  scheduler to zero, blocking the SEC with a NetworkPolicy. Each drill shows
  the pipeline recovering and the gaps it recorded.

## Monitoring

Whichever stack is chosen, the pipeline reports:

- for each source, polls that succeeded and failed, and the time since the
  last success;
- for each filing, the delay from acceptance to first seen;
- open gaps and their lengths;
- queue depth, redeliveries, and consumer lag;
- the fetcher's request rate, retries, `403`s, and which pod holds the
  Lease;
- the database's replication lag, failovers, and the age of its last
  backup.

A dashboard shows coverage over time, with gaps as bands. Alerts fire on a
`403`, on a gap longer than the cadence, and on a backup more than a day old.

## Testing on kind

- Unit tests still never reach the network.
- A fake EDGAR in the cluster serves stored versions from the git store, so
  end-to-end tests install the whole chart on kind in CI without the SEC.
- Drills delete the active fetcher, fail over the database, and block the
  SEC, then check that the record shows each gap and lost nothing.
- A rebuild drops the facts and imports them again from stored bodies; the
  "as of" answers and the export's hashes are then compared with the
  originals.

## What the specifications must say

The normative parts become specifications, one per component or boundary:

- **`spec/snapshots.md`, a new major version:** the store's guarantees on
  Garage and PostgreSQL as well as git; the rate limit and the one-process
  rule across the cluster, through the Lease (FETCH-6, FETCH-7); and the
  user agent from a Secret (FETCH-2, ID-1).
- **Notices:** the discoverer's messages, filings' first-seen times,
  coverage, and gaps.
- **Facts:** the fact table, its times, and what "as of T" returns.
- **The export:** its format, its determinism, and its hashes, answering the
  store's open question 3.
- **Deployment:** pinned versions, such as PostgreSQL's major version 18,
  checked by chart tests.

## What waits on the investigation

- Which acceptance time to trust, and its time zone (SRC-1, SRC-2).
- The notice source and its cadence: `submissions`, the daily indexes, or
  EDGAR's latest-filings feed, and how soon each shows a new filing (Q4).
- How fiscal periods map to facts (Q2, SRC-3), and how restatements appear
  (Q3).
- Whether archived files ever change (Q1).
- Sizes, and how long a backfill takes (Q5).

## Decisions

See [`docs/decisions/`](decisions/) for each record and its status.
Decisions still to come: the monitoring stack, the language and libraries of
the pipeline's services, scaling workers by queue depth, logs, and the
notice source and its cadence.
