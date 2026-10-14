# Specification: notices

**Status:** Version 0.1.0, draft, October 14, 2026. This document governs
what the discoverer makes of the fetcher's records: the filings each
Source's polls show, each with its times as given and the polls that bound
when each Source first showed it; each filing's files and their fetches;
the Backfills the scheduler hands it, and their progress; the coverage and
gaps of each Source's polls; the messages between the discoverer, the
scheduler, and the parse workers; and the views that give each lag. Its
tables, views, and messages are defined in [`schemas/`](schemas/). Where
code and this document disagree, the code is wrong or this document needs a
new version; say which before changing either.

It rests on the decisions to queue work in NATS JetStream ([0003]), to keep
the record in PostgreSQL 18 ([0004]), to write the discoverer in Python
([0006]), to control the pipeline through custom resources that the
scheduler reconciles ([0007]), and to poll each company's `submissions`,
keep every time as its source gives it, and measure each lag from the
record ([0010]). It reads and adds to the snapshot store's records, jobs,
and events, as that specification's version 1.0.0 has them
([`snapshots.md`](snapshots.md)), and it serves the Sources and Backfills
of the controls' version 1.0.0 ([`controls.md`](controls.md)). The pipeline
reads filings from the U.S. Securities and Exchange Commission's EDGAR
system; this document depends on how EDGAR behaves only where it cites a
finding.

## 1. Guarantees

