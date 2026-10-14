"""The documentation site against spec/site.md.

The tests build the site once, with scripts/build_site.py, and check the
built site as it does (SITE-9). Each check also runs on a small site, or a
copy of the sources, broken on purpose, so that each can be seen to fail.
Building reaches no network: Zensical runs from an environment uv makes from
its cache, offline. Each test carries the IDs of the statements it checks,
as @pytest.mark.spec(...).
"""

from __future__ import annotations

import shutil
import subprocess
import tomllib
from pathlib import Path

import build_site
import pytest
import site_status
from markdown import Markdown
from site_links import REPOSITORY, SiteLinksExtension, github_url, has_page, held, resolve
from zensical.config import parse_zensical_config
from zensical.markdown.render import render

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
FAKE_COMMIT = "c0ffee" + "0" * 34


@pytest.fixture(scope="session")
def built() -> list[str]:
    """Build the site, as `uv run scripts/build_site.py` does, and its problems."""
    return build_site.run(ROOT)


@pytest.fixture(scope="session")
def commit() -> str:
    return build_site.git(ROOT, "rev-parse", "HEAD").strip()


@pytest.fixture(scope="session")
def pages(built: list[str]) -> dict[str, build_site.Page]:
    return build_site.html_pages(SITE)


def write(base: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, "utf-8")
    return base


def footer(
    commit: str, notices: str, copyright: str = "Copyright (c) 2026 Nathan Slaughter"
) -> str:
    return (
        '<div class="md-copyright"><p>The data the pipeline keeps comes from the U.S. '
        "Securities and Exchange Commission's EDGAR system. This project isn't affiliated "
        "with or endorsed by the SEC.</p>"
        f'<a href="{REPOSITORY}/blob/{commit}/LICENSE">{copyright}</a>'
        f'<a href="{REPOSITORY}/commit/{commit}"><code>{commit[:12]}</code></a>'
        f'<a href="{notices}">notices</a></div>'
    )


# --- Building ---------------------------------------------------------------


@pytest.mark.spec("SITE-9", "SITE-8")
def test_the_tests_build_the_site_and_it_passes_every_check(built: list[str]) -> None:
    assert built == []
    assert (SITE / "index.html").is_file()


@pytest.mark.spec("SITE-8")
def test_zensical_is_locked_and_configured_strictly() -> None:
    config = tomllib.loads((ROOT / "zensical.toml").read_text("utf-8"))["project"]

    assert build_site.zensical_version(ROOT) is not None
    assert config["docs_dir"] == "."
    assert config["site_dir"] == "site"
    assert config["strict"] is True
    assert config["validation"] == {"not_found": "ignore"}
    assert "site_links" in config["markdown_extensions"]
    assert config["theme"]["custom_dir"] == "overrides"


@pytest.mark.spec("SITE-8")
def test_the_extension_refuses_to_run_without_the_build_scripts_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SITE_COMMIT", raising=False)
    monkeypatch.delenv("SITE_ROOT", raising=False)

    with pytest.raises(RuntimeError, match=r"build_site\.py"):
        Markdown(extensions=[SiteLinksExtension()])


@pytest.mark.spec("SITE-2")
def test_building_writes_only_what_git_ignores(built: list[str]) -> None:
    assert [problem for problem in built if problem.startswith("SITE-2")] == []
    for path in ("site/index.html", ".cache/.gitignore"):
        assert subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0


@pytest.mark.spec("SITE-2")
def test_a_build_that_changes_what_git_sees_is_reported() -> None:
    assert build_site.check_clean("", "", "") == []
    assert len(build_site.check_clean(" M docs/plan.md\n", "", "")) == 1
    assert len(build_site.check_clean("", "", "site/index.html\n")) == 1


# --- What the site holds ----------------------------------------------------


@pytest.mark.spec("SITE-1")
def test_the_site_publishes_the_documents_where_they_are_and_nothing_else(
    built: list[str],
) -> None:
    assert build_site.check_published(ROOT, SITE, build_site.sources(ROOT)) == []
    assert (SITE / "spec" / "notices" / "index.html").is_file()
    assert (SITE / "docs" / "decisions" / "index.html").is_file()
    assert (SITE / "spec" / "schemas" / "records.sql").read_bytes() == (
        ROOT / "spec" / "schemas" / "records.sql"
    ).read_bytes()
    for excluded in ("scripts", "data", "tests", "overrides", "AGENTS", "README"):
        assert not (SITE / excluded).exists()


