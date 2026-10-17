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
  rendering of it is what's unstable: within nine days, it changed for
  every filing in Microsoft's and Walmart's `filings.recent`, and in
  Microsoft's first further file (SRC-24, SRC-25).
- **A filing's files,** at URLs made from its accession number (SRC-21).
  Whether they ever change is for Q1 to show (T8); an amendment is a filing
  of its own (SRC-16).

What only the poll record gives:

1. **When each feed first showed a filing,** and so the lag from
   acceptance to each feed. No source records it afterwards: each day's
   master index is published about 10 PM Eastern on its date (SRC-5,
   SRC-23), so it can't show when a feed first showed a filing within the
   day.
2. **Coverage and gaps:** when the pipeline was polling, and when it
   wasn't.
3. **The feeds' own versions,** such as the rewrite SRC-24 shows.

The first is the one the pipeline can't get elsewhere, and it's a property
of each feed, measured over many filings. Keeping a record of every poll
for as long as the pipeline runs adds little to it, and most of the cost:
at 10 seconds, the three companies' polls are 25,920 records a day, 17.0
to 25.6 MB a day in PostgreSQL, before the daily base backups multiply it
([estimates](../explanation/estimates.md)). That range runs from the
estimates' low case, a list that changes only with its filings, so that
nearly every poll returns a body already held, to their high case, a list
that changes at every poll. Each poll also receives the whole body again,
a body of 158 to 185 KB on October 16, since `submissions` sends no
validators (SRC-4, SRC-22). A cadence of 1 or 2 seconds would narrow each
lag's bounds, but not the feed's own delay, and would multiply the cost
about eightfold.

## Options

- **A. Keep polling, and keep only changes.** A poll that returns the
  version its URL's latest record names adds no record. Any other poll
  adds one, as now: a new version, with its object; a return to an earlier
  version ([VER-1](../../spec/snapshots.md#7-versions)); and a poll that
  gets no version, such as a `403`
  ([FETCH-9](../../spec/snapshots.md#10-fetching)). Coverage is kept as
  intervals, each from a successful poll to the last one before the next
  change or gap. The lag's bounds, the gaps, and the live notices remain;
  most of the records go. It changes the snapshot store's rule of one
  record per fetch.
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

1. **The study.** The pipeline is built as its specifications say, with
   a record for every poll, as the snapshot store's says now. The notices
   take the version T44 writes for SRC-24 and SRC-25 before T24 builds
   the discoverer, as the owner decided ([the plan](../plan.md)). The
   pipeline then runs a bounded study of how long after acceptance
   `submissions` shows a filing, at the 10 seconds the chart's `Source`
   resources start at. Those resources poll only the three companies'
   `submissions`
   ([0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)),
   so the study measures that feed alone. It leaves the rest of Q4 open:
   how soon `companyfacts` and the daily index show a filing, and the
   latest-filings feed, which T11 weighs on what T5 records. The study's
   length, and so its end, is the owner's to set; the proposed default is
   8 weeks from the pipeline's first poll. Its lags reach `data/` through
   the transfer, T36, so findings cite them, as
   [0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)
   decided.
2. **After the study, keep only changes and coverage.** A poll that
   returns the version its URL's latest record names adds no record, and
   any other poll adds one, as in A. Coverage is kept as intervals, with
   enough of each interval's polls to bound each lag as now: the last poll
   that didn't show a filing, and the first that did. The specifications
   that define the record, or read it, say how, each in a version of its
   own: the snapshot store's, the notices', the controls', and the
   telemetry's.
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

- **The poll record doesn't change before the study.** The snapshot
  store's specification keeps a record for every poll, and T22 to T24
  build the pipeline on it, so the study has every poll to measure from.
  The notices still take T44's version before T24, as the plan says. The
  records the study adds stay, since no record is ever changed or removed
  ([LOG-3](../../spec/snapshots.md#8-history)).
- **After it, the records shrink to the lists' changes.** For three
  companies, about 1.4 new filings a day, against 25,920 polls. That holds
  only if `submissions`' body changes only with its filings; if it
  changed at every poll, keeping changes would keep every poll. The study
  shows which (Q1).
- **The snapshot store's rule changes.** One record per fetch becomes one
  record per change: a poll adds one unless it returns the version its
  URL's latest record names, so a return to an earlier version
  ([VER-1](../../spec/snapshots.md#7-versions)) and a poll that gets no
  version, such as a `403`
  ([FETCH-9](../../spec/snapshots.md#10-fetching)), still add one.
  Coverage is kept beside them. It's a major version of the store's
  specification, which says how a record stored under the old rule is
  read. What reads the poll records follows it: the notices' polls and
  gaps; the controls' `Source` status, which the scheduler sets from those
  records ([SOURCE-4](../../spec/controls.md#6-source)), and their term
  for a poll that succeeds, one whose record names a version
  ([§2 of the controls](../../spec/controls.md#2-terms)); and the
  telemetry's fetcher metrics, which count records, and its views of
  coverage.
- **The fetcher's clock check stays as it is.** The fetcher sets its skew
  from each response's `Date` as the response arrives
  ([METRIC-4](../../spec/telemetry.md#the-fetchers-clock)), not from
  records. What goes after the study is each unchanged poll's `Date`,
  which its record keeps now beside its request's and arrival's times
  ([REC-10](../../spec/snapshots.md#6-records)); the study's records keep
  every poll's.
- **T11 has its measurement of `submissions`.** Revisiting the notice
  source and its cadence, once T28's monitoring shows the lags, rests on
  the study's lags.
- **The cadence stays at 10 seconds** through the study. A shorter one
  waits for the study's measure of the feed's own delay.

## Sources

- The findings SRC-1, SRC-4, SRC-12, SRC-16, SRC-21 to SRC-25 in
  [`investigation/edgar.md`](../../investigation/edgar.md#findings-so-far).
  SRC-22 to SRC-25 are those of the first refetch.
- The record's growth in [the estimates](../explanation/estimates.md).
- The tasks T5, T11, T22 to T24, T28, T36, and T44 in
  [the plan](../plan.md).
