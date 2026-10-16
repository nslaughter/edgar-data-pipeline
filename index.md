# EDGAR data pipeline

<!-- status: all -->
**What's built,** as the [status page](docs/status.md) gives it:

- **Built:** NATS JetStream; Garage; PostgreSQL 18; The snapshot store in git; The charts.
- **Specified:** Scheduler; Fetcher; Discoverer; Monitoring: traces; The snapshot store in the cluster (built in part); The transfer from the cluster to git; The controls; The documentation site (built in part).
- **Decided:** Parse workers; Exporter; Query API; Monitoring: metrics, alerts, and dashboards (specified in part; built in part); Monitoring: logs; `edgar-asof`.
- **Planned:** The fake EDGAR; The drills.
<!-- end status -->

## What the pipeline is

A pipeline for the filings companies make with the U.S. Securities and
Exchange Commission, read from its EDGAR system. EDGAR's `submissions`
lists a company's filings, and `companyfacts` its XBRL values, each value
with the accession number of the filing that reported it
([SRC-9](investigation/edgar.md#findings-so-far),
[SRC-14](investigation/edgar.md#findings-so-far)); how often their content
changes is still an open question of the investigation
([Q1](investigation/edgar.md#questions)). The pipeline keeps a record of
its own, in three ways, and claims no more than these
([what the pipeline keeps](docs/design.md#what-the-pipeline-keeps)):

1. **Every filing in its scope, with its acceptance time from the filing
   itself** ([SRC-1](investigation/edgar.md#findings-so-far)), so what was
   known on a date follows from the filings accepted by then.
2. **Every version of a feed it observed, never overwritten,** each body
   stored once by its SHA-256 ([OBJ-1](spec/snapshots.md#5-objects),
   [OBJ-2](spec/snapshots.md#5-objects)).
3. **Where it was looking, and where it wasn't.** Each poll is recorded, so
   coverage and gaps are part of the record.

It holds a small scope, a few companies, not all of EDGAR, and doesn't
recover what it didn't observe. It runs on Kubernetes, installed with Helm
on kind ([components](docs/design.md#components)), and reads EDGAR's content
through `edgar-asof`, a library in a repository of its own
([0009](docs/decisions/0009-read-edgar-through-a-public-point-in-time-library.md)).

The list above says which parts exist. Most of the design is specified and
not yet built; the [status page](docs/status.md) gives each part, with the
tasks that build it.

## How the site is arranged

The site publishes the repository's documents where they are, so each
page's path follows its file's ([SITE-1](spec/site.md#4-what-the-site-holds)):

- **Explanation:** the [design](docs/design.md), which explains the
  architecture and makes no rules, and the
  [decision records](docs/decisions/README.md), one for each significant
  choice, with the options weighed.
- **Reference:** the specifications, whose numbered statements the code and
  tests cite, and the [investigation of EDGAR](investigation/edgar.md),
  whose findings cite the stored responses that show them.
- **Status:** the [status page](docs/status.md), and the
  [plan](docs/plan.md), which orders the tasks and records which are done.

Where a page and a specification disagree, the specification governs.

## The data's source

The data the pipeline keeps comes from the U.S. Securities and Exchange
Commission's EDGAR system. This is an independent project, not affiliated
with or endorsed by the SEC.
