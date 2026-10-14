"""Build the documentation site, then check it (spec/site.md, SITE-8).

    uv run scripts/build_site.py

It takes the commit built from `git rev-parse HEAD`, runs Zensical's build
in its own process with the Markdown extension `scripts/site_links.py`, writes
the commit into each page's footer, and checks the built site for SITE-1 to
SITE-7 and STATUS-3. It prints each problem, starting with the statement it
breaks, and exits 1 if there's any. The site is in `site/`, which git ignores.

Zensical runs from an environment of its own, made from `uv.lock` without
the network (`uv run --isolated --frozen --offline`), not from the project's
`.venv`. The project's environment is inside the repository's root, which is
the site's source (SITE-1), and Zensical 0.0.70 leaves its theme's own
files out of a site when its package lies inside the source
(https://github.com/zensical/zensical/issues/367).
"""

from __future__ import annotations

import os
import posixpath
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import site_status
from site_links import HELD_FILES, REPOSITORY, held

ROOT = Path(__file__).resolve().parent.parent
COMMIT_PLACEHOLDER = "__SITE_COMMIT__"
SHORT_PLACEHOLDER = "__SITE_COMMIT_SHORT__"
NOTICES = "docs/third-party-notices.md"
MERMAID = "docs/assets/mermaid.min.js"
# What the theme writes beside the documents (SITE-1).
THEME_FILES = ("404.html", "sitemap.xml", "search.json")
THEME_DIRS = ("assets/",)
TABS = ("Home", "Explanation", "Reference", "Status")
# The specifications in the order they were first written (SITE-7); one
# written later goes after them, in the order its first commit gives.
SPEC_ORDER = (
    "snapshots.md",
    "deployment.md",
    "controls.md",
    "notices.md",
    "telemetry.md",
    "site.md",
)
EDGAR = "the U.S. Securities and Exchange Commission's EDGAR system"
UNAFFILIATED = "isn't affiliated with or endorsed by the SEC"
# Link relations under which a page loads what an element names (SITE-4).
LOADING_RELS = {
    "stylesheet",
    "icon",
    "shortcut",
    "apple-touch-icon",
    "mask-icon",
    "preload",
    "modulepreload",
    "prefetch",
    "preconnect",
    "dns-prefetch",
    "manifest",
}
LOADING_ATTRS = {
    ("script", "src"),
    ("img", "src"),
    ("img", "srcset"),
    ("source", "src"),
    ("source", "srcset"),
    ("iframe", "src"),
    ("frame", "src"),
    ("embed", "src"),
    ("object", "data"),
    ("video", "src"),
    ("video", "poster"),
    ("audio", "src"),
    ("track", "src"),
    ("image", "href"),
    ("use", "href"),
    ("use", "xlink:href"),
}
CSS_URL = re.compile(r"""url\(\s*['"]?([^'")]+)|@import\s+['"]([^'"]+)""")


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True)
    return result.stdout


# --- Reading built pages ----------------------------------------------------


@dataclass
class Page:
    """What a check needs from a built HTML page."""

    ids: set[str] = field(default_factory=set)
    links: list[str] = field(default_factory=list)
    loads: list[tuple[str, str]] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)
    footer_text: str = ""
    footer_links: list[tuple[str, str]] = field(default_factory=list)
    tabs: list[str] = field(default_factory=list)


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = Page()
        self.footer_depth = 0
        self.link: tuple[str, list[str]] | None = None
        self.tab: list[str] | None = None
        self.in_style = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        for name in ("id", "name") if tag == "a" else ("id",):
            if values.get(name):
                self.page.ids.add(values[name])
        classes = values.get("class", "").split()
        if tag == "div" and (self.footer_depth or "md-copyright" in classes):
            self.footer_depth += 1
        if tag == "a" and "href" in values:
            self.page.links.append(values["href"])
            self.link = (values["href"], [])
            if "md-tabs__link" in classes:
                self.tab = []
        if tag == "link" and LOADING_RELS & set(values.get("rel", "").lower().split()):
            self.page.loads.append((tag, values.get("href", "")))
        for attribute in ("src", "srcset", "data", "poster", "href", "xlink:href"):
            if (tag, attribute) in LOADING_ATTRS and attribute in values:
                self.page.loads.append((tag, values[attribute]))
        if "style" in values:
            self.page.styles.append(values["style"])
        if tag == "style":
            self.in_style = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "div" and self.footer_depth:
            self.footer_depth -= 1
        if tag == "a" and self.link is not None:
            href, text = self.link
            if self.footer_depth:
                self.page.footer_links.append((href, " ".join("".join(text).split())))
            if self.tab is not None:
                self.page.tabs.append(" ".join("".join(self.tab).split()))
                self.tab = None
            self.link = None
        if tag == "style":
            self.in_style = False

    def handle_data(self, data: str) -> None:
        if self.footer_depth:
            self.page.footer_text += data
        if self.link is not None:
            self.link[1].append(data)
        if self.tab is not None:
            self.tab.append(data)
        if self.in_style:
            self.page.styles.append(data)


