"""The notices' schemas in spec/schemas/ against spec/notices.md's tables.

notices.sql defines the tables and views of section 4, each table with the
columns its section gives it, of their types, and each view with its
columns; and the order's and the notice's JSON Schemas have the fields of
section 9's tables. Each test carries the IDs of the statements it checks,
as @pytest.mark.spec(...); tests/test_spec_coverage.py requires every
statement checked by `tests` or `kind` to have one, or to await a later
task.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import extend

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "spec" / "schemas"
SPEC = (ROOT / "spec" / "notices.md").read_text(encoding="utf-8")
SQL = (SCHEMAS / "notices.sql").read_text(encoding="utf-8")
# A table row whose first cell is a name in backticks, and its second cell.
ROW = re.compile(r"^\| `(\w+)` \| ([^|]+) \|")
# A column of a table in notices.sql: a lower-case name at the start of a line
# indented by four spaces, which no constraint or comment begins with.
COLUMN = re.compile(r"^    ([a-z_][a-z0-9_]*) +(.+?),?$", re.M)
TABLE = re.compile(r"^CREATE TABLE (\w+) \((.*?)^\);", re.M | re.S)
VIEW = re.compile(r"^CREATE VIEW (\w+) \((.*?)\) AS$", re.M | re.S)
# A timestamp's and a date's formats hold each day to its month when a
# validator asserts them, as these tests' validators do. jsonschema checks
# them only with a package the tests don't install, so these parse them.
FORMATS = FormatChecker(formats=())


@FORMATS.checks("date-time", raises=ValueError)
def is_date_time(value: object) -> bool:
    return not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is not None


@FORMATS.checks("date", raises=ValueError)
def is_date(value: object) -> bool:
    return not isinstance(value, str) or bool(date.fromisoformat(value))


def pattern(
    validator: Validator, pattern: str, instance: Any, schema: dict[str, Any]
) -> Iterator[ValidationError]:
    r"""JSON Schema's pattern, read as ECMA-262 reads it, as JSON Schema says.

    ECMA-262's `$` matches only at the end of the input, but Python's also
    matches before a final newline, as tests/test_store_schemas.py explains.
    Every `$` in the schemas ends its pattern, and this reads it as `\Z`.
    """
    ends = pattern.endswith("$") and not pattern.endswith(r"\$")
    ecma = pattern[:-1] + r"\Z" if ends else pattern
    if validator.is_type(instance, "string") and not re.search(ecma, instance):
        yield ValidationError(f"{instance!r} does not match {pattern!r}")


# jsonschema's stubs leave extend untyped.
SchemaValidator = extend(Draft202012Validator, {"pattern": pattern})  # type: ignore[no-untyped-call]


def rows_after(marker: str) -> list[tuple[str, str]]:
    """Each row of the first Markdown table after marker: its name and second cell."""
    rest = SPEC[SPEC.index(marker) :].splitlines()
    start = next(i for i, line in enumerate(rest) if line.startswith("|"))
    end = next(i for i in range(start, len(rest)) if not rest[i].startswith("|"))
    rows = [ROW.match(line) for line in rest[start + 2 : end]]
    return [(row[1], row[2].strip()) for row in rows if row]


def tables() -> dict[str, str]:
    """Each table in notices.sql, with what its parentheses hold."""
    return dict(TABLE.findall(SQL))


def views() -> dict[str, list[str]]:
    """Each view in notices.sql, with its columns."""
    return {name: re.split(r",\s*", columns.strip()) for name, columns in VIEW.findall(SQL)}


def schema(name: str) -> dict[str, Any]:
    loaded = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def validator(name: str) -> Validator:
    """A validator that reads patterns as ECMA-262 does and asserts formats.

    Without $schema, which names draft 2020-12, jsonschema keeps this
    validator at each $ref instead of picking draft 2020-12's own.
    """
    loaded = schema(name)
    assert loaded.pop("$schema") == Draft202012Validator.META_SCHEMA["$id"], name
    checked: Validator = SchemaValidator(loaded, format_checker=FORMATS)
    return checked


TABLES = [name for name, _ in rows_after("| Table | One row for each | Columns in |")]
VIEWS = [name for name, _ in rows_after("| View | One row for each | Columns in |")]
ORDER_FIELDS = rows_after("An order's fields:")
NOTICE_FIELDS = rows_after("A notice's fields:")
KINDS = ["filing", "gap", "backfill"]


@pytest.mark.spec("TABLE-1")
def test_notices_sql_defines_the_tables_and_views_of_section_4_and_no_others() -> None:
    # The file creates each table after those its foreign keys name, so its
    # order needn't be the section's.
    created = re.findall(r"^CREATE (?:TABLE|VIEW) (\w+)", SQL, re.M)

    assert sorted(tables()) == sorted(TABLES)
    assert sorted(views()) == sorted(VIEWS)
    assert sorted(created) == sorted(TABLES + VIEWS)


@pytest.mark.spec("TABLE-1")
@pytest.mark.parametrize("table", TABLES)
def test_each_table_has_its_sections_columns_in_order(table: str) -> None:
    columns = dict(COLUMN.findall(tables()[table]))

    assert list(columns) == [name for name, _ in rows_after(f"The table `{table}` has")]


@pytest.mark.spec("TABLE-1")
@pytest.mark.parametrize("table", TABLES)
def test_each_column_has_its_type_and_is_not_null_unless_it_may_be(table: str) -> None:
    columns = dict(COLUMN.findall(tables()[table]))
    for name, kind in rows_after(f"The table `{table}` has"):
        [sql_type] = re.findall(r"`([^`]+)`", kind)

        assert columns[name] == sql_type or columns[name].startswith(sql_type + " "), name
        assert ("NOT NULL" in columns[name]) == (not kind.endswith(", or null")), name


@pytest.mark.spec("TABLE-1")
@pytest.mark.parametrize("view", VIEWS)
def test_each_view_has_its_sections_columns_in_order(view: str) -> None:
    assert views()[view] == [name for name, _ in rows_after(f"The view `{view}` has")]


@pytest.mark.spec("MSG-1")
@pytest.mark.parametrize("name", ["order.schema.json", "notice.schema.json"])
def test_each_message_schema_is_a_json_schema(name: str) -> None:
    Draft202012Validator.check_schema(schema(name))


@pytest.mark.spec("MSG-1")
@pytest.mark.parametrize(
    ("name", "table"),
    [("order.schema.json", ORDER_FIELDS), ("notice.schema.json", NOTICE_FIELDS)],
    ids=["order", "notice"],
)
def test_each_message_schema_has_its_tables_fields(name: str, table: list[tuple[str, str]]) -> None:
    message = schema(name)

    assert list(message["properties"]) == [field for field, _ in table]
    assert message["required"] == [field for field, kind in table if kind.startswith("Required:")]


def kind_fields(kind: str) -> list[str]:
    """The fields section 9's table requires in a notice of kind, and only there."""
    marker = f"Required in a {kind} notice, and only there:"
    return [field for field, cell in NOTICE_FIELDS if cell.startswith(marker)]


