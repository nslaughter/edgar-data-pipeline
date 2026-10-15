# EDGAR data pipeline

A reproducible pipeline for SEC EDGAR filings: versioned snapshots of
EDGAR's feeds, a bulk import anyone can rerun on the same bytes, and
bitemporal queries over financial data as it was known on any past date.

I'm [Nathan Slaughter](https://nathanslaughter.com/), a software engineer with
experience building and operating data pipelines, observability systems, and
infrastructure. I help teams turn incoming data into stored datasets they can
inspect, query, and reprocess. This project applies that to public financial
filings, where when a number was published matters as much as what it says.

**Status:** Early. The repository holds the scripts that snapshot EDGAR's
feeds and verify the stored versions. The investigation of the source has
begun ([`investigation/edgar.md`](investigation/edgar.md)), and the design is
drafted ([`docs/design.md`](docs/design.md)); the pipeline's specification
follows them. The platform the pipeline runs on installs on kind, from the
Helm charts under [`deploy/`](deploy/); the pipeline's own services are still
to be built. The
data comes from the SEC's EDGAR system. This is an independent demonstration,
not client work, and it is not affiliated with or endorsed by the SEC.

The documents are published as a site,
[nslaughter.github.io/edgar-data-pipeline](https://nslaughter.github.io/edgar-data-pipeline/),
whose [status page](https://nslaughter.github.io/edgar-data-pipeline/docs/status/)
says which parts are built and which are only specified or planned.

## What this project will demonstrate

- **A bulk import anyone can rerun.** Every byte the import reads was fetched
  by a recorded request and is stored by its SHA-256. The import reads stored
  versions, never the live feed, so rerunning it on the same versions gives
  the same result.
- **Versioned feeds.** Each fetch of a feed is recorded, and changed content
  becomes a new version beside the old one, so a corrected or rewritten feed
  stays visible.
- **Bitemporal queries.** Each reported value keeps its fiscal period, when it
  was true, and the time each filing that reported it was accepted, when it
  became known. A query can then ask what was known on a past date, including
  values a later filing restated. The model is Martin Fowler's
  [Bitemporal History](https://martinfowler.com/articles/bitemporal-history.html).
- **A record that says where it wasn't looking.** Every poll is recorded, so
  coverage and gaps are part of the data. A filing first seen after an outage
  carries the outage's bounds, and its acceptance time still comes from the
  filing itself.
- **A pipeline on Kubernetes.** A scheduler, written as a Kubernetes
  controller, queues work in NATS JetStream; one fetcher sends every request
  to the SEC and stores each body in Garage; and workers parse each filing's
  facts into PostgreSQL 18. The services are written in Go and Python,
  controlled through custom resources, and installed with Helm on kind. The
  [design](docs/design.md) explains each part.
- **A pipeline you can watch.** Grafana shows the pipeline's health beside
  the record itself: coverage over time with its gaps, and each filing's
  times. One trace follows a filing from the poll that first saw it to its
  facts, across both languages.

## Fetching and versioning a feed

```sh
echo 'SEC_USER_AGENT="Name contact@example.com"' > .env.dev
uv run scripts/snapshot.py FEED URL [URL ...]
uv run scripts/verify.py -v
```

[`scripts/snapshot.py`](scripts/snapshot.py) fetches each URL and appends a
record to `data/feeds/FEED/versions.jsonl`: when the request was sent and
when its response arrived, the HTTP status, the response's `Date` and
validators, and the SHA-256 and length of the body. The body is stored
once, gzipped, under `data/objects/` by its SHA-256. A refetch sends the last
version's validators, so an unchanged feed costs the SEC little and adds a
record without a copy. Nothing stored is overwritten. The store is committed
to this repository, so it holds a deliberately small scope, a few companies'
filings and the feeds that announce them, listed in `data/scope.json`;
`snapshot.py` fetches only URLs the scope covers.
[`scripts/verify.py`](scripts/verify.py) checks the store's invariants:
every object against its SHA-256, every record, against the record's JSON
Schema too, the store's size against GitHub's limits, and, with `--since`,
that nothing committed was changed. `-v` lists each URL's versions.
[`spec/snapshots.md`](spec/snapshots.md) specifies the store: the layout,
each record's fields, what a version is, the scope, how fetches are made,
and the manifest each import writes to pin its inputs. It also specifies the
pipeline's store in the cluster, whose records of the scope's URLs the owner
copies into `data/`.

Both scripts use only Python's standard library.

## Installing the platform on kind

The platform is four Helm charts, installed as four releases in this order,
each once the one before it is ready
([`spec/deployment.md`](spec/deployment.md)): the Prometheus operator's
definitions; cert-manager; the operators, CloudNativePG and its Barman Cloud
plugin, with the remaining definitions; and the umbrella chart, with NATS
JetStream, Garage, PostgreSQL 18, and kube-prometheus-stack. It needs Docker,
kind, kubectl, and Helm 4.

```sh
kind create cluster --config deploy/kind.yaml
helm install prometheus-crds deploy/prometheus-crds --namespace edgar --create-namespace --wait
helm install cert-manager deploy/cert-manager --namespace cert-manager --create-namespace --wait
helm install operators deploy/operators --namespace cnpg-system --create-namespace --wait
helm install edgar deploy/chart --namespace edgar --values deploy/edgar-values.yaml --wait --timeout 20m
EDGAR_KUBE_CONTEXT=kind-edgar uv run pytest tests/test_kind.py
```

`deploy/edgar-values.yaml`, which git ignores, holds what the umbrella chart
requires and no committed file sets: each credential, and a name for the
database's archive in Garage, new for each cluster.
[`deploy/chart/values.schema.json`](deploy/chart/values.schema.json) gives
their places and forms, and CI's [workflow](.github/workflows/ci.yml)
generates a set for each cluster it creates. An upgrade runs `helm upgrade`
in the same order. `deploy/kind.yaml` keeps every volume's data in
`/var/tmp/edgar-kind/volumes` on the host, so a cluster created again finds
it. The tests of the installed charts run against the cluster the context
names; the other tests render the charts, and need Helm and jq.

## Using EDGAR data

The SEC states that "information presented on sec.gov is considered public
information and may be copied or further distributed by users of the web site
without the SEC's permission", and asks for the agency to be cited as the
source ([SEC privacy and website policies](https://www.sec.gov/about/privacy-information)).
Data in this repository comes from the U.S. Securities and Exchange
Commission's EDGAR system.

The SEC's guidance on
[accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
sets a maximum of 10 requests per second and asks automated tools to declare
a user agent naming who runs them and a contact address. Supply your own in
`SEC_USER_AGENT`, in the environment or in `.env.dev`, which git ignores;
the repository ships none. `snapshot.py` refuses to run
without it, never records it, sends at most 5 requests a second, and stops on
a 403.

## Related projects and writing

- [edgar-asof](https://github.com/nslaughter/edgar-asof): the point-in-time
  library this pipeline's Python services will read EDGAR through
  ([decision 0009](docs/decisions/0009-read-edgar-through-a-public-point-in-time-library.md)).
- [financial-data-api](https://github.com/nslaughter/financial-data-api): a
  REST API for a synthetic dataset with revision history and queries as of a
  past date.
- *The timestamps that make financial data usable*: an article on the time
  model this pipeline records, in preparation. I'll link it here when it is
  published.

## Work with me on a dataset your team can retain and use

I build ingestion and storage pipelines around an agreed source, destination,
and research or analysis workflow.

[Discuss a data ingestion project](https://www.linkedin.com/in/nathan-slaughter)
with the source, intended storage, approximate volume, and freshness and
recovery needs.

## License

The code and documents are under the [MIT License](LICENSE). Data under
`data/` comes from the SEC's EDGAR system, as [Using EDGAR data](#using-edgar-data)
describes.
