"""Every spec/*.md's statements and the tests that check them.

Every statement appears in its specification's index with its kind, checked
in ways the specifications name; each ID area belongs to one specification;
every statement checked by `verify`, `tests`, or `kind` has a test marked
with its ID, unless a later task in docs/plan.md, not yet done, writes it;
every marker names a statement; and every problem verify.py reports names
an invariant it checks.

A Python test carries its IDs as @pytest.mark.spec("REC-5", ...). A Go test
carries them in a line of its doc comment, `// spec: SOURCE-1, SOURCE-2`,
the IDs separated by a comma and a space, directly above its
`func TestName(t *testing.T)`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPECS = {path.name: path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("spec/*.md"))}
INDEX_ROW = re.compile(r"^\| ([A-Z]+-\d+) \| (invariant|rule|retired) \| ([^|]+) \| .+ \|$", re.M)
STATEMENT = re.compile(r"^- \*\*([A-Z]+-\d+)\*\* \((invariant|rule)\)", re.M)
# verify.py quotes an ID only where it reports one.
REPORTED = re.compile(r'"([A-Z]+-\d+)"')
# A task's row in docs/plan.md, with its last column, Done.
PLAN_ROW = re.compile(r"^\| (T\d+) \|.*\|([^|]*)\|$", re.M)
# Each way an index says a statement is checked, as the specifications' §3
# name them, so a misspelled one can't exempt a statement from its test.
CHECKS = {"verify", "tests", "kind", "review", "operator", "first import"}
# The checks a statement can't pass without a test that carries its ID.
TESTED_BY = {"verify", "tests", "kind"}
# Statements whose tests a later task in docs/plan.md writes, with that task:
# of spec/controls.md's, T24 the discoverer, and T28 the monitoring, after
# which each ServiceAccount in RBAC-5's table has its grants on kind; of
# spec/snapshots.md's, T24 the discoverer's jobs and its use of edgar-asof,
# and T36 the transfer; of spec/deployment.md's, T36 the transfer's access
# to the cluster store; of spec/notices.md's, T24 the
# discoverer, with the scheduler's orders and the status it sets from
# notices; and of spec/telemetry.md's, T28 the monitoring of observing
# filings, with the services' metrics, and T29 the traces. Strict, like
# xfail_strict: a statement listed here that has a test fails too, and so
# does one whose task the plan marks done, or one checked by verify that
# verify.py already reports.
AWAITING = {
    **dict.fromkeys(["SOURCE-5", "BACKFILL-1", "BACKFILL-3", "FETCH-18", "JOB-3"], "T24"),
    **dict.fromkeys(
        [
            "TABLE-2",
            "TABLE-3",
            "GAP-1",
            "GAP-2",
            "GAP-3",
            "SEEN-1",
            "SEEN-2",
            "SEEN-3",
            "FILE-1",
            "FILE-2",
            "FILE-3",
            "FILE-4",
            "FILE-5",
            "FILE-6",
            "ORDER-1",
            "LIST-1",
            "LIST-2",
            "LIST-3",
            "LIST-4",
            "LIST-5",
            "LIST-6",
            "NOTICE-1",
            "NOTICE-2",
            "MSG-2",
            "DISC-1",
            "DISC-2",
            "LAG-1",
        ],
        "T24",
    ),
    **dict.fromkeys(["RBAC-3", "RBAC-5"], "T28"),
    **dict.fromkeys(
        [
            "METRIC-1",
            "METRIC-2",
            "METRIC-3",
            "METRIC-4",
            "METRIC-5",
            "STATE-1",
            "SCRAPE-1",
            "SCRAPE-2",
            "ALERT-1",
            "ALERT-2",
            "ALERT-3",
            "ALERT-4",
            "VIEW-2",
            "VIEW-3",
            "VIEW-4",
            "VIEW-5",
            "VIEW-6",
        ],
        "T28",
    ),
    **dict.fromkeys(
        [
            "TRACE-1",
            "TRACE-2",
            "TRACE-3",
            "TRACE-4",
            "TRACE-5",
            "TRACE-6",
            "TRACE-7",
            "TRACE-8",
        ],
        "T29",
    ),
    **dict.fromkeys(["XFER-1", "XFER-2", "XFER-3", "LOG-5"], "T36"),
    **dict.fromkeys(["TRANSFER-3", "TRANSFER-4", "TRANSFER-5", "TRANSFER-6"], "T36"),
}


def spec_index(spec: str) -> dict[str, tuple[str, set[str]]]:
    rows = INDEX_ROW.findall(spec)
    return {id: (kind, {c.strip() for c in checked.split(",")}) for id, kind, checked in rows}


def index() -> dict[str, tuple[str, set[str]]]:
    """Every specification's index as one, since an ID names one statement."""
    return {id: row for spec in SPECS.values() for id, row in spec_index(spec).items()}


