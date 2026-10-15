# Open questions and risks

<!-- status: all -->
**What's built,** as the [status page](../status.md) gives it:

- **Built:** The snapshot store in git.
- **Specified:** Scheduler; Fetcher; Discoverer; NATS JetStream; Garage; PostgreSQL 18; Monitoring: traces; The snapshot store in the cluster; The transfer from the cluster to git; The controls; The charts; The documentation site (built in part).
- **Decided:** Parse workers; Exporter; Query API; Monitoring: metrics, alerts, and dashboards (specified in part); Monitoring: logs; `edgar-asof`.
- **Planned:** The fake EDGAR; The drills.
<!-- end status -->

What the design still waits on, and what could undo it: the investigation's
open questions and the tasks that answer them, the releases of `edgar-asof`
that tasks wait on, the assumptions not yet shown, each specification's open
questions, and the risks the documents name. The facts about `edgar-asof`
here were checked against its `main` on October 14, 2026, at commit
`515bb0d`.

## What waits on the investigation

The specifications depend on how EDGAR behaves only through the
investigation's findings, and the investigation is done when every question
has findings the specifications can cite
([questions][inv-questions], [where documents go][agents-where]). [T1],
[T2], and [T3] answered Q2, Q3, and Q5 in part, from the baseline fetched
on October 7, 2026, and some of its findings bear on Q1 and Q4 too. What's
still open needs newer versions of EDGAR's feeds, or the pipeline's own
record, so each remaining task is a run against the SEC, and a run happens
only when the owner asks for it ([rules][agents-rules],
[how the plan works][plan-how]). The
investigation's daily refetches aren't tasks either: each is a run the
owner asks for ([method][inv-method], step 2).

| Question | Settled by findings | Still open | Answered by | Decides |
| --- | --- | --- | --- | --- |
| **Q1.** What each feed returns and how it changes | Which validators each kind of URL sends ([SRC-4]); the URLs of a company's lists and of a filing's files ([SRC-21]) | Whether a conditional request gets a `304`; how often each feed changes, and whether a URL returns to an earlier body; whether a filing's archived files ever change | [T4], [T8] | Which feeds the pipeline reads, and how often |
| **Q2.** Where each time comes from | The two acceptance times and how they differ ([SRC-1], [SRC-2], [SRC-13]); fiscal years and periods, and how each value joins to its filing ([SRC-3], [SRC-6] to [SRC-12]) | Which acceptance time is right for Apple, and the header's time zone | [T6], [T7], [T9] | The time model |
| **Q3.** How restatements and amendments appear | Every filing's value kept beside the others, restatements, amendments, and frames ([SRC-14] to [SRC-17]) | Whether a frame moves to a later filing's value when that filing appears; how the next reports' values appear | [T6], [T7], [T9] | What a query as of a past date returns |
| **Q4.** How new filings are announced | When the daily index files are written ([SRC-5]) | How soon each feed shows a new filing; when each day's index appears, and whether a published one changes; what the latest-filings feed holds | [T4], [T5], [T8], and the pipeline's record | Whether to keep the notice source, and how often to poll ([0010], [T11]) |
| **Q5.** Sizes and pace | Bytes per URL and per filing, and the pace of a backfill ([SRC-18] to [SRC-20]) | Growth per refetch | [T8] | The store's budget, and whether the pace suits backfills |

The questions and what each decides are the investigation's
([questions][inv-questions]); the tasks that answer each are the plan's
([investigation][plan-investigation], [status][status-investigation]).

```mermaid
flowchart LR
    T4["T4: the first refetch"] --> T8["T8: what the refetches show"]
    T6["T6: Microsoft's next 10-Q"] --> T8
    T7["T7: Apple's next 10-K"] --> T8
    T9["T9: Walmart's next 10-Q"]
    T5["T5: the latest-filings feed"]
    R["The pipeline's record, once the fetcher and the discoverer run"]
    T4 --> Q1["Q1: what feeds return"]
    T8 --> Q1
    T4 --> Q4["Q4: how filings are announced"]
    T5 --> Q4
    T8 --> Q4
    R --> Q4
    T8 --> Q5["Q5: sizes and pace"]
    T6 & T7 & T9 --> Q2["Q2: where times come from"]
    T6 & T7 & T9 --> Q3["Q3: restatements"]
    Q4 --> T11["T11: revisit the notice source and cadence"]
```

