# Specification: the snapshot store

**Status:** Version 1.0.0, draft, October 12, 2026. This document governs
the pipeline's two stores of what it fetches from the SEC, and every
program that writes or reads them. The git store, everything under `data/`,
is written by `scripts/snapshot.py` and by the transfer from the cluster,
checked by `scripts/verify.py`, and read by imports. The cluster store,
objects in Garage and records in PostgreSQL, is written by the fetcher from
the jobs that the scheduler and the discoverer queue, and read by the
pipeline's other services. Where code and this document disagree, the code
is wrong or this document needs a new version; say which before changing
either.

Version 1.0.0 extends the store to the cluster. It rests on the decisions
to store bodies in Garage ([0002]), to queue work in NATS JetStream
([0003]), to keep the record in PostgreSQL 18 ([0004]), to send every
request to the SEC through one active fetcher ([0005]), to control the
pipeline through custom resources ([0007]), to read EDGAR through
`edgar-asof` ([0009]), and to record every time raw, as its source gives it
([0010]). [§14](#14-changes-to-this-document) says how data written under
version 0.2.0 is read. The data comes from the U.S. Securities and Exchange
Commission's EDGAR system; this document depends on how EDGAR behaves only
where it cites a finding.

## 1. Guarantees

Each guarantee holds in both stores unless it says otherwise, because of
the statements it lists ([§3](#3-statement-ids)).

1. **Every byte an import reads was fetched by a recorded request and is
   stored unchanged.** [OBJ-1], [OBJ-3], [OBJ-4], [REC-3], [REC-8], [IMP-2],
   [IMP-3], [LOG-2], [LOG-4], [STORE-3].
2. **Feeds are versioned.** Every fetch is recorded, and content that differs
   from what a URL returned before becomes a new version beside the old one.
   [FETCH-14], [VER-1], [LOG-1], [LOG-3].
3. **Imports are pinned.** An import names each input by its SHA-256 and reads
   it from a store, never from the live source. [IMP-1], [IMP-2], [IMP-3].
4. **Nothing stored is changed or removed.** A correction is a new record.
   In the git store, its history shows this: [LOG-1], [LOG-2], with the one
   exception in [OBJ-3]. In the cluster, the database refuses a change to a
   record ([LOG-3]). Garage can't refuse a change to an object ([0002]), so
   the pipeline makes none ([LOG-4]), and a check of the objects finds any
   other ([STORE-3]).
5. **No requester identity is stored.** [ID-1], [ID-2].
6. **Every request keeps the SEC's rules,** whichever program sends it: the
   user's own user agent, at most 5 requests a second, one process at a
   time, and a stop on a `403`. [FETCH-2], [FETCH-6], [FETCH-7], [FETCH-9],
   [FETCH-16], [FETCH-17], [FETCH-18].
7. **The git store holds only its declared scope,** which is kept small enough
   to commit to git with its history. [SCOPE-2], [SCOPE-3], [SCOPE-4],
   [SCOPE-5], [XFER-1].
8. **A finding can cite a fetch the cluster made,** since its record reaches
   the git store unchanged, with its body, when the owner transfers it.
   [XFER-1], [XFER-2], [XFER-3].

## 2. Terms

- **Store:** where fetched bodies and the records of their fetches are kept
  ([§4](#4-layout)). There are two:
  - **The git store:** `data/`, committed to git.
  - **The cluster store:** objects in Garage and records in PostgreSQL, in
    the cluster that the umbrella chart installs.
- **Writer:** a program that fetches into a store: `scripts/snapshot.py`,
  into the git store, and **the fetcher**, the pipeline's service that sends
  every request from the cluster ([0005]), into the cluster store.
- **The transfer:** a program the owner runs, which copies records and their
  objects from the cluster store into the git store
  ([§9](#9-scope)). It sends no request to the SEC.
- **Feed:** a named set of URLs fetched for one purpose. A name, of a feed or
  an import, matches `[a-z0-9][a-z0-9-]*`. Which feeds exist is for the
  source investigation ([open question 1](#13-open-questions)).
- **Fetch:** one request for one URL as a writer makes it, including any
  retries, ending in one record.
- **Body:** a response's content after any `Content-Encoding` is removed.
- **Object:** a stored body.
- **Version:** a distinct body among one URL's records in a store ([VER-1]).
- **Scope:** the URLs the git store may hold, declared in `scope.json`
  ([§9](#9-scope)).
- **Import:** a program run that reads stored bodies and produces output from
  them ([§11](#11-imports)).
- **Job:** a message that asks the fetcher for one fetch, and **event**, one
  that announces the record of a fetch ([§10](#jobs-and-events)).
- **Timestamp:** a time in UTC, RFC 3339 with milliseconds and `Z`, such as
  `2026-10-06T18:04:12.345Z`.
- **The chart**, the umbrella Helm chart ([`deployment.md`](deployment.md)),
  and **the release namespace**, **a Source**, **a Backfill**, and **requests
  are allowed** are as [`controls.md`](controls.md#2-terms) has them.

## 3. Statement IDs

Every normative statement in this document has an ID, such as `REC-5`: an
area and a number. The areas `LAY`, `OBJ`, `REC`, `VER`, `LOG`, `SCOPE`,
`FETCH`, `ID`, `IMP`, `STORE`, `JOB`, and `XFER` belong to this document. An
ID is assigned once and never reused or renumbered. A statement that is
dropped stays in the [index](#12-index), marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of stored data, or of what the chart or a
  schema in [`schemas/`](schemas/) says. Of the git store, it holds
  whenever `data/` is at rest, between runs: `scripts/verify.py` checks
  every such invariant and starts each problem it reports with the
  invariant's ID. Of the cluster store, it holds whenever no fetch is
  writing it, and tests on kind check it. Of a schema or the chart, tests
  check it by reading the schema or rendering the chart, without a cluster
  or the network.
- A **rule** is how a program, or the operator, must behave while it runs,
  such as how fast it sends requests. Tests check a program's rules; those
  of the pipeline's services run against a real API server, or on kind.

The index says how each statement is checked: by `verify`, by `tests`, by
tests on `kind`, by the `operator`, or at the `first import`. Every
statement checked by `verify`, `tests`, or `kind` has at least one test
marked with its ID, as `@pytest.mark.spec("REC-5")`, and every marker names
an ID in a specification's index; Go tests carry IDs in the form the first
Go test sets ([0006]). A test, `tests/test_spec_coverage.py`, enforces both,
except for a statement whose tests a later task in the
[plan](../docs/plan.md) writes. It lists such a statement as awaiting that
task, and fails if the statement has a test or the plan marks the task
done. Of the statements new in this version, those of the schemas have
their tests now. The others await the tasks that build what they describe:
T35 brings `snapshot.py` and `verify.py` to this version, T22 builds the
scheduler, T23 the fetcher, T24 the discoverer, and T36 the transfer.

## 4. Layout

### The git store

```text
data/
  README.md                     points here
  scope.json                    the URLs the store may hold (§9)
  feeds/FEED/versions.jsonl     one record per fetch (§6)
  objects/AA/SHA256.gz          one object per distinct body (§5)
  imports/IMPORT.json           one manifest per import run (§11)
```

- **LAY-1** (invariant) `data/` holds only the files above, with `FEED` and
  `IMPORT` valid names, apart from the unfinished writes [OBJ-3] allows.
  Hidden files, whose names begin with a dot, are ignored.

### The cluster store

```text
Garage, the store's bucket:
  objects/AA/SHA256.gz          one object per distinct body (§5)
PostgreSQL, the pipeline's database:
  records                       one row per fetch (§6), as schemas/records.sql defines it
```

An object has the same name in both stores, and a record's `object` names
its object in its own store. Which bucket and which database, and each
service's credentials for them, are for the deployment specification
([§13](#left-to-other-specifications)).

- **STORE-1** (invariant) [`schemas/records.sql`](schemas/records.sql)
  defines the table `records`, with a column for each field of
  [§6](#6-records)'s table, named as the field is, and two more: `id`, which
  numbers the records in the order they're added, and `job`, the `id` of
  the job the fetch was made for ([§10](#jobs-and-events)), which no two
  records share.
- **STORE-2** (invariant) The pipeline's database holds the table `records`
  as [`schemas/records.sql`](schemas/records.sql) defines it. So it refuses
  a row whose fields aren't of [REC-1]'s types and forms, or that breaks
  [REC-3]'s agreement of `sha256`, `bytes`, and `object`, [REC-4], [REC-6],
  [REC-7], [REC-11], or [REC-5]'s rule for a `304` that names no version.
- **STORE-3** (invariant) The cluster store meets the git store's
  invariants of objects and records: every object in the bucket meets
  [OBJ-1], and every record [REC-1] and [REC-3] to [REC-8], with `object`
  naming an object in the bucket, and [REC-5]'s earlier records those with
  a lower `id`.

### Readers outside the pipeline

`edgar-asof`'s cache follows this store ([0009]). The library's
specification, at its version 0.1.0, gives a cache the git store's layout,
objects, and records of this document's version 0.2.0, without its scope
and imports, and reads a store's `data/` as a cache without fetching into
it. Version 1.0.0 keeps what that relies on: the layout, the objects, and
every field of a record with its meaning. It adds only `received_at` and
`date`, which a reader that doesn't know them ignores ([REC-9]), and widens
`tool` to name the fetcher, whose records the transfer brings into `data/`.
So `data/` stays readable as a cache, and only a reader that holds `tool` to
version 0.2.0's form refuses the fetcher's records, until the library's
specification cites this version.

In the cluster, the library reads only bodies that a service gives it from
the cluster store, and never sends a request or keeps a cache
([FETCH-18]).

## 5. Objects

- **OBJ-1** (invariant) The object at `objects/AA/SHA256.gz` is gzip data
  whose decompressed bytes have the SHA-256 `SHA256`, in lower-case
  hexadecimal, and `AA` is its first two characters. The compressed bytes
  may differ between writers; the identity is the decompressed bytes.
- **OBJ-2** (rule) In the git store, an object is written once: to a
  temporary file named `*.tmp` in `objects/`, flushed to disk, then renamed
  into place. If the path already exists, the temporary file is deleted
  instead, since the same name means the same body.
- **OBJ-3** (invariant) After every completed fetch, every object in the git
  store is named by a record and no `*.tmp` file remains. A run that stops
  between placing an object and appending its record can leave an unnamed
  object, and one that stops mid-write a `*.tmp` file. These are the only
  files under `data/` that may be deleted. A later fetch of the same body
  names the object instead.
- **OBJ-4** (rule) The fetcher stores a body in the bucket as the object
  `objects/AA/SHA256.gz`, as [OBJ-1] describes it, and only if no object
  has that name, since the same name means the same body. A fetcher that
  stops between storing an object and adding its record leaves the object
  unnamed, and a later fetch of the same body names it.

## 6. Records

A record is one fetch. In the git store, each feed has one log,
`feeds/FEED/versions.jsonl`, and a record is a JSON object on one line of
it, in UTF-8. In the cluster store, a record is a row of the table
`records` ([STORE-1]), and its event carries it as a JSON object
([§10](#jobs-and-events)). Either way it has these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `feed` | name | The feed's name. |
| `url` | string | The URL requested. |
| `requested_at` | timestamp | When the final attempt was sent, by the writer's clock. |
| `received_at` | timestamp or null | When the final attempt's response arrived: when its status and headers had been received, by the same clock as `requested_at`. Null if no response arrived. |
| `attempts` | integer ≥ 1 | Requests made for this fetch, retries included. |
| `status` | integer or null | The final HTTP status; null if no response arrived. |
| `error` | string or null | Why nothing was stored, when the status doesn't say: no response arrived, or the response couldn't be stored ([§10](#10-fetching)). Null otherwise. |
| `final_url` | string or null | The URL after redirects, if it differs from `url`. |
| `date` | string or null | The response's `Date`, as received. |
| `etag` | string or null | The response's `ETag`. |
| `last_modified` | string or null | The response's `Last-Modified`. |
| `content_type` | string or null | The response's `Content-Type`. |
| `sha256` | 64 lower-case hex digits, or null | The body's SHA-256: the version this fetch returned or confirmed. |
| `bytes` | integer ≥ 0, or null | The body's length in bytes. |
| `object` | string or null | The object's name in the record's store: in the git store, its path relative to `data/`. |
| `tool` | string | The program that wrote the record. For `snapshot.py`, `scripts/snapshot.py@` and a 40-character commit, with `+dirty` if the script differed from that commit, or `@unknown` outside a repository. For the fetcher, `fetcher@` and the 40-character commit its image was built from, which the image's tag gives ([`deployment.md`, §9](deployment.md#9-later-versions)). |

A record keeps each header's value as the response gave it, and its own
times in UTC; time zones are interpreted when the record is read
([0010]).

- **REC-1** (invariant) Every line of a log is a JSON object with every field
  in the table, each of its type and form, except `received_at` and `date`,
  which a record written under version 0.2.0 doesn't have
  ([§14](#14-changes-to-this-document)). A reader takes either, when it's
  absent, as null.
- **REC-2** (invariant) A record's `feed` is the name of its log's directory.
- **REC-3** (invariant) `sha256`, `bytes`, and `object` are all null or all
  set, and when set, `object` is `objects/AA/SHA256.gz` for that `sha256`,
  and that object exists.
- **REC-4** (invariant) A `200` with `error` null names a body, the one
  received.
- **REC-5** (invariant) A `304` names the version its conditional request
  asked about: the latest earlier record of the same URL with a `sha256` in
  its writer's store ([FETCH-4]). In a log, that's the latest earlier line
  of the log with one, for a record `snapshot.py` wrote; a record the
  transfer copied names a version of the cluster store, which [STORE-3]
  checks there. A `304` to a request that sent no validators names nothing
  and has `error` set.
- **REC-6** (invariant) Any other outcome names nothing: every status but
  `200` and `304`, no response, and a `200` with `error` set.
- **REC-7** (invariant) `error` is set whenever `status` is null.
- **REC-8** (invariant) A record's `bytes` is the length of its object's
  decompressed bytes.
- **REC-9** (rule) Readers ignore fields they don't know, so a later version
  of this document can add a field without rewriting records. Writers write
  the fields in the table's order, so the logs diff cleanly; the order
  carries no meaning.
- **REC-10** (rule) A writer writes every field in the table, `received_at`
  and `date` among them.
- **REC-11** (invariant) `received_at` is set exactly when `status` is, and
  `date` is null whenever `status` is. A record without the field
  `received_at`, as each written under version 0.2.0 is
  ([§14](#14-changes-to-this-document)), is held only to the second; one
  whose `received_at` is null is held to both.
- **REC-12** (invariant) No two records in the git store have the same
  `url`, `requested_at`, and `tool`.
- **REC-13** (invariant) [`schemas/record.schema.json`](schemas/record.schema.json)
  is a JSON Schema of a record, with a property for each field in the
  table, of its type and form, each required but `received_at` and `date`.
  Every record in `data/` validates against it.

## 7. Versions

- **VER-1** (rule) The versions of a URL in a store are the distinct `sha256`
  values among its records there, numbered in the order each first appears.
  Content that returns to an earlier body keeps that body's number: a URL
  that returned A, then B, then A has two versions, and its records show the
  return. Versions are per URL; the same body fetched from two URLs is one
  object and a version of each. `verify.py -v` lists those of the git store,
  where the records the transfer copies count with those `snapshot.py`
  wrote.

## 8. History

`data/` is committed to git, so its history shows that nothing was changed.
`verify.py --since REF` checks these against the commit `REF`.

- **LOG-1** (invariant) Records are only appended: each log as it was at an
  earlier commit is a prefix of the log now.
- **LOG-2** (invariant) Objects and manifests are only added: each one at an
  earlier commit exists now with the same bytes.

The cluster store's history isn't in git, so permissions keep its records
and objects as they were:

- **LOG-3** (rule) Records are only added to the table: the fetcher's
  database role may insert and select them, each other role the pipeline's
  services use may only select them, and none may update, delete, or
  truncate them ([0004]).
- **LOG-4** (rule) Objects are only added to the bucket: only the fetcher's
  key may write to it, and the fetcher never deletes or replaces an object.
  Each other service's key may only read it ([0002]).

## 9. Scope

Because the git store is committed with its history, it holds a deliberately
small scope: a few companies' filings and the feeds that announce them, not
EDGAR as a whole. `scope.json` declares the scope, and each entry says why it
was added:

```json
{
  "urls": [
    {
      "url": "https://data.sec.gov/submissions/CIK0000320193.json",
      "reason": "One company's filing history."
    },
    {
      "url": "https://www.sec.gov/Archives/edgar/data/320193/",
      "reason": "That company's filings."
    }
  ]
}
```

An entry ending in `/` covers every URL that starts with it; any other entry
covers only the URL equal to it. Widening the scope is a reviewed change made
before anything it covers is fetched or transferred into the store. EDGAR's
bulk archives of every company stay outside it
([open question 2](#13-open-questions)).

- **SCOPE-1** (invariant) `scope.json` exists and is a JSON object whose
  `urls` is a list. Each entry is an object with a `url`, a string, and a
  `reason`, a non-empty string.
- **SCOPE-2** (invariant) The scope covers every record's `url`, so an entry
  stays once anything it covers is stored.
- **SCOPE-3** (rule) `snapshot.py` checks every URL it is given against the
  scope before its first request, and fetches nothing if the scope is
  malformed or doesn't cover one of them.

The git store's budget follows GitHub's limits
([open question 2](#13-open-questions)). The baseline's 43 bodies and
`submissions`' four further files take 2,205,179 bytes of objects, the
largest 313,163, and their 47 records 581 bytes each on average (SRC-18).
A log of polls would near the budget soonest, and the transfer's ranges
keep it small ([records the cluster made](#records-the-cluster-made)).

- **SCOPE-4** (invariant) No file under `data/` is larger than 50 MiB, the
  size at which GitHub warns, so none nears the 100 MiB GitHub refuses.
- **SCOPE-5** (invariant) The files under `data/` total at most 1 GB,
  1,000,000,000 bytes, the size GitHub recommends a repository stay under.

### The scope and the cluster

`scope.json` limits what the git store holds, not what the cluster fetches.
The cluster fetches what the owner's values ask for through the controls
([`controls.md`](controls.md)): each Source's URL, each Backfill's filings,
and the files of each filing the discoverer finds, as the notices
specification will say. A Source's URL needn't be in `scope.json`, and the
chart doesn't check it. The owner widens each scope: `scope.json` in a
commit of its own, before anything it adds is fetched or transferred into
the git store, and the cluster's through the chart's values
([RBAC-6](controls.md#9-who-may-change-what)). A record reaches the git
store only if `scope.json` covers its URL, whether `snapshot.py` wrote it
([SCOPE-3]) or the transfer copied it ([XFER-1]).

### Records the cluster made

The owner decided that the pipeline writes the records of the git store's
scope to `data/`, in this format and with their bodies, so that a finding
cites them as it cites any record ([0010]). The transfer writes them, when
the owner runs it, for a range of time the owner names. Copying every
record as it's made would outgrow git: at the 10 seconds each company's
Source starts at ([0010]), the three companies' polls add 25,920 records a
day to the `submissions` log, about 15 MB at some 600 bytes a record, so
the log would pass GitHub's warning at 50 MiB within four days
([open question 2](#13-open-questions)). A finding about when a filing
first appeared needs the polls around it, and the owner transfers the
ranges that hold them.

- **XFER-1** (rule) The transfer copies from the cluster store into `data/`
  each record whose `requested_at` is in the range the owner names, from
  its start up to and not including its end, and whose `url` `scope.json`
  covers, with its object, but those [XFER-3] skips, and nothing else. It
  reads only the cluster store, never the SEC. It copies nothing if
  `scope.json` is malformed, or if a record of the range conflicts with
  another ([XFER-3]). It copies nothing, too, if `data/` with the range
  copied would break [SCOPE-4] or [SCOPE-5], and says which limit it would
  break.
- **XFER-2** (rule) The transfer copies each record unchanged: the fields
  of [§6](#6-records)'s table, in that table's order ([REC-9]), with the
  values the cluster store holds, and `requested_at` and `received_at` in
  [§2](#2-terms)'s form of a timestamp, such as
  `2026-10-06T18:04:12.345Z`. A row's `id` and `job` aren't fields of a
  record, and aren't copied. It places the record's object first, as
  [OBJ-2] says, unless the object is there already, then appends the record
  to its feed's log, in the order of the records' `id`s.
- **XFER-3** (rule) The transfer skips only a record equal in every field to
  one `data/` already holds, so a range copied twice adds nothing. If a
  record of the range shares `url`, `requested_at`, and `tool` with a
  different record, in `data/` or in the range, which [REC-12] doesn't
  allow, the transfer copies nothing and says which records conflict,
  leaving that range to the owner.

## 10. Fetching

Both writers keep these rules. A rule that names a run is `snapshot.py`'s;
the fetcher's counterpart is in the same rule or under
[the fetcher](#the-fetcher).

- **FETCH-1** (rule) Only `https` URLs on `www.sec.gov` and `data.sec.gov`
  are fetched.
- **FETCH-2** (rule) Every request carries a `User-Agent`, the user's own,
  which must contain an `@`, following the SEC's sample of a name and a
  contact address
  ([Accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)).
  The repository ships none, and without it nothing is fetched.
  `snapshot.py` reads it from the `SEC_USER_AGENT` environment variable or
  as [FETCH-15] says, and the fetcher as [FETCH-17] says.
- **FETCH-15** (rule) If the environment has no `SEC_USER_AGENT`,
  `snapshot.py` reads it from `.env.dev` at the repository's root, a file
  git ignores, so each user's value stays out of the repository. A value in
  the environment is used instead of the file's. The file holds lines of
  `NAME=VALUE`, and blank lines and lines starting with `#` are ignored.
- **ID-1** (rule) No file under `data/`, no row of the cluster store, and no
  job or event contains the `User-Agent` or any other request header.
- **FETCH-3** (rule) Each request sends `Accept-Encoding: gzip`.
- **FETCH-4** (rule) If the URL has an earlier record with a `sha256` in the
  writer's store, the latest such record's `etag` is sent as
  `If-None-Match` and its `last_modified` as `If-Modified-Since`, when they
  are set.
- **FETCH-5** (rule) Each attempt times out after 60 seconds, which counts as
  no response.
- **FETCH-6** (rule) Requests start at least 0.2 seconds apart: those of one
  `snapshot.py` process, and those of a cluster's fetchers together. That is
  at most 5 a second, half the SEC's stated maximum of 10.
- **FETCH-7** (rule) Only one process fetches from the SEC at a time, across
  every checkout, worktree, and cluster: a `snapshot.py` run, or a
  cluster's active fetcher. The operator runs `snapshot.py` only while no
  installed chart allows requests, such as while its SecAccess is paused.
- **FETCH-8** (rule) No response, a `429`, `500`, `502`, `503`, or `504`, and
  a body cut short ([FETCH-13]) are retried, up to 4 attempts in all. The
  wait before a retry is the response's `Retry-After`, in seconds or as an
  HTTP date, or else 2 to the power of the attempt number, in seconds;
  either is capped at 60 seconds.
- **FETCH-9** (rule) A `403`, which the SEC returns to clients over its
  limit, is recorded, and no other request follows it: a run stops, and the
  fetcher sends none until the owner acknowledges the stop
  ([ACCESS-3](controls.md#8-secaccess)).
- **FETCH-10** (rule) Any other status is recorded without a retry, and
  fetching continues: the run with the next URL, and the fetcher with its
  next job. A run's exit status is nonzero if any of its fetches stored
  nothing.
- **FETCH-11** (rule) A `Content-Encoding` of `gzip` or `x-gzip` is decoded
  before hashing and storing. No encoding, or `identity`, is stored as
  received.
- **FETCH-12** (rule) Any other `Content-Encoding` is recorded with `error`
  set and nothing stored, without a retry, since the body can't be
  identified without decoding it.
- **FETCH-13** (rule) A body cut short stores nothing and is retried
  ([FETCH-8]): one with fewer bytes than its `Content-Length`, one whose
  connection fails mid-stream, and one whose gzip encoding ends early or
  doesn't decode. If the last attempt is cut short too, the record has the
  status and `error` set.
- **FETCH-14** (rule) A fetch adds exactly one record to its writer's store,
  describing its final attempt, after the object it names, if any, is in
  place ([OBJ-2], [OBJ-4]).

### The fetcher

In the cluster, the fetcher sends every request to the SEC: two replicas,
of which one is active ([0005]).

- **FETCH-16** (rule) A fetcher replica sends requests only while it holds
  the fetcher's Lease in the release namespace, and stops before another
  replica can take it over: it stops leading when it can't renew the Lease
  within a deadline shorter than the Lease's duration.
- **FETCH-17** (rule) The fetcher reads its user agent from `SEC_USER_AGENT`
  in its environment, set from the Secret that [ID-2] describes, and sends
  nothing without one that contains an `@`.
- **ID-2** (invariant) The chart puts the user agent in one Secret only,
  rendered from the value `fetcher.secUserAgent`, and only the fetcher's
  containers read it, as `SEC_USER_AGENT`. Neither the chart's default
  values nor any other values file committed to the repository sets it.
  The owner keeps the value in a values file git ignores, as `.env.dev`
  keeps `snapshot.py`'s ([FETCH-15]).
- **FETCH-18** (rule) In the cluster, only the active fetcher sends requests
  to the SEC. The services that read EDGAR's content through `edgar-asof`
  give it bodies from the cluster store, and never use its client or keep a
  cache of their own ([0009]).

### Jobs and events

The fetcher fetches what jobs ask for, and announces each record with an
event. Both are JSON messages in NATS JetStream ([0003]):

| Stream | Subjects | Messages | Kept |
| --- | --- | --- | --- |
| `JOBS` | `edgar.jobs.poll`, `edgar.jobs.filing`, `edgar.jobs.backfill` | Jobs, [`schemas/job.schema.json`](schemas/job.schema.json) | As a work queue: until the fetcher acknowledges the job |
| `RECORDS` | `edgar.records.poll`, `edgar.records.filing`, `edgar.records.backfill` | Events, [`schemas/event.schema.json`](schemas/event.schema.json) | Until each of the stream's consumers acknowledges the event |

A job's subject, and its event's, end in the job's `kind`. A job's fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | Required: a UUID, in lower case | The job's identity, which the record of its fetch keeps as `job` ([STORE-1]). |
| `kind` | Required: `poll`, `filing`, or `backfill` | What asked for the fetch: a Source's poll; a filing the discoverer found; or a Backfill. |
| `feed` | Required: a name | The feed the fetch is recorded under. |
| `url` | Required: a string beginning `https://www.sec.gov/` or `https://data.sec.gov/` | The URL to fetch ([FETCH-1]). |
| `source` | Required in a poll, and only there: a Source's name | The Source polled. |
| `backfill` | Required in a Backfill's job, and only there: a Backfill's name | The Backfill. |
| `queued_at` | Required: a timestamp | When the job was queued. |
| `expires_at` | A timestamp | When the fetch is no longer worth making. |

An event's fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `id` | Required: an integer ≥ 1 | The record's `id` in the table. |
| `job` | Required: a job | The job the fetch was made for. |
| `record` | Required: a record, with every field of [§6](#6-records)'s table | The record. |

- **JOB-1** (invariant) [`schemas/job.schema.json`](schemas/job.schema.json)
  and [`schemas/event.schema.json`](schemas/event.schema.json) are JSON
  Schemas of a job and an event, each with a property for each field in its
  table, of its type and form, and requiring the fields the table marks
  required. An event's `record` validates against
  [`schemas/record.schema.json`](schemas/record.schema.json), with
  `received_at` and `date` required.
- **JOB-2** (rule) The scheduler queues each poll of a Source as a job of
  kind `poll`, with the Source's name, `feed`, and `url`, and an
  `expires_at` when the Source's next poll is due
  ([SOURCE-2](controls.md#6-source)).
- **JOB-3** (rule) Each fetch for a filing the discoverer finds is queued as
  a job of kind `filing`, and each of a Backfill's as one of kind
  `backfill` with the Backfill's name, by the services the notices
  specification names. Neither has an `expires_at`.
- **JOB-4** (rule) Before each request, the fetcher takes the job queued
  first of the first kind that has one waiting: `poll`, then `filing`, then
  `backfill`. So a Backfill's fetches wait for every other
  ([BACKFILL-2](controls.md#7-backfill)), and the polls, which the chart
  holds to half the fetcher's limit
  ([VAL-3](controls.md#5-the-charts-values)), leave the other half to the
  rest.
- **JOB-5** (rule) The fetcher acknowledges a job whose `expires_at` has
  passed when it takes the job, without a request or a record, unless the
  table already holds the job's record, which [JOB-6] covers.
- **JOB-6** (rule) For each job it fetches, the fetcher adds the record,
  then publishes its event, then acknowledges the job, each once the step
  before has succeeded. Given a job whose record the table already holds,
  as after a redelivery, it publishes that record's event again and
  acknowledges the job, without a request, even if the job's `expires_at`
  has passed ([JOB-5]). So a job is fetched again only if its fetch wasn't
  recorded, and an event can arrive twice, with the same `id`.
- **JOB-7** (rule) A consumer of jobs or events ignores fields it doesn't
  know, as readers of records do ([REC-9]). The fetcher acknowledges a job
  that doesn't validate against
  [`schemas/job.schema.json`](schemas/job.schema.json), or whose `kind`
  isn't its subject's, without a request or a record.

## 11. Imports

An import writes a manifest, `imports/IMPORT.json`, before it reads its
inputs:

```json
{
  "import": "companyfacts-2026-10-06",
  "started_at": "2026-10-06T18:04:12.345Z",
  "tool": "pipeline/import.py@0123456789abcdef0123456789abcdef01234567",
  "inputs": [
    {"feed": "notices", "url": "https://www.sec.gov/...", "sha256": "…"}
  ]
}
```

An import in the cluster, such as a parse worker's, reads its inputs from
the bucket, and keeps its manifest as the facts specification will say
([§13](#left-to-other-specifications)).

- **IMP-1** (invariant) A manifest is a JSON object with `import`, a name the
  same as its file's; `started_at`, a timestamp; `tool`, a non-empty
  string; and `inputs`, a list.
- **IMP-2** (invariant) Each input has a `feed`, a `url`, and a `sha256` that
  a record of that feed and URL holds.
- **IMP-3** (rule) An import writes its manifest before reading its inputs,
  and reads them only from its store's objects: in the git store, from
  `objects/`, and in the cluster, from the bucket, over Garage's S3 API
  ([0002]). It never reads an input from the live source. A rerun with the
  same inputs and the same `tool` commit therefore reads the same bytes.
  What its output is, and how two outputs are compared, is for the
  pipeline's specification ([open question 3](#13-open-questions)).

## 12. Index

| ID | Kind | Checked by | Statement |
| --- | --- | --- | --- |
| LAY-1 | invariant | verify | Only the layout's files are under `data/`. |
| STORE-1 | invariant | tests | `schemas/records.sql` defines the table of records, with a column per field, an `id`, and a `job`. |
| STORE-2 | invariant | kind | The database holds that table, and refuses a malformed record. |
| STORE-3 | invariant | kind | The cluster store's objects and records meet the git store's invariants. |
| OBJ-1 | invariant | verify | An object's decompressed bytes have the SHA-256 in its name. |
| OBJ-2 | rule | tests | Objects are written to `*.tmp`, flushed, and renamed; never replaced. |
| OBJ-3 | invariant | verify | After a completed fetch, every object is named and no `*.tmp` remains. |
| OBJ-4 | rule | kind | The fetcher stores an object in the bucket under its name, only if none has it. |
| REC-1 | invariant | verify | Every log line is a record with every field, well formed. |
| REC-2 | invariant | verify | A record's feed is its log's directory. |
| REC-3 | invariant | verify | `sha256`, `bytes`, and `object` are all set or all null, agree, and exist. |
| REC-4 | invariant | verify, tests | A `200` without an error names the body received. |
| REC-5 | invariant | verify | A `304` names the URL's latest earlier version in its store, or nothing and an error. |
| REC-6 | invariant | verify | Every other outcome names nothing. |
| REC-7 | invariant | verify | No response means an error is recorded. |
| REC-8 | invariant | verify | `bytes` is the object's decompressed length. |
| REC-9 | rule | tests | Readers ignore unknown fields; writers keep the table's order. |
| REC-10 | rule | tests | Writers write every field, `received_at` and `date` among them. |
| REC-11 | invariant | verify | A record's `received_at`, if the field is present, is set exactly when there's a status, and `date` only then. |
| REC-12 | invariant | verify | No two records in `data/` share a URL, `requested_at`, and `tool`. |
| REC-13 | invariant | tests | `schemas/record.schema.json` describes a record, and every record in `data/` validates. |
| VER-1 | rule | tests | Versions are distinct bodies per URL, numbered by first appearance. |
| LOG-1 | invariant | verify | Logs are only appended to. |
| LOG-2 | invariant | verify | Objects and manifests are only added. |
| LOG-3 | rule | kind | The database lets only the fetcher add records, and no one change them. |
| LOG-4 | rule | kind | Only the fetcher writes to the bucket, and it never deletes or replaces an object. |
| SCOPE-1 | invariant | verify | `scope.json` lists URLs, each with a reason. |
| SCOPE-2 | invariant | verify | The scope covers every record's URL. |
| SCOPE-3 | rule | tests | Only URLs in the scope are fetched. |
| SCOPE-4 | invariant | verify | No file under `data/` is over 50 MiB. |
| SCOPE-5 | invariant | verify | `data/` holds at most 1 GB. |
| XFER-1 | rule | tests | The transfer copies the scope's records of the owner's range, with their objects, or nothing if that would break the budget. |
| XFER-2 | rule | tests | Each record is copied unchanged, after its object, in the cluster's order. |
| XFER-3 | rule | tests | Only a record equal to one in `data/` is skipped; a conflict on the URL, `requested_at`, and `tool` copies nothing. |
| FETCH-1 | rule | tests | Only `https` on `www.sec.gov` and `data.sec.gov`. |
| FETCH-2 | rule | tests | The user's own user agent is required and sent. |
| FETCH-15 | rule | tests | Without one in the environment, `snapshot.py` reads the user agent from the ignored `.env.dev`. |
| ID-1 | rule | tests | No request header is stored or sent in a message. |
| FETCH-3 | rule | tests | `Accept-Encoding: gzip` is sent. |
| FETCH-4 | rule | tests | The latest version's validators are sent. |
| FETCH-5 | rule | tests | An attempt times out after 60 seconds. |
| FETCH-6 | rule | tests | Requests start at least 0.2 seconds apart. |
| FETCH-7 | rule | operator | One process fetches at a time, across checkouts and clusters. |
| FETCH-8 | rule | tests | Temporary failures are retried, up to 4 attempts, with capped waits. |
| FETCH-9 | rule | tests | A `403` is recorded, and no request follows it. |
| FETCH-10 | rule | tests | Other statuses aren't retried; the exit status reports failures. |
| FETCH-11 | rule | tests | gzip encodings are decoded before hashing. |
| FETCH-12 | rule | tests | Other encodings are recorded and not stored. |
| FETCH-13 | rule | tests | A body cut short stores nothing and is retried. |
| FETCH-14 | rule | tests | Each fetch adds one record, after its object. |
| FETCH-16 | rule | kind | Only the fetcher holding the Lease sends, and it stops before the Lease can pass. |
| FETCH-17 | rule | kind | The fetcher's user agent comes from its Secret, and without one it sends nothing. |
| ID-2 | invariant | tests | The user agent is in one Secret, from a value no committed file sets, read only by the fetcher. |
| FETCH-18 | rule | kind | In the cluster, only the fetcher sends requests; `edgar-asof` reads stored bodies. |
| JOB-1 | invariant | tests | The schemas of a job and an event have their tables' fields. |
| JOB-2 | rule | tests | The scheduler queues each poll as a job that expires when the next is due. |
| JOB-3 | rule | tests | Filings' and Backfills' fetches are queued as jobs of their kinds, without expiry. |
| JOB-4 | rule | kind | The fetcher takes polls first, then filings', then Backfills'. |
| JOB-5 | rule | kind | An expired job without a record is acknowledged without a request. |
| JOB-6 | rule | kind | Record, then event, then acknowledgement; a recorded job isn't fetched again. |
| JOB-7 | rule | kind | Consumers ignore unknown fields; a malformed job is acknowledged without a request. |
| IMP-1 | invariant | verify | A manifest has its fields and its file's name. |
| IMP-2 | invariant | verify | Every input is held by a record of its feed and URL. |
| IMP-3 | rule | first import | An import writes its manifest first and reads only stored objects. |

## 13. Open questions

1. **Which feeds and companies, and the feeds' names.** The source
   investigation decides, records each feed's URLs and how often they change,
   and adds them to the scope.
2. **How large the scope may grow.** Answered in version 1.0.0 by
   [SCOPE-4] and [SCOPE-5], from the sizes the investigation measured
   (SRC-18, SRC-19). GitHub warns on files over 50 MiB, blocks files over
   100 MiB, and recommends repositories stay under 1 GB
   ([About large files on GitHub](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)),
   so a body whose object is over 100 MiB can't be committed at all. If the
   store would outgrow its budget, objects move to Git LFS or release
   assets, with the records, manifests, and scope unchanged, in a major
   version of this document. That can't shrink a log, which is only
   appended to ([LOG-1]), so a log nearing 50 MiB needs a new layout, such
   as a log per feed and year, in a major version of this document too.
3. **What an import's output is and how reruns are compared.** The pipeline's
   specification defines the output and its identity, so that a rerun can be
   shown to match.
4. **Whether the pace suits bulk backfills.** Answered in version 1.0.0: it
   does, and the pace stays as [FETCH-6] has it. At 5 requests a second, a
   backfill of every filing a company's `submissions` lists takes from 15.6
   minutes for Apple to at least 30.7 for Microsoft, and one of only its
   XBRL 10-Ks, 10-Qs, and amendments about 40 seconds (SRC-20). Behind the
   polls, which take at most half the limit ([JOB-4]), the largest takes
   about an hour.

### Left to other specifications

This version leaves to others what the store relies on:

- **Notices:** which files a filing's fetches include; which service queues
  a Backfill's jobs ([JOB-3]); and what the discoverer does with each event.
- **Facts:** where an import in the cluster keeps its manifest.
- **Telemetry:** the fetcher's metrics, such as the jobs waiting of each
  kind and those that expired; the check of the fetcher's clock against
  each response's `date` ([0010]); and how often the cluster store is
  checked against [STORE-3] in operation, with the alert on a problem.
- **Deployment:** the bucket and the database; each service's credentials,
  with the grants [LOG-3] and [LOG-4] describe; how the table and the
  streams are created, and the streams' replicas; and the Lease's duration
  and renewal deadline ([FETCH-16]).

## 14. Changes to this document

Versions follow semantic versioning. Adding a statement, an optional field,
or a check is a minor version. Changing the meaning of a statement or field,
the layout, or a guarantee is a major version, and must say how data written
under the earlier version is read. The schemas in [`schemas/`](schemas/)
that this document names are part of it, so changing one changes it. IDs are
never reused, and a retired statement stays in the index.

### Reading data written under version 0.2.0

The git store's layout, objects, history, and scope are as version 0.2.0
had them, so a store written under it is a store under this version, and
nothing in it is rewritten ([§1](#1-guarantees)). Its records differ from
this version's in three ways:

- **They lack the fields `received_at` and `date`,** which a reader takes
  as null: not recorded ([REC-1]). So [REC-11] holds them only to its part
  on `date`, and doesn't require a `received_at` of a record with a status.
- **`snapshot.py` wrote every one,** so [REC-5] reads each `304` against its
  log.
- **A record written by `snapshot.py` at a commit before `8de33cd`** read the
  clock before waiting out [FETCH-6]'s interval, so its `requested_at` can
  be up to 0.2 seconds earlier than its request was sent. The
  investigation says which of the records it cites this touches
  ([records cited](../investigation/edgar.md#records-cited)).

[0002]: ../docs/decisions/0002-store-bodies-in-garage.md
[0003]: ../docs/decisions/0003-queue-work-in-nats-jetstream.md
[0004]: ../docs/decisions/0004-keep-the-record-in-postgresql-18.md
[0005]: ../docs/decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0006]: ../docs/decisions/0006-write-the-services-in-go-and-python.md
[0007]: ../docs/decisions/0007-control-the-pipeline-through-custom-resources.md
[0009]: ../docs/decisions/0009-read-edgar-through-a-public-point-in-time-library.md
[0010]: ../docs/decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[STORE-1]: #the-cluster-store
[STORE-3]: #the-cluster-store
[OBJ-1]: #5-objects
[OBJ-2]: #5-objects
[OBJ-3]: #5-objects
[OBJ-4]: #5-objects
[REC-1]: #6-records
[REC-3]: #6-records
[REC-4]: #6-records
[REC-5]: #6-records
[REC-6]: #6-records
[REC-7]: #6-records
[REC-8]: #6-records
[REC-9]: #6-records
[REC-11]: #6-records
[REC-12]: #6-records
[VER-1]: #7-versions
[LOG-1]: #8-history
[LOG-2]: #8-history
[LOG-3]: #8-history
[LOG-4]: #8-history
[SCOPE-2]: #9-scope
[SCOPE-3]: #9-scope
[SCOPE-4]: #9-scope
[SCOPE-5]: #9-scope
[XFER-1]: #records-the-cluster-made
[XFER-2]: #records-the-cluster-made
[XFER-3]: #records-the-cluster-made
[FETCH-1]: #10-fetching
[FETCH-2]: #10-fetching
[FETCH-4]: #10-fetching
[FETCH-6]: #10-fetching
[FETCH-7]: #10-fetching
[FETCH-8]: #10-fetching
[FETCH-9]: #10-fetching
[FETCH-13]: #10-fetching
[FETCH-14]: #10-fetching
[FETCH-15]: #10-fetching
[ID-1]: #10-fetching
[FETCH-16]: #the-fetcher
[FETCH-17]: #the-fetcher
[ID-2]: #the-fetcher
[FETCH-18]: #the-fetcher
[JOB-3]: #jobs-and-events
[JOB-4]: #jobs-and-events
[JOB-5]: #jobs-and-events
[JOB-6]: #jobs-and-events
[IMP-1]: #11-imports
[IMP-2]: #11-imports
[IMP-3]: #11-imports
