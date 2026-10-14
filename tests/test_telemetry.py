"""The dashboards' views in spec/schemas/ against spec/telemetry.md's tables.

dashboards.sql creates the schema `dashboards` and, in it, the views of
section 8, each with the columns its table gives it, and nothing else. Each
test carries the IDs of the statements it checks, as @pytest.mark.spec(...);
tests/test_spec_coverage.py requires every statement checked by `tests` or
`kind` to have one, or to await a later task.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = (ROOT / "spec" / "telemetry.md").read_text(encoding="utf-8")
SQL = (ROOT / "spec" / "schemas" / "dashboards.sql").read_text(encoding="utf-8")
# A table row whose first cell is a name in backticks.
ROW = re.compile(r"^\| `(\w+)` \|")
VIEW = re.compile(r"^CREATE VIEW dashboards\.(\w+) \((.*?)\) AS$", re.M | re.S)
# Every statement that creates something, so the file can create nothing else.
CREATE = re.compile(r"^CREATE (\w+) ([\w.]+)", re.M)


def names_after(marker: str) -> list[str]:
    """The names in the first column of the first Markdown table after marker."""
    rest = SPEC[SPEC.index(marker) :].splitlines()
    start = next(i for i, line in enumerate(rest) if line.startswith("|"))
    end = next(i for i in range(start, len(rest)) if not rest[i].startswith("|"))
    rows = [ROW.match(line) for line in rest[start + 2 : end]]
    return [row[1] for row in rows if row]


def views() -> dict[str, list[str]]:
    """Each view in dashboards.sql, with its columns."""
    return {name: re.split(r",\s*", columns.strip()) for name, columns in VIEW.findall(SQL)}


VIEWS = names_after("| View | One row for each |")


@pytest.mark.spec("VIEW-1")
def test_dashboards_sql_creates_the_schema_and_section_8s_views_and_nothing_else() -> None:
    assert CREATE.findall(SQL) == [
        ("SCHEMA", "dashboards"),
        *(("VIEW", f"dashboards.{name}") for name in views()),
    ]
    assert sorted(views()) == sorted(VIEWS) == ["coverage", "gaps", "lags"]


@pytest.mark.spec("VIEW-1")
@pytest.mark.parametrize("view", VIEWS)
def test_each_view_has_its_tables_columns_in_order(view: str) -> None:
    assert views()[view] == names_after(f"The view `{view}` has")
