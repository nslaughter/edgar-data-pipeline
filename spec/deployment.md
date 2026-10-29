# Specification: deployment

**Status:** Version 2.0.0, draft, October 28, 2026. This document governs
what the pipeline's Helm charts install, in which order and at which
versions, and how they configure what they install: the charts, operators,
and images they pin; the custom resource definitions they install; each
component's replicas, storage, credentials, and placement; the cluster
store's bucket, database, table, and streams, and the fetcher's Lease; the
discoverer's streams, tables, credentials, and durable consumers, and
Grafana's access to the record; the scheduler's Deployment, Lease,
ServiceAccount, access to NATS, and durable consumers; the fetcher's
Deployment, ServiceAccount, credentials, access to its stores and to NATS,
durable consumers, and trust of the fake EDGAR's certificate authority; the
transfer's database role, Garage key, and access to the cluster store from
outside the cluster; the fake EDGAR that CI installs for tests, and how the
pipeline's images are built; the kind cluster they're installed on; the
tools that install them; and the tests that check them. Where a chart and
this document disagree, the chart is wrong or this document needs a new
version; say which before changing either.

It rests on the decisions to deploy on Kubernetes with Helm, on kind
([0001]); to install the definitions and the operators before the umbrella
chart, in four releases ([0011]); and to keep the record in PostgreSQL 18
([0004]); and on the decisions that chose the other components ([0002],
[0003], [0005], [0008]). It deploys what the snapshot store's version 1.1.0
leaves to it ([`snapshots.md`, §13](snapshots.md#left-to-other-specifications)),
what the notices' version 0.1.0 leaves to it
([`notices.md`, §12](notices.md#left-to-other-specifications)), and, for
Grafana, what the telemetry's version 0.1.0 leaves to it
([`telemetry.md`, §11](telemetry.md#left-to-other-specifications)). It
deploys the scheduler, the controller that the controls' version 1.0.0
describes ([`controls.md`](controls.md)), on the decision to reconcile the
controls with it ([0007]). It deploys the fetcher, which the snapshot
store's version 1.1.0 describes
([`snapshots.md`](snapshots.md#the-fetcher)), on the decision to send every
request to the SEC through one active fetcher ([0005]). It gives the
transfer, which that version also describes, its access to the cluster
store ([`snapshots.md`, §8](snapshots.md#8-history)). It installs, for
CI, the fake EDGAR that the fake EDGAR's version 0.1.0 describes
([`fake-edgar.md`](fake-edgar.md)).
Each component's resource requests and limits, which are measured on kind,
aren't in this version ([§9](#9-later-versions)). The pipeline it deploys
reads filings from the U.S. Securities and Exchange Commission's EDGAR
system, but nothing here depends on how EDGAR behaves.

## 1. Guarantees

Each guarantee holds because of the statements it lists ([§3](#3-statement-ids)).

1. **The same commit installs the same software.** Every chart is committed
   at an exact version, every third-party image is named by its digest, and
   each of the pipeline's own images by the commit it's built from.
   [PIN-1], [PIN-2], [PIN-3], [PIN-4], [PIN-5], [PIN-6], [PIN-7], [IMG-1],
   [IMG-3], [IMG-5].
2. **An upgrade is a reviewed change** to a chart, the kind configuration,
   or CI, since nothing is resolved at install time, and nothing the charts
   render is generated as they render it. [PIN-1], [PIN-4], [PIN-6],
   [PIN-7], [SECRET-2].
3. **Each component comes from the project that maintains it,** never from
   Bitnami. [CHART-2], [CHART-3].
4. **The database is PostgreSQL 18.** [PG-1], [PG-2].
5. **The charts render without the network,** so their tests never reach
   it. [PIN-2], [PIN-3].
6. **Every definition is on the cluster before any resource of its kind,
   and is upgraded with the chart that installs it.** [CHART-4], [CRD-1],
   [CRD-2], [CRD-3], [CRD-4], [CRD-5], [CRD-6].
7. **NATS, Garage, and the database keep running when one worker node is
   lost:** each keeps three replicas, one on each worker node; Garage keeps
   a copy of every object on each, and NATS of every stream and every
   durable consumer. [KIND-1], [PLACE-1], [PLACE-2], [NATS-1], [NATS-3],
   [NATS-4], [NATS-5], [NATS-6], [GARAGE-1], [GARAGE-3], [PG-3].
8. **Stored data outlives the cluster.** Garage's objects, the database's
   backups among them, NATS's streams, and Prometheus's and Alertmanager's
   data are kept in a directory of the host, where a cluster recreated with
   it finds them. [KIND-2], [VOL-1], [VOL-2], [VOL-3], [PG-4], [PG-5],
   [PG-10].
9. **No credential is committed, generated as the charts render, or shown
   outside a Secret.** An operator may generate one in the cluster, such as
   the database owner's password ([PG-6]). [SECRET-1], [SECRET-2],
   [SECRET-3], [SECRET-4].
10. **Only the fetcher writes to the cluster store, and neither a service
    nor the transfer can change a record:** each service has credentials of
    its own, and so does the transfer, and only the fetcher's may add records
    and objects ([LOG-3], [LOG-4], [LOG-5]). [GARAGE-4], [GARAGE-5], [PG-6],
    [PG-7], [PG-8], [PG-9], [PG-15], [FETCHER-5], [TRANSFER-1].
11. **Only the discoverer adds to the notices' tables, no service changes
    them, and Grafana reads only the views made for it,** each with a
    database role of its own ([TABLE-3], [VIEW-3]). [PG-7], [PG-11],
    [PG-12], [PG-13], [PG-14], [MON-5].
12. **A stream kept by interest accepts a message only once its durable
    consumers exist,** so it keeps each message it accepts for each service
    that reads it, and refuses one published sooner. [NATS-5], [NATS-6],
    [SCHED-8].
13. **The tests on kind never reach the SEC.** CI installs the fake EDGAR,
    which answers for the SEC's hosts from the stored versions, and every
    pod that asks for those hosts reaches it. There, the fetcher trusts only
    the certificate authority the release makes for the fake EDGAR, so a
    request of its that reached the SEC would fail before it was sent. The
    owner's installations never include it. [FAKE-1], [FAKE-2], [FAKE-3],
    [FAKE-4], [FAKE-5], [FETCHER-12], [FETCHER-13].
14. **One scheduler replica at a time acts, and the other is on another
    worker node,** ready to take the Lease over. A replica that Kubernetes
    stops hands the Lease over without waiting out its duration. The
    scheduler can do with the API server only what the controls grant it
    and its Lease needs. [SCHED-1], [SCHED-2], [SCHED-3], [SCHED-4],
    [SCHED-5], [SCHED-6], [RBAC-1].
15. **One fetcher replica at a time sends and reads jobs, and the other is
    on another worker node,** ready to take the Lease over once its
    duration has passed without a renewal, so the two never send at once
    ([FETCH-16]). The fetcher can do with the API server only what the
    controls grant it and its Lease needs, and no other service reads its
    credentials. [FETCHER-1], [FETCHER-2], [FETCHER-3], [FETCHER-4],
    [FETCHER-5], [FETCHER-10], [LEASE-1], [LEASE-2], [RBAC-2].
16. **The transfer reaches the cluster store from outside the cluster only
    through the API server of the cluster the owner names,** with
    credentials of its own that may only read, which it takes from that
    cluster, and it copies nothing from a cluster that holds the fake
    EDGAR. Nothing the charts render exposes the database or Garage
    outside the cluster. [PG-15], [GARAGE-5], [TRANSFER-1], [TRANSFER-2],
    [TRANSFER-3], [TRANSFER-4], [TRANSFER-5], [TRANSFER-6].

## 2. Terms

- **The charts:** the four Helm charts under `deploy/` that install the
  pipeline, each as a release of its own, in [§5](#5-dependencies)'s order
  ([0011]).
- **Umbrella chart:** the last of them, which installs the platform's
  components, the database, and later the pipeline's own services and
  controls ([0001], [0011]).
- **Dependency:** a chart that one of the charts installs as a subchart,
  listed in its `Chart.yaml`.
- **Definition:** a `CustomResourceDefinition`. A custom resource can be
  created only once its kind's definition is on the cluster.
- **Image:** a container image. An image built from this repository is the
  pipeline's own; any other is third-party.
- **Pin:** a reference that names one artifact and no other: a chart's exact
  version, committed; an image's digest; a tool's exact version.
- **The installed charts:** the charts as CI installs them on kind, in
  order, with the values it installs them with.
- **Worker node:** a node of the kind cluster other than its control plane.
- **The host directory:** the directory of the machine kind runs on that
  kind's configuration mounts into every worker node ([KIND-2]).
- **Credential:** a password, token, key, or shared secret by which one
  component proves itself to another, or that guards an administrative
  interface.
- **Service:** one of the pipeline's own programs that runs in the cluster,
  such as the fetcher, from one of its own images.
- **Active replica:** of a service whose replicas act one at a time, the
  replica that holds the service's Lease, and so does its work, such as
  the scheduler's ([SCHED-4]) or the fetcher's ([FETCH-16]).
- **The cluster store:** the store's objects in Garage and records in
  PostgreSQL, as the snapshot store's specification describes it
  ([`snapshots.md`](snapshots.md#the-cluster-store)).
- **The transfer:** the program the owner runs, outside the cluster, that
  copies records and their objects from the cluster store into `data/`
  ([`snapshots.md`](snapshots.md#2-terms)). It isn't a service.
- **The notices' tables and views:** those that
  [`schemas/notices.sql`](schemas/notices.sql) defines, in which the
  discoverer keeps what it finds
  ([`notices.md`](notices.md#4-the-record-of-notices)).
- **Durable consumer:** a JetStream consumer with a name, which keeps its
  place in its stream, the messages it has delivered and those
  acknowledged, for whichever of its service's replicas reads from it, and
  across their restarts and NATS's ([0003]).
- **The fake EDGAR:** a service for tests, which answers for the SEC's
  hosts, `www.sec.gov` and `data.sec.gov`, from the stored versions under
  `data/` ([`fake-edgar.md`](fake-edgar.md)). CI installs it; the owner's
  installations don't ([FAKE-1]).

## 3. Statement IDs

Every normative statement in this document has an ID, such as `PG-1`: an
area and a number. The areas `CHART`, `PIN`, `PG`, `CRD`, `IMG`, `KIND`,
`VOL`, `PLACE`, `NATS`, `GARAGE`, `MON`, `SECRET`, `LEASE`, `SCHED`,
`FETCHER`, `TRANSFER`, and `FAKE` belong to this document. `FETCHER`, the
fetcher's deployment, is an area apart from the snapshot store's `FETCH`,
its rules for fetching, and `TRANSFER`, the transfer's access to the
cluster store, one apart from the store's `XFER`, its rules for copying. An
ID is assigned once and never reused or renumbered. A statement that is
dropped stays in the [index](#8-index), marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of the charts and the kind configuration
  as committed, and of what the charts render. Tests check it by rendering
  each chart with `helm template`, with the values CI installs it with,
  apart from the credentials and the database archive's server name, which
  CI sets for each cluster, and for which tests give values of their own
  ([SECRET-4], [PG-10]). They run without a cluster or the network, with
  the other tests, wherever those run, and need Helm.
- A **rule** is how the installed charts, or the tools around them, behave,
  or how a service uses what the charts give it, such as the scheduler its
  Lease ([SCHED-4]) or the fetcher its consumers ([FETCHER-10]). Tests check the installed charts in the CI job that
  installs them on kind, and are skipped where no cluster is installed. A
  rule on a tool, such as [IMG-2]'s command, is checked by tests of that
  tool, and one on a service by the service's tests.

The index says how each statement is checked: by `tests`, by tests on
`kind`, in `review` of the pull request that changes what it covers, or by
the `operator`, the owner, who keeps it. Every statement checked by `tests`
or `kind` has at least one test marked with its ID, as
`@pytest.mark.spec("PG-1")`, or, in a Go test, in the form the first Go
test sets ([0006]), and every marker names an ID in a specification's
index. A test, `tests/test_spec_coverage.py`, enforces
both, except for a statement whose tests a later task in the
[plan](../docs/plan.md) writes. It lists such a statement as awaiting that
task, and fails if the statement has a test or the plan marks the task
done. This version's statements come before the charts they describe: T20
builds the charts and writes their tests, apart from [IMG-1] to [IMG-3]'s,
[IMG-5]'s, and [FAKE-1] to [FAKE-5]'s, which T21 writes with the first of
the pipeline's images, the fake EDGAR's; [LEASE-1]'s, [LEASE-2]'s, and
[FETCHER-1] to [FETCHER-13]'s, which T23 writes with the fetcher;
[SCHED-1] to [SCHED-9]'s, which T22 writes with the scheduler; and
[PG-15]'s, [GARAGE-5]'s, and [TRANSFER-1] to [TRANSFER-6]'s, which T36
writes with the transfer. Until then each awaits its task.

## 4. Layout

```text
deploy/
  prometheus-crds/             release 1: the Prometheus operator's definitions
  cert-manager/                release 2: cert-manager
  operators/                   release 3: the operators, and the other definitions
    files/garagenodes.deuxfleurs.fr.yaml
                               Garage's definition, from its source tree
  chart/                       release 4: the umbrella chart
    charts/garage/             Garage's chart, from its source tree
    files/records.sql          the store's table, from spec/schemas/
    files/notices.sql          the notices' tables and views, from spec/schemas/
    files/dashboards.sql       the views Grafana reads, from spec/schemas/
    values.schema.json         what its values must hold
  kind.yaml                    the kind cluster's configuration
```

Each of the four charts holds:

```text
CHART/
  Chart.yaml                   its dependencies, each at an exact version
  Chart.lock                   the same dependencies, as Helm resolved them
  charts/NAME-VERSION.tgz      each dependency from a chart repository
  values.yaml
  templates/                   its own templates, if it has any
```

- **CHART-1** (invariant) The four charts are at the paths the tree gives,
  each with a `Chart.yaml`, a `Chart.lock`, and a `values.yaml`, and the
  kind cluster's configuration is at `deploy/kind.yaml`.

### The kind cluster

kind runs each node as a container on one machine ([0001]). Garage, NATS,
and the database each run three replicas, one on each worker node, so the
loss of a node can be shown ([0002]).

- **KIND-1** (invariant) `deploy/kind.yaml` names one control-plane node
  and three worker nodes.
- **KIND-2** (invariant) `deploy/kind.yaml` mounts one host directory into
  every worker node at `/var/local-path-provisioner`, where kind's storage
  provisioner keeps each volume, so every volume's data is kept on the
  host ([0001]). Which directory is the configuration's to say: one that
  git ignores, or one outside the repository.

### Volumes

kind's default StorageClass, `standard`, deletes a volume's data when its
claim is deleted, and names the volume's directory with a name generated
for it, so a cluster recreated with the same host directory can't find the
data. The umbrella chart adds a class of its own that keeps a volume's data
and names its directory after its claim.

- **VOL-1** (invariant) The umbrella chart renders one StorageClass,
  `edgar-retained`, of kind's provisioner, `rancher.io/local-path`, with
  `reclaimPolicy: Retain`, `volumeBindingMode: WaitForFirstConsumer`, and a
  `pathPattern` that names each volume's directory by its claim's namespace
  and name.
- **VOL-2** (invariant) Each claim the umbrella chart renders, or asks a
  StatefulSet or an operator to create, for NATS, Garage, Prometheus, or
  Alertmanager names `edgar-retained`. The database's claims name kind's
  `standard` class ([PG-3]).
- **VOL-3** (rule) When a claim of `edgar-retained` is deleted, its data
  stays in the host directory, and a new claim of the same namespace and
  name finds it there, in the same cluster or in one recreated with the
  same host directory.

The database's volumes are on kind's class because a data directory left by
an earlier cluster would stop a new one from initializing. What outlives
the cluster for the database is its backups in Garage ([PG-4]), from which
it can be restored ([0004]).

## 5. Dependencies

The charts are installed as four releases, in this order, each once the
release before it is ready ([0011]). Each installs its components from the
charts their own projects maintain ([0001]).

| Order | Chart | Release | Namespace | Installs |
| --- | --- | --- | --- | --- |
| 1 | `deploy/prometheus-crds/` | `prometheus-crds` | `edgar` | The Prometheus operator's definitions ([0008]) |
| 2 | `deploy/cert-manager/` | `cert-manager` | `cert-manager` | cert-manager, with its definitions, which the Barman Cloud plugin requires for its certificates |
| 3 | `deploy/operators/` | `operators` | `cnpg-system` | The CloudNativePG operator ([0004]) and its Barman Cloud plugin, which backs the database up to Garage ([0002]), with their definitions; Garage's definition; and the pipeline's own ([`controls.md`](controls.md#4-the-resources)) |
| 4 | `deploy/chart/` | `edgar` | `edgar` | NATS, with JetStream ([0003]); Garage ([0002]); kube-prometheus-stack ([0008]); the database ([§7](#7-postgresql)); the scheduler ([SCHED-1]); the fetcher ([FETCHER-1]); the fake EDGAR, where CI installs it ([FAKE-1]); and later the pipeline's other services and its controls |

Their dependencies:

| Chart | Dependency | Repository |
| --- | --- | --- |
| `deploy/prometheus-crds/` | `prometheus-operator-crds` | `oci://ghcr.io/prometheus-community/charts` |
| `deploy/cert-manager/` | `cert-manager` | `oci://quay.io/jetstack/charts` |
| `deploy/operators/` | `cloudnative-pg` | `https://cloudnative-pg.github.io/charts` |
| `deploy/operators/` | `plugin-barman-cloud` | `https://cloudnative-pg.github.io/charts` |
| `deploy/chart/` | `nats` | `https://nats-io.github.io/k8s/helm/charts/` |
| `deploy/chart/` | `garage` | None: `script/helm/garage` in Garage's source tree, `https://git.deuxfleurs.fr/Deuxfleurs/garage`, committed ([PIN-3]) |
| `deploy/chart/` | `kube-prometheus-stack` | `oci://ghcr.io/prometheus-community/charts` |

The database itself, a CloudNativePG `Cluster` and its `ImageCatalog`, is
in the umbrella chart's own templates ([§7](#7-postgresql)), as are the
scheduler ([SCHED-1]) and the fetcher ([FETCHER-1]). Tempo, Loki, Alloy,
and the pipeline's other services arrive in later versions. The
operators run as their charts deploy them by default, one replica each:
while one restarts, its resources wait in the API server.

- **CHART-2** (invariant) Each chart's dependencies are those the table
  gives it, each under its name and from its repository, and no others.
- **CHART-3** (invariant) Nothing comes from Bitnami ([0001]): no
  dependency's repository, no repository of a chart a dependency includes,
  and no image a chart renders contains `bitnami`.
- **CHART-4** (rule) CI installs and upgrades the charts as the first
  table's releases, each in its namespace, in the table's order, and each
  with `--wait`, so that a release starts only once the release before it
  is ready. The owner installs and upgrades them the same way.

### Definitions

Helm installs a chart's `crds/` directory on the first install only, and
never upgrades it. It also checks a release against the cluster's API
before it creates anything, so a resource can't be in the release that
installs its definition ([0011]). So each definition is a template of a
release before the first that needs it, and Helm upgrades it with that
release.

- **CRD-1** (invariant) Every definition the charts install is rendered
  from a template: each chart renders the same manifests with
  `helm template --include-crds` as without it, so none comes from a
  `crds/` directory.
- **CRD-2** (invariant) Each definition is rendered by one chart: the
  Prometheus operator's by `deploy/prometheus-crds/`; cert-manager's by
  `deploy/cert-manager/`; and CloudNativePG's, the Barman Cloud plugin's,
  and Garage's by `deploy/operators/`, which also renders the pipeline's
  own ([API-2](controls.md#4-the-resources)). The umbrella chart renders
  none.
- **CRD-3** (invariant) Each custom resource a chart renders is of a kind
  whose definition an earlier chart in the table's order renders. No
  admission webhook that a chart renders with `failurePolicy: Fail` admits
  a resource that the same chart renders.
- **CRD-4** (invariant) Every definition the charts render carries the
  annotation `helm.sh/resource-policy: keep`, so Helm never deletes it, and
  with it every resource of its kind, when a release is uninstalled or an
  upgrade drops it.
- **CRD-5** (invariant) Each definition is of the version of what uses it.
  cert-manager's, CloudNativePG's, and the plugin's come in the charts of
  the operators that use them, and the pipeline's own are those in
  `spec/schemas/`. Two come apart from what uses them, and match it:
  - `prometheus-operator-crds`'s `appVersion` is the tag of the Prometheus
    operator's image that kube-prometheus-stack renders;
  - Garage's definition is the file
    `script/k8s/crd/garagenodes.deuxfleurs.fr.yaml` from Garage's source
    tree, at the tag [PIN-3] names for Garage's chart, committed unchanged
    at `deploy/operators/files/`, and rendered with only [CRD-4]'s
    annotation added.
- **CRD-6** (rule) Every definition on the cluster of the installed charts
  belongs to one of the four releases, as Helm's annotations on it say. No
  component creates one.

### Placement

- **PLACE-1** (invariant) NATS's servers, Garage's nodes, and the
  database's instances each need a worker node of their own. NATS's and
  Garage's pods carry, against the other pods of their component, a
  required pod anti-affinity, or a topology spread constraint with
  `whenUnsatisfiable: DoNotSchedule`, on `kubernetes.io/hostname`. The
  `Cluster` sets `affinity.podAntiAffinityType: required`, with that
  topology key.
- **PLACE-2** (rule) In the installed charts, each of the three runs its
  three replicas on the three worker nodes, one on each.

### NATS

- **NATS-1** (invariant) NATS runs as one cluster of three servers with
  JetStream, each keeping JetStream's file store on a claim of its own
  ([VOL-2]) ([0003]).
- **NATS-2** (rule) In the installed charts, the three servers form one
  cluster, and JetStream reports all three in its meta group, with a
  leader.

The store's jobs and events travel in two streams
([`snapshots.md`](snapshots.md#jobs-and-events)), and the discoverer's
orders and notices in two more
([`notices.md`](notices.md#streams)), each kept as its specification says,
in files, and replicated on all three servers, so a stream keeps its
messages and its leader through the loss of a node:

| Stream | Subjects | Retention | Storage | Replicas |
| --- | --- | --- | --- | --- |
| `JOBS` | `edgar.jobs.poll`, `edgar.jobs.filing`, `edgar.jobs.backfill` | Work queue | File | 3 |
| `RECORDS` | `edgar.records.poll`, `edgar.records.filing`, `edgar.records.backfill` | Interest | File | 3 |
| `ORDERS` | `edgar.orders.backfill` | Work queue | File | 3 |
| `NOTICES` | `edgar.notices.filing`, `edgar.notices.gap`, `edgar.notices.backfill` | Interest | File | 3 |

- **NATS-3** (invariant) The umbrella chart renders a Job, a Helm hook run
  after each install and upgrade, that creates each stream the umbrella
  chart's values list, or updates it to their configuration, through
  JetStream's API. It waits until JetStream accepts its requests, and
  succeeds when each stream already is as the values say. In this version,
  the values list the table's four streams, with the table's subjects,
  retention, storage, and replicas ([0003]).
- **NATS-4** (rule) In the installed charts, each stream the values list
  exists with their configuration, with a replica on each of the three
  servers, and the Job succeeds when it runs again.

#### Durable consumers

A work queue keeps a message until a consumer acknowledges it, whenever the
consumer is made. A stream kept by interest keeps a message only for the
consumers it has when the message is published, and none for a consumer
made later, so each service that reads one needs its durable consumer
before what it should see is published. This version makes the consumers of
the discoverer, the scheduler, and the parse workers
([`notices.md`, §12](notices.md#left-to-other-specifications)), before any
of them is built, so each stream keeps what they read from the first
message on, until they read it:

| Consumer | Stream | Subjects | Read by | Unacknowledged at most |
| --- | --- | --- | --- | --- |
| `discoverer-polls` | `RECORDS` | `edgar.records.poll` | The discoverer: poll events one at a time, in the stream's order ([DISC-1]) | 1 |
| `discoverer-jobs` | `RECORDS` | `edgar.records.filing`, `edgar.records.backfill` | The discoverer: the events of the jobs it queued, one at a time ([DISC-1], [FILE-5], [LIST-3]) | 1 |
| `discoverer-orders` | `ORDERS` | `edgar.orders.backfill` | The discoverer: the scheduler's orders ([LIST-1]) | JetStream's default |
| `scheduler-notices` | `NOTICES` | `edgar.notices.gap`, `edgar.notices.backfill` | The scheduler: gap and backfill notices ([NOTICE-1], [NOTICE-2]) | JetStream's default |
| `parse-workers-notices` | `NOTICES` | `edgar.notices.filing` | The parse workers: filing notices, as the facts specification will say | JetStream's default |

Each is a pull consumer, which any replica of its service may read from,
though only the scheduler's active replica reads the scheduler's
([SCHED-4]). A message it delivers is acknowledged one by one. One not
acknowledged within JetStream's default of 30 seconds is delivered again,
with no limit on how often, so a service that stops partway through a
message gets it again ([DISC-1]). It may go to another replica while the
first is still at work on it, which [DISC-2] makes harmless: a message
delivered again adds
nothing twice. With one unacknowledged at most, each of the discoverer's
consumers of `RECORDS` delivers an event only once the one before it is
acknowledged, which the discoverer does only once the event's transaction
is committed ([DISC-2]). So it handles poll events in the stream's order
([DISC-1]), and the events of its jobs one at a time: when an event's
transaction checks whether a filing or a Backfill is now complete, it sees
the readings each event before it added ([FILE-5], [LIST-3]). Were two
events of one filing or Backfill handled at once, under PostgreSQL's
default isolation neither transaction would see the other's reading, so
neither would find it complete. Delivery begins at the stream's first
message. Each consumer has three replicas, as its stream has, so its place
in the stream survives the loss of a node.

The scheduler's consumer of poll events, `scheduler-polls`, comes with the
scheduler, and is configured and made as these are ([SCHED-8]). So do the
fetcher's consumers of jobs, one for each kind, which come with the fetcher
([FETCHER-9]).

Helm creates the umbrella chart's services, and waits for them to be ready,
before its hook Jobs run ([CHART-4]), so a service may publish before the
Job has made what it publishes to. JetStream refuses a message that no
stream's subjects take, so its publisher knows it wasn't kept, but a stream
kept by interest accepts a message it has no consumer for, and keeps it for
none. So the Job makes each stream's consumers as soon as it has made the
stream, and gives a stream kept by interest its subjects only once its
consumers exist: it makes the stream with only its name as its subject,
JetStream's default when none is given, then its consumers, which
JetStream lets filter subjects their stream doesn't yet take, and then
gives the stream its subjects. Until then, a message published to them is
refused, as one is before the stream exists. A refused message isn't kept
for none, but the stream keeps it only once it's published again. A gap
notice the discoverer publishes, when it starts, from polls the record
already holds follows no message, so no message's delivery brings it again
([GAP-3], [DISC-2]): a new install has such polls when its database was
restored while NATS's data wasn't kept. The notices' version 0.1.0 doesn't
say that the discoverer publishes a refused notice again, and [GAP-3]
publishes a Source's gap notice again only when its gap changes or the
discoverer starts again. Until then, the scheduler doesn't set the Source's
`openGapSince` from it ([NOTICE-1]).

The Job also makes the streams in an order that keeps a message that
follows another from being refused: an event follows a job, and a notice
follows an event or an order. It makes `NOTICES` first, then `RECORDS`,
`ORDERS`, and `JOBS` last, so no event can be published before `RECORDS`
takes it, and no notice that follows a message before `NOTICES` does.

- **NATS-5** (invariant) The umbrella chart's values list the table's
  durable consumers, each on its stream, with the table's subjects as its
  filter subjects and, where the table gives one, its most unacknowledged
  messages as its `max_ack_pending`. Each is a pull consumer with
  `ack_policy: explicit`, `deliver_policy: all`, `max_deliver: -1`, and
  three replicas. [NATS-3]'s Job creates each consumer the values list, or
  updates it to their configuration, once it has created or updated the
  consumer's stream and before the next stream. It creates or updates the
  streams in the order `NOTICES`, `RECORDS`, `ORDERS`, `JOBS`, and succeeds
  when each consumer already is as the values say. It gives a stream kept
  by interest a subject only once each consumer the values list on that
  stream exists: when it creates such a stream, it creates it with only its
  name as its subject, and it updates the stream to the values' subjects
  once it has created or updated the stream's consumers.
- **NATS-6** (rule) In the installed charts, each consumer the values list
  exists on its stream with their configuration, with a replica on each of
  the three servers, and the Job succeeds when it runs again. While the Job
  makes a stream kept by interest, each message published to the subjects
  the values give the stream is either refused or kept for each of the
  stream's consumers whose filter subjects match it.

### Garage

- **GARAGE-1** (invariant) Garage runs as a StatefulSet of three, with a
  replication factor of 3, each node keeping its metadata and its data on
  claims of its own ([VOL-2]) ([0002]). It finds its peers through
  Kubernetes, with its chart's values `garage.kubernetesSkipCrd` and
  `garage.noClusterRole` set to `true`, so it creates no definition
  ([CRD-6]) and holds only a Role in its namespace.
- **GARAGE-2** (invariant) The umbrella chart renders a Job, a Helm hook run
  after each install and upgrade, that sets Garage up as the umbrella
  chart's values describe ([0002]). It:
  - assigns each of the three nodes a zone of its own and a capacity in the
    layout, and applies it;
  - imports each key, with the ID and secret [SECRET-1] describes;
  - creates each bucket;
  - grants each key its buckets.

  It reaches Garage's admin API with the admin token, and succeeds when
  what it would create already exists. In this version, the values list
  two buckets: `database-backups`, with one key, which may read and write
  only that bucket, for the database's backups ([PG-4]); and the cluster
  store's bucket, with its keys ([GARAGE-4], [GARAGE-5]).
- **GARAGE-3** (rule) In the installed charts, Garage's layout holds its
  three nodes, in three zones, each node holding a copy of every object.
  Each bucket and key the values list exists, with the grants they give,
  and the Job succeeds when it runs again.
- **GARAGE-4** (invariant) The cluster store's bucket is `snapshots`. Each
  service that uses it has a key of its own, which may use no other
  bucket: the fetcher's may read and write it, and each other service's
  may only read it ([LOG-4]). No key has Garage's `owner` permission on
  any bucket, which would let it change the bucket's configuration. In
  this version, the values list three keys for the bucket: the fetcher's;
  the discoverer's, which may only read it
  ([`notices.md`, §12](notices.md#left-to-other-specifications)); and the
  transfer's ([GARAGE-5]). Each other service's comes with that service
  ([§9](#9-later-versions)).
- **GARAGE-5** (invariant) The transfer has a key of its own, `transfer`,
  which may only read `snapshots`, may use no other bucket, and has
  Garage's `owner` permission on none ([LOG-5]). Its ID and secret are
  values of the umbrella chart, as every key's are ([SECRET-1]).

### Monitoring

- **MON-1** (invariant) kube-prometheus-stack scrapes nothing kind doesn't
  expose: it renders no monitor or rule group for the controller manager,
  Kubernetes's own scheduler (`kube-scheduler`), etcd, or kube-proxy
  ([0008]).
- **MON-2** (invariant) Grafana is read-only ([0008]). It admits anonymous
  viewers with the `Viewer` role only, with its login form, basic
  authentication, and sign-up turned off, so no one signs in to change
  anything. Its data sources come only from Grafana's own `datasources`
  value, which its configuration checksum covers, so a change to them
  restarts Grafana. Its sidecar provisions dashboards only, which Grafana
  reads again from their files on its own, so no reload needs a sign-in.
  Its dashboards come only from the charts, and a provisioned dashboard
  can't be saved from its interface.
- **MON-3** (invariant) Alertmanager sends nothing outside the cluster: its
  configuration's only receiver is `null`, with no integration ([0008]).
- **MON-4** (invariant) Prometheus, Alertmanager, and Grafana each run one
  replica. Prometheus and Alertmanager keep their data on claims
  ([VOL-2]). Grafana keeps none, since what it shows is provisioned.

Grafana reads the record through the views made for it, as a role that may
read nothing else ([VIEW-3], [PG-11], [PG-13]). It reads them from the
database's replicas, so the dashboards' queries, which read every poll
([`telemetry.md`, §11](telemetry.md#11-open-questions)), don't load the
primary, where the fetcher and the discoverer write. CloudNativePG issues
the database's server certificate from a certificate authority of its own,
which Grafana checks the server against.

- **MON-5** (invariant) Grafana's `datasources` value holds one PostgreSQL
  data source, which isn't editable. It connects, at port 5432, to the
  Service that CloudNativePG makes for the `Cluster`'s replicas, the
  `Cluster`'s name followed by `-ro`, and reads the database `edgar` as
  Grafana's role, `grafana` ([PG-11]). Its password is an environment
  variable of Grafana's container that the data source names, set from the
  role's Secret, so the value holds no credential ([SECRET-3]). It verifies
  the server's certificate and name, with `sslmode: verify-full`, against
  the `Cluster`'s certificate authority. Grafana's pod mounts the
  authority's certificate alone, `ca.crt`, from the Secret CloudNativePG
  keeps it in, without the authority's key.
- **MON-6** (rule) In the installed charts, Grafana's PostgreSQL data
  source connects to the database, verifying its certificate, and reads
  each view in the schema `dashboards`.

### Credentials

Each component keeps its own credentials in Secrets ([0001]). An operator
that generates one in the cluster, such as CloudNativePG for the database's
owner, keeps it there. Every other credential comes from the owner's
values, so the same values render the same chart.

- **SECRET-1** (invariant) Every credential the umbrella chart's
  components use, apart from those an operator generates in the cluster, is
  a value of the umbrella chart. Its `values.schema.json` requires it, and
  none of its committed values files sets it, so `helm template` fails
  without it. In this version: Garage's RPC secret and admin token, the ID
  and secret of each Garage key, the password of each service's database
  role ([PG-7]), of Grafana's ([PG-11]), and of the transfer's ([PG-15]),
  and Grafana's admin password.
  The chart renders each into a Secret, from which its component reads it.
- **SECRET-2** (invariant) The charts render the same manifests from the
  same values: nothing they render is generated as they render it, such as
  a random credential.
- **SECRET-3** (invariant) A credential appears in what the charts render
  only in a Secret. It never appears in a ConfigMap, in a container's
  arguments or literal environment values, or in a custom resource's
  `spec`.
- **SECRET-4** (rule) CI generates new credentials for each cluster it
  creates. The owner keeps theirs in a values file git ignores, and never
  puts one in a commit, a pull request, or a log.

### The fetcher's Lease

One fetcher replica at a time sends requests, the one holding the Lease
([0005]). It stops leading when it can't renew the Lease within its renewal
deadline, and another replica takes the Lease over only once a duration has
passed without a renewal, so the gap between the two keeps them from
sending at once ([FETCH-16]). By default, the timings are those that
Kubernetes's own controllers and controller-runtime's leader election use:
a handover after the active replica is lost takes up to the duration, which
the record shows as a gap. The fetcher's leader election is client-go's
([0006]), which refuses timings that aren't positive, a renewal deadline
that isn't shorter than the duration, and one that isn't greater than 1.2
times the retry period, so the chart refuses them before the fetcher
starts.

- **LEASE-1** (invariant) The umbrella chart gives the fetcher its Lease's
  name, `fetcher`, in the release namespace, and its timings, from the
  values `fetcher.lease.durationSeconds`, 15 by default,
  `fetcher.lease.renewDeadlineSeconds`, 10, and
  `fetcher.lease.retryPeriodSeconds`, 2. It fails to render values where
  any of the three is below 1, where the renewal deadline isn't shorter
  than the duration, where the retry period isn't shorter than the renewal
  deadline, or where the renewal deadline isn't greater than 1.2 times the
  retry period, so it refuses what client-go's leader election refuses
  ([0006]). It grants the fetcher's ServiceAccount, in the release
  namespace, `create` on Leases, and `get` and `update` on the Lease
  `fetcher` only.
- **LEASE-2** (rule) In the installed charts, one of the fetcher's pods
  holds the Lease `fetcher` in the release namespace, with
  `leaseDurationSeconds` as the values give it.

### The fetcher

The fetcher sends every request to the SEC and writes the cluster store
([`snapshots.md`](snapshots.md#the-fetcher), [0005]). It runs as two
replicas, of which one at a time sends requests and takes jobs: the one that
holds the Lease `fetcher`, through client-go's leader election ([LEASE-1],
[FETCH-16], [0006]). The other waits on another worker node, to take the
Lease over when the first is lost. The fetcher uses the API server, for the
SecAccess and its Lease; NATS, for its jobs and events; Garage and the
database, for the cluster store; and the SEC's hosts, which the fake EDGAR
answers for where CI installs it.

Unlike the scheduler's ([SCHED-4]), a fetcher replica that Kubernetes stops
doesn't release the Lease, so another replica takes it over only once its
duration has passed without a renewal, as when the active replica is lost
([the fetcher's Lease](#the-fetchers-lease)). A replica that released it
would have to stop sending first, and release it no sooner than 0.2 seconds
after its last request, or the next replica's first request could break
[FETCH-6] or [FETCH-7], on which the SEC's limit rests. A mistake in the
scheduler's handover would at worst queue polls twice, which the fetcher
still sends within its limit. So an upgrade or a drain that stops the active
fetcher pauses fetching for up to the Lease's duration, 15 seconds by
default, and a Source whose poll's job expires meanwhile shows a gap, as
when the active replica is lost ([JOB-5],
[§5 of the notices](notices.md#5-polls-coverage-and-gaps)).

- **FETCHER-1** (invariant) The umbrella chart renders, in the release
  namespace, a Deployment `fetcher` of two replicas, whose pods run as the
  ServiceAccount `fetcher`, which the chart also renders. Its container
  runs the fetcher's image, one of the pipeline's own, from the value
  `fetcher.image`, `edgar/fetcher` by default ([IMG-1]), and names the port
  it serves its metrics on `metrics` ([METRIC-1]). It claims no volume. The
  chart gives the fetcher `images.commit`, its image's tag, which its
  records' `tool` names ([§6 of the store](snapshots.md#6-records)).
- **FETCHER-2** (invariant) The fetcher's pods each need a worker node of
  their own: they carry, against each other, a required pod anti-affinity,
  or a topology spread constraint with `whenUnsatisfiable: DoNotSchedule`,
  on `kubernetes.io/hostname`, as the scheduler's do ([SCHED-2]).
- **FETCHER-3** (rule) In the installed charts, the fetcher's two pods run
  on two worker nodes. When the pod that holds the Lease `fetcher` is
  deleted, a fetcher pod other than the deleted one holds the Lease, though
  not before the Lease's duration has passed since the deleted pod's last
  renewal.

#### The fetcher's grants

The fetcher's grants on the controls are [RBAC-2]'s: it reads the
SecAccess, to send only while requests are allowed ([ACCESS-1]), and sets
its `status`, to record a stop ([ACCESS-3]). The SecAccess is
cluster-scoped, so a ClusterRole grants them. Its Lease needs what
[LEASE-1] grants, by a Role in the release namespace. It's granted nothing
else: it reads no Source or Backfill, whose jobs carry what it needs
([JOB-2], [JOB-3]), and records no Kubernetes Events, as the scheduler
records none ([SCHED-6]). Whether a replica is the active one is in its
metrics ([METRIC-4]) and in the Lease.

- **FETCHER-4** (invariant) The umbrella chart grants the fetcher's
  ServiceAccount [RBAC-2]'s verbs, on the SecAccess and its `status`, by a
  ClusterRole, and [LEASE-1]'s, on Leases, by a Role in the release
  namespace. No RoleBinding or ClusterRoleBinding the charts render binds
  the ServiceAccount to any other role.

#### The fetcher's credentials and stores

The fetcher proves itself to Garage with its key and to the database with
its role's password ([GARAGE-4], [PG-7]), and identifies the owner to the
SEC with the owner's user agent ([FETCH-2]). Each is a value of the
umbrella chart, which the chart renders into a Secret ([SECRET-1], [ID-2]).
The fetcher reads each in its environment, set from its Secret by
reference, as it reads its user agent ([FETCH-17]), so none is in its
arguments or in a literal value ([SECRET-3]). Without a user agent the
fetcher sends nothing ([FETCH-17]), so the chart requires one, as it
requires each credential, rather than install a fetcher that can't fetch.

Garage's S3 API speaks plain HTTP in the cluster: a request carries a
signature made with the key's secret, never the secret itself. The
database speaks TLS, with a certificate that CloudNativePG issues from an
authority of its own, which the fetcher checks the server against, as
Grafana does ([MON-5]), so it adds records only to the database.

Which variables hold the key and the password, and how the chart gives the
fetcher the rest of what these statements say it gives it, as arguments or
in its environment, is for the chart and the fetcher to agree, as it is for
the scheduler: its Lease's name and timings ([LEASE-1]); the addresses of
Garage, the database, and NATS; the bucket and the region; the database's
name and the role's; where its pod mounts each certificate ([FETCHER-7],
[FETCHER-12]); its consumers' names; the commit; and the release
namespace.

- **FETCHER-5** (invariant) The fetcher's container reads in its
  environment, each by reference to the Secret the umbrella chart renders
  it into: the ID and secret of the Garage key `fetcher` ([GARAGE-4]); the
  password of the database role `fetcher` ([PG-7]); and the user agent, as
  `SEC_USER_AGENT` ([ID-2]). It reads no other Secret in its environment,
  and mounts none but for the certificates of [FETCHER-7] and
  [FETCHER-12]. No other service's container reads the fetcher's key or its
  role's password. The chart's `values.schema.json` requires
  `fetcher.secUserAgent`, a string that contains an `@` ([FETCH-2]).
- **FETCHER-6** (invariant) The umbrella chart gives the fetcher the
  cluster store's bucket, `snapshots` ([GARAGE-4]), and Garage's S3 API, at
  `http://garage:3900`, the S3 port of the Service `garage` in the release
  namespace, with the region that the chart's values give Garage's S3 API.
- **FETCHER-7** (invariant) The umbrella chart gives the fetcher the
  database: it connects, at port 5432, to the Service that CloudNativePG
  makes for the `Cluster`'s primary, the `Cluster`'s name followed by
  `-rw`, and uses the database `edgar` as the role `fetcher` ([PG-7]). It
  verifies the server's certificate and name, as `sslmode: verify-full`
  does, against the `Cluster`'s certificate authority. The fetcher's pod
  mounts the authority's certificate alone, `ca.crt`, from the Secret
  CloudNativePG keeps it in, without the authority's key, as Grafana's
  does ([MON-5]).

#### The fetcher's access to NATS

NATS asks no credential in this version ([§9](#9-later-versions)), so the
fetcher's access to NATS is NATS's address, and the streams and consumers
the Job makes for what it reads and publishes. It reads jobs from `JOBS`
through consumers of its own, and publishes each event on the subject of
its job's kind, which `RECORDS` takes ([JOB-6]).

Before each request, the fetcher takes the job queued first of the first
kind that has one waiting: `poll`, then `filing`, then `backfill` ([JOB-4]).
A consumer delivers the messages it filters in the order its stream holds
them, whatever their subjects, so one consumer of all three kinds would
deliver a Backfill's job queued before a poll's ahead of it. So the fetcher
has a consumer for each kind, and asks the polls' first. A work queue lets
no two of its consumers filter the same subject, and one filter subject
each keeps them apart. `JOBS` keeps each job until a consumer acknowledges
it, whenever the consumer is made, so the consumers come with the fetcher.

Each delivers as many unacknowledged jobs at once as JetStream's default
allows. With one at most, a job that a lost replica took and didn't
acknowledge would hold back each later job of its kind until JetStream
delivered it again: 30 seconds after the replica last said it was at work
on the job, as the next paragraph explains, or after the job was
delivered, if it said nothing ([durable consumers](#durable-consumers)).
Every Source's polls would wait behind one poll's job, which, at the 10
seconds each company's Source starts at ([0010]), has expired by then
([JOB-2]), so the fetcher acknowledges it without a request ([JOB-5]).
With JetStream's default, the replica that takes the Lease over takes the
next job at once, and the earlier one when it's delivered again.

A fetch can take longer than those 30 seconds: up to 4 attempts of up to 60
seconds each, with a wait of up to 60 seconds before each retry ([FETCH-5],
[FETCH-8]). So while it works on a job, the fetcher tells JetStream the job
is in progress, which starts the 30 seconds again, and JetStream doesn't
deliver the job again while the fetcher is still at work on it. A job that
a lost replica held is delivered again 30 seconds after the last such word.

The Job makes `JOBS` last, once the release's resources are ready
([NATS-5], [CHART-4]), so a new install's fetcher starts before its
consumers exist, and waits for them. Until then no job can be queued, since
JetStream refuses one published before `JOBS` exists, and once one is,
`RECORDS`, which the Job makes earlier, takes its event.

- **FETCHER-8** (invariant) The umbrella chart gives the fetcher NATS's
  address, `nats://nats:4222`, as it gives the scheduler ([SCHED-7]). In
  this version it gives it no credential for NATS, which asks none.
- **FETCHER-9** (invariant) The umbrella chart's values list, on `JOBS`,
  the fetcher's three durable consumers, each with one filter subject:
  `fetcher-polls`, of `edgar.jobs.poll`; `fetcher-filings`, of
  `edgar.jobs.filing`; and `fetcher-backfills`, of `edgar.jobs.backfill`.
  Each has JetStream's default for its most unacknowledged messages, and is
  configured as [NATS-5] configures each consumer the values list: a pull
  consumer with `ack_policy: explicit`, `deliver_policy: all`,
  `max_deliver: -1`, and three replicas. They're the only consumers the
  values list on `JOBS`.
- **FETCHER-10** (rule) Only the fetcher's active replica, the one that
  holds the Lease `fetcher`, reads the fetcher's consumers; no other
  replica reads them. It asks `fetcher-polls` for a job first, then
  `fetcher-filings`, then `fetcher-backfills`, as [JOB-4] orders the jobs.
  While it works on a job it took, it tells JetStream that the job is
  in progress before the consumer's 30 seconds pass, so JetStream doesn't
  deliver again a job the fetcher is still at work on, however long its
  fetch takes.
- **FETCHER-11** (rule) The fetcher creates, changes, and deletes no
  stream and no consumer, which [NATS-3]'s Job alone makes, from the
  values. Until a consumer it reads exists, it waits for it, and reads from
  it once it does.

#### The fetcher and the fake EDGAR

Where CI installs the fake EDGAR, it answers for the SEC's hosts with a
certificate that the release's own authority issues ([FAKE-3]), which no
image's roots hold. So the chart gives the fetcher that authority's
certificate there, and the fetcher checks the certificates of the SEC's
hosts against it alone, in place of the roots its image holds. A request
that reached the SEC from such a cluster, as one would if kind's DNS didn't
answer for the SEC's names ([FAKE-4]), would then fail at its TLS
handshake, before its headers, the user agent among them, were sent.
Without the fake EDGAR, which the owner's installations never include
([FAKE-1]), the fetcher checks the SEC's certificates against its image's
roots.

- **FETCHER-12** (invariant) With `fakeEdgar.enabled`, the fetcher's pod
  mounts the certificate of the authority that issues the fake EDGAR's
  certificate ([FAKE-3]) alone, `ca.crt`, from the Secret cert-manager
  keeps it in, without the authority's key, and the umbrella chart gives
  the fetcher that certificate, to check the SEC's hosts against. Without
  `fakeEdgar.enabled`, the pod mounts nothing of the authority's, and the
  chart gives the fetcher no certificate to check them against.
- **FETCHER-13** (rule) Given a certificate to check the SEC's hosts
  against, the fetcher accepts a certificate for those hosts only if it
  chains to that certificate, whatever the roots its image holds; given
  none, only if it chains to one of its image's roots. It sends no request
  over a connection whose certificate it doesn't accept.

### The scheduler

The scheduler is the controller that reconciles the controls
([`controls.md`](controls.md), [0007]). It runs as two replicas, of which
one at a time acts: the one that holds its Lease, through
controller-runtime's leader election ([0006], [0007]). So each Source's
polls are queued once a cadence ([SOURCE-2]), not once by each replica. The
other waits on another worker node, to take the Lease over when the first
is lost. The scheduler uses the API server, for the controls and its Lease,
and NATS, for its jobs, orders, notices, and poll events. It never fetches
and writes nothing to the record ([0007]), so it has neither a Garage key
nor a database role.

- **SCHED-1** (invariant) The umbrella chart renders, in the release
  namespace, a Deployment `scheduler` of two replicas, whose pods run as
  the ServiceAccount `scheduler`, which the chart also renders. Its
  container runs the scheduler's image, one of the pipeline's own, from
  the value `scheduler.image`, `edgar/scheduler` by default ([IMG-1]), and
  names the port it serves its metrics on `metrics` ([METRIC-1]). It claims
  no volume. In this version it mounts no Secret and reads none in its
  environment, so it holds no credential but its ServiceAccount's token.
- **SCHED-2** (invariant) The scheduler's pods each need a worker node of
  their own: they carry, against each other, a required pod anti-affinity,
  or a topology spread constraint with `whenUnsatisfiable: DoNotSchedule`,
  on `kubernetes.io/hostname`, as NATS's and Garage's do ([PLACE-1]).

#### Its Lease

The scheduler's Lease is its own, apart from the fetcher's ([LEASE-1]).
Its timings by default are the fetcher's, which are those of
controller-runtime's leader election. That leader election is client-go's
([0006]), so the chart refuses the timings client-go refuses, as it does
the fetcher's.

controller-runtime runs a controller's work only on the replica that holds
the Lease, and stops it there when the replica can't renew the Lease. A
replica that Kubernetes stops while it holds the Lease, as in an upgrade or
a drain, would otherwise keep it until its duration passed without a
renewal, 15 seconds by default, before another could take it over. At the
10 seconds each company's Source starts at ([0010]), a poll queued that
late can leave a gap in the Source's coverage
([§5 of the notices](notices.md#5-polls-coverage-and-gaps)), at each
upgrade that restarts the scheduler. So a replica that Kubernetes stops
releases the Lease once it has stopped acting, and another replica takes it
over at its next try, and queues each Source's first poll then
([SOURCE-2]). That replica is the other, or the one that replaces the
stopped replica, whichever tries first. A replica can release the Lease
only if it stops acting within its pod's termination grace period. One
that doesn't is killed first, and one that crashes, or is lost without
stopping, as when its node is, still holds the Lease until its duration
passes.

- **SCHED-3** (invariant) The umbrella chart gives the scheduler its
  Lease's name, `scheduler`, in the release namespace, and its timings,
  from the values `scheduler.lease.durationSeconds`, 15 by default,
  `scheduler.lease.renewDeadlineSeconds`, 10, and
  `scheduler.lease.retryPeriodSeconds`, 2. It fails to render values where
  any of the three is below 1, where the renewal deadline isn't shorter
  than the duration, where the retry period isn't shorter than the renewal
  deadline, or where the renewal deadline isn't greater than 1.2 times the
  retry period, so it refuses what client-go's leader election refuses
  ([0006]).
- **SCHED-4** (rule) Only the scheduler's active replica, the one that
  holds the Lease `scheduler`, queues polls, publishes orders, reads the
  scheduler's consumers, and sets the resources' `status`; no other
  replica does any of these. A replica stops acting when it can't renew
  the Lease within the renewal deadline, before another can take the Lease
  over. A replica that Kubernetes stops while it holds the Lease, and that
  stops acting within its pod's termination grace period, releases the
  Lease once it has stopped acting, so another replica takes it over
  without waiting out the Lease's duration.
- **SCHED-5** (rule) In the installed charts, the scheduler's two pods run
  on two worker nodes, and one of them holds the Lease `scheduler` in the
  release namespace, with `leaseDurationSeconds` as the values give it.
  When that pod is deleted, a scheduler pod other than the deleted one
  holds the Lease before the Lease's duration has passed since its last
  renewal.

#### Its grants

The scheduler's grants on the controls are [RBAC-1]'s. It reconciles only
the Sources and Backfills in the release namespace ([SOURCE-1],
[BACKFILL-1], [ORDER-1]), so a Role there grants its verbs on them, and
the scheduler watches them there alone. The SecAccess is cluster-scoped
([§4 of the controls](controls.md#4-the-resources)), so a ClusterRole
grants its verbs on it. Its Lease needs what the fetcher's does
([LEASE-1]): to create it, and then to read and renew it. It's granted
nothing else, so it records no Kubernetes Events: what it did with the
controls is in their `status` ([0007]), and whether a replica is the
active one is in its metrics ([METRIC-3]) and in the Lease.

- **SCHED-6** (invariant) The umbrella chart grants the scheduler's
  ServiceAccount [RBAC-1]'s verbs: those on Sources, Backfills, and their
  `status` by a Role in the release namespace, and those on the SecAccess
  by a ClusterRole. It grants it, in the release namespace, `create` on
  Leases, and `get` and `update` on the Lease `scheduler` only. No
  RoleBinding or ClusterRoleBinding the charts render binds the
  ServiceAccount to any other role.

#### Its access to NATS

NATS asks no credential in this version: any pod that reaches its Service
may publish to any subject and read from any consumer
([§9](#9-later-versions)). So the scheduler's access to NATS is NATS's
address, and the streams and consumers the Job makes for what it
publishes and reads. It publishes jobs on `edgar.jobs.poll`, which `JOBS`
takes ([JOB-2]), and orders on `edgar.orders.backfill`, which `ORDERS`
takes ([ORDER-1]). It reads gap and backfill notices through
`scheduler-notices` ([NATS-5], [NOTICE-1], [NOTICE-2]), and poll events
through `scheduler-polls` ([SCHED-8]).

The scheduler sets each Source's `lastPollTime` and
`lastSuccessfulPollTime` from the records of its polls ([SOURCE-4]). It
reads them in their events, as it reads what the other services publish
([0007]), and not in the database, where it has no role. `RECORDS` is kept
by interest, so the scheduler's consumer must exist before the fetcher
publishes the first poll event it should see, as the discoverer's must
([NATS-5]). The consumer comes with the scheduler, which is built before
the fetcher ([plan](../docs/plan.md)). An event is published again when
its job is delivered again ([JOB-6]), after events of later polls, so
handling the events one at a time, in the stream's order, wouldn't put the
polls in order. The consumer delivers as many unacknowledged events at once
as JetStream's default allows, as `scheduler-notices` does. While no replica reads it, as while the
scheduler is scaled to zero, `RECORDS` keeps each poll event for it until
it does.

The Job makes the streams and consumers only once the release's resources
are ready ([CHART-4]), so a new install's scheduler starts before they
exist. JetStream refuses a job or an order published before its stream
exists, and the publisher knows it wasn't kept
([durable consumers](#durable-consumers)): a poll that can't be queued
before the next is due is skipped ([SOURCE-2]). The notices' version 0.1.0
doesn't say that the scheduler publishes a refused order again, and
[ORDER-1] publishes a Backfill's order again only when the scheduler
becomes the active replica or requests are allowed again. The chart's
default values pause the SecAccess ([VAL-4]), so a new install with them
publishes no order before the Job has made `ORDERS`. A new install whose
values allow requests from the start can have an order refused, and its
Backfill then waits until the scheduler next becomes the active replica.

- **SCHED-7** (invariant) The umbrella chart gives the scheduler NATS's
  address, `nats://nats:4222`: the client port of the Service `nats` in the
  release namespace. In this version it gives it no credential for NATS,
  which asks none.
- **SCHED-8** (invariant) The umbrella chart's values list, on `RECORDS`,
  the scheduler's durable consumer `scheduler-polls`, with the filter
  subject `edgar.records.poll` and JetStream's default for its most
  unacknowledged messages. It's configured as [NATS-5] configures each
  consumer the values list: a pull consumer with `ack_policy: explicit`,
  `deliver_policy: all`, `max_deliver: -1`, and three replicas. So
  [NATS-3]'s Job makes it with `RECORDS`'s other consumers, before
  `RECORDS` takes its subjects.
- **SCHED-9** (rule) The scheduler creates, changes, and deletes no stream
  and no consumer, which [NATS-3]'s Job alone makes, from the values. Until
  a consumer it reads exists, it waits for it, and reads from it once it
  does.

How the chart gives the scheduler its Lease's name and timings, NATS's
address, and the release namespace, as arguments or in its environment, is
for the chart and the scheduler to agree.

### The transfer

The owner runs the transfer outside the cluster, where `data/` is, to copy
records and their objects from the cluster store into it ([XFER-1]). It
isn't a service, so it holds no service's credentials: it reads the store
as a database role and with a Garage key of its own, each of which may only
read it ([LOG-5]). So a mistake in the transfer can't change the cluster
store, and the services' credentials never leave the cluster.

Nothing the charts render exposes the database or Garage outside the
cluster ([TRANSFER-2]), so the transfer reaches them through the Kubernetes
API server, with the context the owner names from their kubeconfig.
Through it, the transfer reads its own credentials from the Secrets the
umbrella chart renders them into, and opens a port-forward to each store,
as `kubectl port-forward` does. So it needs nothing of the cluster that the
owner's context can't already do, and it works the same on kind as on any
other cluster the charts are installed on. It reads the database's
primary, where the fetcher writes each record ([FETCHER-7]), rather than
its replicas, which Grafana reads ([MON-5]). The replicas follow the primary
asynchronously, so one can lag behind it, and a range read there could
miss records the primary holds, which [XFER-1] copies. The primary holds
each record once its transaction commits.

A context can't tell a test cluster from the owner's: kind names each
cluster made from `deploy/kind.yaml` `edgar`, and its context `kind-edgar`,
unless it's given another name. A test cluster's records are of fetches
the fake EDGAR answered, not the SEC, and `data/` would keep them for good
([LOG-1]). So the transfer copies nothing from a cluster that holds the
fake EDGAR ([TRANSFER-6]). It reads the range there all the same, so the
tests on kind, whose cluster holds the fake EDGAR, check its access to the
cluster store.

A port-forward carries the transfer's connection through the API server to
the pod's node, and from there into the pod. What the transfer reads
becomes records that `data/` keeps for good ([LOG-1]), so it authenticates
the database itself. It can't check the server's certificate against the
`Cluster`'s certificate authority, as the fetcher does ([FETCHER-7]),
without reading the Secret that holds the authority's certificate, which
holds the authority's key too, since the API server returns a Secret whole.
It requires SCRAM's channel binding instead, with which only a server that
holds the role's password verifier completes the connection. Garage's S3
API speaks plain HTTP through the port-forward, as it does in the cluster:
a request carries a signature made with the key's secret, never the secret
itself ([FETCHER-6]). Each object is named by its body's SHA-256
([OBJ-1]), which `verify.py` checks of every object the transfer places in
`data/`.

How the transfer learns the `Cluster`'s name, the Secrets' names, the
bucket, and the region is for the chart and the transfer to agree, as it is
for the fetcher.

- **TRANSFER-1** (invariant) The umbrella chart renders the password of the
  database role `transfer` ([PG-15]) and the ID and secret of the Garage
  key `transfer` ([GARAGE-5]) each into a Secret in the release namespace.
  No service's container reads either, in its environment or from a volume.
- **TRANSFER-2** (invariant) The charts expose neither the database nor
  Garage outside the cluster. Every Service the charts render whose
  selector would match the database's pods, which CloudNativePG labels
  with the `Cluster`'s name in `cnpg.io/cluster`, or Garage's pods is of
  type `ClusterIP`, and so is every Service they ask CloudNativePG to make:
  the `Cluster` asks for none but those CloudNativePG makes for every
  `Cluster`, which are of that type, and any `Pooler` the charts render
  asks for one of that type. No Ingress the charts render routes to
  either, and `deploy/kind.yaml` maps no port of a node to the host.
- **TRANSFER-3** (rule) The transfer acts only on the cluster of the
  Kubernetes context the owner names when they run it, in the release
  namespace, `edgar` ([§5](#5-dependencies)). Without a context named, it
  reads nothing, rather than the cluster the kubeconfig's current context
  names.
- **TRANSFER-4** (rule) The transfer reaches that context's API server
  with the owner's credentials for it, from their kubeconfig
  ([TRANSFER-3]). Through it, the transfer reads its role's password and
  its key's ID and secret from [TRANSFER-1]'s Secrets, and reads no other
  Secret. It takes none of the three from its environment, from a file such
  as `~/.pgpass` or `~/.aws/credentials`, or from the chart's values, and
  writes none of them anywhere: not to `data/`, its output, or a log
  ([SECRET-4]).
- **TRANSFER-5** (rule) The transfer reaches the database and Garage's S3
  API only through port-forwards that it opens through that context's API
  server, as `kubectl port-forward` does, and closes before it exits: to
  port 5432 of the Service that CloudNativePG makes for the `Cluster`'s
  primary, the `Cluster`'s name followed by `-rw`, and to the S3 port,
  3900, of the Service `garage`. It reads the database `edgar` as the role
  `transfer` ([PG-15]), over TLS and with SCRAM's channel binding, as
  libpq's `sslmode=require` and `channel_binding=require` ask, and the
  bucket `snapshots` with the key `transfer` ([GARAGE-5]), in the region
  that the chart's values give Garage's S3 API.
- **TRANSFER-6** (rule) The transfer copies nothing from a cluster whose
  release namespace holds the fake EDGAR's Deployment, `fake-edgar`
  ([FAKE-2]), which CI installs and the owner's installations don't
  ([FAKE-1], [FAKE-4]), and says that the fake EDGAR is installed there. It
  looks for the Deployment through that context's API server. It reads the
  range there all the same, through [TRANSFER-5]'s port-forwards, but
  writes nothing to `data/`.

### The fake EDGAR

The tests on kind fetch from the fake EDGAR, so they never reach the SEC
([0001], [`fake-edgar.md`](fake-edgar.md)). CI installs it with the
umbrella chart, and the owner's installations don't. It answers for the
SEC's hosts over HTTPS, so the fetcher, which fetches only `https` URLs on
those hosts ([FETCH-1]), sends it the requests it would send the SEC. CI
points the cluster's DNS at it, so every pod that asks for those hosts
reaches it instead. Its certificate comes from a certificate authority that
the release makes for it through cert-manager, which the second release
installs. A client in the cluster checks it against the authority's
certificate alone, `ca.crt`, as Grafana checks the database's ([MON-5]).
Where CI installs the fake EDGAR, the fetcher trusts that authority alone
([FETCHER-12], [FETCHER-13]).

- **FAKE-1** (invariant) The umbrella chart renders the fake EDGAR, and
  what [FAKE-2] and [FAKE-3] give it, only when its value
  `fakeEdgar.enabled` is `true`. Its default values set it to `false`, and
  no values file committed to the repository sets it to `true`.
- **FAKE-2** (invariant) With `fakeEdgar.enabled`, the umbrella chart
  renders, in the release namespace, a Deployment `fake-edgar` of one
  replica. Its container runs the fake EDGAR's image, one of the
  pipeline's own ([IMG-1]), with the certificate and key of [FAKE-3]
  mounted from their Secret for its server ([SERVE-5]). It claims no
  volume, since its image holds what it serves ([IMG-5]), and mounts no
  ServiceAccount token, since it uses no API. The chart also renders a
  Service `fake-edgar`, whose port 443 reaches the fake EDGAR's server, and
  port 8080 its admin port ([ADMIN-1]).
- **FAKE-3** (invariant) With `fakeEdgar.enabled`, the umbrella chart
  renders, in the release namespace, cert-manager's resources for a
  certificate authority of the release's own: a self-signed issuer; the
  authority's certificate, which that issuer issues; an issuer of the
  authority; and the fake EDGAR's certificate, which the authority's
  issuer issues for the names `www.sec.gov` and `data.sec.gov`. cert-manager
  generates each key in the cluster and keeps it in a Secret, so no value
  holds one ([SECRET-1]).
- **FAKE-4** (rule) CI installs the umbrella chart with `fakeEdgar.enabled`
  set to `true`, after building and loading the pipeline's images with
  [IMG-2]'s command. Before it installs the umbrella chart, it points
  kind's cluster DNS at the fake EDGAR: CoreDNS, which kind runs, answers a
  pod's query for `www.sec.gov` or `data.sec.gov` with the address of the
  Service `fake-edgar` in the release namespace. So no pod of the
  installed charts reaches the SEC.
- **FAKE-5** (rule) In the installed charts, the fake EDGAR's pod is ready.
  A pod's `GET` for a stored URL, sent to that URL, reaches it over HTTPS,
  with a certificate for the URL's host that the release's authority
  issued, and gets the version that `data/` holds at the commit CI
  installs ([SERVE-3]).

## 6. Pins

Which version each pin names is for the charts, the kind configuration, and
CI to say, not this document. An upgrade changes one of them and is reviewed
there, without a new version of this document. The exceptions are
PostgreSQL's major version ([PG-1]) and Helm's ([PIN-7]).

- **PIN-1** (invariant) Each dependency in each chart's `Chart.yaml` names
  one exact version: a semantic version, with or without a leading `v`,
  without a range, a wildcard, or a constraint. The chart's `Chart.lock`
  lists the same dependencies, with the same versions and repositories.
- **PIN-2** (invariant) Each dependency from a chart repository is
  committed as the packaged chart `helm dependency update` downloads, at
  `deploy/CHART/charts/NAME-VERSION.tgz` in the chart that depends on it,
  so `helm template` renders each chart without the network.
- **PIN-3** (invariant) Garage's chart, which no chart repository publishes
  ([0002]), is committed as the directory `deploy/chart/charts/garage/`,
  unchanged from `script/helm/garage` in Garage's source tree at a release
  tag. The umbrella chart's dependency on it names the version in that
  directory's `Chart.yaml`, and the commit that adds or upgrades it names
  the tag.
- **PIN-4** (invariant) Every third-party image the charts render, as the
  value of a field named `image` anywhere in a rendered manifest, whether
  in a container or in a custom resource, is named as
  `REPOSITORY:TAG@sha256:DIGEST`, where `DIGEST` is 64 lower-case
  hexadecimal digits: the tag for readers, and the digest for the runtime.
  An image an operator chooses without such a field is held to [PIN-5].
- **PIN-5** (rule) In every pod of the installed charts, each container and
  init container that runs a third-party image names it by digest, as
  [PIN-4] has it. The charts' pods are those of the resources they render,
  and those their operators create for them, such as the database's
  instances, in whichever namespace they run. Kind's own pods, such as its
  control plane, CoreDNS, kindnet, and its storage provisioner, aren't the
  charts': their images come with the node image that [PIN-6] pins.
- **PIN-6** (invariant) `deploy/kind.yaml` names every node's image as
  `kindest/node:TAG@sha256:DIGEST`, which also pins the cluster's Kubernetes
  version.
- **PIN-7** (invariant) CI installs Helm and kind at exact versions, and
  Helm at major version 4.

### The pipeline's own images

An image built from this repository has no registry, and so no digest. Each
is built from a commit, tagged with it, and loaded into kind's nodes, as the
owner decided ([§9](#9-later-versions)).

- **IMG-1** (invariant) Every image the umbrella chart renders is either
  third-party, named as [PIN-4] has it, or the pipeline's own, named as
  `REPOSITORY:COMMIT` without a digest, where `COMMIT` is the value
  `images.commit`. Every container that runs one of the pipeline's own has
  `imagePullPolicy: Never`. The chart's `values.schema.json` requires
  `images.commit`, 40 lower-case hexadecimal digits, which its default
  values don't set.
- **IMG-2** (rule) One command builds the pipeline's images. It refuses a
  working tree with uncommitted changes, tags each image with the
  40-character hash of the commit checked out, and loads it into each node
  of the kind cluster with `kind load docker-image`. No image of the
  pipeline's own is pushed to, or pulled from, a registry.
- **IMG-3** (rule) In the installed charts, each container that runs one of
  the pipeline's images runs the image built from the commit CI installs
  the charts from.
- **IMG-4** (rule) The owner installs and upgrades the umbrella chart with
  `images.commit` set to the commit checked out, after building and loading
  its images with [IMG-2]'s command.
- **IMG-5** (rule) [IMG-2]'s command builds each image from the files of
  the commit it tags, as that commit has them, and from base images named
  by digest, as [PIN-4] names a third-party image, so the same commit
  builds the same software. The fake EDGAR's image holds `data/` as that
  commit has it, which the fake EDGAR serves ([SERVE-1]).

## 7. PostgreSQL

The pipeline's record and facts are kept in PostgreSQL 18 ([0004]). Which
image of 18 the catalog names, its minor version, its type, and its
operating system, is the chart's to say, like any other pin.

- **PG-1** (invariant) The charts render one CloudNativePG `ImageCatalog`,
  in the umbrella chart. Its only entry has `major: 18` and names an image
  from `ghcr.io/cloudnative-pg/postgresql` whose tag begins with `18.`, by
  digest ([PIN-4]). Every `Cluster` names its image through that catalog,
  with `major: 18`, and sets no `imageName`.
- **PG-2** (rule) Every instance of every `Cluster` in the installed charts
  reports a `server_version_num` from 180000 to 189999: PostgreSQL 18.

A new minor version changes only the catalog's image. A new major version
takes a decision record that supersedes [0004], and a major version of this
document.

### Instances and backups

The database's archive, its WAL and its base backups, is kept in Garage
under a server name. Before a new cluster's first archive, the Barman Cloud
plugin checks that the archive under that name is empty, so each cluster
has a server name of its own: a cluster recreated with the same host
directory, where Garage kept the earlier cluster's archive, archives into
an empty destination, and the earlier archive stays as it was.

- **PG-3** (invariant) The umbrella chart renders one CloudNativePG
  `Cluster`, with three instances ([0004]), its volumes on kind's `standard`
  class ([VOL-2]).
- **PG-4** (invariant) The `Cluster` archives its WAL and takes base
  backups through the Barman Cloud plugin, as its WAL archiver. They go to
  one `ObjectStore`, which the umbrella chart renders, whose destination
  is Garage's `database-backups` bucket, reached with that bucket's key
  from its Secret ([GARAGE-2]). They're kept there under the server name
  that the `Cluster` gives the plugin as its `serverName` parameter: the
  umbrella chart's value `database.backups.serverName`, which its
  `values.schema.json` requires and none of its committed values files
  sets ([PG-10]). A `ScheduledBackup` with `method: plugin` takes a base
  backup of the `Cluster` at least once a day ([0002], [0004]).
- **PG-5** (rule) In the installed charts, the `Cluster` reports that
  continuous archiving to Garage works, and a base backup to Garage
  completes.
- **PG-10** (rule) CI sets a new `database.backups.serverName` for each
  cluster it creates. The owner sets a new one for each cluster they
  create, recreating one with the same host directory included, and keeps
  it for that cluster's upgrades.

The backup plugin has been tested against MinIO, not Garage, so a restore
from these backups is shown on kind before they're relied on ([0002]), by
the drills in the [plan](../docs/plan.md).

### The store's database

The cluster store's records are a table in the pipeline's database
([STORE-1], [STORE-2]). Its owner creates the table and grants each
service's role what [LOG-3] allows, and the transfer's what [LOG-5]
allows, and neither a service nor the transfer connects as the owner, who
could change a record.

- **PG-6** (invariant) The `Cluster` creates one database, `edgar`, the
  pipeline's database, owned by the role `edgar`, whose password
  CloudNativePG generates in the cluster ([SECRET-1]). It sets
  `enableSuperuserAccess: false`. No service connects as `edgar`; only
  [PG-8]'s Job does.
- **PG-7** (invariant) Each service that uses the database connects as a
  role of its own, which the `Cluster` declares in `managed.roles`, able
  to log in, and without superuser, `createdb`, `createrole`,
  `replication`, or `bypassrls`. Its password comes from a Secret the
  umbrella chart renders from a required value ([SECRET-1]). In this
  version, the values list two, `fetcher` and `discoverer`
  ([`notices.md`, §12](notices.md#left-to-other-specifications)); each
  other service's comes with that service ([§9](#9-later-versions)).
- **PG-8** (invariant) The umbrella chart renders a Job, a Helm hook run
  after each install and upgrade, that connects to `edgar` as its owner and:
  - creates the table `records` and its index from
    `deploy/chart/files/records.sql`, unless the table exists, applying
    the file in one transaction, so the table never exists without its
    index;
  - grants the fetcher's role `INSERT` and `SELECT` on `records`, each
    other service's role `SELECT` only ([LOG-3]), the transfer's role
    `SELECT` only ([LOG-5]), and no other role any privilege on it, but its
    owner.

  `deploy/chart/files/records.sql` is a copy of
  [`spec/schemas/records.sql`](schemas/records.sql), since Helm reads only
  files inside the chart. The Job waits until the database accepts its
  connection and each role exists, and succeeds when what it would create
  already exists.
- **PG-9** (rule) In the installed charts, `edgar` holds the table
  `records` as `records.sql` defines it ([STORE-2]). The fetcher's role can
  insert a row and select it; every other service's role can select it;
  and no service's role can update, delete, or truncate a row, nor any but
  the fetcher's insert one.
- **PG-15** (invariant) The transfer reads `edgar` as a role of its own,
  `transfer`, which the `Cluster` declares in `managed.roles`, able to log
  in, and without superuser, `createdb`, `createrole`, `replication`, or
  `bypassrls`. Its password comes from a Secret the umbrella chart renders
  from a required value ([SECRET-1], [TRANSFER-1]). [PG-8]'s Job grants it
  `SELECT` on `records` ([LOG-5]). It's not a service's role, so [PG-12]
  and [PG-13] grant it nothing on the notices' tables and views or on
  `dashboards`.

### The notices and the dashboards

The discoverer keeps what it finds in the notices' tables, beside
`records`, whose rows they reference
([`notices.md`](notices.md#4-the-record-of-notices)), and Grafana reads the
record through the views of `dashboards.sql`, which read the notices'
([`telemetry.md`](telemetry.md#8-the-views-grafana-reads)). So [PG-8]'s Job
makes each after what it reads, and grants what [TABLE-3] and [VIEW-3]
allow. The views read the tables as their owner does, since none sets
`security_invoker`, so a role granted a view needs nothing on the tables
beneath it. A function a view calls, such as `notices.sql`'s
`header_instant`, is executed as the role that reads the view, so each
keeps PostgreSQL's default, which lets every role execute it.

- **PG-11** (invariant) Grafana reads `edgar` as a role of its own,
  `grafana`, which the `Cluster` declares in `managed.roles`, able to log
  in, and without superuser, `createdb`, `createrole`, `replication`, or
  `bypassrls`. Its password comes from a Secret the umbrella chart renders
  from a required value ([SECRET-1]), from which Grafana's data source
  reads it too ([MON-5]). It's not a service's role, so [PG-8] grants it
  nothing on `records`.
- **PG-12** (invariant) [PG-8]'s Job, once `records` exists, also:
  - creates the notices' tables and views, and the functions they call,
    from `deploy/chart/files/notices.sql`, unless its tables exist,
    applying the file in one transaction, so none exists without the
    others;
  - grants the discoverer's role `INSERT` and `SELECT` on each of the
    notices' tables, each other service's role `SELECT` only ([TABLE-3]),
    and each service's role `SELECT` on each of the notices' views, and
    no other role any privilege on them, but their owner.

  `deploy/chart/files/notices.sql` is a copy of
  [`spec/schemas/notices.sql`](schemas/notices.sql). The Job revokes no
  privilege PostgreSQL gives every role on the functions.
- **PG-13** (invariant) [PG-8]'s Job, once the notices' tables and views
  exist, also:
  - creates the schema `dashboards` and its views from
    `deploy/chart/files/dashboards.sql`, unless the schema exists, applying
    the file in one transaction;
  - grants Grafana's role `USAGE` on the schema `dashboards` and `SELECT`
    on each of its views ([VIEW-3]), and no other role any privilege on
    the schema or its views, but their owner.

  `deploy/chart/files/dashboards.sql` is a copy of
  [`spec/schemas/dashboards.sql`](schemas/dashboards.sql). The Job waits
  until Grafana's role exists too.
- **PG-14** (rule) In the installed charts, `edgar` holds the notices'
  tables and views as `notices.sql` defines them, and the schema
  `dashboards` with its views as `dashboards.sql` defines them ([TABLE-2],
  [VIEW-2]). The discoverer's role can insert a row in each of the notices'
  tables and select it; every other service's role can select it; every
  service's role can select each of the notices' views; no service's role
  can update, delete, or truncate a row, nor any but the discoverer's
  insert one; and Grafana's role can select each view in `dashboards`, and
  nothing `records.sql` or `notices.sql` defines ([TABLE-3], [VIEW-3]).

## 8. Index

| ID | Kind | Checked by | Statement |
| --- | --- | --- | --- |
| CHART-1 | invariant | tests | The four charts are at their paths under `deploy/`, and kind's configuration at `deploy/kind.yaml`. |
| CHART-2 | invariant | tests | Each chart's dependencies are the table's, from its repositories. |
| CHART-3 | invariant | tests | No chart or image comes from Bitnami. |
| CHART-4 | rule | kind, operator | The four releases are installed and upgraded in order, each once the one before it is ready. |
| PIN-1 | invariant | tests | Each dependency names an exact version, which `Chart.lock` repeats. |
| PIN-2 | invariant | tests | Each dependency from a repository is committed, packaged. |
| PIN-3 | invariant | tests, review | Garage's chart is committed unchanged from a release tag. |
| PIN-4 | invariant | tests | Each third-party image the charts render is named by tag and digest. |
| PIN-5 | rule | kind | Each third-party image in the installed charts' pods is named by digest. |
| PIN-6 | invariant | tests | Each kind node's image is named by digest. |
| PIN-7 | invariant | tests | CI installs Helm and kind at exact versions, Helm at major version 4. |
| PG-1 | invariant | tests | One image catalog, of major version 18, names the database's image. |
| PG-2 | rule | kind | Every database instance reports PostgreSQL 18. |
| PG-3 | invariant | tests | One database `Cluster`, of three instances, on kind's `standard` class. |
| PG-4 | invariant | tests | The database archives its WAL and backs up daily to Garage, through the plugin, under a server name from a required value. |
| PG-5 | rule | kind | The database's archiving works, and a backup completes. |
| PG-6 | invariant | tests | One database, `edgar`, whose owner no service uses, and no superuser access. |
| PG-7 | invariant | tests | Each service has a database role of its own, without special attributes, its password from a value. |
| PG-8 | invariant | tests | A hook Job creates the table from a copy of `records.sql`, in one transaction, and grants only the fetcher's role inserts. |
| PG-9 | rule | kind | The table is as `records.sql` defines it; only the fetcher's role inserts, and no service's role changes a row. |
| PG-10 | rule | review, operator | CI and the owner set a new archive server name for each cluster they create. |
| PG-11 | invariant | tests | Grafana has a database role of its own, `grafana`, without special attributes, its password from a value. |
| PG-12 | invariant | tests | The hook Job creates the notices' tables and views from a copy of `notices.sql`, in one transaction, and grants only the discoverer's role inserts. |
| PG-13 | invariant | tests | The hook Job creates the schema `dashboards` from a copy of `dashboards.sql`, in one transaction, and grants only Grafana's role its views. |
| PG-14 | rule | kind | The notices' tables and the dashboards' views are as their files define them; only the discoverer's role inserts, every service's role reads the notices' views, no service's role changes a row, and Grafana's role reads only the dashboards' views. |
| PG-15 | invariant | tests | The transfer has a database role of its own, `transfer`, without special attributes, its password from a value, granted only `SELECT` on `records`. |
| CRD-1 | invariant | tests | Every definition is a template, none from a `crds/` directory. |
| CRD-2 | invariant | tests | Each definition is rendered by the chart the table gives it; the umbrella chart renders none. |
| CRD-3 | invariant | tests | Each resource's definition, and any webhook that fails closed on it, comes from an earlier chart. |
| CRD-4 | invariant | tests | Every definition is kept when a release is uninstalled. |
| CRD-5 | invariant | tests, review | The Prometheus operator's and Garage's definitions match the versions that use them. |
| CRD-6 | rule | kind | Every definition on the cluster belongs to one of the four releases. |
| IMG-1 | invariant | tests | The pipeline's images are tagged with `images.commit`, and never pulled. |
| IMG-2 | rule | tests | One command builds the images from a clean commit, tags them with it, and loads them into kind. |
| IMG-3 | rule | kind | The installed charts run the images built from the commit CI installs. |
| IMG-4 | rule | operator | The owner installs the umbrella chart with the commit checked out, after building its images. |
| IMG-5 | rule | tests | Each image is built from its commit's files and from base images named by digest; the fake EDGAR's holds `data/`. |
| KIND-1 | invariant | tests | kind has one control-plane node and three workers. |
| KIND-2 | invariant | tests | Every worker node keeps its volumes in one host directory. |
| VOL-1 | invariant | tests | The `edgar-retained` class keeps a volume's data, at a path named by its claim. |
| VOL-2 | invariant | tests | NATS, Garage, Prometheus, and Alertmanager claim `edgar-retained`; the database claims `standard`. |
| VOL-3 | rule | kind | A new claim of the same namespace and name finds a deleted claim's data. |
| PLACE-1 | invariant | tests | NATS's, Garage's, and the database's replicas each need a worker node of their own. |
| PLACE-2 | rule | kind | Their replicas run one on each worker node. |
| NATS-1 | invariant | tests | NATS is a cluster of three servers, with JetStream on claims. |
| NATS-2 | rule | kind | The three servers form one JetStream cluster, with a leader. |
| NATS-3 | invariant | tests | A hook Job creates or updates the streams `JOBS`, `RECORDS`, `ORDERS`, and `NOTICES`, each in files with three replicas. |
| NATS-4 | rule | kind | Each stream is as the values say, on all three servers, and the Job can run again. |
| NATS-5 | invariant | tests | The Job creates or updates the durable consumers the values list, each right after its stream and before a stream kept by interest takes its subjects, with `NOTICES` first and `JOBS` last. |
| NATS-6 | rule | kind | Each consumer is as the values say, on all three servers, and the Job can run again; a stream kept by interest refuses a message, or keeps it for each consumer, while the Job makes it. |
| GARAGE-1 | invariant | tests | Garage is three nodes, replication factor 3, on claims, with no definition or ClusterRole of its own. |
| GARAGE-2 | invariant | tests | A hook Job sets the layout, keys, buckets, and grants, and can run again. |
| GARAGE-3 | rule | kind | Garage's layout, buckets, keys, and grants are as the values say. |
| GARAGE-4 | invariant | tests | The store's bucket is `snapshots`; only the fetcher's key may write it, and no key owns a bucket. |
| GARAGE-5 | invariant | tests | The transfer has a key of its own, `transfer`, which may only read `snapshots`, and no other bucket. |
| MON-1 | invariant | tests | Nothing kind doesn't expose is scraped. |
| MON-2 | invariant | tests | Grafana is read-only; its data sources come from its own value, which restarts it, and its sidecar provisions dashboards only. |
| MON-3 | invariant | tests | Alertmanager sends nothing outside the cluster. |
| MON-4 | invariant | tests | Prometheus, Alertmanager, and Grafana run one replica each. |
| MON-5 | invariant | tests | Grafana's PostgreSQL data source reads `edgar`'s replicas as `grafana`, its password from the role's Secret, verifying the server against the `Cluster`'s authority. |
| MON-6 | rule | kind | Grafana's PostgreSQL data source connects, verifying the server, and reads each view in `dashboards`. |
| SECRET-1 | invariant | tests | Each credential is a required value, set by no committed file, rendered into a Secret. |
| SECRET-2 | invariant | tests | The same values render the same manifests. |
| SECRET-3 | invariant | tests | A credential appears only in a Secret. |
| SECRET-4 | rule | review, operator | CI generates credentials per cluster, and the owner commits none. |
| LEASE-1 | invariant | tests | The fetcher's Lease, `fetcher`, takes its timings from values, 15, 10, and 2 seconds by default, and the chart refuses those client-go's leader election refuses. |
| LEASE-2 | rule | kind | One of the fetcher's pods holds the Lease, with the values' duration. |
| FETCHER-1 | invariant | tests | The fetcher is a Deployment of two replicas of its image, as its own ServiceAccount, with its port `metrics`, no claim, and the commit its image is tagged with. |
| FETCHER-2 | invariant | tests | The fetcher's pods each need a worker node of their own. |
| FETCHER-3 | rule | kind | The fetcher's pods are on two worker nodes; when the one that holds the Lease is deleted, another holds it, but not before its duration has passed. |
| FETCHER-4 | invariant | tests | The fetcher's ServiceAccount has RBAC-2's grants and its Lease's, and is bound to no other role. |
| FETCHER-5 | invariant | tests | The fetcher reads its Garage key, its role's password, and its user agent in its environment from their Secrets, which no other service reads; the user agent is a required value. |
| FETCHER-6 | invariant | tests | The fetcher is given the bucket `snapshots` and Garage's S3 API. |
| FETCHER-7 | invariant | tests | The fetcher connects to the database's primary as `fetcher`, verifying the server against the `Cluster`'s authority. |
| FETCHER-8 | invariant | tests | The fetcher is given NATS's address, and no NATS credential. |
| FETCHER-9 | invariant | tests | The values list the fetcher's three durable consumers on `JOBS`, one for each kind of job, as NATS-5 configures each consumer. |
| FETCHER-10 | rule | tests | Only the active fetcher reads its consumers, the polls' first, and a job it's at work on isn't delivered again. |
| FETCHER-11 | rule | tests | The fetcher makes no stream or consumer, and waits for those it reads. |
| FETCHER-12 | invariant | tests | Where CI installs the fake EDGAR, the fetcher's pod mounts that authority's certificate alone, and the fetcher is given it to check the SEC's hosts against. |
| FETCHER-13 | rule | tests | Given that certificate, the fetcher accepts only certificates for the SEC's hosts that chain to it; given none, only those that chain to its image's roots. |
| SCHED-1 | invariant | tests | The scheduler is a Deployment of two replicas of its image, as its own ServiceAccount, with its port `metrics`, and no claim or Secret. |
| SCHED-2 | invariant | tests | The scheduler's pods each need a worker node of their own. |
| SCHED-3 | invariant | tests | The scheduler's Lease, `scheduler`, takes its timings from values, 15, 10, and 2 seconds by default, and the chart refuses those client-go's leader election refuses. |
| SCHED-4 | rule | tests | Only the scheduler's replica that holds the Lease acts, and one that Kubernetes stops releases the Lease once it has stopped acting, within its grace period. |
| SCHED-5 | rule | kind | The scheduler's pods are on two worker nodes, one holds the Lease, and another pod holds it before its duration when that pod is deleted. |
| SCHED-6 | invariant | tests | The scheduler's ServiceAccount has RBAC-1's grants and its Lease's, and is bound to no other role. |
| SCHED-7 | invariant | tests | The scheduler is given NATS's address, and no NATS credential. |
| SCHED-8 | invariant | tests | The values list the scheduler's durable consumer of poll events, `scheduler-polls`, on `RECORDS`, as NATS-5 configures each consumer. |
| SCHED-9 | rule | tests | The scheduler makes no stream or consumer, and waits for those it reads. |
| TRANSFER-1 | invariant | tests | The transfer's role's password and key are each in a Secret that no service reads. |
| TRANSFER-2 | invariant | tests | Neither the database nor Garage is exposed outside the cluster: each Service that would select their pods is of type `ClusterIP`. |
| TRANSFER-3 | rule | tests | The transfer acts only on the cluster of the context the owner names, and on none without one. |
| TRANSFER-4 | rule | kind | The transfer reads its role's password and its key only from its Secrets, through the API server, and writes them nowhere. |
| TRANSFER-5 | rule | kind | The transfer reads the database's primary as `transfer`, with channel binding, and the bucket with its key, through port-forwards. |
| TRANSFER-6 | rule | kind | The transfer copies nothing from a cluster that holds the fake EDGAR, though it reads the range there. |
| FAKE-1 | invariant | tests | The umbrella chart renders the fake EDGAR only when `fakeEdgar.enabled`, which no committed values file sets. |
| FAKE-2 | invariant | tests | The fake EDGAR is one replica of its image, with its certificate's Secret mounted but no claim or token, behind a Service with ports 443 and 8080. |
| FAKE-3 | invariant | tests | A certificate authority of the release's own, through cert-manager, issues the fake EDGAR's certificate for the SEC's hosts. |
| FAKE-4 | rule | kind | CI installs the fake EDGAR, and points kind's DNS for the SEC's hosts at it first. |
| FAKE-5 | rule | kind | The fake EDGAR is ready, and answers a pod's request for a stored URL over HTTPS, as the SEC's host. |

## 9. Later versions

Later versions of this document will add:

1. **The pipeline's other services,** as each is built, with their
   replicas, credentials, and placement, such as the discoverer's, and the
   database role and Garage key of each service after the discoverer
   ([PG-7], [GARAGE-4]). The scheduler's and the fetcher's are this
   version's ([the scheduler](#the-scheduler),
   [the fetcher](#the-fetcher)), as is how the fetcher trusts the fake
   EDGAR's certificate authority where CI installs the fake EDGAR, which
   the fake EDGAR's specification leaves to the version that deploys the
   fetcher ([the fetcher and the fake EDGAR](#the-fetcher-and-the-fake-edgar)).
2. **No more durable consumers of the work queues.** `JOBS` and `ORDERS`
   are work queues, which keep a message until it's acknowledged, so a
   consumer of one can come with its service, as the fetcher's of `JOBS`
   do ([FETCHER-9]). Each subject of either now has its consumer, `JOBS`'
   in this version and `ORDERS`' in version 1.1.0 ([NATS-5]), and a work
   queue lets no two of its consumers filter the same subject, so none
   remains to add. A consumer of a stream kept by interest comes in a
   version before the service that publishes what it should see, as
   version 1.1.0's do ([NATS-5]).
3. **Changes to the record's tables and views.** [PG-8]'s Job creates
   `records` only where there's none, and the notices' tables and the
   schema `dashboards` only where there are none ([PG-12], [PG-13]), so a
   later version of `records.sql`, `notices.sql`, or `dashboards.sql` needs
   a migration, which a later version of this document specifies with the
   version of the specification that changes the file.
4. **Each component's resource requests and limits,** measured on kind
   ([0001]), and its probes. A service's readiness can't wait on its
   streams, consumers, or tables, which the hook Jobs make only once the
   release's resources are ready ([CHART-4], [NATS-5], [PG-12]).
5. **Tempo, Loki, and Alloy** ([0008]), with the endpoint the services
   export spans to ([`telemetry.md`, §11](telemetry.md#left-to-other-specifications)).
6. **kube-state-metrics' access to the definitions** of the pipeline's
   resources, beside its access to the resources themselves ([RBAC-3]),
   which the telemetry also leaves to this document
   ([`telemetry.md`, §11](telemetry.md#left-to-other-specifications)).
7. **NATS's credentials,** each service's own, kept in Secrets rendered
   from required values like the others ([SECRET-1]), with permissions
   that limit each service to the subjects it publishes and the consumers
   it reads, such as the scheduler's and the fetcher's ([SCHED-7],
   [FETCHER-8]). NATS asks no credential in this version, so any pod that
   reaches it may publish to any subject.

The owner decided on October 11, 2026 that each image built from this
repository is tagged with the commit it's built from and loaded into kind
with `kind load docker-image`, never pushed to or pulled from a registry,
and decided on October 12, 2026 that this version states it before the
first service is built: [IMG-1] to [IMG-4]. The owner decided on October
11, 2026 that the components' configuration becomes statements before the
platform is built, checked the same way as the pins: this version's
sections 4, 5, and 7. The owner decided on October 12, 2026 that this
version also deploys what the snapshot store's version 1.0.0 leaves to it.
The owner decided on October 14, 2026 that a version of its own deploys
what the notices' version 0.1.0 leaves to this document, before the
discoverer is built, and with it what the telemetry's version 0.1.0 leaves
to it for Grafana: version 1.1.0. The owner decided on October 18, 2026
that the fake EDGAR is specified, in [`fake-edgar.md`](fake-edgar.md) and
in a version of this document, before it's built: version 1.2.0. The owner
confirmed on October 19, 2026 the details that version settles, as
[`fake-edgar.md`, §10](fake-edgar.md#10-open-questions) lists them. The
owner decided on October 19, 2026 that the scheduler's deployment is
specified in a version of this document before it's built, and that the
scheduler reads the records of its polls through a durable consumer of
`edgar.records.poll` on `RECORDS`, rather than as a database role: version
1.3.0. The owner confirmed on October 22, 2026 the details that version
settles. The owner decided on October 24, 2026 that the fetcher's
deployment is specified in a version of this document before it's built,
as the scheduler's was, and confirmed the same day that it leaves the
fetcher's resource requests and limits, its probes, and its NATS
credentials to the later versions above, items 4 and 7: version 1.4.0.
The owner confirmed on October 25, 2026 the details that version
settles. The owner decided on October 28, 2026 that the transfer's access
to the cluster store is specified in a version of this document before the
transfer is built, as each service's deployment is, and that the snapshot
store's specification states the transfer's grants, in its version 1.1.0:
version 2.0.0. The owner confirmed the same day the details that version
settles, its major version among them.

## 10. Changes to this document

Versions follow semantic versioning. Adding a statement, a dependency, or a
check is a minor version. Changing the meaning of a statement, removing or
replacing a dependency, moving one to another repository or chart, or
changing PostgreSQL's or Helm's major version is a major version. IDs are
never reused, and a retired statement stays in the index.

### From version 0.1.0

Version 0.1.0 had one umbrella chart install every component. Version 1.0.0
moves cert-manager, CloudNativePG, and the Barman Cloud plugin to charts
installed before it, and adds the Prometheus operator's definitions as a
chart of their own ([0011]). So [CHART-1], [CHART-2], [PIN-1], [PIN-2],
[PIN-4], [PIN-5], [PG-1], and [PG-2] now speak of every chart. Nothing was
built under version 0.1.0, so nothing installed changes.

Version 1.0.0 also deploys what the snapshot store's version 1.0.0 leaves to
this document: the store's bucket, [GARAGE-4]; its database and each
service's role, [PG-6] to [PG-9]; its streams, [NATS-3] and [NATS-4]; and
the fetcher's Lease, [LEASE-1] and [LEASE-2]. [GARAGE-2] creates the store's
bucket and keys, and [SECRET-1] lists each service's database password
among the credentials.

### From version 1.0.0

Version 1.1.0 deploys what the notices' version 0.1.0 leaves to this
document, and what the telemetry's version 0.1.0 leaves to it for Grafana.
It adds statements, and lengthens the lists that [NATS-3], [GARAGE-4],
[PG-7], and [SECRET-1] give for this version, so it's a minor version: no
statement's meaning changes.

- **NATS:** the streams `ORDERS` and `NOTICES`, [NATS-3]; the durable
  consumers of the discoverer, the scheduler, and the parse workers, the
  order in which the Job makes the streams, and when a stream kept by
  interest takes its subjects, [NATS-5] and [NATS-6].
- **Garage:** the discoverer's key, which may only read the store's
  bucket, [GARAGE-4].
- **PostgreSQL:** the discoverer's role, [PG-7], and Grafana's, [PG-11];
  `notices.sql` and `dashboards.sql`, applied after `records.sql`, with
  their grants, [PG-12] to [PG-14].
- **Grafana:** its PostgreSQL data source, [MON-5] and [MON-6], and its
  role's password among the credentials, [SECRET-1].

Nothing was installed under version 1.0.0, so nothing installed changes.

### From version 1.1.0

Version 1.2.0 installs the fake EDGAR for CI, and says how the pipeline's
images are built. It adds statements, a term, and a guarantee, so it's a
minor version: no statement's meaning changes.

- **The fake EDGAR:** the umbrella chart renders it only when
  `fakeEdgar.enabled` is `true`, as one replica of its image behind a
  Service, with a certificate for the SEC's hosts from an authority of the
  release's own, [FAKE-1] to [FAKE-3]; and CI installs it, pointing kind's
  DNS for the SEC's hosts at it, [FAKE-4] and [FAKE-5]. That is guarantee
  13.
- **The pipeline's images:** each is built from its commit's files and
  from base images named by digest, and the fake EDGAR's holds `data/`,
  [IMG-5].

The owner's installations don't set `fakeEdgar.enabled`, so nothing they
install changes. Neither do CI's until T21 builds the fake EDGAR.

### From version 1.2.0

Version 1.3.0 deploys the scheduler. It adds statements, a term, and a
guarantee, so it's a minor version: no statement's meaning changes.

- **The scheduler:** a Deployment of two replicas of its image, as a
  ServiceAccount of its own, which claims no volume and holds no credential
  but its token, [SCHED-1]; each replica on a worker node of its own,
  [SCHED-2]; its Lease, `scheduler`, with its timings from the values, and
  the active replica's use of it, [SCHED-3] and [SCHED-4], and on kind
  [SCHED-5]; [RBAC-1]'s grants and its Lease's, and no others, [SCHED-6];
  NATS's address, without a credential, [SCHED-7]; and its durable
  consumer of poll events, `scheduler-polls`, on `RECORDS`, which it waits
  for but never makes, [SCHED-8] and [SCHED-9]. That is guarantee 14, and
  the consumer joins guarantee 12.
- **Statement kinds:** a rule may also be how a service uses what the
  charts give it, checked by the service's tests, Go tests among them, in
  the form the first Go test sets, [§3](#3-statement-ids).
- **NATS's credentials:** a later version, [§9](#9-later-versions), item 7.
- **Wording:** [MON-1] names Kubernetes's own scheduler, `kube-scheduler`,
  now that this document has a section on the pipeline's, and [NATS-5]'s
  row in the index no longer counts the consumers the values list. Neither
  statement's meaning changes.

Nothing changes on a cluster until T22 builds the scheduler, which adds
`scheduler-polls` to the values. On a cluster that already has `RECORDS`,
[NATS-3]'s Job then makes the consumer on a stream that already takes its
subjects, before the fetcher, which T23 builds, publishes any poll event.

### From version 1.3.0

Version 1.4.0 deploys the fetcher. It adds statements, an area, and a
guarantee, so it's a minor version: no statement's meaning changes.

- **The fetcher:** a Deployment of two replicas of its image, as a
  ServiceAccount of its own, which claims no volume and is given the commit
  its image is tagged with, [FETCHER-1]; each replica on a worker node of
  its own, [FETCHER-2], and on kind [FETCHER-3], where a replica that
  Kubernetes stops doesn't release the Lease, unlike the scheduler's;
  [RBAC-2]'s grants and its Lease's, and no others, [FETCHER-4]; its Garage
  key, its database role's password, and its user agent, read from their
  Secrets, with the user agent a required value, [FETCHER-5]; the bucket
  and Garage's S3 API, [FETCHER-6], and the database's primary, verified
  against the `Cluster`'s authority, [FETCHER-7]; NATS's address, without a
  credential, [FETCHER-8]; its three durable consumers of `JOBS`, one for
  each kind of job, which only its active replica reads and which it never
  makes, [FETCHER-9] to [FETCHER-11]; and, where CI installs the fake
  EDGAR, that authority's certificate, which it trusts in place of its
  image's roots, [FETCHER-12] and [FETCHER-13]. That is guarantee 15, and
  the fetcher joins guarantees 10 and 13.
- **The area `FETCHER`,** apart from the snapshot store's `FETCH`,
  [§3](#3-statement-ids).
- **Later versions:** [§9](#9-later-versions)'s item 1 no longer names the
  fetcher as a later version, and points to how it trusts the fake EDGAR's
  authority, which the fake EDGAR's specification leaves to this version.
  Item 2 says no durable consumer of a work queue remains to add, now that
  the fetcher's are this version's, and keeps its number.

Nothing changes on a cluster until T23 builds the fetcher, which adds its
consumers to the values. On a cluster that already has `JOBS`, [NATS-3]'s
Job then makes them on the stream, which keeps each job it holds until a
consumer acknowledges it. The fetcher takes each such job as any other, and
acknowledges a poll's job that has expired without a request ([JOB-5]).

### From version 1.4.0

Version 2.0.0 gives the transfer, which the owner runs outside the cluster,
its access to the cluster store. [PG-8]'s Job now grants the transfer's role
`SELECT` on `records`, where [PG-8] granted no role but the services' and
the owner's any privilege on it. That changes the statement's meaning, so
it's a major version. It also adds statements, an area, a term, and a
guarantee, and lengthens the lists that [GARAGE-4] and [SECRET-1] give for
this version:

- **The transfer's credentials:** its database role, `transfer`, granted
  only `SELECT` on `records`, [PG-15] and [PG-8]; its Garage key,
  `transfer`, which may only read the store's bucket, [GARAGE-5] and
  [GARAGE-4]; and their password, ID, and secret among the required
  values, [SECRET-1], each in a Secret that no service reads,
  [TRANSFER-1]. The transfer joins guarantee 10.
- **How the transfer reaches the store:** nothing exposes the database or
  Garage outside the cluster, [TRANSFER-2]; the transfer acts on the
  cluster of the context the owner names, [TRANSFER-3]; it reads its
  role's password and its key from their Secrets through the API server,
  [TRANSFER-4]; it reads the database's primary, with channel binding, and
  the bucket, through port-forwards, [TRANSFER-5]; and it copies nothing
  from a cluster that holds the fake EDGAR, [TRANSFER-6]. That is
  guarantee 16.
- **The term the transfer, and the area `TRANSFER`,** apart from the
  snapshot store's `XFER`, [§2](#2-terms) and [§3](#3-statement-ids).

Nothing changes on a cluster until T36 builds the transfer, which adds its
role, its key, and their Secrets to the umbrella chart, and the role's
grant to [PG-8]'s Job. The Job runs after each upgrade, so the upgrade that
adds the role grants it, and no service's grants change.

[0001]: ../docs/decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md
[0002]: ../docs/decisions/0002-store-bodies-in-garage.md
[0003]: ../docs/decisions/0003-queue-work-in-nats-jetstream.md
[0004]: ../docs/decisions/0004-keep-the-record-in-postgresql-18.md
[0005]: ../docs/decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0006]: ../docs/decisions/0006-write-the-services-in-go-and-python.md
[0007]: ../docs/decisions/0007-control-the-pipeline-through-custom-resources.md
[0008]: ../docs/decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md
[0010]: ../docs/decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[0011]: ../docs/decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md
[ACCESS-1]: controls.md#8-secaccess
[ACCESS-3]: controls.md#8-secaccess
[ADMIN-1]: fake-edgar.md#7-the-admin-port
[BACKFILL-1]: controls.md#7-backfill
[CHART-1]: #4-layout
[CHART-2]: #5-dependencies
[CHART-3]: #5-dependencies
[CHART-4]: #5-dependencies
[CRD-1]: #definitions
[CRD-2]: #definitions
[CRD-3]: #definitions
[CRD-4]: #definitions
[CRD-5]: #definitions
[CRD-6]: #definitions
[DISC-1]: notices.md#processing
[DISC-2]: notices.md#processing
[FAKE-1]: #the-fake-edgar
[FAKE-2]: #the-fake-edgar
[FAKE-3]: #the-fake-edgar
[FAKE-4]: #the-fake-edgar
[FAKE-5]: #the-fake-edgar
[FETCH-1]: snapshots.md#10-fetching
[FETCH-2]: snapshots.md#10-fetching
[FETCH-5]: snapshots.md#10-fetching
[FETCH-6]: snapshots.md#10-fetching
[FETCH-7]: snapshots.md#10-fetching
[FETCH-8]: snapshots.md#10-fetching
[FETCH-16]: snapshots.md#the-fetcher
[FETCH-17]: snapshots.md#the-fetcher
[FETCHER-1]: #the-fetcher
[FETCHER-2]: #the-fetcher
[FETCHER-3]: #the-fetcher
[FETCHER-4]: #the-fetchers-grants
[FETCHER-5]: #the-fetchers-credentials-and-stores
[FETCHER-6]: #the-fetchers-credentials-and-stores
[FETCHER-7]: #the-fetchers-credentials-and-stores
[FETCHER-8]: #the-fetchers-access-to-nats
[FETCHER-9]: #the-fetchers-access-to-nats
[FETCHER-10]: #the-fetchers-access-to-nats
[FETCHER-11]: #the-fetchers-access-to-nats
[FETCHER-12]: #the-fetcher-and-the-fake-edgar
[FETCHER-13]: #the-fetcher-and-the-fake-edgar
[FILE-5]: notices.md#7-a-filings-files
[GAP-3]: notices.md#5-polls-coverage-and-gaps
[GARAGE-1]: #garage
[GARAGE-2]: #garage
[GARAGE-3]: #garage
[GARAGE-4]: #garage
[GARAGE-5]: #garage
[ID-2]: snapshots.md#the-fetcher
[IMG-1]: #the-pipelines-own-images
[IMG-2]: #the-pipelines-own-images
[IMG-3]: #the-pipelines-own-images
[IMG-4]: #the-pipelines-own-images
[IMG-5]: #the-pipelines-own-images
[JOB-2]: snapshots.md#jobs-and-events
[JOB-3]: snapshots.md#jobs-and-events
[JOB-4]: snapshots.md#jobs-and-events
[JOB-5]: snapshots.md#jobs-and-events
[JOB-6]: snapshots.md#jobs-and-events
[KIND-1]: #the-kind-cluster
[KIND-2]: #the-kind-cluster
[LEASE-1]: #the-fetchers-lease
[LEASE-2]: #the-fetchers-lease
[LIST-1]: notices.md#8-backfills
[LIST-3]: notices.md#8-backfills
[LOG-1]: snapshots.md#8-history
[LOG-3]: snapshots.md#8-history
[LOG-4]: snapshots.md#8-history
[LOG-5]: snapshots.md#8-history
[METRIC-1]: telemetry.md#4-metrics
[METRIC-3]: telemetry.md#the-scheduler
[METRIC-4]: telemetry.md#the-fetchers-clock
[MON-1]: #monitoring
[MON-5]: #monitoring
[MON-6]: #monitoring
[NATS-1]: #nats
[NATS-3]: #nats
[NATS-4]: #nats
[NATS-5]: #durable-consumers
[NATS-6]: #durable-consumers
[NOTICE-1]: notices.md#8-backfills
[OBJ-1]: snapshots.md#5-objects
[NOTICE-2]: notices.md#8-backfills
[ORDER-1]: notices.md#8-backfills
[PG-1]: #7-postgresql
[PG-2]: #7-postgresql
[PG-3]: #instances-and-backups
[PG-4]: #instances-and-backups
[PG-5]: #instances-and-backups
[PG-6]: #the-stores-database
[PG-7]: #the-stores-database
[PG-8]: #the-stores-database
[PG-9]: #the-stores-database
[PG-10]: #instances-and-backups
[PG-11]: #the-notices-and-the-dashboards
[PG-12]: #the-notices-and-the-dashboards
[PG-13]: #the-notices-and-the-dashboards
[PG-14]: #the-notices-and-the-dashboards
[PG-15]: #the-stores-database
[PIN-1]: #6-pins
[PIN-2]: #6-pins
[PIN-3]: #6-pins
[PIN-4]: #6-pins
[PIN-5]: #6-pins
[PIN-6]: #6-pins
[PIN-7]: #6-pins
[PLACE-1]: #placement
[PLACE-2]: #placement
[RBAC-1]: controls.md#9-who-may-change-what
[RBAC-2]: controls.md#9-who-may-change-what
[RBAC-3]: controls.md#9-who-may-change-what
[SCHED-1]: #the-scheduler
[SCHED-2]: #the-scheduler
[SCHED-3]: #its-lease
[SCHED-4]: #its-lease
[SCHED-5]: #its-lease
[SCHED-6]: #its-grants
[SCHED-7]: #its-access-to-nats
[SCHED-8]: #its-access-to-nats
[SCHED-9]: #its-access-to-nats
[SECRET-1]: #credentials
[SECRET-2]: #credentials
[SECRET-3]: #credentials
[SECRET-4]: #credentials
[SERVE-1]: fake-edgar.md#4-what-it-serves
[SERVE-3]: fake-edgar.md#4-what-it-serves
[SERVE-5]: fake-edgar.md#4-what-it-serves
[SOURCE-1]: controls.md#6-source
[SOURCE-2]: controls.md#6-source
[SOURCE-4]: controls.md#6-source
[STORE-1]: snapshots.md#the-cluster-store
[STORE-2]: snapshots.md#the-cluster-store
[TABLE-2]: notices.md#4-the-record-of-notices
[TABLE-3]: notices.md#4-the-record-of-notices
[TRANSFER-1]: #the-transfer
[TRANSFER-2]: #the-transfer
[TRANSFER-3]: #the-transfer
[TRANSFER-4]: #the-transfer
[TRANSFER-5]: #the-transfer
[TRANSFER-6]: #the-transfer
[VAL-4]: controls.md#5-the-charts-values
[VIEW-2]: telemetry.md#8-the-views-grafana-reads
[VIEW-3]: telemetry.md#8-the-views-grafana-reads
[VOL-1]: #volumes
[VOL-2]: #volumes
[VOL-3]: #volumes
[XFER-1]: snapshots.md#records-the-cluster-made