def read_page(path: Path) -> Page:
    parser = PageParser()
    parser.feed(path.read_text("utf-8"))
    parser.close()
    page = parser.page
    page.footer_text = " ".join(page.footer_text.split())
    return page


def html_pages(site: Path) -> dict[str, Page]:
    return {p.relative_to(site).as_posix(): read_page(p) for p in sorted(site.rglob("*.html"))}


# --- The repository's side --------------------------------------------------


def sources(root: Path) -> list[str]:
    """Every file the site holds, as git sees the tree: tracked, or untracked and not ignored."""
    tracked = git(root, "ls-files", "-z").split("\0")
    untracked = git(root, "ls-files", "-z", "--others", "--exclude-standard").split("\0")
    return sorted({path for path in tracked + untracked if path and held(path)})


def page_path(source: str) -> str:
    """Where the site publishes a Markdown file's page (SITE-1)."""
    stem = source[: -len(".md")]
    if stem in ("index", "README"):
        return "index.html"
    head, name = posixpath.split(stem)
    if name in ("index", "README"):
        return f"{head}/index.html"
    return f"{stem}/index.html"


def site_prefix(root: Path) -> str:
    """The path of the site's address, which links from the site's root begin with."""
    url: str = tomllib.loads((root / "zensical.toml").read_text("utf-8"))["project"]["site_url"]
    return urlsplit(url).path


def resolve_in_site(site: Path, page: str, path: str) -> str | None:
    """The built file a link on a page leads to, or None if the site lacks it.

    A path from the site's root, as the 404 page's are, begins with the path
    of the site's address.
    """
    path = unquote(path)
    if path.startswith("/"):
        prefix = site_prefix(ROOT)
        if not path.startswith(prefix):
            return None
        target = posixpath.normpath(path[len(prefix) :] or ".")
    else:
        target = posixpath.normpath(posixpath.join(posixpath.dirname(page), path))
    if target == ".." or target.startswith("../"):
        return None
    candidates = [target, posixpath.join(target, "index.html")] if target != "." else ["index.html"]
    for candidate in candidates:
        if (site / candidate).is_file():
            return candidate
    return None


def github_slugs(markdown: str) -> set[str]:
    """The anchors GitHub gives a Markdown file's headings."""
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    in_fence = False
    for line in markdown.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if in_fence:
            continue
        match = re.match(r"^#{1,6} (.+?)\s*#*$", line)
        if not match:
            continue
        text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", match.group(1))
        text = re.sub(r"[`*_]", "", text).lower()
        slug = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        slugs.add(slug if count == 0 else f"{slug}-{count}")
    return slugs


def zensical_version(root: Path) -> str | None:
    match = re.search(
        r'^name = "zensical"\nversion = "([^"]+)"', (root / "uv.lock").read_text("utf-8"), re.M
    )
    return match.group(1) if match else None


def mermaid_version(script: str) -> str | None:
    """Mermaid's own version, as its script gives it beside its getVersion function."""
    match = re.search(r'\{version:"(\d+\.\d+\.\d+)"\}[^;]{0,60}"getVersion"', script)
    return match.group(1) if match else None


# --- The checks -------------------------------------------------------------


def check_published(root: Path, site: Path, held_files: list[str]) -> list[str]:
    """SITE-1: the documents, from their files at their paths, and nothing else."""
    problems = []
    expected: dict[str, str] = {}
    for source in held_files:
        if source.endswith(".md"):
            expected[page_path(source)] = source
            continue
        expected[source] = source
        built = site / source
        if built.is_file() and built.read_bytes() != (root / source).read_bytes():
            problems.append(f"SITE-1: site/{source} differs from {source}")
    built_files = {p.relative_to(site).as_posix() for p in site.rglob("*") if p.is_file()}
    for path, source in sorted(expected.items()):
        if path not in built_files:
            problems.append(f"SITE-1: the site lacks {source}, at site/{path}")
    for path in sorted(built_files - set(expected)):
        if path in THEME_FILES or path.startswith(THEME_DIRS):
            continue
        problems.append(f"SITE-1: site/{path} is neither a document the site holds nor the theme's")
    return problems


