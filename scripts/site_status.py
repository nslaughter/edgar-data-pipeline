"""Write the documentation site's statuses, or check them (spec/site.md, section 6).

Each part of the pipeline has one status, derived from the plan, the
specifications' Status lines, and the decision records' index, never written
by hand (STATUS-1). The status page, `docs/status.md`, lists the parts in a
table whose hand-written cells name each part's decision records,
specifications, and tasks (STATUS-2); this script rewrites the table in
place, between its markers, with each part's status, each task with whether
it's done, and each specification's version (STATUS-3). It also rewrites the
status block that begins the home page and each explanation page (STATUS-4).

    uv run scripts/site_status.py            # rewrite the generated parts
    uv run scripts/site_status.py --check    # write nothing; fail if any differs

The check also fails when the status page leaves out a task, names one that
doesn't exist, or lacks a part STATUS-2 requires. Neither mode writes
anything while a line of the table of parts isn't a row of its columns,
since rewriting the table would drop it.
"""

from __future__ import annotations

import argparse
import posixpath
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATUS_PAGE = "docs/status.md"
HOME_PAGE = "index.md"
EXPLANATION = "docs/explanation"

STAGES = ("Built", "Specified", "Decided", "Planned")
# The parts STATUS-2 requires besides the design's components, with
# monitoring as three parts in place of the design's one.
MONITORING = (
    "Monitoring: metrics, alerts, and dashboards",
    "Monitoring: traces",
    "Monitoring: logs",
)
REQUIRED = (
    "`edgar-asof`",
    "The snapshot store in git",
    "The snapshot store in the cluster",
    "The controls",
    "The charts",
    "The drills",
    "The documentation site",
)
EDGAR_ASOF = "`edgar-asof`"
COLUMNS = (
    "Part",
    "Status",
    "Decision records",
    "Specifications",
    "Tasks that specify it",
    "Tasks that build it",
    "Tasks that decide or revisit it",
)
NONE = "—"


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    section: str
    depends: str
    done_text: str

    @property
    def split(self) -> bool:
        return self.done_text.startswith("Split")

    @property
    def done(self) -> bool:
        """Done: its Done column is filled, and it wasn't split (spec/site.md, §2)."""
        return bool(self.done_text) and not self.split


@dataclass(frozen=True)
class Decision:
    number: str
    path: str
    accepted: bool


@dataclass(frozen=True)
class Part:
    name: str
    decisions: tuple[str, ...]
    specs: tuple[str, ...]
    specify: tuple[str, ...]
    build: tuple[str, ...]
    decide: tuple[str, ...]


@dataclass(frozen=True)
class Sources:
    tasks: dict[str, Task]
    decisions: dict[str, Decision]
    versions: dict[str, str]
    asof_pin: str | None


# --- Reading the sources ----------------------------------------------------


def task_key(id: str) -> int:
    return int(id[1:])


