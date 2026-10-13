# 11. Install the definitions and the operators before the umbrella chart, in four releases

**Status:** Accepted, October 12, 2026. Supersedes the part of
[0001](0001-deploy-on-kubernetes-with-helm-on-kind.md) that has one
umbrella Helm chart install every component.

## Context

[0001](0001-deploy-on-kubernetes-with-helm-on-kind.md) installs every
component with one umbrella Helm chart, and Helm 4 at that
([PIN-7](../../spec/deployment.md#6-pins)). The chart installs operators
(cert-manager, CloudNativePG and its Barman Cloud plugin, and the Prometheus
operator in kube-prometheus-stack) and resources of the kinds they define:
the database's `Cluster` and `ImageCatalog`, the `ObjectStore` its backups
go to, monitors and alert rules, and the pipeline's own `Source`,
`Backfill`, and `SecAccess`
([0007](0007-control-the-pipeline-through-custom-resources.md)). Each kind
needs its custom resource definition (CRD) on the cluster before a resource
of it can be created, and each CRD needs upgrading when its operator is
upgraded.

Helm, through version 4.3.0, offers two places for a CRD, and neither serves
a chart that also creates resources of its kind:

- **A chart's `crds/` directory,** its subcharts' included, is installed
  before anything else on the first install, and never upgraded or deleted
  afterwards. The proposal to change that, "H4HIP: CRD updating", is still
  open.
- **A CRD in a chart's templates** is upgraded with its release. But Helm
  builds the whole release against the cluster's API before it creates
  anything, so a resource of a kind whose CRD is in the same release fails
  to build: "resource mapping not found ... ensure CRDs are installed
  first".

Even once its CRD exists, a resource can be refused at install. Helm
creates a release's resources in one pass, without waiting between them,
and its proposal for ordering them (HIP-0025) is a draft. cert-manager's and
CloudNativePG's admission webhooks have `failurePolicy: Fail`, so a
resource they admit is refused until their pods are ready: the Barman Cloud
plugin's chart renders a cert-manager `Issuer` and two `Certificate`s, and
the umbrella chart a CloudNativePG `Cluster`.

The charts put their CRDs in different places. cert-manager's,
CloudNativePG's, and the Barman Cloud plugin's are in their templates;
kube-prometheus-stack's are in a subchart's `crds/` directory, which it
offers to upgrade through a pre-upgrade Job it calls a preview. Garage
creates its own CRD at startup, through a ClusterRole that may create any
CRD, unless its chart is told to skip it. cert-manager's documentation says
never to embed it as a subchart of another chart, since it manages
cluster-scoped resources and must be installed exactly once.

Helm keeps each release in a Secret, which can hold 1 MiB. The CRDs of the
four operators come to about 940 KiB there, the Prometheus operator's alone
about 610 KiB, so they can't all be templates of one release.

## Options

- **One release, with workarounds.** Every CRD in a local subchart's
  `crds/` directory, so Helm installs them first, and a pre-upgrade hook
  Job that applies them on each upgrade, as kube-prometheus-stack's does.
  The plugin's namespace labeled to skip cert-manager's validation, and
  CloudNativePG's webhooks set to `Ignore`. Keeps 0001, but against
  cert-manager's advice, with two admission checks loosened, and CRDs
  upgraded by a Job outside Helm's own record of the release.
- **One chart, installed as two releases.** A value renders only the
  operators and their CRDs in the first release, and everything else in the
  second. Keeps one chart in name, but each release renders a different
  part of it, which every test must render both ways.
- **Three releases:** cert-manager; then CloudNativePG, the plugin, and the
  pipeline's own CRDs; then the umbrella chart, with the Prometheus
  operator's CRDs left in kube-prometheus-stack's `crds/` directory and
  upgraded by its preview Job. That Job adds about 290 KB to the umbrella
  chart's release, and the operators' monitors can't be rendered before it.
- **Four releases,** each from a chart of its own under `deploy/`, each
  installed only once the release before it is ready: the Prometheus
  operator's CRDs; cert-manager; the operators; and the umbrella chart.

## Decision

Four releases, installed and upgraded in this order, each with `--wait`:

1. **The Prometheus operator's CRDs,** from the Prometheus community's
   `prometheus-operator-crds` chart, which renders them as templates.
2. **cert-manager,** with its CRDs as templates. Its chart,
   `deploy/cert-manager/`, only pins and commits cert-manager's packaged
   chart, with the values it's installed with, and holds nothing else. It
   installs cert-manager once in the cluster, in its own release and
   namespace, which is the reason cert-manager's documentation gives for
   not embedding it in another chart.
3. **The operators:** CloudNativePG and the Barman Cloud plugin, with their
   CRDs, in the namespace the plugin must share with the operator; Garage's
   CRD; and the pipeline's own CRDs, those in `spec/schemas/`.
4. **The umbrella chart:** NATS, Garage, kube-prometheus-stack without its
   CRDs, the database, and later the pipeline's services and controls.

Every CRD is a template of the release before the first that needs it, so
Helm creates and upgrades it with that release, and keeps it when a release
is uninstalled. No release creates a resource of a kind whose CRD, or whose
admission webhook that refuses on failure, it installs itself.

## Consequences

- Upgrades go in the same order, so a CRD is upgraded before any resource
  that relies on a newer field of it.
- Each release stays well under Helm's limit, with room for the operators'
  CRDs to grow.
- The Prometheus operator's CRDs and kube-prometheus-stack are pinned
  separately, so their versions must agree, which a chart test checks.
- Garage is told to skip creating its CRD, which the operators' chart
  installs instead, so Garage needs only a Role in its own namespace, not a
  ClusterRole that may create any CRD.
- "Pinned in the umbrella chart" in 0001 becomes pinned in each of the four
  charts, with the same rules for every one
  ([PIN-1 to PIN-4](../../spec/deployment.md#6-pins)).
- A CRD marked to be kept outlives an uninstall, and with it every resource
  of its kind, such as the SecAccess's record of a stop. Removing one is a
  deliberate step.
- [0008](0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)
  keeps each component's monitors and alert rules in the umbrella chart.
  The operators' can stay there too, since the umbrella chart is installed
  last, once every CRD exists.
- CI and the owner install four releases instead of one, and the deployment
  specification takes a major version, since the umbrella chart's
  dependencies change.

## Sources

- [Helm: custom resource definitions](https://helm.sh/docs/chart_best_practices/custom_resource_definitions/)
- [Helm's install action, `pkg/action/install.go`](https://github.com/helm/helm/blob/v4.3.0/pkg/action/install.go)
- [H4HIP: CRD updating, helm/community#379](https://github.com/helm/community/pull/379)
- [HIP-0025: resource sequencing](https://github.com/helm/community/blob/main/hips/hip-0025.md)
- [cert-manager: installing with Helm](https://cert-manager.io/docs/installation/helm/)
- [cert-manager#4596: no matches for kind in a parent chart](https://github.com/cert-manager/cert-manager/issues/4596)
- [cert-manager: troubleshooting the webhook](https://cert-manager.io/docs/troubleshooting/webhook/)
- [cloudnative-pg/charts#814: CRDs not found as a dependency](https://github.com/cloudnative-pg/charts/issues/814)
- [kube-prometheus-stack: upgrading CRDs](https://github.com/prometheus-community/helm-charts/blob/main/charts/kube-prometheus-stack/UPGRADE.md)
- [prometheus-operator-crds](https://github.com/prometheus-community/helm-charts/tree/main/charts/prometheus-operator-crds)
- [Barman Cloud plugin: installation](https://cloudnative-pg.io/plugin-barman-cloud/docs/installation/)
- [Garage's Helm chart](https://git.deuxfleurs.fr/Deuxfleurs/garage/src/tag/v2.4.1/script/helm/garage) and [its CRD](https://git.deuxfleurs.fr/Deuxfleurs/garage/src/tag/v2.4.1/script/k8s/crd)
