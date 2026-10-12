"""The store's schemas in spec/schemas/ against spec/snapshots.md's tables.

The record's JSON Schema has the fields of section 6's table, and every
record in data/ validates against it; the table in records.sql has a column
for each; and the job's and the event's JSON Schemas have the fields of
section 10's tables. Each test carries the IDs of the statements it checks,
as @pytest.mark.spec(...); tests/test_spec_coverage.py requires every
statement checked by `tests` or `kind` to have one, or to await a later task.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "spec" / "schemas"
SPEC = (ROOT / "spec" / "snapshots.md").read_text(encoding="utf-8")
# A table row whose first cell is a field in backticks, and its type.
FIELD_ROW = re.compile(r"^\| `(\w+)` \| ([^|]+) \|")
# A column of records.sql's table: a lower-case name at the start of an
# indented line, which no constraint or comment begins with, and its type.
COLUMN = re.compile(r"^    ([a-z_][a-z0-9_]*) +(.+?),?$", re.M)
FETCHER = "fetcher@" + "0123456789abcdef" * 2 + "01234567"
# A timestamp's format, "date-time", holds each day to its month when a
# validator asserts it, as these tests' validators do. jsonschema checks
# that format only with a package the tests don't install, so this parses it.
FORMATS = FormatChecker(formats=())


@FORMATS.checks("date-time", raises=ValueError)
def is_date_time(value: object) -> bool:
    return not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is not None


def table_after(marker: str) -> list[str]:
    """The lines of the first Markdown table after marker in the specification."""
    rest = SPEC[SPEC.index(marker) :].splitlines()
    start = next(i for i, line in enumerate(rest) if line.startswith("|"))
    end = next(i for i in range(start, len(rest)) if not rest[i].startswith("|"))
    return rest[start + 2 : end]


def fields(marker: str) -> list[tuple[str, str]]:
    """Each field of the table after marker, with its type."""
    rows = [FIELD_ROW.match(line) for line in table_after(marker)]
    return [(row[1], row[2].strip()) for row in rows if row]


RECORD_FIELDS = fields("Either way it has these fields:")
JOB_FIELDS = fields("A job's fields:")
EVENT_FIELDS = fields("An event's fields:")


def schema(name: str) -> dict[str, Any]:
    loaded = json.loads((SCHEMAS / name).read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def validator(name: str) -> Draft202012Validator:
    """A validator for the schema, which resolves the others by their $id and
    asserts formats."""
    resources = [Resource.from_contents(schema(path.name)) for path in SCHEMAS.glob("*.json")]
    registry: Registry[Any] = Registry().with_resources(
        (resource.contents["$id"], resource) for resource in resources
    )
    return Draft202012Validator(schema(name), registry=registry, format_checker=FORMATS)


def records() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for log in sorted((ROOT / "data" / "feeds").glob("*/versions.jsonl"))
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def new_record(**changes: Any) -> dict[str, Any]:
    """A record as the fetcher writes it under version 1.0.0, with every field."""
    sha256 = "e89635068cd46772d1d3859ce2bc839fab781630787943a1874f258fa354e7a9"
    record = {
        "feed": "submissions",
        "url": "https://data.sec.gov/submissions/CIK0000320193.json",
        "requested_at": "2026-10-12T18:04:12.345Z",
        "received_at": "2026-10-12T18:04:12.512Z",
        "attempts": 1,
        "status": 200,
        "error": None,
        "final_url": None,
        "date": "Mon, 12 Oct 2026 18:04:12 GMT",
        "etag": None,
        "last_modified": None,
        "content_type": "application/json",
        "sha256": sha256,
        "bytes": 163743,
        "object": f"objects/{sha256[:2]}/{sha256}.gz",
        "tool": FETCHER,
    }
    return record | changes


def job(**changes: Any) -> dict[str, Any]:
    """A poll's job."""
    poll = {
        "id": "0192f1d2-5b7e-7c3a-9f1e-2d4c6b8a0e13",
        "kind": "poll",
        "feed": "submissions",
        "url": "https://data.sec.gov/submissions/CIK0000320193.json",
        "source": "apple-submissions",
        "queued_at": "2026-10-12T18:04:12.100Z",
        "expires_at": "2026-10-12T18:04:22.000Z",
    }
    return {k: v for k, v in (poll | changes).items() if v is not None}


