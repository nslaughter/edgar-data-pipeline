# 2. Store bodies in Garage

**Status:** Accepted, October 8, 2026.

## Context

In continuous operation the store outgrows what git should hold, which the
store's specification anticipated
([open question 2](../../spec/snapshots.md#13-open-questions)). Bodies move
to an S3-compatible object store in the cluster, named by SHA-256 as under
`data/objects/`. The store also holds the facts' Parquet exports and the
database's backups ([0004](0004-keep-the-record-in-postgresql-18.md)).

Memory matters most, since kind's nodes share one machine
([0001](0001-deploy-on-kubernetes-with-helm-on-kind.md)). The store must be
maintained, and its license must let us run it unmodified.

## Options

- **MinIO.** Under AGPLv3, which would be fine, but no longer maintained: it
  stopped publishing binaries and images in October 2025, went into
  maintenance mode in December 2025, and its repository has since been
  archived. Ruled out.
- **SeaweedFS.** Apache 2.0, with fuller S3 support, including versioning
  and object lock. It runs as several processes (master, volume server,
  filer, S3 gateway), used about 500 MB in one stress test, and its volume
  servers keep an index in memory that grows with the number of objects.
- **Garage.** AGPLv3; one Rust binary that replicates across nodes. It idled
  under 20 MB and stayed under 200 MB in one stress test; another guide puts
  it at 100 to 200 MB idle. It has no object versioning and no object lock.

## Decision

Garage, with three replicas across kind's worker nodes and a replication
factor of 3, so the loss of a node can be shown. It is installed from the
Helm chart in Garage's source tree at a pinned release. A post-install Job
assigns the cluster layout and creates the buckets and keys, so a Helm
install yields a working store.

## Consequences

- Storage can't prevent an object from being overwritten or deleted.
  Content addressing detects either: an object must hash to its name, and
  every record must find its object
  ([OBJ-1, REC-3](../../spec/snapshots.md#5-objects)). The store's
  guarantees rest on that detection, not on prevention.
- Garage's chart isn't published to a chart repository, so it is vendored
  at a pinned release and upgraded deliberately.
- Run unmodified and reached over the S3 API, Garage places no AGPL
  obligations on the pipeline's code. A client whose policy forbids AGPL
  software would use SeaweedFS, which also offers object lock.
- CloudNativePG's backup plugin has been tested against MinIO, not Garage,
  so the database's backups must be restored on kind before they are relied
  on.

## Sources

- [MinIO Is Dead, Long Live MinIO](https://vonng.com/en/db/minio-resurrect/)
- [minio/minio on GitHub](https://github.com/minio/minio)
- [Garage: introduction](https://garagehq.deuxfleurs.fr/intro.html)
- [Garage: S3 compatibility status](https://garagehq.deuxfleurs.fr/reference_manual/s3_compatibility.html)
- [Garage: object lock feature request](https://git.deuxfleurs.fr/Deuxfleurs/garage/issues/1127)
- [Garage: deploying on Kubernetes](https://garagehq.deuxfleurs.fr/documentation/cookbook/kubernetes)
- [Garage resource guide](https://ramnode.com/guides/garage)
- [VSHN ADR 0050: alternatives to MinIO](https://kb.vshn.ch/app-catalog/adr/0050-alternatives-to-minio-for-s3-compatible-object-storage.html)
- [Garage vs MinIO vs AWS S3](https://glukhov.org/data-infrastructure/object-storage/garage-vs-minio-vs-s3/)
- [Alternatives to MinIO for single-node local S3](https://rmoff.net/2026/01/14/alternatives-to-minio-for-single-node-local-s3/)
- [Deploying SeaweedFS on Kubernetes](https://oneuptime.com/blog/post/2026-02-09-seaweedfs-distributed-storage/markdown)
- [CloudNativePG: Barman Cloud plugin](https://cloudnative-pg.io/plugin-barman-cloud/docs/intro/)
