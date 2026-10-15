# Estimates

<!-- status: all -->
**What's built,** as the [status page](../status.md) gives it:

- **Built:** The snapshot store in git.
- **Specified:** Scheduler; Fetcher; NATS JetStream; Garage; PostgreSQL 18; Monitoring: traces; The snapshot store in the cluster; The transfer from the cluster to git; The controls; The documentation site (built in part).
- **Decided:** Discoverer (specified in part); Parse workers; Exporter; Query API; Monitoring: metrics, alerts, and dashboards (specified in part); Monitoring: logs; `edgar-asof`; The charts (specified in part).
- **Planned:** The fake EDGAR; The drills.
<!-- end status -->

This page estimates what the pipeline asks of the SEC and of the machine it
runs on: the requests it sends a second, the bytes it fetches a day, how
fast each of its stores grows, and the CPU, memory, and storage each
component needs. It does so at the scope and cadences the chart sets by
default, and at three wider scopes. [Scale and limits](scale-and-limits.md)
reads from these estimates which resource runs out first, and when.

Each estimate shows its inputs and its arithmetic. Each input cites a
finding, a statement, or a decision record, or is marked an assumption,
with its reason and the measurement that would replace it. Where an input
is uncertain, the estimate is a range, from a low case to a high one
([PAGE-5]). Nothing here has been measured: no part of the pipeline runs on
kind yet, and the record holds no poll, so no measurement stands beside an
estimate. As the record or a run on kind measures a quantity, this page
gives the measurement beside the estimate ([PAGE-3], [PAGE-5]).

A KB is 1,000 bytes, an MB 1,000,000, a GB 10⁹, and a TB 10¹², as the
investigation counts them ([SRC-18]); a MiB is 2²⁰ bytes. The data the
pipeline keeps comes from the U.S. Securities and Exchange Commission's
EDGAR system.

## The scopes

| Scope | Its Sources | Polls a second | Companies |
| --- | --- | --- | --- |
| **The default** | Each of the three companies' `submissions`, every 10 seconds, as the chart's values start ([0010], [§5 of the controls][controls-values]) | 3 × 1/10 = 0.3 | 3 |
| **The full budget** | Sources that together poll at the budget, 2.5 a second, the most the chart allows ([VAL-3]): 25 companies' `submissions` every 10 seconds, or the three companies' every 1, 1, and 2 seconds | 2.5 | 25, or 3 |
| **A larger feed** | The default's, and each company's `companyfacts` as a Source of its own, also every 10 seconds | 0.6 | 3 |
| **All filers** | Every filer's new filings, found through a feed that covers every company | That feed's | Every filer |

The chart's default values pause the SecAccess, so the chart installed with
them sends nothing to the SEC until the owner's values allow it ([VAL-4]).
These estimates are of the pipeline once they do.

The full budget's estimates below are for 25 companies. The three companies
polled every 1, 1, and 2 seconds poll as often, and differ only in having
the new filings of three.

