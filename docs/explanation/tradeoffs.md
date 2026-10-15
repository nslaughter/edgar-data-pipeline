# Tradeoffs

<!-- status: all -->
**What's built,** as the [status page](../status.md) gives it:

- **Built:** The snapshot store in git.
- **Specified:** Scheduler; Fetcher; NATS JetStream; Garage; PostgreSQL 18; Monitoring: traces; The snapshot store in the cluster; The transfer from the cluster to git; The controls; The documentation site (built in part).
- **Decided:** Discoverer (specified in part); Parse workers; Exporter; Query API; Monitoring: metrics, alerts, and dashboards (specified in part); Monitoring: logs; `edgar-asof`; The charts (specified in part).
- **Planned:** The fake EDGAR; The drills.
<!-- end status -->

For each decision record, 0001 to 0012: what it chose, the alternatives it
weighed and why they lost, what the choice costs, and what would change it.
Where a record says what would change it, this page cites it. Where it
doesn't, what follows is analysis, set apart as such. The records
themselves hold the full context and their sources; the
[index](../decisions/README.md) gives each one's status.

## Summary

| Record | Choice | Alternatives weighed | What would change it |
| --- | --- | --- | --- |
| [0001] | Every component on Kubernetes, installed with Helm, on kind | Docker Compose; a managed cluster | Not stated; its one umbrella chart is already superseded by [0011] |
| [0002] | Garage, three nodes, a replication factor of 3 | MinIO; SeaweedFS | A client whose policy forbids AGPL software, which would use SeaweedFS |
| [0003] | NATS JetStream, three replicas | Kafka or Redpanda; RabbitMQ; a queue in PostgreSQL | Not stated |
| [0004] | PostgreSQL 18, run by CloudNativePG, three instances, one database for the record and the facts | DuckDB alone; PostgreSQL and DuckDB; a bitemporal database such as XTDB | A new major version of PostgreSQL takes a new record; nothing else stated |
| [0005] | One active fetcher, held by a Lease | A limit per pod; a shared token bucket; an egress proxy | Not stated |
| [0006] | Go for the services that move bytes and keep the SEC's rules, Python for those that read EDGAR's content | A Python fetcher built from `snapshot.py`; a division by stage | Not stated |
| [0007] | Three custom resources, reconciled by the scheduler; a read-only dashboard | Controls in the dashboard; an admin API; Helm values and scaling; a separate operator | An admission webhook once something other than the chart changes Sources ([0010]); nothing else stated |
| [0008] | kube-prometheus-stack and a read-only Grafana, then Tempo, then Loki with Alloy | Prometheus without its operator; VictoriaMetrics; `grafana/otel-lgtm`; Jaeger; no logs | A receiver outside the cluster, if the owner asks; a collector, to sample traces if Tempo's volume calls for it |
| [0009] | A public library, `edgar-asof`, in a repository of its own | A package in this repository; no library | Not stated |
| [0010] | Each company's `submissions` as the notice source, at its Source's cadence; each lag measured from the record | Waiting for the investigation; the latest-filings feed; the daily indexes | The lags the record shows ([T11]); past about 25 companies at 10 seconds, a feed that covers every company |
| [0011] | Four Helm releases, installed in order | One release with workarounds; one chart as two releases; three releases | Not stated |
| [0012] | Zensical, from the documents where they are, served by GitHub Pages | GitHub's own rendering; MkDocs with Material; Sphinx with MyST; generators outside Python; another static host | Not stated for the generator; the site's one version is revisited when the pipeline has releases ([§9 of the site][site-questions]) |

Two decisions are still to come: how the parse workers scale with their
queue's depth ([T10]), and which XBRL parser reads the instances ([T12])
([decisions][design-decisions]). One is to be revisited: the notice source
and its cadence, from the lags the record shows ([T11]).

## 0001: Deploy on Kubernetes with Helm, on kind

**The choice.** Every component runs on Kubernetes, with kind as the
cluster for development and CI. Each is deployed as it would be in
production: from the chart or operator its own project maintains, with its
own Secrets, storage, probes, resource requests and limits, and metrics.
Bitnami's charts are avoided, since its free catalog now offers a reduced
set of images under the `latest` tag only ([0001]). The record's one
umbrella chart became four charts installed in order ([0011]).

