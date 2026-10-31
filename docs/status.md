# Status

Which parts of the pipeline are built, and which are only specified,
decided, or planned. Each part's status is derived from the
[plan](plan.md), the specifications' Status lines, and the
[decision records](decisions/README.md), never written by hand
([STATUS-1](../spec/site.md#6-status)). `scripts/site_status.py` writes the
generated columns, and a test fails when they're out of date
([STATUS-3](../spec/site.md#6-status)). The pipeline reads filings from the
U.S. Securities and Exchange Commission's EDGAR system.

## What each status means

A part's status is the furthest of these it has reached
([STATUS-1](../spec/site.md#6-status)):

- **Built:** every task in the plan that builds it is done.
- **Specified:** every task that specifies it is done, so specifications
  govern it, at the versions the table gives.
- **Decided:** every decision record that chooses it is Accepted.
- **Planned:** none of these yet.

When some of a later stage's tasks are done, but not all, the status says
so after it, as "Decided; specified in part". The tasks that decide or
revisit a part, such as one that writes a decision record not yet written,
count toward no status. `edgar-asof`'s tasks are in its own repository, so
it's Built once `uv.lock` pins a release of it; its row names the releases
the plan's tasks wait on.

## The parts

<!-- generated: parts -->
| Part | Status | Decision records | Specifications | Tasks that specify it | Tasks that build it | Tasks that decide or revisit it |
| --- | --- | --- | --- | --- | --- | --- |
| Scheduler | Specified; built in part | [0006](decisions/0006-write-the-services-in-go-and-python.md), [0007](decisions/0007-control-the-pipeline-through-custom-resources.md) | [`controls.md`](../spec/controls.md) 1.0.0, [`snapshots.md`](../spec/snapshots.md) 1.1.0, [`notices.md`](../spec/notices.md) 0.1.1, [`telemetry.md`](../spec/telemetry.md) 0.1.0, [`deployment.md`](../spec/deployment.md) 2.0.0 | T14 (done), T15 (done), T16 (done), T19 (done), T34 (done), T48 (done) | T22 (done), T24 (not done) | — |
| Fetcher | Built | [0005](decisions/0005-send-sec-requests-through-one-active-fetcher.md), [0006](decisions/0006-write-the-services-in-go-and-python.md) | [`snapshots.md`](../spec/snapshots.md) 1.1.0, [`controls.md`](../spec/controls.md) 1.0.0, [`telemetry.md`](../spec/telemetry.md) 0.1.0, [`deployment.md`](../spec/deployment.md) 2.0.0 | T14 (done), T15 (done), T19 (done), T34 (done), T49 (done) | T23 (done) | — |
| Discoverer | Specified | [0006](decisions/0006-write-the-services-in-go-and-python.md), [0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md), [0013](decisions/0013-measure-each-feeds-lag-in-a-bounded-study-then-keep-only-changes.md) | [`notices.md`](../spec/notices.md) 0.1.1, [`telemetry.md`](../spec/telemetry.md) 0.1.0, [`deployment.md`](../spec/deployment.md) 2.0.0 | T16 (done), T19 (done), T38 (done), T44 (done) | T24 (not done) | T11 (not done), T45 (not done) |
| Parse workers | Planned; decided in part | [0006](decisions/0006-write-the-services-in-go-and-python.md), [0009](decisions/0009-read-edgar-through-a-public-point-in-time-library.md), [0014](decisions/0014-scale-the-parse-workers-with-keda-by-their-consumers-backlog.md) (not Accepted) | — | T17 (not done), T32 (not done) | T25 (not done) | T10 (not done), T12 (not done) |
| Exporter | Decided | [0002](decisions/0002-store-bodies-in-garage.md), [0006](decisions/0006-write-the-services-in-go-and-python.md) | — | T18 (not done), T32 (not done) | T27 (not done) | — |
| Query API | Decided | [0006](decisions/0006-write-the-services-in-go-and-python.md), [0009](decisions/0009-read-edgar-through-a-public-point-in-time-library.md) | — | T17 (not done), T32 (not done) | T26 (not done) | — |
| NATS JetStream | Built | [0003](decisions/0003-queue-work-in-nats-jetstream.md) | [`deployment.md`](../spec/deployment.md) 2.0.0 | T13 (done), T34 (done), T38 (done) | T20 (done) | — |
| Garage | Built | [0002](decisions/0002-store-bodies-in-garage.md) | [`deployment.md`](../spec/deployment.md) 2.0.0, [`snapshots.md`](../spec/snapshots.md) 1.1.0 | T13 (done), T15 (done), T34 (done), T38 (done) | T20 (done) | — |
| PostgreSQL 18 | Built | [0004](decisions/0004-keep-the-record-in-postgresql-18.md) | [`deployment.md`](../spec/deployment.md) 2.0.0, [`snapshots.md`](../spec/snapshots.md) 1.1.0 | T13 (done), T15 (done), T34 (done), T38 (done) | T20 (done) | — |
| Monitoring: metrics, alerts, and dashboards | Decided; specified in part; built in part | [0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md) | [`telemetry.md`](../spec/telemetry.md) 0.1.0, [`deployment.md`](../spec/deployment.md) 2.0.0 | T19 (done), T32 (not done), T38 (done) | T20 (done), T28 (not done), T33 (not done) | — |
| Monitoring: traces | Specified | [0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md) | [`telemetry.md`](../spec/telemetry.md) 0.1.0 | T19 (done) | T29 (not done) | — |
| Monitoring: logs | Decided | [0008](decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md) | — | — | T30 (not done) | — |
| `edgar-asof` | Decided | [0009](decisions/0009-read-edgar-through-a-public-point-in-time-library.md) | — | — | `edgar-asof`'s first release (T24); an `edgar-asof` release that specifies the fact model (T17); an `edgar-asof` release that reads XBRL instances (T25); pinned in `uv.lock`: none | — |
| The snapshot store in git | Built | — | [`snapshots.md`](../spec/snapshots.md) 1.1.0 | T15 (done) | T35 (done) | — |
| The snapshot store in the cluster | Built | [0002](decisions/0002-store-bodies-in-garage.md), [0004](decisions/0004-keep-the-record-in-postgresql-18.md), [0005](decisions/0005-send-sec-requests-through-one-active-fetcher.md), [0013](decisions/0013-measure-each-feeds-lag-in-a-bounded-study-then-keep-only-changes.md) | [`snapshots.md`](../spec/snapshots.md) 1.1.0, [`deployment.md`](../spec/deployment.md) 2.0.0 | T15 (done), T34 (done) | T20 (done), T23 (done) | T45 (not done), T46 (not done) |
| The transfer from the cluster to git | Built | [0010](decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md) | [`snapshots.md`](../spec/snapshots.md) 1.1.0, [`deployment.md`](../spec/deployment.md) 2.0.0 | T15 (done), T50 (done) | T36 (done) | — |
| The controls | Specified; built in part | [0007](decisions/0007-control-the-pipeline-through-custom-resources.md) | [`controls.md`](../spec/controls.md) 1.0.0 | T14 (done) | T22 (done), T23 (done), T24 (not done) | — |
| The charts | Built | [0001](decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md), [0011](decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md) | [`deployment.md`](../spec/deployment.md) 2.0.0 | T13 (done), T34 (done), T38 (done) | T20 (done) | — |
| The fake EDGAR | Built | [0001](decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md), [0006](decisions/0006-write-the-services-in-go-and-python.md) | [`fake-edgar.md`](../spec/fake-edgar.md) 0.1.0, [`deployment.md`](../spec/deployment.md) 2.0.0 | T47 (done) | T21 (done) | — |
| The drills | Planned | — | — | — | T31 (not done) | — |
| The documentation site | Specified; built in part | [0012](decisions/0012-publish-the-documents-as-a-site-built-with-zensical.md) | [`site.md`](../spec/site.md) 0.1.0 | T39 (done) | T40 (done), T41 (done), T42 (done), T43 (not done) | — |
<!-- end generated: parts -->

## The investigation

The investigation's tasks answer its
[questions](../investigation/edgar.md#questions), with findings the
specifications cite. They build no part, so they're listed here on their
own.

<!-- generated: investigation -->
| Task | Questions | Done |
| --- | --- | --- |
| T1: Fiscal periods and joins, from the baseline (Q2). | Q2 | October 10, 2026 |
| T2: Restated and repeated values, from the baseline (Q3). | Q3 | October 11, 2026 |
| T3: Sizes and the pace of a backfill, from the baseline (Q5). | Q5 | October 11, 2026 |
| T4: The first refetch (Q1, Q4). | Q1, Q4 | October 16, 2026 |
| T5: The latest-filings feed (Q4). | Q4 | October 18, 2026 |
| T6: Microsoft's next 10-Q (Q2, Q3). | Q2, Q3 | Not done |
| T7: Apple's next 10-K (Q2, Q3). | Q2, Q3 | Not done |
| T8: What the refetches show (Q1, Q4, Q5). | Q1, Q4, Q5 | Not done |
| T9: Walmart's next 10-Q (Q2, Q3). | Q2, Q3 | Not done |
<!-- end generated: investigation -->
