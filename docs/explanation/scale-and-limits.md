# Scale and limits

<!-- status: all -->
**What's built,** as the [status page](../status.md) gives it:

- **Built:** The snapshot store in git.
- **Specified:** Scheduler; Fetcher; Discoverer; NATS JetStream; Garage; PostgreSQL 18; Monitoring: traces; The snapshot store in the cluster; The transfer from the cluster to git; The controls; The charts; The documentation site (built in part).
- **Decided:** Parse workers; Exporter; Query API; Monitoring: metrics, alerts, and dashboards (specified in part); Monitoring: logs; `edgar-asof`.
- **Planned:** The fake EDGAR; The drills.
<!-- end status -->

This page reads from the [estimates](estimates.md) which resource limits
the pipeline first as its scope grows, and at what point: with more
companies, shorter cadences, more feeds, or all of EDGAR's filers, and with
time, since the record only grows ([PAGE-6]). It uses the estimates'
scopes, inputs, and ranges, and adds none but those it marks. What the
pipeline would change to go past each limit is analysis, set apart in
blocks that say so ([PAGE-7]). The data the pipeline keeps comes from the
U.S. Securities and Exchange Commission's EDGAR system.

## The first limit

| As the scope grows | The first limit | Where it's reached |
| --- | --- | --- |
| By companies, at 10 seconds | [The polls' budget](#the-polls-budget) | At 25 companies: the chart refuses a 26th ([VAL-3]) |
| By shorter cadences | The polls' budget | At 2.5 *c* companies at a cadence of *c* seconds: the three companies at 1, 1, and 2 seconds |
| By more feeds | The polls' budget, which each feed's Sources share; first, [the host's disk](#the-hosts-disk), if a large feed's body changes at each poll | Each company's `companyfacts` at 10 seconds is 0.3 polls a second more; in the high case, 500 GB of disk is full after 18 days |
| To all of EDGAR's filers | The polls' budget, for polling each company: the notice source must change | At *N* companies, a cadence of *N* ÷ 2.5 seconds |
| With time | The host's disk, through base backups that nothing removes; at the full budget, [reading the gaps](#reading-the-gaps) may come first | 500 GB is full after 110 to 271 days at the default, 18 to 191 with a larger feed, and 22 to 91 at the full budget, where a read of `gaps` may take 10 seconds after 46 days |

So the first limit that the scope reaches is the polls' budget, which the
chart enforces before anything reaches the cluster ([VAL-3]). The first
that time reaches, unless the backups are removed, is the host's disk at
the default and with a larger feed, in every case the estimates consider.
At the full budget, it's the disk if each poll stores a new version, after
22 days. If lists change only with new filings, the disk lasts 91 days, and
[reading the gaps](#reading-the-gaps) comes first if PostgreSQL reads
fewer than about 2 million polls a second. For all filers, the estimates
give no polls of the feed that would notice their filings, so they don't
say when reading the gaps would slow. After the disk come reading the
gaps, whose cost grows with every poll, and, for a large feed,
[the network](#the-network). [Memory](#memory) is spent up front and grows
little with the scope.

## The polls' budget

The Sources not paused share half the fetcher's limit, 2.5 polls a second,
and the chart refuses values whose Sources would together poll faster
([VAL-3], [§2 of the controls][controls-terms]). Companies and cadence
trade against each other:

| Cadence | Companies within the budget: 2.5 × the cadence |
| --- | --- |
| 1 second | 2, and a third at 2 seconds |
| 10 seconds | 25 |
| 1 minute | 150 |
| 10 minutes | 1,500 |
| 1 hour | 9,000 |
| 1 day | 216,000 |

A cadence bounds how well the record knows when a Source first showed a
filing: between the last poll that didn't show it and the first that did,
about one cadence apart ([SEEN-3]). It bounds, too, when the filing became
available: at 10 seconds, within about 10 seconds plus the source's own
delay ([0010]). So more companies cost each filing's first-seen time its
precision. Decision 0010 expects that past about 25 companies, a feed that
covers every company becomes the better notice source ([0010]), and task
T11 revisits the notice source and its cadence from the lags the record
shows, in a decision record that supersedes 0010 if anything changes
([plan][plan-observing]).

> **Analysis.** No task, decision record, or specification covers this.
> The pipeline could go past the budget in three ways. It could poll a
> feed that covers every company, such as EDGAR's latest-filings feed once
> its URL is confirmed and the scope widened to it (task T5), so one poll
> serves every company; the daily indexes cover every company too, but
> appear hours after acceptance ([SRC-5], [0010]). It could poll closely
> only the companies whose filings matter soonest, and the rest at longer
> cadences, as each Source's own cadence allows
> ([§6 of the controls][controls-source]). Or it could give the polls more
> of the fetcher's limit than half, at the cost of the new filings' fetches
> and Backfills, which share the rest.

## The fetcher's limit

The fetcher sends at most 5 requests a second, half the SEC's stated
maximum ([FETCH-6]), and only one replica sends ([0005]). Polls go first,
then new filings' fetches, then Backfills' ([JOB-4]), so the polls never
wait, and the rest wait for what the polls leave.

- **New filings' fetches fit at every scope.** At the default they're 2.8
  a day. Even for all filers, 6,355 to 12,710 a day are 0.07 to 0.15 a
  second, and 0.53 to 1.06 a second in the evening hours when most filings
  arrive ([estimates](estimates.md#requests-a-second)), within the 2.5 a
  second the budget leaves.
- **Backfills take time, not capacity.** A Backfill of every filing the
  three companies' `submissions` list takes 70 to 81 minutes at the
  default, and 2.2 to 2.5 hours at the full budget
  ([estimates](estimates.md#requests-a-second), [SRC-20]). Assuming each
  company's history takes as many requests as one of these three, from
  4,671 for Apple to 9,208 for Microsoft, 25 companies' take about 21
  hours at the full budget, and 1,000 companies' 22 to 43 days.

So the fetcher's limit doesn't bound the scope before the budget does: it
bounds how long a Backfill takes.

> **Analysis.** No task, decision record, or specification covers this.
> The SEC asks for at most 10 requests a second, and the fetcher keeps to
> 5 ([FETCH-6]). A Backfill of many companies' histories could run while
> the polls are paused, taking the whole limit at the cost of a gap in each
> Source's coverage; or the limit could come
> nearer the SEC's, at the cost of the margin that keeps the pipeline from
> a `403`.

## The host's disk

Every volume is kept in one host directory ([KIND-2]), so Garage's three
copies of each object, of the export, and of the archive, the database's
three instances, NATS's three copies of each stream, and Prometheus's data
share one disk. Nothing stored is removed ([§1 of the store][store-guarantees],
[TABLE-3]), and no statement sets how long the database's backups are kept
([PG-4]). A base backup a day of a database that grows each day makes the
backups grow with the square of the time:

| Scope | After a year | Of that, base backups | 500 GB is full after |
| --- | --- | --- | --- |
| The default | 0.89 to 3.45 TB | 75% to 97% | 110 to 271 days |
| The full budget | 7.3 to 28.5 TB | 75% to 97% | 22 to 91 days |
| A larger feed | 1.8 to 14.3 TB | 31% to 97% | 18 to 191 days |
| All filers | 0.84 to 39.0 TB | 76% to 95% | 37 to 279 days |

These are the [estimates' figures](estimates.md#on-the-hosts-disk), at
the reference of 500 GB free, an assumption, since no document names the
machine kind runs on. In each range, the shorter time is the high case. At
the scopes that poll, each poll then stores a new version of its body, and
by the time the disk is full, the objects take about half of it at the
default, three quarters at the full budget, and nearly all with a larger
feed; which case holds is for the investigation's refetches to show
(tasks T4 and T8). For all filers, the high case has more filings and more
facts, and the base backups and the export take most of the disk. In each
low case, the base backups take nearly all of it.

So the host's disk is the first limit time reaches at the default and
with a larger feed, and at the full budget unless lists change only with
new filings and PostgreSQL reads the polls slowly
([reading the gaps](#reading-the-gaps)). It comes sooner as the scope
grows: 4 to 9 months at the default, 3 weeks to 3 months at the full
budget. All the copies are on that one disk, so Garage's three nodes and
the database's three instances survive the loss of a node, not of the disk
([deployment, §1][deployment-guarantees]).

> **Analysis.** No task, decision record, or specification covers this.
> The deployment could give the backups' object store a retention policy,
> such as a recovery window of 7 days, which the Barman Cloud plugin
> applies by deleting the backups it no longer needs
> ([its retention policies][barman-retention]). About 8 base backups would
> then be kept: at the default after a year, 8 × 6.2 to 9.4 GB, compressed
> by Garage and kept three times, 37 to 113 GB on the host's disk, not 0.86
> to 2.6 TB. The low case would then fill 500 GB after about nine years.
> In the high case, the objects would remain, 3 × 708 MB = 2.1 GB a day,
> and with the rest would fill 500 GB in about 200 days. The plugin could
> also compress the archive itself ([its compression][barman-compression]),
> and the cluster could take fewer base backups, though [PG-4] asks for one
> a day. Garage's data could be kept on a disk of its own, so the
> database's backups would survive the loss of the disk that holds the
> database. A backup is a copy
> of the database, not a record or an object of the store, so removing an
> old one would leave the store's guarantees as they are
> ([§1 of the store][store-guarantees]).

## Reading the gaps

Within 10 seconds of a change in a Source's open gap, the discoverer
publishes it, and at least every 10 seconds it reads the open gaps from the
view `gaps` ([GAP-3], [METRIC-5]). The view takes every successful poll of
every Source, in order, to find where one doesn't follow the one before
([GAP-2], [`notices.sql`][notices-sql]), so each read reads every poll the
record holds, and the polls only grow. Grafana's views `coverage` and
`gaps` read every poll too, each time a dashboard refreshes
([open question 4 of the telemetry][telemetry-open]), though from the
database's replicas, not the primary ([MON-5]).

Assuming PostgreSQL reads 1 to 5 million polls a second in such a query,
joining each to its record and sorting them by Source, which a measurement
on kind replaces ([open question 4 of the telemetry][telemetry-open]):

| Scope | Polls a year | One read, after a year | A read takes 10 seconds after |
| --- | --- | --- | --- |
| The default | 25,920 × 365 = 9.46 million | 1.9 to 9.5 seconds | 386 to 1,929 days |
| The full budget | 216,000 × 365 = 78.8 million | 16 to 79 seconds | 46 to 231 days |
| A larger feed | 51,840 × 365 = 18.9 million | 3.8 to 19 seconds | 193 to 965 days |

Until then, the reads take a growing share of a core: at the default after
a year, 19% to 95% of one, every 10 seconds. Once a read takes longer than
10 seconds, the discoverer can't read the gaps every 10 seconds, as
[METRIC-5] asks. At the full budget that comes in 46 to 231 days. It comes
before the disk is full only if lists change only with new filings, so
that the disk lasts 91 days, and PostgreSQL reads fewer than about 2
million polls a second, so that 91 days' polls, 19.7 million, take 10
seconds to read. At the default and with a larger feed, the disk is full
first, though with a larger feed in the low case by only 2 days.
[Telemetry's open question 4][telemetry-open] already says that a later
version adds what the views need, such as an index or a stored summary of
each day, if they slow.

> **Analysis.** No task, decision record, or specification covers this.
> The discoverer reads each Source's poll events in order, one at a time
> ([DISC-1]), so it could tell when a gap opens and ends as it reads each
> event, and read the view only when it starts. The view could also read
> only the polls since each Source's latest gap, since a gap that ended
> doesn't change.

## Memory

kind runs every node as a container on one machine, so memory is the
scarce resource ([0001]). The estimates put the pipeline at 3.4 to 8.7 GB
for observing filings, and 4.0 to 14.4 GB with everything
([estimates](estimates.md#cpu-memory-and-storage)). Nearly all of it is
spent before the first poll: the database's three instances, Prometheus,
kind's control plane, and the three replicas each of NATS and Garage. The
scope adds little to it: each Source adds 18 to 23 series to Prometheus
([METRIC-2]), and none of the estimates of the services' memory grows with
the scope.

So memory doesn't limit the scope; it limits which machine can run the
pipeline at all. At the high end, kind alone needs 14.4 GB. Each figure
but those of the decision records is an assumption, until each
component's memory is measured on kind
([§9 of the deployment][deployment-later]).

> **Analysis.** No task, decision record, or specification covers this.
> On a smaller machine, the pipeline could run Garage, NATS, and the
> database with one replica each, at the cost of showing what the loss of
> a node does ([0002]), and leave Tempo and Loki out.

## The network

The estimates give the bytes fetched as bodies, before compression
([estimates](estimates.md#bytes-fetched-a-day)): 47 to 56 KB a second at
the default, 393 to 463 KB at the full budget, and 1.2 to 1.5 MB with each
company's `companyfacts`. Every request accepts gzip ([FETCH-3]), and the
records don't say how many bytes crossed the network ([SRC-18]); in the
baseline, a body's size didn't set the pace of the fetches ([SRC-20]).

So the network limits only a large feed. At the full budget of
`companyfacts` Sources, 25 at 10 seconds, the bodies are 2.5 × 3.77 to
4.88 MB = 9.4 to 12.2 MB a second, 75 to 98 Mbit/s if they arrive
uncompressed. On a slower link, the network would limit a scope of large
feeds before the budget does: each `companyfacts` Source at 10 seconds
takes 3.0 to 3.9 Mbit/s uncompressed, so a link of 10 Mbit/s, for
example, would carry about 3.

## The git store

The git store holds only its declared scope, kept small enough to commit
([§9 of the store][store-scope]): no file over 50 MiB, and 1 GB in all
([SCOPE-4], [SCOPE-5]). The transfer brings the cluster's records into it,
each with its object, for the ranges the owner names, and copies nothing
that would break either limit ([XFER-1]). At the default, the polls add
25,920 records a day, about 15 MB at some 600 bytes a record, so the
`submissions` log would pass 50 MiB within four days of polls
([§9 of the store][store-scope]); at the full budget, within about 10 hours.
Their objects can reach the 1 GB first. In the high case, each poll brings
a new object of up to 27,325 bytes ([SRC-18]), so a day of the default's
polls is 25,920 × (27,325 + 600) = 0.72 GB with their records, and the
1 GB is full after about a day and a half of polls; at the full budget,
after about 4 hours. In the low case, a list's new objects are a few a
day, and the log's 50 MiB comes first. A log is only appended to
([LOG-1]), so these are for every transfer together, not for each. The
store's open question 2 says that a log nearing 50 MiB needs a new layout,
such as a log per feed and year, in a major version
([open questions of the store][store-open]).

So the git store limits how many polls findings can cite, not what the
pipeline keeps: the cluster store keeps every record.

## All of EDGAR's filers

The design holds a small scope, not all of EDGAR
([what the pipeline keeps][design-keeps]), and the estimates show where
each part would stop:

- **The notice source stops first.** Polling each filer's `submissions`
  within the budget takes a cadence of the filers' number ÷ 2.5 seconds:
  a day for 216,000.
- **Fetching each new filing's files fits:** 0.07 to 0.15 requests a
  second on average, and about 1 at the evening's peak.
- **Storage grows with the facts.** 24,000 to 49,000 reports a year, each
  of 440 to 4,400 facts, and 32,000 to 64,000 filings of other forms with
  an XBRL instance, each of 40 to 580, add 1.8 to 101 GB a year to the
  database ([estimates](estimates.md#postgresql)), and 0.24 to 15.1 GB to
  the export, or 2.76 TB if every day's run is kept
  ([estimates](estimates.md#the-export)). With the database's backups and
  the export, 500 GB of disk is full after 37 to 279 days.
- **Parsing fits.** 155 to 310 instances a day, 67 to 133 of them
  reports, at an assumed 0.5 to 2 seconds each with lxml, are 1.3 to 10.3
  minutes of a core a day. The parser is still to be chosen, task T12
  ([plan][plan-facts]).

> **Analysis.** No task, decision record, or specification covers this.
> To observe every filer, the pipeline could take its notices from a feed
> that covers every company, poll each company's `submissions` only to
> backfill, and keep the daily indexes as a check that no filing was
> missed. Its record would then grow mostly with the facts, and its
> backups would need a retention policy before anything else.

[0001]: ../decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md
[0002]: ../decisions/0002-store-bodies-in-garage.md
[0005]: ../decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0010]: ../decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[barman-compression]: https://github.com/cloudnative-pg/plugin-barman-cloud/blob/72440855ce8c33ae71d0f3d8aca6634d2f3b8c7c/web/docs/compression.md
[barman-retention]: https://github.com/cloudnative-pg/plugin-barman-cloud/blob/72440855ce8c33ae71d0f3d8aca6634d2f3b8c7c/web/docs/retention.md
[controls-source]: ../../spec/controls.md#6-source
[controls-terms]: ../../spec/controls.md#2-terms
[deployment-guarantees]: ../../spec/deployment.md#1-guarantees
[deployment-later]: ../../spec/deployment.md#9-later-versions
[design-keeps]: ../design.md#what-the-pipeline-keeps
[DISC-1]: ../../spec/notices.md#processing
[FETCH-3]: ../../spec/snapshots.md#10-fetching
[FETCH-6]: ../../spec/snapshots.md#10-fetching
[GAP-2]: ../../spec/notices.md#5-polls-coverage-and-gaps
[GAP-3]: ../../spec/notices.md#5-polls-coverage-and-gaps
[JOB-4]: ../../spec/snapshots.md#jobs-and-events
[KIND-2]: ../../spec/deployment.md#the-kind-cluster
[LOG-1]: ../../spec/snapshots.md#8-history
[METRIC-2]: ../../spec/telemetry.md#4-metrics
[METRIC-5]: ../../spec/telemetry.md#the-discoverer
[MON-5]: ../../spec/deployment.md#monitoring
[notices-sql]: ../../spec/schemas/notices.sql
[PAGE-6]: ../../spec/site.md#7-the-pages-written-for-the-site
[PAGE-7]: ../../spec/site.md#7-the-pages-written-for-the-site
[PG-4]: ../../spec/deployment.md#instances-and-backups
[plan-facts]: ../plan.md#facts
[plan-observing]: ../plan.md#observing-filings
[SCOPE-4]: ../../spec/snapshots.md#9-scope
[SCOPE-5]: ../../spec/snapshots.md#9-scope
[SEEN-3]: ../../spec/notices.md#6-when-each-source-first-showed-each-filing
[SRC-5]: ../../investigation/edgar.md#findings-so-far
[SRC-18]: ../../investigation/edgar.md#findings-so-far
[SRC-20]: ../../investigation/edgar.md#findings-so-far
[store-guarantees]: ../../spec/snapshots.md#1-guarantees
[store-open]: ../../spec/snapshots.md#13-open-questions
[store-scope]: ../../spec/snapshots.md#9-scope
[TABLE-3]: ../../spec/notices.md#4-the-record-of-notices
[telemetry-open]: ../../spec/telemetry.md#11-open-questions
[VAL-3]: ../../spec/controls.md#5-the-charts-values
[XFER-1]: ../../spec/snapshots.md#records-the-cluster-made
