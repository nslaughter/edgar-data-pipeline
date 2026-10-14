# 12. Publish the documents as a site built with Zensical, served by GitHub Pages

**Status:** Proposed, October 14, 2026.

## Context

The repository's documents are the design, eleven decision records, five
specifications with their schemas, the investigation, and the plan: about
5,600 lines, linked to one another by relative paths and by statement and
finding IDs. They're read on GitHub, one file at a time. A reader outside
the project has no reading order but the README's links, and no navigation
across them. They also have no one place that says which parts of the
pipeline exist. Most of the design is specified and not built, and only
the plan says so, task by task. The library the pipeline reads EDGAR
through, `edgar-asof`, is documented in a repository of its own
([0009](0009-read-edgar-through-a-public-point-in-time-library.md)).

What the choice must keep:

- **The documents stay where they are.** Commits and pull requests link to
  their paths, and a specification is never moved or renamed
  ([`AGENTS.md`](../../AGENTS.md#where-documents-go)). A site that copied
  them would hold a second version that could drift.
- **The repository's tools.** Its scripts and tests are Python, run with
  `uv`, and its CI is GitHub Actions. The tests never reach the network.
- **The data's source is cited** wherever it's published: the U.S.
  Securities and Exchange Commission's EDGAR system.

## Options

- **GitHub's own rendering of the repository, with no site.** Nothing to
  build or keep up, and relative links resolve as written. GitHub draws
  Mermaid diagrams in Markdown files ("Diagram rendering is available in
  GitHub Issues, GitHub Discussions, pull requests, wikis, and Markdown
  files", GitHub's documentation on creating diagrams, read October 14,
  2026). But a reader gets no navigation across the documents beyond the
  file tree, and no reading order beyond what a page links to.
- **Zensical,** a static site generator from the team behind Material for
  MkDocs, under the MIT license. Its version 0.0.70, of October 12, 2026,
  is classified "Development Status :: 3 - Alpha" in its package's
  metadata. Its configuration follows MkDocs's, it searches the site in the
  reader's browser, and it installs as a Python package that `uv.lock` can
  pin. Tried with version 0.0.70 on October 14, 2026:
  - With the repository's root as its `docs_dir`, and every other path
    excluded through `exclude_docs`, it builds `spec/` and `docs/` at their
    own paths, so the documents' relative links hold.
  - Its `strict` mode fails the build on a missing anchor, and on a link
    to a Markdown page the site excludes, such as `AGENTS.md`, as it does
    on one to a page that doesn't exist. It doesn't fail on a link to any
    other file the site excludes, such as one under `scripts/`, and the
    built page keeps a link that leads nowhere. Its `validation` setting
    can ignore links to missing pages, while it still fails on a missing
    anchor.
  - Its theme's script loads Mermaid from `unpkg.com` when a page has a
    diagram, unless the page has already loaded Mermaid.
- **MkDocs, with Material for MkDocs.** Mature, and Zensical's
  predecessor. Material for MkDocs has been in maintenance mode since
  November 2025: "while we'll continue to fix critical bugs and security
  issues for 12 month at least, no new features will be added" (its
  announcement of November 11, 2025). MkDocs's latest release on PyPI is
  1.6.1, of August 30, 2024 (read October 14, 2026).
- **Sphinx, with MyST for Markdown.** Mature, and in Python. Its heading
  anchors must be configured to match GitHub's, so that the documents'
  links resolve on both, and its configuration and themes differ from the
  MkDocs family's.
- **A generator outside Python,** such as Docusaurus or Starlight on
  Node.js, or Hugo. Each brings a toolchain that the documents don't
  otherwise need.

Where it's served:

- **GitHub Pages, deployed by a GitHub Actions workflow,** at the address
  GitHub gives a public repository's site,
  `https://nslaughter.github.io/edgar-data-pipeline/`. The repository is
  public, the workflow runs where CI already does, and deploying needs no
  account or token outside GitHub.
- **Another static host,** such as Netlify or Cloudflare Pages. Each needs
  an account and a token outside GitHub, for nothing this site needs.

## Decision

The documents are published as a site built with Zensical, from the files
where they are, with the repository's root as its `docs_dir`, and served
by GitHub Pages at `https://nslaughter.github.io/edgar-data-pipeline/`,
deployed from `main` by a GitHub Actions workflow. The owner chose Zensical
on October 14, 2026, and the address GitHub Pages gives, with no custom
domain, the same day. [`spec/site.md`](../../spec/site.md) governs the
site.

## Consequences

- **The documents stay the only copies.** The site adds pages of its own:
  a home page, explanation pages, a status page, a glossary, and a notices
  page ([PAGE-1](../../spec/site.md#7-the-pages-written-for-the-site)).
  Each is a document in the repository, read on GitHub too.
- **Zensical is still alpha,** so its version is locked in `uv.lock`, and
  an upgrade is a change of its own, checked by building the site.
- **The site needs checks of its own,** since `strict` mode can't tell a
  file the site excludes from a missing one: the tests check every built
  link, and that nothing loads from another host. A link to a file the
  site doesn't hold leads to it on GitHub
  ([SITE-3](../../spec/site.md#4-what-the-site-holds),
  [SITE-8](../../spec/site.md#5-building-and-publishing)). The site serves
  its own copy of Mermaid
  ([SITE-4](../../spec/site.md#4-what-the-site-holds)).
- **No new required check.** The tests build and check the site, so CI's
  existing jobs fail on a site that doesn't build, and the ruleset on
  `main` doesn't change. Only the publishing workflow is new, and it runs
  after a merge
  ([SITE-9, SITE-10](../../spec/site.md#5-building-and-publishing)).
- **The owner sets the repository's Pages source to GitHub Actions** before
  the first deployment.
- **The site distributes its theme's files and Mermaid's,** so it carries
  their licenses' notices
  ([SITE-6](../../spec/site.md#4-what-the-site-holds)).
- **`edgar-asof`'s documents stay in its repository.** The site explains
  the library as a part of the pipeline, and links to its documents at a
  named commit, rather than publishing them
  ([PAGE-2](../../spec/site.md#7-the-pages-written-for-the-site)).

## Sources

- [Zensical](https://zensical.org/), and its package on
  [PyPI](https://pypi.org/project/zensical/), version 0.0.70
- [Material for MkDocs, "Insiders – Now free for everyone", November 11, 2025](https://squidfunk.github.io/mkdocs-material/blog/2025/11/11/insiders-now-free-for-everyone/)
- [MkDocs on PyPI](https://pypi.org/project/mkdocs/)
- [GitHub, Creating diagrams](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/creating-diagrams)
- [GitHub Pages, using a custom workflow](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