def check_pages(held_files: list[str]) -> list[str]:
    """PAGE-1: every Markdown file the site holds is a document, or a page PAGE-1 names."""
    problems = []
    written_pages = (*HELD_FILES, "docs/status.md", "docs/glossary.md", NOTICES, "docs/export.md")
    for source in held_files:
        if not source.endswith(".md"):
            continue
        head = posixpath.dirname(source)
        document = head in ("spec", "investigation", "docs/decisions") or source in (
            "docs/design.md",
            "docs/plan.md",
        )
        written = source in written_pages or head == "docs/explanation"
        if not (document or written):
            problems.append(f"PAGE-1: {source} is neither a document nor a page PAGE-1 names")
    return problems


def check_clean(before: str, after: str, unignored: str) -> list[str]:
    """SITE-2: building changes no tracked file and adds none git doesn't ignore."""
    problems = []
    if before != after:
        problems.append(f"SITE-2: building changed what git sees, from {before!r} to {after!r}")
    if unignored:
        problems.append(f"SITE-2: git doesn't ignore what the build wrote: {unignored.split()}")
    return problems


def check_github_link(root: Path, page: str, href: str, commit: str) -> list[str]:
    """SITE-3: a link to this repository on GitHub at the commit built names what's there."""
    on_github = re.compile(rf"^{re.escape(REPOSITORY)}/(blob|tree)/{commit}/(.+)$")
    match = on_github.match(href.split("#", 1)[0])
    if not match:
        return []
    kind, target = match.group(1), unquote(match.group(2))
    file = root / target
    if not file.exists() or (kind == "tree") != file.is_dir():
        return [f"SITE-3: {page} links to {href}, which isn't in the repository"]
    fragment = urlsplit(href).fragment
    if (
        fragment
        and target.endswith(".md")
        and fragment not in github_slugs(file.read_text("utf-8"))
    ):
        return [f"SITE-3: {page} links to {href}, whose anchor {target} lacks"]
    return []


def check_links(root: Path, site: Path, commit: str, pages: dict[str, Page]) -> list[str]:
    """SITE-3: every link resolves within the site, or leads to the file on GitHub."""
    problems = []
    for path, page in pages.items():
        for href in page.links:
            parts = urlsplit(href)
            if parts.scheme or parts.netloc:
                problems += check_github_link(root, path, href, commit)
                continue
            target = resolve_in_site(site, path, parts.path) if parts.path else path
            if target is None:
                problems.append(f"SITE-3: {path} links to {href}, which the site doesn't hold")
            elif (
                parts.fragment
                and target in pages
                and unquote(parts.fragment) not in pages[target].ids
            ):
                problems.append(f"SITE-3: {path} links to {href}, whose anchor {target} lacks")
    return problems


def remote(url: str) -> bool:
    url = url.strip()
    return url.startswith("//") or bool(re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I))


def urls(value: str) -> list[str]:
    """The addresses an attribute names: one, or each of a srcset's."""
    return [part.strip().split(" ")[0] for part in value.split(",")] if "," in value else [value]


def check_hosts(site: Path, pages: dict[str, Page]) -> list[str]:
    """SITE-4: nothing loaded from another host, and the site's own Mermaid on every page."""
    problems = []
    for path, page in pages.items():
        for tag, value in page.loads:
            for url in urls(value):
                if remote(url):
                    problems.append(f"SITE-4: {path} loads {url} from another host, by a <{tag}>")
        for style in page.styles:
            for match in CSS_URL.finditer(style):
                url = match.group(1) or match.group(2)
                if remote(url):
                    problems.append(f"SITE-4: {path} loads {url} from another host, in a style")
        scripts = [
            resolve_in_site(site, path, value)
            for tag, value in page.loads
            if tag == "script" and not remote(value)
        ]
        if MERMAID not in scripts:
            problems.append(f"SITE-4: {path} doesn't load the site's copy of Mermaid, {MERMAID}")
    for stylesheet in sorted(site.rglob("*.css")):
        for match in CSS_URL.finditer(stylesheet.read_text("utf-8")):
            url = match.group(1) or match.group(2)
            if remote(url):
                name = stylesheet.relative_to(site)
                problems.append(f"SITE-4: {name} loads {url} from another host")
    bundles = sorted((site / "assets" / "javascripts").glob("bundle.*.js"))
    copy = site / MERMAID
    if not bundles or not copy.is_file():
        return [*problems, "SITE-4: the site lacks the theme's script or the copy of Mermaid"]
    wanted = re.search(r"unpkg\.com/mermaid@(\d+)", bundles[0].read_text("utf-8"))
    version = mermaid_version(copy.read_text("utf-8"))
    if wanted is None or version is None or version.split(".")[0] != wanted.group(1):
        major = wanted.group(1) if wanted else "unknown"
        problems.append(
            f"SITE-4: the copy of Mermaid is {version}, but the theme's script loads "
            f"major version {major}"
        )
    return problems


