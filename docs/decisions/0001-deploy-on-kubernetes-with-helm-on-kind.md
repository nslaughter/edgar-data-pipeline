# 1. Deploy on Kubernetes with Helm, on kind

**Status:** Accepted, October 8, 2026. Superseded in part by
[0011](0011-install-the-definitions-and-operators-before-the-umbrella-chart.md),
October 12, 2026: four charts, installed in order, in place of one umbrella
chart.

## Context

The pipeline demonstrates a system a team would run, not a script on a
laptop; the README has promised a pipeline on Kubernetes since the first
commit. Each component has to show how it behaves in production: how it is
deployed, configured, scaled, and recovered, and what it reports. It also has
to run on one machine and in CI.

## Options

- **Kubernetes on kind, installed with Helm.** A real API server, scheduler,
  and controllers, in containers on one machine. The same charts would
  install on a managed cluster.
- **Docker Compose.** Simpler, but without scheduling, Leases, CronJobs, or
  operators, so much of what a production team relies on couldn't be shown.
- **A managed cluster.** Real, but it costs money to keep running and can't
  run in CI the way kind can.

## Decision

Every component runs on Kubernetes, installed by one umbrella Helm chart,
with kind as the cluster for development and CI. Each component is deployed
as it would be in production: from the chart or operator its own project
maintains, with its own Secrets, storage, probes, resource requests and
limits, and metrics.

Bitnami's charts are avoided. Since August and September 2025, Bitnami's
free catalog offers a reduced set of images under the `latest` tag only, and
earlier images moved to a legacy repository that no longer receives updates.

## Consequences

- Charts, operators, and images are pinned in the umbrella chart, so an
  upgrade is a reviewed change.
- kind's nodes share one machine, so memory is the scarce resource. Each
  component's requests and limits are measured on kind, and memory weighs
  heavily in every later decision.
- CI can install the whole chart on kind and run end-to-end tests against a
  fake EDGAR that serves stored versions, so tests still never reach the
  network.
- A kind cluster is disposable. Its persistent volumes are kept on a host
  directory, so recreating the cluster keeps the stored data, and the git
  store remains the reproducible sample outside it.

## Sources

- [Major changes to Bitnami's container image catalog](https://enterprise.devtron.ai/portal/en/kb/articles/major-changes-coming-to-bitnami-s-container-image-catalog)
- [Bitnami application catalog: important changes](https://evoila.com/blog/bitnami-application-catalog-important-changes-take-action-now/)
