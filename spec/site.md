# Specification: the documentation site

**Status:** Version 0.1.0, draft, October 14, 2026. This document governs
the site that publishes this repository's documents: which documents it
holds and at which paths, the pages written for it, how each page says which
parts of the pipeline are built and which are only specified, decided, or
planned, the status page and how it's kept current, the estimates, and how
the site is built, checked, and published, with what it loads and what it
cites. Where the site and this document disagree, the site is wrong or this
document needs a new version; say which before changing either.

It rests on the decision to publish the documents as a site built with
Zensical, from the documents where they are, and served by GitHub Pages
([0012]). The site lets a reader outside the project follow the pipeline's
design: what it keeps and why, how its parts fit and fail, the estimates
behind it, how far it scales, and the alternatives weighed, with the
library the pipeline reads EDGAR through, `edgar-asof` ([0009]). It also
shows plainly which parts exist. The pipeline reads filings from the U.S.
Securities and Exchange Commission's EDGAR system; nothing here depends on
how EDGAR behaves.

## 1. Guarantees

Each guarantee holds because of the statements it lists ([§3](#3-statement-ids)).

1. **The site publishes the repository's documents where they are, and
   holds no copy of them.** A document's page is built from its file at the
   commit the site is built from, and every page is in the site's
   navigation. [SITE-1], [SITE-2], [SITE-7].
2. **Every link on the site works.** A link to a page or a file the site
   holds resolves within it, with its anchor, and a link to any other file
   in the repository leads to that file on GitHub, at the same commit.
   [SITE-3].
3. **The site says plainly what's built and what isn't.** Each part of the
   pipeline has one status, derived from the plan, the specifications, and
   the decision records, and every page that describes a part gives it. A
   test fails when a status is out of date. [STATUS-1], [STATUS-2],
   [STATUS-3], [STATUS-4].
4. **The pages written for the site explain; they make no rules and own no
   facts.** Each fact they give cites the document that owns it, and they
   claim nothing measured or run that no record shows. [PAGE-2], [PAGE-3].
5. **The explanation covers the design from the problem to its limits,**
   `edgar-asof` included, with estimates that show their inputs and their
   arithmetic, and analysis beyond the plan marked as such. [PAGE-4],
   [PAGE-5], [PAGE-6], [PAGE-7], [PAGE-8].
6. **The site loads nothing from another host, cites EDGAR as the data's
   source on every page, and carries the notices of the files it serves.**
   [SITE-4], [SITE-5], [SITE-6].
7. **The site is built and checked before each merge, and published only
   from `main`.** [SITE-8], [SITE-9], [SITE-10].

## 2. Terms

- **The site:** what Zensical builds from this repository, and GitHub
  Pages serves at `https://nslaughter.github.io/edgar-data-pipeline/`
  ([0012]).
- **The built site:** the files `zensical build` writes into `site/`,
  before they're published. **The commit built** is the commit they're
  built from.
- **The pages written for the site:** the home page, the explanation pages,
  the status page, the glossary, and the notices page ([PAGE-1]). Each is a
  document like any other in `docs/`, read on GitHub too.
- **A part of the pipeline:** a component, store, control, or capability
  that the status page lists ([STATUS-2]), such as the fetcher, the
  snapshot store in git, or the alerts.
- **Built**, **Specified**, **Decided**, and **Planned:** a part's status,
  as [STATUS-1] gives them.
- **Analysis:** what a page says about something no task, decision record,
  or specification covers, such as a scope wider than the plan's
  ([PAGE-7]).
- **Task**, **done:** a task in the [plan](../docs/plan.md) and whether its
  row's Done column is filled. A decision record is **Accepted** when
  [`docs/decisions/README.md`](../docs/decisions/README.md) gives its status
  as Accepted, alone or with a note such as "superseded in part".
- **`edgar-asof`:** the public library, in a repository of its own,
  `nslaughter/edgar-asof`, that the pipeline's Python services read EDGAR's
  content through, and whose specification defines the fact model and what
  "as of T" returns ([0009]). It's built for this pipeline, so the site
  explains it as a part of the pipeline, though its documents and its plan
  are in its own repository.

## 3. Statement IDs

Every normative statement in this document has an ID, such as `SITE-3`: an
area and a number. The areas `SITE`, `STATUS`, and `PAGE` belong to this
document. An ID is assigned once and never reused or renumbered. A
statement that is dropped stays in the [index](#8-index), marked retired.

Each statement is one of two kinds:

- An **invariant** is a property of the documents as committed, or of the
  built site. Tests check it by building the site and reading the built
  pages or the documents, offline: building the site reaches no network.
- A **rule** is how the tests, the publishing workflow, or whoever writes a
  page must behave. Tests check the tests' rules. A reviewer of the pull
  request checks the others, as the index's `review` says.

The index says how each statement is checked: by `tests`, by `review`, or
by both. Every statement checked by `tests` has at least one test marked
with its ID, as `@pytest.mark.spec("SITE-3")`. A test,
`tests/test_spec_coverage.py`, enforces it, except for a statement whose
tests a later task in the [plan](../docs/plan.md) writes. It lists such a
statement as awaiting that task. Of this version's statements, those that
tests check await the tasks that build the site: T40, which builds it with
its checks, the home page, the status page, and the notices page; and T41,
which writes the explanation pages, for [PAGE-4]. The others are checked by
review, first in T41 and T42, which write the pages they govern.

## 4. What the site holds

The site is built from the repository's root, with everything outside
these paths excluded. Each document is its own page, at a path that follows
its file's, so a relative link between two documents is a link between
their pages, as it is on GitHub.

- **SITE-1** (invariant) The site's source is the repository's root, as
  Zensical's `docs_dir`. The site publishes `index.md` at the root and every
  Markdown file under `spec/`, `docs/`, and `investigation/`, each built
  from the file itself, at the path that follows the file's: `spec/notices.md`
  at `spec/notices/`, and `docs/decisions/README.md` at `docs/decisions/`.
  The other files under those directories, such as the schemas in
  `spec/schemas/`, are served as they're committed. The site publishes
  nothing else from the repository: nothing under `data/`, `scripts/`,
  `tests/`, `deploy/`, or `.github/`, and not `README.md`, `AGENTS.md`,
  `CLAUDE.md`, or `LICENSE`.
- **SITE-2** (invariant) Building the site changes no file that git
  tracks, and adds none that git doesn't ignore. It writes the built site
  under `site/`, which git ignores. Zensical also writes its cache on every
  build, in `.cache/` beside `zensical.toml`, with a `.gitignore` of its own
  that makes git ignore it ([0012]). No document is copied, moved, or
  generated into another path to build it.
- **SITE-3** (invariant) Every link on a built page resolves:
  1. A link to a page or a file the site holds leads to it within the site,
     and its anchor, if it has one, names an element of that page.
  2. A link to a file in the repository that the site doesn't hold, such
     as `scripts/snapshot.py` or `AGENTS.md`, leads to it on GitHub at the
     commit built, as
     `https://github.com/nslaughter/edgar-data-pipeline/blob/COMMIT/PATH`,
     with its anchor, or `tree/` in place of `blob/` for a directory.
  3. A link to another host is left as written, and isn't checked, since
     the check reaches no network.
- **SITE-4** (invariant) Opening a built page loads nothing from another
  host: no script, stylesheet, font, image, icon, or frame. Pages use the
  reader's system fonts. A diagram is written in Mermaid in its page, as
  GitHub draws it too, and the site draws it with a copy of Mermaid the
  site serves itself:
  1. The copy is `docs/assets/mermaid.min.js`, the script of a release of
     Mermaid, committed as Mermaid publishes it. Every page loads it through
     `zensical.toml`'s `extra_javascript`, so the theme's script finds
     Mermaid loaded, and loads none of its own.
  2. Its major version is the one the theme's script names in the address
     it would load Mermaid from otherwise: 11, for Zensical 0.0.70
     ([0012]).
  3. It's replaced only in a change of its own, which names its new
     version on the notices page ([SITE-6]).
- **SITE-5** (invariant) Every built page's footer:
  1. says that the data the pipeline keeps comes from the U.S. Securities
     and Exchange Commission's EDGAR system, and that the project isn't
     affiliated with or endorsed by the SEC;
  2. gives the copyright line as `LICENSE` gives it, linked to `LICENSE`
     on GitHub;
  3. names the commit built, by its first 12 characters, linked to the
     commit on GitHub;
  4. links to the notices page ([SITE-6]).
- **SITE-6** (invariant) The notices page, `docs/third-party-notices.md`,
  gives the license of each file from another project that the site serves,
  such as the theme's scripts, stylesheets, and icons, and Mermaid's script,
  with the notice each license requires. It names Zensical's version and
  the version of Mermaid's copy ([SITE-4]), and a change to either checks
  the files the site serves again.
- **SITE-7** (invariant) The site's navigation is four tabs, in this order:
  1. **Home:** the home page.
  2. **Explanation:** the design, [`docs/design.md`](../docs/design.md),
     first; then the explanation pages, in the order [PAGE-4] gives; then
     the decision records, after their index.
  3. **Reference:** the specifications, in the order they were first
     written, this one last; the investigation; and the glossary.
  4. **Status:** the status page, then the plan.

  Every page the site publishes is in the navigation, except the notices
  page, which every footer links to. Tutorials and how-to guides, once
  there's something to run, are tabs of their own between Home and
  Explanation, in a later version.

The theme's colors, logo, and favicon are the owner's to choose. Until the
owner does, the site uses the theme's defaults.

## 5. Building and publishing

- **SITE-8** (invariant) Zensical is a development dependency, its version
  locked in `uv.lock`. Its configuration is `zensical.toml`, at the
  repository's root, with `strict` on, so a missing anchor fails the build.
  Zensical can't tell a page the site excludes, such as `AGENTS.md`, from
  one that doesn't exist, so its validation ignores links to pages it
  doesn't hold, and the check of the built site resolves each one
  ([SITE-3]). The site is built with `uv run zensical build --clean`, and
  then checked.
- **SITE-9** (rule) The tests build the site, and check the built site for
  [SITE-1] to [SITE-7] and [STATUS-3], offline. So each of CI's jobs, which
  run the tests, fails on a site that doesn't build or doesn't pass, and
  the site is checked before each merge without a job of its own.
- **SITE-10** (rule) The workflow `.github/workflows/pages.yml` alone
  publishes the site. It runs on each push to `main`, and when the owner
  starts it. It builds the site from that commit, checks it as [SITE-9]
  does, and deploys it with GitHub Pages' own actions, to the site's
  address ([§2](#2-terms)). Its actions are pinned by commit, each with its
  release in a comment. It has only the permissions deploying needs:
  `contents: read`, `pages: write`, and `id-token: write`. One deployment
  runs at a time, and a newer one waits rather than cancel the one in
  progress.

The owner sets the repository's Pages source to GitHub Actions before the
first deployment ([0012]).

## 6. Status

A part's status is derived, never written by hand, so that it can't say
more than the plan, the specifications, and the decision records do.

- **STATUS-1** (invariant) A part's status is the furthest of these it has
  reached:
  1. **Built:** it has at least one task that builds it, and each is done.
  2. **Specified:** it has at least one task that specifies it, and each is
     done, so specifications on `main` govern it, at the versions their
     Status lines give.
  3. **Decided:** it has at least one decision record that chooses it, and
     each is Accepted.
  4. **Planned:** none of the above.

  When some, but not all, of the next stage's tasks are done, the status
  says so after it, as "Decided; specified in part".

  `edgar-asof`'s tasks are in its own repository, which the site's checks
  can't read. So its status is Built once `uv.lock` pins a release of it,
  and until then the furthest of Decided and Planned it has reached by its
  decision records.
- **STATUS-2** (invariant) The status page, `docs/status.md`, has a table
  of the pipeline's parts: each component the design's
  [table](../docs/design.md#components) names, with monitoring as three
  parts, its metrics, alerts, and dashboards, its traces, and its logs;
  `edgar-asof`; the snapshot store in git and in the cluster; the controls;
  the charts; the drills; and the documentation site. Each row names its
  part, the decision records that choose it, the specifications that
  govern it, and the tasks that specify it and that build it.
  `edgar-asof`'s row names instead each release of it that a task in the
  plan waits on, with those tasks, and the version `uv.lock` pins, if any.
  Every task in the plan outside its Investigation section is named by at
  least one row, and every task and decision record a row names exists. The
  investigation's tasks are in a table of their own, with the questions
  each answers.
- **STATUS-3** (invariant) The status page's generated columns give each
  part's status ([STATUS-1]), each of its tasks with whether it's done, and
  each governing specification's version, as the plan, the specifications'
  Status lines, and the decision records' index give them at the same
  commit. A script rewrites them in place, between markers. A test fails
  when they differ from what the script would write, so a pull request
  that marks a task done also updates the status page.
- **STATUS-4** (invariant) The home page, and each explanation page that
  describes a part, begins with the status of each part it describes, as
  the status page gives it, linked to the status page. The same script
  writes these, and the same test checks them.

## 7. The pages written for the site

- **PAGE-1** (invariant) The pages written for the site are:
  1. the home page, `index.md` at the repository's root: what the pipeline
     is and what it keeps, the parts' statuses ([STATUS-4]), how the site
     is arranged, and that the data comes from the U.S. Securities and
     Exchange Commission's EDGAR system;
  2. the explanation pages, under `docs/explanation/` ([PAGE-4]);
  3. the status page, `docs/status.md` ([STATUS-2]);
  4. the glossary, `docs/glossary.md` ([PAGE-8]);
  5. the notices page, `docs/third-party-notices.md` ([SITE-6]).
- **PAGE-2** (rule) The pages written for the site make no rules and own no
  facts. Each fact about the pipeline cites the document that owns it: a
  statement by its ID, a decision record, a section of the design, or a
  task in the plan. Each fact about EDGAR cites its finding's `SRC` ID.
  Each fact about `edgar-asof` cites the document in its repository that
  owns it, by a link at a named commit, and the page gives the date its
  facts about `edgar-asof` were last checked against that repository's
  `main`. Where a page and the document it cites disagree, the document
  governs, and the page is the one to fix. A pull request that changes a
  document in this repository so that a page becomes wrong fixes the page
  too.
- **PAGE-3** (rule) The explanation pages describe the pipeline as it's
  designed, in the present tense, as the design does, and their statuses
  ([STATUS-4]) say which parts are built. They claim nothing observed,
  measured, or run, such as a lag, a size on the cluster, a test on kind,
  or a drill's outcome, unless a finding, or a record that the pipeline or
  CI made, shows it, and they cite it.
- **PAGE-4** (invariant) The explanation pages cover these topics, in this
  order, each on a page of its own, except where one says otherwise:
  1. **The problem:** what EDGAR publishes, what its feeds don't keep, what
     the pipeline keeps, and what it doesn't claim.
  2. **Requirements:** what the pipeline must do; the qualities it must
     have, such as durability, reproducibility, and how soon it sees a
     filing; and the limits the SEC sets; each with the statement, decision
     record, or finding behind it; and what's out of scope.
  3. **Estimates** ([PAGE-5]).
  4. **The architecture:** the components, the boundaries between them,
     and one filing from end to end, with diagrams.
  5. **The data model and its times:** the records, objects, filings,
     polls, coverage, gaps, and facts; the times each keeps; and what an
     "as of" query returns.
  6. **The interfaces:** the custom resources, the messages between the
     services with their schemas, and the query API.
  7. **The components:** for each component the design's table names, on a
     page of its own, what it does, what state it keeps and where, how many
     replicas it runs and why, how it scales, how it fails, and what it
     depends on, `edgar-asof` included where it uses it.
  8. **`edgar-asof`:** what the library is, and why it's a library in a
     repository of its own ([0009]); what its specifications define for the
     pipeline, which are the fact model and what "as of T" returns, raw
     acceptance times and the interpreters that make them instants, and its
     cache, which follows the snapshot store, and where they depart from
     the pipeline's; what it leaves to the pipeline; how the pipeline's
     services use it, reading bodies from the store and never fetching in
     the cluster; how the pipeline and the library come to answer "as of T"
     the same way; and its releases, each with what it holds and the tasks
     in the plan that wait on it, and its state, from its own plan and
     specifications ([PAGE-2]).
  9. **Delivery and consistency:** what each queue and stream guarantees,
     why a message delivered twice does no harm, which writes are
     transactions, and what a reader of the record can rely on while it's
     written.
  10. **Failures and recovery:** for each failure the design and the
      specifications consider, such as losing a pod, a node, the database's
      primary, the queue, or the object store, a `403` from the SEC, or an
      outage of the SEC or of the pipeline: what stops, what's kept, how
      the pipeline recovers, and what the record shows afterward.
  11. **Security and access:** each credential and where it's kept, the
      user agent, who may change what, and what each service may read and
      write.
  12. **Observability:** the metrics, the alerts, the dashboards' views,
      and the traces, and the question each answers.
  13. **Deployment:** the charts, their order, the kind cluster, and the
      pins, `edgar-asof`'s among them.
  14. **Testing and verification:** the unit tests, the fake EDGAR, the
      tests on kind, the drills, the rebuild of the facts from stored
      bodies, and the cases `edgar-asof` publishes for "as of T".
  15. **Scale and limits** ([PAGE-6]).
  16. **Tradeoffs:** for each decision record, the choice, the alternatives
      it weighed, and what would change it.
  17. **Open questions and risks:** what waits on the investigation and on
      `edgar-asof`'s releases, the assumptions not yet shown, such as that
      a filing's archived files never change (Q1), and the risks to the
      design.
- **PAGE-5** (rule) The estimates page gives estimates at the scope and
  cadences the chart sets by default, and at the wider scopes [PAGE-6]
  considers:
  1. requests a second, against the fetcher's limit;
  2. bytes fetched a day;
  3. the growth, a day and a year, of the objects in Garage; of the
     records, filings, polls, and facts in PostgreSQL, with its indexes,
     its write-ahead log, and its backups; of the queue's streams; of the
     export; and of Prometheus's series and storage;
  4. the CPU, memory, and storage each component needs, and the kind
     cluster's nodes that hold them.

  Each estimate shows its inputs and its arithmetic. Each input cites a
  finding, a statement, or a decision record, or is marked an assumption,
  with its reason and the measurement that would replace it. Where an input
  is uncertain, the estimate is a range. Once the record or a run on kind
  measures a quantity, the page gives the measurement beside the estimate,
  and cites it.
- **PAGE-6** (rule) The page on scale and limits gives, from the estimates,
  the resource that limits the pipeline first as its scope grows, such as
  with more companies, more feeds, shorter cadences, or all of EDGAR's
  filers, and the point at which it does. What the pipeline would change to
  go past each limit is analysis ([PAGE-7]).
- **PAGE-7** (rule) Analysis is set apart in a block titled "Analysis",
  which says that no task, decision record, or specification covers it.
  Analysis never states what the pipeline does, only what it could do.
- **PAGE-8** (rule) The glossary defines each term the explanation pages
  use that a reader outside the project may not know: EDGAR's, such as
  accession number and acceptance time; the pipeline's, such as record,
  poll, gap, and sighting; `edgar-asof`'s, such as raw time, instant, and
  interpreter; and the platform's, such as Lease and custom resource. Each
  links to the statement or finding that defines it, where one does.

## 8. Index

| ID | Kind | Checked by | Statement |
| --- | --- | --- | --- |
| SITE-1 | invariant | tests | The site publishes `index.md` and the Markdown under `spec/`, `docs/`, and `investigation/`, from the files themselves, at their paths, and nothing else from the repository. |
| SITE-2 | invariant | tests | Building the site changes no file git tracks, and writes only files git ignores: the built site under `site/`, and Zensical's cache in `.cache/`. |
| SITE-3 | invariant | tests | Every link on a built page resolves within the site, or leads to the file on GitHub at the commit built. |
| SITE-4 | invariant | tests | A built page loads nothing from another host, and the site draws diagrams with its own copy of Mermaid, `docs/assets/mermaid.min.js`, of the major version the theme expects. |
| SITE-5 | invariant | tests | Every footer cites EDGAR, disclaims affiliation with the SEC, gives the copyright line, names the commit built, and links to the notices page. |
| SITE-6 | invariant | tests, review | The notices page gives the license and notice of each file from another project the site serves, and names Zensical's version and Mermaid's. |
| SITE-7 | invariant | tests | The navigation is Home, Explanation, Reference, and Status, holding every page but the notices page. |
| SITE-8 | invariant | tests | Zensical is locked in `uv.lock`, configured in `zensical.toml` with `strict` on. |
| SITE-9 | rule | tests | The tests build the site and check it, offline. |
| SITE-10 | rule | review | Only `pages.yml` publishes the site, from `main`, after the same checks, with pinned actions and the least permissions. |
| STATUS-1 | invariant | tests | A part's status is the furthest of Built, Specified, Decided, and Planned it has reached. |
| STATUS-2 | invariant | tests | The status page lists the parts, `edgar-asof` among them, with their decision records and tasks, and names every task outside the investigation. |
| STATUS-3 | invariant | tests | The status page's generated columns match the plan, the specifications, and the decision records at the same commit. |
| STATUS-4 | invariant | tests | The home page and each explanation page begin with the statuses of the parts they describe. |
| PAGE-1 | invariant | tests | The pages written for the site are the home page, the explanation pages, the status page, the glossary, and the notices page. |
| PAGE-2 | rule | review | The pages written for the site make no rules and cite the document that owns each fact, `edgar-asof`'s at a named commit. |
| PAGE-3 | rule | review | The explanation pages claim nothing measured or run that no finding or record shows. |
| PAGE-4 | invariant | tests, review | The explanation pages cover the seventeen topics, in order, `edgar-asof` among them. |
| PAGE-5 | rule | review | The estimates show their inputs and arithmetic, cite or mark each input, and give ranges where it's uncertain. |
| PAGE-6 | rule | review | The page on scale and limits gives the first limit as the scope grows, and where. |
| PAGE-7 | rule | review | Analysis is set apart in a block titled "Analysis". |
| PAGE-8 | rule | review | The glossary defines the terms a reader outside the project may not know, linked to their definitions. |

## 9. Open questions

None is open. The owner decided on October 14, 2026 that the site keeps no
version per release of the pipeline: it shows `main`, and each page names
the commit built ([SITE-5]). That's revisited when the pipeline has
releases.

The owner also decided on October 14, 2026:

- that the site is built before the pipeline, with Zensical, and served by
  GitHub Pages at the address it gives this repository, with no custom
  domain ([0012]);
- that each part's status is derived from the plan, the specifications,
  and the decision records, and generated, never written by hand
  ([STATUS-1] to [STATUS-4]);
- that the explanation includes analysis of scopes wider than the plan's,
  and estimates of how storage grows and of the compute the pipeline needs
  ([PAGE-5] to [PAGE-7]);
- that `edgar-asof`, though it's in a repository of its own, is
  explained here as a part of the pipeline, at the depth the pipeline
  relies on it ([PAGE-4], [STATUS-2]);
- and, confirming the details this version settled: what the site
  publishes, with `README.md`, `AGENTS.md`, `CLAUDE.md`, and `LICENSE` off
  it ([SITE-1]); the home page at `index.md` in the repository's root, and
  the other pages in `docs/` ([PAGE-1]); a committed status page whose
  generated columns a test checks ([STATUS-2], [STATUS-3]); no CI job of
  its own, since the tests build and check the site ([SITE-9]);
  `pages.yml`'s actions pinned by commit ([SITE-10]); nothing loaded from
  another host, with the site's own copy of Mermaid, and the footer
  ([SITE-4], [SITE-5]); the navigation, with the decision records under
  Explanation ([SITE-7]); and the seventeen topics, each on a page of its
  own ([PAGE-4]).

## 10. Changes to this document

Versions follow semantic versioning. Adding a statement, a page, a topic, a
part, or a check is a minor version. Changing the meaning of a statement,
removing a page or a topic, changing a status's meaning, or changing the
site's address or the paths its pages are published at is a major version,
since links to the site rely on them. IDs are never reused, and a retired
statement stays in the index.

[0009]: ../docs/decisions/0009-read-edgar-through-a-public-point-in-time-library.md
[0012]: ../docs/decisions/0012-publish-the-documents-as-a-site-built-with-zensical.md
[SITE-1]: #4-what-the-site-holds
[SITE-2]: #4-what-the-site-holds
[SITE-3]: #4-what-the-site-holds
[SITE-4]: #4-what-the-site-holds
[SITE-5]: #4-what-the-site-holds
[SITE-6]: #4-what-the-site-holds
[SITE-7]: #4-what-the-site-holds
[SITE-8]: #5-building-and-publishing
[SITE-9]: #5-building-and-publishing
[SITE-10]: #5-building-and-publishing
[STATUS-1]: #6-status
[STATUS-2]: #6-status
[STATUS-3]: #6-status
[STATUS-4]: #6-status
[PAGE-1]: #7-the-pages-written-for-the-site
[PAGE-2]: #7-the-pages-written-for-the-site
[PAGE-3]: #7-the-pages-written-for-the-site
[PAGE-4]: #7-the-pages-written-for-the-site
[PAGE-5]: #7-the-pages-written-for-the-site
[PAGE-6]: #7-the-pages-written-for-the-site
[PAGE-7]: #7-the-pages-written-for-the-site
[PAGE-8]: #7-the-pages-written-for-the-site
