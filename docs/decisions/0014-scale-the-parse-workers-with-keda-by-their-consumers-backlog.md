# 14. Scale the parse workers with KEDA, by their consumer's backlog

**Status:** Proposed, October 31, 2026.

## Context

The design runs the parse workers as a Deployment scaled by queue depth
([components](../design.md#components)), and
[0003](0003-queue-work-in-nats-jetstream.md) makes queue depth,
redeliveries, and consumer lag what workers are scaled by.
[0001](0001-deploy-on-kubernetes-with-helm-on-kind.md) has each component
show how it's scaled, as well as deployed and recovered. Neither says how
the Deployment follows its queue, or between how many replicas.

**Their queue.** The parse workers read filing notices from `NOTICES`, a
stream kept by interest, through their durable consumer,
`parse-workers-notices`: a pull consumer that any replica may read from,
whose notice, if not acknowledged within 30 seconds, is delivered again,
with no limit on how often
([NATS-5](../../spec/deployment.md#durable-consumers)). The consumer is made
before the first notice is published, so it keeps every notice from the
discoverer's first until the parse workers acknowledge it. For each
consumer, JetStream reports the messages it hasn't delivered yet, and
those it has delivered that aren't acknowledged, through each server's
monitoring port.

**Their work** is rare, and comes in bursts. The discoverer publishes a
notice of each filing once its files are held, whether or not its index
page names an instance
([FILE-5](../../spec/notices.md#7-a-filings-files)), and the parse
workers' consumer takes every filing notice. The three companies filed
126 times in the 13 weeks to October 7, 2026, mostly Form 4s
([0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)),
about 1.4 filings a day. A Backfill brings every filing its company's
lists name in its range of dates
([BACKFILL-1](../../spec/controls.md#7-backfill)), whatever its form.
Over the three companies' listed history, that's 9,689 filings, of which
484, 5.0%, have an instance, and 208 of those are their XBRL 10-Ks,
10-Qs, and amendments (SRC-19, SRC-20). The stored instances of their
10-Ks and 10-Qs are 0.9 MB to 10.9 MB (SRC-18). Until the parser is
chosen ([T12](../plan.md#facts)) and the workers are measured, the
[estimates](../explanation/estimates.md#cpu-memory-and-storage) assume
0.5 to 2 seconds an instance, and 150 to 250 MB a worker with lxml, or
0.5 to 2 GB with Arelle.

**How fast notices come.** On average, no faster than the fetcher
fetches their filings' files, at 5 requests a second
([FETCH-6](../../spec/snapshots.md#10-fetching)): two for a filing
without an instance, its header and its index page, and three for one
with an instance
([FILE-1 to FILE-3](../../spec/notices.md#7-a-filings-files)). So a run
of filings with an instance, each fetched from its header to its
instance, averages at most about 1.7 notices a second. That's an
average, not a ceiling. The
fetcher takes a Backfill's jobs in the order they're queued
([JOB-4](../../spec/snapshots.md#jobs-and-events)), and the discoverer
queues an instance's fetch only once it has read the index page that
names it, behind the headers and index pages already queued
([FILE-3](../../spec/notices.md#7-a-filings-files),
[LIST-2](../../spec/notices.md#8-backfills)). Once those are fetched,
each request for an instance completes a filing, so notices with an
instance come at up to 5 a second. They come faster still when the
discoverer catches up on events that waited for it, as after a restart,
since publishing a notice waits on no request.

**The cluster.** kind runs each node as a container on one machine, so
memory is scarce ([0001](0001-deploy-on-kubernetes-with-helm-on-kind.md)).
Its nodes run Kubernetes 1.36.1
([PIN-6](../../spec/deployment.md#6-pins)). There, a
HorizontalPodAutoscaler can't go below one replica unless the feature gate
`HPAScaleToZero` is turned on, which is alpha and off by default through
1.36, and beta and on by default from 1.37. A custom resource's definition
must be installed by an earlier release than the resource
([0011](0011-install-the-definitions-and-operators-before-the-umbrella-chart.md),
[CRD-3](../../spec/deployment.md#definitions)).

## Options

- **KEDA, with its NATS JetStream scaler.** An operator that reads a
  JetStream consumer's backlog from NATS's monitoring port, scales a
  Deployment from zero to one itself, and creates a HorizontalPodAutoscaler
  that scales it from one up, through an external metrics server of its
  own. It finds the consumer's leader among a cluster's servers and reads
  the backlog there. A graduated CNCF project, under Apache 2.0, with a
  chart its project maintains that renders its definitions as templates.
  It adds three pods, the operator, its metrics server, and its admission
  webhooks, each requesting 100 MiB by its chart's default, and six
  definitions to the cluster. Its operator's grants are broad: at its
  chart's defaults, its ClusterRole grants `get` on every resource of
  every API group, the pipeline's controls among them, which
  [RBAC-4](../../spec/controls.md#9-who-may-change-what) forbids, and
  `list` and `watch` on every Secret in the cluster. Two of its chart's
  values narrow both, but leave it able to scale every Deployment in the
  cluster, where the scheduler would need a grant to scale one.
- **A HorizontalPodAutoscaler on an external metric, served from
  Prometheus.** NATS's exporter, a sidecar NATS's chart offers but doesn't
  enable, exports the consumer's backlog; Prometheus scrapes it, and
  prometheus-adapter serves it as an external metric. No new definitions,
  but scaling then waits on Prometheus's scrape every 30 seconds, and stops
  while Prometheus's one replica
  ([MON-4](../../spec/deployment.md#monitoring)) is down. It can't scale to
  zero on Kubernetes 1.36. prometheus-adapter's last release is v0.12.0, of
  May 2024.
- **The scheduler scales them.** The scheduler, a Go controller already
  ([0007](0007-control-the-pipeline-through-custom-resources.md)), reads
  the consumer's backlog from JetStream and sets the Deployment's replicas.
  No new component, but it's autoscaling code to write and test that KEDA
  already has; the scheduler gains a grant to scale a Deployment; and
  parsing comes to depend on its active replica.
- **A HorizontalPodAutoscaler on CPU.** Kubernetes's own, but it needs
  metrics-server, which the charts don't install; it follows work already
  running rather than work waiting; and it can't scale from zero.
- **A fixed number of replicas.** The simplest: one worker parses the 484
  instances of the three companies' listed history in 4 to 16 minutes at
  the estimated rate. But the workers wouldn't scale by queue depth, as
  the design and 0003 have them, and kind would show no scaling, which
  0001 asks of each component.

## Decision

KEDA, with its NATS JetStream scaler, scaling the parse workers from zero
to three by their consumer's backlog:

1. **Where it's installed.** KEDA's chart is a dependency of the operators'
   chart, `deploy/operators/`, the third release, so its definitions are on
   the cluster before the umbrella chart renders a resource of their kinds
   ([0011](0011-install-the-definitions-and-operators-before-the-umbrella-chart.md)).
   The operators' chart sets KEDA's value
   `rbac.enabledCustomScaledRefKinds` to `false`, so its operator's
   ClusterRole keeps only its fixed rules, which let it scale Deployments
   and StatefulSets, all the parse workers need, and grant nothing on the
   pipeline's controls
   ([RBAC-4](../../spec/controls.md#9-who-may-change-what)). It sets
   `permissions.operator.restrict.secret` to `true`, so the operator
   reads Secrets only in its own namespace, not those in the release
   namespace that hold the database roles' passwords, the Garage keys,
   and the user agent that only the fetcher's containers read
   ([ID-2](../../spec/snapshots.md#the-fetcher)).
2. **What scales them.** The umbrella chart renders a `ScaledObject` of the
   parse workers' Deployment, with one trigger of the scaler: the stream
   `NOTICES`, the consumer `parse-workers-notices`, and NATS's default
   account, `$G`, since NATS asks no credential in this version
   ([§9](../../spec/deployment.md#9-later-versions)). The scaler reads
   NATS's monitoring port through NATS's headless Service, which names each
   server, so it can find the consumer's leader.
3. **The backlog.** The consumer's notices not yet delivered, with those
   delivered and not yet acknowledged, which is what the scaler reads.
4. **From zero.** At least zero replicas: any notice in the backlog starts
   one worker, and the workers scale back to zero 5 minutes after the
   backlog last held one, KEDA's default.
5. **To three.** At most three replicas, one for each 10 notices in the
   backlog, KEDA's default. The backlog counts every filing notice, and
   most carry no instance: at the three companies' 5.0%, 10 notices hold
   half an instance on average, 0.25 to 1 second of one worker's work at
   the estimated 0.5 to 2 seconds an instance, while 10 notices that each
   carry one hold 5 to 20 seconds. Three workers parse 1.5 to 6 instances
   a second, and hold 0.45 to 0.75 GB with lxml. A burst of instances can
   come faster than that, so it waits in the backlog until they drain it:
   the 484 instances of the three companies' listed history take three
   workers 1.3 to 5.4 minutes at the estimated rate, where one would take
   4 to 16.
6. **How soon.** KEDA checks the backlog every 10 seconds, rather than its
   default 30, so a filing's notice waits for a worker to be started no
   longer than a Source's default cadence
   ([0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)),
   and then for the worker's pod to start.

The bounds rest on the estimates' assumptions. The parser that T12
chooses, and the workers' measurements on kind, may move them: Arelle's
0.5 to 2 GB a worker would put three workers at 1.5 to 6 GB. The view of
each filing's times from acceptance to facts available
([T32](../plan.md#facts)) may move the floor to one, if starting from zero
delays facts more than a running worker is worth.

## Consequences

- **The idle workers hold no memory.** Between filings, which come about
  1.4 a day, no worker runs. The first filing after 5 idle minutes waits
  for KEDA's next check and a pod's start before its facts, which the view
  of each filing's times from acceptance to facts available shows
  ([T32](../plan.md#facts)).
- **Notices with nothing to parse can start every worker.** The scaler
  counts notices, not instances. So a burst of notices without one, such
  as a Backfill's Form 4s gathering while the first worker starts, can
  start all three workers, which then stay for the
  HorizontalPodAutoscaler's default 5 minutes before it scales them down:
  up to 0.75 GB with lxml, or 6 GB with Arelle. A filing notice names an
  instance only if its filing has one
  ([§9 of the notices](../../spec/notices.md#9-messages-and-processing));
  what a worker does with one that names none is the facts
  specification's to say ([T17](../plan.md#facts)).
- **KEDA joins the operators' release.** Its definitions come to about
  0.77 MB as templates, 0.67 MB of them `ScaledJob`'s. Helm stores a
  release compressed, with its chart's templates beside the rendered
  manifests; rendered and compressed, KEDA's chart comes to about 0.12 MB,
  and the operators' chart now to about 0.23 MB, so the release stays
  within Helm's 1 MiB. KEDA runs in the release's namespace, `cnpg-system`,
  and adds three pods, each requesting 100 MiB by its chart's default,
  which the estimates' totals don't include until the owner accepts this
  record.
- **The cluster's external metrics API is KEDA's.** KEDA serves it
  through an `APIService` that only one server in a cluster can hold, so
  prometheus-adapter couldn't serve external metrics beside it.
- **KEDA's operator still holds grants across the cluster.** With the two
  values above, it may scale every Deployment and StatefulSet; make,
  change, and delete any HorizontalPodAutoscaler and Job; and read, make,
  and change the Secrets in `cnpg-system`, among them the CloudNativePG
  operator's and the Barman Cloud plugin's. While it makes its own
  certificates, as its chart does by default, it may also change every
  validating webhook configuration and `APIService` in the cluster, to
  write its certificate authority into its own; it wouldn't need to if
  cert-manager, which the second release installs, issued them. A release
  of KEDA's own, in a namespace of its own, would keep the other
  operators' Secrets from it, but would make a fifth release
  ([0011](0011-install-the-definitions-and-operators-before-the-umbrella-chart.md)).
- **0003's consequence, as it applies here.** The backlog holds the
  consumer's lag behind its stream, the queue's depth for the parse
  workers, and the notices awaiting their acknowledgment, among them those
  delivered again. The count of redeliveries isn't an input to the
  scaling; it stays a metric.
- **A notice never acknowledged keeps one worker running.** Since a notice
  is delivered again without limit
  ([NATS-5](../../spec/deployment.md#durable-consumers)), a notice whose
  parse always fails keeps the backlog above zero, so the workers never
  scale to zero. What a worker does with a parse that fails is the facts
  specification's to say ([T17](../plan.md#facts)).
- **A worker stopped partway adds nothing.** When the autoscaler removes a
  replica in the middle of a parse, its notice isn't acknowledged and is
  delivered again after 30 seconds, and its facts' transaction isn't
  committed. How a worker stops, and what an interrupted import leaves
  ([IMP-3](../../spec/snapshots.md#11-imports)), is the facts
  specification's to say.
- **Parsing waits while KEDA can't read the backlog.** While the workers
  are at zero and KEDA's operator or NATS's monitoring port is down,
  notices wait in `NOTICES` until a worker starts, and nothing is lost.
- **A version of the deployment specification states it.** KEDA's chart,
  pinned; its definitions with
  [CRD-4](../../spec/deployment.md#definitions)'s annotation, which its
  chart's values add; its images by digest
  ([PIN-4](../../spec/deployment.md#6-pins)), which its chart names by tag
  alone; the two values that narrow its operator's grants, the first of
  which RBAC-4's test checks, since it renders every release; whether
  cert-manager issues KEDA's certificates; the `ScaledObject`, with the
  bounds and timings above; a parse workers' Deployment that leaves its
  replicas to the autoscaler, so a `helm upgrade` doesn't reset them; and
  tests on kind that a filing notice starts a worker and the workers
  return to zero. The telemetry's version for the facts,
  [T32](../plan.md#facts), says which of KEDA's metrics Prometheus
  scrapes, and whether an alert fires when the scaler can't read the
  backlog. The deployment's version needs a task in the plan, before
  [T25](../plan.md#facts) builds the parse workers.

## Sources

- [KEDA: the NATS JetStream scaler](https://keda.sh/docs/2.21/scalers/nats-jetstream/),
  and [its source at v2.21.0](https://github.com/kedacore/keda/blob/v2.21.0/pkg/scalers/nats_jetstream_scaler.go),
  which reads `/jsz` on the consumer's leader, and takes the backlog as
  the consumer's `num_pending` and `num_ack_pending`.
- [KEDA: the `ScaledObject` specification](https://keda.sh/docs/2.21/reference/scaledobject-spec/),
  for the polling interval, the cooldown period, and the replicas' bounds.
- [KEDA's chart, version 2.21.0](https://github.com/kedacore/charts/tree/v2.21.0/keda),
  for its definitions as templates, its pods' resource requests, its
  images, and its operator's roles, as `templates/manager/clusterrole.yaml`
  and `templates/manager/minimal-rbac.yaml` render them from its values.
- [CNCF: the graduation of KEDA](https://www.cncf.io/announcements/2023/08/22/cloud-native-computing-foundation-announces-graduation-of-kubernetes-autoscaler-keda/),
  August 22, 2023.
- [Kubernetes: the feature gate `HPAScaleToZero`](https://github.com/kubernetes/website/blob/main/content/en/docs/reference/command-line-tools-reference/feature-gates/HPAScaleToZero.md),
  and [Kubernetes v1.37: scale workloads to zero with HorizontalPodAutoscaler](https://kubernetes.io/blog/2026/09/02/kubernetes-v1-37-hpa-scale-to-zero-beta/).
- [Kubernetes: the HorizontalPodAutoscaler's default behavior](https://kubernetes.io/docs/concepts/workloads/autoscaling/horizontal-pod-autoscale/#default-behavior),
  whose scale-down waits 300 seconds after the metric falls.
- [prometheus-adapter's releases](https://github.com/kubernetes-sigs/prometheus-adapter/releases).
- NATS's chart, at the version the umbrella chart pins, for the monitoring
  port, 8222, which it enables by default, and its exporter, which it
  doesn't.
- The findings SRC-18, SRC-19, and SRC-20 in
  [`investigation/edgar.md`](../../investigation/edgar.md#findings-so-far).
