"""Every spec/*.md's statements and the tests that check them.

Every statement appears in its specification's index with its kind, checked
in ways the specifications name; each ID area belongs to one specification;
every statement checked by `verify`, `tests`, or `kind` has a test marked
with its ID, unless a later task in docs/plan.md, not yet done, writes it;
every marker names a statement; and every problem verify.py reports names
an invariant it checks.
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
# T20 builds the umbrella chart that spec/deployment.md's tests check; of
# spec/controls.md's, T22 the definitions, the chart's resources, and the
# scheduler, T23 the fetcher, T24 the discoverer, and T28 the monitoring,
# after which each ServiceAccount in RBAC-5's table has its grants on kind;
# and of spec/snapshots.md's, T35 brings snapshot.py and verify.py to its
# version 1.0.0, T22 the scheduler's jobs, T23 the fetcher and the cluster
# store, T24 the discoverer's jobs and its use of edgar-asof, and T36 the
# transfer. Strict, like xfail_strict: a statement listed here that has a
# test fails too, and so does one whose task the plan marks done, or one
# checked by verify that verify.py already reports.
AWAITING = {
    **dict.fromkeys(
        [
            "CHART-1",
            "CHART-2",
            "CHART-3",
            "PIN-1",
            "PIN-2",
            "PIN-3",
            "PIN-4",
            "PIN-5",
            "PIN-6",
            "PIN-7",
            "PG-1",
            "PG-2",
        ],
        "T20",
    ),
    **dict.fromkeys(
        [
            "API-2",
            "API-3",
            "VAL-1",
            "VAL-2",
            "VAL-3",
            "VAL-4",
            "SOURCE-1",
            "SOURCE-2",
            "SOURCE-3",
            "ACCESS-2",
            "RBAC-1",
            "RBAC-4",
        ],
        "T22",
    ),
    **dict.fromkeys(["JOB-2"], "T22"),
    **dict.fromkeys(
        [
            "SOURCE-4",
            "BACKFILL-2",
            "ACCESS-1",
            "ACCESS-3",
            "RBAC-2",
            "STORE-2",
            "STORE-3",
            "OBJ-4",
            "LOG-3",
            "LOG-4",
            "FETCH-16",
            "FETCH-17",
            "ID-2",
            "JOB-4",
            "JOB-5",
            "JOB-6",
            "JOB-7",
        ],
        "T23",
    ),
    **dict.fromkeys(["SOURCE-5", "BACKFILL-1", "BACKFILL-3", "FETCH-18", "JOB-3"], "T24"),
    **dict.fromkeys(["RBAC-3", "RBAC-5"], "T28"),
    **dict.fromkeys(["REC-10", "REC-11", "REC-12", "REC-14", "SCOPE-4", "SCOPE-5"], "T35"),
    **dict.fromkeys(["XFER-1", "XFER-2", "XFER-3"], "T36"),
}


def spec_index(spec: str) -> dict[str, tuple[str, set[str]]]:
    rows = INDEX_ROW.findall(spec)
    return {id: (kind, {c.strip() for c in checked.split(",")}) for id, kind, checked in rows}


def index() -> dict[str, tuple[str, set[str]]]:
    """Every specification's index as one, since an ID names one statement."""
    return {id: row for spec in SPECS.values() for id, row in spec_index(spec).items()}


def area(id: str) -> str:
    return id.rsplit("-", 1)[0]


def markers() -> dict[str, list[str]]:
    """Each ID named by @pytest.mark.spec(...), with the tests that carry it."""
    found: dict[str, list[str]] = {}
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


def test_every_problem_verify_reports_names_an_invariant_it_checks() -> None:
    reported = set(REPORTED.findall((ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")))
    checked = {id for id, (kind, by) in index().items() if kind == "invariant" and "verify" in by}

    assert reported - checked == set()
    assert checked - reported == checked & set(AWAITING)
