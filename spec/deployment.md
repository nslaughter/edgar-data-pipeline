# Specification: deployment

**Status:** Version 0.1.0, draft, October 11, 2026. This document governs
what the pipeline's umbrella Helm chart installs and at which versions: the
charts, operators, and images it pins, the tools that install it, and the
tests that check them. Where the chart and this document disagree, the chart
is wrong or this document needs a new version; say which before changing
either.

It rests on the decisions to deploy on Kubernetes with Helm, on kind
([0001]), and to keep the record in PostgreSQL 18 ([0004]), and on the
decisions that chose the other components ([0002], [0003], [0008]). How each
component is configured, such as its replicas, storage, and resources, isn't
in this version ([§9](#9-later-versions)). The pipeline it
deploys reads filings from the U.S. Securities and Exchange Commission's
EDGAR system, but nothing here depends on how EDGAR behaves.

## 1. Guarantees

Each guarantee holds because of the statements it lists ([§3](#3-statement-ids)).

1. **The same commit installs the same software.** Every chart is committed
   at an exact version, and every third-party image is named by its digest.
   [PIN-1], [PIN-2], [PIN-3], [PIN-4], [PIN-5], [PIN-6], [PIN-7].
2. **An upgrade is a reviewed change** to the chart, the kind
   configuration, or CI, since nothing is resolved at install time.
   [PIN-1], [PIN-4], [PIN-6], [PIN-7].
3. **Each component comes from the project that maintains it,** never from
   Bitnami. [CHART-2], [CHART-3].
4. **The database is PostgreSQL 18.** [PG-1], [PG-2].
5. **The chart renders without the network,** so its tests never reach it.
   [PIN-2], [PIN-3].

## 2. Terms

- **Umbrella chart:** the one Helm chart that installs the pipeline
  ([0001]).
- **Dependency:** a chart the umbrella chart installs as a subchart, listed
  in its `Chart.yaml`.
- **Image:** a container image. An image built from this repository is the
  pipeline's own; any other is third-party.
- **Pin:** a reference that names one artifact and no other: a chart's exact
  version, committed; an image's digest; a tool's exact version.
- **The installed chart:** the umbrella chart as CI installs it on kind,
  with the values it installs it with.

## 3. Statement IDs

Every normative statement in this document has an ID, such as `PG-1`: an
area and a number. The areas `CHART`, `PIN`, and `PG` belong to this
document. An ID is assigned once and never reused or renumbered. A statement
that is dropped stays in the [index](#8-index), marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of the chart as committed, and of what it
  renders. Tests check it by rendering the chart with `helm template`, with
  the values CI installs it with, without a cluster or the network. They run
  with the other tests, wherever those run, and need Helm.
- A **rule** is how the installed chart behaves on a cluster. Tests check it
  against the installed chart, in the CI job that installs it on kind, and
  are skipped where no cluster is installed.

The index says how each statement is checked: by `tests`, by tests on
`kind`, or in `review` of the pull request that changes what it covers. Every
statement checked by `tests` or `kind` has at least one test marked with its
ID, as `@pytest.mark.spec("PG-1")`, and every marker names an ID in a
specification's index. A test, `tests/test_spec_coverage.py`, enforces both.

## 4. Layout

```text
deploy/
  chart/                       the umbrella chart
    Chart.yaml                 its dependencies, each at an exact version
    Chart.lock                 the same dependencies, as Helm resolved them
    charts/NAME-VERSION.tgz    each dependency from a chart repository
    charts/garage/             Garage's chart, from its source tree
    values.yaml
    templates/
  kind.yaml                    the kind cluster's configuration
```

- **CHART-1** (invariant) The umbrella chart is at `deploy/chart/`, and the
  kind cluster's configuration is at `deploy/kind.yaml`.

## 5. Dependencies

In this version, the umbrella chart installs the platform: each component
from the chart its own project maintains ([0001]).

| Dependency | Installs | Repository |
| --- | --- | --- |
| `cert-manager` | cert-manager, which the Barman Cloud plugin requires for its certificates | `oci://quay.io/jetstack/charts` |
| `cloudnative-pg` | The CloudNativePG operator ([0004]) | `https://cloudnative-pg.github.io/charts` |
| `plugin-barman-cloud` | CloudNativePG's Barman Cloud plugin, which backs the database up to Garage ([0002], [0004]) | `https://cloudnative-pg.github.io/charts` |
| `nats` | NATS, with JetStream ([0003]) | `https://nats-io.github.io/k8s/helm/charts/` |
| `garage` | Garage ([0002]) | None: `script/helm/garage` in Garage's source tree, `https://git.deuxfleurs.fr/Deuxfleurs/garage`, committed ([PIN-3]) |
| `kube-prometheus-stack` | The Prometheus operator, Prometheus, Alertmanager, Grafana, kube-state-metrics, and node-exporter ([0008]) | `oci://ghcr.io/prometheus-community/charts` |

The database itself, a CloudNativePG `Cluster` and its `ImageCatalog`, is
in the umbrella chart's own templates ([§7](#7-postgresql)). Tempo, Loki,
Alloy, and the pipeline's own services arrive in later versions.

- **CHART-2** (invariant) The umbrella chart's dependencies are those in the
  table, each under its name and from its repository, and no others.
- **CHART-3** (invariant) Nothing comes from Bitnami ([0001]): no
  dependency's repository, no repository of a chart a dependency includes,
  and no image the chart renders contains `bitnami`.

## 6. Pins

Which version each pin names is for the chart, the kind configuration, and
CI to say, not this document. An upgrade changes one of them and is reviewed
there, without a new version of this document. The exceptions are
PostgreSQL's major version ([PG-1]) and Helm's ([PIN-7]).

- **PIN-1** (invariant) Each dependency in `Chart.yaml` names one exact
  version: a semantic version, with or without a leading `v`, without a
  range, a wildcard, or a constraint. `Chart.lock` lists the same
  dependencies, with the same versions and repositories.
- **PIN-2** (invariant) Each dependency from a chart repository is
  committed as the packaged chart `helm dependency update` downloads, at
  `deploy/chart/charts/NAME-VERSION.tgz`, so `helm template` renders the
  chart without the network.
- **PIN-3** (invariant) Garage's chart, which no chart repository publishes
  ([0002]), is committed as the directory `deploy/chart/charts/garage/`,
  unchanged from `script/helm/garage` in Garage's source tree at a release
  tag. The umbrella chart's dependency on it names the version in that
  directory's `Chart.yaml`, and the commit that adds or upgrades it names
  the tag.
- **PIN-4** (invariant) Every third-party image the chart renders, as the
  value of a field named `image` anywhere in a rendered manifest, whether
  in a container or in a custom resource, is named as
  `REPOSITORY:TAG@sha256:DIGEST`, where `DIGEST` is 64 lower-case
  hexadecimal digits: the tag for readers, and the digest for the runtime.
  An image an operator chooses without such a field is held to [PIN-5].
- **PIN-5** (rule) In every pod of the installed chart, each container and
  init container that runs a third-party image names it by digest, as
  [PIN-4] has it. The chart's pods are those of the resources it renders,
  and those its operators create for them, such as the database's
  instances, in whichever namespace they run. Kind's own pods, such as its
  control plane, CoreDNS, kindnet, and its storage provisioner, aren't the
  chart's: their images come with the node image that [PIN-6] pins.
- **PIN-6** (invariant) `deploy/kind.yaml` names every node's image as
  `kindest/node:TAG@sha256:DIGEST`, which also pins the cluster's Kubernetes
  version.
- **PIN-7** (invariant) CI installs Helm and kind at exact versions, and
  Helm at major version 4.

## 7. PostgreSQL

The pipeline's record and facts are kept in PostgreSQL 18 ([0004]). Which
image of 18 the catalog names, its minor version, its type, and its
operating system, is the chart's to say, like any other pin.

- **PG-1** (invariant) The chart renders one CloudNativePG `ImageCatalog`.
  Its only entry has `major: 18` and names an image from
  `ghcr.io/cloudnative-pg/postgresql` whose tag begins with `18.`, by digest
  ([PIN-4]). Every `Cluster` names its image through that catalog, with
  `major: 18`, and sets no `imageName`.
- **PG-2** (rule) Every instance of every `Cluster` in the installed chart
  reports a `server_version_num` from 180000 to 189999: PostgreSQL 18.

A new minor version changes only the catalog's image. A new major version
takes a decision record that supersedes [0004], and a major version of this
document.

## 8. Index

| ID | Kind | Checked by | Statement |
| --- | --- | --- | --- |
| CHART-1 | invariant | tests | The chart is at `deploy/chart/`, and kind's configuration at `deploy/kind.yaml`. |
| CHART-2 | invariant | tests | The dependencies are the table's, from its repositories. |
| CHART-3 | invariant | tests | No chart or image comes from Bitnami. |
| PIN-1 | invariant | tests | Each dependency names an exact version, which `Chart.lock` repeats. |
| PIN-2 | invariant | tests | Each dependency from a repository is committed, packaged. |
| PIN-3 | invariant | tests, review | Garage's chart is committed unchanged from a release tag. |
| PIN-4 | invariant | tests | Each third-party image the chart renders is named by tag and digest. |
| PIN-5 | rule | kind | Each third-party image in the installed chart's pods is named by digest. |
| PIN-6 | invariant | tests | Each kind node's image is named by digest. |
| PIN-7 | invariant | tests | CI installs Helm and kind at exact versions, Helm at major version 4. |
| PG-1 | invariant | tests | One image catalog, of major version 18, names the database's image. |
| PG-2 | rule | kind | Every database instance reports PostgreSQL 18. |

## 9. Later versions

The owner has decided what later versions of this document will say on two
questions this version leaves open.

1. **The pipeline's own images.** Each image built from this repository is
   tagged with the commit it is built from and loaded into kind with
   `kind load docker-image`, never pushed to or pulled from a registry. The
   owner decided this on October 11, 2026. The version of this document
   that adds the first service says so in statements.
2. **The components' configuration.** The decision records describe each
   component's replicas, storage, Secrets, and placement, such as three
   Garage nodes with a replication factor of 3 ([0002]) and three
   PostgreSQL instances ([0004]); none of it is a statement yet. The owner
   decided on October 11, 2026 that it becomes statements in a minor
   version of this document, written before the platform is built, and
   checked the same way.

## 10. Changes to this document

Versions follow semantic versioning. Adding a statement, a dependency, or a
check is a minor version. Changing the meaning of a statement, removing or
replacing a dependency, moving one to another repository, or changing
PostgreSQL's or Helm's major version is a major version. IDs are never
reused, and a retired statement stays in the index.

[0001]: ../docs/decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md
[0002]: ../docs/decisions/0002-store-bodies-in-garage.md
[0003]: ../docs/decisions/0003-queue-work-in-nats-jetstream.md
[0004]: ../docs/decisions/0004-keep-the-record-in-postgresql-18.md
[0008]: ../docs/decisions/0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md
[CHART-2]: #5-dependencies
[CHART-3]: #5-dependencies
[PIN-1]: #6-pins
[PIN-2]: #6-pins
[PIN-3]: #6-pins
[PIN-4]: #6-pins
[PIN-5]: #6-pins
[PIN-6]: #6-pins
[PIN-7]: #6-pins
[PG-1]: #7-postgresql
[PG-2]: #7-postgresql
