# 3. Queue work in NATS JetStream

**Status:** Proposed, October 8, 2026.

## Context

In continuous operation, components hand work to each other: fetch jobs to
the fetcher, notices of new filings to the parse workers, and an event each
time a body is stored. That work has to survive a crashed pod or a restart,
be acknowledged when done, and be delivered again if a worker dies partway,
so the pipeline can resume after an outage. Memory matters
([0001](0001-deploy-on-kubernetes-with-helm-on-kind.md)).

## Options

- **NATS JetStream.** One small Go binary, with durable streams,
  acknowledgments and redelivery, work-queue retention, and a key-value
  store; its project maintains a Helm chart. Apache 2.0, in the CNCF. In
  2025 its steward, Synadia, sought to take it out of the CNCF and relicense
  it; the dispute was settled in May 2025 with NATS staying in the CNCF
  under Apache 2.0.
- **Kafka or Redpanda.** Durable logs built for far larger volumes; heavier
  to run, and more memory than this pipeline needs.
- **RabbitMQ.** Quorum queues; runs on the Erlang VM, with more memory than
  NATS.
- **A queue in PostgreSQL**, with `SELECT ... FOR UPDATE SKIP LOCKED`. No
  new component, but fetching would stop whenever the database fails over or
  is down, and the database would carry the polling load.

## Decision

NATS JetStream, with three replicas and its streams on persistent volumes.

## Consequences

- Fetching continues while PostgreSQL fails over: parse jobs wait in the
  stream and are delivered when the database is back.
- Queue depth, redeliveries, and consumer lag are metrics, and are what
  workers are scaled by.
- A message may be delivered more than once, so each step is safe to
  repeat: bodies are named by their SHA-256, and filings by their accession
  numbers.

## Sources

- [The Register: CNCF and Synadia on NATS](https://theregister.com/2025/05/02/cncf_synadia_nats)
- [The New Stack: CNCF and Synadia reach an agreement on NATS](https://thenewstack.io/cncf-and-synadia-reach-an-agreement-on-nats/)
- [CNCF: Protecting NATS and the integrity of open source](https://www.cncf.io/blog/2025/04/24/protecting-nats-and-the-integrity-of-open-source-cncfs-commitment-to-the-community)
