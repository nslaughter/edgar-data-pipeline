# Glossary

The terms the explanation pages use that a reader outside the project may
not know, grouped by where they come from: EDGAR, the pipeline,
`edgar-asof`, the platform the pipeline runs on, and the project's
documents. Each links to the statement, finding, or document that defines
it, where one does, and that document governs. The facts about
`edgar-asof` here were checked against its `main` on October 14, 2026, at
commit `515bb0d`. The data the pipeline keeps comes from the U.S.
Securities and Exchange Commission's EDGAR system.

## EDGAR

### 10-K and 10-Q

The forms of a company's periodic reports: a 10-K reports a fiscal year,
and a 10-Q a quarter of it, numbered from the start of the fiscal year
([SRC-3], [SRC-7]). An amendment of one is a 10-K/A or a 10-Q/A
([amendment](#amendment)).

### Acceptance time

When the SEC accepted a filing, and so when what it reports became known.
EDGAR gives it twice: as the header's `ACCEPTANCE-DATETIME`, 14 digits with
no time zone ([SRC-1]), and as `submissions`' `acceptanceDateTime`, in UTC.
Since the first refetch, on October 16, 2026, the second is later than the
header's digits read as US Eastern time, by the Eastern offset from UTC on
the filing's date, for each of the three companies' filings; on October 7,
only Apple's were ([SRC-2], [SRC-25]). `submissions` can change it for a
filing it already lists ([SRC-24]), and the stored versions don't show
which time is right ([SRC-25]). The pipeline keeps both as given, and reads
time zones only in queries ([times][design-times], [0010]).

### Accession number

The identifier of one filing, written `##########-##-######` ([§2 of the
notices][notices-terms]). Its first ten digits can be another filer's CIK:
Apple's 8-K/A's begins with 0001140361 ([SRC-2]). A filing's files are at
URLs made from its company's CIK and its accession number ([SRC-21]).

### Amendment

A filing that amends an earlier one, such as a 10-K/A. It's a new filing,
with its own accession number and acceptance time, not an edit
([what the pipeline keeps][design-keeps]). In `companyfacts`, the amended
filing's values stay beside the amendment's ([SRC-16]).

### CIK

The number that names a filer, such as a company
([`facts.md`, §2][asof-facts-terms]). A filing's header gives it as the
filer's `CENTRAL INDEX KEY`. A feed's URL gives it padded to ten digits,
and EDGAR's archive without leading zeros ([SRC-21]).

### `companyfacts`

A company's XBRL values, at
`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
([feeds][inv-feeds]). Each value has its period, its form, its filing
date, the fiscal year and period of the filing that reported it, not of the
value ([SRC-6]), and its filing's accession number, which joins it to an
acceptance time through `submissions` ([SRC-9], [SRC-12]). It keeps each
filing's value for a concept, unit, and period beside every other filing's
([SRC-14]). The SEC rewrites it in place
([what the pipeline keeps][design-keeps]).

### Daily index

Each business day's list of the filings accepted, under
`https://www.sec.gov/Archives/edgar/daily-index/` ([feeds][inv-feeds]).
Its files were last modified between 10:01 and 10:04 PM on their day, with
no time zone given ([SRC-5]), so a filing first seen there is seen hours
after its acceptance ([0010]).

### Filing

An event: one submission to the SEC, with its own accession number and its
own files. What was known on a date follows from the filings accepted by
then ([what the pipeline keeps][design-keeps]). To the discoverer, a filing
is an accession number that a list names
([§2 of the notices][notices-terms]).

### Fiscal period

The fiscal year, and the period within it, that a report covers. Each
company's fiscal year ends on its own date ([scope][inv-scope]). In
`companyfacts`, `fy` and `fp` give the fiscal year and period of the filing
that reported a value, not of the value ([SRC-6]); `fy` names a year by the
calendar year it ends in, except in Walmart's filings through March 2014,
and `fp` numbers a quarter from the start of the fiscal year ([SRC-7]). A
value's own period is its start and end dates ([FACT-7]), which are its
valid time ([valid time and transaction time](#valid-time-and-transaction-time)).

### Form

A filing's type, such as `10-K`, `10-Q`, `8-K`, or `4`, as `submissions`
and `companyfacts` give it ([SRC-9]). Most of the three companies' filings
are Form 4s ([SRC-19]).

### Frame

The calendar period `companyfacts` matches to a value's own, such as
`CY2025Q3I`. It's neither the value's period nor a fiscal one, and only
some values have one ([SRC-8]): of a key's values, only the latest filed
([SRC-17]).

### Further file

One of the files that list a company's older filings, which `submissions`
names in `filings.files`, in the same columns as its recent filings
([SRC-12]).

### Header

A filing's `ACCESSION-index-headers.html`, in its directory in EDGAR's
archive ([SRC-21]). It gives the filing's acceptance time as
`ACCEPTANCE-DATETIME` ([SRC-1]), and the filer's `FISCAL YEAR END`, which
can differ between a company's filings ([SRC-3]).

### Index page

A filing's `ACCESSION-index.html`. It marks the filing's XBRL instance as
`EXTRACTED XBRL INSTANCE DOCUMENT`, with its size, and in each of the eight
filings stored, it gives the same acceptance time as the header
([SRC-20], [SRC-21]).

### `submissions`

A company's filings, at `https://data.sec.gov/submissions/CIK##########.json`,
each with its accession number, form, dates, and acceptance time
([feeds][inv-feeds]). Its `filings.recent` holds about 1,000 recent filings,
and its further files the older ones ([SRC-9], [SRC-12]). It sends no
validators, so each request downloads the whole body ([SRC-4]). The
pipeline polls each company's `submissions` to see its new filings
([0010]).

### Validators

The response headers `ETag` and `Last-Modified`, which a later request can
send back, as a conditional request, to receive the body only if it changed
([FETCH-4]). Which ones EDGAR sends depends on the kind of URL ([SRC-4]).

### XBRL instance

The file in which a filing gives, in XBRL, the facts it reports
([0009], [SRC-14]). Its filing's index page names it ([SRC-20]). The parse
workers read facts from it ([components][design-components]). In the
baseline, a 10-K's or a 10-Q's is 0.9 MB to 10.9 MB ([SRC-18]).

## The pipeline

### Backfill

A custom resource that asks the pipeline to fetch a company's past filings
over a range of filing dates, once. Its status counts the filings fetched
and failed, and says when it's complete ([§7 of the controls][controls-backfill]).
Its fetches wait for every other fetch, so a backfill never delays the
record of new filings ([BACKFILL-2]).

### Body

A response's content, after any `Content-Encoding` is removed
([§2 of the store][store-terms]).

### Budget

Half the fetcher's limit of 5 requests a second: 2.5 a second, which the
Sources not paused share. The other half is left for each new filing's
fetches and for backfills ([§2 of the controls][controls-terms], [VAL-3]).

### Cadence

A Source's `spec.cadenceSeconds`: the seconds from one of its polls to the
next, at least 1 ([§2 of the controls][controls-terms],
[§6 of the controls][controls-source]). The chart's Sources for the three
companies start at 10 seconds ([0010]). Continuous and daily operation
differ only in cadence.

### Coverage

The time a Source's successful polls covered: from each successful poll's
request to the next one's, when the next follows it without a missed slot,
and otherwise to when its job expired ([§5 of the notices][notices-gaps]).
Between them, coverage and gaps say where the pipeline was looking, and
where it wasn't.

### Drill

An outage caused on purpose on kind, such as deleting the active fetcher or
blocking the SEC, to show the pipeline recovering and the gaps it recorded
([outages and recovery][design-outages], [T31]).

### Event

A message in the stream `RECORDS` that announces the record of one fetch
([§2 of the store][store-terms], [jobs and events][store-jobs]).

### Export

The facts, written as Parquet files in Garage, deterministically, with each
file's SHA-256 ([components][design-components], [0004]). Its format is
for its specification to give ([T18]).

### Fake EDGAR

A service for tests that answers for the SEC's hosts from the stored
versions under `data/`, gives the failures a test cues, and logs every
request its server receives, so the fetching rules are checked without the
SEC ([`fake-edgar.md`][fake-edgar], [testing on kind][design-testing],
[T21]).

### Feed

A named set of URLs fetched for one purpose, such as `submissions`
([§2 of the store][store-terms], [feeds][inv-feeds]).

### First-seen time

For each Source, the first of its polls that showed a filing, after the
last that didn't. After a gap, it's bounded and flagged
([times][design-times]). The record keeps the two polls as a
[sighting](#sighting).

### Gap

Time in which a Source's coverage lapsed: when a successful poll doesn't
follow the one before it, the time from when the earlier poll's job expired
to the later poll's request ([§5 of the notices][notices-gaps]). A missed
poll leaves a gap of about a cadence; a pause, a stop after a `403`, or a
fetcher's handover that misses a poll leaves one until polling resumes. The
record says the gap was there rather than implying nothing changed
([what the pipeline keeps][design-keeps]).

### Import

A program run that reads stored bodies and produces output from them, such
as a parse worker's ([§2 of the store][store-terms],
[§11 of the store][store-imports]).

### Import manifest

The file an import writes before it reads its inputs, naming each input by
its feed, URL, and SHA-256, so that a rerun with the same inputs reads the
same bytes ([IMP-1] to [IMP-3]).

### Job

A message in the stream `JOBS` that asks the fetcher for one fetch. Its
kind says what asked for it: a Source's poll, a filing the discoverer
found, or a Backfill. The fetcher takes polls first
([§2 of the store][store-terms], [jobs and events][store-jobs], [JOB-4]).

### Lag

The time from a filing's acceptance to when a Source first showed it,
bounded below and above by the two polls of its sighting, from each of the
filing's two acceptance times ([§10 of the notices][notices-lags],
[LAG-1]). Each lag is a query over the record ([0010]).

### List

A version of a company's `submissions`, or of one of its further files,
read for the filings it names ([§2 of the notices][notices-terms]).

### Notice

A message the discoverer publishes in the stream `NOTICES`: of a filing
whose files are held, of a change in a Source's open gap, or of a
Backfill's progress ([§2 of the notices][notices-terms],
[streams][notices-streams]).

### Object

A stored body, kept compressed at `objects/AA/SHA256.gz` and named by the
SHA-256 of its bytes, so the same body is stored once
([§2 of the store][store-terms], [OBJ-1]).

### Open gap

A gap not yet ended: the latest successful poll's job has been expired for
as long as its window, with no later successful poll, and the gap runs from
when that job expired ([§5 of the notices][notices-gaps]). A Source's
status gives when it began ([SOURCE-5]).

### Order

A message in the stream `ORDERS` by which the scheduler hands a Backfill to
the discoverer ([§2 of the notices][notices-terms],
[§8 of the notices][notices-backfills]).

### Part

A component, store, control, or capability of the pipeline that the status
page lists, such as the fetcher or the drills
([§2 of the site][site-terms], [STATUS-2]).

### Poll

One fetch of a Source's URL, queued by the scheduler at the Source's
cadence. It succeeds when its record names a version
([§2 of the controls][controls-terms]). Its job expires when the next poll
is due ([JOB-2]).

### Record

One fetch: its URL, its times, its status, and the version it returned, as
a line of a feed's log in the git store or a row of the table `records` in
the cluster ([§6 of the store][store-records]). **The record**, without
more, is all that the pipeline's database holds: the records, and the
discoverer's tables ([§2 of the notices][notices-terms]).

### Requests are allowed

While the SecAccess `cluster` exists, isn't paused, and has no stop the
owner hasn't acknowledged ([§2 of the controls][controls-terms],
[§8 of the controls][controls-secaccess]).

### Scope

The URLs the git store may hold, each with its reason, in
`data/scope.json`, which only the owner widens
([§9 of the store][store-scope]). What the cluster fetches is set by the
Sources and Backfills instead ([the scope and the cluster][store-scope-cluster]).

### SecAccess

A custom resource, one per cluster, named `cluster`, that says whether the
pipeline may send requests to the SEC at all. The owner can pause it. After
a `403`, the fetcher records the stop in its status, and nothing is sent
until the owner acknowledges that stop
([§8 of the controls][controls-secaccess], [ACCESS-3]).

### Sighting

A filing and a Source whose polls showed it, with the two polls that bound
when the Source first showed it: the last whose list didn't name it, and
the first that did ([§2 of the notices][notices-terms],
[§6 of the notices][notices-seen]).

### Snapshot store

The pipeline's two stores of what it fetches: the git store, everything
under `data/`, committed to git; and the cluster store, objects in Garage
and records in PostgreSQL ([§2 of the store][store-terms]). Nothing stored
is changed or removed ([§1 of the store][store-guarantees]).

### Source

A custom resource for one URL the pipeline polls, such as one company's
`submissions`, with the feed its polls are recorded under, its cadence, and
whether it's paused. Its status gives its last polls and any open gap
([§6 of the controls][controls-source]).

### Status

A part's stage, derived from the plan, the specifications, and the decision
records: the furthest of Built, Specified, Decided, and Planned it has
reached, with a later stage reached in part named after it ([STATUS-1]).
The [status page](status.md) gives each part's. A custom resource has a
`status` too, which only the services set
([§9 of the controls][controls-rbac]).

### Transfer

A program the owner runs that copies the records of the git store's scope,
with their objects, from the cluster store into `data/`, for a range of
time the owner names, so that findings can cite them
([§2 of the store][store-terms], [XFER-1]).

### Valid time and transaction time

The two times of each fact: its valid time is the fiscal period or instant
it describes, and its transaction time is the acceptance time of the filing
that reported it, never the database's write time ([times][design-times],
[0004]).