@pytest.mark.spec("MSG-1")
@pytest.mark.parametrize("kind", KINDS)
def test_a_notice_requires_its_kinds_fields_and_allows_them_only_there(kind: str) -> None:
    [rule] = [
        rule
        for rule in schema("notice.schema.json")["allOf"]
        if rule["if"] == {"properties": {"kind": {"const": kind}}}
    ]

    assert rule["then"] == {"required": kind_fields(kind)}
    assert rule["else"] == {"not": {"anyOf": [{"required": [f]} for f in kind_fields(kind)]}}


@pytest.mark.spec("MSG-1")
def test_every_field_but_kind_and_at_belongs_to_one_kind() -> None:
    owned = [field for kind in KINDS for field in kind_fields(kind)]

    assert sorted(owned) == sorted(
        field for field, _ in NOTICE_FIELDS if field not in {"kind", "at"}
    )


def stream_rows() -> list[str]:
    """The rows of section 9's table of streams."""
    rest = SPEC[SPEC.index("| Stream | Subjects |") :].splitlines()
    end = next(i for i, line in enumerate(rest) if not line.startswith("|"))
    return rest[2:end]


@pytest.mark.spec("MSG-1")
def test_the_notice_kinds_are_the_subjects_ends() -> None:
    kinds = schema("notice.schema.json")["properties"]["kind"]["enum"]
    [orders, notices] = stream_rows()

    assert re.findall(r"`edgar\.orders\.(\w+)`", orders) == ["backfill"]
    assert re.findall(r"`edgar\.notices\.(\w+)`", notices) == kinds == KINDS


SHA = "4b05064b7b26" + "0" * 52
# A field a notice() leaves out.
ABSENT = object()
FILE = "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/"


def order(**changes: Any) -> dict[str, Any]:
    message = {
        "backfill": "apple-2025",
        "uid": "6a1f3c2e-9b0d-4e57-8c21-3f4a5b6c7d8e",
        "cik": 320193,
        "filing_date_from": "2025-01-01",
        "filing_date_to": "2025-12-31",
        "ordered_at": "2026-10-14T18:04:12.345Z",
    }
    return {k: v for k, v in (message | changes).items() if v is not None}


