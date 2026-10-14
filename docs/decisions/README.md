# Decision records

One record per significant choice: its context, the options considered, the
decision, its consequences, and the sources it relies on. Records are
numbered in order and never renumbered. Once merged, only a record's status
changes; a later record supersedes an earlier one instead of editing it.

A record's status is **Proposed** until the owner accepts it, then
**Accepted**, or **Superseded** by a later record.

| Record | Decision | Status |
| --- | --- | --- |
| [0001](0001-deploy-on-kubernetes-with-helm-on-kind.md) | Deploy on Kubernetes with Helm, on kind | Accepted; superseded in part by 0011 |
| [0002](0002-store-bodies-in-garage.md) | Store bodies in Garage | Accepted |
| [0003](0003-queue-work-in-nats-jetstream.md) | Queue work in NATS JetStream | Accepted |
| [0004](0004-keep-the-record-in-postgresql-18.md) | Keep the record and the facts in PostgreSQL 18, run by CloudNativePG | Accepted |
| [0005](0005-send-sec-requests-through-one-active-fetcher.md) | Send every SEC request through one active fetcher, held by a Lease | Accepted |
| [0006](0006-write-the-services-in-go-and-python.md) | Write the services in Go and Python, divided by whether they read EDGAR's content | Accepted |
| [0007](0007-control-the-pipeline-through-custom-resources.md) | Control the pipeline through custom resources, reconciled by the scheduler | Accepted |
| [0008](0008-monitor-with-prometheus-and-grafana-then-tempo-and-loki.md) | Monitor with Prometheus and Grafana, then Tempo and Loki | Accepted |
| [0009](0009-read-edgar-through-a-public-point-in-time-library.md) | Read EDGAR through a public point-in-time library, in its own repository | Accepted |
| [0010](0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md) | Poll each company's `submissions`, at the cadence its `Source` holds, and measure lag from the record | Accepted |
| [0011](0011-install-the-definitions-and-operators-before-the-umbrella-chart.md) | Install the definitions and the operators before the umbrella chart, in four releases | Accepted |
| [0012](0012-publish-the-documents-as-a-site-built-with-zensical.md) | Publish the documents as a site built with Zensical, served by GitHub Pages | Proposed |

Decisions still to come: scaling workers by queue depth, and the XBRL
parser.
