# Source investigation: EDGAR

**Status:** Started October 7, 2026. The baseline was fetched October 7;
seventeen findings so far. SRC-6 to SRC-11, on fiscal periods and joins,
and SRC-14 to SRC-17, on restated and repeated values, were read from it
on October 10. SRC-12 and SRC-13, on `submissions`' further files and its
acceptance times, were read from them and the baseline once they were
fetched on October 10. On October 11, SRC-16 was read from Microsoft's
further file as well, and SRC-15 and SRC-17 were checked against the
further files. Since October 10, how soon each feed shows a new filing
(Q4) is measured from the pipeline's record. Data here comes from the
U.S. Securities and Exchange Commission's EDGAR system.

This document records how EDGAR's feeds and filings behave, each finding
shown by versions in the snapshot store
([`spec/snapshots.md`](../spec/snapshots.md)). The pipeline's specification
depends on EDGAR only through these findings and cites them by ID. They also
answer the snapshot store's open questions 1, 2, and 4.

## Scope

Three companies in the Dow Jones Industrial Average, chosen for their
different fiscal years, and EDGAR's daily indexes for this quarter.
[`data/scope.json`](../data/scope.json) lists the URLs, each with its reason.

| Company | CIK | Fiscal year ends |
| --- | --- | --- |
| Apple | 320193 | The last Saturday of September; 52 or 53 weeks |
| Microsoft | 789019 | June 30 |
| Walmart | 104169 | January 31; named for the calendar year it ends in |

## Feeds

Fetches use one feed name per kind of URL. The last column says what each is
expected to hold; findings say what it does hold.

| Feed | URLs | Expected to hold |
| --- | --- | --- |
| `submissions` | `https://data.sec.gov/submissions/CIK##########.json` | A company's filings: accession number, form, filing and report dates, acceptance time. |
| `companyfacts` | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` | A company's XBRL values, each with its period, fiscal year and period, form, filing date, and accession number. |
| `filings` | Under `https://www.sec.gov/Archives/edgar/data/CIK/` | Directory listings, and the index, header, and XBRL documents of selected filings. |
| `daily-index` | Under `https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/` | Each business day's list of the filings accepted. |

## Method

1. **Baseline.** Snapshot each company's `submissions` and `companyfacts`,
   the quarter's daily index listing and each day's master index so far,
   and, for each company, the latest 10-K, the latest 10-Q, and any recent
   amendment: its filing index, header, and XBRL instance. Only selected
   filings are fetched, not each company's whole directory.
2. **Daily refetch.** Each business evening, after the day's index is
   published (SRC-5), refetch `submissions`, `companyfacts`, and the daily
   index listing. Fetch each master index the listing shows that isn't
   stored yet, and refetch the stored ones to see whether a published index
   changes. An unchanged body adds a record without a copy, and a URL that
   sends validators (SRC-4) is asked conditionally, so an unchanged master
   index costs a `304`. Continue through each company's next periodic
   report: Microsoft's and Apple's are expected in late October or early
   November, and Walmart's after its quarter ends October 31.
3. **Arrival, from the pipeline's record.** How long after acceptance each
   feed shows a new filing is measured from the pipeline's record once its
   fetcher and discoverer run, not by polling by hand
   ([0010](../docs/decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)).
   The record keeps every time raw, and each lag is a query over it. A
   finding about arrival cites the records it rests on once they reach the
   store, as the store's version 1.0.0 will say.
4. **New filings.** When a company files a 10-K, a 10-Q, or an amendment,
   fetch its filing index, header, and XBRL instance.
5. **Findings.** Write each finding as the versions show it, and commit it
   with the store it cites, after `uv run scripts/verify.py`.

SEC documentation can suggest what to look for, but a finding rests on
stored versions.

## Questions

Each question says what the pipeline's specification needs from it.

- **Q1. What each feed returns and how it changes.** Content type, encoding,
  and size; whether `ETag` and `Last-Modified` are sent and a conditional
  request gets a `304`; how often the content changes; and whether a URL
  returns to an earlier body. Also whether a filing's archived files ever
  change after acceptance, which the pipeline's record assumes they don't
  ([`docs/design.md`](../docs/design.md)). Decides which feeds the pipeline
  reads and how often.