All filers is wider than the design, which holds a small scope
([what the pipeline keeps][design-keeps]). Polling each company's
`submissions` can't cover every filer within the budget
([scale and limits](scale-and-limits.md#the-polls-budget)), and decision
0010 expects a feed that covers every company to be the better notice
source past about 25 companies ([0010]). No specification names that feed,
so for all filers this page estimates only what fetching each new filing's
files takes and stores, not that feed's own polls.

## The inputs

### From EDGAR

| Input | Value | Behind it |
| --- | --- | --- |
| A company's `submissions` | 157,326 to 185,250 bytes; stored as gzip, 19,732 to 27,325 | [SRC-18] |
| What each poll of it downloads | The whole body, since `submissions` sends no validators | [SRC-4] |
| Its size from day to day | Within the range above | Assumption: `filings.recent` holds about 1,000 filings, as each company's did ([SRC-9]), so the body doesn't grow with each filing. Replaced by the sizes of the investigation's refetches, tasks T4 and T8. |
| A company's `companyfacts` | 3,770,257 to 4,881,196 bytes; stored as gzip, 246,663 to 313,163 | [SRC-18] |
| A filing's header and index page | 15,498 to 43,849 bytes together: a header 5,470 to 28,732, an index page 10,028 to 15,117; stored, 4,568 to 6,647 | [SRC-18] |
| An XBRL instance | Of a 10-K or 10-Q, 904,157 to 10,948,157 bytes, stored as 37,609 to 310,130; of an 8-K/A, 9,836 to 11,695, stored as 1,381 to 2,165 | [SRC-18] |
| Each company's reports a year | About 4: 70 of Apple's XBRL 10-Ks, 10-Qs, and amendments, 68 of Microsoft's, and 70 of Walmart's, since their first in 2009 | [SRC-10], [SRC-20] |
| A year of the three companies' reports, in bytes | 48.93 MB of instances: a 10-K and three 10-Qs are 4.30 MB of Apple's, 39.97 MB of Microsoft's, and 4.65 MB of Walmart's. Stored with their headers and index pages, 1.52 MB | [SRC-19]'s eight filings. Assumption: each next report is the size of the one stored. Replaced by the next reports, tasks T6, T7, and T9. |
| New filings | 126 by the three companies in the 13 weeks to October 7, 2026: 126 ÷ 91 ÷ 3 = 0.46 a company a day | [0010] |
| Filings with an XBRL instance | 5.0%: `submissions` marks 484 of the three companies' 9,689 filings `isXBRL`. 208 of the 484, 43%, are 10-Ks, 10-Qs, and amendments | [SRC-19], [SRC-20] |
| When filings arrive | About 90% of Microsoft's and Walmart's between 4 and 7 PM US Eastern time | [0010] |
| How often a list changes | Low: once for each new filing it lists. High: at every poll | Assumption: a new filing changes the list that names it ([SRC-9]), and nothing stored shows whether anything else changes it; how often each feed's content changes is the investigation's Q1 ([questions]). Replaced by the investigation's daily refetches, tasks T4 and T8. |
| Filings a day across EDGAR | 3,100 to 6,200 | Assumption: a day's master index is 310,875 to 621,000 bytes ([SRC-18]), and it has a line of about 100 bytes for each filing. Replaced by counting the lines of the stored master indexes, a finding the investigation could add. |
| The full budget's 25 companies | Each files 0.46 times a day, and reports as the three companies do on average: 4 reports a year, 48.93 MB ÷ 3 = 16.3 MB of instances, stored with their headers and index pages as 1.52 MB ÷ 3 = 0.51 MB | Assumption: no finding covers a company but the three. Replaced by the record's filings and objects of the companies polled, once the fetcher and the discoverer run, tasks T23 and T24. |
| Every filer's filings | As the three companies': 5.0% with an XBRL instance, 43% of those reports; and as Microsoft's and Walmart's, about 90% between 4 and 7 PM US Eastern time | Assumption: no finding covers another filer's forms or times, and decision 0010 gives the evening's share of two companies' filings ([0010]). Replaced by counting the forms and acceptance times of a day's filings across EDGAR, a finding the investigation could add. |

### From the pipeline

| Input | Value | Behind it |
| --- | --- | --- |
| The fetcher's limit | 5 requests a second | [FETCH-6] |
| The polls' budget | 2.5 requests a second; the rest is left for new filings' fetches and Backfills | [VAL-3], [§2 of the controls][controls-terms] |
| Which fetch goes first | Polls, then new filings' fetches, then Backfills' | [JOB-4] |
| What a poll adds | A record, a row of `polls`, a job, and an event | [FETCH-14], [GAP-1], [JOB-2], [JOB-6] |
| What a new filing adds | Fetches of its header and index page, and of the XBRL instance its index page names: 2 + 5.0% = 2.05 fetches | [FILE-2], [FILE-3] |
| What a body adds to Garage | An object, once for each distinct body | [OBJ-4], [VER-1] |
| Copies of each object | 3, one on each of Garage's three nodes | [GARAGE-1], [GARAGE-3] |
| Copies of the database | 3 instances, each on a volume of its own | [PG-3] |
| Where every volume is kept | One host directory, so one disk | [KIND-2] |
| The database's archive | Its WAL continuously, and a base backup at least once a day, in Garage | [PG-4] |
| How long backups are kept | Every one, uncompressed | Assumption: no statement sets a retention policy or compression ([PG-4]), and without them the Barman Cloud plugin deletes no backup ([its retention policies][barman-retention]) and archives uncompressed ([its compression][barman-compression]). Replaced by the chart's settings, task T20, and the bucket's size on kind. |
| WAL archived | A 16 MiB segment at least every 5 minutes while the database is written: 288 × 16 MiB = 4.83 GB a day | Assumption: CloudNativePG's default `archive_timeout` of 5 minutes, which no statement changes ([CloudNativePG's defaults][cnpg-defaults]); PostgreSQL's default segment of 16 MiB; and a segment closed early is as long as a full one ([`archive_timeout`][pg-archive-timeout]). Replaced by the archive's size on kind. |
| What Garage stores of them | WAL and base backups, 2 to 4 times smaller; gzip bodies, as they are | Assumption: Garage compresses each block with zstd by default ([its configuration][garage-config]), and PostgreSQL fills the rest of a segment closed early with zeros ([its commit][pg-zero-fill]), which take almost nothing compressed. The plugin's own benchmark found its algorithms make a database's backup 2.4 to 5.9 times smaller ([its compression][barman-compression]). Replaced by Garage's bytes on disk on kind. |
| A row's bytes, with its indexes | A record 556 to 624; a poll 98; a reading 263; a filing 981, with its sighting and its header's and index page's readings; a filing's fetch 1,205, its job and its record | Assumption: from the columns' types in [`records.sql`][records-sql] and [`notices.sql`][notices-sql], PostgreSQL's 24 bytes of a row's header and 4 of its pointer, and index pages 70% to 90% full. Replaced by each table's `pg_total_relation_size` on kind. |
| WAL written | 1.5 to 3 times the tables' growth | Assumption: each row's WAL record, its index entries, and an image of each page first changed after each checkpoint. Replaced by `pg_stat_wal` on kind. |
| An empty database | 0.05 GB | Assumption. Replaced by its size on kind. |
| WAL kept by each instance | 0.6 to 1.5 GB | Assumption: CloudNativePG's default `wal_keep_size` of 512 MB ([its defaults][cnpg-defaults]), and PostgreSQL's default `max_wal_size` of 1 GB. Replaced by each instance's `pg_wal` on kind. |
| Facts a report | 440 to 4,400 | `companyfacts` holds 2,641 values of the six stored reports, 440 each: only their facts of `us-gaap` and `dei` concepts with a unit and without dimensions ([SRC-14]). Assumption: an instance holds up to 10 times as many. Replaced by the facts the parse workers write, task T25. |
| A fact's row | 150 to 400 bytes, with its indexes | Assumption: the fact table is the facts specification's, task T17, still to be written. |
| A fact in the export | 20 to 60 bytes | Assumption: the export's format is task T18's. |
| The export's runs | A run a day. Low: the export holds each fact once. High: each run writes every fact, and every run's files are kept | Assumption: how often the export runs and what it keeps are task T18's, and no statement removes its files. Replaced by the export's specification, task T18, and the export's size in Garage on kind. |
| Prometheus | A scrape every 30 seconds, kept 10 days, at 1 to 2 bytes a sample | Assumption: kube-prometheus-stack's defaults ([its values][kps-values]), which no statement changes, and Prometheus's own figure for a sample ([its storage][prom-storage]). Replaced by Prometheus's own metrics on kind. |
| The platform's own series | 20,000 to 60,000 | Assumption: what kube-prometheus-stack scrapes of a cluster of four nodes: the API server, kubelets and containers, node-exporter, and kube-state-metrics. Replaced by `prometheus_tsdb_head_series` on kind. |
| The pipeline's own series | 18 to 23 a Source, 6 a Backfill, and 320 to 550 more | From the metrics and labels of [§4][telemetry-metrics] and [§5][telemetry-state] of the telemetry. Assumption: up to 8 statuses seen of each kind of job, one or two discoverer replicas, and 40 to 80 series of each replica's own client library. Replaced by Prometheus's count on kind. |
| A job and an event in a stream | 400 to 500 bytes, and 1,100 to 1,400 | Assumption: a job's fields, about 300 bytes of JSON; an event's record, about 600 bytes as the git store's logs keep one ([§9 of the store][store-scope]), with its job; their trace context ([TRACE-1]); and the stream's own overhead. Replaced by the streams' bytes on kind. |
| Free disk | 500 GB | Assumption: no document names the machine kind runs on. 500 GB is a reference, and the arithmetic takes any other. |

