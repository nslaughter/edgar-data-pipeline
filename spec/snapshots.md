# Specification: the snapshot store

**Status:** Version 0.2.0, draft, October 7, 2026. This document governs
everything under `data/` and every program that reads or writes it:
`scripts/snapshot.py`, `scripts/verify.py`, and any later import. Where code
and this document disagree, the code is wrong or this document needs a new
version; say which before changing either.

The pipeline's own specification, covering EDGAR's feeds, parsing, the time
model, queries, and deployment, follows the source investigation. This
document is only the contract that makes imports reproducible and feeds
versioned, which no finding about the source changes.

## 1. Guarantees

Each guarantee holds because of the statements it lists ([§3](#3-statement-ids)).

1. **Every byte an import reads was fetched by a recorded request and is
   stored unchanged.** [OBJ-1], [OBJ-3], [REC-3], [REC-8], [IMP-2], [IMP-3],
   [LOG-2].
2. **Feeds are versioned.** Every fetch is recorded, and content that differs
   from what a URL returned before becomes a new version beside the old one.
   [FETCH-14], [VER-1], [LOG-1].
3. **Imports are pinned.** An import names each input by its SHA-256 and reads
   it from the store, never from the live source. [IMP-1], [IMP-2], [IMP-3].
4. **Nothing stored is changed or removed.** A correction is a new record.
   [LOG-1], [LOG-2], with the one exception in [OBJ-3].
5. **No requester identity is stored.** [ID-1].
6. **The store holds only its declared scope,** which is kept small enough
   to commit to git with its history. [SCOPE-2], [SCOPE-3].

## 2. Terms

- **Feed:** a named set of URLs fetched for one purpose. A name, of a feed or
  an import, matches `[a-z0-9][a-z0-9-]*`. Which feeds exist is for the
  source investigation ([open question 1](#13-open-questions)).
- **Fetch:** one request for one URL as `snapshot.py` makes it, including any
  retries, ending in one record.
- **Body:** a response's content after any `Content-Encoding` is removed.
- **Object:** a stored body.
- **Version:** a distinct body among one URL's records ([VER-1]).
- **Scope:** the URLs the store may hold, declared in `scope.json`
  ([§9](#9-scope)).
- **Import:** a program run that reads stored bodies and produces output from
  them ([§11](#11-imports)).

## 3. Statement IDs

Every normative statement in this document has an ID, such as `REC-5`: an
area and a number. An ID is assigned once and never reused or renumbered. A
statement that is dropped stays in the [index](#12-index), marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of `data/` that holds whenever it is at
  rest, between runs. `scripts/verify.py` checks every invariant and starts
  each problem it reports with the invariant's ID.
- A **rule** is how a program must behave while it runs, such as how fast it
  sends requests. Tests check rules.

The index says how each statement is checked: by `verify`, by `tests`, by
the `operator`, or at the `first import`. Every statement checked by
`verify` or `tests` has at least one test marked with its ID, as
`@pytest.mark.spec("REC-5")`, and every marker names an ID in the index. A
test, `tests/test_spec_coverage.py`, enforces both.

## 4. Layout

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

## 5. Objects

- **OBJ-1** (invariant) The object at `objects/AA/SHA256.gz` is gzip data
  whose decompressed bytes have the SHA-256 `SHA256`, in lower-case
  hexadecimal, and `AA` is its first two characters. The compressed bytes
  may differ between writers; the identity is the decompressed bytes.
- **OBJ-2** (rule) An object is written once: to a temporary file named
  `*.tmp` in `objects/`, flushed to disk, then renamed into place. If the
  path already exists, the temporary file is deleted instead, since the same
  name means the same body.
- **OBJ-3** (invariant) After every completed fetch, every object is named by
  a record and no `*.tmp` file remains. A run that stops between placing an
  object and appending its record can leave an unnamed object, and one that
  stops mid-write a `*.tmp` file. These are the only files under `data/` that
  may be deleted. A later fetch of the same body names the object instead.

## 6. Records

Each feed has one log, `feeds/FEED/versions.jsonl`. A record is a JSON
object on one line, in UTF-8, with these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `feed` | name | The feed's name. |
| `url` | string | The URL requested. |
| `requested_at` | timestamp | When the final attempt was sent: UTC, RFC 3339 with milliseconds and `Z`, as `2026-10-06T18:04:12.345Z`, by the fetching machine's clock. |
| `attempts` | integer ≥ 1 | Requests made for this fetch, retries included. |
| `status` | integer or null | The final HTTP status; null if no response arrived. |
| `error` | string or null | Why nothing was stored, when the status doesn't say: no response arrived, or the response couldn't be stored ([§10](#10-fetching)). Null otherwise. |
| `final_url` | string or null | The URL after redirects, if it differs from `url`. |
| `etag` | string or null | The response's `ETag`. |
| `last_modified` | string or null | The response's `Last-Modified`. |
| `content_type` | string or null | The response's `Content-Type`. |
| `sha256` | 64 lower-case hex digits, or null | The body's SHA-256: the version this fetch returned or confirmed. |
| `bytes` | integer ≥ 0, or null | The body's length in bytes. |
| `object` | string or null | The object's path relative to `data/`. |
| `tool` | string | The program that wrote the record: `scripts/snapshot.py@` and a 40-character commit, with `+dirty` if the script differed from that commit, or `@unknown` outside a repository. |

- **REC-1** (invariant) Every line of a log is a JSON object with every field
  in the table, each of its type and form.
- **REC-2** (invariant) A record's `feed` is the name of its log's directory.
- **REC-3** (invariant) `sha256`, `bytes`, and `object` are all null or all
  set, and when set, `object` is `objects/AA/SHA256.gz` for that `sha256`,
  and that object exists.
- **REC-4** (invariant) A `200` with `error` null names a body, the one
  received.
- **REC-5** (invariant) A `304` names the version its conditional request
  asked about: the latest earlier record of the same URL with a `sha256`
  ([FETCH-4]). A `304` to a request that sent no validators names nothing
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

## 7. Versions

- **VER-1** (rule) The versions of a URL are the distinct `sha256` values
  among its records, numbered in the order each first appears. Content that
  returns to an earlier body keeps that body's number: a URL that returned A,
  then B, then A has two versions, and its records show the return. Versions
  are per URL; the same body fetched from two URLs is one object and a
  version of each. `verify.py -v` lists them.

## 8. History

`data/` is committed to git, so its history shows that nothing was changed.
`verify.py --since REF` checks these against the commit `REF`.

- **LOG-1** (invariant) Records are only appended: each log as it was at an
  earlier commit is a prefix of the log now.
- **LOG-2** (invariant) Objects and manifests are only added: each one at an
  earlier commit exists now with the same bytes.

## 9. Scope

Because the store is committed with its history, it holds a deliberately
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
before anything it covers is fetched into the store. EDGAR's bulk archives of
every company stay outside it ([open question 2](#13-open-questions)).

- **SCOPE-1** (invariant) `scope.json` exists and is a JSON object whose
  `urls` is a list. Each entry is an object with a `url`, a string, and a
  `reason`, a non-empty string.
- **SCOPE-2** (invariant) The scope covers every record's `url`, so an entry
  stays once anything it covers is stored.
- **SCOPE-3** (rule) `snapshot.py` checks every URL it is given against the
  scope before its first request, and fetches nothing if the scope is
  malformed or doesn't cover one of them.

## 10. Fetching

- **FETCH-1** (rule) Only `https` URLs on `www.sec.gov` and `data.sec.gov`
  are fetched.
- **FETCH-2** (rule) Every request carries the `User-Agent` from the
  `SEC_USER_AGENT` environment variable, which must contain an `@`,
  following the SEC's sample of a name and a contact address
  ([Accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)).
  Each user supplies their own; the repository ships none, and without it
  nothing is fetched.
- **FETCH-15** (rule) If the environment has no `SEC_USER_AGENT`,
  `snapshot.py` reads it from `.env.dev` at the repository's root, a file
  git ignores, so each user's value stays out of the repository. A value in
  the environment is used instead of the file's. The file holds lines of
  `NAME=VALUE`, and blank lines and lines starting with `#` are ignored.
- **ID-1** (rule) No file under `data/` contains the `User-Agent` or any
  other request header.
- **FETCH-3** (rule) Each request sends `Accept-Encoding: gzip`.
- **FETCH-4** (rule) If the URL has an earlier record with a `sha256`, the
  latest such record's `etag` is sent as `If-None-Match` and its
  `last_modified` as `If-Modified-Since`, when they are set.
- **FETCH-5** (rule) Each attempt times out after 60 seconds, which counts as
  no response.
- **FETCH-6** (rule) Requests from one process start at least 0.2 seconds
  apart: at most 5 a second, half the SEC's stated maximum of 10.
- **FETCH-7** (rule) Only one process fetches from the SEC at a time.
- **FETCH-8** (rule) No response, a `429`, `500`, `502`, `503`, or `504`, and
  a body cut short ([FETCH-13]) are retried, up to 4 attempts in all. The
  wait before a retry is the response's `Retry-After`, in seconds or as an
  HTTP date, or else 2 to the power of the attempt number, in seconds;
  either is capped at 60 seconds.
- **FETCH-9** (rule) A `403`, which the SEC returns to clients over its
  limit, is recorded, and the run stops without another request.
- **FETCH-10** (rule) Any other status is recorded without a retry, and the
  run continues. The run's exit status is nonzero if any fetch stored
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
- **FETCH-14** (rule) A fetch appends exactly one record, describing its
  final attempt, after the object it names, if any, is in place
  ([OBJ-2]).

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

- **IMP-1** (invariant) A manifest is a JSON object with `import`, a name the
  same as its file's; `started_at`, a timestamp as in [§6](#6-records);
  `tool`, a non-empty string; and `inputs`, a list.
- **IMP-2** (invariant) Each input has a `feed`, a `url`, and a `sha256` that
  a record of that feed and URL holds.
- **IMP-3** (rule) An import writes its manifest before reading its inputs,
  and reads them only from `objects/`, never from the network. A rerun with
  the same inputs and the same `tool` commit therefore reads the same bytes.
  What its output is, and how two outputs are compared, is for the
  pipeline's specification ([open question 3](#13-open-questions)).

## 12. Index

| ID | Kind | Checked by | Statement |
| --- | --- | --- | --- |
| LAY-1 | invariant | verify | Only the layout's files are under `data/`. |
| OBJ-1 | invariant | verify | An object's decompressed bytes have the SHA-256 in its name. |
| OBJ-2 | rule | tests | Objects are written to `*.tmp`, flushed, and renamed; never replaced. |
| OBJ-3 | invariant | verify | After a completed fetch, every object is named and no `*.tmp` remains. |
| REC-1 | invariant | verify | Every log line is a record with every field, well formed. |
| REC-2 | invariant | verify | A record's feed is its log's directory. |
| REC-3 | invariant | verify | `sha256`, `bytes`, and `object` are all set or all null, agree, and exist. |
| REC-4 | invariant | verify, tests | A `200` without an error names the body received. |
| REC-5 | invariant | verify | A `304` names the URL's latest earlier version, or nothing and an error. |
| REC-6 | invariant | verify | Every other outcome names nothing. |
| REC-7 | invariant | verify | No response means an error is recorded. |
| REC-8 | invariant | verify | `bytes` is the object's decompressed length. |
| REC-9 | rule | tests | Readers ignore unknown fields; writers keep the table's order. |
| VER-1 | rule | tests | Versions are distinct bodies per URL, numbered by first appearance. |
| LOG-1 | invariant | verify | Logs are only appended to. |
| LOG-2 | invariant | verify | Objects and manifests are only added. |
| SCOPE-1 | invariant | verify | `scope.json` lists URLs, each with a reason. |
| SCOPE-2 | invariant | verify | The scope covers every record's URL. |
| SCOPE-3 | rule | tests | Only URLs in the scope are fetched. |
| FETCH-1 | rule | tests | Only `https` on `www.sec.gov` and `data.sec.gov`. |
| FETCH-2 | rule | tests | The user's own `SEC_USER_AGENT` is required and sent. |
| FETCH-15 | rule | tests | Without one in the environment, the user agent is read from the ignored `.env.dev`. |
| ID-1 | rule | tests | No request header is stored. |
| FETCH-3 | rule | tests | `Accept-Encoding: gzip` is sent. |
| FETCH-4 | rule | tests | The latest version's validators are sent. |
| FETCH-5 | rule | tests | An attempt times out after 60 seconds. |
| FETCH-6 | rule | tests | Requests start at least 0.2 seconds apart. |
| FETCH-7 | rule | operator | One process fetches at a time. |
| FETCH-8 | rule | tests | Temporary failures are retried, up to 4 attempts, with capped waits. |
| FETCH-9 | rule | tests | A `403` is recorded and stops the run. |
| FETCH-10 | rule | tests | Other statuses aren't retried; the exit status reports failures. |
| FETCH-11 | rule | tests | gzip encodings are decoded before hashing. |
| FETCH-12 | rule | tests | Other encodings are recorded and not stored. |
| FETCH-13 | rule | tests | A body cut short stores nothing and is retried. |
| FETCH-14 | rule | tests | Each fetch appends one record, after its object. |
| IMP-1 | invariant | verify | A manifest has its fields and its file's name. |
| IMP-2 | invariant | verify | Every input is held by a record of its feed and URL. |
| IMP-3 | rule | first import | An import writes its manifest first and reads only stored objects. |

## 13. Open questions

1. **Which feeds and companies, and the feeds' names.** The source
   investigation decides, records each feed's URLs and how often they change,
   and adds them to the scope.
2. **How large the scope may grow.** The investigation measures real sizes,
   and the scope's budget is set from them. GitHub warns on files over
   50 MiB, blocks files over 100 MiB, and recommends repositories stay under
   1 GB
   ([About large files on GitHub](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)),
   so a body whose object is over 100 MiB can't be committed at all. If the
   store outgrows git, objects move to Git LFS or release assets, with the
   records, manifests, and scope unchanged.
3. **What an import's output is and how reruns are compared.** The pipeline's
   specification defines the output and its identity, so that a rerun can be
   shown to match.
4. **Whether the pace suits bulk backfills.** The investigation measures how
   long a backfill takes at 5 requests a second.

## 14. Changes to this document

Versions follow semantic versioning. Adding a statement, an optional field,
or a check is a minor version. Changing the meaning of a statement or field,
the layout, or a guarantee is a major version, and must say how data written
under the earlier version is read. IDs are never reused, and a retired
statement stays in the index.

[OBJ-1]: #5-objects
[OBJ-2]: #5-objects
[OBJ-3]: #5-objects
[REC-3]: #6-records
[REC-8]: #6-records
[VER-1]: #7-versions
[LOG-1]: #8-history
[LOG-2]: #8-history
[SCOPE-2]: #9-scope
[SCOPE-3]: #9-scope
[FETCH-4]: #10-fetching
[FETCH-8]: #10-fetching
[FETCH-13]: #10-fetching
[FETCH-14]: #10-fetching
[ID-1]: #10-fetching
[IMP-1]: #11-imports
[IMP-2]: #11-imports
[IMP-3]: #11-imports