**Why.** The pipeline demonstrates a system a team would run, so each
component has to show how it's deployed, configured, scaled, and
recovered, and the whole has to run on one machine and in CI ([0001]).

**Alternatives weighed.**

- **Docker Compose:** simpler, but without scheduling, Leases, CronJobs, or
  operators, so much of what a production team relies on couldn't be shown.
- **A managed cluster:** real, but it costs money to keep running, and
  can't run in CI the way kind can.

**What it costs.** kind's nodes share one machine, so memory is the scarce
resource, and it weighs in every later decision; each component's requests
and limits are measured on kind ([0001],
[§9 of the deployment][deployment-later]). Every component is pinned, so an
upgrade is a reviewed change ([PIN-1] to [PIN-7]).

**What would change it.** The record doesn't say. It notes that the same
charts would install on a managed cluster.

> **Analysis.** No task, decision record, or specification covers this.
> The choice rests on two needs the record names: running on one machine,
> and running in CI. A pipeline that had to keep observing through the
> loss of that machine would need nodes on more than one host, which kind
> doesn't give. The managed cluster the record weighed is then the option
> to weigh again, at its cost, and the charts would come with it.

## 0002: Store bodies in Garage

**The choice.** Garage, an S3-compatible object store, with three replicas
across kind's worker nodes and a replication factor of 3, so the loss of a
node can be shown. It holds the bodies by SHA-256, the facts' Parquet
exports, and the database's backups. A Job after each install sets its
layout and creates its buckets and keys, so a Helm install yields a working
store ([0002], [GARAGE-1], [GARAGE-2]).

**Alternatives weighed.**

- **MinIO:** its license, AGPLv3, would have done, but it's no longer
  maintained: it stopped publishing binaries and images in October 2025,
  went into maintenance mode that December, and its repository has since
  been archived.
- **SeaweedFS:** Apache 2.0, with fuller S3 support, including versioning
  and object lock. But it runs as several processes, used about 500 MB in
  one stress test, and its volume servers keep an index in memory that
  grows with the number of objects.

Garage is one Rust binary that replicates across nodes. It idled under 20
MB and stayed under 200 MB in one stress test, and another guide puts it at
100 to 200 MB idle. The record puts memory first, since kind's nodes share
one machine ([0002]).

**What it costs.** Garage has no object versioning and no object lock, so
storage can't prevent an object from being overwritten or deleted. The
store's guarantees rest on detecting it instead: an object must hash to its
name, and every record must find its object ([OBJ-1], [REC-3], [STORE-3]),
and only the fetcher's key may write the bucket ([LOG-4]). Garage's chart
isn't published to a chart repository, so it's committed at a pinned
release ([PIN-3]). CloudNativePG's backup plugin has been tested against
MinIO, not Garage, so the backups are restored on kind before they're relied
on ([0002], [T31]).

**What would change it.** The record says: a client whose policy forbids
AGPL software would use SeaweedFS, which also offers object lock. Run
unmodified and reached over the S3 API, Garage places no AGPL obligations on
the pipeline's own code ([0002]).

> **Analysis.** No task, decision record, or specification covers this.
> A requirement that storage refuse a change to an object, rather than let
> a check find it, would point the same way, since SeaweedFS offers object
> lock and Garage doesn't.

## 0003: Queue work in NATS JetStream

**The choice.** NATS JetStream, with three replicas and its streams on
persistent volumes ([0003]). The fetcher's jobs and events travel in two
streams, each replicated on all three servers: `JOBS`, kept as a work queue
until the fetcher acknowledges a job, and `RECORDS`, kept until each of its
consumers acknowledges an event ([NATS-3], [jobs and events][store-jobs]).

**Why.** Work handed between components has to survive a crashed pod or a
restart, be acknowledged when done, and be delivered again if a worker
dies partway, so the pipeline can resume after an outage ([0003]).

**Alternatives weighed.**

- **Kafka or Redpanda:** durable logs built for far larger volumes; heavier
  to run, with more memory than this pipeline needs.