@pytest.mark.spec("SITE-1")
def test_a_missing_page_a_changed_file_or_a_stray_file_is_reported(tmp_path: Path) -> None:
    root = write(tmp_path / "root", {"docs/a.md": "# A\n", "spec/s.json": "{}\n"})
    site = write(
        tmp_path / "site",
        {"spec/s.json": '{"changed": true}\n', "scripts/x.py": "", "assets/x.css": ""},
    )

    problems = build_site.check_published(root, site, ["docs/a.md", "spec/s.json"])

    assert problems == [
        "SITE-1: site/spec/s.json differs from spec/s.json",
        "SITE-1: the site lacks docs/a.md, at site/docs/a/index.html",
        "SITE-1: site/scripts/x.py is neither a document the site holds nor the theme's",
    ]


@pytest.mark.spec("SITE-1")
def test_the_site_holds_the_paths_under_its_directories() -> None:
    assert held("index.md")
    assert held("spec/notices.md")
    assert held("docs/decisions/README.md")
    assert held("investigation/edgar.md")
    assert not held("README.md")
    assert not held("AGENTS.md")
    assert not held("scripts/snapshot.py")
    assert not held("data/scope.json")


@pytest.mark.spec("PAGE-1")
def test_the_site_holds_only_documents_and_the_pages_written_for_it() -> None:
    assert build_site.check_pages(build_site.sources(ROOT)) == []


@pytest.mark.spec("PAGE-1")
def test_a_page_page_1_does_not_name_is_reported() -> None:
    held_files = ["index.md", "docs/explanation/x.md", "docs/status.md", "docs/notes.md"]

    assert build_site.check_pages(held_files) == [
        "PAGE-1: docs/notes.md is neither a document nor a page PAGE-1 names"
    ]


# --- Links ------------------------------------------------------------------


@pytest.mark.spec("SITE-3")
def test_every_link_on_the_site_resolves(
    built: list[str], commit: str, pages: dict[str, build_site.Page]
) -> None:
    assert build_site.check_links(ROOT, SITE, commit, pages) == []
    plan = pages["docs/plan/index.html"].links
    assert f"{REPOSITORY}/blob/{commit}/AGENTS.md#rules" in plan
    assert f"{REPOSITORY}/tree/{commit}/spec/schemas" in (pages["spec/snapshots/index.html"].links)


@pytest.mark.spec("SITE-3")
def test_a_link_that_leads_nowhere_is_reported(tmp_path: Path) -> None:
    blob = f"{REPOSITORY}/blob/{FAKE_COMMIT}"
    tree = f"{REPOSITORY}/tree/{FAKE_COMMIT}"
    here = "https://nslaughter.github.io/edgar-data-pipeline"
    links = [
        "../missing/",
        "../b/#nope",
        "../b/#here",
        f"{blob}/no/such/file.py",
        f"{blob}/AGENTS.md#no-such-heading",
        f"{blob}/AGENTS.md#rules",
        # What the site holds, linked on GitHub: a page, and a directory
        # with a page of its own.
        f"{blob}/spec/site.md",
        f"{tree}/docs/decisions",
        # A directory without a page is linked on GitHub, as tree/, not blob/.
        f"{blob}/spec/schemas",
        f"{tree}/spec/schemas",
        # The site's own host is within the site, absolute or not.
        f"{here}/no-such-page/#missing",
        "//nslaughter.github.io/edgar-data-pipeline/b/#nope",
        f"{here}/b/#here",
        "https://nslaughter.github.io/edgar-asof/",
        "https://example.com/",
    ]
    site = write(
        tmp_path,
        {
            "a/index.html": "".join(f'<a href="{link}">x</a>' for link in links),
            "b/index.html": '<h2 id="here">Here</h2>',
        },
    )

    problems = build_site.check_links(ROOT, site, FAKE_COMMIT, build_site.html_pages(site))

    assert problems == [
        "SITE-3: a/index.html links to ../missing/, which the site doesn't hold",
        "SITE-3: a/index.html links to ../b/#nope, whose anchor b/index.html lacks",
        f"SITE-3: a/index.html links to {blob}/no/such/file.py, which isn't in the repository",
        f"SITE-3: a/index.html links to {blob}/AGENTS.md#no-such-heading, whose anchor "
        "AGENTS.md lacks",
        f"SITE-3: a/index.html links to {blob}/spec/site.md on GitHub, but the site holds "
        "spec/site.md",
        f"SITE-3: a/index.html links to {tree}/docs/decisions on GitHub, but the site holds "
        "docs/decisions",
        f"SITE-3: a/index.html links to {blob}/spec/schemas, which isn't in the repository",
        f"SITE-3: a/index.html links to {here}/no-such-page/#missing, which the site doesn't hold",
        "SITE-3: a/index.html links to //nslaughter.github.io/edgar-data-pipeline/b/#nope, "
        "whose anchor b/index.html lacks",
        "SITE-3: a/index.html links to https://nslaughter.github.io/edgar-asof/, which the site "
        "doesn't hold",
    ]


