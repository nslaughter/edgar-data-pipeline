# Specification: controls

**Status:** Version 1.0.0, draft, October 13, 2026. This document governs
the pipeline's controls: the custom resources `Source`, `Backfill`, and
`SecAccess`, whose definitions are in [`schemas/`](schemas/); the values the
umbrella chart renders them from, and the checks it makes before anything
reaches the cluster; what the scheduler and the fetcher do with each
resource and report in its status; and who may change what. Where code and
this document disagree, the code is wrong or this document needs a new
version; say which before changing either.

It rests on the decisions to control the pipeline through custom resources
that the scheduler reconciles ([0007]), to poll each company's
`submissions` at the cadence its `Source` holds, bounded in the chart
([0010]), and to send every request to the SEC through one active fetcher
([0005]). The pipeline reads filings from the U.S. Securities and Exchange
Commission's EDGAR system; this document depends on how EDGAR behaves only
where it cites a finding.

## 1. Guarantees

Each guarantee holds because of the statements it lists ([§3](#3-statement-ids)).

1. **Every control is a field of a resource, set from the chart's values.**
   The owner changes a control by changing the umbrella chart's values, and
   Helm applies it. The API server keeps it through restarts, and the
   resource's status shows what the pipeline did with it. [API-1], [API-2],
   [VAL-1], [RBAC-6].
2. **The chart refuses a configuration that polls too fast,** before
   anything reaches the cluster: each cadence is at least a second, and all
   the Sources together poll at most half the fetcher's limit. [VAL-2],
   [VAL-3], [API-3].
3. **Nothing is sent to the SEC until the owner allows it,** and a stop
   after a `403` holds until the owner acknowledges it, across restarts and
   handovers. [VAL-4], [ACCESS-1], [ACCESS-2], [ACCESS-3].
4. **A paused control queues nothing.** [SOURCE-1], [ACCESS-2].
5. **A backfill never delays the record of new filings.** [BACKFILL-2].
6. **Only the owner changes a control; the services change only status.**
   [RBAC-1], [RBAC-2], [RBAC-3], [RBAC-4], [RBAC-5], [RBAC-6].

## 2. Terms

- **The chart:** the umbrella Helm chart that installs the pipeline
  ([`deployment.md`](deployment.md)). **Its values** are those it's
  installed or upgraded with.
- **The charts:** the four Helm charts that install the pipeline, in order,
  the umbrella chart last; **the operators' chart** is the third, which
  installs the definitions ([§5 of `deployment.md`](deployment.md#5-dependencies)).
- **The release namespace:** the namespace the chart is installed in.
- **Control:** a field of a resource's `spec`. The owner sets controls; the
  pipeline's services set `status`.
- **Poll:** one fetch of a Source's URL, queued by the scheduler. A poll
  **succeeds** when its fetch's record names a version
  ([REC-3](snapshots.md#6-records)).
- **Cadence:** a Source's `spec.cadenceSeconds`, the seconds from one of
  its polls to the next.
- **The budget:** half the fetcher's limit of 5 requests a second
  ([FETCH-6](snapshots.md#10-fetching), [0005]): 2.5 requests a second,
  which all the Sources not paused share. The other half is left for each
  new filing's fetches and for backfills.
- **Timestamp:** a time in UTC, RFC 3339 with milliseconds and `Z`, as the
  store's records give `requested_at` ([§6 of the store](snapshots.md#6-records)),
  such as `2026-10-06T18:04:12.345Z`.
- **Requests are allowed** while the SecAccess `cluster` exists, isn't
  paused, and has no stop the owner hasn't acknowledged
  ([§8](#8-secaccess)).

## 3. Statement IDs

Every normative statement in this document has an ID, such as `SOURCE-1`:
an area and a number. The areas `API`, `VAL`, `SOURCE`, `BACKFILL`,
`ACCESS`, and `RBAC` belong to this document. An ID is assigned once and
never reused or renumbered. A statement that is dropped stays in the
[index](#10-index), marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of the definitions in `spec/schemas/`, or
  of the chart as committed and what it renders. Tests check it by reading
  the definitions, or by rendering the chart with `helm template`, without a
  cluster or the network.
- A **rule** is how the API server, the pipeline's services, or the owner
  behaves. Tests check the API server's and the services' against a real
  API server with envtest, as the scheduler is tested ([0007]), or against
  the installed chart on kind.

The index says how each statement is checked: by `tests`, by tests on
`kind`, or by the `operator`, the owner, who keeps it. Every statement
checked by `tests` or `kind` has at least one test marked with its ID, and
every marker names an ID in a specification's index; Go tests carry IDs in
the form the first Go test sets ([0006]). A test,
`tests/test_spec_coverage.py`, enforces both, except for a statement whose
tests a later task in the [plan](../docs/plan.md) writes. It lists such a
statement as awaiting that task, and fails if the statement has a test or
the plan marks the task done. API-1's tests come with this version; the
other statements await the tasks that build what they describe: T22 the
Go types that generate the definitions, the chart's resources, and the
scheduler; T23 the fetcher; T24 the discoverer; and T28 the monitoring,
which grants kube-state-metrics its access. [RBAC-5] waits for T28 too,
since it checks every ServiceAccount in [§9](#9-who-may-change-what)'s
table: the fetcher's comes with T23, and kube-state-metrics gets its
grants with T28.

## 4. The resources

The three resources are in the API group `edgar.nathanslaughter.com`, at
version `v1alpha1` ([0007]). Each definition is a
`CustomResourceDefinition`, written here first; the scheduler's Go types are
written to generate the same. The operators' chart installs and upgrades
them, before the chart renders any resource of theirs
([`deployment.md`](deployment.md#definitions)).

| Kind | Plural | Scope | Definition | One for each |
| --- | --- | --- | --- | --- |
| `Source` | `sources` | Namespaced | [`schemas/sources.yaml`](schemas/sources.yaml) | URL the pipeline polls |
| `Backfill` | `backfills` | Namespaced | [`schemas/backfills.yaml`](schemas/backfills.yaml) | range of a company's past filings to fetch once |
| `SecAccess` | `secaccesses` | Cluster | [`schemas/secaccesses.yaml`](schemas/secaccesses.yaml) | cluster, named `cluster` |

`SecAccess` is cluster-scoped because there is one per cluster ([0007]):
one fetcher sends every request ([0005]), so whether it may is a fact about
the cluster. A second release of the chart would render a second SecAccess
`cluster`, which Helm refuses to install, since the first release owns it.

- **API-1** (invariant) `spec/schemas/` holds the three definitions in the
  table, each a `CustomResourceDefinition` of `apiextensions.k8s.io/v1`
  named `PLURAL.edgar.nathanslaughter.com`, with the table's kind, plural,
  and scope, and one version, `v1alpha1`, served and stored, with the
  status subresource.
- **API-2** (invariant) The definitions the operators' chart installs, and
  those generated from the scheduler's Go types, are the ones in
  `spec/schemas/`, apart from `description` fields and annotations.
- **API-3** (rule) The API server refuses a resource that its definition
  refuses: one without a `spec`, or without a field the tables below mark
  required, or with a value outside a field's type or form; a Source whose `cadenceSeconds` is
  less than 1; a Backfill whose range ends before it starts, or whose
  `spec` changes; and a SecAccess named anything but `cluster`.

## 5. The chart's values

The owner sets every control in the chart's values, and changes it with
`helm upgrade` ([0010]). These values allow requests, poll Apple's
`submissions` every 10 seconds, and backfill Apple's filings of 2025; each
Source's and each Backfill's key is its name:

```yaml
controls:
  secAccess:
    paused: false
  sources:
    apple-submissions:
      feed: submissions
      url: https://data.sec.gov/submissions/CIK0000320193.json
      cadenceSeconds: 10
  backfills:
    apple-2025:
      cik: 320193
      filingDateFrom: "2025-01-01"
      filingDateTo: "2025-12-31"
```

- **VAL-1** (invariant) The chart renders one Source for each entry of
  `controls.sources`, and one Backfill for each entry of
  `controls.backfills`, each named by the entry's key, in the release
  namespace, with the entry as its `spec`; and one SecAccess, named
  `cluster`, with `controls.secAccess` as its `spec`. It renders no other
  Source, Backfill, or SecAccess, and no `status`.
- **VAL-2** (invariant) The chart refuses values from which it would render
  a resource that the API server refuses, by its definition ([API-3]) or by
  its name, apart from a change to a Backfill's `spec`, which depends on the
  `spec` the cluster holds: `helm template`, `helm install`, and
  `helm upgrade` fail. Its `values.schema.json` requires each Source's
  `cadenceSeconds`, an integer of at least 1 ([0010]).
- **VAL-3** (invariant) The chart refuses values whose Sources not paused
  would together poll faster than the budget: those whose sum of
  `1 / cadenceSeconds` is more than 5/2. A sum of exactly 5/2 passes: 25
  Sources at 10 seconds, or two at 1 second and one at 2, pass, while 26 at
  10 seconds, or three at 1 second, fail.
- **VAL-4** (invariant) The chart's default values pause the SecAccess, so
  the chart installed with them sends nothing to the SEC until the owner's
  values allow it.

Helm runs one operation on a release at a time, so two changes can't each
pass [VAL-3] and together exceed the budget ([0010]). Nothing reaches the
cluster another way ([RBAC-6]), so no admission webhook checks the
resources. Should anything, the definition still refuses a cadence under a
second, and the scheduler reports a total over the budget ([SOURCE-3]).

The chart renders without the cluster, so it can't see that a Backfill's
`spec` differs from the one the cluster holds. The API server refuses that
change ([API-3]), so `helm upgrade` fails then, though `helm template`
passes.

The chart's values start with a Source for each company's `submissions`, at
10 seconds ([0010]); a cadence is changed there, and isn't a statement.
Whether a Source's URL must be in `data/scope.json` is for the store's
version 1.0.0.

## 6. Source

One URL the pipeline polls, such as one company's `submissions`, its notice
source ([0010]). Continuous and daily operation differ only in cadence.

Its `spec`:

| Field | Type | Meaning |
| --- | --- | --- |
| `feed` | Required: a name, `[a-z0-9][a-z0-9-]*` | The feed its polls are recorded under, as in the store ([§2 of the store](snapshots.md#2-terms)), such as `submissions`. |
| `url` | Required: a string beginning `https://www.sec.gov/` or `https://data.sec.gov/` | The URL polled ([FETCH-1](snapshots.md#10-fetching)). |
| `cadenceSeconds` | Required: an integer from 1 to 2,147,483,647 | The seconds from one poll to the next. Each Source states its own, since no one default suits both a company's `submissions` and a daily index ([0010]). |
| `paused` | A boolean; `false` if absent | Whether its polls are paused. |

Its `status`, which the scheduler sets:

| Field | Type | Meaning |
| --- | --- | --- |
| `lastPollTime` | A timestamp | When the last poll whose record is written was sent: its record's `requested_at`. |
| `lastSuccessfulPollTime` | A timestamp | When the last poll that succeeded was sent. |
| `openGapSince` | A timestamp | Present only while the record holds an open gap for the Source, as the notices specification defines a gap: when the gap began. |
| `conditions` | A list of Kubernetes conditions | `Polling` and `WithinBudget`, below. |

| Condition | `True`, with its reason | `False`, with its reason |
| --- | --- | --- |
| `Polling` | `Polling`: the scheduler queues its polls. | The first of these that applies, in this order. `NoSecAccess`: there is no SecAccess `cluster`. `RequestsStopped`: the SecAccess has a stop the owner hasn't acknowledged. `RequestsPaused`: the SecAccess is paused. `Paused`: the Source is paused. |
| `WithinBudget` | `WithinBudget`: the Sources not paused in the release namespace together poll within the budget. | `OverBudget`: they don't; its message gives their total, in requests a second. |

- **SOURCE-1** (rule) The scheduler queues polls only of Sources in the
  release namespace, and of each only while it's polling: while it isn't
  paused and requests are allowed. Its `Polling` condition says whether it
  is, and if not, why not: the first reason that applies of `NoSecAccess`,
  `RequestsStopped`, `RequestsPaused`, and `Paused`, in that order.
- **SOURCE-2** (rule) A Source's first poll is queued when it begins
  polling: when it's created or unpaused, when requests are allowed again,
  or when a scheduler replica becomes the active one. Each later poll is
  due `cadenceSeconds` after the one before it was due, is queued no
  earlier, and is skipped if it can't be queued before the one after it is
  due. A new `cadenceSeconds` applies from the next poll, which is due the
  new cadence after the last poll queued.
- **SOURCE-3** (rule) The scheduler sets each Source's `WithinBudget`
  condition: `False` while the Sources not paused in the release namespace
  together poll faster than the budget, as [VAL-3] computes it, and `True`
  otherwise. Over the budget, it polls them as it would otherwise; the
  fetcher's limit still holds ([FETCH-6](snapshots.md#10-fetching)).
- **SOURCE-4** (rule) Each poll is a fetch of the Source's `url`, recorded
  under its `feed`. The scheduler sets `lastPollTime` and
  `lastSuccessfulPollTime` from those records.
- **SOURCE-5** (rule) The scheduler sets `openGapSince` while the record
  holds an open gap for the Source, to when it began, and removes it when
  the gap closes.

## 7. Backfill

A company's past filings, over a range of filing dates, fetched once
([0007]).

Its `spec`, which can't change once the Backfill is created:

| Field | Type | Meaning |
| --- | --- | --- |
| `cik` | Required: an integer from 1 to 9,999,999,999 | The company's CIK, as in its `submissions` URL. |
| `filingDateFrom` | Required: a date, `YYYY-MM-DD` | The first filing date in the range. |
| `filingDateTo` | Required: a date, `YYYY-MM-DD`, not before `filingDateFrom` | The last filing date in the range. |

To fetch another range, the owner adds another Backfill.

Its `status`, which the scheduler sets:

| Field | Type | Meaning |
| --- | --- | --- |
| `filings` | An integer ≥ 0 | The filings in the range, once they're listed. |
| `filingsFetched` | An integer ≥ 0 | Of those, the filings whose fetches all succeeded, or were already held ([BACKFILL-1]). |
| `filingsFailed` | An integer ≥ 0 | Of those, the filings with a fetch that ended without a version. |
| `startTime` | A timestamp | When its first fetch was queued. |
| `completionTime` | A timestamp | When its last filing was fetched or failed, or, in a range with no filings, when they were listed. |
| `conditions` | A list of Kubernetes conditions | `Complete`, below. |

| Condition | `True`, with its reason | `False`, with its reason |
| --- | --- | --- |
| `Complete` | `Complete`: the filings in the range are listed, and each is fetched or failed. | `InProgress`: they aren't listed yet, or some aren't fetched or failed yet. |

- **BACKFILL-1** (rule) For each Backfill in the release namespace, the
  pipeline fetches each filing that the company's `submissions` lists,
  with its further files
  ([SRC-12](../investigation/edgar.md#findings-so-far)), with a
  `filingDate` from `filingDateFrom` to `filingDateTo`, inclusive. It
  fetches each as it fetches a new filing, as the notices specification
  says, except a filing whose fetches the record already holds, each with a
  version. So a Backfill interrupted by a restart continues where it
  stopped.
- **BACKFILL-2** (rule) The fetcher sends a Backfill's fetch only while no
  poll, and no fetch that isn't a Backfill's, is waiting to be sent, so a
  backfill never delays the record of new filings ([0007]).
- **BACKFILL-3** (rule) The scheduler keeps each Backfill's `status` as the
  table says: its `Complete` condition is `True`, with `completionTime`
  set, once the filings in the range are listed and each is fetched or
  failed. So a range with no filings is complete once they're listed.

## 8. SecAccess

Whether the pipeline may send requests to the SEC at all: one per cluster,
named `cluster` ([0007]).

Its `spec`:

| Field | Type | Meaning |
| --- | --- | --- |
| `paused` | A boolean; `false` if absent | Whether every request to the SEC is paused. |
| `acknowledgedStopAt` | A timestamp | The `at` of the stop the owner acknowledges. |

Its `status`, which the fetcher sets:

| Field | Type | Meaning |
| --- | --- | --- |
| `stop` | An object | The last stop: the fetch that got a `403`. |
| `stop.at` | Required: a timestamp | When that fetch's request was sent: its record's `requested_at`. |
| `stop.url` | Required: a string | That fetch's URL. |

Requests are allowed while the SecAccess `cluster` exists, isn't paused,
and has no `stop`, or an `acknowledgedStopAt` equal to its `stop.at`. An
acknowledgement names one stop, so a later `403` stops requests again until
the owner acknowledges that one too. The owner acknowledges a stop by
copying its `at` into the values, as `controls.secAccess.acknowledgedStopAt`,
and running `helm upgrade`.

- **ACCESS-1** (rule) The fetcher sends a request to the SEC only while
  requests are allowed.
- **ACCESS-2** (rule) While requests aren't allowed, the scheduler queues
  no poll and nothing for a Backfill.
- **ACCESS-3** (rule) After a `403`, the fetcher records the stop in the
  SecAccess's `status`, in place of any earlier one, before it sends
  another request, and sends none until the owner acknowledges it
  ([FETCH-9](snapshots.md#10-fetching), [0005]). A fetcher that takes over
  the Lease reads the stop there, and sends nothing until the owner
  acknowledges it.

## 9. Who may change what

The owner sets each resource's `spec`, through the chart. The services set
only `status`: the scheduler a Source's and a Backfill's, and the fetcher
the SecAccess's ([0007]). kube-state-metrics reads the resources, to export
their status to Prometheus. None of them adds finalizers, labels, or
annotations, so none needs to change a resource itself.

| ServiceAccount | Sources and Backfills | Their `status` | The SecAccess | Its `status` |
| --- | --- | --- | --- | --- |
| The scheduler's | `get`, `list`, `watch` | `update`, `patch` | `get`, `list`, `watch` | None |
| The fetcher's | None | None | `get`, `list`, `watch` | `update`, `patch` |
| kube-state-metrics' | `get`, `list`, `watch` | None | `get`, `list`, `watch` | None |

- **RBAC-1** (invariant) The chart grants the scheduler's ServiceAccount
  the verbs in its row of the table on the three resources, and no others.
- **RBAC-2** (invariant) The chart grants the fetcher's ServiceAccount the
  verbs in its row, and no others.
- **RBAC-3** (invariant) The chart grants kube-state-metrics'
  ServiceAccount the verbs in its row, and no others.
- **RBAC-4** (invariant) No Role or ClusterRole the charts render, their
  dependencies' included, grants any verb on the three resources or their
  `status`, by name or by a wildcard, except as the table says. None grants
  `create`, `update`, `patch`, `delete`, or `deletecollection` on the
  resources themselves.
- **RBAC-5** (rule) In the installed charts, each of the charts'
  ServiceAccounts can do with the three resources exactly what the table
  grants it, and those not in the table nothing, as the API server's
  access reviews answer, whatever binding grants it.
- **RBAC-6** (rule) The owner changes the resources only through the
  chart's values, with `helm install` or `helm upgrade`, never with
  `kubectl` or another client, and never changes their `status`. `kubectl`
  is for reading them.

## 10. Index

| ID | Kind | Checked by | Statement |
| --- | --- | --- | --- |
| API-1 | invariant | tests | The three definitions are in `spec/schemas/`, in one group, at `v1alpha1`. |
| API-2 | invariant | tests | The operators' chart's definitions and those generated from Go are those in `spec/schemas/`. |
| API-3 | rule | tests | The API server refuses a resource its definition refuses. |
| VAL-1 | invariant | tests | The chart renders the resources from `controls` in its values, and no others. |
| VAL-2 | invariant | tests | The chart refuses values that would render a resource the API server refuses, but for a changed Backfill. |
| VAL-3 | invariant | tests | The chart refuses Sources that together would poll faster than the budget. |
| VAL-4 | invariant | tests | The chart's default values pause the SecAccess. |
| SOURCE-1 | rule | tests | Only a Source that's polling is polled, and its condition says so. |
| SOURCE-2 | rule | tests | Polls are due a cadence apart, from when a Source begins polling. |
| SOURCE-3 | rule | tests | Each Source's condition says whether the Sources are within the budget. |
| SOURCE-4 | rule | tests | Each poll is recorded under its Source's feed, and its times set the status. |
| SOURCE-5 | rule | tests | A Source's status gives when its open gap began. |
| BACKFILL-1 | rule | tests | A Backfill fetches its range's filings, except those already held. |
| BACKFILL-2 | rule | kind | A Backfill's fetches wait for every other fetch. |
| BACKFILL-3 | rule | tests | A Backfill's status counts its filings and says when it's complete. |
| ACCESS-1 | rule | kind | The fetcher sends requests only while they're allowed. |
| ACCESS-2 | rule | tests | While requests aren't allowed, the scheduler queues nothing. |
| ACCESS-3 | rule | kind | A `403` is recorded as a stop, which holds until the owner acknowledges it. |
| RBAC-1 | invariant | tests | The scheduler may read the resources, and change only Sources' and Backfills' status. |
| RBAC-2 | invariant | tests | The fetcher may read the SecAccess, and change only its status. |
| RBAC-3 | invariant | tests | kube-state-metrics may only read the resources. |
| RBAC-4 | invariant | tests | No other role in the charts grants access, and none lets a service change a `spec`. |
| RBAC-5 | rule | kind | On the cluster, each ServiceAccount can do exactly what the table grants. |
| RBAC-6 | rule | operator | The owner changes the resources only through the chart's values. |

## 11. Other specifications

This version leaves to others what the controls rely on:

- **The store's version 1.0.0:** how a poll or a fetch is queued to the
  fetcher; the records it writes, whose times set a Source's status; and
  whether a Source's URL must be in `data/scope.json`.
- **Notices:** what a gap is; which files a filing's fetches include,
  which a Backfill fetches too; and which service lists a Backfill's
  filings and queues their fetches, since the scheduler doesn't read
  EDGAR's content ([0006]).
- **Telemetry:** the metrics kube-state-metrics exports from status, and
  the alerts on them.

The deployment's version 1.0.0 says how the definitions are installed and
upgraded: as templates of the operators' chart, which is released before
the chart ([CRD-2](deployment.md#definitions)).

## 12. Changes to this document

Versions follow semantic versioning. Adding a statement, an optional field,
a condition, a reason, or a check is a minor version. Changing the meaning
of a statement or a field, or a definition so that it refuses a resource it
accepted, is a major version, and says what happens to the resources the
cluster holds. The definitions in `spec/schemas/` are part of this
document, so changing one changes it. IDs are never reused, and a retired
statement stays in the index.

### From version 0.1.0

Version 0.1.0 had the umbrella chart install the definitions. The
deployment's version 1.0.0 installs them from the operators' chart instead,
released before it ([0011]). So [API-2] now names the operators' chart, and
[RBAC-4] and [RBAC-5] cover every chart's roles and ServiceAccounts, not
only the umbrella chart's. No resource of these kinds was created under
version 0.1.0, so none the cluster holds changes.

[0005]: ../docs/decisions/0005-send-sec-requests-through-one-active-fetcher.md
[0006]: ../docs/decisions/0006-write-the-services-in-go-and-python.md
[0007]: ../docs/decisions/0007-control-the-pipeline-through-custom-resources.md
[0010]: ../docs/decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[0011]: ../docs/decisions/0011-install-the-definitions-and-operators-before-the-umbrella-chart.md
[API-1]: #4-the-resources
[API-2]: #4-the-resources
[API-3]: #4-the-resources
[VAL-1]: #5-the-charts-values
[VAL-2]: #5-the-charts-values
[VAL-3]: #5-the-charts-values
[VAL-4]: #5-the-charts-values
[SOURCE-1]: #6-source
[SOURCE-3]: #6-source
[BACKFILL-1]: #7-backfill
[BACKFILL-2]: #7-backfill
[ACCESS-1]: #8-secaccess
[ACCESS-2]: #8-secaccess
[ACCESS-3]: #8-secaccess
[RBAC-1]: #9-who-may-change-what
[RBAC-2]: #9-who-may-change-what
[RBAC-3]: #9-who-may-change-what
[RBAC-4]: #9-who-may-change-what
[RBAC-5]: #9-who-may-change-what
[RBAC-6]: #9-who-may-change-what