@pytest.mark.spec("REC-13", "JOB-1")
@pytest.mark.parametrize("name", ["record.schema.json", "job.schema.json", "event.schema.json"])
def test_each_schema_is_a_json_schema(name: str) -> None:
    Draft202012Validator.check_schema(schema(name))


@pytest.mark.spec("REC-13")
def test_the_record_schema_has_the_tables_fields_in_order() -> None:
    record = schema("record.schema.json")

    assert list(record["properties"]) == [field for field, _ in RECORD_FIELDS]
    assert record["required"] == [
        field for field, _ in RECORD_FIELDS if field not in ("received_at", "date")
    ]


@pytest.mark.spec("REC-13")
def test_every_record_in_the_store_validates() -> None:
    record = validator("record.schema.json")
    stored = records()

    assert stored
    assert [list(record.iter_errors(each)) for each in stored] == [[] for _ in stored]


@pytest.mark.spec("REC-13")
@pytest.mark.parametrize(
    "changes",
    [
        {},
        {"tool": "scripts/snapshot.py@" + "a" * 40 + "+dirty"},
        {"tool": "scripts/snapshot.py@unknown"},
        {"status": None, "received_at": None, "date": None, "error": "no response: timeout"},
        {"requested_at": "2028-02-29T23:59:59.999Z", "received_at": "2028-03-01T00:00:00.000Z"},
    ],
)
def test_the_record_schema_accepts_each_writers_record(changes: dict[str, Any]) -> None:
    assert list(validator("record.schema.json").iter_errors(new_record(**changes))) == []


@pytest.mark.spec("REC-13")
@pytest.mark.parametrize(
    "changes",
    [
        {"received_at": "2026-10-12T18:04:12Z"},
        {"requested_at": "2026-10-12 18:04:12.345Z"},
        {"requested_at": "2026-02-30T25:61:61.000Z"},
        {"requested_at": "2026-02-30T18:04:12.345Z"},
        {"requested_at": "2026-02-29T18:04:12.345Z"},
        {"received_at": "2026-13-12T18:04:12.512Z"},
        {"received_at": "2026-10-12T24:00:00.000Z"},
        {"received_at": "2026-12-31T23:59:60.000Z"},
        {"tool": "fetcher@0123456"},
        {"tool": "edgar-asof@0.1.0"},
        {"sha256": "E8" * 32},
        {"bytes": -1},
        {"attempts": 0},
        {"feed": "Submissions"},
        {"date": 1760292252},
    ],
)
def test_the_record_schema_refuses_a_field_of_the_wrong_form(changes: dict[str, Any]) -> None:
    assert list(validator("record.schema.json").iter_errors(new_record(**changes))) != []


def wrong_types() -> list[tuple[str, object]]:
    """For each field of section 6's table, values of JSON types its type
    excludes: a string for an integer, a number for anything else, and null
    for a field that can't be null."""
    wrong: list[tuple[str, object]] = []
    for field, kind in RECORD_FIELDS:
        wrong.append((field, "1" if kind.startswith("integer") else 1))
        if "null" not in kind:
            wrong.append((field, None))
    return wrong


@pytest.mark.spec("REC-13")
@pytest.mark.parametrize(
    ("field", "value"), wrong_types(), ids=[f"{f}={v!r}" for f, v in wrong_types()]
)
def test_the_record_schema_refuses_a_field_of_the_wrong_type(field: str, value: object) -> None:
    assert list(validator("record.schema.json").iter_errors(new_record(**{field: value}))) != []


def columns() -> dict[str, str]:
    sql = (SCHEMAS / "records.sql").read_text(encoding="utf-8")
    [table] = re.findall(r"^CREATE TABLE records \((.*?)^\);", sql, re.M | re.S)
    return dict(COLUMN.findall(table))


@pytest.mark.spec("STORE-1")
def test_the_table_has_a_column_for_each_field_and_an_id_and_a_job() -> None:
    assert list(columns()) == ["id", "job", *(field for field, _ in RECORD_FIELDS)]


@pytest.mark.spec("STORE-1")
def test_the_id_numbers_the_records_and_each_job_has_one() -> None:
    assert columns()["id"].startswith("bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY")
    assert columns()["job"] == "uuid NOT NULL UNIQUE"


