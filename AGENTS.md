# Working in this repository

This repository will hold a pipeline for SEC EDGAR filings and the source
investigation it rests on. For now it holds the scripts that snapshot
EDGAR's feeds and verify the stored versions.

## Read these first

| Document | Governs |
| --- | --- |
| [`spec/snapshots.md`](spec/snapshots.md) | The two stores of what the pipeline fetches: everything under `data/`, and the cluster's objects in Garage and records in PostgreSQL. Their layout, records, versions, history, and scope; how fetches are made, by `scripts/snapshot.py` or the fetcher; the jobs that ask the fetcher for fetches and the events that announce their records, whose schemas are in [`spec/schemas/`](spec/schemas/); how the cluster's records reach `data/`, and the transfer's read-only access to the cluster store; and import manifests, as numbered statements with an index. `scripts/snapshot.py` implements its rules and `scripts/verify.py` checks its invariants of `data/`. |
| [`spec/deployment.md`](spec/deployment.md) | What the four Helm charts under `deploy/` install, in which order and at which versions, and how they configure it: their dependencies and the definitions each installs, the pins of every chart, image, and tool, PostgreSQL's major version 18, each component's replicas, storage, credentials, and placement, the cluster store's bucket, database, table, and streams, the fetcher's Deployment, Lease, grants, credentials, durable consumers of jobs, and trust of the fake EDGAR's certificate authority, the scheduler's Deployment, Lease, grants, and durable consumer of poll events, the discoverer's streams, tables, credentials, and durable consumers, Grafana's database role and data source, the transfer's database role, Garage key, and access to the cluster store through the API server, the fake EDGAR that CI installs, the kind cluster, and the pipeline's own images and how they're built, as numbered statements with an index. The charts are under `deploy/`; tests render them, in `tests/test_deployment.py`, and check them installed on kind, in `tests/test_kind.py`. |
| [`spec/controls.md`](spec/controls.md) | The pipeline's controls: the `Source`, `Backfill`, and `SecAccess` custom resources, whose definitions are in [`spec/schemas/`](spec/schemas/); the chart's values they're rendered from and the checks on them, such as each cadence's bounds; what the scheduler and the fetcher do with each; and who may change what, as numbered statements with an index. The scheduler implements its rules for Sources; the rest of its code, and most of its tests, are still to be written. |
| [`spec/notices.md`](spec/notices.md) | What the discoverer makes of the fetcher's records: the filings each `Source`'s polls show, with both acceptance times as given and the polls that bound when each `Source` first showed them; each filing's files and their fetches; how the scheduler hands each `Backfill` to the discoverer, and its progress; each `Source`'s coverage and gaps; the orders and notices between the services, whose schemas are in [`spec/schemas/`](spec/schemas/) with the tables and views; and the views that give each lag, as numbered statements with an index. The discoverer and most of its tests are still to be written. |
| [`spec/telemetry.md`](spec/telemetry.md) | What the pipeline reports about itself while it observes filings: the metrics the scheduler, the fetcher, and the discoverer serve, the fetcher's clock among them; the metrics kube-state-metrics exports from the controls' status; what Prometheus scrapes; the alerts on a stop after a `403`, on a gap longer than its `Source`'s cadence, and on a backup more than a day old; the views of coverage, gaps, and lags that Grafana reads, defined in [`spec/schemas/`](spec/schemas/); and the trace context carried through NATS, as numbered statements with an index. The metrics, alerts, and traces, and most of their tests, are still to be written. |
| [`spec/site.md`](spec/site.md) | The documentation site that publishes these documents: which documents it holds, built from their files where they are; the pages written for it, which make no rules and cite the document that owns each fact, `edgar-asof`'s among them; each part's status, derived from the plan, the specifications, and the decision records, on a status page a test keeps current; the estimates and the analysis beyond the plan; and how the site is built, checked by the tests, and published from `main` by GitHub Pages, as numbered statements with an index. The site and its tests are still to be written. |
| [`spec/fake-edgar.md`](spec/fake-edgar.md) | The fake EDGAR that tests fetch from: which stored version under `data/` it serves for a request, and its `304`s; the responses a test cues it to give, such as a `403` or a body cut short; its log of the requests its server receives, read through its admin port, whose schemas are in [`spec/schemas/`](spec/schemas/); and the one set of checks of the fetching rules that reads that log, for `scripts/snapshot.py` and the fetcher, as numbered statements with an index. The fake EDGAR and most of its tests are still to be written. |
| [`investigation/edgar.md`](investigation/edgar.md) | What EDGAR's feeds and filings do: the investigation's scope, method, and questions, and findings with IDs that cite the stored versions showing them. The pipeline's specification depends on EDGAR only through these findings. |
| [`docs/design.md`](docs/design.md) | Nothing; it explains. The pipeline's architecture on Kubernetes, the record it keeps and its times, and what waits on the investigation. [`docs/decisions/`](docs/decisions/) records each significant choice and its status. |
| [`docs/plan.md`](docs/plan.md) | Nothing; it orders the work. The tasks that build the pipeline, in order, what each depends on, and which are done. [Doing the next item](#doing-the-next-item) takes the next task from it. |

## Where documents go

Each document makes one kind of claim, and its directory says which.

```text
spec/                    normative: numbered statements that code and tests cite
  snapshots.md           the snapshot store
  deployment.md          what the charts install, in which order, and its pins
  controls.md            the custom resources that control the pipeline
  notices.md             the filings the discoverer finds, their times, and gaps
  telemetry.md           the metrics, alerts, dashboards' views, and traces
  site.md                the documentation site, its pages, and their statuses
  fake-edgar.md          the fake EDGAR that tests fetch from
  NAME.md                one per component, or boundary between components
  schemas/               machine-readable contracts that statements name
docs/
  design.md              the architecture and the reasons for it; makes no rules
  decisions/NNNN-*.md    one record per significant choice
  plan.md                the tasks, in order, and which are done
  status.md              each part's status, generated from the plan
  explanation/           the site's explanation of the design; makes no rules
  glossary.md            the terms the explanation uses
  third-party-notices.md the notices of the files the site serves
index.md                 the site's home page; makes no rules
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
  Schema for messages and records, SQL for tables, and a
  `CustomResourceDefinition` for a custom resource. A statement names it, such
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
- **The pages written for the site explain, as the design does.** They
  make no rules, and cite the document that owns each fact
  ([PAGE-2](spec/site.md#7-the-pages-written-for-the-site)).
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
  the IDs it checks, as `@pytest.mark.spec("REC-5")` in Python, or in Go as
  a line `// spec: REC-5, REC-6` of the test's doc comment, directly above
  its `func TestName(t *testing.T)`. A new statement gets the next unused
  number in its area; IDs are never reused or renumbered.
  `tests/test_spec_coverage.py` reads every `spec/*.md`, and fails if a
  statement checked by `verify`, `tests`, or `kind` has no test, or a marker
  names no statement. A statement whose tests a later task in the plan
  writes is listed there with that task, until the tests exist.
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
  the SEC only when the owner asks for that run; a `SEC_USER_AGENT` in the
  environment or `.env.dev` is not a request.
- **Keep the store's scope small.** `data/scope.json` lists the URLs the
  store may hold, each with its reason
  ([§9](spec/snapshots.md#9-scope)), and `snapshot.py` fetches nothing
  else. Widen it only when the owner asks, in its own commit, before
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
  send anything to other projects unless the owner asks.

## Commands

| Task | Command |
| --- | --- |
| Install the development environment | `uv sync` |
| Format check | `uv run ruff format --check .` |
| Lint | `uv run ruff check .` |
| Type check | `uv run mypy` |
| Tests | `uv run pytest`, with Helm 4 and jq installed |
| Go checks | `go mod tidy -diff`, `go vet ./...`, and `golangci-lint run`, at the version CI pins |
| Go tests | `KUBEBUILDER_ASSETS="$(go tool setup-envtest use 1.36.2 -p path)" go test ./...` |
| Regenerate the resources' Go code | `go tool controller-gen object paths=./api/...` |
| Build the pipeline's images and load them into kind | `uv run scripts/build_images.py` |
| Test the installed charts | `EDGAR_KUBE_CONTEXT=kind-edgar uv run pytest tests/test_kind.py` |
| Snapshot a feed | `uv run scripts/snapshot.py FEED URL ...`, with `SEC_USER_AGENT` in the environment or `.env.dev` |
| Transfer a range of the cluster store into `data/` | `go run ./cmd/transfer --context CONTEXT --from TIME --until TIME`, then `uv run scripts/verify.py` |
| Verify the store | `uv run scripts/verify.py -v` |
| Verify history since a commit | `uv run scripts/verify.py --since origin/main` |
| Build and check the documentation site | `uv run scripts/build_site.py` |
| Update the site's statuses | `uv run scripts/site_status.py` |

## Commits and pull requests

Write commit subjects as short imperative sentences in sentence case, such as
"Snapshot feeds into versioned storage", with a body that says what changed
and why. Commit each logical change separately.

The repository allows squash merges only, so the pull request's description
becomes the commit on `main`. Keep it current as commits are added. It lists
what changed, the checks run with their results, and anything deferred.

A pull request merges only once the checks a ruleset on `main` requires have
passed: the `CI` workflow's jobs `Python 3.12`, `Python 3.13`, and
`Python 3.14`. The ruleset names each job it requires, and only the owner
changes it, on GitHub. So a pull request that adds, removes, or renames a CI
job names among what awaits the owner whether the ruleset changes with it,
and when. The jobs are named for the Python versions they run on, so a change
to the versions renames them. A job the pull request no longer runs leaves
the ruleset before the merge, or the pull request can't merge. A job newly
required joins it after the merge, and each other open pull request then
merges `origin/main`, so that the job runs on it.

No pull request merges until the owner's iterative review has run on it and
ended, whatever it changes: documents, data, and this file included. The
owner starts the review, with `/iterative-review`. Each of its reviews is by a
reviewer who didn't write or fix the change and doesn't share the context of
whoever did. It runs in this order:

1. Two reviews at the same time.
2. Fixes: each finding is fixed, or declined with a reason, and answered under
   it on the pull request.
3. A third review, of the fixes, when the first two reviews found anything of
   high or medium severity or the fixes changed behavior or tests, and only
   if the fixes pushed a commit. Its findings are fixed or declined the same
   way, which ends the review. Without a third review, the fixes end it.

When the first two reviews find nothing, the review ends there. Each review is
posted on the pull request as a review of its own, numbered, even when it
finds nothing, so the pull request shows which reviews ran. A review of the
whole code, with `--codebase`, has no pull request until its fixes open one,
so that pull request's description records the first two reviews instead,
and any third review is posted on it. A run with `--review-only` posts
nothing and fixes nothing, so it doesn't count.

An agent merges a pull request only when the owner asks it to merge that pull
request, in the current conversation, and only after the owner's iterative
review has ended. The owner's approval to merge comes after the review, never
in place of it. If the review hasn't run, or hasn't ended, don't merge, even
when the owner asks: say so, and stop.

The review's comments are anchored to the branch's commits, so never rebase,
amend, or force-push a branch with an open pull request. To bring in changes
from `main`, merge `origin/main` into the branch.

## Doing the next item

When the owner asks for the next item, do the next task in the plan,
`docs/plan.md`: one task, in one pull request. The request assigns that task
and nothing beyond it.

1. **Update `main`.** Fetch `origin`, and fast-forward the local `main` to
   `origin/main`. Leave the main checkout on `main`, because other agents may
   share it. If the update changed this file or the plan, read it again
   before going on.
2. **Find the task.** Go through the plan's tasks in order. The next item is
   the first task that is:
   - **not done:** its row in the plan on `main` doesn't mark it done. The
     plan is the progress record, and each task's pull request updates it.
   - **not taken:** no open pull request names its ID in the title
     (`gh pr list --state open`), and no branch names it
     (`git branch --all --list '*/<id>-*'`, with the ID in lowercase). A
     task is taken from the moment step 4 makes its branch.
   - **ready to start:** each task it depends on is done. Documents still
     come in order ([where documents go](#where-documents-go)): a
     specification that relies on the source needs the findings it cites,
     and code needs its specification. A decision record it depends on must
     be Accepted. A release of another repository it depends on, such as
     `edgar-asof`, must be published. An approval it needs, such as a run
     against the SEC or a wider scope, is met only when the owner gives it
     in the current conversation.

   Never build on an unmerged branch. A task whose dependency is still in an
   open pull request waits for it to merge.
3. **Stop if nothing can start.** Make no branch and no change. Report why,
   and what would unblock the next task: a pull request to merge, a decision
   record to accept, an approval such as a run against the SEC, a finding
   the investigation still needs, or a release of `edgar-asof`. When there
   is no plan, or every task in it is done, the next step is for the owner
   to write or extend it. Stop too when the documents contradict each other
   on the task ([rules](#rules)).
4. **Make a worktree.** Create the branch `<area>/<id>-<short-name>` from
   `origin/main` in a git worktree of its own, outside the main checkout,
   and run `uv sync` in it. `<id>` is the task's ID in lowercase, so other
   agents see that the task is taken (step 2). `<area>` is the directory the
   task mainly changes, as earlier branches did, such as `investigation`,
   `docs`, `spec`, or `scripts`, as in `spec/t4-notices`.
5. **Read for the task.** Follow [read these first](#read-these-first), and
   read closely what the task's row names: the statements and their tests,
   the findings and the stored versions they cite, the decision records, and
   the design's sections.
6. **Do the task, and only the task.** Keep the [rules](#rules): a change to
   a specification is its own pull request, versioned as the document says,
   and a run against the SEC happens only when the owner asks for it. Don't
   change what a statement, a finding, or an Accepted decision record says
   beyond what the task asks: that takes the owner's decision, so stop and
   ask. A test of this task's work that the plan assigns to a later task may
   be written now: say in the plan and the pull request that it was written
   ahead of that task.
7. **Record the details you settled.** Where the task left a detail open and
   you decided it, write it where [where documents go](#where-documents-go)
   puts it: a rule as a statement in its spec, with the next unused ID; what
   EDGAR does as a finding, with the stored versions that show it; and a
   significant choice as a decision record, whose status is Proposed. In a
   code task, a detail the specification leaves open is an ambiguity: stop
   and report it instead ([rules](#rules)). List each detail you settled in
   the pull request for the owner to confirm, with the alternative where
   there was one. Only the owner confirms a detail or accepts a decision
   record.
8. **Update the plan and the status lines.** Mark the task done in the plan,
   with the date, and name the next task. Then run
   `uv run scripts/site_status.py`, which updates the status page and the
   statuses that begin the site's pages
   ([STATUS-3](spec/site.md#6-status)); a test fails until it's run. Update
   the **Status** line of each document the task changed. When another open
   pull request also edits the
   plan, the later one to merge merges `origin/main` into its branch once the
   other merges, rather than rebasing
   ([commits and pull requests](#commits-and-pull-requests)): it keeps both
   tasks' entries, and names the next task again.
9. **Verify.** Run the checks CI runs, from [commands](#commands): the format
   check, the lint, the type check, the tests, the Go checks, the Go tests,
   and `uv run scripts/verify.py --since origin/main`. Check that each new
   test fails without the behavior it covers. Runs against the SEC wait for
   the owner, and only one runs at a time, across every checkout and worktree
   ([FETCH-7](spec/snapshots.md#10-fetching)). A worktree has no `.env.dev`,
   because git ignores it, so a run there takes `SEC_USER_AGENT` from the
   environment. Report only what you ran, with its outcome.
10. **Open the pull request.** Commit as
    [commits and pull requests](#commits-and-pull-requests) says. Push the
    branch, and open the pull request with `gh pr create`, under a title
    that ends with the task's ID, such as "Specify the notices (T4)". Its
    description gives:
    - the task, and the plan it's in, linked;
    - what changed;
    - the details to confirm, linked to where step 7 recorded them, or that
      there are none;
    - the checks run, with their results;
    - any tests written ahead of a later task;
    - anything deferred, and the remaining work.

    Don't merge it: an agent merges a pull request only when the owner asks
    it to merge that pull request, in the current conversation, and only
    after the owner's iterative review has ended
    ([commits and pull requests](#commits-and-pull-requests)).
11. **Report.** Finish with a short handoff: what changed, the evidence, the
    limitations, and the remaining work, with the pull request's link and
    the next task. Name what awaits the owner: the iterative review, this
    task's details to confirm, any decision record still Proposed, and any
    approval, such as a run against the SEC.