@pytest.mark.spec("SITE-3", "SITE-8")
def test_the_extension_leads_links_to_files_the_site_does_not_hold_to_github() -> None:
    assert resolve("spec/notices.md", "../AGENTS.md#rules") == "AGENTS.md"
    assert resolve("spec/notices.md", "schemas/") == "spec/schemas"
    assert resolve("index.md", "docs/design.md") == "docs/design.md"
    assert resolve("index.md", "https://example.com/") is None
    assert resolve("index.md", "#anchor") is None
    assert resolve("index.md", "../outside") is None
    assert github_url("abc", "AGENTS.md", False, "rules") == (
        f"{REPOSITORY}/blob/abc/AGENTS.md#rules"
    )
    assert github_url("abc", "scripts", True, "") == f"{REPOSITORY}/tree/abc/scripts"
    assert has_page(ROOT / "docs" / "decisions")
    assert not has_page(ROOT / "spec" / "schemas")


@pytest.mark.spec("SITE-3", "SITE-8")
def test_the_extension_leads_links_in_raw_html_to_github_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SITE_COMMIT", FAKE_COMMIT)
    monkeypatch.setenv("SITE_ROOT", str(ROOT))
    parse_zensical_config(str(ROOT / "zensical.toml"))
    markdown = (
        "# Links\n\n"
        '## The <a href="../AGENTS.md#rules">rules</a>\n\n'
        '[Rules](../AGENTS.md#rules), <a href="../AGENTS.md#rules">rules</a>, '
        "<a href='../scripts/snapshot.py'>the script</a>, and "
        '<a href="design.md">the design</a>.\n\n'
        '<div markdown>\n<a href="../spec/schemas/">the schemas</a>\n</div>\n\n'
        # Attributes as HTML tokenizes them: a `>` or an href within a quoted
        # value is the value's, and a value may be unquoted.
        '<a title="a > b" href="../AGENTS.md#rules">rules</a>, '
        "<a title=\"its href = '../README.md'\" href=../scripts/snapshot.py>the script</a>, "
        "<A Href = ../AGENTS.md#rules>rules</A>.\n"
    )

    # As Zensical renders a page, with the site's configuration.
    content = render(markdown, "docs/plan.md", "docs/plan/")["content"]
    parser = build_site.PageParser()
    parser.feed(content)

    assert [link for link in parser.page.links if not link.startswith("#")] == [
        f"{REPOSITORY}/blob/{FAKE_COMMIT}/AGENTS.md#rules",
        f"{REPOSITORY}/blob/{FAKE_COMMIT}/AGENTS.md#rules",
        f"{REPOSITORY}/blob/{FAKE_COMMIT}/AGENTS.md#rules",
        f"{REPOSITORY}/blob/{FAKE_COMMIT}/scripts/snapshot.py",
        "../design/",
        f"{REPOSITORY}/tree/{FAKE_COMMIT}/spec/schemas",
        f"{REPOSITORY}/blob/{FAKE_COMMIT}/AGENTS.md#rules",
        f"{REPOSITORY}/blob/{FAKE_COMMIT}/scripts/snapshot.py",
        f"{REPOSITORY}/blob/{FAKE_COMMIT}/AGENTS.md#rules",
    ]
    assert "title=\"its href = '../README.md'\"" in content


@pytest.mark.spec("SITE-3")
def test_github_gives_headings_the_anchors_the_documents_use() -> None:
    slugs = build_site.github_slugs(
        "# Title\n\n## 10. Fetching\n\n## The `edgar-asof` page\n\n"
        "## The `received_at` field\n\n## `SEC_USER_AGENT`\n\n## SEC_USER_AGENT, _set_ **once**\n\n"
        "~~~\n## Not a heading\n```\n~~~\n\n## Title\n"
    )

    assert slugs == {
        "title",
        "10-fetching",
        "the-edgar-asof-page",
        "the-received_at-field",
        "sec_user_agent",
        "sec_user_agent-set-once",
        "title-1",
    }