@pytest.mark.spec("STORE-1")
def test_each_column_has_its_fields_type() -> None:
    expected = {
        "timestamp": "timestamp (3) with time zone NOT NULL",
        "timestamp or null": "timestamp (3) with time zone",
        "integer ≥ 1": "integer NOT NULL",
        "integer or null": "integer",
        "integer ≥ 0, or null": "bigint",
    }
    for field, kind in RECORD_FIELDS:
        sql = columns()[field]
        want = expected.get(kind, "text NOT NULL" if "null" not in kind else "text")

        assert sql.startswith(want), field
        assert ("NOT NULL" in sql) == ("null" not in kind), field


@pytest.mark.spec("JOB-1")
@pytest.mark.parametrize(
    ("name", "table"), [("job.schema.json", JOB_FIELDS), ("event.schema.json", EVENT_FIELDS)]
)
def test_each_message_schema_has_its_tables_fields(name: str, table: list[tuple[str, str]]) -> None:
    message = schema(name)

    assert list(message["properties"]) == [field for field, _ in table]
    assert message["required"] == [field for field, kind in table if kind.startswith("Required:")]


@pytest.mark.spec("JOB-1")
def test_the_job_kinds_are_the_subjects_ends() -> None:
    kinds = schema("job.schema.json")["properties"]["kind"]["enum"]
    [jobs, events] = table_after("Both are JSON messages in NATS JetStream")

    assert re.findall(r"`edgar\.jobs\.(\w+)`", jobs) == kinds
    assert re.findall(r"`edgar\.records\.(\w+)`", events) == kinds


@pytest.mark.spec("JOB-1")
@pytest.mark.parametrize(
    "message",
    [
        job(),
        job(expires_at=None),
        job(kind="filing", source=None, expires_at=None),
        job(kind="backfill", source=None, backfill="apple-2025", expires_at=None),
        job(url="https://www.sec.gov/Archives/edgar/data/320193/"),
        job(queued_at="2028-02-29T23:59:59.999Z", expires_at="2028-03-01T00:00:09.999Z"),
    ],
)
def test_the_job_schema_accepts_each_kind_of_job(message: dict[str, Any]) -> None:
    assert list(validator("job.schema.json").iter_errors(message)) == []


@pytest.mark.spec("JOB-1")
@pytest.mark.parametrize(
    "message",
    [
        job(source=None),
        job(backfill="apple-2025"),
        job(kind="filing"),
        job(kind="backfill", source=None),
        job(kind="other"),
        job(url="http://data.sec.gov/submissions/CIK0000320193.json"),
        job(url="https://example.com/"),
        job(id="0192F1D2-5B7E-7C3A-9F1E-2D4C6B8A0E13"),
        job(queued_at=None),
        job(queued_at="2026-10-12T18:04:12Z"),
        job(queued_at="2026-10-12T24:04:12.100Z"),
        job(expires_at="2026-10-12T18:04:22Z"),
        job(expires_at="2026-02-30T18:04:22.000Z"),
        job(source="Apple_Submissions"),
        job(feed="Submissions"),
        job(kind="backfill", source=None, backfill="Apple_2025", expires_at=None),
    ],
)
def test_the_job_schema_refuses_a_malformed_job(message: dict[str, Any]) -> None:
    assert list(validator("job.schema.json").iter_errors(message)) != []


@pytest.mark.spec("JOB-1")
def test_the_event_schema_accepts_a_record_with_every_field() -> None:
    event = {"id": 1, "job": job(), "record": new_record()}

    assert list(validator("event.schema.json").iter_errors(event)) == []


@pytest.mark.spec("JOB-1")
@pytest.mark.parametrize(
    "event",
    [
        {"id": 1, "job": job(), "record": {**new_record(), "received_at": "now"}},
        {"id": 1, "job": job(), "record": {k: v for k, v in new_record().items() if k != "date"}},
        {
            "id": 1,
            "job": job(),
            "record": {k: v for k, v in new_record().items() if k != "received_at"},
        },
        {"id": 0, "job": job(), "record": new_record()},
        {"id": 1, "job": job(source=None), "record": new_record()},
        {"id": 1, "record": new_record()},
    ],
)
def test_the_event_schema_refuses_a_malformed_event(event: dict[str, Any]) -> None:
    assert list(validator("event.schema.json").iter_errors(event)) != []
