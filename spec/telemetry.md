# Specification: telemetry

**Status:** Version 0.1.0, draft, October 14, 2026. This document governs
what the pipeline reports about itself while it observes filings: the
metrics the scheduler, the fetcher, and the discoverer serve; the metrics
kube-state-metrics exports from the controls' status; what Prometheus
scrapes; the alerts; the views of the record that Grafana reads, which are
defined in [`schemas/dashboards.sql`](schemas/dashboards.sql); and the trace
context the services carry through NATS. Where code and this document
disagree, the code is wrong or this document needs a new version; say which
before changing either.

It rests on the decisions to monitor with kube-prometheus-stack and a
read-only Grafana, then Tempo and Loki ([0008]); to have every service
serve Prometheus metrics and send traces with OpenTelemetry ([0006]); to
send every request to the SEC through one active fetcher, which raises an
alert after a `403` ([0005]); to control the pipeline through custom
resources whose status kube-state-metrics exports ([0007]); and to measure
each lag from the record ([0010]). It reports on what the snapshot store's
version 1.0.0 ([`snapshots.md`](snapshots.md)), the controls' version 1.0.0
([`controls.md`](controls.md)), and the notices' version 0.1.0
([`notices.md`](notices.md)) specify, and on the database that the
deployment's version 1.0.0 installs ([`deployment.md`](deployment.md)). The
pipeline reads filings from the U.S. Securities and Exchange Commission's
EDGAR system; nothing here depends on how EDGAR behaves.

## 1. Guarantees

