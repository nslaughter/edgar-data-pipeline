# Specification: deployment

**Status:** Version 1.0.0, draft, October 13, 2026. This document governs
what the pipeline's Helm charts install, in which order and at which
versions, and how they configure what they install: the charts, operators,
and images they pin; the custom resource definitions they install; each
component's replicas, storage, credentials, and placement; the cluster
store's bucket, database, table, and streams, and the fetcher's Lease; the
kind cluster they're installed on; the tools that install them; and the
tests that check them. Where a chart and this document disagree, the chart
is wrong or this document needs a new version; say which before changing
either.

It rests on the decisions to deploy on Kubernetes with Helm, on kind
([0001]); to install the definitions and the operators before the umbrella
chart, in four releases ([0011]); and to keep the record in PostgreSQL 18
([0004]); and on the decisions that chose the other components ([0002],
[0003], [0005], [0008]). It deploys what the snapshot store's version 1.0.0
leaves to it ([`snapshots.md`, §13](snapshots.md#left-to-other-specifications)).
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
   [IMG-3].
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
   a copy of every object on each, and NATS of every stream. [KIND-1],
   [PLACE-1], [PLACE-2], [NATS-1], [NATS-3], [NATS-4], [GARAGE-1],
   [GARAGE-3], [PG-3].
8. **Stored data outlives the cluster.** Garage's objects, the database's
   backups among them, NATS's streams, and Prometheus's and Alertmanager's
   data are kept in a directory of the host, where a cluster recreated with
   it finds them. [KIND-2], [VOL-1], [VOL-2], [VOL-3], [PG-4], [PG-5],
   [PG-10].
9. **No credential is committed, generated as the charts render, or shown
   outside a Secret.** An operator may generate one in the cluster, such as
   the database owner's password ([PG-6]). [SECRET-1], [SECRET-2],
   [SECRET-3], [SECRET-4].
10. **Only the fetcher writes to the cluster store, and no service can
    change a record:** each service has credentials of its own, and only
    the fetcher's may add records and objects ([LOG-3], [LOG-4]). [GARAGE-4],
    [PG-6], [PG-7], [PG-8], [PG-9].

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
- **The cluster store:** the store's objects in Garage and records in
  PostgreSQL, as the snapshot store's specification describes it
  ([`snapshots.md`](snapshots.md#the-cluster-store)).

## 3. Statement IDs

Every normative statement in this document has an ID, such as `PG-1`: an
area and a number. The areas `CHART`, `PIN`, `PG`, `CRD`, `IMG`, `KIND`,
`VOL`, `PLACE`, `NATS`, `GARAGE`, `MON`, `SECRET`, and `LEASE` belong to
this document. An ID is assigned once and never reused or renumbered. A
statement that is dropped stays in the [index](#8-index), marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of the charts and the kind configuration
  as committed, and of what the charts render. Tests check it by rendering
  each chart with `helm template`, with the values CI installs it with,
  apart from the credentials and the database archive's server name, which
  CI sets for each cluster, and for which tests give values of their own
  ([SECRET-4], [PG-10]). They run without a cluster or the network, with
  the other tests, wherever those run, and need Helm.
- A **rule** is how the installed charts, or the tools around them, behave.
  Tests check the installed charts in the CI job that installs them on
  kind, and are skipped where no cluster is installed. A rule on a tool,
  such as [IMG-2]'s command, is checked by tests of that tool.

The index says how each statement is checked: by `tests`, by tests on
`kind`, in `review` of the pull request that changes what it covers, or by
the `operator`, the owner, who keeps it. Every statement checked by `tests`
or `kind` has at least one test marked with its ID, as
`@pytest.mark.spec("PG-1")`, and every marker names an ID in a
specification's index. A test, `tests/test_spec_coverage.py`, enforces
both, except for a statement whose tests a later task in the
[plan](../docs/plan.md) writes. It lists such a statement as awaiting that
task, and fails if the statement has a test or the plan marks the task
done. This version's statements come before the charts they describe: T20
builds the charts and writes their tests, apart from [IMG-1] to [IMG-3]'s,
which T21 writes with the first of the pipeline's images, and [LEASE-1]'s
and [LEASE-2]'s, which T23 writes with the fetcher. Until then each awaits
its task.

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
| 4 | `deploy/chart/` | `edgar` | `edgar` | NATS, with JetStream ([0003]); Garage ([0002]); kube-prometheus-stack ([0008]); the database ([§7](#7-postgresql)); and later the pipeline's services and controls |

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
in the umbrella chart's own templates ([§7](#7-postgresql)). Tempo, Loki,
Alloy, and the pipeline's own services arrive in later versions. The
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
([`snapshots.md`](snapshots.md#jobs-and-events)), each kept as that
specification says, in files, and replicated on all three servers, so a
stream keeps its messages and its leader through the loss of a node:

| Stream | Subjects | Retention | Storage | Replicas |
| --- | --- | --- | --- | --- |
| `JOBS` | `edgar.jobs.poll`, `edgar.jobs.filing`, `edgar.jobs.backfill` | Work queue | File | 3 |
| `RECORDS` | `edgar.records.poll`, `edgar.records.filing`, `edgar.records.backfill` | Interest | File | 3 |

- **NATS-3** (invariant) The umbrella chart renders a Job, a Helm hook run
  after each install and upgrade, that creates each stream the umbrella
  chart's values list, or updates it to their configuration, through
  JetStream's API. It waits until JetStream accepts its requests, and
  succeeds when each stream already is as the values say. In this version,
  the values list the table's two streams, with the table's subjects,
  retention, storage, and replicas ([0003]).
- **NATS-4** (rule) In the installed charts, each stream the values list
  exists with their configuration, with a replica on each of the three
  servers, and the Job succeeds when it runs again.

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
  store's bucket, with its keys ([GARAGE-4]).
- **GARAGE-3** (rule) In the installed charts, Garage's layout holds its
  three nodes, in three zones, each node holding a copy of every object.
  Each bucket and key the values list exists, with the grants they give,
  and the Job succeeds when it runs again.
- **GARAGE-4** (invariant) The cluster store's bucket is `snapshots`. Each
  service that uses it has a key of its own, which may use no other
  bucket: the fetcher's may read and write it, and each other service's
  may only read it ([LOG-4]). No key has Garage's `owner` permission on
  any bucket, which would let it change the bucket's configuration. In
  this version, the values list one key for the bucket, the fetcher's;
  each other service's comes with that service ([§9](#9-later-versions)).

### Monitoring

- **MON-1** (invariant) kube-prometheus-stack scrapes nothing kind doesn't
  expose: it renders no monitor or rule group for the controller manager,
  the scheduler, etcd, or kube-proxy ([0008]).
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
  role ([PG-7]), and Grafana's admin password. The chart renders each into
  a Secret, from which its component reads it.
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
service's role what [LOG-3] allows, and no service connects as the owner,
who could change a record.

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
  version, the values list one, `fetcher`; each other service's comes with
  that service ([§9](#9-later-versions)).
- **PG-8** (invariant) The umbrella chart renders a Job, a Helm hook run
  after each install and upgrade, that connects to `edgar` as its owner and:
  - creates the table `records` and its index from
    `deploy/chart/files/records.sql`, unless the table exists, applying
    the file in one transaction, so the table never exists without its
    index;
  - grants the fetcher's role `INSERT` and `SELECT` on `records`, each
    other service's role `SELECT` only ([LOG-3]), and no other role any
    privilege on it, but its owner.

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
| KIND-1 | invariant | tests | kind has one control-plane node and three workers. |
| KIND-2 | invariant | tests | Every worker node keeps its volumes in one host directory. |
| VOL-1 | invariant | tests | The `edgar-retained` class keeps a volume's data, at a path named by its claim. |
| VOL-2 | invariant | tests | NATS, Garage, Prometheus, and Alertmanager claim `edgar-retained`; the database claims `standard`. |
| VOL-3 | rule | kind | A new claim of the same namespace and name finds a deleted claim's data. |
| PLACE-1 | invariant | tests | NATS's, Garage's, and the database's replicas each need a worker node of their own. |
| PLACE-2 | rule | kind | Their replicas run one on each worker node. |
| NATS-1 | invariant | tests | NATS is a cluster of three servers, with JetStream on claims. |
| NATS-2 | rule | kind | The three servers form one JetStream cluster, with a leader. |
| NATS-3 | invariant | tests | A hook Job creates or updates the streams `JOBS` and `RECORDS`, each in files with three replicas. |
| NATS-4 | rule | kind | Each stream is as the values say, on all three servers, and the Job can run again. |
| GARAGE-1 | invariant | tests | Garage is three nodes, replication factor 3, on claims, with no definition or ClusterRole of its own. |
| GARAGE-2 | invariant | tests | A hook Job sets the layout, keys, buckets, and grants, and can run again. |
| GARAGE-3 | rule | kind | Garage's layout, buckets, keys, and grants are as the values say. |
| GARAGE-4 | invariant | tests | The store's bucket is `snapshots`; only the fetcher's key may write it, and no key owns a bucket. |
| MON-1 | invariant | tests | Nothing kind doesn't expose is scraped. |
| MON-2 | invariant | tests | Grafana is read-only; its data sources come from its own value, which restarts it, and its sidecar provisions dashboards only. |
| MON-3 | invariant | tests | Alertmanager sends nothing outside the cluster. |
| MON-4 | invariant | tests | Prometheus, Alertmanager, and Grafana run one replica each. |
| SECRET-1 | invariant | tests | Each credential is a required value, set by no committed file, rendered into a Secret. |
| SECRET-2 | invariant | tests | The same values render the same manifests. |
| SECRET-3 | invariant | tests | A credential appears only in a Secret. |
| SECRET-4 | rule | review, operator | CI generates credentials per cluster, and the owner commits none. |
| LEASE-1 | invariant | tests | The fetcher's Lease, `fetcher`, takes its timings from values, 15, 10, and 2 seconds by default, and the chart refuses those client-go's leader election refuses. |
| LEASE-2 | rule | kind | One of the fetcher's pods holds the Lease, with the values' duration. |

## 9. Later versions

Later versions of this document will add:

1. **The pipeline's own services,** as each is built, with their replicas,
   credentials, and placement, such as the fetcher's two replicas ([0005]),
   and the database role and Garage key of each service after the fetcher
   ([PG-7], [GARAGE-4]).
2. **The streams' durable consumers,** each with its service. `RECORDS`
   keeps an event only for the consumers it has when the event is
   published, so a service that reads events needs its consumer before
   the fetcher publishes what it should see.
3. **Changes to the store's table.** [PG-8]'s Job creates the table only
   where there's none, so a later version of `records.sql` needs a
   migration, which a later version of this document specifies with the
   snapshot store's version that changes the table.
4. **Each component's resource requests and limits,** measured on kind
   ([0001]), and its probes.
5. **Tempo, Loki, and Alloy** ([0008]).

The owner decided on October 11, 2026 that each image built from this
repository is tagged with the commit it's built from and loaded into kind
with `kind load docker-image`, never pushed to or pulled from a registry,
and decided on October 12, 2026 that this version states it before the
first service is built: [IMG-1] to [IMG-4]. The owner decided on October
11, 2026 that the components' configuration becomes statements before the
platform is built, checked the same way as the pins: this version's
sections 4, 5, and 7. The owner decided on October 12, 2026 that this
version also deploys what the snapshot store's version 1.0.0 leaves to it.

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

[0001]: ../docs/decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md
[0002]: ../docs/decisions/0002-store-bodies-in-garage.md
[0003]: ../docs/decisions/0003-queue-work-in-nats-jetstream.md
[0004]: ../docs/decisions/0004-keep-the-record-in-postgresql-18.md
[0005]: ../docs/decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0006]: ../docs/decisions/0006-write-the-services-in-go-and-python.md
[0008]: ../docs/decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md
[0011]: ../docs/decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md
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
[FETCH-16]: snapshots.md#the-fetcher
[GARAGE-1]: #garage
[GARAGE-2]: #garage
[GARAGE-3]: #garage
[GARAGE-4]: #garage
[IMG-1]: #the-pipelines-own-images
[IMG-2]: #the-pipelines-own-images
[IMG-3]: #the-pipelines-own-images
[IMG-4]: #the-pipelines-own-images
[KIND-1]: #the-kind-cluster
[KIND-2]: #the-kind-cluster
[LEASE-1]: #the-fetchers-lease
[LEASE-2]: #the-fetchers-lease
[LOG-3]: snapshots.md#8-history
[LOG-4]: snapshots.md#8-history
[NATS-1]: #nats
[NATS-3]: #nats
[NATS-4]: #nats
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
[PIN-1]: #6-pins
[PIN-2]: #6-pins
[PIN-3]: #6-pins
[PIN-4]: #6-pins
[PIN-5]: #6-pins
[PIN-6]: #6-pins
[PIN-7]: #6-pins
[PLACE-1]: #placement
[PLACE-2]: #placement
[SECRET-1]: #credentials
[SECRET-2]: #credentials
[SECRET-3]: #credentials
[SECRET-4]: #credentials
[STORE-1]: snapshots.md#the-cluster-store
[STORE-2]: snapshots.md#the-cluster-store
[VOL-1]: #volumes
[VOL-2]: #volumes
[VOL-3]: #volumes