# --- What a page loads ------------------------------------------------------


@pytest.mark.spec("SITE-4")
def test_no_page_loads_anything_from_another_host(
    built: list[str], pages: dict[str, build_site.Page]
) -> None:
    assert build_site.check_hosts(SITE, pages) == []
    assert (SITE / build_site.MERMAID).read_bytes() == (ROOT / build_site.MERMAID).read_bytes()


@pytest.mark.spec("SITE-4")
def test_a_page_that_loads_from_another_host_or_lacks_mermaid_is_reported(
    tmp_path: Path,
) -> None:
    local = '<script src="../docs/assets/mermaid.min.js"></script>'
    site = write(
        tmp_path,
        {
            "assets/javascripts/bundle.1.min.js": 'load("https://unpkg.com/mermaid@11/x.js")',
            "docs/assets/mermaid.min.js": 'a={version:"10.9.0"},b=s(()=>a.version,"getVersion")',
            "a/index.html": (
                f"{local}"
                '<script src="https://unpkg.com/x.js"></script>'
                '<link rel="stylesheet" href="//fonts.googleapis.com/css">'
                '<div style="background: url(https://example.com/i.png)"></div>'
                # What the theme writes for a repo_url, which its script asks
                # GitHub's API about.
                f'<a href="{REPOSITORY}" class="md-source" data-md-component="source">x</a>'
            ),
            "b/index.html": "<p>no Mermaid here</p>",
            "assets/stylesheets/main.css": "@import 'https://example.com/font.css';",
        },
    )

    problems = build_site.check_hosts(site, build_site.html_pages(site))

    assert problems == [
        "SITE-4: a/index.html loads https://unpkg.com/x.js from another host, by a <script>",
        "SITE-4: a/index.html loads //fonts.googleapis.com/css from another host, by a <link>",
        "SITE-4: a/index.html loads https://example.com/i.png from another host, in a style",
        f"SITE-4: a/index.html names the repository {REPOSITORY} in the theme's source "
        "component, whose script loads the repository's facts from another host",
        "SITE-4: b/index.html doesn't load the site's copy of Mermaid, docs/assets/mermaid.min.js",
        "SITE-4: assets/stylesheets/main.css loads https://example.com/font.css from another host",
        "SITE-4: the copy of Mermaid is 10.9.0, but the theme's script loads major version 11",
    ]


# --- Footers and notices ----------------------------------------------------


@pytest.mark.spec("SITE-5")
def test_every_footer_carries_the_notices_and_the_commit(
    built: list[str], commit: str, pages: dict[str, build_site.Page]
) -> None:
    assert build_site.check_footers(ROOT, SITE, commit, pages) == []
    assert ("https://github.com/nslaughter/edgar-data-pipeline/commit/" + commit, commit[:12]) in (
        pages["index.html"].footer_links
    )


@pytest.mark.spec("SITE-5")
def test_a_footer_that_lacks_a_notice_or_keeps_the_placeholder_is_reported(
    tmp_path: Path,
) -> None:
    notices = "../docs/third-party-notices/"
    site = write(
        tmp_path,
        {
            "docs/third-party-notices/index.html": footer(FAKE_COMMIT, "./"),
            "a/index.html": footer(FAKE_COMMIT, notices),
            "b/index.html": footer(FAKE_COMMIT, notices, copyright="Copyright 2026"),
            "c/index.html": '<div class="md-copyright">__SITE_COMMIT__</div>',
        },
    )

    problems = build_site.check_footers(ROOT, site, FAKE_COMMIT, build_site.html_pages(site))

    assert problems == [
        "SITE-5: b/index.html's footer lacks the copyright line, linked to LICENSE",
        "SITE-5: c/index.html's footer lacks the data's source or the disclaimer",
        "SITE-5: c/index.html's footer lacks the copyright line, linked to LICENSE",
        "SITE-5: c/index.html's footer lacks the commit built",
        "SITE-5: c/index.html's footer doesn't link to the notices page",
        "SITE-5: site/c/index.html still holds the commit's placeholder",
    ]


@pytest.mark.spec("SITE-6")
def test_the_notices_page_names_zensical_and_mermaid() -> None:
    assert build_site.check_notices(ROOT) == []