def check_footers(root: Path, site: Path, commit: str, pages: dict[str, Page]) -> list[str]:
    """SITE-5: each footer's source, disclaimer, copyright, commit, and notices link."""
    problems = []
    license_lines = (root / "LICENSE").read_text("utf-8").splitlines()
    copyright = next(line.strip() for line in license_lines if line.startswith("Copyright"))
    wanted = {
        "the copyright line, linked to LICENSE": (f"{REPOSITORY}/blob/{commit}/LICENSE", copyright),
        "the commit built": (f"{REPOSITORY}/commit/{commit}", commit[:12]),
    }
    notices = page_path(NOTICES)
    for path, page in pages.items():
        if EDGAR not in page.footer_text or UNAFFILIATED not in page.footer_text:
            problems.append(f"SITE-5: {path}'s footer lacks the data's source or the disclaimer")
        for name, link in wanted.items():
            if link not in page.footer_links:
                problems.append(f"SITE-5: {path}'s footer lacks {name}")
        targets = [
            resolve_in_site(site, path, href) for href, _ in page.footer_links if not remote(href)
        ]
        if notices not in targets:
            problems.append(f"SITE-5: {path}'s footer doesn't link to the notices page")
    for file in sorted(site.rglob("*")):
        if file.is_file() and file.suffix in (".html", ".json", ".xml"):
            text = file.read_text("utf-8")
            if COMMIT_PLACEHOLDER in text or SHORT_PLACEHOLDER in text:
                built = file.relative_to(site).as_posix()
                problems.append(f"SITE-5: site/{built} still holds the commit's placeholder")
    return problems


def check_notices(root: Path) -> list[str]:
    """SITE-6: the notices page names Zensical's version and the Mermaid copy's."""
    path = root / NOTICES
    if not path.is_file():
        return [f"SITE-6: the site has no notices page, {NOTICES}"]
    text = path.read_text("utf-8")
    problems = []
    zensical = zensical_version(root)
    if zensical is None or f"Zensical {zensical}" not in text:
        problems.append(f"SITE-6: {NOTICES} doesn't name the Zensical uv.lock pins, {zensical}")
    copy = root / MERMAID
    mermaid = mermaid_version(copy.read_text("utf-8")) if copy.is_file() else None
    if mermaid is None or f"Mermaid {mermaid}" not in text:
        problems.append(f"SITE-6: {NOTICES} doesn't name the version of Mermaid's copy, {mermaid}")
    return problems


def first_written(root: Path, path: str) -> int:
    """When a file was first committed, in seconds; a file not yet committed is last."""
    times = git(root, "log", "--diff-filter=A", "--format=%ct", "--", path).split()
    return int(times[-1]) if times else sys.maxsize


def flatten(entries: Any) -> list[str]:
    if isinstance(entries, str):
        return [entries]
    paths = []
    for entry in entries:
        if isinstance(entry, str):
            paths.append(entry)
        else:
            for value in entry.values():
                paths.extend(flatten(value))
    return paths


def expected_nav(root: Path, held_files: list[str]) -> dict[str, list[str]]:
    """Each tab's pages, in the order SITE-7 gives, of the pages that exist."""
    held_md = [s for s in held_files if s.endswith(".md")]
    explanation = sorted(s for s in held_md if s.startswith("docs/explanation/"))
    records = sorted(
        s for s in held_md if s.startswith("docs/decisions/") and s != "docs/decisions/README.md"
    )

    def spec_order(spec: str) -> tuple[int, int, str]:
        name = posixpath.basename(spec)
        known = SPEC_ORDER.index(name) if name in SPEC_ORDER else len(SPEC_ORDER)
        return known, first_written(root, spec), spec

    specs = sorted((s for s in held_md if posixpath.dirname(s) == "spec"), key=spec_order)
    export = [s for s in ("docs/export.md",) if s in held_md]
    investigation = sorted(s for s in held_md if s.startswith("investigation/"))
    glossary = [s for s in ("docs/glossary.md",) if s in held_md]
    return {
        "Home": ["index.md"],
        "Explanation": ["docs/design.md", *explanation, "docs/decisions/README.md", *records],
        "Reference": [*specs, *export, *investigation, *glossary],
        "Status": ["docs/status.md", "docs/plan.md"],
    }