Each guarantee holds because of the statements it lists ([§3](#3-statement-ids)).

1. **Each filing a Source shows after its first poll is fetched:** its
   header, its index page, and the XBRL instance the index page names.
   [FILE-1], [FILE-2], [FILE-3].
2. **Each filing keeps both its acceptance times as given:** `submissions`'
   `acceptanceDateTime`, and the digits of its header's
   `ACCEPTANCE-DATETIME` ([0010]). [SEEN-2], [FILE-3].
3. **The record says when each Source first showed each filing:** between
   two of its polls, which the record names, and whether a gap lies between
   them. [SEEN-2], [SEEN-3], [LAG-1].
4. **The record says where the pipeline was looking, and where it wasn't.**
   Each Source's gaps follow from its polls by one definition, in a view, so
   a record that arrives late changes what the view gives, and nothing
   stored. Each Source's status shows its open gap. [GAP-1], [GAP-2],
   [GAP-3], [NOTICE-1].
5. **Each lag is a query,** from each acceptance time to when each Source
   first showed the filing, and time zones are read only there. [LAG-1].
6. **A Backfill fetches its range's filings as a new filing's are fetched,**
   and its progress reaches its status. [ORDER-1], [LIST-1], [LIST-2],
   [LIST-3], [LIST-4], [LIST-5], [LIST-6], [NOTICE-2].
7. **Nothing the discoverer stores is changed, and a message delivered
   twice repeats its step harmlessly.** [TABLE-3], [DISC-1], [DISC-2],
   [MSG-2].

## 2. Terms

- **The discoverer:** the pipeline's service that reads the fetcher's
  events, finds the filings each version lists, and queues the fetches of
  their files ([0006]). **The scheduler**, **the fetcher**, and **the parse
  workers** are the services the design describes
  ([components](../docs/design.md#components)).
- **Record**, **version**, **object**, **job**, **event**, **feed**, and
  **timestamp** are as the store has them
  ([§2 of the store](snapshots.md#2-terms)). **The record**, without more,
  is what the pipeline's database holds: the store's table `records`, and
  this document's tables. **The bucket** is the cluster store's.
- **Source**, **Backfill**, **poll**, a poll that **succeeds**,
  **cadence**, **the release namespace**, and **requests are allowed** are
  as the controls have them ([§2 of the controls](controls.md#2-terms)). A
  Source's **polls** are the records of the jobs of kind `poll` that name
  it, of the URL each job names: should its URL change, its polls of the
  new URL are counted apart from those of the old.
- **List:** a version of a company's `submissions`, or of one of its
  further files, which lists filings in the columns that [SRC-12]
  describes. Of a company's `submissions`, `filings.recent` lists its
  recent filings ([SRC-9]), and `filings.files` names its further files.
- **Filing:** an accession number, `##########-##-######`, that a list
  names.
- **A filing's files:** its header, its index page, and the XBRL instance
  its index page names, if it names one ([§7](#7-a-filings-files)).
- **Held:** the record holds a URL when one of the URL's records names a
  version. A filing's file is held when the record holds the URL that
  [FILE-1] gives it, and a filing is held when its header and its index
  page are held, and so is the instance that the reading of its held index
  page names, if that reading names one.
- **Reading:** what the discoverer reads from a version: a list's filings,
  a header's acceptance time, or the instance an index page names.
- **A poll's window:** from its job's `queued_at` to its `expires_at`, when
  its Source's next poll is due ([JOB-2]).
- **Coverage**, **gap**, and **open gap:** as
  [§5](#5-polls-coverage-and-gaps) defines them.
- **Sighting:** a filing and a Source whose polls showed it, with the two
  polls that bound when it first showed it
  ([§6](#6-when-each-source-first-showed-each-filing)).
- **Order:** a message by which the scheduler hands a Backfill to the
  discoverer. **Notice:** a message the discoverer publishes, of a filing,
  of a gap, or of a Backfill's progress
  ([§9](#9-messages-and-processing)).

## 3. Statement IDs

Every normative statement in this document has an ID, such as `SEEN-2`: an
area and a number. The areas `TABLE`, `GAP`, `SEEN`, `FILE`, `ORDER`,
`LIST`, `NOTICE`, `MSG`, `DISC`, and `LAG` belong to this document. An ID
is assigned once and never reused or renumbered. A statement that is
dropped stays in the [index](#11-index), marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of the record, or of a schema in
  [`schemas/`](schemas/). Of a schema, tests check it by reading the
  schema, without a cluster or the network. Of the record, it holds
  whenever the discoverer isn't writing it, and tests on kind check it.
- A **rule** is how the discoverer or the scheduler behaves while it runs.
  Tests check it, as each service's own tests do, or on kind.

The index says how each statement is checked: by `tests`, or by tests on
`kind`. Every statement checked by `tests` or `kind` has at least one test
marked with its ID, and every marker names an ID in a specification's
index; Go tests carry IDs in the form the first Go test sets ([0006]). A
test, `tests/test_spec_coverage.py`, enforces both, except for a statement
whose tests a later task in the [plan](../docs/plan.md) writes. It lists
such a statement as awaiting that task, and fails if the statement has a
test or the plan marks the task done. [TABLE-1]'s and [MSG-1]'s tests come
with this version. The others await T24, which builds the discoverer, and
with it the scheduler's side of [§8](#8-backfills): its orders, and the
status it sets from notices, which [SOURCE-5] and [BACKFILL-3] await too.

## 4. The record of notices

The discoverer keeps what it finds in the pipeline's database, beside the
store's table `records`, in the tables and views that
[`schemas/notices.sql`](schemas/notices.sql) defines. Rows are only added,
as records are ([LOG-3]), so each row says what the discoverer found when
it found it. A time the discoverer copies from a job or a record is as
given there; one it adds itself is by its own clock.

| Table | One row for each | Columns in |
| --- | --- | --- |
| `polls` | Poll whose event the discoverer read | [§5](#5-polls-coverage-and-gaps) |
| `readings` | Version it read as a list, a header, or an index page | [§6](#6-when-each-source-first-showed-each-filing) |
| `filings` | Filing that a list it read names | [§6](#6-when-each-source-first-showed-each-filing) |
| `sightings` | Filing and Source whose polls showed it | [§6](#6-when-each-source-first-showed-each-filing) |
| `jobs` | Job it queued | [§7](#7-a-filings-files) |
| `backfills` | Backfill the scheduler ordered | [§8](#8-backfills) |
| `backfill_filings` | Filing in a Backfill's range | [§8](#8-backfills) |
| `backfill_listings` | Backfill whose lists it has all read | [§8](#8-backfills) |
| `backfill_outcomes` | Filing of a Backfill that was fetched or failed | [§8](#8-backfills) |

| View | One row for each | Columns in |
| --- | --- | --- |
| `held_files` | File of a filing that the record holds | [§7](#7-a-filings-files) |
| `gaps` | Gap in a Source's coverage, ended or open | [§5](#5-polls-coverage-and-gaps) |
| `backfill_progress` | Backfill, with its progress | [§8](#8-backfills) |
| `lags` | Sighting, with its times and the bounds of each lag | [§10](#10-times-and-lags) |

A column's type is PostgreSQL's, and a column is `NOT NULL` unless its type
says "or null". A view's columns are given with their meaning.

- **TABLE-1** (invariant) [`schemas/notices.sql`](schemas/notices.sql)
  defines the tables and views of these two tables, and no others. Each
  table has the columns its section gives it, in that order, each of its
  type, and `NOT NULL` unless its type says "or null"; each view has the
  columns its section gives it, in that order.
- **TABLE-2** (invariant) The pipeline's database holds the tables and
  views as `notices.sql` defines them, so it refuses a row that breaks a
  constraint there.
- **TABLE-3** (rule) Rows are only added to the tables: the discoverer's
  database role may insert and select them, each other role the pipeline's
  services use may only select them, and none may update, delete, or
  truncate them ([0004]).

## 5. Polls, coverage, and gaps

A Source's polls are due a cadence apart, and each poll's job expires when
the next is due, so a poll whose job expires before the fetcher takes it
isn't sent at all ([SOURCE-2], [JOB-2], [JOB-5]). A poll that is skipped,
expires unsent, or fails leaves its slot without a successful poll: the
record wasn't looking then. A job gives neither its due time nor its Source's cadence, so
coverage takes each poll's window, from when its job was queued until it
expired, as the cadence in force when it was queued.

The table `polls` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `record` | `bigint` | The poll's record, by its `id`: its URL, its times, and whether it succeeded. The primary key. |
| `source` | `text` | The Source its job names. |
| `queued_at` | `timestamp (3) with time zone` | Its job's `queued_at`. |
| `expires_at` | `timestamp (3) with time zone` | Its job's `expires_at`. |

Take a Source's successful polls of a URL in the order of their records'
`id`s, the order the fetcher added them:

- **Follows:** a successful poll follows the one before it when its job was
  queued less than its own window after the earlier one's job expired. It
  is then the poll of the slot that began when the earlier one's job
  expired, or of one that began sooner, if the cadence was shortened or
  polling began again, so no slot passed between them without a successful
  poll.
- **Coverage:** from a Source's first successful poll on, the time from each
  successful poll's request to the next one's, when the next follows it,
  and otherwise to when its job expired.
- **Gap:** when a successful poll doesn't follow the one before it, the time
  from when the earlier one's job expired to the later one's request.
- **Open gap:** once the latest successful poll's job has been expired for
  as long as its window, with no later successful poll in the record, a gap
  is open, from when that job expired.

So a poll sent late in its slot, after retries, leaves no gap, and a missed
poll leaves one of about a cadence. A pause, a stop after a `403`, or a
fetcher's handover that misses a poll leaves a gap until polling resumes.
A lengthened cadence leaves none. An open gap ends without leaving a gap
when a poll that follows is recorded late, after retries: `gaps` is a view,
so it then shows none.

The view `gaps` has these columns:

| Column | Meaning |
| --- | --- |
| `source` | The Source. |
| `url` | The URL its polls fetched. |
| `after_poll` | The record of the successful poll before the gap. |
| `before_poll` | The record of the successful poll that ended it; null while it's open. |
| `began_at` | When it began: when `after_poll`'s job expired. |
| `ended_at` | When it ended: `before_poll`'s `requested_at`; null while it's open. |

- **GAP-1** (rule) For each event of a poll, the discoverer adds the poll to
  `polls`, with its record's `id` and its job's `source`, `queued_at`, and
  `expires_at`.
- **GAP-2** (invariant) The view `gaps` gives each gap in each Source's
  coverage of each URL, as this section defines it: each that has ended,
  and each that is open when the view is read.
- **GAP-3** (rule) Within 10 seconds of a change in the open gap that `gaps`
  gives a Source and URL, the discoverer publishes their gap notice: when
  one opens, with its `began_at` as `open_since`, and when it ends, with
  `open_since` null. When it starts, it publishes one for each gap open
  then.

## 6. When each Source first showed each filing

Each successful poll of a company's `submissions` lists the company's
recent filings ([SRC-9]). The discoverer reads each such version, compares
the filings it lists with those the Source has shown before, and keeps, for
each filing each Source shows, the two polls that bound when it first
showed it ([0010]).

The table `readings` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `sha256` | `text` | The version read. The primary key. |
| `kind` | `text` | What it was read as: `list` ([SEEN-1]), `header`, or `index`, an index page ([FILE-3]). |
| `acceptance_datetime` | `text`, or null | In a header's reading, its `ACCEPTANCE-DATETIME`: 14 digits, as given ([SRC-1]). Null in any other. |
| `instance` | `text`, or null | In an index page's reading, the URL of the XBRL instance it names. Null if it names none, and in any other reading. |
| `error` | `text`, or null | Why the version couldn't be read as its kind; null if it was. |
| `tool` | `text` | The discoverer that read it: `discoverer@` and the 40-character commit its image was built from, as the fetcher's `tool` names the fetcher ([§6 of the store](snapshots.md#6-records)). |

The table `filings` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `accession_number` | `text` | The filing, `##########-##-######`. The primary key. |
| `form` | `text` | Its `form`, as the list the discoverer first read it from gives it. |
| `filing_date` | `text` | Its `filingDate`, as that list gives it. |
| `acceptance_date_time` | `text` | Its `acceptanceDateTime`, as that list gives it ([SRC-2]). |
| `listed_by` | `bigint` | The record of that list's fetch, by its `id`. |

The table `sightings` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `source` | `text` | The Source. |
| `url` | `text` | The URL its polls fetched. |
| `accession_number` | `text` | The filing. |
| `first_shown` | `bigint` | The first of the Source's polls, by its record's `id`, whose list named the filing. |
| `last_not_shown` | `bigint`, or null | The latest of its polls before that whose list the discoverer had read; null if there was none. |

- **SEEN-1** (rule) For each event of a successful poll of a company's
  `submissions`, `https://data.sec.gov/submissions/CIK##########.json`
  ([SRC-21]), the discoverer reads the version from the bucket as a list,
  and adds its reading, of kind `list`, unless `readings` holds one of that
  version. A company's `submissions` is read when it's a JSON object whose
  `filings.recent` holds the columns `accessionNumber`, `form`,
  `filingDate`, and `acceptanceDateTime`, each a list of strings, all of
  one length, and whose `filings.files` is a list of objects, each with a
  `name`, a `filingFrom`, and a `filingTo`, strings; a further file, when
  it's a JSON object that holds those columns itself ([SRC-12]). A version
  that isn't read has a reading with `error` set.
- **SEEN-2** (rule) For each filing that a poll's list names, if the list
  is read, the discoverer adds the filing to `filings`, unless it's there,
  with the values the list gives it and the poll's record as `listed_by`.
  Unless `sightings` holds the filing for the poll's Source and URL, it
  adds a sighting of it, with the poll as `first_shown`, and as
  `last_not_shown` the latest of the Source's polls of the URL in `polls`
  before it, by record `id`, whose version has a reading of kind `list`
  without an error, or null if there's none.
- **SEEN-3** (invariant) For each sighting, the list of its `first_shown`
  names its filing, and the list of its `last_not_shown`, when it has one,
  doesn't; that poll is an earlier one of the same Source and URL. So the
  Source first showed the filing after `last_not_shown`'s request was sent,
  and by the time `first_shown`'s response arrived.

These are the tightest bounds the record holds, unless a poll's event
reached the discoverer after a later poll's of the same Source, as when the
fetcher publishes an event again after a restart ([JOB-6]); they then
still bound when the Source first showed the filing, more loosely. A
filing a Source shows at its first poll whose list is read has no
`last_not_shown`, since the record doesn't say how long the Source had
shown it.

## 7. A filing's files

A filing's header gives its acceptance time ([SRC-1]), and its index page
names its XBRL instance ([SRC-20]), which the parse workers read. The
discoverer queues each fetch whether or not requests are allowed: while
they aren't, the fetcher sends none ([ACCESS-1]), and the jobs wait.

The table `jobs` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | `uuid` | The job's `id`, which the record of its fetch keeps as `job` ([STORE-1]). The primary key. |
| `kind` | `text` | Its `kind`: `filing` or `backfill`. |
| `backfill` | `uuid`, or null | The `uid` of the Backfill it's for, set exactly in a job of kind `backfill`. |
| `accession_number` | `text`, or null | The filing whose file it fetches; null in the job of a list. |
| `file` | `text` | What it fetches: a filing's `header`, `index`, or `instance`; or, for a Backfill, a company's `submissions` or a `further` file. |
| `url` | `text` | Its `url`. |
| `cause` | `bigint`, or null | The record, by its `id`, whose event led the discoverer to queue it; null in the first job of a Backfill, which its order led to. |
| `queued_at` | `timestamp (3) with time zone` | Its `queued_at`. |

The view `held_files` has these columns:

| Column | Meaning |
| --- | --- |
| `accession_number` | The filing. |
| `file` | Which of its files: `header`, `index`, or `instance`. |
| `url` | The file's URL ([FILE-1]). |
| `record` | The first of the URL's records that names a version. |
| `sha256` | That version. |

- **FILE-1** (rule) Each job the discoverer queues of a filing's file has
  the feed `filings`, as the investigation's records of filings do, and one
  URL: that of the file's first job in `jobs`, if it has one, and otherwise
  the one this table gives ([SRC-21]):

  | File | `file` | URL |
  | --- | --- | --- |
  | Header | `header` | `https://www.sec.gov/Archives/edgar/data/CIK/NODASHES/ACCESSION-index-headers.html` |
  | Index page | `index` | `https://www.sec.gov/Archives/edgar/data/CIK/NODASHES/ACCESSION-index.html` |
  | XBRL instance | `instance` | The URL its index page's reading names |

  `CIK` is the CIK of the company whose list led to the fetch, the poll's
  or the Backfill's, without leading zeros. `ACCESSION` is the accession
  number, and `NODASHES` the accession number without its dashes.
- **FILE-2** (rule) When [SEEN-2] adds a sighting with a `last_not_shown`,
  and `jobs` holds no job of its filing, the discoverer queues the fetches
  of the filing's header and its index page, as jobs of kind `filing`
  ([JOB-3]), and adds them to `jobs`. A filing that a Source shows with no
  `last_not_shown` isn't fetched for it: a Backfill fetches it, when the
  owner asks for one ([§8](#8-backfills)).
- **FILE-3** (rule) For each event of a job in `jobs` of a filing's header
  or index page, whose record names a version, the discoverer reads the
  version from the bucket, and adds its reading, unless `readings` holds
  one of that version:
  - a header's, of kind `header`, with the 14 digits of its
    `ACCEPTANCE-DATETIME`, as given ([SRC-1]), or with `error` set if it
    gives none;
  - an index page's, of kind `index`, with the URL that its row marked
    `EXTRACTED XBRL INSTANCE DOCUMENT` links, `https://www.sec.gov` and the
    link's path ([SRC-21]), or with none if no row is so marked. Its
    reading has `error` set if more than one row is, or if the link isn't
    a path in the filing's directory.

  When the reading of an index page names an instance that the record
  doesn't hold, and `jobs` holds no job of it with this event's record as
  its `cause`, the discoverer queues the instance's fetch, of the index
  page's job's kind, and its Backfill, if it has one, and adds it to
  `jobs`.
- **FILE-4** (rule) The discoverer queues no fetch again because it ended
  without a version. The record shows the failure, and a Backfill of the
  filing's date fetches the filing again ([LIST-2]).
- **FILE-5** (rule) When an event makes a filing complete, the discoverer
  publishes the filing's notice. A filing is complete when its header and
  its index page are held, each with a reading of its held version without
  an error, and the instance that the index page's reading names, if it
  names one, is held.
- **FILE-6** (invariant) The view `held_files` gives, for each file of each
  filing that `jobs` holds a job of, the first record of its URL that
  names a version, if the record holds one.

## 8. Backfills

The scheduler doesn't read EDGAR's content ([0006]), and the discoverer
can't read Backfills ([RBAC-4]). So the scheduler hands each Backfill to
the discoverer in an order; the discoverer lists the Backfill's filings,
queues their fetches, and reports its progress in backfill notices; and the
scheduler sets its status from them ([BACKFILL-1], [BACKFILL-3]). A
Backfill's lists are its company's `submissions` and the further files that
hold filings in its range.

The table `backfills` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `uid` | `uuid` | The Backfill's `metadata.uid`, from its order. The primary key. |
| `name` | `text` | Its name. |
| `cik` | `bigint` | Its `spec.cik`. |
| `filing_date_from` | `date` | Its `spec.filingDateFrom`. |
| `filing_date_to` | `date` | Its `spec.filingDateTo`. |
| `ordered_at` | `timestamp (3) with time zone` | Its order's `ordered_at`. |

The table `backfill_filings` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `backfill` | `uuid` | The Backfill. |
| `accession_number` | `text` | A filing in its range. |

The table `backfill_listings` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `backfill` | `uuid` | The Backfill. The primary key. |
| `listed_at` | `timestamp (3) with time zone` | When the discoverer had read each of its lists. |

The table `backfill_outcomes` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `backfill` | `uuid` | The Backfill. |
| `accession_number` | `text` | One of its filings. |
| `outcome` | `text` | `fetched` or `failed`, as the Backfill's status counts them ([§7 of the controls](controls.md#7-backfill)). |
| `at` | `timestamp (3) with time zone` | When the discoverer added it. |

The view `backfill_progress` has these columns:

| Column | Meaning |
| --- | --- |
| `uid` | The Backfill. |
| `name` | Its name. |
| `started_at` | When its first job was queued: the earliest `queued_at` of its jobs. |
| `listed_at` | Its listing's `listed_at`; null until it's listed. |
| `filings` | The number of its filings, once it's listed; null until then. |
| `fetched` | The number of its outcomes `fetched`. |
| `failed` | The number of its outcomes `failed`. |
| `completed_at` | Once it's listed and each of its filings has an outcome, the latest of its `listed_at` and its outcomes' `at`; null until then. |

- **ORDER-1** (rule) While requests are allowed, the scheduler publishes an
  order for each Backfill in the release namespace whose status has no
  `startTime`: when it first sees the Backfill, and again each time it
  becomes the active replica or requests are allowed again, until the
  Backfill's status has one. An order gives the Backfill's name, its
  `metadata.uid`, and its `spec`.
- **LIST-1** (rule) For each order whose `uid` isn't in `backfills`, the
  discoverer queues the fetch of its company's `submissions`, at
  `https://data.sec.gov/submissions/CIK##########.json` with the order's
  `cik` padded to ten digits ([SRC-21]), as a job of kind `backfill` with
  the feed `submissions` ([JOB-3]), and adds the Backfill to `backfills`
  and the job to `jobs`.
- **LIST-2** (rule) For each event of a Backfill's job of a list whose
  record names a version, the discoverer reads the list as [SEEN-1] does,
  and adds its reading, unless `readings` holds one. If the list is read:
  - for a company's `submissions`, it queues the fetch of each further file
    whose `filingFrom` to `filingTo` overlaps the Backfill's range, at
    `https://data.sec.gov/submissions/` followed by its `name` ([SRC-21]),
    as a job of kind `backfill` with the feed `submissions`. A further file
    lists no filing from outside those dates ([SRC-12]), so the others hold
    none in the range.
  - it adds each filing the list names with a `filingDate` in the range,
    from `filing_date_from` to `filing_date_to` inclusive, to `filings`,
    unless it's there, and to `backfill_filings`.
  - for each of those filings that is held, it adds the outcome `fetched`.
    For each other, it queues the fetch of each of its files that isn't
    held: its header, its index page, and the instance that the reading of
    its held index page names, if it names one. Each is a job of kind
    `backfill` ([FILE-1]).
- **LIST-3** (rule) Once each of a Backfill's lists is read, its company's
  `submissions` and each further file [LIST-2] queued, the discoverer adds
  its listing to `backfill_listings`. A Backfill one of whose lists can't
  be fetched or read is never listed, so it stays incomplete
  ([BACKFILL-3]).
- **LIST-4** (rule) For each event of a Backfill's job of a filing's file,
  unless the filing has an outcome for the Backfill, the discoverer adds
  one: `failed` if the event's record names no version, or `fetched` if the
  filing is then held.
- **LIST-5** (rule) After each change to a Backfill's rows, the discoverer
  publishes its backfill notice, with its progress as `backfill_progress`
  then gives it.
- **LIST-6** (invariant) The view `backfill_progress` gives each Backfill
  in `backfills`, with its progress as its columns say.
- **NOTICE-1** (rule) The scheduler sets each Source's `openGapSince` from
  the latest gap notice, by `at`, of its name and the URL its `spec` gives:
  to the notice's `open_since`, or, when that's null, it removes the field
  ([SOURCE-5]).
- **NOTICE-2** (rule) The scheduler sets each Backfill's status from the
  latest backfill notice, by `at`, of its `metadata.uid` ([BACKFILL-3]):
  `startTime` from `started_at`, `filingsFetched` from `fetched`, and
  `filingsFailed` from `failed`; `filings` from `filings` once that's set;
  and, once `completed_at` is set, `completionTime` from it, with the
  `Complete` condition `True`.

A Backfill removed from the cluster isn't stopped: the jobs already queued
for it are fetched, and its rows stay.

## 9. Messages and processing

### Streams

The scheduler hands Backfills to the discoverer, and the discoverer
publishes what it finds, in JSON messages in NATS JetStream ([0003]),
beside the store's jobs and events
([jobs and events](snapshots.md#jobs-and-events)):

| Stream | Subjects | Messages | Kept |
| --- | --- | --- | --- |
| `ORDERS` | `edgar.orders.backfill` | Orders, [`schemas/order.schema.json`](schemas/order.schema.json) | As a work queue: until the discoverer acknowledges the order |
| `NOTICES` | `edgar.notices.filing`, `edgar.notices.gap`, `edgar.notices.backfill` | Notices, [`schemas/notice.schema.json`](schemas/notice.schema.json) | Until each of the stream's consumers acknowledges the notice |

A notice's subject ends in its `kind`. The scheduler reads gap and backfill
notices, and the parse workers read filing notices, as the facts
specification will say.

An order's fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `backfill` | Required: a Backfill's name | The Backfill. |
| `uid` | Required: a UUID, in lower case | Its `metadata.uid`, which tells it from an earlier Backfill of the same name. |
| `cik` | Required: an integer from 1 to 9,999,999,999 | Its `spec.cik`. |
| `filing_date_from` | Required: a date, `YYYY-MM-DD` | Its `spec.filingDateFrom`. |
| `filing_date_to` | Required: a date, `YYYY-MM-DD` | Its `spec.filingDateTo`. |
| `ordered_at` | Required: a timestamp | When the scheduler published it. |

A notice's fields:

| Field | Type | Meaning |
| --- | --- | --- |
| `kind` | Required: `filing`, `gap`, or `backfill` | What it's a notice of. |
| `at` | Required: a timestamp | When the discoverer published it. |
| `accession_number` | Required in a filing notice, and only there: `##########-##-######` | The filing. |
| `form` | Required in a filing notice, and only there: a string | Its `form`, as `filings` holds it. |
| `filing_date` | Required in a filing notice, and only there: a string | Its `filing_date`, as `filings` holds it. |
| `listed_acceptance` | Required in a filing notice, and only there: a string | Its `acceptance_date_time`, as `filings` holds it ([SRC-2]). |
| `header_acceptance` | Required in a filing notice, and only there: 14 digits | The `acceptance_datetime` of its header's reading ([SRC-1]). |
| `files` | Required in a filing notice, and only there: an object of files | Its files, under `header`, `index`, and `instance` if it has one, each with its `feed`, its `url`, and the `sha256` that `held_files` gives it: what an import's manifest names an input by ([IMP-2]). |
| `source` | Required in a gap notice, and only there: a Source's name | The Source. |
| `url` | Required in a gap notice, and only there: a string | The URL its polls fetched. |
| `open_since` | Required in a gap notice, and only there: a timestamp, or null | When its open gap began; null when it has none. |
| `backfill` | Required in a backfill notice, and only there: a Backfill's name | The Backfill. |
| `uid` | Required in a backfill notice, and only there: a UUID, in lower case | Its `uid`. |
| `started_at` | Required in a backfill notice, and only there: a timestamp | Its `started_at`, as `backfill_progress` gives it. |
| `filings` | Required in a backfill notice, and only there: an integer ≥ 0, or null | Its `filings`. |
| `fetched` | Required in a backfill notice, and only there: an integer ≥ 0 | Its `fetched`. |
| `failed` | Required in a backfill notice, and only there: an integer ≥ 0 | Its `failed`. |
| `completed_at` | Required in a backfill notice, and only there: a timestamp, or null | Its `completed_at`. |

- **MSG-1** (invariant) [`schemas/order.schema.json`](schemas/order.schema.json)
  and [`schemas/notice.schema.json`](schemas/notice.schema.json) are JSON
  Schemas of an order and a notice, each with a property for each field in
  its table, of its type and form. Each requires the fields its table marks
  required, and a notice also those its table requires in a notice of its
  `kind`, and none it allows only in another kind.
- **MSG-2** (rule) A consumer of orders or notices ignores fields it doesn't
  know, as a consumer of jobs and events does ([JOB-7]), and acknowledges a
  message that doesn't validate against its schema, or a notice whose
  `kind` isn't its subject's, without acting on it.

### Processing

- **DISC-1** (rule) The discoverer reads every event of the stream
  `RECORDS` and every order. It reads poll events one at a time, in the
  order the stream holds them, so each Source's in the order the fetcher
  published them. It ignores an event of a job of kind `filing` or
  `backfill` that `jobs` doesn't hold. It acknowledges a message only once
  it has done with it what this document says, so a message it stopped on
  is delivered again ([0003]).
- **DISC-2** (rule) For each message, the discoverer adds the rows this
  document calls for in one transaction, leaving each row the record holds
  as it is. It then publishes each job in `jobs` whose `cause` is the
  message's record, or, for an order, the Backfill's first job, unless the
  table `records` holds the job's record; then it publishes the message's
  notices; then it acknowledges the message. So a message delivered again
  adds nothing twice, and the jobs its first delivery queued are published
  again, with their `id`s, until fetched: the fetcher fetches a job once
  ([JOB-6]).

## 10. Times and lags

The record keeps each time as its source gives it ([0010]): each record's
`requested_at` and `received_at` by the fetcher's clock
([§6 of the store](snapshots.md#6-records)), each filing's
`acceptanceDateTime` as its list gives it, and the digits of its header's
`ACCEPTANCE-DATETIME`. The view `lags` reads the acceptance times as
instants:

- **`submissions`' `acceptanceDateTime`** as the instant it states, in RFC
  3339 with a `Z` ([SRC-2]);
- **the header's digits,** which give no time zone ([SRC-1]), as US
  Eastern time, `America/New_York`. Read so, Microsoft's and Walmart's
  headers agree with their `submissions` ([SRC-2]), and all eight stored
  headers fall within EDGAR's hours ([SRC-13]). Read so, each of Apple's
  three stored headers is 4 hours earlier than its time in `submissions`
  ([SRC-2]), so Apple's lags from one of the two times show as outliers
  until a finding settles which is right ([0010]).

A Source first showed a filing after the request of its last poll that
didn't show it was sent, and by the time the response of the first that did
arrived ([SEEN-3]). So each lag, from an acceptance time to when the Source
first showed the filing, is more than its low bound and at most its high
bound.

The view `lags` has these columns:

| Column | Meaning |
| --- | --- |
| `source` | The Source. |
| `url` | The URL its polls fetched. |
| `accession_number` | The filing. |
| `form` | Its `form`, as `filings` holds it. |
| `listed_acceptance` | Its `acceptance_date_time`, as `filings` holds it. |
| `header_acceptance` | The `acceptance_datetime` of its header's reading, without an error; null until the record holds one. |
| `last_not_shown` | The sighting's `last_not_shown`. |
| `not_shown_requested_at` | That poll's `requested_at`. |
| `not_shown_received_at` | That poll's `received_at`. |
| `first_shown` | The sighting's `first_shown`. |
| `shown_requested_at` | That poll's `requested_at`. |
| `shown_received_at` | That poll's `received_at`. |
| `gap` | Whether a gap lies between the two polls; null without a `last_not_shown`. |
| `listed_lag_low` | `not_shown_requested_at`, less `listed_acceptance` read as an instant. |
| `listed_lag_high` | `shown_received_at`, less `listed_acceptance` read as an instant. |
| `header_lag_low` | `not_shown_requested_at`, less `header_acceptance` read as an instant. |
| `header_lag_high` | `shown_received_at`, less `header_acceptance` read as an instant. |

- **LAG-1** (invariant) The view `lags` gives each sighting, with its
  columns as this section's table says, reading each acceptance time as
  this section does. A lag is null when its times are, or when its
  acceptance time names no instant.

## 11. Index

| ID | Kind | Checked by | Statement |
| --- | --- | --- | --- |
| TABLE-1 | invariant | tests | `schemas/notices.sql` defines the tables and views, with their columns. |
| TABLE-2 | invariant | kind | The database holds them, and refuses a row that breaks their constraints. |
| TABLE-3 | rule | kind | Only the discoverer adds rows, and no one changes them. |
| GAP-1 | rule | tests | Each poll whose event the discoverer reads is added to `polls`. |
| GAP-2 | invariant | kind | `gaps` gives each Source's gaps, ended and open. |
| GAP-3 | rule | tests | A change in a Source's open gap is published within 10 seconds. |
| SEEN-1 | rule | tests | Each successful poll of a company's `submissions` is read as a list. |
| SEEN-2 | rule | tests | Each filing a list names is recorded, with a sighting for each Source that shows it. |
| SEEN-3 | invariant | kind | A sighting's two polls bound when its Source first showed the filing. |
| FILE-1 | rule | tests | A filing's files are fetched at the URLs SRC-21 gives, each at one URL. |
| FILE-2 | rule | tests | A filing a Source shows after its first poll gets its header and index page fetched. |
| FILE-3 | rule | tests | Headers and index pages are read, and the instance an index page names is fetched. |
| FILE-4 | rule | tests | A failed fetch isn't queued again by the discoverer. |
| FILE-5 | rule | tests | A filing whose files are held and read gets a filing notice. |
| FILE-6 | invariant | kind | `held_files` gives the first record that holds each of a filing's files. |
| ORDER-1 | rule | tests | The scheduler orders each Backfill not yet started, while requests are allowed. |
| LIST-1 | rule | tests | An order queues the fetch of its company's `submissions`, once. |
| LIST-2 | rule | tests | A Backfill's lists give its filings, its further files, and the fetches of its filings not held. |
| LIST-3 | rule | tests | A Backfill is listed once each of its lists is read. |
| LIST-4 | rule | tests | Each of a Backfill's filings gets one outcome, fetched or failed. |
| LIST-5 | rule | tests | Each change to a Backfill's progress is published. |
| LIST-6 | invariant | kind | `backfill_progress` gives each Backfill's progress. |
| NOTICE-1 | rule | tests | The scheduler sets a Source's open gap from its gap notices. |
| NOTICE-2 | rule | tests | The scheduler sets a Backfill's status from its backfill notices. |
| MSG-1 | invariant | tests | The schemas of an order and a notice have their tables' fields. |
| MSG-2 | rule | tests | Consumers ignore unknown fields, and acknowledge a malformed message without acting on it. |
| DISC-1 | rule | tests | The discoverer reads every event and order, poll events in order, and acknowledges each once done. |
| DISC-2 | rule | tests | Rows, then jobs, then notices, then the acknowledgement; a message delivered again adds nothing twice. |
| LAG-1 | invariant | kind | `lags` gives each sighting's times and the bounds of each lag. |

## 12. Open questions

1. **Whether a filing's files are in EDGAR's archive once its company's
   `submissions` lists it** (Q4). The record shows each fetch that ends
   without a version. If new filings' fetches do, a later version queues
   them again after a delay; until then, a Backfill fetches them again
   ([FILE-4]).
2. **The header's time zone** ([SRC-1], [SRC-2], [SRC-13]). The lags read
   it as US Eastern time. A finding that settles it changes the function
   `header_instant` in `notices.sql`, in a new version of this document,
   and nothing stored.
3. **Exact gaps.** A job gives neither its due time nor its Source's
   cadence, so [§5](#5-polls-coverage-and-gaps) takes each poll's window as
   the cadence. A shortened cadence can hide a poll missed at the change,
   and a lengthened one can show an open gap until the first poll at the
   new cadence. A poll's due time in its job, in a minor version of the
   store's specification, would let a later version of this one count
   missed slots exactly.
4. **A version that a later discoverer would read differently.** Each
   version's reading is kept once, by the discoverer that first read it,
   whose `tool` it names. How a corrected reading is recorded is for a
   later version.

### Left to other specifications

This version leaves to others what the notices rely on:

- **Deployment:** the streams `ORDERS` and `NOTICES`, with their replicas;
  the tables and views of `notices.sql`, made after the store's table, and
  their grants ([TABLE-3]); the discoverer's database role, and its Garage
  key, which may only read the bucket ([GARAGE-4]); and the durable
  consumers, each made before what it reads is published: the
  discoverer's of every subject of `RECORDS`, its poll events' with one
  unacknowledged at a time ([DISC-1]), and of `ORDERS`; the scheduler's of
  gap and backfill notices; and the parse workers' of filing notices
  ([`deployment.md`, §9](deployment.md#9-later-versions)).
- **Facts:** what the parse workers do with a filing notice, and how they
  find the complete filings whose notices they didn't read.
- **Telemetry:** the discoverer's metrics, such as readings with an error
  and fetches of filings' files that ended without a version; the views of
  coverage and lags that Grafana shows; and the alert on a gap longer than
  its Source's cadence.

## 13. Changes to this document

Versions follow semantic versioning. Adding a statement, an optional field,
a column, a view, or a check is a minor version. Changing the meaning of a
statement, a field, or a column, or what a view gives, is a major version,
and says how the rows already written are read. The schemas in
[`schemas/`](schemas/) that this document names are part of it, so changing
one changes it. IDs are never reused, and a retired statement stays in the
index.

[0003]: ../docs/decisions/0003-queue-work-in-nats-jetstream.md
[0004]: ../docs/decisions/0004-keep-the-record-in-postgresql-18.md
[0006]: ../docs/decisions/0006-write-the-services-in-go-and-python.md
[0007]: ../docs/decisions/0007-control-the-pipeline-through-custom-resources.md
[0010]: ../docs/decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[SRC-1]: ../investigation/edgar.md#findings-so-far
[SRC-2]: ../investigation/edgar.md#findings-so-far
[SRC-9]: ../investigation/edgar.md#findings-so-far
[SRC-12]: ../investigation/edgar.md#findings-so-far
[SRC-13]: ../investigation/edgar.md#findings-so-far
[SRC-20]: ../investigation/edgar.md#findings-so-far
[SRC-21]: ../investigation/edgar.md#findings-so-far
[ACCESS-1]: controls.md#8-secaccess
[BACKFILL-1]: controls.md#7-backfill
[BACKFILL-3]: controls.md#7-backfill
[RBAC-4]: controls.md#9-who-may-change-what
[SOURCE-2]: controls.md#6-source
[SOURCE-5]: controls.md#6-source
[GARAGE-4]: deployment.md#garage
[IMP-2]: snapshots.md#11-imports
[JOB-2]: snapshots.md#jobs-and-events
[JOB-3]: snapshots.md#jobs-and-events
[JOB-5]: snapshots.md#jobs-and-events
[JOB-6]: snapshots.md#jobs-and-events
[JOB-7]: snapshots.md#jobs-and-events
[LOG-3]: snapshots.md#8-history
[STORE-1]: snapshots.md#the-cluster-store
[TABLE-1]: #4-the-record-of-notices
[TABLE-3]: #4-the-record-of-notices
[GAP-1]: #5-polls-coverage-and-gaps
[GAP-2]: #5-polls-coverage-and-gaps
[GAP-3]: #5-polls-coverage-and-gaps
[SEEN-1]: #6-when-each-source-first-showed-each-filing
[SEEN-2]: #6-when-each-source-first-showed-each-filing
[SEEN-3]: #6-when-each-source-first-showed-each-filing
[FILE-1]: #7-a-filings-files
[FILE-2]: #7-a-filings-files
[FILE-3]: #7-a-filings-files
[FILE-4]: #7-a-filings-files
[ORDER-1]: #8-backfills
[LIST-1]: #8-backfills
[LIST-2]: #8-backfills
[LIST-3]: #8-backfills
[LIST-4]: #8-backfills
[LIST-5]: #8-backfills
[LIST-6]: #8-backfills
[NOTICE-1]: #8-backfills
[NOTICE-2]: #8-backfills
[MSG-1]: #streams
[MSG-2]: #streams
[DISC-1]: #processing
[DISC-2]: #processing
[LAG-1]: #10-times-and-lags
