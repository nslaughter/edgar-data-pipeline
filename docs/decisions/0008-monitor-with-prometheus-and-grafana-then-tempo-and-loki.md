# 8. Monitor with Prometheus and Grafana, then Tempo and Loki

**Status:** Proposed, October 9, 2026.

## Context

The design lists what the pipeline must report: each source's polls, the
delay from acceptance to first seen, open gaps, queue depth and
redeliveries, the fetcher's requests and `403`s and which pod holds the
Lease, and the database's replication and backups. A dashboard must also
show the record itself: coverage over time with its gaps, and each filing's
times. CloudNativePG, NATS, and Garage each expose Prometheus metrics.
Monitoring is installed as it would be in production, and memory is the
scarce resource on kind
([0001](0001-deploy-on-kubernetes-with-helm-on-kind.md)).

## Options

- **kube-prometheus-stack,** the Prometheus community's chart: the
  Prometheus operator, Prometheus, Alertmanager, Grafana, kube-state-metrics,
  and node-exporter. Each part of the umbrella chart declares its own
  scraping and alert rules as resources (ServiceMonitor, PodMonitor,
  PrometheusRule).
- **Prometheus and Grafana without the operator.** One pod fewer, but every
  scrape target and rule is written by hand in one central configuration,
  instead of beside the component it watches.
- **VictoriaMetrics, with its operator.** Less memory and disk than
  Prometheus, and its operator converts the Prometheus operator's resources,
  so charts that ship them still work. Less familiar to the teams this
  project addresses.
- **Grafana's all-in-one image,** `grafana/otel-lgtm`: metrics, logs, and
  traces in one container. Its project says it is meant for development,
  demos, and testing, so it doesn't model production.
- **For traces, Tempo or Jaeger.** Both work with OpenTelemetry. Tempo is
  built to be queried from Grafana, linked from metrics and logs; Jaeger
  would bring a second UI.
- **For logs, Loki or none.** `kubectl logs` is enough on kind, but it loses
  a deleted pod's logs and can't search every service for one accession
  number.

## Decision

- **kube-prometheus-stack,** with the defaults kind doesn't need turned off,
  such as scraping the control plane. Each component's monitors and alert
  rules live in the umbrella chart beside it.
- **Grafana is read-only**
  ([0007](0007-control-the-pipeline-through-custom-resources.md)). Its
  dashboards are provisioned from the chart, with two data sources:
  Prometheus for the pipeline, and PostgreSQL for the record, through a role
  that may read only views made for the dashboards.
- **Tempo next, then Loki fed by Grafana Alloy,** in that order. The services
  send traces by OTLP and carry each trace through NATS in message headers,
  so one trace follows a filing from poll to facts across Go and Python
  ([0006](0006-write-the-services-in-go-and-python.md)). Logs are JSON and
  carry the filing's accession number. Alloy rather than Promtail, which
  reached end of life in March 2026.
- **Tempo and Loki keep their data on their own volumes,** not in Garage, so
  an outage of Garage can still be observed.
- **Alertmanager has no receiver outside the cluster** unless the owner asks
  for one. Alerts show in Grafana and Alertmanager.

## Consequences

- The alerts the design names, on a `403`, on a gap longer than its source's
  cadence, and on a backup more than a day old, are specified and tested
  with `promtool test rules` in CI, like any other rule.
- The metrics and traces each service emits are a contract the dashboards
  and alerts rely on, so they get a specification of their own.
- The views Grafana reads are part of the record's schema, so changing them
  is a spec change.
- Memory: an estimated 1 GB for kube-prometheus-stack, most of it
  Prometheus, and a few hundred MB each for Tempo and for Loki with Alloy.
  Each is measured on kind before its limits are set.
- The services speak OTLP, so a collector can be put between them and Tempo
  without changing them.

## Sources

- [kube-prometheus-stack](https://github.com/prometheus-community/helm-charts/tree/main/charts/kube-prometheus-stack)
- [Prometheus: unit testing for rules](https://prometheus.io/docs/prometheus/latest/configuration/unit_testing_rules/)
- [VictoriaMetrics operator: Prometheus objects](https://docs.victoriametrics.com/operator/integrations/prometheus/)
- [grafana/docker-otel-lgtm](https://github.com/grafana/docker-otel-lgtm)
- [Grafana Tempo](https://grafana.com/docs/tempo/latest/), [Grafana Loki](https://grafana.com/docs/loki/latest/), and [Grafana Alloy](https://grafana.com/docs/alloy/latest/)
- [Promtail's end of life](https://grafana.com/docs/loki/latest/send-data/promtail/)
- [OpenTelemetry: propagators API](https://opentelemetry.io/docs/specs/otel/context/api-propagators/)
- [Garage: monitoring](https://garagehq.deuxfleurs.fr/documentation/reference-manual/monitoring/)