def area(id: str) -> str:
    return id.rsplit("-", 1)[0]


# A Go test function with its doc comment, and a line of that comment that
# names the IDs the test checks.
GO_TEST = re.compile(r"((?:^//.*\n)+)^func (Test\w+)\(t \*testing\.T\)", re.M)
GO_MARKER = re.compile(r"^// spec: ([A-Z]+-\d+(?:, [A-Z]+-\d+)*)$", re.M)
GO_MARKER_LINE = re.compile(r"^[ \t]*//[ \t]*spec:.*$", re.M)


def go_test_files() -> list[Path]:
    """Each Go test file in the repository, outside the directories git ignores."""
    return sorted(
        path
        for path in ROOT.rglob("*_test.go")
        if not any(part.startswith(".") or part == "site" for part in path.relative_to(ROOT).parts)
    )


def go_markers() -> dict[str, list[str]]:
    """Each ID a Go test's `// spec:` line names, with the tests that carry it."""
    found: dict[str, list[str]] = {}
    for path in go_test_files():
        for comment, name in GO_TEST.findall(path.read_text(encoding="utf-8")):
            for ids in GO_MARKER.findall(comment):
                for id in ids.split(", "):
                    found.setdefault(id, []).append(f"{path.relative_to(ROOT)}:{name}")
    return found


def markers() -> dict[str, list[str]]:
    """Each ID named by @pytest.mark.spec(...), or by a Go test's `// spec:`
    line, with the tests that carry it."""
    found: dict[str, list[str]] = go_markers()
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "spec"
                    and isinstance(decorator.func.value, ast.Attribute)
                    and decorator.func.value.attr == "mark"
                ):
                    for arg in decorator.args:
                        assert isinstance(arg, ast.Constant)
                        assert isinstance(arg.value, str)
                        found.setdefault(arg.value, []).append(node.name)
    return found


def needing_tests() -> list[str]:
    return [
        id for id, (kind, checked) in index().items() if kind != "retired" and checked & TESTED_BY
    ]


def plan_tasks() -> dict[str, str]:
    """Each task in docs/plan.md, with what its Done column says."""
    plan = (ROOT / "docs" / "plan.md").read_text(encoding="utf-8")
    return {task: done.strip() for task, done in PLAN_ROW.findall(plan)}


def test_every_statement_is_in_its_specs_index_with_its_kind() -> None:
    for name, spec in SPECS.items():
        stated = dict(STATEMENT.findall(spec))
        indexed = {id: kind for id, (kind, _) in spec_index(spec).items() if kind != "retired"}

        assert stated == indexed, name


def test_every_statement_is_checked_in_ways_the_specs_name() -> None:
    unknown = {id: checked - CHECKS for id, (_, checked) in index().items() if checked - CHECKS}

    assert unknown == {}


def test_each_id_area_belongs_to_one_spec() -> None:
    owners: dict[str, set[str]] = {}
    for name, spec in SPECS.items():
        for id in spec_index(spec):
            owners.setdefault(area(id), set()).add(name)

    assert {a: names for a, names in owners.items() if len(names) > 1} == {}


def test_every_statement_checked_by_verify_tests_or_kind_has_a_test() -> None:
    marked = markers()

    assert [id for id in needing_tests() if id not in marked and id not in AWAITING] == []


def test_a_statement_awaits_its_test_only_until_the_test_exists() -> None:
    marked = markers()

    assert sorted(set(AWAITING) - set(needing_tests())) == []
    assert sorted(id for id in AWAITING if id in marked) == []


def test_a_statement_awaits_only_a_task_the_plan_has_not_done() -> None:
    not_done = {task for task, done in plan_tasks().items() if not done}

    assert sorted(set(AWAITING.values()) - not_done) == []


def test_every_marker_names_a_statement() -> None:
    assert sorted(set(markers()) - set(index())) == []


def test_every_go_spec_line_is_in_a_tests_doc_comment_in_its_form() -> None:
    """A `// spec:` line elsewhere, or in another form, would mark no test."""
    stray = []
    for path in go_test_files():
        text = path.read_text(encoding="utf-8")
        attached = [
            line for comment, _ in GO_TEST.findall(text) for line in GO_MARKER.findall(comment)
        ]
        lines = GO_MARKER_LINE.findall(text)
        well_formed = [line for line in lines if GO_MARKER.fullmatch(line)]
        if len(attached) != len(lines) or len(well_formed) != len(lines):
            stray.append(str(path.relative_to(ROOT)))

    assert stray == []


def test_the_go_tests_carry_markers() -> None:
    assert go_test_files()
    assert go_markers()


def test_every_problem_verify_reports_names_an_invariant_it_checks() -> None:
    reported = set(REPORTED.findall((ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")))
    checked = {id for id, (kind, by) in index().items() if kind == "invariant" and "verify" in by}

    assert reported - checked == set()
    assert checked - reported == checked & set(AWAITING)
