# Specification: the fake EDGAR

**Status:** Version 0.1.0, draft, October 19, 2026. This document governs
the fake EDGAR, a service for tests that answers as EDGAR from the stored
versions in the git store: which version it serves for a request, and how
it answers a conditional one; the responses a test cues it to give
instead; its log of the requests its server receives, and how tests read
it; and the one set of checks of the fetching rules that reads that log,
for `scripts/snapshot.py` and for the fetcher. How the charts install it on
kind, with its image and its certificate, is the deployment's
([`deployment.md`](deployment.md#the-fake-edgar)). Where code and this
document disagree, the code is wrong or this document needs a new version;
say which before changing either.

It rests on the decisions to deploy on kind, with a fake EDGAR so that
tests never reach the network ([0001]), and to write the fake EDGAR in
Python with the standard library, serving the git store under `data/`, so
that one set of checks runs against both implementations of the fetching
rules ([0006]). It serves the stored versions as the snapshot store's
version 1.0.0 describes them ([`snapshots.md`](snapshots.md)). Those are
filings and feeds from the U.S. Securities and Exchange Commission's EDGAR
system; this document depends on how EDGAR behaves only where it cites a
finding.

## 1. Guarantees

Each guarantee holds because of the statements it lists ([§3](#3-statement-ids)).

1. **A test that fetches from the fake EDGAR gets what EDGAR gave:** the
   stored versions, with the validators each was stored with, and a `304`
   to a request that asks about the version it would get. [SERVE-1],
   [SERVE-2], [SERVE-3], [SERVE-4].
2. **A test can have the fake EDGAR refuse or fail on cue,** with a
   `429`, a `403`, or any other status, with the headers and body the test
   gives; an earlier version; a body cut short; or no response. So the
   tests of the fetching rules can have the failures that [FETCH-8],
   [FETCH-9], [FETCH-10], [FETCH-12], and [FETCH-13] handle.
   [CUE-1], [CUE-2], [CUE-3].
3. **Every request its server receives is in its log, in order,** with
   its headers and the answer it got, and no request header leaves the
   fake EDGAR but through the answers of its admin port. [SERVE-5],
   [REQ-1], [REQ-2], [REQ-3], [ADMIN-1].
4. **The fetching rules are checked the same way for both writers:** one
   set of checks reads the log, for `snapshot.py`'s requests in its tests
   and for the fetcher's on kind. [CHECK-1], [CHECK-2], [CHECK-3].
5. **The fake EDGAR sends nothing, and changes nothing it serves.**
   [SERVE-1].

## 2. Terms

- **The fake EDGAR:** this service. It isn't one of the pipeline's six
  services ([0006]): it's for tests, CI installs it on kind, and the
  owner's installations don't ([FAKE-1]).
- **The SEC's hosts:** `www.sec.gov` and `data.sec.gov`, the only hosts the
  writers fetch from ([FETCH-1]).
- **Its server:** the port at which the fake EDGAR answers as EDGAR.
  **Its admin port:** the port at which tests cue it and read its log
  ([§7](#7-the-admin-port)).
- **Stored URL:** a URL that a record in the `data/` the fake EDGAR serves
  names a version of ([REC-3]). Its **versions** are as [VER-1] has them,
  and its **latest version** is the one its latest such record names, by
  `requested_at`.
- **A request's target:** its path and query, as its request line gives
  them. **The URLs a request names:** `https://`, then a host, then its
  target. The host is the request's `Host`, without a port, when that's one
  of the SEC's hosts, and either of them otherwise, as when a test on one
  machine sends to the fake EDGAR's own address.
- **Cue:** responses a test asks the fake EDGAR to give the next requests
  for a URL, in place of what it would serve ([§5](#5-cues)).
- **The request log:** the fake EDGAR's list of the requests its server has
  received ([§6](#6-the-request-log)). It's no store's log of records
  ([§6 of the store](snapshots.md#6-records)).
- **Writer**, **record**, **version**, **object**, **body**, and
  **timestamp** are as [§2 of the store](snapshots.md#2-terms) has them.

## 3. Statement IDs

Every normative statement in this document has an ID, such as `SERVE-1`: an
area and a number. The areas `SERVE`, `CUE`, `REQ`, `ADMIN`, and `CHECK`
belong to this document. An ID is assigned once and never reused or
renumbered. A statement that is dropped stays in the [index](#9-index),
marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of a schema in [`schemas/`](schemas/)
  that this document names. Tests check it by reading the schema.
- A **rule** is how the fake EDGAR, or the tests that use it, behave.
  Tests check the fake EDGAR's rules by running it on one machine, on a
  `data/` of their own, without the network, and on kind, where the charts
  install it ([`deployment.md`](deployment.md#the-fake-edgar)).

The index says how each statement is checked: by `tests`, or by tests on
`kind`. Every statement has at least one test marked with its ID, as
`@pytest.mark.spec("SERVE-1")`, and every marker names an ID in a
specification's index. A test, `tests/test_spec_coverage.py`, enforces
both, except for a statement whose tests a later task in the
[plan](../docs/plan.md) writes. It lists such a statement as awaiting that
task, and fails if the statement has a test or the plan marks the task
done. Of this version's statements, those of the schemas have their tests
now. The others await the tasks that build what they describe: T21 builds
the fake EDGAR, and T23 the fetcher, whose requests [CHECK-3] checks.

## 4. What it serves

The fake EDGAR serves a git store: in the cluster, `data/` as committed at
the commit its image is built from, which the image holds
([IMG-5](deployment.md#the-pipelines-own-images)); and on one machine, the
`data/` a test gives it. It answers as EDGAR answered the writers, from the
records of their fetches.

- **SERVE-1** (rule) The fake EDGAR reads the `data/` it's given, laid out
  as [§4 of the store](snapshots.md#4-layout) says, once, when it starts.
  It serves only what that holds, changes nothing in it, and sends no
  request to anything. It refuses to start if two stored URLs differ only
  in their host, since a request's target would then name both.
- **SERVE-2** (rule) A `GET` is for the stored URL among the URLs it names,
  if there's one. A `GET` for no stored URL gets a `404`, unless a cue
  answers it ([CUE-2]), and a request with any other method a `405`, each
  with no body.
- **SERVE-3** (rule) A `GET` for a stored URL gets a `200` with the URL's
  latest version, unless [SERVE-4] or a cue answers it. Its headers are
  `Content-Type`, `ETag`, and `Last-Modified`, each with the value of the
  latest record of the URL that names that version, and only where that
  record has one, so each URL carries the validators EDGAR gave it
  ([SRC-4]); `Date`, the fake EDGAR's clock, as RFC 9110 gives it; and
  `Content-Length`. When the request has an `Accept-Encoding` that
  accepts `gzip`, as RFC 9110 reads that header, the fake EDGAR sends the
  version's object as it's stored, which is gzip data ([OBJ-1]), with
  `Content-Encoding: gzip`. Otherwise, as when the request has no
  `Accept-Encoding`, it sends the body itself, without a
  `Content-Encoding`. It sends no redirect.
- **SERVE-4** (rule) A conditional `GET` for a stored URL gets a `304`
  when the version [SERVE-3] would send hasn't changed, as RFC 9110 says:
  when its `If-None-Match` is `*` or lists the version's `ETag`, by weak
  comparison; or, when it sends no `If-None-Match`, when its
  `If-Modified-Since` is a date no earlier than the version's
  `Last-Modified`. A version without an `ETag` matches no list, and one
  without a `Last-Modified` no date. So a writer, which sends only the
  validators it stored ([FETCH-4]), gets a `304` only for a URL to which
  EDGAR gave them, and the whole body each time for a URL to which EDGAR
  gave none, as from EDGAR ([SRC-4], [SRC-22]). The `304` has no body, and
  carries `Date` and the version's `ETag` and `Last-Modified`, where it has
  them.
- **SERVE-5** (rule) The fake EDGAR's server speaks HTTPS when it's given
  a certificate and its key, as in the cluster
  ([FAKE-3](deployment.md#the-fake-edgar)), and plain HTTP otherwise, as on
  one machine. Its admin port speaks plain HTTP. Its server answers one
  request at a time, in the order they arrive, and closes each connection
  once it has answered the request on it.

## 5. Cues

The stored versions are the responses EDGAR gave, and the fetches the
investigation stored got a `200` or a `304` ([SRC-20], [SRC-22]). The
fetching rules' failures need others: a `429` or a `5xx` to retry, a `403`
to stop at, a body cut short, and no response at all ([FETCH-8],
[FETCH-9], [FETCH-13]). A test cues the fake EDGAR to give them, through
its admin port ([ADMIN-1]). A cue is a JSON object,
[`schemas/fake-edgar-cue.schema.json`](schemas/fake-edgar-cue.schema.json).

A cue's fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `url` | A string beginning `https://www.sec.gov/` or `https://data.sec.gov/` | The URL whose next requests the cue answers. Without it, the cue answers the next requests for any URL. |
| `responses` | Required: a list of one or more responses | The responses, in order, one for each request the cue answers. |

A response's fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `status` | An integer from 100 to 599 | Answer with this status, `headers`, and `body`, and no version. |
| `headers` | An object, each of whose values is a string | The headers sent with `status`, each by its name. |
| `body` | A string | The body sent with `status`, encoded as UTF-8. Without it, the body is empty. |
| `sha256` | 64 lower-case hexadecimal digits | Serve this version of the URL in place of its latest. |
| `cut` | `true` | Send the version, its latest or `sha256`'s, but stop halfway through its body. |
| `drop` | `true` | Send no response. |

A response has `status`, with `headers` and `body` or without them; or
`sha256`, `cut`, or both; or `drop` alone.

- **CUE-1** (rule) The fake EDGAR keeps each cue a test adds through its
  admin port, in the order they're added, until it has given each of the
  cue's responses, or until a test removes every cue ([ADMIN-1]).
- **CUE-2** (rule) A `GET` that names the `url` of a kept cue gets the
  next response of the first such cue added. A `GET` that names none gets
  the next response of the first kept cue without a `url`, if there's one.
  A `GET` a kept cue answers gets this, in place of what [SERVE-2] to
  [SERVE-4] would give, and each response is given once:
  1. With `status`, that status, with `headers`, and with `body` or none.
     The fake EDGAR adds `Date` and `Content-Length`, unless `headers` has
     them.
  2. With `sha256`, `cut`, or both, a version of the stored URL it's for:
     `sha256`'s, or its latest without one. If there's no such version, it
     gets a `404` with no body. Without `cut`, it gets what [SERVE-3] and
     [SERVE-4] give of that version. With `cut`, it gets the `200`
     [SERVE-3] gives of it, even to a conditional request, but only the
     first half of the bytes sent as its body, rounded down, after which
     the fake EDGAR closes the connection. So the body has fewer bytes
     than its `Content-Length`, and a gzip encoding ends early
     ([FETCH-13]).
  3. With `drop`, no response: the fake EDGAR closes the connection once
     it has read the request ([FETCH-8]).
- **CUE-3** (invariant) [`schemas/fake-edgar-cue.schema.json`](schemas/fake-edgar-cue.schema.json)
  is a JSON Schema of a cue, with a property for each field in the cue's
  table, and each response a property for each field in the response's,
  each of its type and form. It requires the fields the tables mark
  required, and holds a response to the combinations of fields the
  section gives.

## 6. The request log

The fake EDGAR keeps a log of every request its server receives, so tests
see what a writer sent and the answer it got. An entry of the log is a JSON
object,
[`schemas/fake-edgar-request.schema.json`](schemas/fake-edgar-request.schema.json),
with these fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `n` | Required: an integer ≥ 1 | The request's place in the log: 1 for the first request since the fake EDGAR started, and one more for each after it. |
| `received_at` | Required: a timestamp | When the request's request line had been read, by the fake EDGAR's clock. |
| `client` | Required: a string | The address the request came from, without its port. |
| `method` | Required: a string | The request's method. |
| `target` | Required: a string | The request's target. |
| `url` | Required: a string beginning `https://www.sec.gov/` or `https://data.sec.gov/`, or null | The stored URL the request was for ([SERVE-2]), or null if none. |
| `headers` | Required: an object, each of whose names is in lower case and each of whose values is a string | Each of the request's headers, by its name in lower case. A header sent more than once has its values joined by `, `, in the order sent. |
| `status` | Required: an integer from 100 to 599, or null | The status of the response the request got, or null if it got none ([CUE-2]). |
| `cued` | Required: a boolean | Whether a cue answered the request ([CUE-2]). |

- **REQ-1** (rule) The fake EDGAR adds each request its server receives
  to the request log, as one entry with the table's fields, once it has
  answered the request or closed its connection. Entries are in the order
  of their requests ([SERVE-5]), and the log keeps each from when the fake
  EDGAR starts until it stops.
- **REQ-2** (rule) The fake EDGAR keeps the request log in memory only. It
  writes no request header, the `User-Agent` among them, to its output, to
  a file, or anywhere but the answers its admin port gives ([ADMIN-1]). So
  a writer's user agent, which no store holds ([ID-1]), is in no log of
  the cluster's either.
- **REQ-3** (invariant) [`schemas/fake-edgar-request.schema.json`](schemas/fake-edgar-request.schema.json)
  is a JSON Schema of an entry of the request log, with a property for
  each field in the table, of its type and form, each required.

## 7. The admin port

Tests reach the admin port directly on one machine, and on kind through
the Service the chart gives it ([FAKE-2](deployment.md#the-fake-edgar)).

| Request | Body | Answer |
| --- | --- | --- |
| `POST /cues` | A cue | `204` once the fake EDGAR keeps the cue; `400`, keeping nothing, if the body isn't a cue. |
| `DELETE /cues` | None | `204` once the fake EDGAR keeps no cue. |
| `GET /requests?after=N` | None | `200` with a JSON array of the request log's entries whose `n` is greater than `N`, an integer ≥ 0, in the order of `n`; without `after`, every entry. `400` if `N` isn't such an integer. |

- **ADMIN-1** (rule) The fake EDGAR's admin port answers each request in
  the table as the table says, a body that validates against
  [`schemas/fake-edgar-cue.schema.json`](schemas/fake-edgar-cue.schema.json)
  being a cue, and any other request with a `404`. A request to the admin
  port isn't one of the request log's.

## 8. The checks

The fetching rules have two implementations, `snapshot.py` for the git
store and the fetcher for the cluster ([0006]). One set of checks, written
once, reads the request log of each one's requests, so that both are held
to the same reading of the rules: the user agent, at most 5 requests a
second, and nothing after a `403`.

- **CHECK-1** (rule) The checks read a list of the request log's entries,
  in the log's order, and the user agent the writer was given, and report
  each entry that breaks a fetching rule:
  1. one whose `user-agent` header isn't that user agent, or has no `@`
     ([FETCH-2]);
  2. one whose `received_at` is less than 0.18 seconds after the entry
     before it: the 0.2 seconds of [FETCH-6], less 20 milliseconds, which
     allows for the time a request takes to arrive;
  3. one that follows an entry whose `status` is `403` ([FETCH-9]).
- **CHECK-2** (rule) The tests of `snapshot.py` run it against the fake
  EDGAR, on one machine, and the checks on the entries of its requests,
  which they pass: a run of several stored URLs, one of them cued a body
  cut short, one a `429`, and one no response, each retried
  ([FETCH-8]); and a run of several, the second of them cued a `403`,
  after which no request follows.
- **CHECK-3** (rule) The tests on kind run the same checks, with the user
  agent CI gives the fetcher ([FETCH-17]), on the entries of the fetcher's
  requests to the fake EDGAR in the installed charts, which they pass: a
  stop after a cued `403` among them, up to the acknowledgement that ends
  it ([ACCESS-3](controls.md#8-secaccess)).

## 9. Index

| ID | Kind | Checked by | Statement |
| --- | --- | --- | --- |
| SERVE-1 | rule | tests | The fake EDGAR serves the `data/` it's given, read at its start, changes nothing, and sends nothing. |
| SERVE-2 | rule | tests | A `GET` is for the stored URL it names; one for none gets a `404`, and another method a `405`. |
| SERVE-3 | rule | tests | A `GET` for a stored URL gets its latest version, with its record's headers, gzip-encoded only when the request's `Accept-Encoding` accepts it. |
| SERVE-4 | rule | tests | A conditional `GET` gets a `304` when the version hasn't changed, as RFC 9110 says. |
| SERVE-5 | rule | tests | Its server speaks HTTPS with a certificate, plain HTTP without one, and answers one request at a time. |
| CUE-1 | rule | tests | Cues are kept in order until each response is given, or a test removes them. |
| CUE-2 | rule | tests | A cued request gets the cue's next response: a status, a version, a body cut short, or none. |
| CUE-3 | invariant | tests | `schemas/fake-edgar-cue.schema.json` describes a cue. |
| REQ-1 | rule | tests | Each request is one entry of the log, in order, once answered. |
| REQ-2 | rule | tests | The log is kept in memory, and no request header is written anywhere. |
| REQ-3 | invariant | tests | `schemas/fake-edgar-request.schema.json` describes an entry of the log. |
| ADMIN-1 | rule | tests | The admin port adds and removes cues, and gives the log after a place in it. |
| CHECK-1 | rule | tests | One set of checks reports a wrong user agent, requests less than 0.18 seconds apart, and any request after a `403`. |
| CHECK-2 | rule | tests | `snapshot.py`'s requests to the fake EDGAR pass the checks, with retries and a stop on cue. |
| CHECK-3 | rule | kind | The fetcher's requests to the fake EDGAR on kind pass the same checks. |

## 10. Open questions

The owner decided on October 18, 2026 that the fake EDGAR is specified,
here and in the deployment's version 1.2.0, before T21 builds it, since no
specification covered it. None is open: the owner confirmed on October 19,
2026 each detail this version settles, keeping each proposed default over
its alternative:

1. **Which version it serves** ([SERVE-2] to [SERVE-4]). Decided: each
   URL's latest version, an earlier one on cue, a `304` as RFC 9110 gives
   it, and a `404` for a URL not stored. The alternative is each version
   in turn, one for each request, in the order stored: it replays a URL's
   history without a cue, but what a test gets would depend on how many
   requests came before it.
2. **Failures on cue** ([§5](#5-cues)). Decided: cues through the admin
   port, for one URL or any, each response given once, in order: a status,
   an earlier version, a body cut short, or no response. No cue delays a
   response, as a test of [FETCH-5]'s timeout would need, or sends a body
   that isn't UTF-8 text, as gzip data is, so no cue gives a body encoded
   `x-gzip` that decodes ([FETCH-11]); a kind of response for each, such as
   a delay or a body in base64, would be a minor version
   ([§12](#12-changes-to-this-document)). The alternative is a fixed
   failure for each of some URLs, such as a path that always answers
   `403`: no admin port, but each failure is then a URL of its own, which a
   writer must be asked to fetch in place of the URL whose failure a test
   needs.
3. **Its log of requests** ([§6](#6-the-request-log), [ADMIN-1]).
   Decided: kept in memory, and read through the admin port from a place
   in it, with one request answered at a time, so the log's order is the
   requests'. The alternative is a JSON line for each request on its
   output, read on kind with `kubectl logs`: no admin port, but the user
   agent would be in the pod's log, which [REQ-2] rules out.
4. **How requests reach it** ([SERVE-5], and the deployment's
   [FAKE-3](deployment.md#the-fake-edgar) and
   [FAKE-4](deployment.md#the-fake-edgar)). Decided: in the cluster,
   HTTPS with a certificate for the SEC's hosts, from an authority that the
   release makes only when the fake EDGAR is installed, and kind's DNS
   answering the SEC's names with the fake EDGAR's Service, so the writers
   fetch the URLs [FETCH-1] allows and no pod reaches the SEC; and plain
   HTTP on one machine, as the tests of `snapshot.py` already run a local
   server. The alternative is plain HTTP
   at an address of its own, which a setting only CI gives the fetcher
   sends to instead of the SEC's hosts: no certificate or DNS, but an
   exception to [FETCH-1], the fetcher's TLS untested, and nothing to stop
   another pod from reaching the SEC.
5. **The checks' allowance** ([CHECK-1]). Decided: 20 milliseconds less
   than [FETCH-6]'s 0.2 seconds, by when each request arrives. The
   alternative is no allowance: it holds arrivals to [FETCH-6] exactly,
   but fails a run in which one request arrives late, as after a TLS
   handshake, and the next on time.

The deployment's version 1.2.0, which installs the fake EDGAR, settles
three more, which the owner confirmed too:

6. **How it's installed** ([FAKE-1](deployment.md#the-fake-edgar),
   [FAKE-2](deployment.md#the-fake-edgar)). Decided: by the umbrella
   chart, only when the value `fakeEdgar.enabled` is `true`, which CI sets
   and no committed values file does, as one replica behind a Service. The
   alternative is a chart or manifests of its own, which CI installs apart
   from the four releases: the umbrella chart holds nothing for tests, but
   the installed charts then run none of the pipeline's images until a
   service is built, so [IMG-3](deployment.md#the-pipelines-own-images)'s
   test has nothing to check.
7. **How `data/` reaches it** ([IMG-5](deployment.md#the-pipelines-own-images)).
   Decided: in its image, which [IMG-2](deployment.md#the-pipelines-own-images)'s
   command builds from the files of the commit it tags, so the image
   serves that commit's `data/`, 2.7 MB now. The alternative is the
   checkout's `data/`, which `deploy/kind.yaml` mounts into the nodes as it
   mounts the volumes' directory: no new image when `data/` changes, but
   the image wouldn't say which `data/` it serves.
8. **The base images, by digest**
   ([IMG-5](deployment.md#the-pipelines-own-images)). Decided: each of the
   pipeline's images is built from base images named by digest, so the
   same commit builds the same software. The alternative is base images
   named by tag, which their publishers may move to other images.

This version settles two more of its own, which the owner confirmed too:

9. **No metrics or traces** ([§11](#11-left-to-other-specifications)).
   Decided: the fake EDGAR exposes no metrics and sends no traces, since
   it's not one of the services the telemetry's specification observes,
   and its request log shows what its server receives. [0006] lists it
   apart from the six services, for tests, with the standard library
   alone, but says that every service exposes Prometheus metrics and sends
   traces. The alternative is metrics and traces, as [0006] says of every
   service: they'd need Prometheus's and OpenTelemetry's client libraries
   beside the standard library.
10. **A request without `Accept-Encoding`** ([SERVE-3]). Decided: the body
    itself, without a `Content-Encoding`, as to a request whose
    `Accept-Encoding` doesn't accept `gzip`, so a client that sends no
    `Accept-Encoding`, such as Python's `urllib.request` or `curl` without
    `--compressed`, reads the body as it's sent. The writers send
    `Accept-Encoding: gzip` ([FETCH-3]), so neither choice changes what
    they get, and no finding says what EDGAR sends such a request. The
    alternative is the stored object, with `Content-Encoding: gzip`, which
    RFC 9110 allows for a request without the header: the fake EDGAR would
    decode nothing, but such a client would get gzip data it doesn't
    decode.

## 11. Left to other specifications

This version leaves to others what the fake EDGAR relies on:

- **Deployment:** how the charts install it on kind, only for CI; its
  image, which holds `data/`; its certificate, from an authority of the
  release's own; and how kind's DNS sends the SEC's names to it
  ([`deployment.md`](deployment.md#the-fake-edgar)).
- **Deployment, with the fetcher:** how the fetcher trusts that authority
  when CI installs the fake EDGAR, in the version that deploys the fetcher
  ([`deployment.md`, §9](deployment.md#9-later-versions)).

It exposes no metrics and sends no traces, since it's not one of the
services the telemetry's specification observes
([`telemetry.md`](telemetry.md)), as the owner decided
([§10](#10-open-questions), question 9).

## 12. Changes to this document

Versions follow semantic versioning. Adding a statement, an optional field,
a kind of response a cue may give, a request of the admin port, or a check
is a minor version. Changing the meaning of a statement or a field, or
removing any of these, is a major version, since the tests of both writers
rely on them. The schemas in [`schemas/`](schemas/) that this document
names are part of it, so changing one changes it. IDs are never reused, and
a retired statement stays in the index.

[0001]: ../docs/decisions/0001-deploy-on-kubernetes-with-helm-on-kind.md
[0006]: ../docs/decisions/0006-write-the-services-in-go-and-python.md
[ACCESS-3]: controls.md#8-secaccess
[ADMIN-1]: #7-the-admin-port
[CHECK-1]: #8-the-checks
[CHECK-2]: #8-the-checks
[CHECK-3]: #8-the-checks
[CUE-1]: #5-cues
[CUE-2]: #5-cues
[CUE-3]: #5-cues
[FAKE-1]: deployment.md#the-fake-edgar
[FETCH-1]: snapshots.md#10-fetching
[FETCH-2]: snapshots.md#10-fetching
[FETCH-3]: snapshots.md#10-fetching
[FETCH-4]: snapshots.md#10-fetching
[FETCH-5]: snapshots.md#10-fetching
[FETCH-6]: snapshots.md#10-fetching
[FETCH-8]: snapshots.md#10-fetching
[FETCH-9]: snapshots.md#10-fetching
[FETCH-10]: snapshots.md#10-fetching
[FETCH-11]: snapshots.md#10-fetching
[FETCH-12]: snapshots.md#10-fetching
[FETCH-13]: snapshots.md#10-fetching
[FETCH-17]: snapshots.md#the-fetcher
[ID-1]: snapshots.md#10-fetching
[OBJ-1]: snapshots.md#5-objects
[REC-3]: snapshots.md#6-records
[REQ-1]: #6-the-request-log
[REQ-2]: #6-the-request-log
[REQ-3]: #6-the-request-log
[SERVE-1]: #4-what-it-serves
[SERVE-2]: #4-what-it-serves
[SERVE-3]: #4-what-it-serves
[SERVE-4]: #4-what-it-serves
[SERVE-5]: #4-what-it-serves
[SRC-4]: ../investigation/edgar.md#findings-so-far
[SRC-20]: ../investigation/edgar.md#findings-so-far
[SRC-22]: ../investigation/edgar.md#findings-so-far
[VER-1]: snapshots.md#7-versions