- **Q2. Where each time comes from.** Which source gives a filing's
  acceptance time, at what precision and in which time zone, and whether
  `submissions` and the filing's header agree. Which source gives each
  value's fiscal period; whether `companyfacts`' `fy` and `fp` describe the
  value's period or the filing's; how each company's fiscal year appears;
  and how a value joins to its filing's acceptance time through the
  accession number, since `companyfacts` gives only a filing date. Decides
  the time model.
- **Q3. How restatements and amendments appear.** When a later filing
  reports a different value for the same concept and period, whether
  `companyfacts` keeps both, each with its filing; how an amendment such as
  a 10-K/A appears; and how a restated value differs from an earlier
  period's value reported again unchanged. Decides what a query as of a past
  date returns.
- **Q4. How new filings are announced.** How long after acceptance a filing
  appears in `submissions`, `companyfacts`, and the daily index; when a
  day's index is published; and whether a published index changes. EDGAR's
  latest-filings feed is a further candidate, once its URL is confirmed and
  the scope widened to cover it. The pipeline's record measures how soon
  each feed shows a filing (method, step 3), and the daily refetches show
  when indexes are published and whether they change. Decides whether the
  pipeline keeps the notice source it starts with
  ([0010](../docs/decisions/0010-poll-each-companys-submissions-and-measure-lag-from-the-record.md)),
  and how often it polls.
- **Q5. Sizes and pace.** Bytes per URL and per filing, growth per refetch,
  and the requests and time a backfill of one company's filings takes at 5
  requests a second. Sets the store's budget and says whether the pace
  suits backfills.

The investigation is done when every question has findings the pipeline's
specification can cite.

## Findings

