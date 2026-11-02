# 15. Poll the latest-filings feed for every filer's filings, and reconcile each day with the daily index

**Status:** Proposed, November 2, 2026. The owner asked for it on November
2, 2026, ahead of T11, from the goal the owner stated that day rather than
from measured lags. It would supersede the part of
[0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)
that makes each company's `submissions` the notice source, and the part of
[0013](0013-measure-each-feeds-lag-in-a-bounded-study-then-keep-only-changes.md)
whose study measures `submissions` alone.

## Context

**The notice source now.**
[0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)
starts the pipeline with each company's `submissions` as its notice source:
one `Source` for each company, polled every 10 seconds. It expects that
past about 25 companies, a feed that covers every company becomes the
better source. All Sources' polls share half the fetcher's limit of 5
requests a second
([VAL-3](../../spec/controls.md#5-the-charts-values),
[FETCH-6](../../spec/snapshots.md#10-fetching)), so a cadence of *c*
seconds covers 2.5 *c* companies: 25 at 10 seconds, 150 at a minute, and
1,500 at 10 minutes
([scale and limits](../explanation/scale-and-limits.md#the-polls-budget)).
Each poll of `submissions` downloads the whole list, 157,326 to 185,250
bytes, since it sends no validators (SRC-4, SRC-18).
[0013](0013-measure-each-feeds-lag-in-a-bounded-study-then-keep-only-changes.md)
has the pipeline measure how long after acceptance `submissions` shows a
filing, in an 8-week study (T45). It leaves the latest-filings feed to
T11, which waits on T28's monitoring.

**The owner's goal.** On November 2, 2026, the owner stated what the
pipeline is for. It isn't tracking a small set of companies. It's a
database over a very large universe of companies, at least the S&P 500's
historical constituents, kept for a very long time, with comprehensive
coverage of every filing. More important still, it serves bitemporal
analysis, which the owner expects needs capture as close to real time as
possible. Backfills run
whenever capacity is free. Polling each company can't serve that goal. At
the full budget, the S&P 500's roughly 500 members alone would each be
polled every 200 seconds, and a filer outside the list goes unseen until
it's added.

**What a notice source decides.** Each fact is known from its filing's
acceptance time, read from the filing's own header (SRC-1,
[0004](0004-keep-the-record-in-postgresql-18.md)). A filing's header and
its files can be fetched at any later time (SRC-21,
[0013](0013-measure-each-feeds-lag-in-a-bounded-study-then-keep-only-changes.md)).
So "as of" answers don't depend on how soon the pipeline sees a filing
([times](../design.md#times)); they depend on its seeing every filing.
How soon it sees one decides how fresh the database is. It also lets the
record measure when each feed made a filing public
([0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)).

**The feeds that cover every filer,** as the findings show them:

- **The latest-filings feed** lists the latest filings of every filer and
  form, newest first (SRC-27). One request covers them all. EDGAR builds it
  for each request and sends no validators. So each poll gets the whole
  body, 56,306 bytes at `count=100`, which the git store keeps as 4,035
  bytes of gzip. Each poll is also a new version, since the body names the
  second it was made (SRC-26). Its 100 entries are 100 pairs of filing and
  party, holding 50 filings (SRC-27). Fetched on a Sunday morning, they
  reached back 2 hours and 48 minutes into Friday evening. At the pace of
  the master indexes' pairs, spread evenly over EDGAR's 16 hours, 100
  entries span about 14 to 31 minutes, and less in a busier hour. So a
  poll that comes later than that after the one before can miss filings
  (SRC-28). The stored version doesn't show whether its times are
  acceptance times (SRC-27). Nor does it show whether every filing appears
  in the feed.
- **Each day's master index** lists that day's filings, one line for each
  CIK a filing names (SRC-28). Each day's file appears between 10:01 and
  10:04 p.m. Eastern on its date. The quarter's listing shows the new day
  at about 1:45 a.m. the next morning (SRC-5, SRC-23). None of the four
  stored master indexes changed in the 9 to 14 days after it was published
  (SRC-23). No finding yet compares a day's master index with another list
  of that day's filings.

The SEC documents other sources, none of them in the scope or the
findings:

- **The XBRL RSS feeds:** the latest 100 filings with XBRL data, which the
  SEC says are updated every 10 minutes on business days.
- **The Public Dissemination Service:** a paid subscription that pushes
  each accepted filing to the subscriber's server over a dedicated
  connection.
- **For history:** quarterly indexes of every filing from 1994 on, and
  bulk archives rebuilt each night.

## Options

- **A. Keep polling each company's `submissions`**
  ([0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)),
  lengthening every cadence as the universe grows. There's no new source
  to investigate. But freshness falls as the universe grows, filers outside
  it go unseen, and the polls take half the fetcher's limit. At the full
  budget they fetch 34 to 40 GB a day
  ([estimates](../explanation/estimates.md#bytes-fetched-a-day)).
- **B. The latest-filings feed alone.** One poll covers every filer: at 10
  seconds, 0.1 requests a second, 2% of the limit. But a filing missed
  between polls, or one the feed never shows, stays missed, and nothing in
  the record would show it.
- **C. The latest-filings feed, checked each day against the master
  index.** As B, and also, each night, the discoverer reads the day's
  master index. It records each filing the feed didn't show as found in
  the index. A filing the feed missed is then found within about a day,
  and its acceptance time comes from its header, as any other filing's
  does.
- **D. The daily index alone**
  ([0013](0013-measure-each-feeds-lag-in-a-bounded-study-then-keep-only-changes.md)'s
  option B). It's the simplest and cheapest, and as complete as the index
  is. But a day's filings are first seen only once its master index is
  published, at about 10 p.m. Eastern on its date, or at about 1:45 a.m.
  the next morning through the listing (SRC-5, SRC-23). That's against
  the owner's goal, and no feed's lag within the day is measured.
- **E. The Public Dissemination Service.** It pushes each filing as it's
  disseminated: by the SEC's account, it sends each accepted public filing
  to its subscribers in real time. But it's a paid subscription over a dedicated
  connection, outside the fetcher's model of HTTP requests at a limited
  rate ([0005](0005-send-sec-requests-through-one-active-fetcher.md)).
- **F. The XBRL RSS feeds.** They hold only filings with XBRL data, updated
  every 10 minutes by the SEC's account. The many filings without XBRL,
  such as Forms 4, would go unseen.

## Decision

C. Each detail below is the owner's, where
[what the owner decided](#what-the-owner-decided) gives it, and otherwise
the proposed default of the matching item under
[details for the owner](#details-for-the-owner-to-decide):

1. **The notice source is the latest-filings feed,** at the URL the scope
   holds: every form, ownership forms included, with `count=100` (T5). One
   `Source` polls it every 10 seconds, at all hours: 8,640 polls a day, 0.1
   requests a second. The discoverer reads each new version and adds each
   filing it names, matched by accession number, with its sighting
   ([§6 of the notices](../../spec/notices.md#6-when-each-source-first-showed-each-filing)).
   It keeps each entry's party, CIK, role, and time as the feed gives them
   (SRC-27), raw, as
   [0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)
   keeps `submissions`' `acceptanceDateTime`. A filing's acceptance time is
   still its header's (SRC-1).
2. **Every filer's filings are recorded.** For each filing the feed shows,
   the discoverer fetches its header, its index page, and the XBRL
   instance the index page names, as
   [FILE-1 to FILE-3](../../spec/notices.md#7-a-filings-files) say, under
   its issuer's CIK, as the `link` of its `Issuer` entry gives it
   (SRC-27), as the owner decided. A filing with no `Issuer` entry read is
   fetched under its `Subject` entry's CIK, if one is read, and otherwise
   under its first entry's (details 9 and 10). Which filers' facts the
   parse workers write, and how the universe is kept, is a decision still
   to come.
3. **A poll that doesn't reach back leaves a gap.** When a successful poll
   names none of the filings the previous poll named, the feed may have
   shown filings between them that neither poll caught. The record keeps
   that stretch as a gap in the feed's coverage, from the earlier poll to
   the later one, as a missed poll leaves one now
   ([GAP-2](../../spec/notices.md#5-polls-coverage-and-gaps)).
4. **Each day is checked against its master index.** A second `Source`
   polls the quarter's daily index listing every hour. For each day the
   listing shows that the discoverer hasn't read yet, from the day after
   the feed's first poll whose list is read (detail 12), it queues that
   day's master index and reads it. After a quarter ends, its listing is
   still polled until its last days are read (detail 13). Each filing the
   index lists that no poll of the feed showed is recorded as first shown
   by that index, and its files are fetched, under the CIK of the first
   line that lists it (detail 11). Filings are matched by accession
   number. The record counts each filing that only one of the two sources
   showed.
5. **The three companies' `submissions` Sources stay through the study**
   ([0013](0013-measure-each-feeds-lag-in-a-bounded-study-then-keep-only-changes.md),
   T45). So for their filings, the record bounds when each of the two feeds
   first showed each one, and the study measures both lags. After the
   study they end, unless the owner keeps them.

## What the owner decided

1. **The CIK a filing's files are fetched under: its issuer's,** on
   November 2, 2026, over the CIK of the first entry read for the filing,
   which detail 4 proposed. A filing's `Issuer` entry gives the issuer's
   CIK in its `link` (SRC-27). In the feed's stored version, 35 of the 41
   Forms 4 and 4/A list a reporting owner's entry first, so the first
   entry's CIK would fetch most Forms 4 under an insider's CIK. Forms 4 are
   60% to 74% of the three companies' filings (SRC-19), and the scope
   covers only the companies' own directories, so the transfer wouldn't
   have copied those the feed showed first
   ([XFER-1](../../spec/snapshots.md#records-the-cluster-made)). The CIK
   of a filing with no `Issuer` entry, and of one only the master index
   lists, is still the owner's to decide (details 9 to 11).

## Details for the owner to decide

Each has a proposed default, which the decision above uses. The owner
decides each one by keeping its default or choosing another. Detail 4 is
decided, and keeps its number.

1. **The feed's cadence.** Proposed default: 10 seconds, as 0010's
   Sources start. That bounds each filing's first-seen time within about
   10 seconds, at 2% of the limit. At 60 seconds the polls would cost a
   sixth as much and bound it within a minute. That's still well within
   the 14 to 31 minutes that 100 entries span on average (SRC-28).
2. **The hours it polls.** Proposed default: all hours, at one cadence,
   since a `Source` has no field for hours
   ([§6 of the controls](../../spec/controls.md#6-source)). Polling only
   during EDGAR's hours, 6 a.m. to 10 p.m. Eastern (SRC-25), would make
   5,760 polls a day rather than 8,640. It needs a new field in the
   `Source`.
3. **A poll that doesn't reach back.** Proposed default: record the gap,
   and leave its filings to that night's check against the master index.
   The alternative is to ask for the feed's next page with `start`. That
   needs a finding that `start` pages the feed, and widens the scope to
   each page's URL.
4. **The CIK a filing's files are fetched under.** The owner decided it:
   its issuer's ([what the owner decided](#what-the-owner-decided)).
5. **Which files of every filer's filings are fetched.** Proposed default:
   the header, the index page, and the instance, as FILE-1 to FILE-3 say
   now. The estimates put every filer's fetches at 0.07 to 0.15 a second
   on average, and up to 21% of the limit in the evening
   ([estimates](../explanation/estimates.md#requests-a-second)). They
   put what those files store at 16.8 to 82.9 MB of new objects a day, 6.1
   to 30 GB a year, which Garage keeps three times on the host's disk
   ([GARAGE-3](../../spec/deployment.md#garage)). In PostgreSQL, a
   filing's rows take 981 bytes and each fetch's 1,205: 10.7 to 21.4 MB a
   day, more than the feed's polls add. Each of the database's three
   instances keeps them
   ([PG-3](../../spec/deployment.md#instances-and-backups)), and each
   later day's base backup copies them again
   ([estimates](../explanation/estimates.md#how-the-stores-grow)). The
   alternative is only each filing's header, for its acceptance time,
   until the decision on the universe: about half the fetches, and none
   of the instances, which are 2.6 to 41.7 MB of each day's objects.
6. **The bodies of the feed's versions.** Proposed default: keep each one,
   as the snapshot store keeps every version now. If each body compresses
   as the stored one did, that's about 34.9 MB of gzip a day at 10
   seconds, or 12.7 GB a year, which Garage keeps three times over
   ([GARAGE-3](../../spec/deployment.md#garage)). Keeping only changes,
   which 0013 adopts after its study, doesn't shrink them, since every
   poll's body differs. Whether a version of the store treats this feed
   differently is for T46.
7. **How often the listing is polled.** Proposed default: every hour, 24
   polls a day. A day's filings are then checked within about an hour of
   the listing's showing them, by about 2:45 a.m. Eastern the next
   morning (SRC-23).
8. **The plan.** Proposed default: T11 becomes this record. It then
   depends on the owner alone, rather than also on T28. The notices'
   version for the feed and the daily check comes before T24 builds the
   discoverer, as T44's did
   ([0013](0013-measure-each-feeds-lag-in-a-bounded-study-then-keep-only-changes.md)),
   so the discoverer is built once.
9. **The CIK of a filing whose entries name a `Subject` and no
   `Issuer`,** as a Form 144's and a Schedule 13D's, 13D/A's, or 13G's do
   (SRC-27). Proposed default: the subject's, as its `Subject` entry's
   `link` gives it. The subject is the company the filing concerns, as a
   Form 4's issuer is, so the three companies' Forms 144 and Schedules
   would be fetched under the directories the scope covers. The
   alternative is the first entry's CIK, a reporting owner's or a filer's
   for each Form 144 and half the Schedules in the stored version; the
   findings don't show which party's directory holds these filings.
10. **The CIK of a filing whose entries read name neither an `Issuer` nor
    a `Subject`.** That's a filing with a single filer, whose role no
    stored entry shows, since the stored version's 50 filings are all
    ownership forms and Schedules (SRC-28); or a Form 4 whose issuer's
    entry a poll's 100 entries cut off, as the stored version's last entry
    was (SRC-27). Proposed default: the CIK of its first entry read, as
    its `link` gives it, which for a single filer is the filer's own. The
    alternative leaves such a filing to that night's check against the
    master index, as detail 3 leaves a gap's filings, but finds a single
    filer's filings a day late.
11. **The CIK of a filing only the master index lists.** The index lists a
    filing on a line for each CIK it names (SRC-28), and no finding shows
    that it gives each one's role. Proposed default: the CIK of the first
    line that lists it. If one of the three companies' `submissions`
    Sources showed the filing before the index is read, its fetches are
    queued under the company's CIK already, and FILE-1 keeps the URL of a
    file's first job. The
    alternative is the CIK of a line that a `submissions` Source polls,
    when one does, which keeps the three companies' filings in the scope's
    directories even when no Source showed them.
12. **The first day checked.** Proposed default: the day after that of
    the feed's first poll whose list is read, and every day after it.
    Earlier days, which the feed didn't cover whole, are left to
    Backfills, as
    [FILE-2](../../spec/notices.md#7-a-filings-files) leaves the filings
    a Source shows at its first poll. Checking every day the listing shows
    would, at a start on December 1, 2026, read about 40 business days at
    once: at 3,100 to 6,200 filings a day and 2.05 fetches a filing
    ([estimates](../explanation/estimates.md#requests-a-second)), 254,000
    to 508,000 fetches, 14 to 28 hours of the fetcher's whole limit. The
    feed's new filings would wait behind them, since the fetcher takes a
    filing's jobs in the order they were queued
    ([JOB-4](../../spec/snapshots.md#jobs-and-events)), and each of those
    days' filings would count as one the feed didn't show.
13. **The listing at a quarter's end.** Proposed default: a quarter's
    listing is still polled after the quarter ends, beside the new
    quarter's, until the discoverer has read a poll of it made after the
    new quarter's first day, and the master index of each day that poll
    shows. The listing shows a day at about 1:45 a.m. Eastern the next
    morning (SRC-23), so it shows the quarter's last day only in the new
    quarter; and a pipeline that was down across the quarter's end reads
    the days it missed once it's back. The alternative, polling only the
    new quarter's listing from its first day, leaves the old quarter's
    last day unchecked, and any day an outage left unread.

## Consequences

- **Freshness no longer falls as the universe grows.** One poll covers
  every filer. The cadence is set by how fresh the owner wants filings to
  be, and the universe by what the database keeps.
- **Backfills get most of the limit.** Polls fall from 0.3 requests a
  second to 0.1, or 0.4 while the study's three Sources run. New filings'
  fetches take 0.07 to 0.15 on average
  ([estimates](../explanation/estimates.md#requests-a-second)). Backfills,
  whose fetches go last
  ([JOB-4](../../spec/snapshots.md#jobs-and-events),
  [BACKFILL-2](../../spec/controls.md#7-backfill)), take the rest. On
  average that's 4.45 to 4.53 a second while the study's Sources run,
  less than the 4.7 they get now, and 4.75 to 4.83 after the study. The
  estimates assume 90% of new filings come in three evening hours, when
  their fetches take 0.53 to 1.06 a second. Then Backfills get 3.5 to
  4.1 a second during the study, and 3.8 to 4.4 after it; at night and on
  weekends they get more. These leave out the fetches of the filings
  the daily check finds, which the record will count. VAL-3's budget
  stays, though after the study the polls use a twenty-fifth of it.
- **Every filing in the master index of a day checked is found.** A
  filing the feed shows is first seen within a cadence, plus the feed's
  own delay. One it misses is found within about a day. Either way its
  acceptance time comes from its header, so "as of" answers don't depend
  on which source found it. Whether the index lists every filing, and
  whether the feed shows every filing, is for the record to show: the
  daily check counts each filing that only one of them showed.
- **The record keeps a new kind of gap.** A poll that doesn't reach back is
  a gap in the feed's coverage, though the poll succeeded. Answers about
  what the pipeline held at a time carry it, as they carry a missed poll
  ([times](../design.md#times)).
- **What the feed costs.** At 10 seconds, the polls fetch 486 MB a day and
  store about 34.9 MB of gzip (detail 6). Each poll adds a record, a row of
  `polls`, and, since each body is new, a reading: 917 to 985 bytes, or
  7.9 to 8.5 MB a day in PostgreSQL
  ([estimates](../explanation/estimates.md#postgresql)). The three
  companies' polls at the same cadence fetch 4.1 to 4.8 GB a day.
- **What every filer's files cost.** As detail 5's default fetches them,
  6,355 to 12,710 fetches a day store 16.8 to 82.9 MB of objects, which
  Garage keeps three times over, and add 10.7 to 21.4 MB a day to PostgreSQL,
  before any facts, which the decision on the universe sets
  ([estimates](../explanation/estimates.md#how-the-stores-grow)).
- **The investigation gains questions.** The record answers them as it
  answers Q4's lags
  ([0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)):
  - how long after acceptance the feed shows a filing;
  - whether its times are acceptance times (SRC-27);
  - how far back its 100 entries reach in the busiest hour (SRC-28);
  - whether the feed and the master index list the same filings.

  A finding about them cites the records once the transfer copies them to
  `data/` ([XFER-1](../../spec/snapshots.md#records-the-cluster-made)).
  `scope.json` covers the feed already, and this quarter's listing and
  master indexes, but not the next quarter's. It covers filings' files
  only in the three companies' directories (SRC-27). So as the scope
  stands, the two questions that need each filing's header (SRC-1), how
  long after acceptance the feed shows a filing and whether its times are
  acceptance times, are answered from the three companies' filings alone.
  Answering them for other filers needs the scope widened, which is the
  owner's to ask for.
- **The daily index's URL names its quarter.** The listing's Source
  changes each quarter: `2027/QTR1/` from January 1, 2027, while
  `2026/QTR4/`'s is still polled until its last days are read
  (detail 13). `scope.json` must change too before the transfer can copy
  the new quarter's records. The controls' version says whether the
  chart's values name the quarter or the scheduler derives it.
- **`edgar-asof` reads two more feeds.** The discoverer reads EDGAR through
  `edgar-asof`
  ([0009](0009-read-edgar-through-a-public-point-in-time-library.md)). So
  the library gains readers of the feed's Atom entries and of a day's
  master index, in a release the discoverer pins. Its tasks are planned in
  its own repository.
- **The specifications change, each in a version of its own:**
  - the notices', for the feed's and the index's readings and sightings,
    the new gap, and the daily check;
  - the controls', for the two Sources and the listing's quarter;
  - the deployment's, for the chart's default Sources;
  - the telemetry's, for each feed's lag, and the filings that only one
    source showed.
- **The design follows this record once it's Accepted:** its notice
  source, and what waits on the investigation.

## Sources

- The findings SRC-1, SRC-4, SRC-5, SRC-18, SRC-19, SRC-21, SRC-23, and
  SRC-25 to SRC-28 in [`investigation/edgar.md`](../../investigation/edgar.md#findings).
- The feed's stored version, of 56,306 bytes, kept as the object
  `36a5016d80ce782d8f2aed1d77fa2b31dbeb0fa6e63edfcdd7fb2dbc352416d6`, of
  4,035 bytes.
- [The estimates](../explanation/estimates.md), and
  [scale and limits](../explanation/scale-and-limits.md).
- [SEC, Structured Disclosure RSS Feeds](https://www.sec.gov/structureddata/rss-feeds),
  for the XBRL RSS feeds and how often they're updated.
- [SEC, the PDS new subscriber document](https://www.sec.gov/files/edgar-pds-new-subscriber-document.pdf),
  for the Public Dissemination Service.
- [SEC, Accessing EDGAR Data](https://www.sec.gov/os/accessing-edgar-data),
  for the quarterly indexes, and
  [SEC, EDGAR Application Programming Interfaces](https://www.sec.gov/page/sec-api-documentation),
  for the nightly bulk archives.
