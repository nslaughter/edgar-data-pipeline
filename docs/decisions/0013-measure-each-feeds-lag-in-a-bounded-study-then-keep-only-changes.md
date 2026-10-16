# 13. Measure each feed's lag in a bounded study, then keep only the lists' changes and coverage

**Status:** Proposed, October 16, 2026. The owner chose this option on
October 16, 2026; it's Accepted once the owner accepts this record. Once
accepted, it supersedes the part of
[0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)
that keeps a record of every poll for as long as the pipeline runs.

## Context

[0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)
has the pipeline poll each company's `submissions` every 10 seconds, record
every poll with its times, and measure from that record how long after
acceptance each feed shows a filing. The first refetch, on October 16,
2026, and the estimates of the record's growth raise the question of what
that record is worth keeping for.

What's recoverable later, without the poll record:

- **Which filings exist.** `submissions` keeps listing a filing once it's
  listed, moving older ones to its further files (SRC-12, SRC-24), and a
  day's master index doesn't change once published (SRC-23).
- **Each filing's acceptance time,** from its header (SRC-1), which is
  part of the filing. It's the time each fact is known from
  ([0004](0004-keep-the-record-in-postgresql-18.md)). `submissions`'
  rendering of it is what's unstable: it changed for every filing of two
  companies in nine days (SRC-24, SRC-25).
- **A filing's files,** at URLs made from its accession number (SRC-21).
  Whether they ever change is for Q1 to show (T8); an amendment is a filing
  of its own (SRC-16).

What only the poll record gives:

1. **When each feed first showed a filing,** and so the lag from
   acceptance to each feed. No source records it afterwards: the daily
   index gives the date alone, published about 10 PM Eastern (SRC-23).
2. **Coverage and gaps:** when the pipeline was polling, and when it
   wasn't.
3. **The feeds' own versions,** such as the rewrite SRC-24 shows.

The first is the one the pipeline can't get elsewhere, and it's a property
of each feed, measured over many filings. Keeping a record of every poll
for as long as the pipeline runs adds little to it, and most of the cost:
at 10 seconds, the three companies' polls are 25,920 records a day, nearly
all of a body already held, 17 to 26 MB a day in PostgreSQL, before the
daily base backups multiply it
([estimates](../explanation/estimates.md)). Each poll also downloads the
whole body, 157 to 185 KB, since `submissions` sends no validators (SRC-4,
SRC-22). A cadence of 1 or 2 seconds would narrow each lag's bounds, but
not the feed's own delay, and would multiply the cost about eightfold.

## Options

- **A. Keep polling, and keep only changes.** A poll that returns the
  version already held adds no record; a new version adds one, with its
  object, as now. Coverage is kept as intervals, each from a successful
  poll to the last one before the next change or gap. The lag's bounds,
  the gaps, and the live notices remain; most of the records go. It
  changes the snapshot store's rule of one record per fetch.
- **B. Stop polling, and build nightly.** Read each day's master index once
  it's published, fetch the new filings, and take each one's time from its
  header. Simplest and cheapest, and "as of" answers are unchanged, since
  they rest on acceptance times. It gives up notices within the day, and
  any measure of a feed's lag.
- **C. Measure, then decide.** Run the pipeline as specified, a record for
  every poll, for a bounded study of each feed's lag, record the lags as
  findings, then move to A or B with the measured margin.
- **Keep 0010 as it is.** A record of every poll for as long as the
  pipeline runs.

## Decision

C, then A:

1. **The study.** The pipeline is built as the specifications now say,
   with a record for every poll, and runs a bounded study of how long
   after acceptance `submissions` shows a filing, at the 10 seconds the
   chart's `Source` resources start at. The study's length, and so its
   end, is the owner's to set. Its lags reach `data/` through the
   transfer, so findings cite them, as
   [0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)
   decided.
2. **After the study, keep only changes and coverage.** A poll that
   returns the version already held adds no record, and coverage is kept
   as intervals, with enough of each interval's polls to bound each lag as
   now: the last poll that didn't show a filing, and the first that did.
   The specifications that define the record say how, each in a version
   of its own: the snapshot store's, the notices', and the telemetry's.
3. **The record's purpose.** Each fact is known from its filing's
   acceptance time, from the header, as before
   ([0004](0004-keep-the-record-in-postgresql-18.md)). The poll record
   measures when each feed made a filing visible, and keeps the pipeline's
   own coverage; it isn't an archive of the feeds.

B stays open: if the study shows that no use needs filings within the
day, a later record can replace the polls with the nightly index.

## What the owner decided

1. **C, then A,** on October 16, 2026, over keeping 0010 as it is, A at
   once, and B.

## Consequences

- **Nothing changes before the study.** The specifications stay as they
  are, and T22 to T24 build them, so the study has every poll to measure
  from. The records it adds stay, since no record is ever changed or
  removed ([LOG-3](../../spec/snapshots.md#8-history)).
- **After it, the records shrink to the lists' changes.** For three
  companies, about 1.4 new filings a day, against 25,920 polls. That holds
  only if `submissions`' body changes only with its filings; if it
  changed at every poll, keeping changes would keep every poll. The study
  shows which (Q1).
- **The snapshot store's rule changes.** One record per fetch becomes one
  per version, with coverage beside it: a major version of the store's
  specification, which says how a record stored under the old rule is
  read. The notices' polls and gaps, and the telemetry's views of coverage,
  follow it.
- **The fetcher's clock check moves.** It reads each response's `Date`
  ([METRIC-4](../../spec/telemetry.md#the-fetchers-clock)); once
  unchanged polls add no record, it reads them from the fetcher's metrics,
  not from records.
- **T11 has its measurement.** Revisiting the notice source and its
  cadence rests on the study's lags.
- **The cadence stays at 10 seconds** through the study. A shorter one
  waits for the study's measure of the feed's own delay.

## Sources

- The findings SRC-1, SRC-4, SRC-12, SRC-16, SRC-21 to SRC-25 in
  [`investigation/edgar.md`](../../investigation/edgar.md#findings-so-far).
  SRC-22 to SRC-25 are those of the first refetch.
- The record's growth in [the estimates](../explanation/estimates.md).
