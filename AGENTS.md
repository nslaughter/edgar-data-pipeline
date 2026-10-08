# Working in this repository

This repository will hold a pipeline for SEC EDGAR filings and the source
investigation it rests on. For now it holds the scripts that snapshot
EDGAR's feeds and verify the stored versions.

## Read these first

| Document | Governs |
| --- | --- |
| [`spec/snapshots.md`](spec/snapshots.md) | Everything under `data/`: the layout, records, versions, history, scope, how fetches are made, and import manifests, as numbered statements with an index. `scripts/snapshot.py` implements its rules and `scripts/verify.py` checks its invariants. |
| [`investigation/edgar.md`](investigation/edgar.md) | What EDGAR's feeds and filings do: the investigation's scope, method, and questions, and findings with IDs that cite the stored versions showing them. The pipeline's specification depends on EDGAR only through these findings. |
| [`docs/design.md`](docs/design.md) | Nothing; it explains. The pipeline's architecture on Kubernetes, the record it keeps and its times, and what waits on the investigation. [`docs/decisions/`](docs/decisions/) records each significant choice and its status. |

## Where documents go

Each document makes one kind of claim, and its directory says which.

```text
spec/                    normative: numbered statements that code and tests cite
  snapshots.md           the snapshot store
  NAME.md                one per component, or boundary between components
  schemas/               machine-readable contracts that statements name
docs/
  design.md              the architecture and the reasons for it; makes no rules
  decisions/NNNN-*.md    one record per significant choice
investigation/
  edgar.md               what EDGAR does, as findings with their evidence
```

- **A rule the code or data must follow goes in `spec/`,** where a test or
  `verify.py` checks it. An explanation of why goes in `docs/`. What EDGAR
  does goes in `investigation/`. The README describes the project for people
  and is none of these.
- **One spec per component or boundary, not one per kind of content.** A
  boundary's spec is its contract: what the producer guarantees and what the
  consumer may rely on. A component's behavior and its interface change
  together, so they share a document and a version. There is no separate
  contracts document.
- **A contract a machine can check is a schema** in `spec/schemas/`: JSON
  Schema for messages and records, SQL for tables. A statement names it, such
  as "a notice validates against `notice.schema.json`", and tests validate
  against it. The schema is part of its spec, so changing it changes the
  spec.
- **Each spec has its own version,** as its document says, so the store's
  contract stays put while the pipeline's specs change. Each ID area, such
  as `REC` or `FETCH`, belongs to one spec, so an ID names one statement
  across all of `spec/`.
- **Specs depend on EDGAR only through findings.** A statement that relies
  on how the source behaves cites the finding's `SRC` ID. Code and tests cite
  spec IDs, not findings.
- **The design document makes no rules.** It describes the components, the
  boundaries between them, and the alternatives rejected, and cites spec IDs
  for anything normative. A testable rule found there moves to a spec.
- **A significant choice gets a decision record,** such as which queue or
  database to use: `docs/decisions/NNNN-title.md`, numbered in order, with
  the context, the options, the choice, and its consequences. A later record
  supersedes an earlier one instead of editing it.
- **Documents come in order:** the investigation, then the design, then a
  spec per component or boundary, then code. Parts of the design that don't
  depend on the source, such as deployment, can start sooner.
- **Never move or rename a spec.** Commits and pull requests on `main` link
  to its path, and that history can't be changed.

## Rules

- **The specification comes first.** If code and a specification disagree,
  investigate the code first. If you conclude the specification is wrong,
  ambiguous, or contradicts itself, stop and report it, quoting the passages,
  instead of choosing an interpretation. A change to a specification is its
  own pull request, versioned as the document says.
- **Every statement has an ID, and its tests carry it.** Mark each test with
  the IDs it checks, as `@pytest.mark.spec("REC-5")`. A new statement gets
  the next unused number in its area; IDs are never reused or renumbered.
  `tests/test_spec_coverage.py` fails if a statement checked by `verify` or
  `tests` has no test, or a marker names no statement. It reads only
  `spec/snapshots.md` so far; the pull request that adds a second spec
  extends it to every `spec/*.md`.
- **Investigate the source before specifying the pipeline.** Record how
  EDGAR's feeds and filings behave in
  [`investigation/edgar.md`](investigation/edgar.md), with the stored
  versions that show it, before a specification depends on that behavior.
  Don't build undocumented source behavior into code on your own; report it.
- **Every request to the SEC goes through `scripts/snapshot.py`,** or later
  through a fetcher that keeps the same rules: a declared `SEC_USER_AGENT`,
  at most 5 requests a second, one process at a time, and a stop on a 403.
  Never put the `SEC_USER_AGENT` value in code, tests, records, commits, or
  pull requests; it lives in the environment or in `.env.dev`, which git
  ignores ([FETCH-15](spec/snapshots.md#10-fetching)). Run anything against
  the SEC only when the operator asks for that run; a `SEC_USER_AGENT` in the
  environment or `.env.dev` is not a request.
- **Keep the store's scope small.** `data/scope.json` lists the URLs the
  store may hold, each with its reason
  ([§9](spec/snapshots.md#9-scope)), and `snapshot.py` fetches nothing
  else. Widen it only when the operator asks, in its own commit, before
  fetching what it adds. Never remove an entry that covers a record.
- **Never edit or delete stored data** ([§1](spec/snapshots.md#1-guarantees)).
  Logs are appended to, and objects and manifests are only added. A
  correction is a new record. Run `uv run scripts/verify.py` before
  committing anything under `data/`.
- **Imports read stored versions, never the live source.** Each import
  writes its manifest before reading its inputs
  ([§11](spec/snapshots.md#11-imports)).
- **Cite the source.** Data here comes from the U.S. Securities and Exchange
  Commission's EDGAR system; say so wherever it is published or described.
- **Tests never reach the network.** They use a local server or stored
  versions.
- **Nothing outside this repository.** Do not open issues, post comments, or
  send anything to other projects unless the operator asks.

## Commands

| Task | Command |
| --- | --- |
| Install the development environment | `uv sync` |
| Format check | `uv run ruff format --check .` |
| Lint | `uv run ruff check .` |
| Type check | `uv run mypy` |
| Tests | `uv run pytest` |
| Snapshot a feed | `uv run scripts/snapshot.py FEED URL ...`, with `SEC_USER_AGENT` in the environment or `.env.dev` |
| Verify the store | `uv run scripts/verify.py -v` |
| Verify history since a commit | `uv run scripts/verify.py --since origin/main` |

## Commits and pull requests

Write commit subjects as short imperative sentences in sentence case, such as
"Snapshot feeds into versioned storage", with a body that says what changed
and why. Commit each logical change separately.

The repository allows squash merges only, so the pull request's description
becomes the commit on `main`. Keep it current as commits are added. It lists
what changed, the checks run with their results, and anything deferred.