@pytest.mark.spec("SITE-6")
def test_a_notices_page_that_names_other_versions_is_reported(tmp_path: Path) -> None:
    root = write(
        tmp_path,
        {
            "uv.lock": 'name = "zensical"\nversion = "9.9.9"\n',
            build_site.MERMAID: 'a={version:"11.17.2"},b=s(()=>a.version,"getVersion")',
            build_site.NOTICES: "These notices are for Zensical 0.0.70 and Mermaid 11.0.0.\n",
        },
    )

    assert build_site.check_notices(root) == [
        f"SITE-6: {build_site.NOTICES} doesn't name the Zensical uv.lock pins, 9.9.9",
        f"SITE-6: {build_site.NOTICES} doesn't name the version of Mermaid's copy, 11.17.2",
    ]


# --- Navigation -------------------------------------------------------------


@pytest.mark.spec("SITE-7")
def test_the_navigation_is_four_tabs_holding_every_page(
    built: list[str], pages: dict[str, build_site.Page]
) -> None:
    expected = build_site.expected_nav(ROOT, build_site.sources(ROOT))

    assert build_site.check_nav(build_site.read_nav(ROOT), expected, pages["index.html"]) == []
    assert pages["index.html"].tabs == ["Home", "Explanation", "Reference", "Status"]
    assert expected["Reference"][:6] == [f"spec/{name}" for name in build_site.SPEC_ORDER]


@pytest.mark.spec("SITE-7")
def test_a_navigation_out_of_order_or_missing_a_page_is_reported() -> None:
    nav = build_site.read_nav(ROOT)
    expected = build_site.expected_nav(ROOT, build_site.sources(ROOT))
    swapped = [nav[0], nav[2], nav[1], nav[3]]
    missing = [*nav[:3], {"Status": ["docs/plan.md", build_site.NOTICES]}]

    assert len(build_site.check_nav(swapped, expected, None)) == 1
    assert build_site.check_nav(missing, expected, None) == [
        "SITE-7: the Status tab holds ['docs/plan.md', 'docs/third-party-notices.md'], "
        "not ['docs/status.md', 'docs/plan.md']",
        "SITE-7: the navigation holds the notices page, docs/third-party-notices.md",
    ]


# --- Statuses ---------------------------------------------------------------


@pytest.mark.spec("STATUS-1")
@pytest.mark.parametrize(
    ("build", "specify", "decided", "status"),
    [
        ([True], [True], [True], "Built"),
        ([True, True], [], [], "Built"),
        ([False], [True, True], [True], "Specified"),
        ([True, False], [True], [True], "Specified; built in part"),
        ([False], [True, False], [True], "Decided; specified in part"),
        ([True, False], [True, False], [True], "Decided; specified in part; built in part"),
        ([False], [False], [True, True], "Decided"),
        ([False], [], [True, False], "Planned; decided in part"),
        ([False], [], [], "Planned"),
        ([], [], [], "Planned"),
    ],
)
def test_a_parts_status_is_the_furthest_stage_it_has_reached(
    build: list[bool], specify: list[bool], decided: list[bool], status: str
) -> None:
    assert site_status.derive(build, specify, decided) == status


@pytest.mark.spec("STATUS-1")
def test_a_split_task_is_not_done_and_edgar_asof_is_built_once_pinned() -> None:
    split = site_status.Task("T37", "x", "Documentation", "T27", "Split on October 14, 2026")
    done = site_status.Task("T39", "x", "Documentation", "—", "October 14, 2026: version 0.1.0")
    sources = site_status.read_sources(ROOT)
    asof = site_status.Part(site_status.EDGAR_ASOF, ("0009",), (), (), (), ())
    pinned = site_status.Sources(sources.tasks, sources.decisions, sources.versions, "0.1.0")

    assert (split.split, split.done, done.done) == (True, False, True)
    assert site_status.part_status(asof, sources) == "Decided"
    assert site_status.part_status(asof, pinned) == "Built"


@pytest.mark.spec("STATUS-2")
def test_the_status_page_names_every_task_and_the_parts_required() -> None:
    assert [p for p in site_status.check(ROOT) if p.startswith("STATUS-2")] == []