## Requests a second

| Scope | Polls a second | New filings' fetches | Of the limit, 5 a second |
| --- | --- | --- | --- |
| The default | 0.3 | 3 × 0.46 = 1.4 filings a day × 2.05 = 2.8 fetches a day | 0.30, 6% |
| The full budget | 2.5 | 25 × 0.46 = 11.5 filings a day × 2.05 = 23.7 fetches a day | 2.50, 50% |
| A larger feed | 0.3 + 0.3 = 0.6 | 2.8 fetches a day | 0.60, 12% |
| All filers | That feed's | 3,100 to 6,200 filings a day × 2.05 = 6,355 to 12,710 fetches a day, 0.07 to 0.15 a second; with 90% of them in three evening hours, 0.53 to 1.06 a second then | 1% to 3% on average, and up to 21% in the evening; and that feed's polls |

New filings' fetches are a few a day at the default: the polls are nearly
all the load. Decision 0010 gives the same 0.3 a second ([0010]).

A Backfill takes what's left of the limit, since its fetches go last
([JOB-4], [BACKFILL-2]). The investigation counted the requests of each
Backfill ([SRC-20]):

| Backfill | Requests | At the default, 5 − 0.3 = 4.7 a second | At the full budget, 2.5 a second |
| --- | --- | --- | --- |
| Every filing the three companies' `submissions` list | 4,671 + 6,166 + 9,025 = 19,862, and at least 3,042 more for Microsoft's second further file: 22,904 | 70 to 81 minutes | 2.2 to 2.5 hours |
| Only their XBRL 10-Ks, 10-Qs, and amendments | 210 + 204 + 210 = 624 | 2.2 minutes | 4.2 minutes |

These take longer if the fetcher, like `snapshot.py` in the baseline, sends
fewer than 5 a second: at the baseline's 3.6 to 4.0 a second, each Backfill
of a company takes 24% to 39% longer ([SRC-20]).