An arrow from a task to a task is a dependency; one from a task to a
question is what the task answers. Each task in the diagram but [T11] sends
requests to the SEC, and [T5] also widens the scope, once the owner
confirms the feed's URL ([investigation][plan-investigation]). [T11] waits
for the monitoring that shows the lags, [T28], and for the owner.

### Q1: what each feed returns and how it changes

The baseline shows which validators each kind of URL sends: `submissions`,
`companyfacts`, and directory listings send none, so each refetch of them
downloads the whole body ([SRC-4]). Whether a conditional request gets a
`304` is for the first refetch, [T4], to show. How often each feed's
content changes, and whether a URL returns to an earlier body, come from
the daily refetches, in [T8]. [T8] also refetches the baseline's eight
filings, to see whether any archived file changed, which the pipeline's
record assumes none does ([below](#assumptions-not-yet-shown)).

### Q2: where each time comes from

A filing's header gives its acceptance time as 14 digits with no time zone
([SRC-1]). `submissions` gives it in UTC, 4 or 5 hours later than the
header's digits for Microsoft's and Walmart's filings, as converting US
Eastern time to UTC would make it, and 8 hours later for all three of
Apple's stored filings ([SRC-2]). Read as UTC, `submissions`' times put
most of Apple's filings outside the hours the SEC gives for EDGAR
([SRC-13]). The baseline doesn't show which time is right for Apple. [T6],
[T7], and [T9] each record whether a new report's header and `submissions`
agree, and [T7] whether Apple's still differ by 8 hours
([investigation][plan-investigation]).

Two things wait on the answer. The view of lags reads the header's digits
as US Eastern time, and a finding that settles the zone changes one
function in `notices.sql`, in a new version of the notices specification,
and nothing stored ([notices' open question 2][notices-questions]). And
`edgar-asof` has no default interpreter of acceptance times ([TIME-9]):
whether the header's digits read as Eastern time become its default waits
on a finding that compares more headers with `submissions` than [SRC-2] and
[SRC-13] do ([`edgar-asof`'s plan, T12][asof-later]).

### Q3: how restatements and amendments appear

`companyfacts` keeps each filing's value for a concept, unit, and period
beside every other filing's ([SRC-14]); a restated value differs from one
reported again only in its `val`, with no field that marks the change
([SRC-15]); and an amendment is a filing of its own ([SRC-16]). Only the
latest-filed value of a key has a `frame`, but the baseline holds one
version of each company's `companyfacts`, so it doesn't show a frame moving
to a later filing's value when that filing appears; the daily refetches
through each company's next report can ([SRC-17]). [T6], [T7], and [T9]
record how each new report's values appear, and [T7] how a 10-K reports
earlier years' values again ([investigation][plan-investigation]).

### Q4: how new filings are announced

How soon each feed shows a new filing is measured from the pipeline's
record, once its fetcher and discoverer run, not from the investigation's
tasks ([0010], [method][inv-method], step 3). The record keeps every time
raw, and each lag is a query over it ([LAG-1]). So Q4 is answered only
after the pipeline's first part runs, and the investigation finishes after
it ([0010]). The first refetch, [T4], and the daily refetches, which [T8]
reads, show when each day's index appears and whether a published one
changes; [T5] records what
the latest-filings feed holds, so that [T11] can weigh it as a second
notice source.

Two of the specifications' open questions are parts of Q4: whether a
filing's files are in EDGAR's archive once its company's `submissions`
lists it ([notices' open question 1][notices-questions]), and whether
`companyfacts` can give a new filing's values before `submissions` lists
the filing ([`facts.md`'s open question 1][asof-facts-questions]).

### Q5: sizes and pace

The baseline gives the sizes of each kind of body ([SRC-18]) and of every
filing a company lists ([SRC-19]), and the pace of a backfill: at 5
requests a second, from 15.6 minutes for Apple's filings to at least 30.7
for Microsoft's, and about 40 seconds for only a company's XBRL 10-Ks,
10-Qs, and amendments ([SRC-20]). So the store's open question 4 is
answered: the pace suits backfills ([store's open questions][store-questions]).
Growth per refetch waits for [T8].

## What waits on `edgar-asof`'s releases

The pipeline's Python services read EDGAR's content through a pinned
`edgar-asof` ([0009]). A task that needs the library waits on a release of
it, not on its tasks, and is ready once that release is published
([how the plan works][plan-how]). No release is published yet: the
library's README at the named commit says it's not yet released
([README][asof-readme]), and `uv.lock` pins none ([status][status-parts]).
The page on [`edgar-asof`](edgar-asof.md) explains what each release
holds.

| Task here | The release it waits on, in this repository's plan | In `edgar-asof`'s plan |
| --- | --- | --- |
| [T24], the discoverer, which reads `submissions` and headers through the library | Its first release | Release 0.1.0 is its T9, after its T2 to T8 and T13, when the owner asks ([first release][asof-first-release]) |
| [T17], the facts specification, which cites a version of the library's for the fact model and "as of T" | A release that specifies the fact model | Its T1, done October 14, 2026, wrote [`facts.md`][asof-facts] version 0.1.0, which defines both. Its plan puts T1, and T8, which builds the facts, in its first release, 0.1.0, which its T9 publishes ([first release][asof-first-release]); it isn't published yet |
| [T25], the parse workers, which build facts through the library | A release that reads XBRL instances | Its T11, after its first release, this repository's [T12], the XBRL parser, and the owner ([later][asof-later]) |
| [T26], the query API, tested against the cases the library publishes | None named: its dependencies are [T17] and [T25] | Its T10 publishes the cases, after its first release, in a release it names as one [T26] waits on ([later][asof-later]) |
| [T27], the exporter, through a pinned `edgar-asof` | None named: its dependencies are [T18] and [T25] | No release named |

The two plans describe [T26] differently: `edgar-asof`'s plan says [T26]
waits on the release that publishes the cases, while this repository's
plan lists only [T17] and [T25] as its dependencies, though its row says
it's tested against those cases.

```mermaid
flowchart LR
    subgraph here["This repository"]
        F["A finding that records the feeds' layout"]
        P12["T12: the XBRL parser"]
        P17["T17: facts spec"]
        P24["T24: the discoverer"]
        P25["T25: the parse workers"]
        P26["T26: the query API"]
    end
    subgraph asof["edgar-asof"]
        A8["T8: facts"]
        A9["T9: release 0.1.0"]
        A10["T10: published cases"]
        A11["T11: XBRL instances"]
    end
    F --> A8
    A8 --> A9
    A9 --> P24
    A9 --> A10
    A10 -.-> P26
    A9 --> A11
    P12 --> A11
    A11 --> P25
    A9 --> P17
```

The dashed arrow is the dependency only `edgar-asof`'s plan names. The
arrow from release 0.1.0 to T17 joins the two plans: this repository's
names a release that specifies the fact model ([T17]), and the library's
puts its facts specification in release 0.1.0
([first release][asof-first-release]).

### A finding no task here names yet

The library's first release waits on this repository. Its T8, which builds
the facts, depends on a finding here that records the parts of the feeds'
layout no finding records yet: how `companyfacts` nests its values, the
column `accessionNumber` that holds a list's accession numbers, and what
`filings.files` is for a company with no further file. The owner deferred
that finding until the library's T8, on October 14, 2026
([`facts.md`'s open question 2][asof-facts-questions],
[`edgar-asof`'s plan][asof-first-release]). No task in this repository's
[plan][plan-investigation] names it. So the discoverer, [T24], waits
through the library's T9 and T8 on a finding that no task here schedules.

### How the library reads the store

The library's cache follows the snapshot store at its version 0.2.0, and it
reads a store's `data/` as a cache ([`fetching.md`, §6][asof-cache]). The
store's version 1.0.0 keeps what that relies on, adds two fields a reader
ignores, and widens `tool` to name the fetcher, so only a reader that holds
`tool` to version 0.2.0's form refuses the fetcher's records, until the
library's specification cites version 1.0.0
([readers outside the pipeline][store-readers]). In the cluster, the
library never fetches: the services give it bodies from the cluster store
([FETCH-18]), and its facts can be read from bodies the caller gives,
from which it fetches nothing ([FACT-2][asof-facts-reading]).

## Assumptions not yet shown

The design rests on a few things that no finding or record shows yet. Each
names what would show it.

| Assumption | What rests on it | What would show it |
| --- | --- | --- |
| A filing's archived files never change after acceptance (Q1) | The record itself: what was known on a date follows from the filings accepted by then ([what the pipeline keeps][design-keeps]). Each of a filing's files is held by the first record that names a version of it, and a filing already held isn't fetched again ([FILE-2], [FILE-6], [LIST-2]). | [T8], which refetches the baseline's eight filings |
| A header's acceptance time is US Eastern time | Each lag from the header's time ([§10 of the notices][notices-lags]) | A finding that settles the zone ([notices' open question 2][notices-questions]) |
| The SEC's `Date` is when it generated each response | The fetcher's check of its own clock, by which every lag is measured ([the fetcher's clock][telemetry-clock]) | A finding from the refetches' records, which keep each `date` beside `requested_at` and `received_at` ([telemetry's open question 1][telemetry-questions]) |
| A filing's files are in EDGAR's archive once `submissions` lists it | The discoverer's fetches of each new filing's files, which it doesn't queue again when they fail ([FILE-4]) | The record, which shows each fetch that ends without a version ([notices' open question 1][notices-questions]) |
| `companyfacts` doesn't give a new filing's values before `submissions` lists the filing | `edgar-asof`'s facts, which would otherwise have no time from `submissions` and stay unplaced until a later read | A finding from the pipeline's record; the owner kept that default on October 14, 2026 ([`facts.md`'s open question 1][asof-facts-questions]) |
| `submissions` shows a filing within about a second of its dissemination, as the SEC's documentation says, which isn't a finding | How soon the pipeline sees a filing: within about a cadence, 10 seconds at the start, plus the source's own delay ([0010]) | The lags the record shows (Q4), which [T11] weighs |
| The database's backups to Garage restore | Recovery of the database, whose volumes don't outlive the cluster ([§4 of the deployment][deployment-volumes]) | The restore drill on kind, since the backup plugin was tested against MinIO, not Garage ([0002], [T31]) |
| Every component fits on kind's one machine | Each component's requests and limits, still to be measured ([0001], [§9 of the deployment][deployment-later]) | Measurements on kind |

## Each specification's open questions

### The snapshot store

[`snapshots.md`'s §13][store-questions] has four. Which feeds and
companies the store holds is for the investigation to decide. How large
the scope may grow is answered by a budget, no file over 50 MiB and at most
1 GB in all ([SCOPE-4], [SCOPE-5]); if the store would outgrow it, objects
move to Git LFS or release assets, and a log nearing 50 MiB needs a new
layout, each in a major version. What an import's output is, and how a
rerun is compared with it, is for the export's specification, [T18]
([what the specifications must say][design-specs]). Whether the pace suits
backfills is answered (Q5).

### Deployment

[`deployment.md`][deployment] has no section of open questions. Its
version 1.1.0 deploys what the notices and the telemetry left to it: the
streams `ORDERS` and `NOTICES`, and the durable consumers of the
discoverer, the scheduler, and the parse workers ([NATS-3], [NATS-5]); the
discoverer's role and key ([PG-7], [GARAGE-4]); Grafana's role and its
data source ([PG-11], [MON-5]); and the notices' tables and views and the
dashboards' views, with their grants ([PG-12], [PG-13]). A stream kept
by interest takes its subjects only once its consumers exist, so a notice
published before `NOTICES` has its consumers is refused rather than kept
for none: even the gap notice the discoverer publishes when it starts on a
new install whose database was restored while NATS's data wasn't kept
([durable consumers][deployment-consumers]). Its [§9][deployment-later] lists what later versions add: the pipeline's
own services, with their replicas, credentials, and placement; the
fetcher's durable consumer of `JOBS`, a work queue, which keeps a job until
it's acknowledged; migrations for later versions of the record's tables
and views; each component's requests, limits, and probes, measured on
kind; Tempo, Loki, and Alloy; and kube-state-metrics' access to the
definitions of the pipeline's resources.

### Controls

[`controls.md`][controls] has no section of open questions. What it left
to other specifications ([§11][controls-other]), the store's version 1.0.0,
the notices, and the telemetry now give. One choice waits on a condition:
no admission webhook checks the Sources until something other than the
chart creates or changes them ([0010], owner's decision 5).

### Notices

[`notices.md`'s §12][notices-questions] has four:

1. **Whether a filing's files are in EDGAR's archive once `submissions`
   lists it** (Q4). If new filings' fetches end without a version, a later
   version queues them again after a delay; until then, a Backfill fetches
   them again.
2. **The header's time zone.** The lags read it as US Eastern time; a
   finding that settles it changes the function `header_instant`, and
   nothing stored.
3. **Exact gaps.** A job gives neither its due time nor its Source's
   cadence, so each poll's window stands in for the cadence. A shortened
   cadence can hide a poll missed at the change, and a lengthened one can
   show an open gap until the first poll at the new cadence. A poll's due
   time in its job would let a later version count missed slots exactly.
4. **A version that a later discoverer would read differently.** Each
   version's reading is kept once, by the discoverer that first read it;
   how a corrected reading is recorded is for a later version.

### Telemetry

[`telemetry.md`'s §11][telemetry-questions] has four:

1. **Whether the SEC's `Date` is when it generated each response.** Until a
   finding shows it, the fetcher's clock skew is shown and not alerted on.
2. **How often the cluster store is checked against [STORE-3] in
   operation,** and the alert on a problem. No component checks it yet.
3. **Sampling.** Every trace is sampled; at the starting cadence, the polls
   start 25,920 traces a day, most of which find no filing.
4. **The views' cost.** `coverage` and `gaps` read every poll, about 9.5
   million a year at the starting cadence, so how fast the dashboards read
   them is measured on kind.

It also leaves to later versions the platform's own metrics and alerts,
such as NATS's redeliveries and the database's replication; an alert on a
Source with no poll at all, which `SourceGap` doesn't cover, once the owner
adds a task for it ([alerts][telemetry-alerts]); and logs.

### The documentation site

[`site.md`'s §9][site-questions] has none open. The site shows `main`, with
no version per release of the pipeline, which is revisited when the
pipeline has releases.

### `edgar-asof`'s specifications

[`facts.md`'s §10][asof-facts-questions] has two, each with a default the
owner kept or deferred on October 14, 2026: whether `companyfacts` can give
a new filing's values before `submissions` lists it, and the finding that
records the feeds' layout ([above](#a-finding-no-task-here-names-yet)).
`fetching.md` and `times.md` have none; their next versions name the
functions and types a caller uses ([`edgar-asof`'s plan, T2 and
T3][asof-first-release]).

### What isn't written yet

Two specifications: the facts ([T17]) and the export ([T18]), with a minor
version of the telemetry for them ([T32]). Two decision records: how the
parse workers scale with their queue's depth ([T10]), and which XBRL parser
reads the instances, a narrow parser on lxml or Arelle ([T12]). And one
revisit: whether to keep each company's `submissions` as the notice source
([T11]).

## Risks the documents name

### To the record's completeness and integrity

- **What the pipeline didn't observe is lost.** A version the SEC served
  and replaced during a gap is lost to everyone; the record says the gap
  was there rather than implying nothing changed
  ([what the pipeline keeps][design-keeps]).
- **Storage can't refuse a change to an object.** Garage has no object lock,
  so the store detects an overwrite or a deletion rather than preventing
  it ([0002], [STORE-3]), and how often the cluster store is checked in
  operation is open ([telemetry's open question 2][telemetry-questions]).
- **A filing a Source already shows at its first poll isn't fetched for
  it.** The record doesn't say how long the Source had shown it, so a
  Backfill fetches it, when the owner asks for one ([FILE-2],
  [§6 of the notices][notices-seen]).
- **The git store's budget.** At the starting cadence, a log of every poll
  would pass GitHub's warning at 50 MiB within four days, so the transfer
  copies only the ranges the owner names ([records the cluster
  made][store-transfer]).

### To the times

- **The header's time zone is unsettled,** and until a finding settles it,
  Apple's 8-hour difference shows in the lags as an outlier ([0010],
  [SRC-2]).
- **A lag is only as good as its clocks.** The fetcher checks its clock
  against each response's `Date` ([0010], [METRIC-4]), which rests on an
  assumption ([above](#assumptions-not-yet-shown)).
- **Gaps and first sightings are bounded, not exact.** A changed cadence
  can hide or show a gap for one window
  ([notices' open question 3][notices-questions]), and a poll's event that
  arrives after a later one's loosens a sighting's bounds
  ([§6 of the notices][notices-seen]).
- **The earliest records' times.** A record that `snapshot.py` wrote
  before commit `8de33cd` can give a `requested_at` up to 0.2 seconds
  early; no finding relies on a record's time to within a second
  ([§14 of the store][store-changes], [records cited][inv-records]).

### To capacity

- **The rate limit.** Every request goes through one fetcher, capped at 5
  a second, half the SEC's stated maximum ([0005]), and the Sources share
  half of that, 2.5 a second ([VAL-3]). At 10 seconds, that covers about
  25 companies ([0010]).
- **Every poll downloads the whole body,** since `submissions` sends no
  validators ([SRC-4]).
- **Memory on one machine.** kind's nodes share one machine ([0001]). The
  database's three instances and its operator are the design's largest
  steady memory cost ([0004]), and kube-prometheus-stack takes an
  estimated 1 GB ([0008]).
- **The record's growth.** The dashboards' views read every poll, about
  9.5 million a year at the starting cadence, and the polls start 25,920
  traces a day ([telemetry's open questions][telemetry-questions]).

### To operation

- **A handover can leave a gap.** A new fetcher takes the Lease only once
  its duration has passed without a renewal, 15 seconds by default
  ([LEASE-1]). Decision 0005 records every handover as a gap ([0005]); the
  notices' specification gives a gap only where the handover misses a poll
  ([§5 of the notices][notices-gaps]).
- **A `403` stops everything until the owner acts.** The stop survives
  restarts and handovers, and holds until the owner acknowledges it
  ([ACCESS-3]); `SecRequestsStopped` fires meanwhile ([ALERT-1]).
- **Alerts stay in the cluster.** Alertmanager has no receiver outside the
  cluster unless the owner asks for one, so alerts show in Grafana and
  Alertmanager ([0008], [MON-3]).
- **A Source that never polls raises no alert.** `SourceGap` doesn't cover
  a Source and URL with no poll at all, as when no fetcher runs from the
  start ([alerts][telemetry-alerts]).
- **Single replicas.** The operators run one replica each, and while one
  restarts its resources wait in the API server
  ([§5 of the deployment][deployment-dependencies]); Prometheus,
  Alertmanager, and Grafana run one each ([MON-4]).
- **Definitions outlive their releases.** Every definition is kept when its
  release is uninstalled, and with it every resource of its kind, so
  removing one is a deliberate step; the Prometheus operator's definitions
  and kube-prometheus-stack are pinned apart, and must agree ([0011],
  [CRD-4], [CRD-5]).

### From the software the pipeline depends on

- **Zensical is alpha,** so its version is locked and each upgrade is a
  change of its own ([0012]).
- **Garage's chart isn't published to a chart repository,** so it's
  committed at a pinned release and upgraded deliberately ([0002],
  [PIN-3]).
- **The records weighed projects that changed under them:** MinIO's end of
  maintenance ([0002]), Bitnami's reduced catalog ([0001]), the 2025
  dispute over NATS's license, settled with NATS in the CNCF under Apache
  2.0 ([0003]), Promtail's end of life ([0008]), and Material for MkDocs's
  maintenance mode ([0012]).

### To the project

- **Two implementations of the fetching rules,** `snapshot.py` and the Go
  fetcher, kept together by one set of checks through the fake EDGAR
  ([0006], [testing](testing-and-verification.md#the-fake-edgar)).
- **A library in a repository of its own** implies releases and upkeep,
  and a change the pipeline needs is released there first ([0009]); the
  tasks that wait on it are [above](#what-waits-on-edgar-asofs-releases).

### Beyond what the documents name

> **Analysis.** No task, decision record, or specification covers this.
> Every worker node is a container on one machine ([0001]), and every
> volume of NATS, Garage, Prometheus, and Alertmanager is kept in one
> directory of that machine ([KIND-2], [VOL-2]), as are the database's
> backups, which go to Garage ([PG-4]). So the replicas that survive the
> loss of a node don't survive the loss of that disk. What the documents
> place outside the cluster is the git store ([0001]), with the records the
> transfer has copied into it ([XFER-1]). The pipeline could keep a copy of
> the backups, or of the bucket, on a second machine.

[0001]: ../decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md
[0002]: ../decisions/0002-store-bodies-in-garage.md
[0003]: ../decisions/0003-queue-work-in-nats-jetstream.md
[0004]: ../decisions/0004-keep-the-record-in-postgresql-18.md
[0005]: ../decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0006]: ../decisions/0006-write-the-services-in-go-and-python.md
[0008]: ../decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md
[0009]: ../decisions/0009-read-edgar-through-a-public-point-in-time-library.md
[0010]: ../decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[0011]: ../decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md
[0012]: ../decisions/0012-publish-the-documents-as-a-site-built-with-zensical.md
[design-keeps]: ../design.md#what-the-pipeline-keeps
[design-specs]: ../design.md#what-the-specifications-must-say
[status-parts]: ../status.md#the-parts
[status-investigation]: ../status.md#the-investigation
[plan-how]: ../plan.md#how-the-plan-works
[plan-investigation]: ../plan.md#investigation
[T1]: ../plan.md#investigation
[T2]: ../plan.md#investigation
[T3]: ../plan.md#investigation
[T4]: ../plan.md#investigation
[T5]: ../plan.md#investigation
[T6]: ../plan.md#investigation
[T7]: ../plan.md#investigation
[T8]: ../plan.md#investigation
[T9]: ../plan.md#investigation
[T10]: ../plan.md#facts
[T11]: ../plan.md#observing-filings
[T12]: ../plan.md#facts
[T17]: ../plan.md#facts
[T18]: ../plan.md#facts
[T24]: ../plan.md#observing-filings
[T25]: ../plan.md#facts
[T26]: ../plan.md#facts
[T27]: ../plan.md#facts
[T28]: ../plan.md#observing-filings
[T31]: ../plan.md#traces-logs-and-drills
[T32]: ../plan.md#facts
[agents-rules]: ../../AGENTS.md#rules
[agents-where]: ../../AGENTS.md#where-documents-go
[inv-questions]: ../../investigation/edgar.md#questions
[inv-method]: ../../investigation/edgar.md#method
[inv-records]: ../../investigation/edgar.md#records-cited
[store-questions]: ../../spec/snapshots.md#13-open-questions
[store-readers]: ../../spec/snapshots.md#readers-outside-the-pipeline
[store-transfer]: ../../spec/snapshots.md#records-the-cluster-made
[store-changes]: ../../spec/snapshots.md#14-changes-to-this-document
[deployment]: ../../spec/deployment.md
[deployment-volumes]: ../../spec/deployment.md#volumes
[deployment-dependencies]: ../../spec/deployment.md#5-dependencies
[deployment-later]: ../../spec/deployment.md#9-later-versions
[deployment-consumers]: ../../spec/deployment.md#durable-consumers
[controls]: ../../spec/controls.md
[controls-other]: ../../spec/controls.md#11-other-specifications
[notices-questions]: ../../spec/notices.md#12-open-questions
[notices-seen]: ../../spec/notices.md#6-when-each-source-first-showed-each-filing
[notices-gaps]: ../../spec/notices.md#5-polls-coverage-and-gaps
[notices-lags]: ../../spec/notices.md#10-times-and-lags
[telemetry-questions]: ../../spec/telemetry.md#11-open-questions
[telemetry-clock]: ../../spec/telemetry.md#the-fetchers-clock
[telemetry-alerts]: ../../spec/telemetry.md#7-alerts
[site-questions]: ../../spec/site.md#9-open-questions
[asof-readme]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/README.md
[asof-facts]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md
[asof-facts-reading]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#4-reading-a-companys-facts
[asof-facts-questions]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#10-open-questions
[asof-cache]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/fetching.md#6-the-cache
[asof-first-release]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/docs/plan.md#the-first-release
[asof-later]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/docs/plan.md#later
[TIME-9]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/times.md#4-interpreters
[SRC-1]: ../../investigation/edgar.md#findings-so-far
[SRC-2]: ../../investigation/edgar.md#findings-so-far
[SRC-3]: ../../investigation/edgar.md#findings-so-far
[SRC-4]: ../../investigation/edgar.md#findings-so-far
[SRC-5]: ../../investigation/edgar.md#findings-so-far
[SRC-6]: ../../investigation/edgar.md#findings-so-far
[SRC-12]: ../../investigation/edgar.md#findings-so-far
[SRC-13]: ../../investigation/edgar.md#findings-so-far
[SRC-14]: ../../investigation/edgar.md#findings-so-far
[SRC-15]: ../../investigation/edgar.md#findings-so-far
[SRC-16]: ../../investigation/edgar.md#findings-so-far
[SRC-17]: ../../investigation/edgar.md#findings-so-far
[SRC-18]: ../../investigation/edgar.md#findings-so-far
[SRC-19]: ../../investigation/edgar.md#findings-so-far
[SRC-20]: ../../investigation/edgar.md#findings-so-far
[SRC-21]: ../../investigation/edgar.md#findings-so-far
[STORE-3]: ../../spec/snapshots.md#the-cluster-store
[SCOPE-4]: ../../spec/snapshots.md#9-scope
[SCOPE-5]: ../../spec/snapshots.md#9-scope
[XFER-1]: ../../spec/snapshots.md#records-the-cluster-made
[FETCH-18]: ../../spec/snapshots.md#the-fetcher
[VAL-3]: ../../spec/controls.md#5-the-charts-values
[ACCESS-3]: ../../spec/controls.md#8-secaccess
[KIND-2]: ../../spec/deployment.md#the-kind-cluster
[VOL-2]: ../../spec/deployment.md#volumes
[GARAGE-4]: ../../spec/deployment.md#garage
[PG-4]: ../../spec/deployment.md#instances-and-backups
[PG-7]: ../../spec/deployment.md#the-stores-database
[PIN-3]: ../../spec/deployment.md#6-pins
[CRD-4]: ../../spec/deployment.md#definitions
[CRD-5]: ../../spec/deployment.md#definitions
[MON-3]: ../../spec/deployment.md#monitoring
[MON-4]: ../../spec/deployment.md#monitoring
[MON-5]: ../../spec/deployment.md#monitoring
[NATS-3]: ../../spec/deployment.md#nats
[NATS-5]: ../../spec/deployment.md#durable-consumers
[PG-11]: ../../spec/deployment.md#the-notices-and-the-dashboards
[PG-12]: ../../spec/deployment.md#the-notices-and-the-dashboards
[PG-13]: ../../spec/deployment.md#the-notices-and-the-dashboards
[LEASE-1]: ../../spec/deployment.md#the-fetchers-lease
[FILE-2]: ../../spec/notices.md#7-a-filings-files
[FILE-4]: ../../spec/notices.md#7-a-filings-files
[FILE-6]: ../../spec/notices.md#7-a-filings-files
[LIST-2]: ../../spec/notices.md#8-backfills
[LAG-1]: ../../spec/notices.md#10-times-and-lags
[METRIC-4]: ../../spec/telemetry.md#the-fetchers-clock
[ALERT-1]: ../../spec/telemetry.md#7-alerts