Each guarantee holds because of the statements it lists ([§3](#3-statement-ids)).

1. **What each service does, and each control's status, is a metric that
   Prometheus scrapes from every replica.** [METRIC-1], [METRIC-3],
   [METRIC-4], [METRIC-5], [STATE-1], [SCRAPE-1], [SCRAPE-2].
2. **The owner is alerted while requests to the SEC are stopped after a
   `403`, while a Source that's polling has an open gap longer than its
   cadence, and while the database's latest backup is more than a day
   old.** [ALERT-1], [ALERT-2], [ALERT-3], [ALERT-4].
3. **Grafana reads the record only through views made for it,** which give
   each Source's coverage, its gaps, and its lags, and it can change
   nothing. [VIEW-1], [VIEW-2], [VIEW-3], [VIEW-4], [VIEW-5], [VIEW-6].
4. **One trace follows each filing,** from the poll that first showed it,
   or for a Backfill's filing from its listing, through each of its fetches
   and readings to its notice. [TRACE-1], [TRACE-2], [TRACE-3], [TRACE-4],
   [TRACE-5].
5. **Telemetry carries no secret, and adds nothing to the requests to the
   SEC:** no credential, user agent, or request header in a label or a
   span, and no trace context in a request. Each label takes values from a
   small set.
   [METRIC-2], [TRACE-5], [TRACE-6].

## 2. Terms

- **The services:** the scheduler, the fetcher, and the discoverer
  ([components](../docs/design.md#components)). **The active replica** of
  the scheduler or the fetcher is the one that holds its Lease ([0005],
  [0007]); each replica of the discoverer is active.
- **Metric**, **label**, **series**, **counter**, and **gauge** are as
  Prometheus has them. A metric **is absent** when no series of it is
  served.
- **Unix time:** seconds since 1970-01-01T00:00:00Z.
- **kube-state-metrics:** the exporter that kube-prometheus-stack installs,
  which exports the state of Kubernetes resources as metrics ([0007],
  [0008]).
- **An alert fires** when Prometheus evaluates its rule and finds the
  rule's condition true, and has found it true for at least the rule's
  `for`, as Prometheus's alerting rules have it.
- **Source**, **Backfill**, **SecAccess**, **poll**, **cadence**, **the
  chart**, **the release namespace**, and **requests are allowed** are as
  the controls have them ([§2 of the controls](controls.md#2-terms)).
  **Job**, **event**, **record**, and **timestamp** are as the store has
  them ([§2 of the store](snapshots.md#2-terms)), and **order**,
  **notice**, **sighting**, **gap**, and **open gap** as the notices have
  them ([§2 of the notices](notices.md#2-terms)).
- **Span**, **trace**, **root span**, **span link**, and **sampled** are as
  OpenTelemetry has them; **trace context** is a span's identity as the W3C
  Trace Context headers carry it.

## 3. Statement IDs

Every normative statement in this document has an ID, such as `ALERT-2`: an
area and a number. The areas `METRIC`, `STATE`, `SCRAPE`, `ALERT`, `VIEW`,
and `TRACE` belong to this document. An ID is assigned once and never
reused or renumbered. A statement that is dropped stays in the
[index](#10-index), marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of a schema in [`schemas/`](schemas/), of
  the chart as committed and what it renders, or of the record. Of a schema
  or the chart, tests check it by reading the schema or rendering the chart,
  without a cluster or the network. Of the record, it holds whenever no
  service is writing it, and tests on kind check it.
- A **rule** is how a service, kube-state-metrics, Prometheus, or the
  database behaves while it runs. Tests check a service's rules as its own
  tests do, an alert's with `promtool test rules` ([0008]), and the others
  on kind.

The index says how each statement is checked: by `tests`, or by tests on
`kind`. Every statement checked by `tests` or `kind` has at least one test
marked with its ID, and every marker names an ID in a specification's
index; Go tests carry IDs in the form the first Go test sets ([0006]). A
test, `tests/test_spec_coverage.py`, enforces both, except for a statement
whose tests a later task in the [plan](../docs/plan.md) writes. It lists
such a statement as awaiting that task, and fails if the statement has a
test or the plan marks the task done. [VIEW-1]'s tests come with this
version. The trace statements await T29, which brings traces; the others
await T28, which brings the monitoring of observing filings.

## 4. Metrics

Each service serves the metrics of its table below. A metric's name begins
`edgar_` and the service's name, a counter's ends `_total`, and a time is in
seconds, as Prometheus's conventions have them. Each replica counts only
what it did itself since it started, so a dashboard sums a counter's rates
over the replicas. Each service's client library also serves metrics of its
own, such as its process's, which aren't this document's.

A label's values:

| Label | Values |
| --- | --- |
| `source` | A Source's name. |
| `url` | A Source's `spec.url`. |
| `backfill` | A Backfill's name. |
| `subject` | A subject of a stream the service reads ([JOB-7], [MSG-2]). |
| `code` | An HTTP status, three digits, or `none`. |
| `kind`, `file`, `outcome`, `bounded` | As the metric's row says. |

- **METRIC-1** (rule) Each replica of each service serves its metrics in
  Prometheus's text format, over HTTP at `/metrics` on its container port
  named `metrics`, from when it starts, whether or not it's the active
  replica.
- **METRIC-2** (rule) Each label takes only the values the table above
  gives it. No label carries an accession number, a job's or a record's
  `id`, a URL other than a Source's, the user agent, a credential, or a
  request header ([ID-1]).

### The scheduler

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `edgar_scheduler_leader` | gauge | | 1 while this replica is the active one, and 0 otherwise. |
| `edgar_scheduler_polls_queued_total` | counter | `source` | Polls it queued, each as a job ([JOB-2]). |
| `edgar_scheduler_polls_skipped_total` | counter | `source` | Polls it skipped, because it couldn't queue them before the next was due ([SOURCE-2]). |
| `edgar_scheduler_orders_total` | counter | | Orders it published ([ORDER-1]). |
| `edgar_scheduler_messages_refused_total` | counter | `subject` | Notices it acknowledged without acting on them ([MSG-2]). |

- **METRIC-3** (rule) The scheduler serves the metrics of its table, as the
  table says.

### The fetcher

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `edgar_fetcher_leader` | gauge | | 1 while this replica holds the fetcher's Lease ([FETCH-16]), and 0 otherwise. |
| `edgar_fetcher_requests_total` | counter | `kind`, `code` | Requests it sent to the SEC, each attempt of each fetch ([FETCH-8]), by its job's `kind`, and the response's status, or `none` if no response arrived. |
| `edgar_fetcher_fetches_total` | counter | `kind`, `outcome` | Records it added ([FETCH-14]), by its job's `kind`, and `version` if the record names a version, or `none` if it doesn't. |
| `edgar_fetcher_polls_total` | counter | `source`, `outcome` | Of those, the records of polls, by the Source its job names. |
| `edgar_fetcher_jobs_expired_total` | counter | | Jobs it acknowledged without a request because they had expired ([JOB-5]). |
| `edgar_fetcher_jobs_held` | gauge | `kind` | The jobs of each `kind` that the stream `JOBS` holds, those waiting and any being fetched, as JetStream counted them at most 10 seconds before. Only the active replica serves it. |
| `edgar_fetcher_clock_skew_seconds` | gauge | | How far its clock is from the SEC's, at the least, by the latest response it received with a `Date` ([below](#the-fetchers-clock)). Absent until it receives one. |
| `edgar_fetcher_messages_refused_total` | counter | `subject` | Jobs it acknowledged without a request because they didn't validate, or their `kind` wasn't their subject's ([JOB-7]). |

Each fetch adds one record ([FETCH-14]), so its retries are its requests
less one, and the rate of retries is the difference of the first two
counters' rates.

#### The fetcher's clock

Each lag is measured by the fetcher's clock ([0010]), so the fetcher checks
its clock against each response's `Date`. A server stamps `Date` with the
second in which it generated the response ([RFC 9110, §6.6.1]), at some
moment after the request was sent and before the response arrived. So if
`D`, the instant a response's `Date` names, is the start of that second,
the fetcher's clock was ahead of the server's by more than
`requested_at − D − 1` seconds, and by at most `received_at − D`. The skew
is the value of that range nearest zero: positive when the fetcher's clock
is ahead, negative when it's behind, and zero when the response allows
clocks that agree. Whether the SEC's `Date` is when the response was
generated is still to be shown ([open question 1](#11-open-questions)).

- **METRIC-4** (rule) The fetcher serves the metrics of its table, as the
  table says. For each response whose `Date` is an HTTP date, naming the
  instant `D`, it sets `edgar_fetcher_clock_skew_seconds` to
  `max(requested_at − D − 1, min(0, received_at − D))`, in seconds.

### The discoverer

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `edgar_discoverer_messages_total` | counter | `subject` | Events and orders it acted on and acknowledged ([DISC-1]). |
| `edgar_discoverer_messages_refused_total` | counter | `subject` | Those it acknowledged without acting on them ([MSG-2]). |
| `edgar_discoverer_readings_total` | counter | `kind`, `outcome` | Readings it added ([SEEN-1], [FILE-3], [LIST-2]), by `kind`, `list`, `header`, or `index`, and `read`, or `error` if the reading's `error` is set. |
| `edgar_discoverer_sightings_total` | counter | `source`, `bounded` | Sightings it added ([SEEN-2]), by Source, and `true` if the sighting has a `last_not_shown`, or `false` if it doesn't. |
| `edgar_discoverer_jobs_queued_total` | counter | `kind`, `file` | Jobs it queued, by `kind`, `filing` or `backfill`, and `file` ([§7 of the notices](notices.md#7-a-filings-files)). |
| `edgar_discoverer_jobs_failed_total` | counter | `kind`, `file` | Events of jobs that `jobs` holds whose record names no version: fetches of a filing's files, or of a Backfill's lists, that ended without one ([FILE-4], [LIST-3]). |
| `edgar_discoverer_notices_total` | counter | `kind` | Notices it published, by `kind`, `filing`, `gap`, or `backfill`. |
| `edgar_discoverer_open_gap_since_seconds` | gauge | `source`, `url` | For each Source and URL with an open gap, when it began, as Unix time ([GAP-2]). Absent for one with none. |

- **METRIC-5** (rule) The discoverer serves the metrics of its table, as the
  table says. It reads the open gaps from `gaps` at least every 10 seconds,
  as [GAP-3] needs, and sets `edgar_discoverer_open_gap_since_seconds` from
  each reading.

## 5. Metrics of the controls

kube-state-metrics exports the state of the three resources from their
fields ([0007]). Each metric is a gauge. A timestamp is exported as Unix
time, to the second, and a metric whose field is absent is absent, but for
`paused`, which is 0 when absent, as the resources have it. A condition's
metric is 1 while its `status` is `True`, and 0 otherwise.

A Source's metrics, each with the labels `namespace`, `source`, its name,
and `url`, its `spec.url`:

| Metric | Field |
| --- | --- |
| `edgar_source_cadence_seconds` | `spec.cadenceSeconds` |
| `edgar_source_paused` | `spec.paused` |
| `edgar_source_last_poll_time_seconds` | `status.lastPollTime` |
| `edgar_source_last_successful_poll_time_seconds` | `status.lastSuccessfulPollTime` |
| `edgar_source_open_gap_since_seconds` | `status.openGapSince` |
| `edgar_source_condition` | Each of `status.conditions`, with the labels `type` and `reason` |

A Backfill's, each with the labels `namespace` and `backfill`, its name:

| Metric | Field |
| --- | --- |
| `edgar_backfill_filings` | `status.filings` |
| `edgar_backfill_filings_fetched` | `status.filingsFetched` |
| `edgar_backfill_filings_failed` | `status.filingsFailed` |
| `edgar_backfill_start_time_seconds` | `status.startTime` |
| `edgar_backfill_completion_time_seconds` | `status.completionTime` |
| `edgar_backfill_condition` | Each of `status.conditions`, with the labels `type` and `reason` |

The SecAccess's, each with the label `secaccess`, its name:

| Metric | Field |
| --- | --- |
| `edgar_secaccess_paused` | `spec.paused` |
| `edgar_secaccess_acknowledged_stop_time_seconds` | `spec.acknowledgedStopAt`, also as the label `at` |
| `edgar_secaccess_stop_time_seconds` | `status.stop.at`, also as the label `at` |

The label `at` gives the timestamp as the SecAccess holds it, to the
millisecond. Each timestamp has one form
([§2 of the controls](controls.md#2-terms)), so a stop and an
acknowledgement are the same instant exactly when their `at` labels are
equal, though their values, to the second, can be equal when they aren't.

- **STATE-1** (rule) In the installed charts, kube-state-metrics exports,
  for each Source and Backfill in the release namespace and for the
  SecAccess, the metrics of its kind's table, from the fields it names, as
  this section says, and no other metric whose name begins
  `edgar_source_`, `edgar_backfill_`, or `edgar_secaccess_`.

## 6. Scraping

Each component's monitors live in the umbrella chart beside it ([0008]).
kube-prometheus-stack already scrapes kube-state-metrics.

- **SCRAPE-1** (invariant) The umbrella chart renders a `PodMonitor` for
  each service, which selects its pods and scrapes `/metrics` on their port
  named `metrics`, and one for the database's instances, which scrapes
  their port named `metrics`, where each serves the Barman Cloud plugin's
  metrics ([PG-4]). The chart's Prometheus selects each.
- **SCRAPE-2** (rule) In the installed charts, Prometheus scrapes every
  replica of each service, every instance of the database, and
  kube-state-metrics, and each target is up.

## 7. Alerts

The alerts the design names ([monitoring](../docs/design.md#monitoring)):

| Alert | `severity` | Fires | `for` |
| --- | --- | --- | --- |
| `SecRequestsStopped` | `critical` | While the SecAccess has a stop the owner hasn't acknowledged | None |
| `SourceGap` | `warning` | For each Source that's polling, while its URL has an open gap longer than its cadence | 2 minutes |
| `DatabaseBackupStale` | `warning` | While the database's latest base backup is more than a day old, or it has none | 1 hour |

A `403` stops requests until the owner acknowledges it ([ACCESS-3]), so
`SecRequestsStopped` fires from the stop until the acknowledgement, not
only at the `403`. It compares the two by their `at` labels, as the
controls compare them, so an acknowledgement that differs from the stop
only in its milliseconds leaves it firing. `SourceGap` is silent for a Source that the owner paused,
or that requests being paused or stopped keep from polling: the record
still shows each such gap, and a stop has its own alert. Its `for` covers
the moments after polling resumes, before the first poll closes the gap. A
gap that ended before the rule found it open shows in the views and
dashboards, not in an alert. A daily base backup finishes some minutes
after the one before it turns a day old ([PG-4]), and
`DatabaseBackupStale`'s hour lets it.

- **ALERT-1** (rule) `SecRequestsStopped` fires while
  `edgar_secaccess_stop_time_seconds` is served and no
  `edgar_secaccess_acknowledged_stop_time_seconds` with the same `at` is.
- **ALERT-2** (rule) `SourceGap` fires for a Source and URL once, for 2
  minutes, `edgar_discoverer_open_gap_since_seconds` of the Source and URL,
  at its greatest across the discoverer's replicas, has been earlier than
  the rule's evaluation by more than the Source's
  `edgar_source_cadence_seconds` with that URL, and the Source's
  `edgar_source_condition` of type `Polling` has been 1.
- **ALERT-3** (rule) `DatabaseBackupStale` fires once, for an hour, the
  greatest `barman_cloud_cloudnative_pg_io_last_available_backup_timestamp`
  that the database's instances serve, the Unix time of the latest base
  backup in Garage, or 0 before the first, has been earlier than the rule's
  evaluation by more than 86,400 seconds.
- **ALERT-4** (invariant) The umbrella chart renders the alerts of the
  table in `PrometheusRule` resources that the chart's Prometheus selects,
  each under its name, with its `for` and its `severity` label, and no other
  alert of its own.

## 8. The views Grafana reads

Grafana reads the record from PostgreSQL through a role that may read only
views made for the dashboards ([0008]). They're in the schema `dashboards`,
which [`schemas/dashboards.sql`](schemas/dashboards.sql) defines, and they
read the notices' tables and views ([`notices.md`](notices.md#4-the-record-of-notices)),
so a dashboard shows what the record holds, by the same definitions. Each
time is as the record holds it, and each length is in seconds.

| View | One row for each |
| --- | --- |
| `coverage` | Stretch of a Source's coverage of a URL, between its gaps |
| `gaps` | Gap in a Source's coverage of a URL, ended or open |
| `lags` | Sighting whose lag the record bounds |

The view `coverage` has these columns:

| Column | Meaning |
| --- | --- |
| `source` | The Source. |
| `url` | The URL its polls fetched. |
| `began_at` | The `requested_at` of the stretch's first successful poll: the Source's first of the URL, or the one that ended a gap. |
| `ended_at` | When the stretch's last successful poll's job expired: where the next gap began, or, with none after it, where the latest poll's coverage ends. |

The view `gaps` has these columns:

| Column | Meaning |
| --- | --- |
| `source` | The Source. |
| `url` | The URL its polls fetched. |
| `began_at` | When it began, as the notices' `gaps` gives it. |
| `ended_at` | When it ended; null while it's open. |
| `seconds` | Its length: to `ended_at`, or, while it's open, to when the view is read. |

The view `lags` has these columns:

| Column | Meaning |
| --- | --- |
| `source` | The Source. |
| `url` | The URL its polls fetched. |
| `accession_number` | The filing. |
| `form` | Its `form`. |
| `shown_received_at` | When the response of the first poll that showed it arrived. |
| `gap` | Whether a gap lies between the two polls that bound when the Source first showed it. |
| `listed_lag_low_seconds` | `listed_lag_low`, in seconds. |
| `listed_lag_high_seconds` | `listed_lag_high`, in seconds. |
| `header_lag_low_seconds` | `header_lag_low`, in seconds. |
| `header_lag_high_seconds` | `header_lag_high`, in seconds. |

- **VIEW-1** (invariant) [`schemas/dashboards.sql`](schemas/dashboards.sql)
  creates the schema `dashboards` and, in it, the views of this section's
  table, each with the columns its table gives it, in that order, and
  creates nothing else.
- **VIEW-2** (invariant) The pipeline's database holds the schema
  `dashboards` and its views, as `dashboards.sql` defines them.
- **VIEW-3** (rule) Grafana reads the pipeline's database as a role of its
  own, which may use the schema `dashboards` and select its views, and can
  neither read nor change any table or view that `records.sql` or
  `notices.sql` defines.
- **VIEW-4** (invariant) The view `coverage` gives each stretch of each
  Source's coverage of each URL, as the notices define coverage
  ([§5 of the notices](notices.md#5-polls-coverage-and-gaps)), with its
  columns as its table says.
- **VIEW-5** (invariant) The view `gaps` gives each row of the notices'
  `gaps` ([GAP-2]), with its columns as its table says.
- **VIEW-6** (invariant) The view `lags` gives each row of the notices'
  `lags` that has a `last_not_shown` ([LAG-1]), with its columns as its
  table says.

## 9. Traces

One trace follows each filing ([0008]). A Source's poll starts a trace,
which its fetch, the discoverer's reading of its list, and each filing the
list first shows join: the fetches of the filing's files, their readings,
and the filing's notice. Two filings a poll first shows share its trace. A
Backfill's order starts a trace for its lists, and each of its filings gets
a trace of its own, linked to the list that named it, so a Backfill of
thousands of filings isn't one trace. The parse workers will continue each
filing's trace to its facts, as the facts specification will say.

Each message carries its trace context in its NATS headers, not in its
body, so jobs, events, orders, and notices are as their schemas have them
([JOB-1], [MSG-1]).

| Attribute | Value |
| --- | --- |
| `edgar.job.id` | A job's `id`, or an event's job's. |
| `edgar.job.kind` | A job's `kind`, or an event's job's. |
| `edgar.source` | A job's `source`, an event's job's, or a gap notice's. |
| `edgar.backfill` | A job's `backfill`, an event's job's, an order's, or a backfill notice's. |
| `edgar.record.id` | An event's `id`. |
| `edgar.accession_number` | A filing notice's `accession_number`; and on the discoverer's spans of a job of a filing's file and of its event, the filing that `jobs` holds for it. |

- **TRACE-1** (rule) Each job, event, order, and notice that a service
  publishes carries, in its NATS headers, the trace context of the span
  that published it: `traceparent`, and `tracestate` if the span has one.
- **TRACE-2** (rule) The scheduler starts a trace for each poll it queues
  and each order it publishes: the span that publishes it is a root span,
  sampled.
- **TRACE-3** (rule) A service that acts on a message makes its span a
  child of the context that the message's headers carry, or a root span,
  sampled, if they carry no valid one. Each message it publishes while
  acting on it carries that span's context, but those of [TRACE-4]. A span
  is sampled when its parent is.
- **TRACE-4** (rule) When the discoverer queues the fetches of a Backfill's
  filing ([LIST-2]), it starts a trace for the filing: a root span, sampled,
  with a span link to the span of the list's event, and the filing's jobs
  carry its context.
- **TRACE-5** (rule) Each span that publishes a message, or acts on one,
  has the attributes of the table that the message gives it. No span's
  attributes or events carry the user agent, a credential, or a request
  header ([ID-1]).
- **TRACE-6** (rule) The fetcher sends the SEC no trace context: none of its
  requests has a `traceparent`, `tracestate`, or `baggage` header.
- **TRACE-7** (rule) A service exports its spans by OTLP to the endpoint
  that `OTEL_EXPORTER_OTLP_ENDPOINT` gives in its environment, and exports
  none while it's unset, carrying trace context all the same ([TRACE-1]).

## 10. Index

| ID | Kind | Checked by | Statement |
| --- | --- | --- | --- |
| METRIC-1 | rule | tests | Every replica of each service serves its metrics at `/metrics` on its port `metrics`. |
| METRIC-2 | rule | tests | Labels take only their listed values, and carry no identifier, user agent, credential, or request header. |
| METRIC-3 | rule | tests | The scheduler serves its metrics. |
| METRIC-4 | rule | tests | The fetcher serves its metrics, its clock's skew among them. |
| METRIC-5 | rule | tests | The discoverer serves its metrics, each open gap among them. |
| STATE-1 | rule | kind | kube-state-metrics exports the controls' fields as the tables say, and nothing else under their names. |
| SCRAPE-1 | invariant | tests | The chart renders a `PodMonitor` for each service and for the database's instances. |
| SCRAPE-2 | rule | kind | Prometheus scrapes every replica, every instance, and kube-state-metrics. |
| ALERT-1 | rule | tests | `SecRequestsStopped` fires while a stop isn't acknowledged. |
| ALERT-2 | rule | tests | `SourceGap` fires while a polling Source's open gap is longer than its cadence, for 2 minutes. |
| ALERT-3 | rule | tests | `DatabaseBackupStale` fires once the latest base backup has been more than a day old for an hour. |
| ALERT-4 | invariant | tests | The chart renders the three alerts, and no other of its own. |
| VIEW-1 | invariant | tests | `schemas/dashboards.sql` creates the schema `dashboards` and its three views, with their columns. |
| VIEW-2 | invariant | kind | The database holds the schema and its views. |
| VIEW-3 | rule | kind | Grafana's role may read only the views in `dashboards`. |
| VIEW-4 | invariant | kind | `coverage` gives each stretch of each Source's coverage. |
| VIEW-5 | invariant | kind | `gaps` gives each gap, with its length. |
| VIEW-6 | invariant | kind | `lags` gives each sighting whose lag is bounded, in seconds. |
| TRACE-1 | rule | tests | Each message carries the trace context of the span that published it. |
| TRACE-2 | rule | tests | Each poll and each order starts a trace. |
| TRACE-3 | rule | tests | Acting on a message continues its trace. |
| TRACE-4 | rule | tests | Each of a Backfill's filings gets a trace, linked to its list. |
| TRACE-5 | rule | tests | Spans carry the attributes of their messages, and nothing secret. |
| TRACE-6 | rule | kind | The fetcher sends no trace context to the SEC. |
| TRACE-7 | rule | tests | Spans are exported by OTLP to the endpoint given, and none without one. |

## 11. Open questions

1. **Whether the SEC's `Date` is when it generated each response.** A
   cache may serve a response with the `Date` of when it was first
   generated, which [METRIC-4]'s skew would read as the fetcher's clock
   running ahead. The records of the investigation's refetches keep each
   `date` beside its `requested_at` and `received_at` ([REC-10]), so a
   finding can show it. Until one does, the skew is shown and not alerted
   on; once one does, a minor version adds an alert on it.
2. **How often the cluster store is checked against [STORE-3] in
   operation, and the alert on a problem,** which the store leaves to this
   document ([`snapshots.md`, §13](snapshots.md#left-to-other-specifications)).
   No component checks it yet. A later version specifies the check, such
   as a CronJob that reads the bucket and the table, its metrics, and its
   alert.
3. **Sampling.** Every trace is sampled. At the cadence each company's
   Source starts at ([0010]), the polls start 25,920 traces a day, most of
   which find no filing. If Tempo's volume calls for it, a later version
   samples them, through a collector placed between the services and Tempo
   ([0008]).
4. **The views' cost.** `coverage` and `gaps` read every poll, so each read
   grows with the record: at the starting cadence, about 9.5 million polls
   a year. How fast the dashboards read them is measured on kind, and a
   later version adds what they need, such as an index or a stored summary
   of each day, if they slow.

### Left to other specifications

This version leaves to others what telemetry relies on:

- **Deployment:** Grafana's database role, its password, and its
  PostgreSQL data source ([MON-2], [SECRET-1]); `dashboards.sql`, applied
  after `notices.sql`, with [VIEW-3]'s grants; kube-state-metrics' access
  to the definitions of the three resources, beside its access to the
  resources ([RBAC-3]); and Tempo, with the endpoint the services export
  spans to ([TRACE-7], [`deployment.md`, §9](deployment.md#9-later-versions)).
- **Facts:** the parse workers', the query API's, and the exporter's
  metrics and traces; how the parse workers continue each filing's trace to
  its facts; and the view of each filing's times from acceptance to facts
  available, in a minor version of this document.
- **Later versions of this document:** the platform's own metrics, such as
  NATS's streams and consumers, with their redeliveries and consumer lag,
  Garage's, and the database's replication and failovers, and the alerts
  on them; and logs, in Loki ([0008]).

## 12. Changes to this document

Versions follow semantic versioning. Adding a statement, a metric, a label
value, a view, an alert, or a check is a minor version. Changing the meaning
of a statement or a metric, renaming or removing a metric, a label, an
alert, or a view's column, or changing what a view gives, is a major
version, since dashboards and alerts rely on each. The schemas in
[`schemas/`](schemas/) that this document names are part of it, so changing
one changes it. IDs are never reused, and a retired statement stays in the
index.

[0005]: ../docs/decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0006]: ../docs/decisions/0006-write-the-services-in-go-and-python.md
[0007]: ../docs/decisions/0007-control-the-pipeline-through-custom-resources.md
[0008]: ../docs/decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md
[0010]: ../docs/decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[RFC 9110, §6.6.1]: https://www.rfc-editor.org/rfc/rfc9110#section-6.6.1
[ACCESS-3]: controls.md#8-secaccess
[RBAC-3]: controls.md#9-who-may-change-what
[SOURCE-2]: controls.md#6-source
[MON-2]: deployment.md#monitoring
[PG-4]: deployment.md#instances-and-backups
[SECRET-1]: deployment.md#credentials
[DISC-1]: notices.md#processing
[FILE-3]: notices.md#7-a-filings-files
[FILE-4]: notices.md#7-a-filings-files
[GAP-2]: notices.md#5-polls-coverage-and-gaps
[GAP-3]: notices.md#5-polls-coverage-and-gaps
[LAG-1]: notices.md#10-times-and-lags
[LIST-2]: notices.md#8-backfills
[LIST-3]: notices.md#8-backfills
[MSG-1]: notices.md#streams
[MSG-2]: notices.md#streams
[ORDER-1]: notices.md#8-backfills
[SEEN-1]: notices.md#6-when-each-source-first-showed-each-filing
[SEEN-2]: notices.md#6-when-each-source-first-showed-each-filing
[FETCH-8]: snapshots.md#10-fetching
[FETCH-14]: snapshots.md#10-fetching
[FETCH-16]: snapshots.md#the-fetcher
[ID-1]: snapshots.md#10-fetching
[JOB-1]: snapshots.md#jobs-and-events
[JOB-2]: snapshots.md#jobs-and-events
[JOB-5]: snapshots.md#jobs-and-events
[JOB-7]: snapshots.md#jobs-and-events
[REC-10]: snapshots.md#6-records
[STORE-3]: snapshots.md#the-cluster-store
[METRIC-1]: #4-metrics
[METRIC-2]: #4-metrics
[METRIC-3]: #the-scheduler
[METRIC-4]: #the-fetchers-clock
[METRIC-5]: #the-discoverer
[STATE-1]: #5-metrics-of-the-controls
[SCRAPE-1]: #6-scraping
[SCRAPE-2]: #6-scraping
[ALERT-1]: #7-alerts
[ALERT-2]: #7-alerts
[ALERT-3]: #7-alerts
[ALERT-4]: #7-alerts
[VIEW-1]: #8-the-views-grafana-reads
[VIEW-2]: #8-the-views-grafana-reads
[VIEW-3]: #8-the-views-grafana-reads
[VIEW-4]: #8-the-views-grafana-reads
[VIEW-5]: #8-the-views-grafana-reads
[VIEW-6]: #8-the-views-grafana-reads
[TRACE-1]: #9-traces
[TRACE-2]: #9-traces
[TRACE-3]: #9-traces
[TRACE-4]: #9-traces
[TRACE-5]: #9-traces
[TRACE-6]: #9-traces
[TRACE-7]: #9-traces