- **RabbitMQ:** quorum queues, on the Erlang VM, with more memory than NATS.
- **A queue in PostgreSQL,** with `SELECT ... FOR UPDATE SKIP LOCKED`: no
  new component, but fetching would stop whenever the database fails over
  or is down, and the database would carry the polling load.

NATS is one small Go binary with durable streams, acknowledgements and
redelivery, work-queue retention, and a key-value store, under Apache 2.0
in the CNCF ([0003]).

**What it costs.** A message may be delivered more than once, so each step
is safe to repeat: bodies are named by their SHA-256 and filings by their
accession numbers ([0003]). The specifications make that concrete: the
fetcher fetches a job again only if its fetch wasn't recorded ([JOB-6]),
and a message the discoverer gets again adds nothing twice ([DISC-2]).

**What would change it.** The record doesn't say.

> **Analysis.** No task, decision record, or specification covers this.
> The record turns down Kafka and Redpanda as built for far larger
> volumes, and the queue in PostgreSQL because fetching would stop with the
> database. A pipeline whose volume grew toward what those logs are built
> for could weigh them again; one that valued a component fewer over
> fetching through a failover could weigh the queue in PostgreSQL.

## 0004: Keep the record and the facts in PostgreSQL 18

**The choice.** PostgreSQL 18, run by the CloudNativePG operator with three
instances, its major version pinned through an image catalog that names the
exact 18.x image by digest. Records, filings, coverage, and facts share one
database. Times are explicit columns: the fiscal period as valid time, the
filing's acceptance time as transaction time, and the pipeline's first-seen
time with the bounds of any gap, never the database's write time. A
deterministic Parquet export of the facts, written to Garage, makes the
dataset portable ([0004], [PG-1], [PG-3]).

**Why.** The scale is small, millions of rows even after a backfill, so
speed doesn't decide; memory, integrity, and what the project demonstrates
do ([0004]).

**Alternatives weighed.**

- **DuckDB alone:** facts as write-once Parquet files in Garage, records in
  a NATS stream, and DuckDB in the query service. The lightest option, with
  no database server, but integrity is checked after the fact rather than
  enforced, and records and facts are joined across files.
- **Both, PostgreSQL for records and DuckDB for facts:** each benefit comes
  more cheaply from one engine, and it adds queries across two stores,
  reconciliation between them, and two of every backup, migration, and
  metric.
- **A natively bitemporal database, such as XTDB:** its transaction time is
  when the database received a write, where the pipeline's is when the SEC
  accepted the filing, so it would need explicit time columns anyway; it's
  also heavier, and less familiar to the teams the project addresses.

**What it gains and costs.** Decision 0004 has one transaction record a
filing, its parse, and its facts, so they can't disagree ([0004]). The
notices' specification records the filing apart from its parse, in the
discoverer's own transaction, when a poll's list names it ([SEEN-2],
[DISC-2]), and leaves how facts are written to the facts specification
([T17]). The pipeline's role can insert but not update or delete, so the
database itself enforces the append-only record ([0004], [LOG-3]). The
cost is memory: three always-on instances and the operator are the
design's largest steady memory cost ([0004]).

**What would change it.** The record says a minor upgrade changes the image
catalog, and a major upgrade is a new decision record ([0004],
[§7 of the deployment][deployment-pg]). It names nothing else that
would.

> **Analysis.** No task, decision record, or specification covers this.
> The record sets speed aside because the scale is small. If the facts
> grew to where query speed decided, the option it rejected for its
> overhead, Parquet and DuckDB for the facts, is the one to weigh again;
> the export already writes the facts to Parquet in Garage.

## 0005: Send every SEC request through one active fetcher

**The choice.** One active fetcher of two replicas: the one holding a
Kubernetes Lease consumes the fetch jobs and sends them, at most 5 a
second, and the standby waits ([0005]). Every other component queues jobs.

**Why.** The SEC asks automated tools for at most 10 requests a second, and
the store allows 5 a second, from one process at a time, with a stop on a
`403` ([FETCH-6], [FETCH-7], [FETCH-9]). With several pods, those limits
must hold across the whole cluster, and a `403` must stop all fetching, not
one pod's ([0005]).

**Alternatives weighed.**

- **A limit per pod,** 5 divided by the number of replicas: it breaks when
  the replica count changes, and a `403` still needs coordination.
