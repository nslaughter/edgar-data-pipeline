# 9. Read EDGAR through a public point-in-time library

**Status:** Accepted, October 10, 2026.

## Context

This repository is private, and the pipeline it plans is many tasks away
(the plan, proposed in pull request #10). Until it runs, none of the work can be used or
shown outside it.

The pipeline's core question is what a company reported as known at a past
time: each value kept with its period and the acceptance time of each filing
that reported it ([Times](../design.md#times)). That question is useful on
its own, without a cluster. The most used Python library for EDGAR,
edgartools, reads and parses filings; its README, read October 10, 2026,
mentions neither point-in-time nor "as of" queries.

The pipeline already divides its services by whether they read EDGAR's
content ([0006](0006-write-the-services-in-go-and-python.md)). The
discoverer, the parse workers, and the exporter read it, in Python. Only the
Go fetcher sends requests to the SEC
([0005](0005-send-sec-requests-through-one-active-fetcher.md)).

## Options

- **A package in this repository.** Specifications, decisions, and changes
  stay in one place, and a change that spans both lands in one pull request.
  It is public only when the whole repository is, and it reads as a
  component of a Kubernetes project, not as a library.
- **A separate public repository that the pipeline depends on.** It can be
  public and usable after a few sessions, with its own releases, issues, and
  versions. A change the pipeline needs is released in the library first,
  then pinned here; a local path dependency keeps that cheap while both
  change quickly.
- **No library.** The pipeline stays the only artifact, private until it
  runs.

## Decision

A separate public repository, `nslaughter/edgar-asof`, holding a Python
library released on PyPI as `edgar-asof`. The owner chose this option and
the name on October 10, 2026.

What the library does:

- **Reads EDGAR's company feeds:** `submissions`, including the older filings
  it lists in separate files; `companyfacts`; and each filing's header, for
  its acceptance time.
- **Keeps each reported value in two times:** its period, when the value was
  true, and the acceptance time of each filing that reported it, when it
  became known, as in Fowler's Bitemporal History and this design.
- **Keeps each acceptance time raw,** as its source gives it: the digits of
  the header's `ACCEPTANCE-DATETIME`, and `submissions`' `acceptanceDateTime`.
  Turning them into instants, including which time zone the header's digits
  are in, is an explicit step a caller can replace, because SRC-1 and SRC-2
  leave the zone unsettled: Apple's `submissions` times are 8 hours later
  than its headers. The owner set the same rule for the pipeline on October
  10, recording every time raw and interpreting zones in queries, so the
  library's "as of T" and the pipeline's lag queries agree.
- **Answers "as of T":** for a concept and a period, the value known at T,
  and the history of values that later filings reported for it.
- **Fetches for people who use it on its own,** under the SEC's rules for
  automated access: a user agent the caller must supply, a rate limit, and
  conditional requests. Its local cache follows the snapshot store's model
  ([`spec/snapshots.md`](../../spec/snapshots.md)): each body stored once by
  its SHA-256, each fetch a record with its request time, status, and
  validators, and nothing overwritten. Where the library's specification
  departs from that model, it says why.

What it leaves alone:

- **Filing documents and forms in general,** which edgartools reads.
- **Anything at scale:** scheduling, queues, object storage, the database,
  and monitoring stay in the pipeline.

How the pipeline uses it:

- **The Python services import a pinned version** to read EDGAR's content
  and to build facts. The Go fetcher keeps its own client under 0005; the
  library's client serves people who run the library without the pipeline,
  and follows the same rules.
- **The library's specification defines the fact model and what "as of T"
  returns.** This repository's `spec/facts.md` (T17) cites a version of it
  and adds how facts are stored. The Go query API (T26) is tested against
  cases the library publishes, so both answer the same way.

## What the owner decided

1. **The name: `edgar-asof`,** for both the repository and the PyPI package.
   It says what the library answers, and it doesn't suggest the SEC endorses
   it.
2. **The first release's facts: `companyfacts`,** each value joined to its
   filing's acceptance time through `submissions`. XBRL instances, which give
   every fact a filing reports, come later, once the parser is chosen (T12).
   T1 asks whether each `companyfacts` value joins to an acceptance time;
   the first release depends on its answer.
3. **Where the findings live: here, made public.** The library's
   specification depends on how EDGAR behaves: where acceptance times come
   from (SRC-1, SRC-2), how values join to filings (Q2, T1), and how restated
   values appear (Q3, T2). This repository becomes public, so the library
   cites its findings by link instead of restating them.
4. **The license: MIT,** for this repository and the library. A public
   repository without a license may be read but not reused. MIT is the most
   common for Python libraries, edgartools among them; Apache-2.0, which adds
   an explicit patent grant, was the alternative.

## Consequences

- **The plan changes once this is accepted.** T1 and T2 feed the library's
  specification as well as this one. T15 (the store's version 1.0.0) and
  T17 (facts) cite the library's specification, and the library's first
  release becomes tasks of its own. The plan itself is proposed in an open
  pull request (#10), so it is revised after this record is decided.
- **A public library implies releases and some upkeep.** Keeping it to
  `submissions`, `companyfacts`, and headers keeps that light: those
  feeds change less often than filing formats and taxonomies.
- **The owner's site can link the library** as demonstration code for its
  point-in-time claims once a release exists.

## Sources

- [Martin Fowler, Bitemporal History](https://martinfowler.com/articles/bitemporal-history.html)
- [SEC, Accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
- [SEC, EDGAR application programming interfaces](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [edgartools README](https://github.com/dgunning/edgartools)