### Version

A distinct body among one URL's records in a store, numbered in the order
each first appears. A URL that returns to an earlier body keeps that body's
number ([VER-1]).

## `edgar-asof`

The public library the pipeline's Python services read EDGAR's content
through, in a repository of its own ([0009]). Its specifications define
these terms.

### As of T

For one key, an instant T, and an interpreter, which of the key's facts
were known at T, by the acceptance of their filings alone. The facts placed
at the latest instant at or before T are `known`, those placed earlier
`earlier`, those after T `later`, and those the interpreter couldn't place
`unplaced` ([ASOF-1] to [ASOF-3]). The design summarizes the answer as
the value from the latest filing accepted at or before T, for a key that
includes dimensions ([times][design-times]). The library's answer has four
parts, and its key has no dimensions; the
[query API](explanation/query-api.md#where-the-design-and-the-librarys-specification-differ-in-detail)
page sets out where the two differ. The pipeline's query API answers it
([T26]).

### Cache

A directory where the library keeps the bodies it fetched and their
records, in the snapshot store's layout without its scope and imports. It
can read a store's `data/` as a cache, and never fetches into one
([`fetching.md`, §6][asof-cache]).

### Fact

A value that `companyfacts` gives, with its key and its filing
([`facts.md`, §2][asof-facts-terms], [§5][asof-facts-facts]). Every value is
a fact, kept as given, beside every other value for its key ([FACT-6]). The
library's first release makes facts from `companyfacts`; the pipeline's
parse workers make them from XBRL instances, through a later release
([0009], [T25]).

### Instant

A point in time, in UTC ([`times.md`, §1][asof-times-terms]).

### Interpreter

A function that turns a filing's raw times into one instant, or nothing if
it can't ([`times.md`, §1][asof-times-terms], [TIME-6]). The library
provides two: one reads `submissions`' time as the UTC instant it states,
and one reads the header's digits in a time zone the caller names. It has
no default, so the caller always passes one ([TIME-7] to [TIME-9]).

### Key

A company, a concept, a unit, and a period: an end, and for most durations
a start ([`facts.md`, §2][asof-facts-terms], [FACT-7]). The design's key
also has dimensions ([times][design-times]), which no `companyfacts` value
has; facts read from XBRL instances may need them, in a later version
([`facts.md`, left to other specifications][asof-facts-left]).

### Placed

A fact is placed at the instant an interpreter gives its filing, and
unplaced when the interpreter gives nothing. It's known at T when it's
placed at or before T ([`facts.md`, §2][asof-facts-terms]).

### Published cases

Cases of "as of T", with their inputs and answers, that the library is to
publish, so that the pipeline's query API is tested against them and both
answer the same way ([0009], [`edgar-asof`'s plan, T10][asof-later]).

### Raw time

An acceptance time as its source gives it: the header's 14 digits, or
`submissions`' string ([`times.md`, §1][asof-times-terms], [TIME-1],
[TIME-2]). Raw times are compared, ordered, or converted only through an
interpreter ([TIME-5]).

## The platform

### Admission webhook

A service that the Kubernetes API server asks to accept or refuse a
resource before it stores it. One whose `failurePolicy` is `Fail` refuses
the resources it admits until its pods are ready ([0011]). No webhook
checks the pipeline's own resources for now ([0010]).

### Base backup

A copy of the whole database as it is at one moment, which, with the WAL
archived after it, restores the database to any later moment. The
database takes one at least once a day, into Garage ([PG-4]). No statement
says how long they're kept, so the
[estimates](explanation/estimates.md#the-databases-archive) keep every
one.

### Chart

A Helm chart: Kubernetes manifests, templated from values, that Helm
installs as a release. The pipeline has four, under `deploy/`, installed in
order; the last, the umbrella chart, installs the platform's components,
the database, and later the pipeline's services and controls
([§2 of the deployment][deployment-terms], [0011]).

### CloudNativePG

The operator that runs the pipeline's PostgreSQL: a `Cluster` of three
instances, with backups to Garage through its Barman Cloud plugin ([0004],
[PG-3], [PG-4]).

### Controller

A program that watches resources in the Kubernetes API server and acts to
make the cluster match them. The scheduler is the controller of the
pipeline's custom resources ([0007]).

### Custom resource

A resource of a kind that Kubernetes doesn't define itself, but that a
definition adds to the API server, which then keeps it through restarts and
serves it like any other ([0007]). The pipeline's controls are three:
Source, Backfill, and SecAccess ([§4 of the controls][controls-resources]).

### Definition

A `CustomResourceDefinition`, which adds a kind of custom resource to the
API server. A resource can be created only once its kind's definition is
on the cluster ([§2 of the deployment][deployment-terms],
[definitions][deployment-definitions]).

### Durable consumer

A JetStream consumer with a name, which keeps its place in its stream, the
messages it has delivered and those acknowledged, for whichever of its
service's replicas reads from it, and across their restarts and NATS's. A
stream kept by interest keeps a message only for the consumers it has when
the message is published, so the deployment makes each durable consumer
right after its stream, before a stream kept by interest takes its
subjects, and before the streams whose messages lead to what it reads. Each
of the pipeline's is a pull consumer, from which its service's replicas ask
for messages ([§2 of the deployment][deployment-terms], [NATS-5]).

### envtest

A test harness of controller-runtime that runs a real Kubernetes API server
for tests, without the rest of a cluster. The scheduler is tested with it
([0007]).

### Garage

An S3-compatible object store, one binary that replicates across nodes.
The pipeline runs three nodes with a replication factor of 3, holding
bodies, exports, and the database's backups ([0002], [GARAGE-1]).

### Helm release

One installation of a chart on a cluster, which Helm records, upgrades, and
uninstalls as a whole. The pipeline is four releases, installed in order
([0011], [§5 of the deployment][deployment-dependencies]).

### Host directory

The directory of the machine kind runs on that's mounted into every worker
node, where every volume's data is kept, so that stored data outlives the
cluster ([§2 of the deployment][deployment-terms], [KIND-2]).

### kind

Kubernetes in Docker: a cluster whose nodes are containers on one machine.
The pipeline's has one control-plane node and three worker nodes, and runs
for development and in CI ([0001], [KIND-1]).

### kube-prometheus-stack

The Prometheus community's chart that installs the Prometheus operator,
Prometheus, Alertmanager, Grafana, kube-state-metrics, and node-exporter
([0008]).

### kube-state-metrics

The exporter that kube-prometheus-stack installs, which exports the state
of Kubernetes resources as metrics, the controls' status among them
([§2 of the telemetry][telemetry-terms],
[§5 of the telemetry][telemetry-state]).

### Lease

A Kubernetes object that one of several replicas holds, and renews, to be
the one that acts. The fetcher's Lease, `fetcher`, decides which of its two
replicas sends requests and takes jobs; another replica takes it over only
once its duration, 15 seconds by default, has passed without a renewal,
even when Kubernetes stopped the replica that held it ([0005], [FETCH-16],
[LEASE-1], [FETCHER-3]). The scheduler's leader election uses a Lease of its
own, `scheduler`, which a replica that Kubernetes stops releases, so
another takes it over without waiting out its duration ([0007], [SCHED-3],
[SCHED-4]).

### NATS JetStream

NATS's persistence layer: durable streams, acknowledgements, and
redelivery. The pipeline runs it as one cluster of three servers ([0003],
[NATS-1]).

### Operator

A controller that runs a piece of software on Kubernetes from custom
resources: CloudNativePG runs the database from its `Cluster`, and the
Prometheus operator runs Prometheus and its rules ([0004], [0008],
[0011]).

### promtool

Prometheus's command-line tool. `promtool test rules` tests alert rules
against series given over time, in CI ([0008]).

### Retention

How long a store keeps what it holds before it removes it. Nothing stored
in the snapshot store is removed ([§1 of the store][store-guarantees]).
Prometheus keeps its data for a set time, and the database's backups are
kept as long as their policy says; no statement sets either, so the
[estimates](explanation/estimates.md#the-inputs) take each tool's
default.

### Series

As Prometheus has it: a metric with one set of its labels' values, sampled
at each scrape. Each of the pipeline's labels takes its values from a small
set, so its series grow with its Sources and Backfills, not with its
filings ([METRIC-2]).

### Span and trace

As OpenTelemetry has them: a trace follows one filing through the
services, and each step in it is a span. Each message carries its trace
context in its NATS headers ([§2 of the telemetry][telemetry-terms],
[§9 of the telemetry][telemetry-traces]).

### Stream

A NATS JetStream stream: a durable log of the messages on a set of
subjects, kept until its retention lets them go. `JOBS` and `ORDERS` are work
queues, which keep a message until it's acknowledged; `RECORDS` and
`NOTICES` keep one until each of their consumers acknowledges it
([jobs and events][store-jobs], [streams][notices-streams], [NATS-3]).

### Worker node

A node of the kind cluster other than its control plane. NATS, Garage, and
the database each keep one replica on each of the three
([§2 of the deployment][deployment-terms], [PLACE-1]).

### Write-ahead log (WAL)

PostgreSQL's log of each change, written before the change itself, from
which an instance recovers and its standbys follow it. The database
archives it into Garage continuously, with its base backups, in segments
of 16 MiB by PostgreSQL's default ([PG-4];
[estimates](explanation/estimates.md#the-databases-archive)).

## The documents

### Analysis

What an explanation page says about something no task, decision record, or
specification covers, set apart in a block that says so. It says only what
the pipeline could do ([§2 of the site][site-terms], [PAGE-7]).

### Decision record

A record of one significant choice, with its context, its options, the
decision, and its consequences. It's Proposed until the owner accepts it,
and a later record supersedes it rather than editing it
([decision records](decisions/README.md)).

### Finding

A statement of how EDGAR behaves, with an ID such as SRC-2, that cites the
stored versions that show it. The specifications depend on EDGAR only
through findings ([findings][inv-findings],
[where documents go][agents-where]).

### Invariant and rule

The two kinds of statement. An invariant is a property of something at
rest, such as stored data or a schema; a rule is how a program, a service,
or the owner behaves while it runs ([§3 of the store][store-ids]).

### Statement

A numbered normative sentence in a specification, with an ID such as
FETCH-6, made of an area and a number, never reused. Each test carries the
IDs of the statements it checks ([§3 of the store][store-ids],
[rules][agents-rules]).

### Task

One unit of work in the [plan](plan.md), done in one pull request whose
title ends with its ID, such as T24 ([how the plan works][plan-how]).

### The owner

The person who runs the pipeline and decides what the documents leave
open. A statement checked by `operator` is the owner's to keep
([§3 of the controls][controls-ids]).

[0001]: decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md
[0002]: decisions/0002-store-bodies-in-garage.md
[0003]: decisions/0003-queue-work-in-nats-jetstream.md
[0004]: decisions/0004-keep-the-record-in-postgresql-18.md
[0005]: decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0007]: decisions/0007-control-the-pipeline-through-custom-resources.md
[0008]: decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md
[0009]: decisions/0009-read-edgar-through-a-public-point-in-time-library.md
[0010]: decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[0011]: decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md
[design-keeps]: design.md#what-the-pipeline-keeps
[design-times]: design.md#times
[design-components]: design.md#components
[design-outages]: design.md#outages-and-recovery
[design-testing]: design.md#testing-on-kind
[plan-how]: plan.md#how-the-plan-works
[T18]: plan.md#facts
[T21]: plan.md#observing-filings
[T25]: plan.md#facts
[T26]: plan.md#facts
[T31]: plan.md#traces-logs-and-drills
[agents-rules]: ../AGENTS.md#rules
[agents-where]: ../AGENTS.md#where-documents-go
[inv-scope]: ../investigation/edgar.md#scope
[inv-feeds]: ../investigation/edgar.md#feeds
[inv-findings]: ../investigation/edgar.md#findings
[fake-edgar]: ../spec/fake-edgar.md
[store-guarantees]: ../spec/snapshots.md#1-guarantees
[store-terms]: ../spec/snapshots.md#2-terms
[store-ids]: ../spec/snapshots.md#3-statement-ids
[store-records]: ../spec/snapshots.md#6-records
[store-scope]: ../spec/snapshots.md#9-scope
[store-scope-cluster]: ../spec/snapshots.md#the-scope-and-the-cluster
[store-jobs]: ../spec/snapshots.md#jobs-and-events
[store-imports]: ../spec/snapshots.md#11-imports
[controls-terms]: ../spec/controls.md#2-terms
[controls-ids]: ../spec/controls.md#3-statement-ids
[controls-resources]: ../spec/controls.md#4-the-resources
[controls-source]: ../spec/controls.md#6-source
[controls-backfill]: ../spec/controls.md#7-backfill
[controls-secaccess]: ../spec/controls.md#8-secaccess
[controls-rbac]: ../spec/controls.md#9-who-may-change-what
[deployment-terms]: ../spec/deployment.md#2-terms
[deployment-dependencies]: ../spec/deployment.md#5-dependencies
[deployment-definitions]: ../spec/deployment.md#definitions
[notices-terms]: ../spec/notices.md#2-terms
[notices-gaps]: ../spec/notices.md#5-polls-coverage-and-gaps
[notices-seen]: ../spec/notices.md#6-when-each-source-first-showed-each-filing
[notices-backfills]: ../spec/notices.md#8-backfills
[notices-streams]: ../spec/notices.md#streams
[notices-lags]: ../spec/notices.md#10-times-and-lags
[telemetry-terms]: ../spec/telemetry.md#2-terms
[telemetry-state]: ../spec/telemetry.md#5-metrics-of-the-controls
[telemetry-traces]: ../spec/telemetry.md#9-traces
[site-terms]: ../spec/site.md#2-terms
[asof-cache]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/fetching.md#6-the-cache
[asof-facts-terms]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#2-terms
[asof-facts-facts]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#5-facts
[asof-facts-left]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#left-to-other-specifications
[asof-times-terms]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/times.md#1-terms
[asof-later]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/docs/plan.md#later
[FACT-6]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#5-facts
[FACT-7]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#5-facts
[ASOF-1]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#7-as-of-t
[ASOF-3]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#7-as-of-t
[TIME-1]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/times.md#3-raw-times
[TIME-2]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/times.md#3-raw-times
[TIME-5]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/times.md#3-raw-times
[TIME-6]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/times.md#4-interpreters
[TIME-7]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/times.md#4-interpreters
[TIME-9]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/times.md#4-interpreters
[SRC-1]: ../investigation/edgar.md#findings-so-far
[SRC-2]: ../investigation/edgar.md#findings-so-far
[SRC-3]: ../investigation/edgar.md#findings-so-far
[SRC-4]: ../investigation/edgar.md#findings-so-far
[SRC-5]: ../investigation/edgar.md#findings-so-far
[SRC-6]: ../investigation/edgar.md#findings-so-far
[SRC-7]: ../investigation/edgar.md#findings-so-far
[SRC-8]: ../investigation/edgar.md#findings-so-far
[SRC-9]: ../investigation/edgar.md#findings-so-far
[SRC-12]: ../investigation/edgar.md#findings-so-far
[SRC-14]: ../investigation/edgar.md#findings-so-far
[SRC-16]: ../investigation/edgar.md#findings-so-far
[SRC-17]: ../investigation/edgar.md#findings-so-far
[SRC-18]: ../investigation/edgar.md#findings-so-far
[SRC-19]: ../investigation/edgar.md#findings-so-far
[SRC-20]: ../investigation/edgar.md#findings-so-far
[SRC-21]: ../investigation/edgar.md#findings-so-far
[SRC-24]: ../investigation/edgar.md#findings-so-far
[SRC-25]: ../investigation/edgar.md#findings-so-far
[OBJ-1]: ../spec/snapshots.md#5-objects
[VER-1]: ../spec/snapshots.md#7-versions
[XFER-1]: ../spec/snapshots.md#records-the-cluster-made
[FETCH-4]: ../spec/snapshots.md#10-fetching
[FETCH-16]: ../spec/snapshots.md#the-fetcher
[JOB-2]: ../spec/snapshots.md#jobs-and-events
[JOB-4]: ../spec/snapshots.md#jobs-and-events
[IMP-1]: ../spec/snapshots.md#11-imports
[IMP-3]: ../spec/snapshots.md#11-imports
[VAL-3]: ../spec/controls.md#5-the-charts-values
[SOURCE-5]: ../spec/controls.md#6-source
[BACKFILL-2]: ../spec/controls.md#7-backfill
[ACCESS-3]: ../spec/controls.md#8-secaccess
[KIND-1]: ../spec/deployment.md#the-kind-cluster
[KIND-2]: ../spec/deployment.md#the-kind-cluster
[PLACE-1]: ../spec/deployment.md#placement
[NATS-1]: ../spec/deployment.md#nats
[NATS-3]: ../spec/deployment.md#nats
[NATS-5]: ../spec/deployment.md#durable-consumers
[GARAGE-1]: ../spec/deployment.md#garage
[PG-3]: ../spec/deployment.md#instances-and-backups
[PG-4]: ../spec/deployment.md#instances-and-backups
[METRIC-2]: ../spec/telemetry.md#4-metrics
[LEASE-1]: ../spec/deployment.md#the-fetchers-lease
[FETCHER-3]: ../spec/deployment.md#the-fetcher
[LAG-1]: ../spec/notices.md#10-times-and-lags
[STATUS-1]: ../spec/site.md#6-status
[STATUS-2]: ../spec/site.md#6-status
[PAGE-7]: ../spec/site.md#7-the-pages-written-for-the-site
[SCHED-3]: ../spec/deployment.md#its-lease
[SCHED-4]: ../spec/deployment.md#its-lease
