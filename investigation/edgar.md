# Source investigation: EDGAR

**Status:** Started October 7, 2026. The baseline was fetched October 7;
five findings so far. Since October 10, how soon each feed shows a new
filing (Q4) is measured from the pipeline's record. Data here comes from the
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

The quarter's daily index listing, in `daily-index` at
`https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/index.json`:
`b2627c1a365d` 17:40:47.727Z.

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
