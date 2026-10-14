"""A Markdown extension that leads links to files the site doesn't hold to GitHub.

The documentation site publishes the documents where they are
(spec/site.md, SITE-1), so a relative link between two documents is a link
between their pages. A link to any other file in the repository, such as
`scripts/snapshot.py` or `AGENTS.md`, leads to that file on GitHub at the
commit built (SITE-3), and this extension rewrites it so while Zensical
renders each page, before Zensical rewrites the relative links that remain
(SITE-8). `scripts/build_site.py` runs the build with this directory on
Python's path and the commit in `SITE_COMMIT`; without them, the build fails.
"""

from __future__ import annotations

import os
import posixpath
import xml.etree.ElementTree as etree
from pathlib import Path
from urllib.parse import urlsplit

from markdown import Extension, Markdown
from markdown.treeprocessors import Treeprocessor
from zensical.extensions.context import ContextPreprocessor

REPOSITORY = "https://github.com/nslaughter/edgar-data-pipeline"
# What the site holds: the home page, and everything under these directories
# (SITE-1). Everything else in the repository is linked on GitHub.
HELD_FILES = ("index.md",)
HELD_DIRS = ("spec", "docs", "investigation")


def held(path: str) -> bool:
    """Whether the site holds a file, given by its path from the repository's root."""
    return path in HELD_FILES or path.split("/", 1)[0] in HELD_DIRS


def has_page(directory: Path) -> bool:
    """Whether the site has a page for a directory: its index.md or README.md (SITE-1)."""
    return (directory / "index.md").is_file() or (directory / "README.md").is_file()


def github_url(commit: str, path: str, is_dir: bool, fragment: str) -> str:
    """The address of a file in the repository on GitHub, at a commit (SITE-3)."""
    kind = "tree" if is_dir else "blob"
    url = f"{REPOSITORY}/{kind}/{commit}/{path}"
    return f"{url}#{fragment}" if fragment else url


def resolve(page: str, href: str) -> str | None:
    """The repository path a relative link on a page names, or None if it names none.

    `page` is the page's source path relative to the repository's root. A
    link with a scheme or a host, a link to an anchor alone, and a link that
    leaves the repository name no file in it.
    """
    parts = urlsplit(href)
    if parts.scheme or parts.netloc or not parts.path or parts.path.startswith("/"):
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(page), parts.path))
    if target == ".." or target.startswith("../"):
        return None
    return "" if target == "." else target


class SiteLinks(Treeprocessor):
    def __init__(self, md: Markdown, root: Path, commit: str) -> None:
        super().__init__(md)
        self.root = root
        self.commit = commit

    def run(self, root: etree.Element) -> None:
        context = ContextPreprocessor.from_markdown(self.md)
        if context is None:
            raise RuntimeError("site_links runs only within Zensical's build")
        page = context.page.path
        for element in root.iter("a"):
            href = element.get("href")
            if href is None:
                continue
            target = resolve(page, href)
            if target is None or target == "":
                continue
            path = self.root / target
            if not path.exists():
                # A link to nothing is left as written, for build_site.py to
                # report (SITE-3).
                continue
            # The site holds the files under its directories, and a directory
            # only when it has a page; any other directory, such as
            # spec/schemas/, is linked on GitHub.
            if held(target) and (not path.is_dir() or has_page(path)):
                continue
            fragment = urlsplit(href).fragment
            element.set("href", github_url(self.commit, target, path.is_dir(), fragment))


class SiteLinksExtension(Extension):
    def extendMarkdown(self, md: Markdown) -> None:
        commit = os.environ.get("SITE_COMMIT")
        root = os.environ.get("SITE_ROOT")
        if not commit or not root:
            raise RuntimeError(
                "site_links needs SITE_COMMIT and SITE_ROOT: build the site with "
                "`uv run scripts/build_site.py`"
            )
        # After `inline` (20), which makes the links, and before Zensical's
        # own links processor (0), which rewrites the relative links left.
        md.treeprocessors.register(SiteLinks(md, Path(root), commit), "site_links", 5)


def makeExtension(**kwargs: object) -> SiteLinksExtension:
    return SiteLinksExtension(**kwargs)