## Bytes fetched a day

A body's bytes are as its record counts them, after any `Content-Encoding`
is removed ([§2 of the store][store-terms]). Every request accepts gzip
([FETCH-3]), so fewer bytes may cross the network, but the records keep
neither a response's encoding nor its length ([SRC-18]).

| Scope | Arithmetic | A day |
| --- | --- | --- |
| The default | Polls: 25,920 × 157,326 to 185,250 bytes = 4.08 to 4.80 GB. New filings' headers and index pages: 1.4 × 15,498 to 43,849 = 21 to 61 KB. Reports: 48.93 MB a year, 0.13 MB a day, in a few days of each quarter | 4.1 to 4.8 GB |
| The full budget | Polls: 216,000 × 157,326 to 185,250 = 33.98 to 40.01 GB. 25 companies' new filings' headers and index pages: 11.5 × 15,498 to 43,849 = 0.18 to 0.50 MB. Reports: 48.93 MB × 25 ÷ 3 = 408 MB a year, 1.1 MB a day | 34 to 40 GB |
| A larger feed | The default's, and `companyfacts`: 25,920 × 3,770,257 to 4,881,196 = 97.7 to 126.5 GB | 102 to 131 GB |
| All filers | Headers and index pages: 3,100 to 6,200 × 15,498 to 43,849 = 48 to 272 MB. Instances: 5.0% of the filings, 155 to 310, of which 43% are reports of 904,157 to 10,948,157 bytes and the rest 9,836 to 11,695: 61 MB to 1.46 GB | 0.11 to 1.73 GB, and that feed's polls |

At the default, 4.1 to 4.8 GB a day is 47 to 56 KB a second, on average.
At the full budget it's 393 to 463 KB a second, and with a larger feed,
1.2 to 1.5 MB a second.

## How the stores grow

Nothing stored is changed or removed ([§1 of the store][store-guarantees],
[TABLE-3]), so every store grows with time at every scope.

### Objects in Garage

A body becomes an object only when it's new: each version of a list, and
each file of each new filing ([OBJ-4], [VER-1]). In the low case, a list
changes once for each new filing; in the high case, at every poll.

