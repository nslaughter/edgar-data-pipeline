"""spec/snapshots.md's statements and the tests that check them (§3).

Every statement appears in the index with its kind; every statement checked
by `verify` or `tests` has a test marked with its ID; every marker names a
statement; and every problem verify.py reports names an invariant it checks.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / "spec" / "snapshots.md").read_text(encoding="utf-8")
INDEX_ROW = re.compile(r"^\| ([A-Z]+-\d+) \| (invariant|rule|retired) \| ([^|]+) \| .+ \|$", re.M)
STATEMENT = re.compile(r"^- \*\*([A-Z]+-\d+)\*\* \((invariant|rule)\)", re.M)
# verify.py quotes an ID only where it reports one.
REPORTED = re.compile(r'"([A-Z]+-\d+)"')


def index() -> dict[str, tuple[str, set[str]]]:
    rows = INDEX_ROW.findall(SPEC)
    return {id: (kind, {c.strip() for c in checked.split(",")}) for id, kind, checked in rows}


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


def test_every_statement_is_in_the_index_with_its_kind() -> None:
    stated = dict(STATEMENT.findall(SPEC))
    indexed = {id: kind for id, (kind, _) in index().items() if kind != "retired"}

    assert stated == indexed


def test_every_statement_checked_by_verify_or_tests_has_a_test() -> None:
    marked = markers()
    required = [
        id
        for id, (kind, checked) in index().items()
        if kind != "retired" and checked & {"verify", "tests"}
    ]

    assert [id for id in required if id not in marked] == []


def test_every_marker_names_a_statement() -> None:
    assert sorted(set(markers()) - set(index())) == []


def test_every_problem_verify_reports_names_an_invariant_it_checks() -> None:
    reported = set(REPORTED.findall((ROOT / "scripts" / "verify.py").read_text(encoding="utf-8")))
    checked = {id for id, (kind, by) in index().items() if kind == "invariant" and "verify" in by}

    assert reported - checked == set()
    assert checked - reported == set()
