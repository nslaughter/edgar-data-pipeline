# 10. Poll each company's `submissions`, and measure lag from the record

**Status:** Proposed, October 10, 2026.

## Context

The pipeline's notice source and its cadence were to wait on the
investigation's Q4: how long after acceptance each feed shows a new filing
([what waits on the investigation](../design.md#what-waits-on-the-investigation)).
The investigation planned to measure it by polling Microsoft's and Apple's
feeds every minute around their next reports. A minute can't tell a delay of
a second from one of fifty. Polling finer for weeks would outgrow the git
store: at about 526 bytes a record, polling three companies every 5 seconds
for four hours each business day adds about 70 MB to one log in three weeks,
past the size at which GitHub warns
([open question 2](../../spec/snapshots.md#13-open-questions)).

The SEC says `submissions` is updated in real time as filings are
disseminated, typically within a second, and its XBRL APIs, `companyfacts`
among them, within a minute, or longer at peak times. That is documentation,
not a finding. In the baseline's `submissions`, the three companies filed
126 times in the 13 weeks to October 7, 2026, mostly Form 4s, and about 90%
of Microsoft's and Walmart's filings were accepted between 4 and 7 PM US
Eastern time.

On October 10, 2026, the owner decided that the running pipeline, with good
observability, measures this: it records every relevant time as part of the
record, and each lag is a query.

## Options

- **Wait for the investigation.** Choose the source and cadence after
  watching filings arrive, as planned. Notices wait for weeks of watching,
  and the watching measures only to its polling interval.
- **Start with each company's `submissions`, and measure in the pipeline.**
  The baseline already shows what it holds and that it sends no validators
  (SRC-2, SRC-4). Requests grow with the number of companies.
- **Start with EDGAR's latest-filings feed.** One request covers every
  company. Its URL isn't confirmed, the scope must be widened to it, it
  changes on nearly every poll, so the store keeps a body each time, and a
  poll that comes too late can miss filings in a busy burst.
- **Start with the daily indexes.** Complete and kept in the archive, but
  published around 10 PM (SRC-5), so a filing is first seen hours after its
  acceptance.

## Decision

Start with each company's `submissions`, polled every 10 seconds, a cadence
each `Source` holds and the owner can change
([0007](0007-control-the-pipeline-through-custom-resources.md)). The
pipeline records every time raw, as its source gives it, and measures each
lag from the record:

- **for each fetch,** when the request was sent and when the response
  arrived, the response's `Date`, and its `Last-Modified` when sent;
- **for each filing,** the digits of the header's `ACCEPTANCE-DATETIME` and
  `submissions`' `acceptanceDateTime`, as given;
- **for each filing and each source that lists filings,** the last poll that
  didn't show the filing and the first that did.

Time zones are interpreted in queries, not when times are written, as
`edgar-asof` does
([0009](0009-read-edgar-through-a-public-point-in-time-library.md)),
because SRC-1 and SRC-2 leave the header's zone unsettled.

## Consequences

- **Q4 is answered from the record,** once the fetcher and the discoverer
  run, not before. So the investigation finishes after the pipeline's first
  part runs. The notices specification relies on what the baseline already
  shows (SRC-2, SRC-4), not on the lags. The first lags come after the
  late-October reports, but any filing measures them, and the archive keeps
  the reports' content for Q2 and Q3.
- **A filing's public availability lies between its acceptance and its
  first-seen time.** At 10 seconds, that bounds it within about 10 seconds
  plus the source's own delay. Over many filings, the smallest lags observed
  approach the source's delay, because polls fall at random moments relative
  to acceptance.
- **The load is small.** Three companies every 10 seconds is 0.3 requests a
  second, 6% of the limit, and 25,920 records a day in PostgreSQL. Each poll
  downloads the whole body, 20 to 27 KB compressed, since `submissions`
  sends no validators (SRC-4). At this cadence, half the limit covers about
  25 companies; past that, a feed covering every company becomes the better
  source.
- **The specifications record the times.** The store's version 1.0.0 adds
  the response's `Date` and arrival time to each record, and the notices
  specification defines each filing's times and the queries that give each
  lag.
- **A lag is only as good as its clocks.** The fetcher's clock is checked
  against the SEC's `Date`. Until the header's zone is settled, Apple's
  8-hour difference (SRC-2) shows in the lag queries as an outlier.
- **Findings about arrival rest on the pipeline's records,** which live in
  the cluster. The store's version 1.0.0 says how they reach `data/`, so a
  finding can cite them as it cites any stored version. Its proposed
  default: the pipeline writes the records of the git store's scope to
  `data/` in the existing format, with their bodies.
- **The source is revisited from the lags the record shows:** whether to
  keep each company's `submissions`, add the latest-filings feed, or change
  the cadence. A change is a later record that supersedes this one.
- **The investigation drops its minute-by-minute watch.**

## Sources

- [SEC, EDGAR application programming interfaces](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