@pytest.mark.spec("STATUS-2")
def test_a_task_left_out_or_a_part_missing_is_reported() -> None:
    sources = site_status.read_sources(ROOT)
    text = (ROOT / site_status.STATUS_PAGE).read_text("utf-8")
    region = site_status.between(text, "parts")
    assert region is not None
    parts, malformed = site_status.read_parts(text[region[0] : region[1]])
    assert malformed == []
    changed = [
        part
        if part.name != "The controls"
        else site_status.Part(part.name, ("0007", "0099"), part.specs, ("T14", "T99"), ("T37",), ())
        for part in parts
        if part.name != "The drills"
    ]

    problems = site_status.check_parts(ROOT, changed, sources)

    assert problems == [
        "STATUS-2: the status page has no part 'The drills'",
        "STATUS-2: 'The controls' names T99, which the plan lacks",
        "STATUS-2: 'The controls' names T37, which was split",
        "STATUS-2: 'The controls' names record 0099, which doesn't exist",
        "STATUS-2: no part names T31, 'Drills.'",
    ]


@pytest.mark.spec("STATUS-2")
def test_a_line_of_the_parts_table_that_is_not_a_row_is_reported_and_kept(
    tmp_path: Path,
) -> None:
    root = copy_sources(tmp_path)
    status = root / site_status.STATUS_PAGE
    text = status.read_text("utf-8")
    drills = next(line for line in text.splitlines() if line.startswith("| The drills |"))
    short = "| The export's page | Planned | — | — | — | T43 |"
    status.write_text(text.replace(drills, f"{drills}\n{short}"), "utf-8")

    pages, problems = site_status.generate(root)

    assert problems == [
        f"STATUS-2: docs/status.md's table of parts has a line that isn't a row of 7 cells: {short}"
    ]
    # Rewriting the table would drop the line, so nothing is written.
    assert pages == {}
    assert site_status.check(root) == problems


def copy_sources(target: Path) -> Path:
    """The files site_status.py reads, copied, so a test can change them."""
    names = ["docs/status.md", "docs/plan.md", "docs/decisions/README.md", "docs/design.md"]
    names += ["uv.lock", "index.md"]
    names += [p.relative_to(ROOT).as_posix() for p in (ROOT / "spec").glob("*.md")]
    names += [p.relative_to(ROOT).as_posix() for p in (ROOT / "docs/explanation").glob("*.md")]
    for name in names:
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target / name)
    return target


@pytest.mark.spec("STATUS-3")
def test_the_status_pages_generated_columns_are_current() -> None:
    assert site_status.check(ROOT) == []


@pytest.mark.spec("STATUS-3")
def test_a_task_marked_done_makes_the_status_page_out_of_date(tmp_path: Path) -> None:
    root = copy_sources(tmp_path)
    plan = root / "docs" / "plan.md"
    row = next(line for line in plan.read_text("utf-8").splitlines() if line.startswith("| T22 |"))
    plan.write_text(
        plan.read_text("utf-8").replace(row, row[: -len(" |")] + "October 15, 2026 |"),
        "utf-8",
    )

    assert site_status.check(root) == [
        f"STATUS-3: {page}'s generated parts are out of date; run `uv run scripts/site_status.py`"
        for page in ("docs/status.md", "index.md")
    ]
    pages, _ = site_status.generate(root)
    assert "| The controls | Built |" in pages["docs/status.md"]


@pytest.mark.spec("STATUS-4")
def test_the_home_page_begins_with_the_parts_statuses() -> None:
    text = (ROOT / "index.md").read_text("utf-8")

    assert text.startswith("# EDGAR data pipeline\n\n<!-- status: all -->\n")
    assert "- **Built:** The snapshot store in git" in text
    assert [p for p in site_status.check(ROOT) if p.startswith("STATUS-4")] == []


@pytest.mark.spec("STATUS-4")
def test_a_page_without_its_status_block_first_is_reported(tmp_path: Path) -> None:
    root = copy_sources(tmp_path)
    index = root / "index.md"
    text = index.read_text("utf-8")
    index.write_text(
        text.replace("# EDGAR data pipeline\n\n", "# EDGAR data pipeline\n\nIntro.\n\n")
    )
    write(
        root,
        {
            "docs/explanation/the-problem.md": "# The problem\n\nNo block.\n",
            "docs/explanation/the-architecture.md": (
                "# The architecture\n\n"
                "<!-- status: Fetcher; No such part -->\n<!-- end status -->\n"
            ),
        },
    )

    problems = site_status.check(root)

    assert "STATUS-4: index.md doesn't begin with its status block" in problems
    assert "STATUS-4: docs/explanation/the-problem.md has no status block" in problems
    assert (
        "STATUS-4: docs/explanation/the-architecture.md names parts the status page lacks: "
        "['No such part']"
    ) in problems
