# Specification: notices

**Status:** Version 1.0.0, draft, November 9, 2026. This document governs
what the discoverer makes of the fetcher's records: the filings each
Source's polls show, each with its times as given and the polls that bound
when each Source first showed it; each day's check of the latest-filings
feed against that day's master index; each filing's files and their
fetches; the Backfills the scheduler hands it, and their progress; the
coverage and gaps of each Source's polls; the messages between the
discoverer, the scheduler, and the parse workers; and the views that give
each lag and each day's check. Its
tables, views, and messages are defined in [`schemas/`](schemas/). Where
code and this document disagree, the code is wrong or this document needs a
new version; say which before changing either.

It rests on the decisions to queue work in NATS JetStream ([0003]), to keep
the record in PostgreSQL 18 ([0004]), to write the discoverer in Python
([0006]), to control the pipeline through custom resources that the
scheduler reconciles ([0007]), to keep every time as its source gives it
and measure each lag from the record ([0010]), and to poll the
latest-filings feed for every filer's filings and check each day against
its master index ([0015]), which supersedes the part of [0010] that makes
each company's `submissions` the notice source. It reads and adds to the snapshot store's records, jobs,
and events, as that specification's version 1.0.0 has them
([`snapshots.md`](snapshots.md)), and it serves the Sources and Backfills
of the controls' version 1.0.0 ([`controls.md`](controls.md)). The pipeline
reads filings from the U.S. Securities and Exchange Commission's EDGAR
system; this document depends on how EDGAR behaves only where it cites a
finding.

## 1. Guarantees

