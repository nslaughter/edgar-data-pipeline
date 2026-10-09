# 7. Control the pipeline through custom resources, reconciled by the scheduler

**Status:** Accepted, October 9, 2026.

## Context

A running pipeline needs a few controls: pausing and resuming, resuming
after a `403`, each source's polling cadence, which companies are in scope,
and backfills of past filings. Each is rare and deliberate, and each changes
the record: a pause is a gap, and a new source widens the scope, which
`AGENTS.md` reserves for the owner. A control should therefore record who
used it and when, and survive a pod's restart.

[0005](0005-send-sec-requests-through-one-active-fetcher.md) has the fetcher
stop after a `403` and wait for a person, but doesn't say where the stop is
kept. Kept in the active fetcher's memory, it would be forgotten when the
standby takes over.

The pipeline also gets a dashboard
([0008](0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md)),
the easiest place to put a control.

## Options

- **Custom resources, reconciled by the scheduler as a controller.** Each
  control is a field of a Kubernetes resource: `kubectl apply` is the
  control, RBAC says who may use it, the API server keeps it through
  restarts, and the resource's status shows what the pipeline did.
- **Controls in the dashboard,** as buttons in Grafana or a web page of our
  own. They need their own login, permissions, and audit trail, and they
  put writes beside a view that is shared and left open.
- **An admin API with a command-line client,** on one of the Go services.
  Explicit, but it rebuilds the authentication, authorization, and history
  the API server already has.
- **Helm values and scaling:** cadence and scope in values, and a pause by
  scaling the fetcher to zero. No new code, but a pause looks like a
  failure, status is shown nowhere, and resuming after a `403` has no place
  to be recorded.
- **A separate operator** beside the scheduler: the same resources, with one
  more Deployment for work the scheduler exists to do.

## Decision

The scheduler is a controller, written in Go with controller-runtime
([0006](0006-write-the-services-in-go-and-python.md)), and the pipeline's
controls are three custom resources in an API group of the project's own:

- **`Source`:** one feed the pipeline polls, such as one company's
  `submissions` or the daily index, with its cadence and whether it is
  paused. Continuous and daily operation differ only in cadence. Its status
  holds the last successful poll and any open gap.
- **`Backfill`:** a company and a date range of past filings to fetch. The
  scheduler queues the fetches and records progress in its status, so a
  backfill resumes where it stopped after a crash.
- **`SecAccess`:** one per cluster, saying whether the pipeline may send
  requests to the SEC at all. The owner can pause it. After a `403`, the
  fetcher records the stop in its status, and fetching resumes only when the
  owner acknowledges that stop in its spec.

The dashboard is read-only; every control is one of these resources.

## Consequences

- RBAC says who may do what: the owner may change the resources' specs, and
  the scheduler and the fetcher may change only their status. In the
  cluster, "only when the owner asks" is a permission.
- The stop after a `403` survives a handover: the standby reads it from
  `SecAccess` and sends nothing until the owner acknowledges it.
- While fetching is paused or stopped, the scheduler queues nothing, and the
  time is recorded as a gap like any other.
- Backfill fetches are queued apart from polls, and the fetcher serves polls
  first, so a backfill can't delay the record of new filings.
- Status is a summary for people. The record in PostgreSQL stays
  authoritative, and the scheduler updates status from the events the other
  services publish.
- The scheduler runs as two replicas, one active through controller-runtime's
  leader election, which also uses a Lease. No pod is added.
- The resources' schemas are a contract of their own, versioned from
  `v1alpha1`. They are written in `spec/schemas/` first, and a test checks
  that the definitions generated from the Go types match them. The
  controller is tested against a real API server with envtest.
- Scope lives in two places: `data/scope.json` for the git store, and
  `Source` resources in the cluster. The next major version of
  `spec/snapshots.md` says how they relate.
- kube-state-metrics exports the resources' status to Prometheus, so each
  source's health and each backfill's progress appear in Grafana.
- No controller deploys the pipeline's own services, which Helm does, and
  none is in the data path: the scheduler never fetches and never writes the
  record.

## Sources

- [Kubernetes: custom resources](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
- [controller-runtime](https://github.com/kubernetes-sigs/controller-runtime)
- [The Kubebuilder book: envtest](https://book.kubebuilder.io/reference/envtest)
- [kube-state-metrics: custom resource state metrics](https://github.com/kubernetes/kube-state-metrics/blob/main/docs/metrics/extend/customresourcestate-metrics.md)
