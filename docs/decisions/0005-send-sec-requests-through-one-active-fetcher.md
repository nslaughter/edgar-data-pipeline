# 5. Send every SEC request through one active fetcher, held by a Lease

**Status:** Accepted, October 8, 2026.

## Context

The SEC asks automated tools for at most 10 requests a second. The store's
specification allows 5 a second from one process, one process at a time,
and stops on a `403`
([FETCH-6, FETCH-7, FETCH-9](../../spec/snapshots.md#10-fetching)). With
several pods, those limits must hold across the whole cluster, and a `403`
must stop all fetching, not one pod's.

## Options

- **One active fetcher, held by a Kubernetes Lease.** Two replicas; the one
  holding the Lease consumes fetch jobs from the queue, and the other takes
  over if it dies.
- **A limit per pod**, 5 divided by the number of replicas. It breaks when
  the replica count changes, and a `403` still needs coordination.
- **A shared token bucket**, in NATS's key-value store or PostgreSQL, from
  which every worker takes a token before each request. More moving parts,
  harder to test, and a `403` becomes a shared flag every worker must check.
- **An egress proxy with a global rate-limit service**, such as Envoy's.
  Realistic for many upstreams; heavy for one.

## Decision

One active fetcher. Other components queue fetch jobs; the fetcher holding
the Lease sends them, at most 5 a second, and the standby waits.

## Consequences

- "One process at a time" becomes "one Lease holder", which can be tested
  on kind by deleting the active pod.
- The whole pipeline is capped at 5 requests a second, half the SEC's limit.
- After a `403`, the fetcher stops sending, raises an alert, and waits for
  the operator; it doesn't resume on its own.
- A handover leaves a short pause in fetching, recorded as a gap like any
  other.
- The fetcher alone mounts the Secret holding `SEC_USER_AGENT`
  ([FETCH-2, ID-1](../../spec/snapshots.md#10-fetching)).