- **A shared token bucket,** in NATS's key-value store or PostgreSQL: more
  moving parts, harder to test, and a `403` becomes a shared flag every
  worker must check.
- **An egress proxy with a global rate-limit service,** such as Envoy's:
  realistic for many upstreams, heavy for one.

**What it costs.** The whole pipeline is capped at 5 requests a second,
half the SEC's limit. A handover leaves a short pause in fetching, recorded
as a gap like any other, and takes up to the Lease's duration, 15 seconds
by default ([0005], [LEASE-1]). The notices' specification gives a gap
only where the handover misses a poll ([§5 of the notices][notices-gaps]).
After a `403`, nothing is sent until the owner acknowledges the stop
([ACCESS-3]). In return, "one process at a time" becomes "one Lease
holder", which a test on kind can show by deleting the active pod
([0005]).

**What would change it.** The record doesn't say.

> **Analysis.** No task, decision record, or specification covers this.
> Throughput wouldn't change it: the cap comes from the SEC's stated
> maximum, not from what one fetcher can send ([FETCH-6]). A pipeline that
> read from more than one upstream, where the record judges an egress
> proxy realistic, could weigh that option again.

## 0006: Write the services in Go and Python

**The choice.** Divide the services by whether they read EDGAR's content.
Go for those that move bytes and keep the rules for talking to the SEC: the
scheduler, the fetcher, and the query API. Python for those that read what
EDGAR publishes: the discoverer, the parse workers, and the exporter,
beside the scripts and the investigation, and the fake EDGAR for tests
([0006]). The owner requires at least one service in each language, and the
line between them follows a rule, so a new service has an obvious home.

**Alternatives weighed.**

- **Go only for the scheduler and the query API,** with a Python fetcher
  built from `snapshot.py`: the fetching rules would be written once, but Go
  would get only the two thinnest services, and the Lease and the rate
  limit, the parts a platform team looks at first, would be in Python,
  where leader election by Lease is much less used.
- **A division by stage,** Go up to storing a body and recording the
  filing, Python after it: the discoverer would read `submissions` and
  headers in Go, so knowledge of EDGAR's formats would live in both
  languages.

