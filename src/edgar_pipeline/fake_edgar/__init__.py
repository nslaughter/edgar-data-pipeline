"""The fake EDGAR: answers as EDGAR from the stored versions in a git store.

A service for tests, never one of the pipeline's own (spec/fake-edgar.md). It
serves each stored URL's latest version, with the validators its record kept,
and a 304 to a request that asks about it (SERVE-1 to SERVE-4); gives the
responses a test cues through its admin port in place of those (CUE-1,
CUE-2, ADMIN-1); and logs every request its server receives, in memory only,
for tests to read through the admin port (REQ-1, REQ-2). `checks` holds the
one set of checks of the fetching rules that reads that log (CHECK-1). It
uses only Python's standard library (decision record 0006).

    python -m edgar_pipeline.fake_edgar --data data
"""
