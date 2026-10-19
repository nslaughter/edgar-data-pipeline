# Requirements

<!-- status: all -->
**What's built,** as the [status page](../status.md) gives it:

- **Built:** NATS JetStream; Garage; PostgreSQL 18; The snapshot store in git; The charts; The fake EDGAR.
- **Specified:** Fetcher; Monitoring: traces; The snapshot store in the cluster (built in part); The transfer from the cluster to git; The controls; The documentation site (built in part).
- **Decided:** Scheduler (specified in part); Discoverer (specified in part); Parse workers; Exporter; Query API; Monitoring: metrics, alerts, and dashboards (specified in part; built in part); Monitoring: logs; `edgar-asof`.
- **Planned:** The drills.
<!-- end status -->

This page covers what the pipeline must do, the qualities it must have, and
the limits the SEC sets, each with the statement, decision record, or
finding behind it and the part of the pipeline that meets it. It ends with
what's out of scope. The page restates no rule: each requirement is the
cited document's, and that document governs.

## What the pipeline must do

| Requirement | Behind it | Where it's met |
| --- | --- | --- |
| Poll each Source's URL at the cadence the Source holds, while it isn't paused and requests are allowed | [0010], [SOURCE-1], [SOURCE-2] | The scheduler, which queues each poll as a job that expires when the next is due ([JOB-2]) |
| Send every request to the SEC through one active fetcher | [0005], [FETCH-16], [FETCH-18] | The fetcher replica that holds the Lease `fetcher` ([LEASE-1]) |
| Store each distinct body once, by its SHA-256, and add one record for each fetch | [OBJ-4], [FETCH-14], [STORE-1] | The fetcher, into Garage's bucket `snapshots` and PostgreSQL's table `records` ([GARAGE-4], [PG-8]) |
| Find each filing that a company's `submissions` lists, and when each Source first showed it | [SEEN-1], [SEEN-2], [SEEN-3] | The discoverer, in the tables `filings` and `sightings` |
| Fetch each new filing's header, index page, and XBRL instance | [FILE-1], [FILE-2], [FILE-3] | The discoverer queues the fetches, and the fetcher makes them |
| Record each Source's coverage and gaps | [GAP-1], [GAP-2], [GAP-3] | The discoverer's table `polls`, and the view `gaps` |
| Give each lag, from each acceptance time to when each Source first showed the filing, as a query | [0010], [LAG-1] | The view `lags`, and the dashboards' view of it ([VIEW-6]) |
| Fetch a company's past filings over a range of filing dates, once | [BACKFILL-1], [BACKFILL-3], [ORDER-1], [LIST-1] to [LIST-6] | The scheduler hands each Backfill to the discoverer, which lists its filings and queues their fetches |
| Pause one Source, pause every request, and stop after a `403` until the owner acknowledges it | [SOURCE-1], [ACCESS-1], [ACCESS-2], [ACCESS-3] | The scheduler and the fetcher, from the `Source` and `SecAccess` resources |
| Parse each filing's XBRL instance into facts, with the filing's acceptance time, under an import manifest | [Components][design-components], [IMP-1] to [IMP-3] | The parse workers; the facts specification, task T17, isn't written |
| Answer "as of T" | [Times][design-times], [0009] | The query API, tasks T17 and T26 |
| Export the facts to Parquet, deterministically, with each file's SHA-256 | [Components][design-components], [0004] | The exporter; the export's specification is task T18 |
| Bring the records of the git store's scope from the cluster into `data/`, so findings can cite them | [0010], [XFER-1] to [XFER-3] | The transfer, which the owner runs, task T36 |
| Report what it does: metrics, alerts, the views Grafana reads, and traces | [Telemetry's guarantees][telemetry-guarantees] | The services, kube-state-metrics, Prometheus, and Grafana |

All but the facts, the "as of T" answers, and the export are specified.
Those three wait on specifications still to be written: the facts, task
T17, and the export, task T18 ([plan][plan-facts]).

## The qualities it must have

### Durability

Nothing stored is changed or removed, and a correction is a new record
([§1 of the store][store-guarantees]). How that holds differs by store:

- **In the git store,** history shows it: each log at an earlier commit is
  a prefix of the log now, and each object and manifest is still there with
  the same bytes ([LOG-1], [LOG-2]), as `scripts/verify.py --since` checks.
- **In PostgreSQL,** the database refuses it: the fetcher's role may insert
  and select records, every other service's role may only select them, and
  none may update, delete, or truncate them ([LOG-3], [PG-8]). The
  discoverer's tables are kept the same way ([TABLE-3]).
- **In Garage,** nothing can refuse it, since Garage has no object lock
  ([0002]). So only the fetcher's key may write the bucket, the fetcher
  never deletes or replaces an object ([LOG-4], [GARAGE-4]), and a check
  finds any change, since an object must hash to its name and every record
  must find its object ([STORE-3]). The guarantee rests on detection, not
  prevention ([0002]).

NATS, Garage, and the database keep running when one of kind's three worker
nodes is lost: each keeps three replicas, one on each worker node, and
Garage keeps a copy of every object, and NATS of every stream, on each
([deployment's guarantees][deployment-guarantees], [PLACE-1], [NATS-3],
[GARAGE-1], [PG-3]). Stored data outlives the cluster. Every worker node
keeps its volumes in one directory of the host ([KIND-2]), and the claims of
NATS, Garage, Prometheus, and Alertmanager are of the class
`edgar-retained`, whose data a cluster recreated with that directory finds
([VOL-1] to [VOL-3]). The database's claims are of kind's `standard` class,
whose data a recreated cluster can't find, so what outlives the cluster for
the database is its backups in Garage ([VOL-2], [PG-4]). The database
archives its write-ahead log to Garage and takes a base backup at least once
a day ([PG-4]). Its backup plugin has been tested against MinIO, not Garage,
so a restore is shown on kind, by the drills, before the backups are relied
on ([instances and backups][deployment-backups], task T31).

Work in flight survives a restart: queued work is kept in NATS JetStream's
streams, on persistent volumes, and work not acknowledged is delivered again
([0003], [outages and recovery][design-outages]). [Failures and
recovery](failures-and-recovery.md) takes each failure in turn.

### Reproducibility

- **An import reads the same bytes when it's run again.** It names each
  input by its SHA-256, writes its manifest before reading, and reads only
  stored objects, never the live source ([IMP-1] to [IMP-3]).
- **An earlier "as of" answer stays the same.** A restated value appears
  only from its filing's acceptance on ([times][design-times]). `edgar-asof`
  makes the same promise for its answers: the same bodies, key, T, and
  interpreter give the same answer, and the answer names each body by URL
  and SHA-256 ([`edgar-asof`'s facts specification, §1][asof-facts-1]).
  This was checked against `edgar-asof`'s `main`, at commit `515bb0d`, on
  October 14, 2026.
- **A rerun can be compared with the original.** The export of the facts is
  deterministic and hashed, so a rebuild's export can be compared with the
  original's ([0004]); pyarrow is pinned, since the export's bytes depend on
  its version ([0006]). A drill drops the facts, imports them again from
  stored bodies, and compares the "as of" answers and the export's hashes
  ([testing on kind][design-testing], task T31).
- **The same commit installs the same software.** Every chart is committed
  at an exact version, every third-party image named by its digest, and each
  of the pipeline's own images by the commit it's built from
  ([deployment's guarantees][deployment-guarantees], [PIN-1], [PIN-4],
  [IMG-1]).

### Auditability

- **Each record names what wrote it:** `tool` gives the program and the
  commit it ran from, such as `fetcher@` and the 40-character commit the
  fetcher's image was built from ([§6 of the store][store-records]).
  Each of the discoverer's readings names the discoverer's commit the same
  way ([§6 of the notices][notices-seen]).
- **Each time is kept as its source gave it,** and interpreted only in
  queries ([0010]).
- **Each fetch traces to why it was made.** A record keeps the `id` of its
  job ([STORE-1]), and each job the discoverer queues keeps the record whose
  event led to it ([§7 of the notices][notices-files]).
- **Each control is a field of a resource, set from the chart's values.**
  The owner changes a control only with `helm install` or `helm upgrade`,
  and the services change only status ([RBAC-6], [RBAC-1], [RBAC-2]), so a
  control survives restarts and its status shows what the pipeline did with
  it ([§1 of the controls][controls-guarantees], [0007]).
- **A finding can cite a fetch the cluster made,** since the transfer brings
  its record into `data/` unchanged, with its body ([XFER-2], [0010]).

### How soon it sees a filing

Each company's `submissions` is polled every 10 seconds to start, a cadence
the owner changes in the chart's values ([0010], [§5 of the
controls][controls-values]). The fetcher takes polls before any other job,
and a Backfill's fetches only when nothing else waits ([JOB-4],
[BACKFILL-2]). A poll's job expires when the next poll is due, and the
fetcher acknowledges an expired job without a request ([JOB-2], [JOB-5]),
so it spends no request on a poll whose time has passed.

A filing's public availability lies between its acceptance and its
first-seen time, and at 10 seconds that bounds it within about 10 seconds
plus the source's own delay ([0010]). How long `submissions` takes to show a
filing after its acceptance is the investigation's question Q4, measured
from the pipeline's own record once it runs ([questions], [method]).
After a filing is first seen, its header and index page, then its XBRL
instance, are fetched as jobs of kind `filing`, behind the polls ([FILE-2],
[FILE-3], [JOB-4]). The documents set no target for the time from
acceptance to facts. A view of each filing's times, from acceptance to facts
available, comes with telemetry for facts (task T32,
[plan][plan-facts]).

In daily operation, a Source is polled once a day, and each filing's
first-seen time is bounded within a day ([two modes][design-modes]).

### Knowing where it wasn't looking

A missed poll leaves a gap of about a cadence. A pause, a stop after a
`403`, or a fetcher's handover that misses a poll leaves one until polling
resumes ([§5 of the notices][notices-gaps]). The record holds each one:

- Coverage and gaps follow from each Source's polls by one definition, in
  the view `gaps`, so a record that arrives late changes what the view
  gives, and nothing stored ([GAP-2]).
- The discoverer publishes a change in a Source's open gap within 10
  seconds ([GAP-3]), and the scheduler shows it in the Source's status as
  `openGapSince` ([NOTICE-1], [SOURCE-5]).
- The alert `SourceGap` fires for a Source that's polling once its open gap
  has been longer than its cadence for 2 minutes ([ALERT-2]).
- Each sighting says whether a gap lies between the two polls that bound
  when its Source first showed the filing ([LAG-1]).

A fetcher's handover after the active replica is lost takes up to the
Lease's duration, 15 seconds by default, which the record shows as a gap
([the fetcher's Lease][deployment-lease], [LEASE-1]). Around a change of
cadence, the gaps can be inexact, since a job gives neither its due time nor
its Source's cadence: a shortened cadence can hide a poll missed at the
change, and a lengthened one can show an open gap until the first poll at
the new cadence ([open questions of the notices][notices-open]).

### Fitting on one machine

kind runs every node as a container on one machine, so memory is the scarce
resource, and it weighs in each choice of component ([0001]). Memory
weighed in choosing Garage over SeaweedFS ([0002]) and NATS JetStream over
Kafka and RabbitMQ ([0003]), and the database's three instances and its
operator are the design's largest steady memory cost ([0004]).
kube-prometheus-stack is estimated at 1 GB ([0008]). Each component's
requests and limits are measured on kind before they're set
([§9 of deployment][deployment-later]). Until then, the
[estimates](estimates.md#cpu-memory-and-storage) put the whole pipeline at
4.0 to 14.4 GB of memory, and [scale and limits](scale-and-limits.md#memory)
says what that asks of the machine kind runs on.

## The limits the SEC sets

| What the SEC sets | What the pipeline does | Behind it |
| --- | --- | --- |
| A declared user agent | Every request carries the owner's own `User-Agent`, which must contain an `@`, following the SEC's sample of a name and a contact address. The repository ships none, and without one nothing is fetched. In the cluster it's in one Secret, which only the fetcher reads. No record, job, or event holds it. | [FETCH-2], [FETCH-15], [FETCH-17], [ID-1], [ID-2] |
| At most 10 requests a second | Requests start at least 0.2 seconds apart, across all of a cluster's fetchers together: at most 5 a second, half the SEC's stated maximum. One process fetches at a time, across every checkout, worktree, and cluster: a `snapshot.py` run, or a cluster's active fetcher, which sends only while it holds the Lease. | [FETCH-6], [FETCH-7], [FETCH-16], [0005] |
| A `403` to a client over its limit | The `403` is recorded, and no request follows it. The fetcher records the stop in `SecAccess`, where a replica that takes over finds it, and sends nothing until the owner acknowledges it. An alert fires until then. | [FETCH-9], [ACCESS-3], [ALERT-1] |

Two rules of the pipeline's own go with these. Only `https` URLs on
`www.sec.gov` and `data.sec.gov` are fetched ([FETCH-1]). No response, a
`429`, `500`, `502`, `503`, or `504`, and a body cut short are retried, up to
4 attempts in all, after the response's `Retry-After`, or else 2 to the
power of the attempt number, in seconds, either capped at 60 seconds
([FETCH-8]).

The fetcher's limit is shared. The Sources not paused may together poll at
most half of it, 2.5 requests a second, and the chart refuses values that
would poll faster ([VAL-3]). The other half is left for each new filing's
fetches and for Backfills ([terms of the controls][controls-terms]).

| Case | Arithmetic | Result |
| --- | --- | --- |
| The chart's starting Sources | 3 Sources × 1/10 a second | 0.3 requests a second, 6% of the limit ([0010]) |
| Most Sources at 10 seconds within the budget | 2.5 ÷ (1/10) | 25 Sources pass, and 26 fail ([VAL-3]) |
| Polls a day at the start | 3 × 86,400 ÷ 10 | 25,920 records a day ([0010]) |
| A Backfill of every filing a company's `submissions` lists, at 5 a second | 4,671 requests for Apple, 9,025 for Walmart, and at least 6,166 + 3,042 = 9,208 for Microsoft | 15.6 minutes for Apple, 30.1 for Walmart, and at least 30.7 for Microsoft ([SRC-20]) |
| A Backfill of only a company's XBRL 10-Ks, 10-Qs, and amendments | 204 to 210 requests | About 40 seconds ([SRC-20]) |

Behind the polls, which take at most half the limit, the largest of those
Backfills takes about an hour ([open questions of the store][store-open]).
Nothing is sent to the SEC until the owner's values allow it: the chart's
default values pause the `SecAccess` ([VAL-4]). In the repository's own
work, a task that sends requests to the SEC is ready only when the owner
asks for that run ([how the plan works][plan-how]). Tests never reach the
network; a fake EDGAR in the cluster serves stored versions instead
([testing on kind][design-testing]).

## What's out of scope

- **All of EDGAR.** The pipeline holds a small scope, not all of EDGAR
  ([what the pipeline keeps][design-keeps]). The git store holds only what
  `data/scope.json` declares, and EDGAR's bulk archives of every company
  stay outside it ([§9 of the store][store-scope]). The cluster polls the
  Sources the owner's values name, starting with each company's
  `submissions` ([§5 of the controls][controls-values]). EDGAR's
  latest-filings feed, which covers every company, is in the git store's
  scope for the investigation, which recorded what it holds (task T5,
  [plan][plan-investigation]; [SRC-26] to [SRC-28]), but no Source polls
  it.
- **Filing documents in general.** For each filing, the pipeline fetches
  its header, its index page, and the XBRL instance the index page names
  ([FILE-1]). `edgar-asof` leaves filing documents and forms in general to
  other libraries ([0009]).
- **What it didn't observe.** A version the SEC served and replaced during
  a gap is lost; the record shows the gap ([what the pipeline
  keeps][design-keeps]).
- **The time a filing became public.** The SEC doesn't state it; the record
  bounds it between acceptance and first seen ([times][design-times]).
- **Controls anywhere but the resources.** Grafana is read-only, and every
  control is a field of `Source`, `Backfill`, or `SecAccess` ([0007],
  [MON-2]). No admission webhook checks the resources, since nothing but
  the chart creates them ([§5 of the controls][controls-values], [0010]).
- **Alerts outside the cluster.** Alertmanager has no receiver outside the
  cluster unless the owner asks for one ([0008], [MON-3]).
- **An image registry.** The pipeline's own images are tagged with the
  commit they're built from and loaded into kind, never pushed or pulled
  ([IMG-2]).
- **A managed cluster.** Everything runs on kind, for development and CI;
  the same charts would install on a managed cluster ([0001]).

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
[ACCESS-1]: ../../spec/controls.md#8-secaccess
[ACCESS-2]: ../../spec/controls.md#8-secaccess
[ACCESS-3]: ../../spec/controls.md#8-secaccess
[ALERT-1]: ../../spec/telemetry.md#7-alerts
[ALERT-2]: ../../spec/telemetry.md#7-alerts
[asof-facts-1]: https://github.com/nslaughter/edgar-asof/blob/515bb0dc7af1b76a269413f29ba3edc390193e46/spec/facts.md#1-guarantees
[BACKFILL-1]: ../../spec/controls.md#7-backfill
[BACKFILL-2]: ../../spec/controls.md#7-backfill
[BACKFILL-3]: ../../spec/controls.md#7-backfill
[controls-guarantees]: ../../spec/controls.md#1-guarantees
[controls-terms]: ../../spec/controls.md#2-terms
[controls-values]: ../../spec/controls.md#5-the-charts-values
[deployment-backups]: ../../spec/deployment.md#instances-and-backups
[deployment-guarantees]: ../../spec/deployment.md#1-guarantees
[deployment-later]: ../../spec/deployment.md#9-later-versions
[deployment-lease]: ../../spec/deployment.md#the-fetchers-lease
[design-components]: ../design.md#components
[design-keeps]: ../design.md#what-the-pipeline-keeps
[design-modes]: ../design.md#two-modes-one-pipeline
[design-outages]: ../design.md#outages-and-recovery
[design-testing]: ../design.md#testing-on-kind
[design-times]: ../design.md#times
[FETCH-1]: ../../spec/snapshots.md#10-fetching
[FETCH-2]: ../../spec/snapshots.md#10-fetching
[FETCH-6]: ../../spec/snapshots.md#10-fetching
[FETCH-7]: ../../spec/snapshots.md#10-fetching
[FETCH-8]: ../../spec/snapshots.md#10-fetching
[FETCH-9]: ../../spec/snapshots.md#10-fetching
[FETCH-14]: ../../spec/snapshots.md#10-fetching
[FETCH-15]: ../../spec/snapshots.md#10-fetching
[FETCH-16]: ../../spec/snapshots.md#the-fetcher
[FETCH-17]: ../../spec/snapshots.md#the-fetcher
[FETCH-18]: ../../spec/snapshots.md#the-fetcher
[FILE-1]: ../../spec/notices.md#7-a-filings-files
[FILE-2]: ../../spec/notices.md#7-a-filings-files
[FILE-3]: ../../spec/notices.md#7-a-filings-files
[GAP-1]: ../../spec/notices.md#5-polls-coverage-and-gaps
[GAP-2]: ../../spec/notices.md#5-polls-coverage-and-gaps
[GAP-3]: ../../spec/notices.md#5-polls-coverage-and-gaps
[GARAGE-1]: ../../spec/deployment.md#garage
[GARAGE-4]: ../../spec/deployment.md#garage
[ID-1]: ../../spec/snapshots.md#10-fetching
[ID-2]: ../../spec/snapshots.md#the-fetcher
[IMG-1]: ../../spec/deployment.md#the-pipelines-own-images
[IMG-2]: ../../spec/deployment.md#the-pipelines-own-images
[IMP-1]: ../../spec/snapshots.md#11-imports
[IMP-3]: ../../spec/snapshots.md#11-imports
[JOB-2]: ../../spec/snapshots.md#jobs-and-events
[JOB-4]: ../../spec/snapshots.md#jobs-and-events
[JOB-5]: ../../spec/snapshots.md#jobs-and-events
[KIND-2]: ../../spec/deployment.md#the-kind-cluster
[LAG-1]: ../../spec/notices.md#10-times-and-lags
[LEASE-1]: ../../spec/deployment.md#the-fetchers-lease
[LIST-1]: ../../spec/notices.md#8-backfills
[LIST-6]: ../../spec/notices.md#8-backfills
[LOG-1]: ../../spec/snapshots.md#8-history
[LOG-2]: ../../spec/snapshots.md#8-history
[LOG-3]: ../../spec/snapshots.md#8-history
[LOG-4]: ../../spec/snapshots.md#8-history
[MON-2]: ../../spec/deployment.md#monitoring
[MON-3]: ../../spec/deployment.md#monitoring
[method]: ../../investigation/edgar.md#method
[NATS-3]: ../../spec/deployment.md#nats
[NOTICE-1]: ../../spec/notices.md#8-backfills
[notices-files]: ../../spec/notices.md#7-a-filings-files
[notices-gaps]: ../../spec/notices.md#5-polls-coverage-and-gaps
[notices-open]: ../../spec/notices.md#12-open-questions
[notices-seen]: ../../spec/notices.md#6-when-each-source-first-showed-each-filing
[OBJ-4]: ../../spec/snapshots.md#5-objects
[ORDER-1]: ../../spec/notices.md#8-backfills
[PG-3]: ../../spec/deployment.md#instances-and-backups
[PG-4]: ../../spec/deployment.md#instances-and-backups
[PG-8]: ../../spec/deployment.md#the-stores-database
[PIN-1]: ../../spec/deployment.md#6-pins
[PIN-4]: ../../spec/deployment.md#6-pins
[PLACE-1]: ../../spec/deployment.md#placement
[plan-facts]: ../plan.md#facts
[plan-how]: ../plan.md#how-the-plan-works
[plan-investigation]: ../plan.md#investigation
[questions]: ../../investigation/edgar.md#questions
[RBAC-1]: ../../spec/controls.md#9-who-may-change-what
[RBAC-2]: ../../spec/controls.md#9-who-may-change-what
[RBAC-6]: ../../spec/controls.md#9-who-may-change-what
[SEEN-1]: ../../spec/notices.md#6-when-each-source-first-showed-each-filing
[SEEN-2]: ../../spec/notices.md#6-when-each-source-first-showed-each-filing
[SEEN-3]: ../../spec/notices.md#6-when-each-source-first-showed-each-filing
[SOURCE-1]: ../../spec/controls.md#6-source
[SOURCE-2]: ../../spec/controls.md#6-source
[SOURCE-5]: ../../spec/controls.md#6-source
[SRC-20]: ../../investigation/edgar.md#findings-so-far
[SRC-26]: ../../investigation/edgar.md#findings-so-far
[SRC-27]: ../../investigation/edgar.md#findings-so-far
[SRC-28]: ../../investigation/edgar.md#findings-so-far
[STORE-1]: ../../spec/snapshots.md#the-cluster-store
[STORE-3]: ../../spec/snapshots.md#the-cluster-store
[store-guarantees]: ../../spec/snapshots.md#1-guarantees
[store-open]: ../../spec/snapshots.md#13-open-questions
[store-records]: ../../spec/snapshots.md#6-records
[store-scope]: ../../spec/snapshots.md#9-scope
[TABLE-3]: ../../spec/notices.md#4-the-record-of-notices
[telemetry-guarantees]: ../../spec/telemetry.md#1-guarantees
[VAL-3]: ../../spec/controls.md#5-the-charts-values
[VAL-4]: ../../spec/controls.md#5-the-charts-values
[VIEW-6]: ../../spec/telemetry.md#8-the-views-grafana-reads
[VOL-1]: ../../spec/deployment.md#volumes
[VOL-2]: ../../spec/deployment.md#volumes
[VOL-3]: ../../spec/deployment.md#volumes
[XFER-1]: ../../spec/snapshots.md#records-the-cluster-made
[XFER-2]: ../../spec/snapshots.md#records-the-cluster-made
[XFER-3]: ../../spec/snapshots.md#records-the-cluster-made