**What it costs.** The fetching rules have two implementations,
`snapshot.py` and the Go fetcher, so one set of checks, through the fake
EDGAR, runs against both ([testing](testing-and-verification.md#the-fake-edgar)).
CI runs a second toolchain. The languages meet only at NATS messages,
PostgreSQL tables, and Garage objects, each a schema that tests on both
sides validate against ([0006]). In return, a change in EDGAR's formats
touches one language.

**What would change it.** The record doesn't say.

> **Analysis.** No task, decision record, or specification covers this.
> The record starts from the owner's requirement of both languages, and
> doesn't weigh one language alone. Without that requirement, one language
> would remove the second toolchain and the second implementation of the
> fetching rules, at the cost the record names for a Python fetcher.

## 0007: Control the pipeline through custom resources

**The choice.** The scheduler is a controller, and the pipeline's controls
are three custom resources: `Source`, one feed polled at a cadence, which
can be paused; `Backfill`, a company's past filings over a range of dates;
and `SecAccess`, one per cluster, which says whether the pipeline may send
requests to the SEC at all and keeps the stop after a `403`. The dashboard
is read-only ([0007]). The record made `kubectl apply` on a resource the
control ([0007]). A later record and the controls' specification render
the resources from the umbrella chart's values, and have the owner set
every control there, for Helm to apply ([0010], [VAL-1], [RBAC-6]).

**Why.** Each control is rare and deliberate, and changes the record: a
pause is a gap, and a new source widens the scope, which is the owner's to
do. So a control should record who used it and when, and survive a pod's
restart. A stop after a `403` kept in the active fetcher's memory would be
forgotten when the standby takes over ([0007]).

**Alternatives weighed.**

- **Controls in the dashboard,** as buttons in Grafana or a page of the
  project's own: they need their own login, permissions, and audit trail,
  and put writes beside a view that's shared and left open.
- **An admin API with a command-line client,** on one of the Go services:
  explicit, but it rebuilds the authentication, authorization, and history
  the API server already has.
- **Helm values and scaling,** with cadence and scope in values, and a
  pause by scaling the fetcher to zero: no new code, but a pause looks like
  a failure, status is shown nowhere, and resuming after a `403` has no
  place to be recorded.
- **A separate operator** beside the scheduler: the same resources, with
  one more Deployment for work the scheduler exists to do.

**What it costs.** The resources' schemas are a contract of their own,
versioned from `v1alpha1`, and the definitions generated from the Go types
must match them ([API-1], [API-2]). Scope lives in two places:
`data/scope.json` for the git store, and the Sources in the cluster
([the scope and the cluster][store-scope-cluster]). Status is a summary for
people: the record in PostgreSQL stays authoritative ([0007]).

**What would change it.** The record doesn't say what would change the
choice of custom resources. A later record says what would change how they
are checked: no admission webhook guards the Sources for now, and if
something other than the chart comes to create or change them, a webhook is
added, with certificates from cert-manager ([0010], owner's decision 5).

> **Analysis.** No task, decision record, or specification covers this.
> The record turns down controls in the dashboard for the login,
> permissions, and audit trail they'd need. An interface that only
> proposed changes to the chart's values, applied through a reviewed
> change, would keep the API server as the record of each control.

## 0008: Monitor with Prometheus and Grafana, then Tempo and Loki

**The choice.** kube-prometheus-stack, with each component's monitors and
alert rules in the umbrella chart beside it. Grafana is read-only, with two
data sources: Prometheus, and PostgreSQL through a role that may read only
views made for the dashboards. Tempo comes next, then Loki fed by Grafana
Alloy; the services send traces by OTLP and carry each trace through NATS
in message headers. Tempo and Loki keep their data on their own volumes,
not in Garage, so an outage of Garage can still be observed. Alertmanager
has no receiver outside the cluster ([0008], [MON-2], [MON-3]).

**Alternatives weighed.**

- **Prometheus and Grafana without the operator:** one pod fewer, but every
  scrape target and rule is written by hand in one central configuration,
  not beside the component it watches.
- **VictoriaMetrics, with its operator:** less memory and disk than
  Prometheus, and its operator converts the Prometheus operator's
  resources; less familiar to the teams the project addresses.
- **Grafana's all-in-one image, `grafana/otel-lgtm`:** its project says it's
  meant for development, demos, and testing, so it doesn't model
  production.
- **Jaeger, for traces:** it would bring a second interface, where Tempo is
  built to be queried from Grafana.
- **No logs beyond `kubectl logs`:** that loses a deleted pod's logs and
  can't search every service for one accession number. Alloy, not
  Promtail, which reached its end of life in March 2026, feeds Loki.

**What it costs.** Memory: an estimated 1 GB for kube-prometheus-stack, most
of it Prometheus, and a few hundred MB each for Tempo and for Loki with
Alloy, each measured on kind before its limits are set. The metrics and
traces become a contract that the dashboards and alerts rely on, with a
specification of their own, and the views Grafana reads are part of the
record's schema ([0008], [telemetry]).

**What would change it.** The record names two changes and what brings
them: a receiver outside the cluster, once the owner asks for one; and a
collector between the services and Tempo, which needs no change to the
services, since they speak OTLP ([0008]). The telemetry specification adds
when the collector would come: if Tempo's volume calls for sampling the
traces, which are all sampled now
([telemetry's open question 3][telemetry-questions]).

> **Analysis.** No task, decision record, or specification covers this.
> If kube-prometheus-stack's memory, once measured, proved too large for
> kind's one machine, VictoriaMetrics, which the record credits with less
> memory and disk, and whose operator reads the same resources, is the
> alternative to weigh again.

## 0009: Read EDGAR through a public point-in-time library

**The choice.** A separate public repository, `nslaughter/edgar-asof`,
holding a Python library released on PyPI. It reads `submissions`,
`companyfacts`, and each filing's header; keeps each value in two times,
its period and the acceptance time of each filing that reported it; keeps
each acceptance time raw, and turns it into an instant only in a step the
caller can replace; and answers "as of T". It also fetches, under the SEC's
rules, for people who use it on its own, with a cache that follows the
snapshot store. The pipeline's Python services import a pinned version and
give it bodies from the store; the Go fetcher keeps its own client
([0009], [FETCH-18]).

**Why.** The repository was private, and the pipeline many tasks away, so
none of the work could be used or shown outside it. The core question,
what a company reported as known at a past time, is useful on its own,
without a cluster ([0009]).

**Alternatives weighed.**

- **A package in this repository:** specifications, decisions, and changes
  stay in one place, and a change that spans both lands in one pull
  request. But it's public only when the whole repository is, and it reads
  as a component of a Kubernetes project, not as a library.
- **No library:** the pipeline stays the only artifact, private until it
  runs.

**What it costs.** A public library implies releases and some upkeep, kept
light by limiting it to `submissions`, `companyfacts`, and headers, which
change less often than filing formats and taxonomies. A change the pipeline
needs is released in the library first, then pinned here ([0009]). So
several of the pipeline's tasks wait on the library's releases
([open questions and risks](open-questions-and-risks.md#what-waits-on-edgar-asofs-releases)).

**What would change it.** The record doesn't say.

> **Analysis.** No task, decision record, or specification covers this.
> Part of the record's case was that this repository was private; the
> owner decided in the same record that it becomes public. What still
> argues for a separate repository is a library usable without a cluster,
> with releases of its own. If keeping the two in step came to cost more
> than that, the package in this repository is the option to weigh again.

## 0010: Poll each company's `submissions`, and measure lag from the record

**The choice.** Start with each company's `submissions` as the notice
source, polled at the cadence its Source holds, which the owner sets in the
chart's values; the chart's Sources for the three companies start at 10
seconds. The pipeline records every time raw, as its source gives it, and
measures each lag from the record, interpreting time zones only in queries
([0010]). The chart refuses a configuration whose Sources together would
poll faster than half the fetcher's limit ([VAL-3]).

**Why.** The source and cadence were to wait for the investigation's Q4,
how soon each feed shows a new filing. Polling every minute, as the
investigation planned, can't tell a delay of a second from one of fifty,
and polling finer for weeks would outgrow the git store. So the owner
decided that the running pipeline measures it ([0010]).

**Alternatives weighed.**

- **Waiting for the investigation:** notices wait for weeks of watching,
  and the watching measures only to its polling interval.
- **EDGAR's latest-filings feed:** one request covers every company, but
  its URL isn't confirmed, the scope must be widened to it, it changes on
  nearly every poll, so the store keeps a body each time, and a poll that
  comes too late can miss filings in a busy burst.
- **The daily indexes:** complete and kept in the archive, but published
  around 10 PM ([SRC-5]), so a filing is first seen hours after its
  acceptance.

**What it costs.** Q4 is answered only once the fetcher and the discoverer
run, so the investigation finishes after the pipeline's first part runs.
Each poll downloads the whole body, 20 to 27 KB compressed, since
`submissions` sends no validators ([SRC-4]); at the starting cadence,
three companies are 0.3 requests a second, 6% of the limit, and 25,920
records a day. A lag is only as good as its clocks, and until the header's
time zone is settled, Apple's 8-hour difference ([SRC-2]) shows in the lag
queries as an outlier ([0010]).

**What would change it.** The record says what would. The source is
revisited from the lags the record shows: whether to keep each company's
`submissions`, or add the latest-filings feed. A new source is a later record that supersedes
this one; a new cadence is only a change to a Source. At 10 seconds, half
the fetcher's limit covers about 25 companies, and past that a feed that
covers every company becomes the better source ([0010]). The plan's [T11]
makes that revisit when the owner asks for it, once the monitoring shows
the lags ([T28]), and weighs the latest-filings feed if [T5] has recorded
what it holds.

## 0011: Install the definitions and the operators before the umbrella chart

**The choice.** Four Helm releases, installed and upgraded in order, each
with `--wait`: the Prometheus operator's custom resource definitions;
cert-manager; the operators, CloudNativePG and its Barman Cloud plugin,
with Garage's definition and the pipeline's own; and last the umbrella
chart. Every definition is a template of the release before the first that
needs it, so Helm creates and upgrades it with that release
([0011], [CHART-4], [CRD-2], [CRD-3]).

**Why.** Helm, through version 4.3.0, installs a chart's `crds/` directory
on the first install only, and never upgrades it. A definition in a chart's
templates is upgraded, but Helm builds the whole release against the
cluster's API before it creates anything, so a resource whose definition is
in the same release fails to build. cert-manager's and CloudNativePG's
admission webhooks refuse resources until their pods are ready. And Helm
keeps each release in a Secret of at most 1 MiB, where the four operators'
definitions come to about 940 KiB ([0011]).

**Alternatives weighed.**

- **One release, with workarounds:** every definition in a local
  subchart's `crds/` directory, a hook Job that applies them on each
  upgrade, one namespace labeled to skip cert-manager's validation, and
  CloudNativePG's webhooks set to `Ignore`. It keeps one chart, but against
  cert-manager's advice, with two admission checks loosened, and
  definitions upgraded outside Helm's own record of the release.
- **One chart, installed as two releases,** a value choosing which part
  each renders: one chart in name, but every test must render it both
  ways.
- **Three releases,** with the Prometheus operator's definitions left in
  kube-prometheus-stack and upgraded by its own Job: that adds about 290 KB
  to the umbrella chart's release, and the operators' monitors can't be
  rendered before it.

**What it costs.** CI and the owner install four releases instead of one.
The Prometheus operator's definitions and kube-prometheus-stack are pinned
separately, so a chart test checks that their versions agree ([CRD-5]).
Every definition is kept when its release is uninstalled, and with it every
resource of its kind, such as the SecAccess's record of a stop, so removing
one is a deliberate step ([CRD-4]).

**What would change it.** The record doesn't say.

> **Analysis.** No task, decision record, or specification covers this.
> The record rests on Helm's behavior through version 4.3.0, and names two
> of Helm's proposals: one, still open, to upgrade a chart's definitions,
> and one, a draft, to order the resources of a release. If Helm came to do
> both, fewer releases could serve, and the pipeline could weigh merging
> them.

## 0012: Publish the documents as a site built with Zensical

**The choice.** A site built with Zensical, from the files where they are,
with the repository's root as its source, and served by GitHub Pages at
the address GitHub gives the repository, deployed from `main` by a GitHub
Actions workflow ([0012], [SITE-1], [SITE-10]). The documents stay where
they are, since commits and pull requests link to their paths, and a copy
could drift.

**Alternatives weighed.**

- **GitHub's own rendering, with no site:** nothing to build, links that
  resolve as written, and Mermaid diagrams drawn. But no navigation across
  the documents beyond the file tree, and no reading order beyond what a
  page links to.
- **MkDocs, with Material for MkDocs:** mature, and Zensical's predecessor,
  but Material for MkDocs has been in maintenance mode since November
  2025, and MkDocs's latest release was of August 2024, when the record
  was written.
- **Sphinx, with MyST:** mature, and in Python, but its heading anchors
  must be configured to match GitHub's, so the documents' links resolve on
  both.
- **A generator outside Python,** such as Docusaurus, Starlight, or Hugo:
  each brings a toolchain the documents don't otherwise need.
- **Another static host,** such as Netlify or Cloudflare Pages: each needs
  an account and a token outside GitHub.

**What it costs.** Zensical is alpha, so its version is locked, and an
upgrade is a change of its own, checked by building the site. Its `strict`
mode can't tell a file the site leaves out from one that's missing, so the
build checks every link itself ([SITE-3], [SITE-8]). The site serves its
own copy of Mermaid, and carries the notices of the files it serves
([SITE-4], [SITE-6]).

**What would change it.** The record doesn't say what would change the
generator. The site's specification says one choice is revisited: the site
keeps no version per release of the pipeline, and shows `main`, until the
pipeline has releases ([§9 of the site][site-questions]).

> **Analysis.** No task, decision record, or specification covers this.
> Zensical's configuration follows MkDocs's, the record says. If
> Zensical's development stalled while it's still alpha, the generators
> the record weighed are the ones to weigh again, and a configuration in
> MkDocs's form could keep that move small.

[0001]: ../decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md
[0002]: ../decisions/0002-store-bodies-in-garage.md
[0003]: ../decisions/0003-queue-work-in-nats-jetstream.md
[0004]: ../decisions/0004-keep-the-record-in-postgresql-18.md
[0005]: ../decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0006]: ../decisions/0006-write-the-services-in-go-and-python.md
[0007]: ../decisions/0007-control-the-pipeline-through-custom-resources.md
[0008]: ../decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md
[0009]: ../decisions/0009-read-edgar-through-a-public-point-in-time-library.md
[0010]: ../decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[0011]: ../decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md
[0012]: ../decisions/0012-publish-the-documents-as-a-site-built-with-zensical.md
[design-decisions]: ../design.md#decisions
[notices-gaps]: ../../spec/notices.md#5-polls-coverage-and-gaps
[T5]: ../plan.md#investigation
[T10]: ../plan.md#facts
[T11]: ../plan.md#observing-filings
[T12]: ../plan.md#facts
[T17]: ../plan.md#facts
[T28]: ../plan.md#observing-filings
[T31]: ../plan.md#traces-logs-and-drills
[telemetry]: ../../spec/telemetry.md
[telemetry-questions]: ../../spec/telemetry.md#11-open-questions
[site-questions]: ../../spec/site.md#9-open-questions
[deployment-later]: ../../spec/deployment.md#9-later-versions
[deployment-pg]: ../../spec/deployment.md#7-postgresql
[store-jobs]: ../../spec/snapshots.md#jobs-and-events
[store-scope-cluster]: ../../spec/snapshots.md#the-scope-and-the-cluster
[SRC-2]: ../../investigation/edgar.md#findings-so-far
[SRC-4]: ../../investigation/edgar.md#findings-so-far
[SRC-5]: ../../investigation/edgar.md#findings-so-far
[OBJ-1]: ../../spec/snapshots.md#5-objects
[REC-3]: ../../spec/snapshots.md#6-records
[LOG-3]: ../../spec/snapshots.md#8-history
[LOG-4]: ../../spec/snapshots.md#8-history
[STORE-3]: ../../spec/snapshots.md#the-cluster-store
[FETCH-6]: ../../spec/snapshots.md#10-fetching
[FETCH-7]: ../../spec/snapshots.md#10-fetching
[FETCH-9]: ../../spec/snapshots.md#10-fetching
[FETCH-18]: ../../spec/snapshots.md#the-fetcher
[JOB-6]: ../../spec/snapshots.md#jobs-and-events
[DISC-2]: ../../spec/notices.md#processing
[SEEN-2]: ../../spec/notices.md#6-when-each-source-first-showed-each-filing
[API-1]: ../../spec/controls.md#4-the-resources
[API-2]: ../../spec/controls.md#4-the-resources
[VAL-3]: ../../spec/controls.md#5-the-charts-values
[ACCESS-3]: ../../spec/controls.md#8-secaccess
[VAL-1]: ../../spec/controls.md#5-the-charts-values
[RBAC-6]: ../../spec/controls.md#9-who-may-change-what
[CHART-4]: ../../spec/deployment.md#5-dependencies
[CRD-2]: ../../spec/deployment.md#definitions
[CRD-3]: ../../spec/deployment.md#definitions
[CRD-4]: ../../spec/deployment.md#definitions
[CRD-5]: ../../spec/deployment.md#definitions
[PIN-1]: ../../spec/deployment.md#6-pins
[PIN-3]: ../../spec/deployment.md#6-pins
[PIN-7]: ../../spec/deployment.md#6-pins
[PG-1]: ../../spec/deployment.md#7-postgresql
[PG-3]: ../../spec/deployment.md#instances-and-backups
[NATS-3]: ../../spec/deployment.md#nats
[GARAGE-1]: ../../spec/deployment.md#garage
[GARAGE-2]: ../../spec/deployment.md#garage
[MON-2]: ../../spec/deployment.md#monitoring
[MON-3]: ../../spec/deployment.md#monitoring
[LEASE-1]: ../../spec/deployment.md#the-fetchers-lease
[SITE-1]: ../../spec/site.md#4-what-the-site-holds
[SITE-3]: ../../spec/site.md#4-what-the-site-holds
[SITE-4]: ../../spec/site.md#4-what-the-site-holds
[SITE-6]: ../../spec/site.md#4-what-the-site-holds
[SITE-8]: ../../spec/site.md#5-building-and-publishing
[SITE-10]: ../../spec/site.md#5-building-and-publishing
