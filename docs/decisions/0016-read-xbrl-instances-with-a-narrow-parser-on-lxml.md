# 16. Read XBRL instances with a narrow parser on lxml, in `edgar-asof`

**Status:** Proposed, November 5, 2026. Task T12 proposed it.

## Context

[0006](0006-write-the-services-in-go-and-python.md) writes the parse
workers in Python and leaves their XBRL parser to choose: a narrow parser
on lxml, or Arelle, an open-source XBRL processor. The parse workers read
each stored XBRL instance and write its facts with the filing's acceptance
time, under an import manifest ([components](../design.md#components)).
They build facts through a pinned `edgar-asof`
([0009](0009-read-edgar-through-a-public-point-in-time-library.md),
[T25](../plan.md#facts)), whose later task T11 reads XBRL instances with
the parser this record chooses. So the parser is the library's, and it
serves people who use the library on its own as well as the pipeline.

**What the facts need,** from Q2's and Q3's findings. A value's own period
is its context's dates, and a duration that starts and ends on the same
day is given no `start` in `companyfacts`
([SRC-6, SRC-11](../../investigation/edgar.md#findings-so-far)). The
filing's fiscal year and period, and its period's end, are its `dei` facts
`DocumentFiscalYearFocus`, `DocumentFiscalPeriodFocus`, and
`DocumentPeriodEndDate` (SRC-6). Each value `companyfacts` keeps of the
six 10-Ks and 10-Qs whose instances are stored is one of its instance's
facts: those of `us-gaap` and `dei` concepts that have a unit, have no
dimensions, and aren't nil. The instance holds more: facts of the company's own concepts, facts with
dimensions, nil facts, and text (SRC-14). It also gives each number's
`decimals`, which `companyfacts` doesn't (SRC-15). Some facts are given
twice: 23 keys to `decimals` -6 and -8, of which `companyfacts` keeps the
value to -6 (SRC-14), and three of Microsoft's as the same number written
two ways, such as `18.00` and `18`
([SRC-32](../../investigation/edgar.md#findings-so-far)). `edgar-asof`'s
fact model keeps every value, merging none, and each value exactly as
written ([its FACT-6 and FACT-8][asof-facts]), and leaves facts read from
instances, whose keys may need dimensions, to a later version
([left to other specifications][asof-facts-left]).

**What the pipeline holds.** Of each filing, the discoverer fetches its
header, its index page, and the instance that the index page marks
([FILE-1 to FILE-3](../../spec/notices.md#7-a-filings-files),
[SRC-20, SRC-21](../../investigation/edgar.md#findings-so-far)). Each
request to the SEC goes through the one active fetcher
([0005](0005-send-sec-requests-through-one-active-fetcher.md)), and an
import reads its inputs only from the store, never from the live source
([IMP-3](../../spec/snapshots.md#11-imports)). Each stored instance holds
every context and unit its facts name, and names as its schema a file of
its own filing, `EX-101.SCH`, listed in its header with up to four
linkbases. The store holds none of these files (SRC-32).

**What an instance alone shows.** Under XBRL 2.1, every numeric item
names a unit and no other item does (section 4.6.2), and a numeric item
gives its `decimals` or `precision` unless it's nil or a fraction
(section 4.6.3). So an instance alone shows each fact's name, its period
and dimensions, its unit, whether it's a number, its value, and its
precision. What it doesn't show is in the taxonomy that its `schemaRef`
starts: the filing's schema, its linkbases, and everything they import
(section 3.2). That's each concept's data type, period type, and balance,
its labels, the relationships between concepts, and whether the instance
is valid.

**Its size, and the cluster's.** A 10-K's or a 10-Q's instance is 0.9 MB
to 10.9 MB (SRC-18). Until the parser is chosen, the
[estimates](../explanation/estimates.md#cpu-memory-and-storage) assume 0.5
to 2 seconds an instance, and 150 to 250 MB a worker with lxml, or 0.5 to
2 GB with Arelle. kind runs each node as a container on one machine, so
memory is scarce ([0001](0001-deploy-on-kubernetes-with-helm-on-kind.md)),
and up to three workers run at once
([0014](0014-scale-the-parse-workers-with-keda-by-their-consumers-backlog.md)).

**How the options were measured.** On the eight stored instances, with
Python 3.13 on an Apple M3 Pro, outside the cluster, each library in an
environment of its own. The figures below are of reading an instance
alone: a worker also imports its other libraries, reads from Garage, and
writes to PostgreSQL. Nothing sent a request to the SEC, so no taxonomy
was loaded.

## Options

- **A narrow parser on lxml.** A reader of the parts of XBRL 2.1 that the
  stored instances use (SRC-32): contexts with explicit and typed
  dimensions, units of one measure or of one divided by another, and
  facts that are children of the root. It reads the instance alone, so it
  needs nothing the store doesn't hold. Read this way, the six 10-Ks' and
  10-Qs' instances give each of the 2,641 values `companyfacts` gives
  them, with the same key and the same value, but Microsoft's two
  `EntityListingParValuePerShare` (SRC-14). lxml 6.1.3 read each in 5 to
  30 milliseconds, and the process, with its Python, peaked at 30 to 75 MB.
  lxml is under BSD-3-Clause, with no dependencies of its own, and its
  metadata names Python 3.10 to 3.15, where CI runs 3.12 to 3.14. Arelle
  and edgartools are built on it. It's code to write, test, and keep: it
  checks nothing it doesn't read, so it doesn't validate an instance, and
  gives no labels or types. Another company's instance, or an older one,
  may use parts of XBRL that no stored instance shows.
- **The same narrow parser on the standard library's ElementTree.** It
  keeps `edgar-asof` free of dependencies, as it is now. It took about
  twice as long as lxml, 12 and 56 milliseconds for Apple's and
  Microsoft's 10-Ks where lxml took 7 and 28, with about the same memory.
  It gives no element the prefix its document declares for a
  namespace, so a reader keeps the declarations from the parser's events
  itself, and it sets no limit on a text's length.
- **Arelle.** A full XBRL processor: it discovers an instance's taxonomy,
  validates XBRL 2.1 and XBRL Dimensions, reads inline XBRL, and gives each
  concept's labels and type. `arelle-release` 2.47.4 is under Apache-2.0,
  for Python 3.11 on, 3.14 included. It has 15 dependencies, among them
  numpy, Pillow, openpyxl, and bottle, and with them it takes 92 MB
  installed, where lxml takes 20 MB. Its package holds no validation of
  the EDGAR Filer Manual's rules: that comes from its EDGAR plugin, which
  is a repository of its own, `Arelle/EDGAR`, as are its viewers of SEC
  filings. Offline, with only the stored instance, it read
  Microsoft's 10-K's 446 contexts and 6 units but none of its 1,869 facts:
  it logged `xbrl:schemaImportMissing` and "Instance facts missing schema
  concept definition", and peaked at 220 MB. So it needs each filing's
  schema and linkbases, one to five more files of each filing with an
  instance (SRC-32), fetched through the fetcher and stored, and every
  taxonomy they import, for each year back to a company's first XBRL
  filing, given to it offline as taxonomy packages or in its cache, since
  the services that read EDGAR's content never fetch
  ([FETCH-18](../../spec/snapshots.md#the-fetcher)). How much memory it
  takes with the whole taxonomy isn't measured, since that needs those
  files from the SEC.
- **Another XBRL library.** py-xbrl 3.0.3 is under the GPLv3, where
  `edgar-asof` is under MIT (0009), and it loads the taxonomy that an
  instance's `schemaRef` names before it reads a fact, as Arelle does.
  edgartools 5.62.1, under MIT, reads an instance alone, from a file. It
  has 22 dependencies, among them pandas and pyarrow 23.0.1 or later,
  whose version the exporter pins
  ([0006](0006-write-the-services-in-go-and-python.md)), so the library's
  floor would bind the export's writer. Its import and read of Microsoft's
  10-K peaked at 297 MB, and its table of that instance's facts held
  1,758 of its 1,869, by rules of its own.

## Decision

A narrow parser on lxml, in `edgar-asof`:

1. **Where it lives.** In `edgar-asof`, specified and built by its task
   T11, and released for [T25](../plan.md#facts), which the parse workers
   call through a pinned release
   ([0009](0009-read-edgar-through-a-public-point-in-time-library.md)).
   No code of this repository parses XBRL.
2. **What it reads.** The instance alone, as stored: the one the index
   page marks (SRC-20). Never its schema, its linkbases, or the
   taxonomies, and it fetches nothing.
3. **What it gives,** for each fact, as the instance gives it: its name,
   as its namespace and local name, with the prefix the instance declares;
   its context's entity, and its period, as a start and an end or as an
   instant, a duration that starts and ends on the same day included; its
   dimensions, each explicit member's dimension and member, and each
   typed member's dimension and value; its unit, as its measures, or the
   measures it divides; whether it's a number, by whether it names a unit
   (XBRL 2.1, section 4.6.2); its value, exactly as written; whether it's
   nil; its `decimals` or `precision`; and its `id`. The filing's fiscal
   year and period, and its period's end, are `dei` facts (SRC-6), so they
   come with the rest.
4. **What it merges or drops: nothing.** A fact given twice comes out
   twice, in the instance's order (SRC-14, SRC-32). Which of them a fact
   or an answer takes is for `edgar-asof`'s specification to say, and the
   pipeline's facts specification ([T17](../plan.md#facts)) after it.
5. **What it refuses.** An instance whose root isn't `xbrli:xbrl`; one
   with a document type declaration; a fact with a child element, which a
   tuple or a fraction has and no stored fact does; a fact whose context or
   unit the instance doesn't hold; and a number with neither `decimals`
   nor `precision` that isn't nil. It refuses the whole instance, with the
   reason, rather than give part of its facts. What a worker does with an
   instance refused is the facts specification's to say.
6. **What it leaves out.** The instance's footnotes, the files its
   `schemaRef` names, and so each concept's labels, type, and
   relationships, and any check of validity.
7. **How lxml parses.** With no access to the network, no entities
   expanded, and libxml2's limits on size kept, so a text over its limit
   of 10,000,000 bytes refuses the instance. The longest stored text is
   1,164,644 characters (SRC-32).

## Consequences

- **The facts come from the store alone.** Rebuilding the facts from
  stored bodies, a drill of [T31](../plan.md#traces-logs-and-drills),
  needs no taxonomy, and the fetcher fetches nothing more for the parse
  workers. Someone who uses `edgar-asof` on its own needs only the
  instance.
- **The parse is a small part of a worker.** At 5 to 30 milliseconds and
  under 75 MB an instance, measured outside the cluster, it's well inside
  the estimates' assumption for a worker, which stays until kind measures
  the workers ([0014](0014-scale-the-parse-workers-with-keda-by-their-consumers-backlog.md)).
  Once this record is Accepted, the estimates' figures for Arelle, up to
  6 GB for three workers, no longer apply.
- **`edgar-asof` gains lxml,** its first dependency, under BSD-3-Clause.
- **Nothing checks an instance's validity.** The parser checks that it
  can read the instance, not that the instance is valid XBRL, that its
  calculations add up, or that its concepts are in their taxonomies. A
  later need for labels or validation, such as a dashboard that names
  concepts, takes a record of its own, with the taxonomy's files fetched
  and stored.
- **A fact's type comes from the instance alone.** Whether a fact is a
  number comes from its unit, and its period from its context. A text
  fact can hold escaped HTML, up to 1,164,644 characters in those stored
  (SRC-32), and the parser gives it as written.
- **Older instances aren't stored yet.** The stored instances are of
  eight filings from October 31, 2025 to September 1, 2026 (SRC-31), and
  a Backfill brings filings from each company's first in XBRL, in 2009
  (SRC-10, SRC-20). An instance that uses parts of XBRL the parser doesn't
  read is refused, so it shows as a refusal, not as wrong facts. Storing
  older instances to check them is a widening of the scope and a run,
  which the owner asks for.
- **A taxonomy's namespace changes each year.** One instance names
  `http://fasb.org/us-gaap/2026` where the others name `…/2025` (SRC-32),
  while `companyfacts` names the taxonomy `us-gaap` (SRC-30). How a fact's
  concept is written across years, as `edgar-asof` writes a concept as
  `us-gaap:Revenues` ([its FACT-7][asof-facts]), is the library's
  specification's to say.
- **Facts given twice reach the fact model.** Two values of one key to
  different `decimals`, and one number written two ways, both come out.
  `companyfacts` keeps the value to -6 of the 23 keys (SRC-14), and the
  library's specification says whether a fact read from an instance does
  the same.
- **It reads EDGAR's extracted instance, not the filing's inline XBRL.**
  A filing whose index page marks no instance gives the parser nothing,
  as a notice without an instance names none
  ([§9 of the notices](../../spec/notices.md#9-messages-and-processing)).

## Sources

- [XBRL 2.1](https://www.xbrl.org/Specification/XBRL-2.1/REC-2003-12-31/XBRL-2.1-REC-2003-12-31+corrected-errata-2013-02-20.html),
  sections 3.2, for the rules that discover an instance's taxonomy; 4.6.2,
  for the unit that every numeric item names and no other item does; and
  4.6.3, for `decimals` and `precision`.
- [lxml 6.1.3](https://pypi.org/project/lxml/6.1.3/), its metadata, and
  [its parser's options](https://lxml.de/parsing.html#parser-options).
- [Arelle](https://github.com/Arelle/Arelle),
  [`arelle-release` 2.47.4](https://pypi.org/project/arelle-release/2.47.4/),
  its metadata and the plugins it holds, and its
  [EDGAR plugins](https://github.com/Arelle/EDGAR).
- [py-xbrl 3.0.3](https://pypi.org/project/py-xbrl/3.0.3/), its metadata,
  and its `xbrl/instance.py`, which parses the taxonomy before the facts.
- [edgartools 5.62.1](https://pypi.org/project/edgartools/5.62.1/), its
  metadata, and its `XBRL.from_files`.
- `edgar-asof`'s [`spec/facts.md`][asof-facts], version 1.0.0, at commit
  `61e00f1`.
- The findings SRC-6, SRC-10, SRC-11, SRC-14, SRC-15, SRC-18, SRC-20,
  SRC-21, SRC-30, SRC-31, and SRC-32 in
  [`investigation/edgar.md`](../../investigation/edgar.md#findings-so-far).

[asof-facts]: https://github.com/nslaughter/edgar-asof/blob/61e00f1/spec/facts.md#5-facts
[asof-facts-left]: https://github.com/nslaughter/edgar-asof/blob/61e00f1/spec/facts.md#left-to-other-specifications