| Scope | Low | High |
| --- | --- | --- |
| The default | 1.4 filings a day × (19,732 for the list's version + 4,568 for the header and index page) × 365 = 12 MB, and 1.52 MB of reports: 14 MB a year | 25,920 × 27,325 = 708 MB a day: 259 GB a year |
| The full budget | 11.5 × (19,732 + 4,568) × 365 = 102 MB, and 25 companies' reports, 13 MB: 0.12 GB a year | 216,000 × 27,325 = 5.90 GB a day: 2.15 TB a year |
| A larger feed | The default's, and a version of each `companyfacts` with each report, 12 a year × 246,663 = 3 MB: 17 MB a year | The default's, and 25,920 × 313,163 = 8.12 GB a day: 3.22 TB a year |
| All filers | 3,100 × 4,568 = 14.2 MB, and 155 instances, 43% stored at 37,609 and the rest at 1,381, 2.6 MB: 16.8 MB a day, 6.1 GB a year | 6,200 × 6,647 = 41.2 MB, and 310 instances, 43% at 310,130 and the rest at 2,165, 41.7 MB: 82.9 MB a day, 30 GB a year |

Garage keeps each object on each of its three nodes ([GARAGE-3]), so each
takes three times its size on the host's disk ([KIND-2]). A gzip body
compresses no further. Which case holds decides most of Garage's growth,
and the investigation's refetches will show it (tasks T4 and T8).

### PostgreSQL

Each poll adds a record and a row of `polls`, 654 to 722 bytes. In the
high case, a poll of `submissions` adds a reading of its new version too,
263 more, since the discoverer reads only `submissions` ([SEEN-1]): 654 to
985 bytes a poll of `submissions`, and 654 to 722 a poll of another feed.

| Scope | Arithmetic | A day | A year |
| --- | --- | --- | --- |
| The default | 25,920 polls × 654 to 985 bytes; filings and facts add under 0.1 MB a day | 17.0 to 25.6 MB | 6.2 to 9.3 GB |
| The full budget | 216,000 × 654 to 985 | 141 to 213 MB | 52 to 78 GB |
| A larger feed | 25,920 polls of `submissions` × 654 to 985, and 25,920 of `companyfacts` × 654 to 722 | 34 to 44 MB | 12 to 16 GB |
| All filers | 3,100 to 6,200 filings × 981; 6,355 to 12,710 fetches × 1,205; and 10.7 to 214 million facts a year × 150 to 400 | 15 to 256 MB | 5.5 to 93 GB |

The first poll of each Source adds its list's filings, about 1,000 each
([SRC-9]), with a sighting of each: about 3,000 rows each for the three
companies, under 2 MB in all.

Facts are few at the scopes of a few companies: 12 reports a year × 440 to
4,400 = 5,280 to 52,800 facts, 0.8 to 21 MB a year, at the default; 100
reports, 44,000 to 440,000 facts, 7 to 176 MB, at the full budget. A
Backfill of the three companies' 208 reports ([SRC-19]) adds 92,000 to
915,000 facts, 14 to 366 MB. For all filers, facts are most of the
database: 155 to 310 instances a day × 43% × 365 = 24,000 to 49,000
reports a year, × 440 to 4,400 = 10.7 to 214 million facts, 1.6 to 86 GB a
year.

Each of the three instances holds all of it ([PG-3]), so the database
takes three times its size on the host's disk, and each instance keeps
0.6 to 1.5 GB of WAL besides.

### The database's archive

The archive is in Garage, under the bucket `database-backups` ([PG-4]).

- **WAL.** The database writes 1.5 to 3 times its growth as WAL: 25 to
  77 MB a day at the default, 212 to 640 MB at the full budget. Each 16 MiB
  segment is archived when it fills, or after 5 minutes, so at every scope
  here the archive takes a segment every 5 minutes, 4.83 GB a day by the
  bucket's own count, 1.76 TB a year. Garage compresses the zeros that fill
  each segment's rest, so it stores about the WAL written, compressed: 6
  to 38 MB a day at the default.
- **Base backups.** Each day's base backup is the whole database as it is
  that day, and none is removed. Day *d*'s is 0.05 GB + *d* × the day's
  growth, so the first year's add up to 365 × 0.05 GB + (1 + 2 + … + 365)
  × the day's growth = 18 GB + 66,795 × the day's growth:

  | Scope | The day's growth | Base backups in the first year | In Garage, 2 to 4 times smaller |
  | --- | --- | --- | --- |
  | The default | 17.0 to 25.6 MB | 1.15 to 1.73 TB | 0.29 to 0.86 TB |
  | The full budget | 141 to 213 MB | 9.5 to 14.3 TB | 2.4 to 7.1 TB |
  | A larger feed | 34 to 44 MB | 2.3 to 3.0 TB | 0.57 to 1.49 TB |
  | All filers | 15 to 256 MB | 1.0 to 17.1 TB | 0.26 to 8.6 TB |

Kept forever, the base backups grow with the square of the time: each day
adds a copy of a database that is itself a day larger. In the low case they
are most of the host's disk within weeks, and in the high case they share it
with the objects ([below](#on-the-hosts-disk)).

### The queue's streams

A job waits in `JOBS` until the fetcher takes it, and an event in `RECORDS`
until each of its consumers acknowledges it ([jobs and events][store-jobs]).
While they keep up, the streams hold a few messages. They grow while a
consumer is down, and each message is on all three servers ([NATS-3]). A
poll's job stays until it's taken, though it has expired ([JOB-5]):

| Scope | Polls' jobs, while no fetcher runs | Events, while the discoverer is down |
| --- | --- | --- |
| The default | 25,920 a day × 400 to 500 bytes = 10 to 13 MB a day | 25,920 a day × 1,100 to 1,400 bytes = 29 to 36 MB a day |
| The full budget | 216,000 × 400 to 500 = 86 to 108 MB a day | 216,000 × 1,100 to 1,400 = 238 to 302 MB a day |
| A larger feed | 51,840 × 400 to 500 = 21 to 26 MB a day | 51,840 × 1,100 to 1,400 = 57 to 73 MB a day |
| All filers | That feed's polls', which this page doesn't estimate | That feed's polls', which this page doesn't estimate |

New filings' fetches add nothing meanwhile: the discoverer queues them as
it reads the polls' events ([FILE-2]), so none is queued while no poll is
made or no event read. A Backfill's jobs wait behind the polls and new
filings' fetches ([JOB-4]): one of every filing the three companies'
`submissions` list, 22,904 jobs × 400 to 500 bytes, holds 9.2 to 11.5 MB
in `JOBS`, taken in 70 to 81 minutes at the default, and 2.2 to 2.5 hours
at the full budget.

`ORDERS` and `NOTICES` carry an order for each Backfill and a notice for
each filing, gap, and step of a Backfill ([streams][notices-streams]): a
few a day at the default.

### The export

The export's format, how often it runs, and what it keeps are the export's
specification's, task T18 ([plan][plan-facts]). Until then, a fact takes
20 to 60 bytes in it, and it runs once a day. In the low case, the export
holds each fact once: each run writes only the facts new since the one
before, or replaces it. In the high case, each run writes every fact, and
every run's files are kept, so a day adds as many bytes as every fact so
far, and the first year's runs add up to (1 + 2 + … + 365) = 66,795 days'
new facts, as the base backups do ([above](#the-databases-archive)):

| Scope | Facts a year | Each fact once: a day, and a year | Every run kept: a day after a year, and the first year |
| --- | --- | --- | --- |
| The default | 5,280 to 52,800 | 5,280 × 20 bytes ÷ 365 = 0.3 KB, to 52,800 × 60 ÷ 365 = 8.7 KB; 0.1 to 3.2 MB | 3.2 MB; 8.7 KB × 66,795 = 0.58 GB |
| The full budget | 44,000 to 440,000 | 2.4 to 72 KB; 0.9 to 26 MB | 26 MB; 72 KB × 66,795 = 4.8 GB |
| A larger feed | The default's | The default's | The default's |
| All filers | 10.7 to 214 million | 0.59 to 35 MB; 0.21 to 12.8 GB | 12.8 GB; 35 MB × 66,795 = 2.35 TB |

A Backfill of the three companies' 208 reports adds 92,000 to 915,000
facts, 1.8 to 55 MB, to the export once in the low case, and to each run
after it in the high case. Garage keeps each file on each of its three
nodes ([GARAGE-3]), so the export takes three times its size on the
host's disk. That's much only for all filers in the high case: 3 × 2.35 =
7.0 TB after a year, and 17% of the disk by the time it's full
([below](#on-the-hosts-disk)).

### Prometheus

Every label takes values from a small set ([METRIC-2]), so the pipeline's
series grow with the Sources and Backfills, not with the filings:

| Scope | The pipeline's series | With the platform's | Kept for 10 days |
| --- | --- | --- | --- |
| The default | 320 to 550 + 3 × 18 to 23 = 374 to 619 | 20,374 to 60,619 | 20,374 × 2,880 scrapes a day × 1 byte × 10 = 0.59 GB, to 60,619 × 2,880 × 2 × 10 = 3.49 GB |
| The full budget | 320 to 550 + 25 × 18 to 23 = 770 to 1,125 | 20,770 to 61,125 | 0.60 to 3.52 GB |
| A larger feed | 320 to 550 + 6 × 18 to 23 = 428 to 688 | 20,428 to 60,688 | 0.59 to 3.50 GB |
| All filers | 320 to 550, and 18 to 23 for each of that feed's Sources | 20,320 to 60,550, and that feed's | 0.59 to 3.49 GB, and 0.5 to 1.3 MB for each of that feed's Sources |

Each Source adds 18 to 23 series, 0.5 to 1.3 MB at 10 days. Prometheus's
storage stops growing once its oldest data is 10 days old, and
Alertmanager's at 120 hours ([its values][kps-values]).

Traces, in Tempo, come later ([plan][plan-traces]). At the default, the
polls start 25,920 traces a day ([open question 3 of the
telemetry][telemetry-open]), each of 4 spans: the scheduler's publishing of
the job, the fetcher's acting on it and publishing the event, and the
discoverer's acting on that ([TRACE-1], [TRACE-3]). At an assumed 0.2 to
1 KB a span, that's 21 to 104 MB a day; how long Tempo keeps them is for a
later version of the deployment ([§9 of the deployment][deployment-later]).

### On the host's disk

Every volume is in one host directory ([KIND-2]), so the stores share one
disk. After *d* days it holds:

- 3 × Garage's: the objects, the export, the WAL archived, and the base
  backups ([GARAGE-3]), with the export's low case in the low case and its
  high case in the high ([above](#the-export));
- 3 × the database's, and each instance's WAL ([PG-3]);
- Prometheus's 0.6 to 3.5 GB, and the streams' few MB.

| Scope | After 30 days | After a year | Of that, base backups | 500 GB is full after |
| --- | --- | --- | --- | --- |
| The default | 12 to 98 GB | 0.89 to 3.45 TB | 75% to 97% | 110 to 271 days |
| The full budget | 70 to 738 GB | 7.3 to 28.5 TB | 75% to 97% | 22 to 91 days |
| A larger feed | 20 to 846 GB | 1.8 to 14.3 TB | 31% to 97% | 18 to 191 days |
| All filers | 12 to 303 GB | 0.81 to 33.5 TB | 77% to 95% | 41 to 283 days |

In each range, the shorter time is the high case, where each poll stores a
new version: at the default, after 110 days, its objects take 234 GB of
the disk, and its base backups 243 GB. In the low case, the base backups are nearly all of
it. Had Garage stored each WAL segment at its full 16 MiB, the WAL alone
would take 3 × 4.83 = 14.5 GB a day, and fill 500 GB in 34 days at any of
these scopes.

## CPU, memory, and storage

kind runs every node as a container on one machine, so memory is the
scarce resource ([0001]). Each component's resource requests and limits
are measured on kind before a later version of the deployment states them
([§9 of the deployment][deployment-later]). Until then, each figure here
but those the decision records give is an assumption, from what programs
of its kind take, which that measurement replaces.

| Component | Replicas | Memory each | CPU | Storage |
| --- | --- | --- | --- | --- |
| Scheduler | 2, one active ([design][design-components]) | 30 to 100 MB: a Go controller | A job a poll: a few millicores at the default | None |
| Fetcher | 2, one active ([0005]) | 50 to 150 MB: a Go service that holds a body of up to 10.9 MB ([SRC-18]) | A request, a hash, and a gzip a poll: under 10 millicores at the default | None |
| Discoverer | 1 or 2: a Deployment ([design][design-components]) | 100 to 250 MB: a Python service that reads a list of up to 314 KB ([SRC-18]) | Reading each poll's list, and reading `gaps` every 10 seconds ([GAP-3], [METRIC-5]), which grows with the polls ([scale and limits](scale-and-limits.md#reading-the-gaps)) | None |
| Parse workers | Scaled by queue depth, task T10 | 150 to 250 MB with lxml, 0.5 to 2 GB with Arelle, task T12 | 0.5 to 2 seconds an instance with lxml: a few a year at the default, or 2 to 7 minutes for a Backfill of 208 | None |
| Exporter | 1, while it runs: a CronJob | 100 to 500 MB | A run a day | Its files, in Garage |
| Query API | 1 or 2: a stateless Deployment | 30 to 100 MB | Each query's | None |
| NATS JetStream | 3 ([NATS-1]) | 30 to 150 MB | Little | Its streams ([above](#the-queues-streams)) |
| Garage | 3 ([GARAGE-1]) | 20 to 200 MB: idle under 20 MB, under 200 MB in one stress test ([0002]) | Little | 3 copies of each object and of the archive |
| PostgreSQL 18 | 3 instances ([PG-3]), with their operator and its backup plugin | 250 MB to 1 GB each, and 100 to 300 MB for the operator and the plugin: the design's largest steady memory cost ([0004]) | Under one insert a second at the default; reading `gaps` grows with the polls | 3 copies of the database |
| kube-prometheus-stack | 1 of each part ([MON-4]) | About 1 GB in all, most of it Prometheus ([0008]) | Its scrapes, every 30 seconds | 0.6 to 3.5 GB |
| Tempo, and Loki with Alloy | Later ([0008]) | A few hundred MB each ([0008]): 200 to 500 MB | Little | Their own volumes |
| cert-manager | 3 pods | 30 to 100 MB each | Little | None |
| kind's control plane, and each node's agents | 1 control-plane node; agents on all 4 nodes ([KIND-1]) | 1 to 2 GB in all | 0.5 to 1.5 cores in all, with Prometheus's scrapes | Its own |

Memory adds up to 3.4 to 8.7 GB for what observing filings needs, and 4.0
to 14.4 GB with one or two parse workers, the query API, the exporter,
Tempo, and Loki.

NATS, Garage, and the database each place one replica on each of the three
worker nodes ([PLACE-1]), 0.3 to 1.35 GB on each. With everything, and
the rest spread evenly, each worker node holds 1.0 to 4.1 GB, and the
control-plane node kind's own 1 to 2 GB. Every node's volumes are on the
host's one disk ([KIND-2]).

At the default, the pipeline's own work is a few dozen milliseconds of CPU
a poll, 0.3 polls a second: about a hundredth of a core. What grows is
reading `gaps` every 10 seconds, which reads every poll the record holds,
so its cost grows with the polls' rate and with time
([scale and limits](scale-and-limits.md#reading-the-gaps)).

## What would replace these estimates

| Measurement | Replaces | When |
| --- | --- | --- |
| How often each feed's body changes, and its sizes over time | How often a list changes, and so Garage's growth | The investigation's daily refetches, tasks T4 and T8 |
| The next reports' instances | The reports' sizes | Tasks T6, T7, and T9 |
| Each component's memory and CPU, each volume's size, the archive's size in Garage, and Prometheus's series | The rest of this page's assumptions | The platform on kind, task T20, then each service as it's built: tasks T21 to T24 |
| The cost of reading `gaps` and `coverage` | The polls read a second, in [reading the gaps](scale-and-limits.md#reading-the-gaps) | Measured on kind ([open question 4 of the telemetry][telemetry-open]), with the monitoring, task T28 |
| Facts a report, a fact's row, a fact in the export, and the export's runs | The facts' and the export's growth | Tasks T17, T18, T25, and T27 |
| The pipeline's own polls, fetches, and filings | Requests, bytes, and rows | The record, once the fetcher and the discoverer run, tasks T23 and T24 |

[0001]: ../decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md
[0002]: ../decisions/0002-store-bodies-in-garage.md
[0004]: ../decisions/0004-keep-the-record-in-postgresql-18.md
[0005]: ../decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0008]: ../decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md
[0010]: ../decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[barman-compression]: https://github.com/cloudnative-pg/plugin-barman-cloud/blob/72440855ce8c33ae71d0f3d8aca6634d2f3b8c7c/web/docs/compression.md
[barman-retention]: https://github.com/cloudnative-pg/plugin-barman-cloud/blob/72440855ce8c33ae71d0f3d8aca6634d2f3b8c7c/web/docs/retention.md
[BACKFILL-2]: ../../spec/controls.md#7-backfill
[cnpg-defaults]: https://github.com/cloudnative-pg/cloudnative-pg/blob/89e5adb773dac7fc00eff1a73f74795c37973c67/docs/src/postgresql_conf.md
[controls-terms]: ../../spec/controls.md#2-terms
[controls-values]: ../../spec/controls.md#5-the-charts-values
[deployment-later]: ../../spec/deployment.md#9-later-versions
[design-components]: ../design.md#components
[design-keeps]: ../design.md#what-the-pipeline-keeps
[FETCH-3]: ../../spec/snapshots.md#10-fetching
[FETCH-6]: ../../spec/snapshots.md#10-fetching
[FETCH-14]: ../../spec/snapshots.md#10-fetching
[FILE-2]: ../../spec/notices.md#7-a-filings-files
[FILE-3]: ../../spec/notices.md#7-a-filings-files
[GAP-1]: ../../spec/notices.md#5-polls-coverage-and-gaps
[GAP-3]: ../../spec/notices.md#5-polls-coverage-and-gaps
[GARAGE-1]: ../../spec/deployment.md#garage
[GARAGE-3]: ../../spec/deployment.md#garage
[garage-config]: https://garagehq.deuxfleurs.fr/documentation/reference-manual/configuration/
[JOB-2]: ../../spec/snapshots.md#jobs-and-events
[JOB-4]: ../../spec/snapshots.md#jobs-and-events
[JOB-5]: ../../spec/snapshots.md#jobs-and-events
[JOB-6]: ../../spec/snapshots.md#jobs-and-events
[KIND-1]: ../../spec/deployment.md#the-kind-cluster
[KIND-2]: ../../spec/deployment.md#the-kind-cluster
[kps-values]: https://github.com/prometheus-community/helm-charts/blob/3246603bcb2f1539709017311bc41702ef3a7b01/charts/kube-prometheus-stack/values.yaml
[METRIC-2]: ../../spec/telemetry.md#4-metrics
[METRIC-5]: ../../spec/telemetry.md#the-discoverer
[MON-4]: ../../spec/deployment.md#monitoring
[NATS-1]: ../../spec/deployment.md#nats
[NATS-3]: ../../spec/deployment.md#nats
[notices-sql]: ../../spec/schemas/notices.sql
[notices-streams]: ../../spec/notices.md#streams
[OBJ-4]: ../../spec/snapshots.md#5-objects
[PAGE-3]: ../../spec/site.md#7-the-pages-written-for-the-site
[PAGE-5]: ../../spec/site.md#7-the-pages-written-for-the-site
[pg-archive-timeout]: https://www.postgresql.org/docs/18/runtime-config-wal.html#GUC-ARCHIVE-TIMEOUT
[pg-zero-fill]: https://github.com/postgres/postgres/commit/4a33bb59dfc33566f04e18ab5e1f90b8e7461052
[PG-3]: ../../spec/deployment.md#instances-and-backups
[PG-4]: ../../spec/deployment.md#instances-and-backups
[PLACE-1]: ../../spec/deployment.md#placement
[plan-facts]: ../plan.md#facts
[plan-traces]: ../plan.md#traces-logs-and-drills
[prom-storage]: https://prometheus.io/docs/prometheus/latest/storage/#operational-aspects
[questions]: ../../investigation/edgar.md#questions
[records-sql]: ../../spec/schemas/records.sql
[SEEN-1]: ../../spec/notices.md#6-when-each-source-first-showed-each-filing
[SRC-4]: ../../investigation/edgar.md#findings-so-far
[SRC-9]: ../../investigation/edgar.md#findings-so-far
[SRC-10]: ../../investigation/edgar.md#findings-so-far
[SRC-14]: ../../investigation/edgar.md#findings-so-far
[SRC-18]: ../../investigation/edgar.md#findings-so-far
[SRC-19]: ../../investigation/edgar.md#findings-so-far
[SRC-20]: ../../investigation/edgar.md#findings-so-far
[store-guarantees]: ../../spec/snapshots.md#1-guarantees
[store-jobs]: ../../spec/snapshots.md#jobs-and-events
[store-scope]: ../../spec/snapshots.md#9-scope
[store-terms]: ../../spec/snapshots.md#2-terms
[TABLE-3]: ../../spec/notices.md#4-the-record-of-notices
[telemetry-metrics]: ../../spec/telemetry.md#4-metrics
[telemetry-open]: ../../spec/telemetry.md#11-open-questions
[telemetry-state]: ../../spec/telemetry.md#5-metrics-of-the-controls
[TRACE-1]: ../../spec/telemetry.md#9-traces
[TRACE-3]: ../../spec/telemetry.md#9-traces
[VAL-3]: ../../spec/controls.md#5-the-charts-values
[VAL-4]: ../../spec/controls.md#5-the-charts-values
[VER-1]: ../../spec/snapshots.md#7-versions
