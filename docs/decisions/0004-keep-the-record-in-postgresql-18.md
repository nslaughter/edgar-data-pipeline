# 4. Keep the record and the facts in PostgreSQL 18, run by CloudNativePG

**Status:** Accepted, October 8, 2026.

## Context

The pipeline needs one place for its fetch records, the filings it has seen
and their times, its coverage and gaps, and the facts parsed from each
filing, and it must answer "as of" queries: what was known on a past date,
by fiscal period and acceptance time. The scale is small, millions of rows
even after a backfill, so speed doesn't decide; memory, integrity, and what
the project demonstrates do.

The times are the SEC's, not the database's. A fact's valid time is the
fiscal period it describes, and its transaction time is the acceptance time
of the filing that reported it, read from the filing
([SRC-1](../../investigation/edgar.md#findings-so-far)).

## Options

- **PostgreSQL alone.** One transactional store. Writes are checked as they
  happen: by constraints, by a role that can insert but not update or
  delete, and, from PostgreSQL 18, by temporal keys (`WITHOUT OVERLAPS`).
  It is the largest steady memory cost in the design.
- **DuckDB alone.** Facts as write-once Parquet files in Garage, records in
  a NATS stream, and DuckDB embedded in the query service. The lightest
  option, with no database server, but integrity is checked after the fact
  rather than enforced, and records and facts are joined across files.
- **Both:** PostgreSQL for records, Parquet and DuckDB for facts. Rejected:
  each of its benefits comes more cheaply from one engine, and it adds
  queries across two stores, reconciliation between them, and two of every
  backup, migration, and metric.
- **A natively bitemporal database, such as XTDB.** Its transaction time is
  when the database received a write, but ours is when the SEC accepted the
  filing, so it would need explicit time columns anyway; it is also heavier
  and less familiar to the teams this project addresses.

## Decision

PostgreSQL 18, run by the CloudNativePG operator with three instances. The
cluster definition pins the major version to 18 through an image catalog,
which names the exact 18.x image by digest. Records, filings, coverage, and
facts share one database. Times are explicit columns: the fiscal period as
valid time, the filing's acceptance time as transaction time, and the
pipeline's own first-seen time with the bounds of any gap; never the
database's write time. A deterministic Parquet export of the facts, written
to Garage, makes the dataset portable.

## Consequences

- One transaction records a filing, its parse, and its facts, so they can't
  disagree.
- The pipeline's role can insert but not update or delete, so the database
  itself enforces the append-only record.
- The pin to 18 becomes a specification statement, checked by a chart test.
  A minor upgrade changes the image catalog; a major upgrade is a new
  decision record.
- Three always-on instances and the operator are the design's largest steady
  memory cost; they are sized on kind.
- Failover, point-in-time recovery, and backups to Garage can be
  demonstrated. Backups to Garage need a restore test first
  ([0002](0002-store-bodies-in-garage.md)).
- Anyone can query the export with DuckDB, pandas, or Polars without the
  cluster. Hashed, the export is also how a rerun of an import is compared
  with the original, answering the store's
  [open question 3](../../spec/snapshots.md#13-open-questions).

## Sources

- [CloudNativePG 1.27 release notes](https://cloudnative-pg.io/docs/1.28/release_notes/v1.27)
- [CloudNativePG 1.27.1 released](https://cloudnative-pg.io/releases/cloudnative-pg-1-27.1-released/)
- [cloudnative-pg/postgres-containers](https://github.com/cloudnative-pg/postgres-containers)
- [Temporal constraints in PostgreSQL 18 (Neon)](https://neon.com/postgresql/18/temporal-constraints)
- [Temporal constraints in PostgreSQL 18 (Better Stack)](https://betterstack.com/community/guides/databases/postgres-temporal-constraints/)
- [CloudNativePG: Barman Cloud plugin](https://cloudnative-pg.io/plugin-barman-cloud/docs/intro/)
