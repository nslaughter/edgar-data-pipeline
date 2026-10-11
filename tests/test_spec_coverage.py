"""Every spec/*.md's statements and the tests that check them.

Every statement appears in its specification's index with its kind; each ID
area belongs to one specification; every statement checked by `verify`,
`tests`, or `kind` has a test marked with its ID, unless a later task in
docs/plan.md writes it; every marker names a statement; and every problem
verify.py reports names an invariant it checks.
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
# The checks a statement can't pass without a test that carries its ID.
TESTED_BY = {"verify", "tests", "kind"}
# Statements whose tests a later task in docs/plan.md writes, with that task:
# T20 builds the umbrella chart that spec/deployment.md's tests check. Strict,
# like xfail_strict: a statement listed here that has a test fails too.
AWAITING = dict.fromkeys(
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
)


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


def test_every_statement_is_in_its_specs_index_with_its_kind() -> None:
    for name, spec in SPECS.items():
        stated = dict(STATEMENT.findall(spec))
        indexed = {id: kind for id, (kind, _) in spec_index(spec).items() if kind != "retired"}

        assert stated == indexed, name


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


def test_every_marker_names_a_statement() -> None:
    assert sorted(set(markers()) - set(index())) == []


def test_every_problem_verify_reports_names_an_invariant_it_checks() -> None:
    reported = set(REPORTED.findall((ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")))
    checked = {id for id, (kind, by) in index().items() if kind == "invariant" and "verify" in by}

    assert reported - checked == set()
    assert checked - reported == set()