def check_nav(nav: list[Any], expected: dict[str, list[str]], home: Page | None) -> list[str]:
    """SITE-7: four tabs, each with its pages in order, and every page but the notices."""
    problems = []
    tabs = [next(iter(entry)) for entry in nav if isinstance(entry, dict)]
    if tabs != list(TABS):
        return [f"SITE-7: the navigation's tabs are {tabs}, not {list(TABS)}"]
    for entry in nav:
        name, value = next(iter(entry.items()))
        paths, wanted = flatten(value), expected[name]
        if name == "Explanation" and set(paths) == set(wanted):
            # The explanation pages' order is PAGE-4's, which T42 checks.
            paths = [p for p in paths if not p.startswith("docs/explanation/")]
            wanted = [p for p in wanted if not p.startswith("docs/explanation/")]
        if paths != wanted:
            problems.append(f"SITE-7: the {name} tab holds {paths}, not {wanted}")
    if NOTICES in flatten(nav):
        problems.append(f"SITE-7: the navigation holds the notices page, {NOTICES}")
    if home is not None and home.tabs != list(TABS):
        problems.append(f"SITE-7: the built home page shows the tabs {home.tabs}")
    return problems


def read_nav(root: Path) -> list[Any]:
    config = tomllib.loads((root / "zensical.toml").read_text("utf-8"))
    nav: list[Any] = config["project"].get("nav", [])
    return nav


def check_site(root: Path, site: Path, commit: str) -> list[str]:
    """Every problem with the built site (SITE-1, SITE-3 to SITE-7, PAGE-1, STATUS-3)."""
    pages = html_pages(site)
    held_files = sources(root)
    return [
        *check_published(root, site, held_files),
        *check_pages(held_files),
        *check_links(root, site, commit, pages),
        *check_hosts(site, pages),
        *check_footers(root, site, commit, pages),
        *check_notices(root),
        *check_nav(read_nav(root), expected_nav(root, held_files), pages.get("index.html")),
        *site_status.check(root),
    ]


# --- Building ---------------------------------------------------------------


def stamp(site: Path, commit: str) -> None:
    """Write the commit built into each page's footer (SITE-5)."""
    for path in site.rglob("*.html"):
        text = path.read_text("utf-8")
        if COMMIT_PLACEHOLDER in text or SHORT_PLACEHOLDER in text:
            text = text.replace(SHORT_PLACEHOLDER, commit[:12]).replace(COMMIT_PLACEHOLDER, commit)
            path.write_text(text, "utf-8")


def build(root: Path, commit: str) -> list[str]:
    """Run Zensical's build in its own process, and stamp the commit (SITE-8)."""
    uv = shutil.which("uv")
    if uv is None:
        return ["SITE-8: building the site needs uv, which isn't on the path"]
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["SITE_COMMIT"] = commit
    env["SITE_ROOT"] = str(root)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(root / "scripts"), env.get("PYTHONPATH")])
    )
    command = [
        uv,
        "run",
        "--isolated",
        "--frozen",
        "--offline",
        "python",
        "-m",
        "zensical",
        "build",
        "--clean",
    ]
    result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        return [f"SITE-8: Zensical's build failed:\n{result.stdout}{result.stderr}"]
    stamp(root / "site", commit)
    return []


def run(root: Path) -> list[str]:
    """Build the site, and every problem with it or with the build (SITE-8)."""
    commit = git(root, "rev-parse", "HEAD").strip()
    seen = ("status", "--porcelain=v1", "--untracked-files=all")
    before = git(root, *seen)
    problems = build(root, commit)
    if not problems:
        problems = check_site(root, root / "site", commit)
    after = git(root, *seen)
    unignored = git(root, "ls-files", "--others", "--exclude-standard", "--", "site", ".cache")
    return [*problems, *check_clean(before, after, unignored)]


def main() -> int:
    problems = run(ROOT)
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print(f"Built and checked the site in {ROOT / 'site'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
