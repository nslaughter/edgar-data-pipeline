# The snapshot store

Source: U.S. Securities and Exchange Commission, EDGAR.

```text
data/
  feeds/FEED/versions.jsonl     one record per fetch, appended, never edited
  objects/AA/SHA256.gz          each distinct body once, gzipped
  imports/IMPORT.json           one manifest per import run
```

[`spec/snapshots.md`](../spec/snapshots.md) governs everything here: the
layout, each record's fields, what a version is, how fetches are made, what
an import's manifest pins, and what `scripts/verify.py` checks. List each
URL's versions with `uv run scripts/verify.py -v`.
