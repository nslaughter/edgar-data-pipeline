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
# of spec/deployment.md's, T20 builds the charts that most of its tests
# check, T21 the first of the pipeline's images, and T23 the fetcher, with
# its Lease; of spec/controls.md's, T22 the Go types that generate the
# definitions, the chart's resources, and the scheduler, T23 the fetcher,
# T24 the discoverer, and T28 the monitoring, after which each
# ServiceAccount in RBAC-5's table has its grants on kind; of
# spec/snapshots.md's, T22 the scheduler's jobs, T23 the fetcher and the
# cluster store, T24 the discoverer's jobs and its use of edgar-asof, and
# T36 the transfer; of spec/notices.md's, T24 the discoverer, with the
# scheduler's orders and the status it sets from notices; and of
# spec/telemetry.md's, T28 the monitoring of observing filings, with the
# services' metrics, and T29 the traces. Strict, like
# xfail_strict: a statement listed here that has a test fails too, and so
# does one whose task the plan marks done, or one checked by verify that
# verify.py already reports.
AWAITING = {
    **dict.fromkeys(
        [
            "CHART-1",
            "CHART-2",
            "CHART-3",
            "CHART-4",
            "PIN-1",
            "PIN-2",
            "PIN-3",
            "PIN-4",
            "PIN-5",
            "PIN-6",
            "PIN-7",
            "PG-1",
            "PG-2",
            "PG-3",
            "PG-4",
            "PG-5",
            "PG-6",
            "PG-7",
            "PG-8",
            "PG-9",
            "CRD-1",
            "CRD-2",
            "CRD-3",
            "CRD-4",
            "CRD-5",
            "CRD-6",
            "KIND-1",
            "KIND-2",
            "VOL-1",
            "VOL-2",
            "VOL-3",
            "PLACE-1",
            "PLACE-2",
            "NATS-1",
            "NATS-2",
            "NATS-3",
            "NATS-4",
            "GARAGE-1",
            "GARAGE-2",
            "GARAGE-3",
            "GARAGE-4",
            "MON-1",
            "MON-2",
            "MON-3",
            "MON-4",
            "SECRET-1",
            "SECRET-2",
            "SECRET-3",
        ],
        "T20",
    ),
    **dict.fromkeys(["IMG-1", "IMG-2", "IMG-3"], "T21"),
    **dict.fromkeys(["LEASE-1", "LEASE-2"], "T23"),
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
        ["TRACE-1", "TRACE-2", "TRACE-3", "TRACE-4", "TRACE-5", "TRACE-6", "TRACE-7"], "T29"
    ),
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