A finding states one behavior, names the question it answers, and cites the
records that show it: the feed, the URL, the first 12 hex digits of the
`sha256` (as `snapshot.py` prints them), and `requested_at`, either in the
finding or through the [records cited](#records-cited) below.
`uv run scripts/verify.py -v` lists each URL's versions. Findings have IDs,
`SRC-1` onward, that are never reused. A finding that later versions
contradict is marked superseded and stays; a new finding cites the new
versions.

```markdown
- **SRC-1** (Q2) The behavior, in one or two sentences.
  Shown by `submissions` https://data.sec.gov/submissions/CIK0000320193.json
  `0123456789ab` at 2026-10-07T22:00:00.000Z.
```

### Records cited

The baseline was fetched on October 7, 2026 by `scripts/snapshot.py` at
commit `70ab28b`. Each entry is a record's `sha256` prefix and its
`requested_at` on that day, in UTC.

Each company's `submissions` and `companyfacts`, at
`https://data.sec.gov/submissions/CIK##########.json` and
`https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`, with the
CIK padded to ten digits:

| Company | CIK | `submissions` | `companyfacts` |
| --- | --- | --- | --- |
| Apple | 320193 | `e89635068cd4` 17:40:13.617Z | `73a86c6aedc3` 17:40:46.711Z |
| Microsoft | 789019 | `2484a2a3b567` 17:40:13.829Z | `f8aae2965b20` 17:40:47.082Z |
| Walmart | 104169 | `5cafa5114f8e` 17:40:13.971Z | `54ec3fbf36dc` 17:40:47.337Z |

The eight filings in `filings`. Each one's directory is
`https://www.sec.gov/Archives/edgar/data/CIK/ACCESSION/`, with the
accession number's dashes removed; the header is
`ACCESSION-index-headers.html` in that directory, and the listing is
`index.json`:

| Company | Form | Accession number | Header | Directory listing |
| --- | --- | --- | --- | --- |
| Apple | 10-K | 0000320193-25-000079 | `4b05064b7b26` 17:41:43.408Z | `d61dde83df2d` 17:41:08.170Z |
| Apple | 10-Q | 0000320193-26-000020 | `a51cfcf32d03` 17:41:44.124Z | `3e5dde4c0403` 17:41:08.350Z |
| Apple | 8-K/A | 0001140361-26-035325 | `59fe5a75478c` 17:41:45.120Z | `16e98f070459` 17:41:08.474Z |
| Microsoft | 10-K | 0001193125-26-323660 | `a4677192c07a` 17:41:45.643Z | `bf17f342b5ff` 17:41:08.680Z |
| Microsoft | 10-Q | 0001193125-26-191507 | `7b8036685442` 17:41:46.314Z | `10a9ce7f8519` 17:41:09.149Z |
| Walmart | 10-K | 0000104169-26-000055 | `d310a19046ca` 17:41:47.079Z | `a576f2522e5b` 17:41:09.269Z |
| Walmart | 10-Q | 0000104169-26-000154 | `0bc822d86b45` 17:41:47.626Z | `dd6bc42dc512` 17:41:09.456Z |
| Walmart | 8-K/A | 0000104169-26-000028 | `91aa33498ef4` 17:41:48.242Z | `7408a912ac83` 17:41:09.665Z |

The XBRL instances of the six 10-Ks and 10-Qs and the two 8-K/As, in the
same directories:

| Company | Form | Instance | Record |
| --- | --- | --- | --- |
| Apple | 10-K | `aapl-20250927_htm.xml` | `e1076735f1c8` 17:41:43.991Z |
| Apple | 10-Q | `aapl-20260627_htm.xml` | `28f986bb243c` 17:41:44.837Z |
| Apple | 8-K/A | `ef20081427_8ka_htm.xml` | `598c297043dd` 17:41:45.530Z |
| Microsoft | 10-K | `msft-20260630_htm.xml` | `56e56d82ee2d` 17:41:46.095Z |
| Microsoft | 10-Q | `msft-20260331_htm.xml` | `d9c4e6b92fcc` 17:41:46.815Z |
| Walmart | 10-K | `wmt-20260131_htm.xml` | `07c72faa90cf` 17:41:47.464Z |
| Walmart | 10-Q | `wmt-20260731_htm.xml` | `2e39345b995e` 17:41:48.099Z |
| Walmart | 8-K/A | `wmt-20260115_htm.xml` | `d6d74874acd8` 17:41:48.887Z |

The quarter's daily index listing, in `daily-index` at
`https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/index.json`:
`b2627c1a365d` 17:40:47.727Z.

The further `submissions` files that each company's `submissions` names in
`filings.files`, fetched on October 10, 2026 by `scripts/snapshot.py` at
commit `02b4946`, in `submissions` at
`https://data.sec.gov/submissions/FILE`. That commit is pull request #14's,
which was squash-merged into `main` as `889d5fb`, so `main`'s history
doesn't hold it; `scripts/snapshot.py` is the same at both. Microsoft's
second file, `CIK0000789019-submissions-002.json`, is outside the scope:

| Company | File | Filing dates | Record |
| --- | --- | --- | --- |
| Apple | `CIK0000320193-submissions-001.json` | 1994-01-26 to 2015-09-11 | `4995251b37e0` 16:57:28.443Z |
| Microsoft | `CIK0000789019-submissions-001.json` | 2008-08-13 to 2020-08-05 | `de768837e664` 16:57:28.887Z |
| Walmart | `CIK0000104169-submissions-001.json` | 2012-06-05 to 2023-05-26 | `7a164befa763` 16:57:29.177Z |
| Walmart | `CIK0000104169-submissions-002.json` | 1994-02-14 to 2012-06-04 | `e162c62bfcae` 16:57:29.338Z |

Each `requested_at` above can be up to 0.2 seconds earlier than its
request was sent, which is the time
[§6](../spec/snapshots.md#6-records) gives it. `scripts/snapshot.py`, the
same at `70ab28b` and `02b4946`, reads the clock before it waits out the
0.2 seconds it keeps between requests
([FETCH-6](../spec/snapshots.md#10-fetching)). So Walmart's second further
file's record, at 16:57:29.338Z, is 161 ms after the one before it, and
Walmart's `submissions` record, at 17:40:13.971Z, is 142 ms after
Microsoft's, closer together than the requests could start. The records
stay as they are ([§1](../spec/snapshots.md#1-guarantees)), and no
finding relies on a record's time to within a second.

### Findings so far

- **SRC-1** (Q2) A filing's header gives its acceptance time as
  `ACCEPTANCE-DATETIME`, 14 digits `YYYYMMDDHHMMSS`, with no time zone. In
  each of the eight filings, every file in the directory listing has that
  time as its `last-modified`, and its date is the header's
  `FILED AS OF DATE`.
  Shown by the eight headers and directory listings in the records cited.
- **SRC-2** (Q2) `submissions` gives `acceptanceDateTime` in RFC 3339 with a
  `Z`, and in all 3,002 recent filings of the three companies its
  milliseconds are `.000`. It is later than the header's time (SRC-1) by
  4 hours for Microsoft's and Walmart's filings from March to August and
  5 hours for Walmart's of January 30, as converting US Eastern time to UTC
  would make it. For all three of Apple's, it is 8 hours later. That
  includes the 8-K/A, whose accession number begins with another filer's
  CIK, 0001140361, and whose acceptance this puts on the day after its
  filing date. The baseline doesn't show which time is right for Apple, or
  why Apple's differ.

  | Filing | Header | `submissions` | Later by |
  | --- | --- | --- | --- |
  | Apple 10-K 0000320193-25-000079 | 20251031060126 | 2025-10-31T14:01:26.000Z | 8 h |
  | Apple 10-Q 0000320193-26-000020 | 20260731060102 | 2026-07-31T14:01:02.000Z | 8 h |
  | Apple 8-K/A 0001140361-26-035325 | 20260901163035 | 2026-09-02T00:30:35.000Z | 8 h |
  | Microsoft 10-K 0001193125-26-323660 | 20260729160801 | 2026-07-29T20:08:01.000Z | 4 h |
  | Microsoft 10-Q 0001193125-26-191507 | 20260429160624 | 2026-04-29T20:06:24.000Z | 4 h |
  | Walmart 10-K 0000104169-26-000055 | 20260313160624 | 2026-03-13T20:06:24.000Z | 4 h |
  | Walmart 10-Q 0000104169-26-000154 | 20260828161128 | 2026-08-28T20:11:28.000Z | 4 h |
  | Walmart 8-K/A 0000104169-26-000028 | 20260130161002 | 2026-01-30T21:10:02.000Z | 5 h |

  Shown by the three `submissions` records and the eight headers in the
  records cited.
- **SRC-3** (Q2) A company's fiscal year end can differ between its filings'
  headers. Apple's header for the 10-K of the year ending 2025-09-27 gives
  `FISCAL YEAR END` `0927`, while those of its 10-Q for the quarter ending
  2026-06-27 and its 8-K/A give `0926`. `submissions` gives one
  `fiscalYearEnd`, `0926`. Microsoft's headers all give `0630` and
  Walmart's `0131`, as their `submissions` do.
  Shown by the three `submissions` records and the eight headers in the
  records cited.
- **SRC-4** (Q1) Which validators a response carries depends on the kind of
  URL. In the baseline's 200s, `submissions`, `companyfacts`, and filing
  directory listings sent neither `ETag` nor `Last-Modified`. Filing
  indexes and headers sent only `Last-Modified`. XBRL instances, the daily
  index listing, and the daily master indexes sent both. A refetch of a URL
  without validators can't be conditional
  ([FETCH-4](../spec/snapshots.md#10-fetching)), so it receives the whole
  body each time. Whether a conditional request gets a `304` is for the
  first refetch to show.
  Shown by all 43 of the baseline's records, requested from 17:40:13.617Z
  to 17:41:48.887Z.
- **SRC-5** (Q4) Each day's daily index files have a `last-modified`
  between 10:01 and 10:04 PM on the date in their names, with no time zone
  given. The quarter's listing, fetched at 17:40:47.727Z, 1:40 PM US
  Eastern, held October 1, 2, 5, and 6, and not yet October 7.
  Shown by the daily index listing in the records cited.
- **SRC-6** (Q2) `companyfacts`' `fy` and `fp` are the fiscal year and
  period of the filing that reported a value, not of the value. In all 216
  accession numbers of the three companies, every value with the same
  `accn` has the same `fy`, `fp`, `form`, and `filed`, whatever its own
  dates. Apple's 10-K for the year ending 2025-09-27 gives `fy` 2025 and
  `fp` `FY` to all 427 of its values, whose `end` runs from 2022-09-24 to
  2025-10-17; 187 of them end on 2025-09-27. In the 86 filings that
  `submissions` lists (SRC-9), 46% of the values end on the filing's
  `reportDate`. In each of the six 10-Ks and 10-Qs whose XBRL instances are
  stored, `fy` and `fp` are the instance's `dei:DocumentFiscalYearFocus`
  and `dei:DocumentFiscalPeriodFocus`, and `reportDate` is its
  `dei:DocumentPeriodEndDate`. Six filings give a null `fy` and `fp` to
  all 1,828 of their values: three 8-Ks, Apple's of 2015-01-28 and
  Microsoft's of 2015-10-27 and 2024-12-03, and Walmart's PRE 14A, DEF 14A,
  and 424B2 of April 2026. A value's own period is given by its `start`,
  for a duration, and its `end` (SRC-7), though a duration that starts and
  ends on the same day has no `start` (SRC-11). Its `frame`, when it has
  one, is a calendar period matched to the value's, whose dates can differ
  from the value's own (SRC-8).
  Shown by the three `companyfacts` and `submissions` records and the six
  XBRL instances in the records cited.
- **SRC-7** (Q2) `fy` names a fiscal year by the calendar year it ends in,
  except in Walmart's filings through March 2014, and `fp` numbers a 10-Q's
  quarter from the start of the fiscal year. Apple's and Microsoft's 10-Ks
  and 10-Qs since 2009 all do so: Apple's quarter ending 2025-12-27 is `fy`
  2026, `fp` `Q1`, and so is Microsoft's ending 2025-09-30. Walmart's 20
  filings from 2009-09-09 to 2014-03-21 name the year it began in, so the
  10-K for the year ending 2014-01-31 gives `fy` 2013. Its 50 10-Ks and
  10-Qs since, from the 10-Q of 2014-06-06, name the year it ends in, so
  the quarter ending 2014-04-30 is `fy` 2015, `fp` `Q1`, and no Walmart
  filing gives `fy` 2014. A value's `start` and `end` are the first and
  last days of its period: Microsoft's years run from July 1 to June 30,
  and Walmart's from February 1 to January 31. Apple's last 52 or 53
  weeks, 364 or 371 days, and end on a Saturday from September 24 to 30:
  on `submissions`' `fiscalYearEnd`, `0926` (SRC-3), in 3 of its 17 years
  since 2009, after it in 9, and before it in 5.
  Shown by the three `companyfacts` and `submissions` records in the
  records cited.
- **SRC-8** (Q2) A value's `frame` is a calendar period matched to the
  value's own, not that period itself and not a fiscal one, and only some
  values have one. It is `CY` and a year, then `Q1` to `Q4` for a quarter,
  then `I` for an instant. An `I` frame is the calendar quarter end
  nearest the value's `end`, from 32 days before it to 42 days after:
  Walmart's instant of 2026-01-31 is `CY2025Q4I`, and Microsoft's
  `EntityCommonStockSharesOutstanding` of 2010-01-25 is `CY2009Q4I`. All
  but one of the 16,248 values with an `I` frame have no `start`, the one
  being Walmart's `CommonStockDividendsPerShareDeclared` from 2015-02-18
  to 2015-02-19, and a value with no `start` can be a duration (SRC-11),
  so an `I` frame doesn't show that a value is an instant. A quarter's
  frame is the calendar quarter holding most of it: Walmart's from
  2025-02-01 to 2025-04-30 is `CY2025Q1`, and Microsoft's ending
  2025-09-30, `fy` 2026 `Q1`, is `CY2025Q3`. A year's is the calendar year
  it begins in for Walmart's years, which end on January 31, and the one
  it ends in for Apple's and Microsoft's: Walmart's year ending
  2026-01-31, `fy` 2026, is `CY2025`, and Microsoft's from 2025-07-01 to
  2026-06-30, `fy` 2026, is `CY2026`, though 184 of its days fall in 2025.
  39% of Apple's values have a `frame`, 42% of Microsoft's, and 37% of
  Walmart's, and no two values of the same concept and unit have the same
  one. Which of the values reported for a period has it is for Q3.
  Shown by the three `companyfacts` records in the records cited.
- **SRC-9** (Q2) A value's `accn` joins it to its filing in `submissions`'
  `filings.recent`, and so to that filing's `acceptanceDateTime`, whenever
  `recent` holds the filing. `recent` holds 1,000 of Apple's filings, from
  2015-09-17; 1,002 of Microsoft's, from 2020-08-07; and 1,000 of
  Walmart's, from 2023-06-02. The 86 accession numbers in `companyfacts`
  filed since then are all in `recent`, with the same `form`, and with a
  `filingDate` equal to `filed`; they hold 31,807 of the three companies'
  82,991 values, 38%. Every 10-K and 10-Q in `recent` has values in
  `companyfacts`. For 13 of Apple's 44, filed from 2015-10-28 to
  2019-07-31, `acceptanceDateTime` is on the day after `filed`, between
  00:31 and 02:34 UTC, as SRC-2 found for Apple's 8-K/A; for none of
  Microsoft's or Walmart's. Of the eight filings whose headers are stored,
  the six 10-Ks and 10-Qs have values, each with its header's
  `FILED AS OF DATE` as `filed`, and the two 8-K/As have none.
  Shown by the three `companyfacts` and `submissions` records and the
  eight headers in the records cited.
- **SRC-10** (Q2) The stored `submissions` join the rest of the values,
  51,184 or 62%, to no filing and no acceptance time. Their 130 accession
  numbers were all filed before `recent`'s first filing: from 2009-07-22 to
  2015-07-22 for Apple, 40% of its values; from 2009-10-23 to 2020-07-30
  for Microsoft, 64%; and from 2009-09-09 to 2023-03-17 for Walmart, 80%.
  Each one's filing date lies within the dates of a further file that
  `submissions` names in `filings.files`, such as
  `CIK0000320193-submissions-001.json`, which the scope doesn't cover, so
  whether those files list them is still to be shown. On three days Apple
  made two filings with values, all among these, so `filed` doesn't order
  them: 2010-01-25, 2013-04-24, and 2015-01-28.
  Shown by the three `companyfacts` and `submissions` records in the
  records cited.
  **Superseded in part by SRC-12:** the further files, stored since
  October 10, list all 130 accession numbers, so the stored `submissions`
  now join every value to a filing and its `acceptanceDateTime`.
- **SRC-11** (Q2) `companyfacts` gives a duration that starts and ends on
  the same day as it gives an instant, with an `end` and no `start`. In
  the six 10-Ks and 10-Qs whose XBRL instances are stored, all but three
  of the 2,641 values have their context's dates: its `startDate` and
  `endDate` as their `start` and `end`, or its `instant` as their `end`.
  The three are Microsoft's `GoodwillImpairmentLoss` values of 0 in its
  10-K for the year ending 2026-06-30, whose contexts start and end on
  May 1 of 2024, 2025, and 2026: each has only an `end`, and an `I` frame,
  `CY2024Q1I`, `CY2025Q1I`, and `CY2026Q1I` (SRC-8). So a value with no
  `start` isn't always an instant. In the three companies' `companyfacts`,
  59 values of four concepts, in 31 filings, have no `start` while the
  same company's other values of that concept do; these three are the
  only ones in a filing whose instance is stored.
  Shown by the three `companyfacts` records and the six XBRL instances in
  the records cited.
- **SRC-12** (Q2) `submissions`' further files list a company's older
  filings in the same 16 columns as `filings.recent`, and with them every
  `companyfacts` value of the three companies joins to a filing and its
  `acceptanceDateTime`. Each further file is a JSON object of those
  columns, each a list as long as the `filingCount` that `filings.files`
  gives the file, and no accession number is in two of a company's lists.
  A file's earliest `filingDate` is its `filingFrom`, but its latest can be
  earlier than its `filingTo`: Apple's file ends on 2015-09-11, not
  2015-09-15; Microsoft's first on 2020-08-05, not 2020-08-06; and
  Walmart's first on 2023-05-26, not 2023-06-01. Every `acceptanceDateTime`
  in them is RFC 3339 with a `Z` and milliseconds of `.000`, as in
  `filings.recent` (SRC-2). They list all 130 accession numbers that
  SRC-10 joined to no filing: Apple's 28 in its file, Microsoft's 46 in its
  first, and Walmart's 56 in its two, each with the same `form` and with a
  `filingDate` equal to `filed`, as SRC-9 found for `filings.recent`. So
  all 82,991 values join, the 51,184 of SRC-10 through the further files.
  Microsoft's second file, which lists its filings from 1994-02-14 to
  2008-08-12, before any of its values, isn't needed for them.
  Shown by the three `companyfacts` and `submissions` records and the four
  further files in the records cited.
- **SRC-13** (Q2) Read as the UTC instants they state, `submissions`'
  `acceptanceDateTime` values put most of Apple's filings, and most of
  Walmart's before June 2023, outside the hours the SEC gives for EDGAR:
  6:00 a.m. to 10:00 p.m. Eastern Time on business days
  ([EDGAR Filer Manual, Volume II, §2.3.1](https://www.sec.gov/files/edgar/filermanual/efmvol2-c2.pdf)).
  Each count here compares the time of day alone, not whether the day is
  a business day. 1,371 of Apple's 2,126 filings since 2003 fall outside
  those hours, 1,088 after 10 p.m., in `filings.recent` and its further
  file alike. 1,732 of Walmart's 3,274 filings from 2003 to 2023-05-26, in
  its further files, fall outside them, but none of the 1,000 in its
  `filings.recent`, which begins on 2023-06-02. Only 3 of Microsoft's
  3,004 filings since 2008-08-13 do, all `EFFECT` notices that the SEC
  files itself, with accession numbers beginning `9999999995`, at 00:15
  Eastern Time. Moved 8 hours earlier, the difference SRC-2 found between
  Apple's `submissions` and its headers, and read as Eastern Time, all but
  52 of those Apple filings and all but 30 of those Walmart filings fall
  within EDGAR's hours. Read as Eastern Time, all eight stored headers'
  times do (SRC-1). Before 2003, 130 of Apple's 138 filings and 118 of
  Walmart's 147 have an `acceptanceDateTime` of exactly 08:00:00 or
  10:00:00 UTC, which no stored filing since 2003 has; Microsoft's before
  2008-08-13 aren't stored. So, read as UTC, `acceptanceDateTime`
  disagrees with EDGAR's stated hours for those filings, and `submissions`
  doesn't mark which of its values do. Which time is right for them isn't
  shown: no stored header is of one of them, and the stored versions don't
  show whether the SEC's own `EFFECT` notices keep EDGAR's hours.
  Shown by the three `submissions` records, the four further files, and
  the eight headers in the records cited.
- **SRC-14** (Q3) `companyfacts` keeps each filing's value for a concept,
  unit, and period beside every other filing's, each with its own `accn`,
  `filed`, `form`, `fy`, and `fp`; a later filing's value replaces none. Of
  the three companies' 40,702 keys, each a concept, a unit, a `start`, and
  an `end`, 24,825 have values from more than one filing, up to 12, and no
  filing gives a key twice. In each of the six 10-Ks and 10-Qs whose XBRL
  instances are stored, the values with its `accn` are its facts of
  `us-gaap` and `dei` concepts that have a unit, no dimensions, and aren't
  nil, one value per key, whether or not an earlier filing reported them:
  1,386 of their 2,641 values have a key an earlier filing reported. Each
  value is its fact's, except Microsoft's `EntityListingParValuePerShare`,
  0.00000625 in both its instances and 0.000006 in `companyfacts`. Facts of
  a company's own concepts, facts with dimensions, and nil facts don't
  appear. Three of the instances give two values for each of 23 keys, one
  to `decimals` -6 and one to -8, and `companyfacts` gives the one to -6:
  Walmart's 10-K for the year ending 2026-01-31 gives its cash at that
  date as 10,700,000,000 and 10,727,000,000, and `companyfacts` gives
  10,727,000,000. Five 8-Ks also report the 10-K before each again:
  Apple's of 2013-04-24 and 2015-01-28, and Microsoft's of 2013-11-26,
  2015-10-27, and 2024-12-03. 2,859 of their 2,888 values have a key that
  10-K reported, each with the 10-K's value.
  Shown by the three `companyfacts` records and the six 10-K and 10-Q XBRL
  instances in the records cited.
- **SRC-15** (Q3) A restated value differs from one reported again
  unchanged only in its `val`. No field says that a value changed, which
  earlier value it differs from, or why, and `companyfacts` doesn't give a
  value's `decimals`. Of the 24,825 keys with values from more than one
  filing (SRC-14), 1,487 have more than one `val`: 426 of Apple's 7,141,
  434 of Microsoft's 8,860, and 627 of Walmart's 8,824. In order of
  `filed`, and on Apple's three days with two filings (SRC-10) of
  accession number, which there is also the order of `acceptanceDateTime`
  (SRC-12), their values change 1,559 times, nearly all in 10-Qs and
  10-Ks: 870 times in a 10-Q, 622 in a 10-K, 62 in Apple's 10-K/A
  (SRC-16), 4 in an 8-K, and once in Microsoft's 10-Q/A. The four in 8-Ks
  each return to the 10-K's value after a 10-Q changed it (SRC-14). 107
  of the changes are arithmetic matches: both values are nonzero, and one
  equals the other rounded half-up, with halves rounded away from zero,
  at the place of the coarser value's last nonzero digit. Of the 107, the
  stored instances give both amounts' `decimals`, each from the filing
  that reported it or a later one that repeats it, for only 3. In two,
  the precision falls from the 10-K's instance to a later 10-Q's, and the
  10-Q that made the change has no stored instance. Apple's
  `LongTermDebt` at 2025-09-27 is 90,678,000,000 in its 10-K for the year
  ending then, to `decimals` -6 in its instance, and 90,700,000,000 in
  its three 10-Qs since, to -8 in the instance of the last. Walmart's
  `SupplierFinanceProgramObligation` at 2026-01-31 is 5,989,000,000 in
  its 10-K for the year ending then, to -6 in its instance, and
  6,000,000,000 in its two 10-Qs since, to -8 in the instance of the
  last. The third is a restatement at the same precision:
  Microsoft's `DividendsCommonStockCash` for 2026-01-01 to 2026-03-31 is
  6,760,000,000 in its 10-Q of 2026-04-29 and 6,758,000,000 in its 10-K
  of 2026-07-29, to -6 in both instances. Other changes keep the
  precision: Apple's `OtherAssetsNoncurrent` at 2025-09-27 is
  83,727,000,000 in the 10-K and the 10-Q of 2026-01-30, and
  72,634,000,000 in the 10-Qs of 2026-05-01 and 2026-07-31, to -6 in both
  instances. In 29 keys a later filing returns to an earlier value:
  Apple's `LongTermDebt` at 2023-09-30 is 105,103,000,000 in its 10-K of
  2023-11-03, 105,100,000,000 in the three 10-Qs after it, and
  105,103,000,000 again in its 10-K of 2024-11-01.
  Shown by the three `companyfacts` records, Apple's further file, and the
  six 10-K and 10-Q XBRL instances in the records cited.
- **SRC-16** (Q3) An amendment appears in `companyfacts` as a filing of its
  own: form `10-K/A` or `10-Q/A`, its own `accn` and `filed`, and the `fy`
  and `fp` of the report it amends. The values of the filing it amends
  stay beside its own (SRC-14). Three of the 216 accession numbers are
  amendments, all filed before `recent`'s first filing (SRC-10):

  - Apple's 10-K/A of 2010-01-25 gives `fy` 2009 and `fp` `FY`, as its
    10-K of 2009-10-27 does. All 210 of its values have keys the 10-K
    reported, and 77 differ from the 10-K's, 15 of which Apple's 10-Q of
    the same day also gives. `SalesRevenueNet` for the year ending
    2009-09-26 is 36,537,000,000 in the 10-K and 42,905,000,000 in the
    10-K/A, and `NetIncomeLoss` 5,704,000,000 and 8,235,000,000.
  - Walmart's 10-Q/A of 2013-10-21 gives `fy` 2013 and `fp` `Q2`, as its
    10-Q of 2013-09-05 does. 288 of its 289 values have keys the 10-Q
    reported, each with the 10-Q's value. The other is
    `EntityCommonStockSharesOutstanding` at 2013-10-17, which the 10-Q
    gives at 2013-09-03.
  - Microsoft's 10-Q/A of 2012-01-27, `fy` 2012 and `fp` `Q2`, is the only
    filing in `companyfacts` with that `fy` and `fp`, and the first filing
    to report the keys of 245 of its 440 values, among them all 218 that
    end on 2011-12-31. Microsoft's further file (SRC-12) lists a 10-Q for
    that quarter, 0001193125-12-017029, filed 2012-01-19 and marked
    `isXBRL` 0, which has no values in `companyfacts`; it marks the 10-Q/A,
    whose `reportDate` is also 2011-12-31, `isXBRL` 1.

  `recent` lists no 10-K/A or 10-Q/A for the three companies, and none of
  the four 8-K/As it marks `isXBRL` has values in `companyfacts`. The
  instances of two of them, Apple's and Walmart's (SRC-9), hold only `dei`
  facts with no unit, 40 and 49.
  Shown by the three `companyfacts` and `submissions` records, Microsoft's
  further file, and the two 8-K/A XBRL instances in the records cited.
- **SRC-17** (Q3) Of a key's values, only the latest filed has a `frame`
  (SRC-8), whether it repeats the earlier values or changes them. In all
  18,902 keys that have values from more than one filing and a frame, the
  frame is on the value with the latest `filed`. In six of Apple's keys,
  its 10-Q and 8-K of 2013-04-24 are both latest, and the frame is on the
  8-K's value, whose accession number and `acceptanceDateTime` (SRC-12)
  are the later. So a frame carries the
  latest filing's value even where an earlier one is more precise: Apple's
  `LongTermDebt` at 2025-09-27 has `CY2025Q3I` on the 90,700,000,000 of
  its 10-Q of 2026-07-31, not the 90,678,000,000 of its 10-K (SRC-15). The
  baseline holds one version of each company's `companyfacts`, so it
  doesn't show a frame moving to a later filing's value when that filing
  appears; the daily refetches through each company's next report
  (method, step 2) can.
  Shown by the three `companyfacts` records, Apple's further file, and
  Apple's 10-K and 10-Q XBRL instances in the records cited.
