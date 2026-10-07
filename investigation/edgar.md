# Source investigation: EDGAR

**Status:** Started October 7, 2026. No findings yet. Data here comes from
the U.S. Securities and Exchange Commission's EDGAR system.

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
2. **Daily refetch.** Each business evening, refetch `submissions`,
   `companyfacts`, and the daily index listing, and fetch the new day's
   master index. A refetch sends the last version's validators, so an
   unchanged URL adds a record without a copy. Continue through each
   company's next periodic report: Microsoft's and Apple's are expected in
   late October or early November, and Walmart's after its quarter ends
   October 31.
3. **New filings.** When a company files a 10-K, a 10-Q, or an amendment,
   fetch its filing index, header, and XBRL instance, and note when it first
   appears in each feed.
4. **Findings.** Write each finding as the versions show it, and commit it
   with the store it cites, after `uv run scripts/verify.py`.

SEC documentation can suggest what to look for, but a finding rests on
stored versions.

## Questions

Each question says what the pipeline's specification needs from it.

- **Q1. What each feed returns and how it changes.** Content type, encoding,
  and size; whether `ETag` and `Last-Modified` are sent and a conditional
  request gets a `304`; how often the content changes; and whether a URL
  returns to an earlier body. Decides which feeds the pipeline reads and how
  often.
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
  day's index is published; and whether a published index changes. Decides
  where the pipeline's notices of new filings come from.
- **Q5. Sizes and pace.** Bytes per URL and per filing, growth per refetch,
  and the requests and time a backfill of one company's filings takes at 5
  requests a second. Sets the store's budget and says whether the pace
  suits backfills.

The investigation is done when every question has findings the pipeline's
specification can cite.

## Findings

A finding states one behavior, names the question it answers, and cites the
records that show it: the feed, the URL, the first 12 hex digits of the
`sha256` (as `snapshot.py` prints them), and `requested_at`.
`uv run scripts/verify.py -v` lists each URL's versions. Findings have IDs,
`SRC-1` onward, that are never reused. A finding that later versions
contradict is marked superseded and stays; a new finding cites the new
versions.

```markdown
- **SRC-1** (Q2) The behavior, in one or two sentences.
  Shown by `submissions` https://data.sec.gov/submissions/CIK0000320193.json
  `0123456789ab` at 2026-10-07T22:00:00.000Z.
```

None yet.