def notice(kind: str, /, **changes: Any) -> dict[str, Any]:
    fields: dict[str, dict[str, Any]] = {
        "filing": {
            "accession_number": "0000320193-25-000079",
            "form": "10-K",
            "filing_date": "2025-10-31",
            "listed_acceptance": "2025-10-31T14:01:26.000Z",
            "header_acceptance": "20251031060126",
            "files": {
                "header": {
                    "feed": "filings",
                    "url": FILE + "0000320193-25-000079-index-headers.html",
                    "sha256": SHA,
                },
                "index": {
                    "feed": "filings",
                    "url": FILE + "0000320193-25-000079-index.html",
                    "sha256": SHA,
                },
                "instance": {
                    "feed": "filings",
                    "url": FILE + "aapl-20250927_htm.xml",
                    "sha256": SHA,
                },
            },
        },
        "gap": {
            "source": "apple-submissions",
            "url": "https://data.sec.gov/submissions/CIK0000320193.json",
            "open_since": "2026-10-14T18:04:22.000Z",
        },
        "backfill": {
            "backfill": "apple-2025",
            "uid": "6a1f3c2e-9b0d-4e57-8c21-3f4a5b6c7d8e",
            "started_at": "2026-10-14T18:04:12.512Z",
            "filings": 236,
            "fetched": 200,
            "failed": 1,
            "completed_at": None,
        },
    }
    message = {"kind": kind, "at": "2026-10-14T18:04:13.000Z", **fields[kind], **changes}
    return {k: v for k, v in message.items() if v is not ABSENT}


@pytest.mark.spec("MSG-1")
@pytest.mark.parametrize(
    "message",
    [
        order(),
        order(cik=9999999999),
        order(filing_date_from="2028-02-29", filing_date_to="2028-02-29"),
        order(added_later=True),
    ],
)
def test_the_order_schema_accepts_an_order(message: dict[str, Any]) -> None:
    assert list(validator("order.schema.json").iter_errors(message)) == []


@pytest.mark.spec("MSG-1")
@pytest.mark.parametrize(
    "message",
    [
        order(backfill=None),
        order(uid=None),
        order(cik=None),
        order(filing_date_from=None),
        order(filing_date_to=None),
        order(ordered_at=None),
        order(backfill="Apple_2025"),
        order(backfill="apple-2025\n"),
        order(uid="6A1F3C2E-9B0D-4E57-8C21-3F4A5B6C7D8E"),
        order(cik=0),
        order(cik=10000000000),
        order(cik="320193"),
        order(filing_date_from="2025-13-01"),
        order(filing_date_from="2026-02-29"),
        order(filing_date_to="2025-12-31T00:00:00.000Z"),
        order(ordered_at="2026-10-14T18:04:12Z"),
        order(ordered_at="2026-02-30T18:04:12.345Z"),
    ],
)
def test_the_order_schema_refuses_a_malformed_order(message: dict[str, Any]) -> None:
    assert list(validator("order.schema.json").iter_errors(message)) != []


@pytest.mark.spec("MSG-1")
@pytest.mark.parametrize(
    "message",
    [
        notice("filing"),
        notice(
            "filing",
            files={
                "header": {"feed": "filings", "url": FILE + "h.html", "sha256": SHA},
                "index": {"feed": "filings", "url": FILE + "i.html", "sha256": SHA},
            },
        ),
        notice("gap"),
        notice("gap", open_since=None),
        notice("backfill"),
        notice("backfill", filings=None, fetched=0, failed=0),
        notice("backfill", completed_at="2026-10-14T18:44:12.000Z"),
        notice("gap", added_later=True),
    ],
)
def test_the_notice_schema_accepts_each_kind_of_notice(message: dict[str, Any]) -> None:
    assert list(validator("notice.schema.json").iter_errors(message)) == []


@pytest.mark.spec("MSG-1")
@pytest.mark.parametrize(
    "message",
    [
        notice("filing", kind="other"),
        notice("filing", at=ABSENT),
        notice("filing", at="2026-10-14T18:04:13Z"),
        *(notice(kind, **{field: ABSENT}) for kind in KINDS for field in kind_fields(kind)),
        notice("gap", accession_number="0000320193-25-000079"),
        notice("filing", source="apple-submissions"),
        notice("filing", fetched=1),
        notice("backfill", open_since=None),
        notice("filing", accession_number="320193-25-000079"),
        notice("filing", header_acceptance="2025-10-31 06:01:26"),
        notice("filing", header_acceptance="20251031060126\n"),
        notice("filing", files={"header": {"feed": "filings", "url": FILE, "sha256": SHA}}),
        notice(
            "filing",
            files={
                "header": {"feed": "filings", "url": FILE, "sha256": SHA},
                "index": {"feed": "filings", "url": "https://data.sec.gov/x", "sha256": SHA},
            },
        ),
        notice(
            "filing",
            files={
                "header": {"feed": "filings", "url": FILE, "sha256": SHA.upper()},
                "index": {"feed": "filings", "url": FILE, "sha256": SHA},
            },
        ),
        notice("gap", source="Apple_Submissions"),
        notice("gap", open_since="2026-10-14"),
        notice("backfill", uid="not-a-uid"),
        notice("backfill", filings=-1),
        notice("backfill", fetched=None),
        notice("backfill", failed=1.5),
        notice("backfill", started_at=None),
        notice("backfill", completed_at="2026-10-14T24:00:00.000Z"),
    ],
)
def test_the_notice_schema_refuses_a_malformed_notice(message: dict[str, Any]) -> None:
    assert list(validator("notice.schema.json").iter_errors(message)) != []
