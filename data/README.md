# The snapshot store

Source: U.S. Securities and Exchange Commission, EDGAR.

```text
data/
  scope.json                    the URLs the store may hold, each with a reason
  feeds/FEED/versions.jsonl     one record per fetch, appended, never edited
  objects/AA/SHA256.gz          each distinct body once, gzipped
  imports/IMPORT.json           one manifest per import run
```

[`spec/snapshots.md`](../spec/snapshots.md) governs everything here: the
layout, each record's fields, what a version is, the scope, how fetches are
made, what an import's manifest pins, and what `scripts/verify.py` checks.
The store is committed to git, so its scope is kept deliberately small: a few
companies' filings and the feeds that announce them. List each URL's
versions with `uv run scripts/verify.py -v`.