Each guarantee holds because of the statements it lists ([§3](#3-statement-ids)).

1. **Each filing a Source shows after its first poll is fetched:** its
   header, its index page, and the XBRL instance the index page names. So
   is each filing that a day's master index lists and the feed didn't
   show, from the first day checked. [FILE-1], [FILE-2], [FILE-3],
   [DAY-4].
2. **Each filing keeps its acceptance times as given:** the time the
   version the discoverer first added the filing from gives it, which is
   `submissions`' `acceptanceDateTime` or the feed's `updated`, where that
   version gives one, and the digits of its header's `ACCEPTANCE-DATETIME`
   ([0010], [0015]). [SEEN-2], [LIST-2], [DAY-3], [FILE-3].
3. **The record says when each Source first showed each filing:** between
   two of its polls, which the record names, and whether a gap lies between
   them. [SEEN-2], [SEEN-3], [LAG-1].
4. **The record says where the pipeline was looking, and where it wasn't.**
   Each Source's gaps follow from its polls by one definition, in a view, so
   a record that arrives late changes what the view gives, and nothing
   stored. A poll of the feed that doesn't reach back to the one before
   leaves a gap, though both succeeded. Each Source's status shows its open
   gap. [GAP-1], [GAP-2], [GAP-3], [GAP-4], [NOTICE-1].
5. **Each lag is a query,** from each acceptance time to when each Source
   first showed the filing, and time zones are read only there. [LAG-1].
6. **A Backfill fetches its range's filings as a new filing's are fetched,**
   and its progress reaches its status. [ORDER-1], [LIST-1], [LIST-2],
   [LIST-3], [LIST-4], [LIST-5], [LIST-6], [NOTICE-2].
7. **Nothing the discoverer stores is changed, and a message delivered
   twice repeats its step harmlessly.** [TABLE-3], [DISC-1], [DISC-2],
   [MSG-2].
8. **Each day from the first day checked is checked against its master
   index,** and the record counts each filing that only one of the feed
   and the master indexes showed. [FEED-1], [FEED-2], [DAY-1], [DAY-2],
   [DAY-3], [DAY-5], [DAY-6].

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
- **The feed:** EDGAR's latest-filings feed, at the URL the scope gives it
  ([SRC-26]), which lists the latest filings of every filer and form,
  newest first, with an **entry** for each of a filing's parties
  ([SRC-27]). Its versions are read as the feed ([FEED-1]).
- **Listing:** a version of a quarter's daily index listing, which names
  the master index of each day it shows ([SRC-34]). **Master index:** a
  version of a day's master index, which lists the filings EDGAR
  disseminated that day, on a line for each CIK a filing names
  ([SRC-35]).
- **Day:** a date as the daily indexes name it, in US Eastern time
  ([SRC-5], [SRC-34]). **The first day checked** and a **day checked** are
  as [the daily check](#the-daily-check) defines them.
- **Filing:** an accession number, `##########-##-######`, that a list, a
  version of the feed, or a master index names.
- **A filing's files:** its header, its index page, and the XBRL instance
  its index page names, if it names one ([§7](#7-a-filings-files)).
- **Held:** the record holds a URL when one of the URL's records names a
  version. A filing's file is held when the record holds the URL that
  [FILE-1] gives it, and a filing is held when its header and its index
  page are held, and so is the instance that the reading of its held index
  page names, if that reading names one.
- **Reading:** what the discoverer reads from a version: the filings of a
  list, of the feed, or of a master index; the days a listing shows; a
  header's acceptance time; or the instance an index page names.
- **A poll's window:** from its job's `queued_at` to its `expires_at`, when
  its Source's next poll is due ([JOB-2]).
- **Reaches back**, **coverage**, **gap**, and **open gap:** as
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
area and a number. The areas `TABLE`, `GAP`, `SEEN`, `FEED`, `DAY`,
`FILE`, `ORDER`, `LIST`, `NOTICE`, `MSG`, `DISC`, and `LAG` belong to this
document. An ID
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
| `feed_polls` | Successful poll of the feed whose event it read | [§5](#5-polls-coverage-and-gaps) |
| `readings` | Version it read as a list, the feed, a listing, a master index, a header, or an index page | [§6](#6-when-each-source-first-showed-each-filing) |
| `filings` | Filing that a list, a version of the feed, or a master index it read names | [§6](#6-when-each-source-first-showed-each-filing) |
| `sightings` | Filing and Source whose polls showed it | [§6](#6-when-each-source-first-showed-each-filing) |
| `entries` | Entry of a filing, in the first version of the feed it read that names the filing | [The latest-filings feed](#the-latest-filings-feed) |
| `listing_days` | Day a listing it read shows | [The daily check](#the-daily-check) |
| `index_filings` | Filing a master index it read lists | [The daily check](#the-daily-check) |
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
| `checks` | Day checked, with the filings only one source showed | [The daily check](#the-daily-check) |
| `listings` | Quarter's listing a Source's polls fetched | [The daily check](#the-daily-check) |

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

The feed lists only its latest entries ([SRC-27]), so it can show a filing
between two polls that neither names, when they come far enough apart
([SRC-28]). A poll of the feed that names none of the filings the one
before it named may have missed some, though both succeeded, so the record
keeps that stretch as a gap too ([0015]).

The table `polls` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `record` | `bigint` | The poll's record, by its `id`: its URL, its times, and whether it succeeded. The primary key. |
| `source` | `text` | The Source its job names. |
| `queued_at` | `timestamp (3) with time zone` | Its job's `queued_at`. |
| `expires_at` | `timestamp (3) with time zone` | Its job's `expires_at`. |

The table `feed_polls` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `record` | `bigint` | The poll's record, by its `id`. The primary key. |
| `previous` | `bigint`, or null | Its Source's latest successful poll of the URL in `polls` before it, by record `id`, when the discoverer read it; null if there was none. |
| `reaches_back` | `boolean` | Whether its version and `previous`'s are both read as the feed, without an error, and name a filing in common. |

Take a Source's successful polls of a URL in the order of their records'
`id`s, the order the fetcher added them:

- **Follows:** a successful poll follows the one before it when its job was
  queued less than its own window after the earlier one's job expired. It
  is then the poll of the slot that began when the earlier one's job
  expired, or of one that began sooner, if the cadence was shortened or
  polling began again, so no slot passed between them without a successful
  poll.
- **Reaches back:** a successful poll of the feed reaches back when
  `feed_polls` gives the successful poll before it as its `previous`, with
  `reaches_back` true.
- **Gap:** when a successful poll of the feed doesn't reach back, the time
  from the request of the poll before it to its own request, whether or
  not it follows. Otherwise, when a successful poll doesn't follow the one
  before it, the time from when the earlier one's job expired to the later
  one's request.
- **Coverage:** from a Source's first successful poll on, the time no gap
  covers, until its latest successful poll's job expired: from each
  successful poll's request to the next one's, when the next follows it
  and, of the feed, reaches back.
- **Open gap:** once the latest successful poll's job has been expired for
  as long as its window, with no later successful poll in the record, a gap
  is open, from when that job expired.

So a poll sent late in its slot, after retries, leaves no gap, and a missed
poll leaves one of about a cadence. A pause, a stop after a `403`, or a
fetcher's handover that misses a poll leaves a gap until polling resumes.
A lengthened cadence leaves none. An open gap ends without leaving a gap
when a poll that follows is recorded late, after retries: `gaps` is a view,
so it then shows none. A poll of the feed that doesn't reach back leaves a
gap of at least about a cadence. One whose version can't be read as the
feed reaches back to none, and the next reaches back to it neither, so it
leaves a gap on each side. Whether a poll reaches back is judged against
the poll before it when the discoverer read it, so a poll whose event
reached the discoverer late, after a later poll's, can leave a gap that the
polls didn't ([JOB-6]).

The view `gaps` has these columns:

| Column | Meaning |
| --- | --- |
| `source` | The Source. |
| `url` | The URL its polls fetched. |
| `after_poll` | The record of the successful poll before the gap. |
| `before_poll` | The record of the successful poll that ended it; null while it's open. |
| `began_at` | When it began: when `after_poll`'s job expired, or, where `before_poll` is of the feed and doesn't reach back, `after_poll`'s `requested_at`. |
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
- **GAP-4** (rule) For each event of a successful poll of the feed
  ([FEED-1]), the discoverer adds the poll to `feed_polls`, with its
  Source's latest successful poll of the URL in `polls` before it, by
  record `id`, as `previous`, and as `reaches_back` whether the two polls'
  versions are both read as the feed, without an error, and name a filing
  in common.

## 6. When each Source first showed each filing

Each successful poll of a company's `submissions` lists the company's
recent filings ([SRC-9]), and each of the feed lists the latest filings of
every filer ([SRC-27]). The discoverer reads each such version, compares
the filings it lists with those the Source has shown before, and keeps, for
each filing each Source shows, the two polls that bound when it first
showed it ([0010], [0015]). It checks the feed each day against that day's
master index ([the daily check](#the-daily-check)).

The table `readings` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `sha256` | `text` | The version read. The primary key. |
| `kind` | `text` | What it was read as: `list` ([SEEN-1]); `feed` ([FEED-1]); `listing` ([DAY-1]); `master`, a master index ([DAY-3]); `header`; or `index`, an index page ([FILE-3]). |
| `acceptance_datetime` | `text`, or null | In a header's reading, its `ACCEPTANCE-DATETIME`: 14 digits, as given ([SRC-1]). Null in any other. |
| `instance` | `text`, or null | In an index page's reading, the URL of the XBRL instance it names. Null if it names none, and in any other reading. |
| `error` | `text`, or null | Why the version couldn't be read as its kind; null if it was. |
| `tool` | `text` | The discoverer that read it: `discoverer@` and the 40-character commit its image was built from, as the fetcher's `tool` names the fetcher ([§6 of the store](snapshots.md#6-records)). |

The table `filings` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `accession_number` | `text` | The filing, `##########-##-######`. The primary key. |
| `form` | `text` | Its form, as the version the discoverer first read it from gives it: a list's `form`, the `term` of the `category` of its entries in the feed, or a master index's form type. |
| `filing_date` | `text` | Its filing date, as that version gives it: a list's `filingDate`, the `Filed` its entries' `summary` gives, or a master index's date filed, `YYYYMMDD` ([SRC-35]). |
| `acceptance_date_time` | `text`, or null | Its time, as that version gives it: a list's `acceptanceDateTime` ([SRC-25]), or its entries' `updated` ([SRC-27]); null from a master index, which gives none. A later list can give it another ([SRC-24]). |
| `listed_by` | `bigint` | The record of that version's fetch, by its `id`: a poll's, or a master index's. |

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
- **SEEN-2** (rule) For each filing that a poll's list or version of the
  feed names, if the version is read ([SEEN-1], [FEED-1]), the discoverer
  adds the filing to `filings`, unless it's there, with the values the
  version gives it and the poll's record as `listed_by`. Unless `sightings`
  holds the filing for the poll's Source and URL, it adds a sighting of it,
  with the poll as `first_shown`, and as `last_not_shown` the latest of the
  Source's polls of the URL in `polls` before it, by record `id`, whose
  version has a reading of the same kind without an error, or null if
  there's none.
- **SEEN-3** (invariant) For each sighting, the version of its
  `first_shown` names its filing, and the version of its `last_not_shown`,
  when it has one, doesn't; that poll is an earlier one of the same Source
  and URL. So the Source first showed the filing after `last_not_shown`'s
  request was sent, and by the time `first_shown`'s response arrived.

These are the tightest bounds the record holds, unless a poll's event
reached the discoverer after a later poll's of the same Source, as when the
fetcher publishes an event again after a restart ([JOB-6]); they then
still bound when the Source first showed the filing, more loosely. A
filing a Source shows at its first poll whose list is read has no
`last_not_shown`, since the record doesn't say how long the Source had
shown it. A filing the feed showed only between two of its polls that
neither names, as when the later doesn't reach back, has no sighting of
the feed: a master index lists it ([the daily check](#the-daily-check)).

### The latest-filings feed

Each version of the feed lists the latest filings of every filer and form,
newest first: 100 entries, of which a filing has one for each of its
parties, adjacent, each giving its form, the party's name, CIK, and role,
the filing's directory under the party's CIK, its filing date, and a time
to the second with its offset ([SRC-27], [SRC-33]). The discoverer keeps
each filing's entries raw, from the first version it read that names the
filing, as it keeps each time as its source gives it ([0010], [0015]). A
filing's files are fetched under its issuer's CIK, as its entries give it
([FILE-1]).

The table `entries` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `accession_number` | `text` | The filing. |
| `position` | `integer` | The entry's place among the version's entries, from 1 for its first. |
| `listed_by` | `bigint` | The poll whose version gives it, by its record's `id`. |
| `form` | `text` | Its `category`'s `term`, as given. |
| `party` | `text` | The party's name, as its `title` gives it. |
| `cik` | `text` | The party's CIK, ten digits, as its `title` gives it. |
| `role` | `text` | The party's role, as its `title` gives it, such as `Reporting`, `Issuer`, `Subject`, or `Filed by` ([SRC-27]). |
| `link` | `text` | Its `link`'s `href`, as given. |
| `filed` | `text` | The `Filed` its `summary` gives, as given: `YYYY-MM-DD`. |
| `updated` | `text` | Its `updated`, as given. |

- **FEED-1** (rule) For each event of a successful poll of the feed, at the
  URL the scope gives it,
  `https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=&company=&dateb=&owner=include&start=0&count=100&output=atom`
  ([SRC-26]), the discoverer reads the version from the bucket as the
  feed, and adds its reading, of kind `feed`, unless `readings` holds one
  of that version. A version is read as the feed when it's XML whose root
  is `feed` in the Atom namespace, `http://www.w3.org/2005/Atom`, and each
  of whose `entry` children has ([SRC-33]):
  - a `title` of the form `FORM - NAME (CIK) (ROLE)`: a form, a name, a
    CIK of ten digits, and a role that holds no parenthesis;
  - a `link` whose `href` is
    `https://www.sec.gov/Archives/edgar/data/CIK/NODASHES/ACCESSION-index.htm`,
    with the title's CIK without its leading zeros;
  - a `summary` that gives, after `Filed:`, a date, `YYYY-MM-DD`, and after
    `AccNo:`, the accession number that the `link` gives, and that
    `NODASHES` gives without its dashes;
  - an `updated`; a `category` whose `term` is the title's form; and an
    `id`, `urn:tag:sec.gov,2008:accession-number=` followed by that
    accession number;

  and when each filing's entries are adjacent and give one form, filing
  date, and `updated` ([SRC-27]). Its filings are the accession numbers its
  entries give. A version that isn't read has a reading with `error` set.
- **FEED-2** (rule) For each filing that a version of the feed names, if
  the version is read, the discoverer adds each of the filing's entries in
  it to `entries`, with the poll's record as `listed_by`, unless `entries`
  holds the filing's. So a filing's entries are those of the first version
  of the feed the discoverer read that names it.

A filing the feed names was shown by the feed, so `entries` holds each
filing the feed showed, and only those. Its `updated` is the time the
version gives it, which the stored version doesn't show to be its
acceptance time ([SRC-27]): its header's is ([SRC-1]). The feed's last
entry can be a filing's only one, its issuer's cut off ([SRC-27]); a later
version that names the filing doesn't add the rest.

### The daily check

The feed's polls can miss a filing, and nothing yet shows that the feed
shows every filing. So each day is checked against its master index, which
lists the filings EDGAR disseminated that day, some filed on earlier days,
on a line for each CIK a filing names ([SRC-28], [SRC-35]). A Source polls
the quarter's daily index listing, which names each day's master index
once it's published, at about 1:45 a.m. US Eastern time the next morning
([SRC-23], [SRC-34]). The discoverer reads each day's master index from the
first day checked, and fetches the files of each filing it lists that the
feed didn't show ([0015]).

The **first day checked** is the day after the date, in US Eastern time,
of the request of the feed's first poll, by record `id`, whose version is
read as the feed without an error: `notices.sql`'s function
`first_day_checked` gives it. Earlier days, which the feed didn't cover
whole, are left to Backfills, as [FILE-2] leaves the filings a Source
shows at its first poll ([0015]). A **day checked** is one from the first
day checked whose master index the record holds a version of, read as a
master index without an error.

The table `listing_days` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `sha256` | `text` | The listing's version, which `readings` holds. |
| `day` | `date` | A day it shows: one whose master index, `master.YYYYMMDD.idx`, it names ([SRC-34]). |

The table `index_filings` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `sha256` | `text` | The master index's version, which `readings` holds. |
| `accession_number` | `text` | A filing it lists. |
| `cik` | `text` | The CIK of the first line that lists the filing, as given: without leading zeros ([SRC-35]). |
| `form` | `text` | That line's form type, as given. |
| `date_filed` | `text` | That line's date filed, as given: `YYYYMMDD`. |

The view `checks` has these columns:

| Column | Meaning |
| --- | --- |
| `day` | A day checked. |
| `url` | Its master index's URL. |
| `record` | The first record of that URL that names a version read as a master index without an error. |
| `filings` | The number of filings that version lists. |
| `index_only` | The number of those that `entries` doesn't hold: that no version of the feed the discoverer read named. |
| `feed_only` | The number of filings `entries` holds whose `filed` is the day, as `YYYY-MM-DD`, that no master index of a day checked lists. |

The view `listings` has these columns:

| Column | Meaning |
| --- | --- |
| `url` | A quarter's listing that a Source's polls fetched. |
| `quarter_ends` | The last day of its quarter. |
| `read_through_at` | When the record first held a successful poll of it, requested after the next quarter's first day ended, in US Eastern time, whose version is read as a listing, with each day it shows from the first day checked a day checked: the latest of that poll's `received_at` and those days' records' `received_at`, for the earliest such poll. Null until there's one. Polling the listing can then end ([0015]). |

- **DAY-1** (rule) For each event of a successful poll of a quarter's daily
  index listing, `https://www.sec.gov/Archives/edgar/daily-index/YYYY/QTRn/index.json`,
  with `n` from 1 to 4 ([SRC-34]), the discoverer reads the version from
  the bucket as a listing, and adds its reading, of kind `listing`, and
  each day it shows to `listing_days`, unless `readings` holds a reading of
  that version. A version is read as a listing when it's a JSON object
  whose `directory`'s `item` is a list of objects, each with a `name` that's
  a string, and the name of each that's `master.`, eight digits, and `.idx`
  gives a date of the listing's quarter as `YYYYMMDD`; the days it shows
  are those dates. A version that isn't read has a reading with `error`
  set.
- **DAY-2** (rule) For each event of a successful poll of a listing, if the
  version is read, for each day it shows from the first day checked on,
  the discoverer queues the fetch of that day's master index: the
  listing's URL with `index.json` replaced by `master.YYYYMMDD.idx`
  ([SRC-34]). It doesn't if `jobs` holds a job of that URL whose fetch
  either isn't in the table `records` yet or recorded a version. The job is
  of kind `filing` ([JOB-3]), with the feed `daily-index`, which the store
  records the daily indexes under, and the discoverer adds it to `jobs`, of
  the file `master`. So a day whose fetch ended without a version is
  queued again by the next poll of the listing that shows it, and none is
  queued before the feed has a poll whose version is read.
- **DAY-3** (rule) For each event of a job in `jobs` of a master index,
  whose record names a version, the discoverer reads the version from the
  bucket as a master index, and adds its reading, of kind `master`, unless
  `readings` holds one of that version. A version is read as a master
  index when it's ASCII text whose lines, after the first that's only
  dashes, each give five fields separated by `|` ([SRC-35]): a CIK, digits
  without a leading zero; a company name; a form type; a date filed, eight
  digits; and a file name, `edgar/data/`, the line's CIK, `/`, an accession
  number, and `.txt`. A version that isn't read has a reading with `error`
  set. If it's read, for each filing a line names, the discoverer adds a
  row to `index_filings` with the values of the first line that names it,
  and adds the filing to `filings`, unless it's there, with that line's
  form type and date filed, no time, and the event's record as
  `listed_by`; each unless the table holds it already.
- **DAY-4** (rule) For each filing a master index lists, if the version is
  read, when `entries` doesn't hold the filing and `jobs` holds no job of
  it, the discoverer queues the fetches of the filing's header and its
  index page, as jobs of kind `filing` ([JOB-3]), and adds them to `jobs`.
  So a filing the feed didn't name is fetched within about a day, and when
  no other version listed it first, `filings` records it as first listed
  by the master index: its `listed_by` is the index's record ([0015]).
- **DAY-5** (invariant) The view `checks` gives each day checked, with the
  filings its master index lists and the counts of those only one of the
  feed and the master indexes showed, as its columns say.
- **DAY-6** (invariant) The view `listings` gives each quarter's listing
  that a Source's polls fetched, with its quarter's last day and when it
  was read through, as its columns say.

Filings are matched by accession number, and `checks` counts the filings
that only one of the feed and the master indexes showed ([0015]). A filing
is matched to a day by its filing date as the feed gives it. A master index
lists some filings filed on an earlier day ([SRC-35]), so such a filing
counts in `feed_only` for that earlier day, if it's a day checked, only
until the master index that lists it is read. A filing that only the master
index lists has no time but its header's, and no sighting, so no lag
([§10](#10-times-and-lags)).

## 7. A filing's files

A filing's header gives its acceptance time ([SRC-1]), and its index page
names its XBRL instance ([SRC-20]), which the parse workers read. The
discoverer queues each fetch whether or not requests are allowed: while
they aren't, the fetcher sends none ([ACCESS-1]), and the jobs wait.

The table `jobs` has these columns:

| Column | Type | Meaning |
| --- | --- | --- |
| `id` | `uuid` | The job's `id`, which the record of its fetch keeps as `job` ([STORE-1]). The primary key. |
| `kind` | `text` | Its `kind`: `filing`, as a master index's is too ([DAY-2]), or `backfill`. |
| `backfill` | `uuid`, or null | The `uid` of the Backfill it's for, set exactly in a job of kind `backfill`. |
| `accession_number` | `text`, or null | The filing whose file it fetches; null in the job of a list or of a master index. |
| `file` | `text` | What it fetches: a filing's `header`, `index`, or `instance`; for a Backfill, a company's `submissions` or a `further` file; or a day's `master` index ([DAY-2]). |
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

  `CIK` is, without leading zeros, the CIK of what led to the fetch:
  - a company's `submissions` or one of its further files, the poll's or
    the Backfill's: the company's CIK;
  - the feed: the CIK in the `link` of the filing's first entry in
    `entries` whose role is `Issuer`; failing one, of its first whose role
    is `Subject`; and failing both, of its first entry ([SRC-27],
    [0015]);
  - a master index: the `cik` that `index_filings` gives the filing, its
    first line's ([SRC-35]).

  `ACCESSION` is the accession number, and `NODASHES` the accession number
  without its dashes.
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
| `listed_acceptance` | Required in a filing notice, and only there: a string, or null | Its `acceptance_date_time`, as `filings` holds it ([SRC-25], [SRC-27]): null for a filing first listed by a master index. |
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
([§6 of the store](snapshots.md#6-records)), each filing's time as the
version the discoverer first added it from gives it ([SEEN-2], [LIST-2],
[DAY-3]), and the digits of its header's `ACCEPTANCE-DATETIME`. The view
`lags` reads the acceptance times as instants:

- **`submissions`' `acceptanceDateTime`** as the instant it states, in RFC
  3339 with a `Z` ([SRC-25]);
- **the feed's `updated`** as the instant it states, in RFC 3339 with its
  offset from UTC, such as `-04:00` ([SRC-27], [SRC-33]);
- **the header's digits,** which give no time zone ([SRC-1]), as US
  Eastern time, `America/New_York`.

Read so, the two times disagree for each of the three companies. Each
company's `submissions` gives a filing's time later than its header's
digits read as Eastern time, by the Eastern offset from UTC on its date,
as each of the eight stored headers shows: 4 hours in daylight time and 5
in standard time. Moved earlier by that offset, nearly all of the three
companies' recent filings fall within EDGAR's hours, 6:00 a.m. to 10:00
p.m. Eastern time; read as the instants they state, most of Microsoft's
and a third of Walmart's fall outside them ([SRC-25]). The stored versions
don't show which time is right ([SRC-25]). So `lags` gives a lag from
each, and a filing whose times differ by that offset has lags from
`submissions`' time shorter than those from its header by as much, until a
finding settles which is right ([0010]).

`submissions` can also change the time it gives a filing it already
lists: between October 7 and October 16, 2026, it moved the time of every
filing in Microsoft's and Walmart's `filings.recent`, and in Microsoft's
first further file, later by that offset, and changed no other column of
them ([SRC-24]). `filings` keeps the time from the list the discoverer
first added the filing from, and the lags read that one; a later list's
time is in that list's version in the bucket, which the lags don't read.
So how a filing's two times differ can depend on when that list was read.

A filing the discoverer first added from the feed has the feed's `updated`
as its listed time, which the stored version doesn't show to be when it was
accepted ([SRC-27]); its lags from that time and from its header's show
whether they agree ([0015]). A filing first added from a master index has
none, and no sighting, so `lags` doesn't give it. Since one row of
`filings` keeps the time from whichever version first named the filing, a
sighting of one Source can have the listed time of another's version: a
filing of the three companies' that the feed showed first has the feed's
`updated` in the lags of their `submissions` Sources too.

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
| GAP-2 | invariant | kind | `gaps` gives each Source's gaps, ended and open, and the feed's where a poll doesn't reach back. |
| GAP-3 | rule | tests | A change in a Source's open gap is published within 10 seconds. |
| GAP-4 | rule | tests | Each successful poll of the feed is recorded with whether it reaches back to the one before. |
| SEEN-1 | rule | tests | Each successful poll of a company's `submissions` is read as a list. |
| SEEN-2 | rule | tests | Each filing a list or the feed names is recorded, with a sighting for each Source that shows it. |
| SEEN-3 | invariant | kind | A sighting's two polls bound when its Source first showed the filing. |
| FEED-1 | rule | tests | Each successful poll of the feed is read as the feed. |
| FEED-2 | rule | tests | Each filing's entries are kept, from the first version of the feed that names it. |
| DAY-1 | rule | tests | Each successful poll of a listing is read, with the days it shows. |
| DAY-2 | rule | tests | Each day a listing shows, from the first day checked, has its master index fetched, again after a failure. |
| DAY-3 | rule | tests | Each master index is read, with the filings it lists. |
| DAY-4 | rule | tests | A filing a master index lists that the feed didn't name gets its header and index page fetched. |
| DAY-5 | invariant | kind | `checks` gives each day checked, with the filings only one of the feed and the indexes showed. |
| DAY-6 | invariant | kind | `listings` gives each quarter's listing, and when it was read through. |
| FILE-1 | rule | tests | A filing's files are fetched at the URLs SRC-21 gives, under the CIK of what led to them, each at one URL. |
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
   `submissions` or the feed lists it** (Q4). The record shows each fetch
   that ends without a version. If new filings' fetches do, a later version queues
   them again after a delay; until then, a Backfill fetches them again
   ([FILE-4]).
2. **Which acceptance time is right, and the header's time zone**
   ([SRC-1], [SRC-24], [SRC-25]). The lags read `submissions`' time as the
   instant it states and the header's digits as US Eastern time, and the
   two differ by the Eastern offset. The lags can settle it: a filing is
   public only once it's accepted ([0010]), so an acceptance time from
   which a filing's lag has a high bound below zero is later than when
   its Source showed it, and isn't when it was accepted. A finding that
   settles it changes how `lags` reads that time, in `notices.sql`, in a
   new version of this document, and nothing stored: the function
   `listed_instant` or `header_instant`, where one reading holds for every
   time the record keeps. Since `submissions` can change how it gives the
   times of filings it already lists ([SRC-24]), and a function of the
   time alone can't tell one form from another, the finding may instead
   show when it did, and `lags` read each filing's time by when its list
   was fetched: the `received_at` of its `listed_by` record, which changes
   what `lags` joins too. A database that already holds the view and its
   functions takes the change through a migration
   ([`deployment.md`, §9](deployment.md#9-later-versions)).
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
5. **Whether the feed's `updated` is a filing's acceptance time**
   ([SRC-27]). `lags` reads it as the instant it states, beside the
   header's digits read as Eastern time; their lags show whether the two
   agree. A finding that settles it changes nothing stored.
6. **Whether the feed and the master indexes list the same filings, and on
   which day** ([SRC-35]). `checks` counts each filing that only one of
   them showed, matching a filing the feed named to the day of the filing
   date it gives. Whether the feed shows the filings that a master index
   lists with an earlier date filed, and which filing date it gives them,
   is for the record to show. A finding that they differ can change how
   `checks` matches a filing to a day.
7. **Whether the feed's `-index.htm` is the index page** ([SRC-27]).
   [FILE-1] fetches `ACCESSION-index.html`, the index page [SRC-21] found,
   under the CIK the feed's entries give; the record of each fetch shows
   whether the filing's directory holds it under that CIK.
8. **How far back the feed's 100 entries reach in the busiest hour**
   ([SRC-28]). The gaps that polls which don't reach back leave show it,
   and so whether its cadence, or the `count` its URL asks for, needs to
   change, which [0015] leaves to the record.

### Left to other specifications

This version leaves to others what the notices rely on:

- **Controls:** the two Sources [0015] adds, the feed's and the quarter's
  listing's, and how the listing's Source follows its quarter: a quarter's
  listing is polled beside the next quarter's until `listings` gives its
  `read_through_at` ([0015], detail 13), and the scheduler, which reads the
  record only through notices ([NOTICE-1]), needs to learn when that is.

- **Deployment:** the streams `ORDERS` and `NOTICES`, with their replicas;
  the tables and views of `notices.sql`, made after the store's table, and
  their grants ([TABLE-3]); the discoverer's database role, and its Garage
  key, which may only read the bucket ([GARAGE-4]); and the durable
  consumers, each made before what it reads is published: the
  discoverer's of every subject of `RECORDS`, its poll events' with one
  unacknowledged at a time ([DISC-1]), and of `ORDERS`; the scheduler's of
  gap and backfill notices; and the parse workers' of filing notices
  ([`deployment.md`, §9](deployment.md#9-later-versions)). Also the
  chart's default Sources, which [0015] makes the feed's, the listing's,
  and the three companies' through the lag study; and, for a database that
  holds version 0.1.1's tables, as the chart makes them where there are
  none, their migration to this version's
  ([`deployment.md`, §9](deployment.md#9-later-versions)).
- **Facts:** what the parse workers do with a filing notice, and how they
  find the complete filings whose notices they didn't read.
- **Telemetry:** the discoverer's metrics, such as readings with an error
  and fetches of filings' files that ended without a version; the views of
  coverage and lags that Grafana shows; the alert on a gap longer than its
  Source's cadence; each feed's lag, and the counts in `checks` ([0015]);
  and whether a gap that a poll of the feed which doesn't reach back leaves
  is alerted on, since it can be as short as a cadence. Its version 0.1.0
  doesn't follow this one yet: the readings it counts are of the kinds
  `list`, `header`, and `index` only; the failed fetches it counts are of
  filings' files and Backfills' lists, not master indexes; and its view of
  coverage says a stretch ends where its last poll's job expired, where a
  gap that a poll of the feed which doesn't reach back leaves begins at a
  request instead, as `gaps` gives it and its SQL reads it.
- **`edgar-asof`:** readers of the feed and of a day's master index, in a
  release the discoverer pins ([0009], [0015]).
- **The scope:** `data/scope.json` covers the feed, this quarter's listing
  and master indexes, and filings' files only in the three companies'
  directories ([SRC-27]), so the transfer copies only those of the
  discoverer's fetches. Widening it, for the next quarter or for other
  filers, is the owner's to ask for ([§9 of the store](snapshots.md#9-scope)).

## 13. Changes to this document

Versions follow semantic versioning. Adding a statement, an optional field,
a column, a view, or a check is a minor version. Changing the meaning of a
statement, a field, or a column, or what a view gives, is a major version,
and says how the rows already written are read. Changing only what it
explains, or the findings it cites, is a patch version. The schemas in
[`schemas/`](schemas/) that this document names are part of it, so changing
one changes it. IDs are never reused, and a retired statement stays in the
index.

### From version 0.1.1

Version 1.0.0 takes in [0015], which makes the latest-filings feed the
notice source for every filer, and checks each day against its master
index, from the findings on the feed and the daily indexes ([SRC-26] to
[SRC-28], [SRC-33] to [SRC-35]). It's a major version: it changes what
`gaps` gives, the meaning of [SEEN-2] and of [FILE-1]'s CIK, and lets
`filings`' `acceptance_date_time` and a filing notice's `listed_acceptance`
be null. No discoverer has written rows yet, since task T24 builds it, so
no row is read differently. A database that holds version 0.1.1's tables
takes this version's through a migration, which the deployment's version
specifies ([§12](#left-to-other-specifications)).

- **Terms:** the feed and its entries, a listing, a master index, a day,
  the first day checked, a day checked, and reaching back.
- **Polls, coverage, and gaps:** a poll of the feed that doesn't reach back
  to the one before leaves a gap from that one's request ([GAP-2]), which
  the table `feed_polls` records ([GAP-4]). Coverage is the time no gap
  covers.
- **The latest-filings feed:** its versions' readings ([FEED-1]), its
  filings and sightings ([SEEN-2]), and its entries, kept raw in `entries`
  ([FEED-2]).
- **The daily check:** the days a listing shows ([DAY-1]); each day's
  master index from the first day checked, fetched again after a failure
  ([DAY-2]), and read ([DAY-3]); the fetches of the filings the feed
  didn't name ([DAY-4]); and the views `checks` and `listings` ([DAY-5],
  [DAY-6]), with the function `first_day_checked`.
- **A filing's files:** the CIK each is fetched under ([FILE-1]), and a
  master index's job, of the file `master` and kind `filing`.
- **Messages:** a filing notice's `listed_acceptance` is null for a filing
  first listed by a master index.
- **Times and lags:** the feed's `updated` is read as the instant it
  states, and `listed_instant`'s comment says so; the function doesn't
  change.
- **Open questions** 5 to 8, and what's left to the controls', the
  deployment's, and the telemetry's specifications, `edgar-asof`, and the
  scope.
- **Guarantees** 1, 2, and 4 say so, and guarantee 8 is new.

### From version 0.1.0

Version 0.1.1 takes in what the first refetch showed of `submissions`'
acceptance times ([SRC-24], [SRC-25]), whose findings superseded those
version 0.1.0 cited for them. It changes no statement, field, column, or
view, so it's a patch version: `lags` reads both acceptance times as
version 0.1.0 does.

- **Times and lags:** [§10](#10-times-and-lags) says why the header's
  digits are still read as Eastern time, now that each company's
  `submissions` gives a time later than them by the Eastern offset, not
  Apple's alone; that the lags from the two times then differ by that
  offset; and that the lags read the time from the list a filing was
  first added from, since `submissions` can change it.
- **Guarantee 2,** `filings`' `acceptance_date_time`, and a filing
  notice's `listed_acceptance` say so too. Guarantee 2 also lists
  [LIST-2], which adds a Backfill's filings to `filings` as [SEEN-2] adds
  a poll's.
- **Open question 2** asks which acceptance time is right, not only the
  header's zone. It says how the lags can settle it, and that a finding
  that settles it changes a function, or, since `submissions` can change
  how it gives a filing's time, has `lags` read the time by when its list
  was fetched.
- **`notices.sql`**'s comments cite the new findings. Its tables, views,
  and functions don't change.

[0003]: ../docs/decisions/0003-queue-work-in-nats-jetstream.md
[0004]: ../docs/decisions/0004-keep-the-record-in-postgresql-18.md
[0006]: ../docs/decisions/0006-write-the-services-in-go-and-python.md
[0007]: ../docs/decisions/0007-control-the-pipeline-through-custom-resources.md
[0009]: ../docs/decisions/0009-read-edgar-through-a-public-point-in-time-library.md
[0010]: ../docs/decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md
[0015]: ../docs/decisions/0015-poll-the-latest-filings-feed-and-reconcile-each-day-with-the-daily-index.md
[SRC-1]: ../investigation/edgar.md#findings-so-far
[SRC-5]: ../investigation/edgar.md#findings-so-far
[SRC-9]: ../investigation/edgar.md#findings-so-far
[SRC-12]: ../investigation/edgar.md#findings-so-far
[SRC-20]: ../investigation/edgar.md#findings-so-far
[SRC-21]: ../investigation/edgar.md#findings-so-far
[SRC-23]: ../investigation/edgar.md#findings-so-far
[SRC-24]: ../investigation/edgar.md#findings-so-far
[SRC-25]: ../investigation/edgar.md#findings-so-far
[SRC-26]: ../investigation/edgar.md#findings-so-far
[SRC-27]: ../investigation/edgar.md#findings-so-far
[SRC-28]: ../investigation/edgar.md#findings-so-far
[SRC-33]: ../investigation/edgar.md#findings-so-far
[SRC-34]: ../investigation/edgar.md#findings-so-far
[SRC-35]: ../investigation/edgar.md#findings-so-far
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
[GAP-4]: #5-polls-coverage-and-gaps
[SEEN-1]: #6-when-each-source-first-showed-each-filing
[SEEN-2]: #6-when-each-source-first-showed-each-filing
[SEEN-3]: #6-when-each-source-first-showed-each-filing
[FEED-1]: #the-latest-filings-feed
[FEED-2]: #the-latest-filings-feed
[DAY-1]: #the-daily-check
[DAY-2]: #the-daily-check
[DAY-3]: #the-daily-check
[DAY-4]: #the-daily-check
[DAY-5]: #the-daily-check
[DAY-6]: #the-daily-check
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