def read_plan(text: str) -> dict[str, Task]:
    """Each task in the plan, with its section and its row's cells."""
    tasks: dict[str, Task] = {}
    section = ""
    for line in text.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
        if not re.match(r"^\| T\d+ \|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        id, depends, done = cells[0], cells[-2], cells[-1]
        task = "|".join(cells[1:-2])
        title = re.match(r"\*\*(.+?)\*\*", task)
        tasks[id] = Task(id, title.group(1) if title else task, section, depends, done)
    return tasks


def read_decisions(text: str) -> dict[str, Decision]:
    """Each decision record in the index, with whether it's Accepted (spec/site.md, §2)."""
    decisions = {}
    for match in re.finditer(r"^\| \[(\d{4})\]\(([^)]+)\) \|[^|]*\| ([^|]+) \|$", text, re.M):
        number, path, status = match.groups()
        decisions[number] = Decision(number, path, status.strip().startswith("Accepted"))
    return decisions


def read_versions(root: Path) -> dict[str, str]:
    """Each specification's version, as its Status line gives it."""
    versions = {}
    for path in sorted((root / "spec").glob("*.md")):
        match = re.search(r"\*\*Status:\*\* Version (\d+\.\d+\.\d+)", path.read_text("utf-8"))
        if match:
            versions[path.name] = match.group(1)
    return versions


def read_asof_pin(lock: str) -> str | None:
    """The version of `edgar-asof` that `uv.lock` pins, if any."""
    match = re.search(r'^name = "edgar-asof"\nversion = "([^"]+)"', lock, re.M)
    return match.group(1) if match else None


def read_sources(root: Path) -> Sources:
    return Sources(
        tasks=read_plan((root / "docs" / "plan.md").read_text("utf-8")),
        decisions=read_decisions((root / "docs" / "decisions" / "README.md").read_text("utf-8")),
        versions=read_versions(root),
        asof_pin=read_asof_pin((root / "uv.lock").read_text("utf-8")),
    )


def design_components(design: str) -> list[str]:
    """The components the design's table names, in its order."""
    section = design.split("## Components", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| ([^|]+?) \|", section, re.M)
    return [row for row in rows if row not in ("Component", "---")]


# --- Statuses ---------------------------------------------------------------


def derive(build: list[bool], specify: list[bool], decided: list[bool]) -> str:
    """A part's status, from whether each of its tasks is done and each record Accepted.

    The furthest stage every one of whose tasks or records is done, then each
    later stage reached in part (STATUS-1).
    """
    stages = (("Built", build), ("Specified", specify), ("Decided", decided))
    reached = next(
        (name for name, marks in stages if marks and all(marks)),
        "Planned",
    )
    later = [name for name, _ in stages][: STAGES.index(reached)]
    partial = [
        f"{name.lower()} in part"
        for name, marks in reversed(stages)
        if name in later and any(marks) and not all(marks)
    ]
    return "; ".join([reached, *partial])


def part_status(part: Part, sources: Sources) -> str:
    decided = [n in sources.decisions and sources.decisions[n].accepted for n in part.decisions]
    if part.name == EDGAR_ASOF:
        # Its tasks are in its own repository: Built once uv.lock pins a
        # release of it (STATUS-1).
        return derive([sources.asof_pin is not None], [], decided)
    done = {id: task.done for id, task in sources.tasks.items()}
    return derive(
        [done.get(id, False) for id in part.build],
        [done.get(id, False) for id in part.specify],
        decided,
    )


def asof_releases(tasks: dict[str, Task]) -> list[tuple[str, list[str]]]:
    """Each release of `edgar-asof` a task waits on, with the tasks, in the plan's order."""
    releases: dict[str, list[str]] = {}
    for task in tasks.values():
        for item in task.depends.split(","):
            item = item.strip()
            if "edgar-asof" in item:
                releases.setdefault(item, []).append(task.id)
    return list(releases.items())


# --- The status page --------------------------------------------------------


def markers(name: str) -> tuple[str, str]:
    return f"<!-- generated: {name} -->", f"<!-- end generated: {name} -->"


def between(text: str, name: str) -> tuple[int, int] | None:
    """Where the generated region `name` is, or None if the text has none."""
    begin, end = markers(name)
    start = text.find(begin)
    if start < 0:
        return None
    finish = text.find(end, start)
    if finish < 0:
        return None
    return start + len(begin), finish


def ids(cell: str, pattern: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in re.findall(pattern, cell):
        if match not in found:
            found.append(match)
    return tuple(found)


def read_parts(table: str) -> tuple[list[Part], list[str]]:
    """The parts the status page's table names, from its hand-written cells.

    Also the problems: each line that isn't a row of the table's columns,
    which rewriting the table would drop (STATUS-2).
    """
    parts = []
    problems = []
    for line in table.strip().splitlines():
        if not line.strip():
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not line.lstrip().startswith("|") or len(cells) != len(COLUMNS):
            problems.append(
                f"STATUS-2: {STATUS_PAGE}'s table of parts has a line that isn't a row of "
                f"{len(COLUMNS)} cells: {line.strip()}"
            )
            continue
        if cells[0] in ("Part", "---"):
            continue
        name, _, decisions, specs, specify, build, decide = cells
        parts.append(
            Part(
                name=name,
                decisions=ids(decisions, r"\b(\d{4})\b"),
                specs=ids(specs, r"\b([a-z][a-z0-9-]*\.md)\b"),
                specify=ids(specify, r"\b(T\d+)\b"),
                build=() if name == EDGAR_ASOF else ids(build, r"\b(T\d+)\b"),
                decide=ids(decide, r"\b(T\d+)\b"),
            )
        )
    return parts, problems


def render_tasks(task_ids: tuple[str, ...], tasks: dict[str, Task]) -> str:
    rendered = []
    for id in task_ids:
        task = tasks.get(id)
        rendered.append(f"{id} ({'done' if task and task.done else 'not done'})")
    return ", ".join(rendered) or NONE


def render_parts(parts: list[Part], sources: Sources) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + " --- |" * len(COLUMNS)]
    for part in parts:
        decisions = ", ".join(
            (
                f"[{n}](decisions/{posixpath.basename(sources.decisions[n].path)})"
                + ("" if sources.decisions[n].accepted else " (not Accepted)")
            )
            if n in sources.decisions
            else n
            for n in part.decisions
        )
        specs = ", ".join(
            f"[`{name}`](../spec/{name}) {sources.versions.get(name, 'no version')}"
            for name in part.specs
        )
        if part.name == EDGAR_ASOF:
            releases = [
                f"{release} ({', '.join(waiting)})"
                for release, waiting in asof_releases(sources.tasks)
            ]
            pin = sources.asof_pin or "none"
            build = "; ".join([*releases, f"pinned in `uv.lock`: {pin}"])
        else:
            build = render_tasks(part.build, sources.tasks)
        cells = [
            part.name,
            part_status(part, sources),
            decisions or NONE,
            specs or NONE,
            render_tasks(part.specify, sources.tasks),
            build,
            render_tasks(part.decide, sources.tasks),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n" + "\n".join(lines) + "\n"


def render_investigation(tasks: dict[str, Task]) -> str:
    lines = ["| Task | Questions | Done |", "| --- | --- | --- |"]
    for task in tasks.values():
        if task.section != "Investigation":
            continue
        questions = ", ".join(re.findall(r"\bQ\d+\b", task.title)) or NONE
        when = task.done_text.split(":", 1)[0] if task.done else "Not done"
        lines.append(f"| {task.id}: {task.title} | {questions} | {when} |")
    return "\n" + "\n".join(lines) + "\n"


# --- Status blocks ----------------------------------------------------------

BLOCK = re.compile(r"<!-- status: (.*?) -->\n(.*?)<!-- end status -->", re.S)


def render_block(names: list[str], statuses: dict[str, str], page: str) -> str:
    link = posixpath.relpath(STATUS_PAGE, posixpath.dirname(page) or ".")
    if names == ["all"]:
        lines = [f"**What's built,** as the [status page]({link}) gives it:", ""]
        for stage in STAGES:
            members = [
                name + (f" ({status.split('; ', 1)[1]})" if "; " in status else "")
                for name, status in statuses.items()
                if status.split(";", 1)[0] == stage
            ]
            lines.append(f"- **{stage}:** {'; '.join(members) if members else 'none'}.")
        return "\n".join(lines) + "\n"
    shown = [f"{name}: {statuses.get(name, 'not on the status page')}" for name in names]
    return f"**Status** ([status page]({link})): " + ". ".join(shown) + ".\n"


def block_pages(root: Path) -> list[str]:
    explanation = sorted(p.relative_to(root).as_posix() for p in (root / EXPLANATION).glob("*.md"))
    return [HOME_PAGE, *explanation]


# --- Writing and checking ---------------------------------------------------


def generate(root: Path) -> tuple[dict[str, str], list[str]]:
    """Each page with its generated parts rewritten, and the problems found."""
    sources = read_sources(root)
    problems: list[str] = []
    pages: dict[str, str] = {}

    status_path = root / STATUS_PAGE
    text = status_path.read_text("utf-8")
    parts_region = between(text, "parts")
    investigation_region = between(text, "investigation")
    if parts_region is None or investigation_region is None:
        problems.append(f"STATUS-2: {STATUS_PAGE} lacks the markers of its generated tables")
        return pages, problems
    if investigation_region[0] < parts_region[0]:
        problems.append(f"STATUS-2: {STATUS_PAGE} has its investigation's table before its parts'")
        return pages, problems
    parts, malformed = read_parts(text[parts_region[0] : parts_region[1]])
    if malformed:
        # Rewriting the table would drop those lines, so nothing is written.
        problems += malformed
        return pages, problems
    problems += check_parts(root, parts, sources)
    pages[STATUS_PAGE] = (
        text[: parts_region[0]]
        + render_parts(parts, sources)
        + text[parts_region[1] : investigation_region[0]]
        + render_investigation(sources.tasks)
        + text[investigation_region[1] :]
    )
    statuses = {part.name: part_status(part, sources) for part in parts}

    for page in block_pages(root):
        path = root / page
        if not path.exists():
            continue
        text = path.read_text("utf-8")
        match = BLOCK.search(text)
        if match is None:
            problems.append(f"STATUS-4: {page} has no status block")
            continue
        names = [name.strip() for name in match.group(1).split(";") if name.strip()]
        unknown = [name for name in names if name != "all" and name not in statuses]
        if unknown:
            problems.append(f"STATUS-4: {page} names parts the status page lacks: {unknown}")
        first = re.match(r"\A# [^\n]+\n\n<!-- status: ", text)
        if first is None:
            problems.append(f"STATUS-4: {page} doesn't begin with its status block")
        start, end = match.span(2)
        pages[page] = text[:start] + render_block(names, statuses, page) + text[end:]
    return pages, problems


def check_parts(root: Path, parts: list[Part], sources: Sources) -> list[str]:
    """STATUS-2: every task named, everything named exists, and the parts required."""
    problems = []
    names = [part.name for part in parts]
    components = [
        c
        for c in design_components((root / "docs" / "design.md").read_text("utf-8"))
        if c != "Monitoring"
    ]
    for required in [*components, *MONITORING, *REQUIRED]:
        if required not in names:
            problems.append(f"STATUS-2: the status page has no part {required!r}")
    named: set[str] = set()
    for part in parts:
        for id in (*part.specify, *part.build, *part.decide):
            named.add(id)
            if id not in sources.tasks:
                problems.append(f"STATUS-2: {part.name!r} names {id}, which the plan lacks")
            elif sources.tasks[id].split:
                problems.append(f"STATUS-2: {part.name!r} names {id}, which was split")
        for number in part.decisions:
            if number not in sources.decisions:
                problems.append(
                    f"STATUS-2: {part.name!r} names record {number}, which doesn't exist"
                )
        for spec in part.specs:
            if spec not in sources.versions:
                problems.append(f"STATUS-2: {part.name!r} names {spec}, which has no version")
    for task in sources.tasks.values():
        if task.section != "Investigation" and not task.split and task.id not in named:
            problems.append(f"STATUS-2: no part names {task.id}, {task.title!r}")
    return problems


def check(root: Path) -> list[str]:
    """Every problem with the statuses as committed (STATUS-2 to STATUS-4)."""
    pages, problems = generate(root)
    for page, text in pages.items():
        if (root / page).read_text("utf-8") != text:
            problems.append(
                f"STATUS-3: {page}'s generated parts are out of date; "
                "run `uv run scripts/site_status.py`"
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--check", action="store_true", help="write nothing; fail if any differs")
    args = parser.parse_args(argv)
    if args.check:
        problems = check(ROOT)
    else:
        pages, problems = generate(ROOT)
        for page, text in pages.items():
            if (ROOT / page).read_text("utf-8") != text:
                (ROOT / page).write_text(text, "utf-8")
                print(f"wrote {page}")
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
