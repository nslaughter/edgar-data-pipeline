"""The fake EDGAR's schemas in spec/schemas/ against spec/fake-edgar.md's tables.

The cue's JSON Schema has the fields of section 5's tables, and holds a
response to the combinations the section gives; and the request log's has
the fields of section 6's table. Each test carries the IDs of the statements
it checks, as @pytest.mark.spec(...); tests/test_spec_coverage.py requires
every statement checked by `tests` or `kind` to have one, or to await a
later task.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import extend

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "spec" / "schemas"
SPEC = (ROOT / "spec" / "fake-edgar.md").read_text(encoding="utf-8")
CUE = "fake-edgar-cue.schema.json"
REQUEST = "fake-edgar-request.schema.json"
# A table row whose first cell is a name in backticks, and its second cell.
ROW = re.compile(r"^\| `(\w+)` \| ([^|]+) \|")
# A timestamp's format holds each day to its month when a validator asserts
# it, as these tests' validators do. jsonschema checks it only with a package
# the tests don't install, so this parses it.
FORMATS = FormatChecker(formats=())
SHA256 = "73a86c6aedc31f77cac2ea4df5f80f0b3bd7e6eb58bb4e01444fbedf3afb9c43"
URL = "https://data.sec.gov/submissions/CIK0000320193.json"
ABSENT = object()


@FORMATS.checks("date-time", raises=ValueError)
def is_date_time(value: object) -> bool:
    return not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is not None


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


def required(table: list[tuple[str, str]]) -> list[str]:
    return [field for field, kind in table if kind.startswith("Required:")]


CUE_FIELDS = rows_after("A cue's fields:")
RESPONSE_FIELDS = rows_after("A response's fields:")
REQUEST_FIELDS = rows_after("with these fields:")


@pytest.mark.spec("CUE-3", "REQ-3")
@pytest.mark.parametrize("name", [CUE, REQUEST])
def test_each_schema_is_a_json_schema(name: str) -> None:
    Draft202012Validator.check_schema(schema(name))


@pytest.mark.spec("CUE-3")
def test_the_cue_schema_has_its_tables_fields() -> None:
    cue = schema(CUE)
    response = cue["$defs"]["response"]

    assert list(cue["properties"]) == [field for field, _ in CUE_FIELDS]
    assert cue["required"] == required(CUE_FIELDS)
    assert list(response["properties"]) == [field for field, _ in RESPONSE_FIELDS]
    assert response.get("required", []) == required(RESPONSE_FIELDS)


@pytest.mark.spec("REQ-3")
def test_the_request_schema_has_its_tables_fields_each_required() -> None:
    request = schema(REQUEST)

    assert list(request["properties"]) == [field for field, _ in REQUEST_FIELDS]
    assert request["required"] == [field for field, _ in REQUEST_FIELDS]
    assert required(REQUEST_FIELDS) == [field for field, _ in REQUEST_FIELDS]


def cue(url: Any = URL, **response: Any) -> dict[str, Any]:
    """A cue of one response, or of the response given; ABSENT leaves a field out."""
    fields = {"url": url, "responses": [response or {"status": 403}]}
    return {k: v for k, v in fields.items() if v is not ABSENT}


@pytest.mark.spec("CUE-3")
@pytest.mark.parametrize(
    "message",
    [
        cue(),
        cue(url=ABSENT),
        cue(url="https://www.sec.gov/Archives/edgar/data/320193/"),
        cue(status=429, headers={"Retry-After": "1"}),
        cue(status=503, headers={}, body="Service Unavailable"),
        cue(status=200, body=""),
        cue(sha256=SHA256),
        cue(cut=True),
        cue(sha256=SHA256, cut=True),
        cue(drop=True),
        {
            "url": URL,
            "responses": [{"status": 429}, {"cut": True}, {"drop": True}, {"status": 403}],
        },
        {"responses": [{"status": 403}], "added_later": True},
    ],
)
def test_the_cue_schema_accepts_a_cue(message: dict[str, Any]) -> None:
    assert list(validator(CUE).iter_errors(message)) == []


@pytest.mark.spec("CUE-3")
@pytest.mark.parametrize(
    "message",
    [
        {"url": URL},
        {"url": URL, "responses": []},
        {"url": URL, "responses": {"status": 403}},
        cue(url="http://data.sec.gov/submissions/CIK0000320193.json"),
        cue(url="https://example.com/submissions/CIK0000320193.json"),
        cue(url="https://sec.gov/"),
        cue(status=99),
        cue(status=600),
        cue(status="403"),
        cue(status=403.5),
        cue(status=429, headers={"Retry-After": 1}),
        cue(status=429, headers=["Retry-After: 1"]),
        cue(status=200, body=0),
        cue(sha256=SHA256.upper()),
        cue(sha256=SHA256[:-1]),
        cue(cut=False),
        cue(drop=False),
        cue(status=403, cut=True),
        cue(status=403, sha256=SHA256),
        cue(status=403, drop=True),
        cue(sha256=SHA256, drop=True),
        cue(cut=True, drop=True),
        cue(headers={"Retry-After": "1"}),
        cue(body="refused"),
        cue(sha256=SHA256, headers={"Retry-After": "1"}),
        cue(cut=True, body="cut"),
        cue(drop=True, headers={}),
        {"url": URL, "responses": [{}]},
        {"url": URL, "responses": [{"stauts": 403}]},
    ],
)
def test_the_cue_schema_refuses_a_malformed_cue(message: dict[str, Any]) -> None:
    assert list(validator(CUE).iter_errors(message)) != []


def entry(**changes: Any) -> dict[str, Any]:
    """An entry of the request log, with changes; ABSENT leaves a field out."""
    fields: dict[str, Any] = {
        "n": 1,
        "received_at": "2026-10-18T10:22:42.614Z",
        "client": "10.244.1.7",
        "method": "GET",
        "target": "/submissions/CIK0000320193.json",
        "url": URL,
        "headers": {
            "host": "data.sec.gov",
            "user-agent": "Test Runner test@example.com",
            "accept-encoding": "gzip",
        },
        "status": 200,
        "cued": False,
        **changes,
    }
    return {k: v for k, v in fields.items() if v is not ABSENT}


@pytest.mark.spec("REQ-3")
@pytest.mark.parametrize(
    "message",
    [
        entry(),
        entry(n=2**40),
        entry(client="::1"),
        entry(method="POST", target="/", url=None, status=405),
        entry(target="/missing.json", url=None, status=404),
        entry(
            target="/cgi-bin/browse-edgar?action=getcurrent&output=atom",
            url="https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&output=atom",
        ),
        entry(headers={}),
        entry(headers={"if-none-match": '"a", "b"', "x-custom_header.1": "v"}),
        entry(status=None, cued=True),
        entry(status=403, cued=True),
        entry(added_later=True),
    ],
)
def test_the_request_schema_accepts_an_entry(message: dict[str, Any]) -> None:
    assert list(validator(REQUEST).iter_errors(message)) == []


@pytest.mark.spec("REQ-3")
@pytest.mark.parametrize(
    "message",
    [
        *(entry(**{field: ABSENT}) for field, _ in REQUEST_FIELDS),
        entry(n=0),
        entry(n="1"),
        entry(received_at="2026-10-18T10:22:42Z"),
        entry(received_at="2026-10-18T10:22:42.614+00:00"),
        entry(received_at="2026-02-30T10:22:42.614Z"),
        entry(client=None),
        entry(method=None),
        entry(target=None),
        entry(url="http://data.sec.gov/submissions/CIK0000320193.json"),
        entry(url="https://127.0.0.1:8443/submissions/CIK0000320193.json"),
        entry(headers={"User-Agent": "Test Runner test@example.com"}),
        entry(headers={"user agent": "Test Runner test@example.com"}),
        entry(headers={"accept-encoding": ["gzip"]}),
        entry(headers=[["host", "data.sec.gov"]]),
        entry(status=99),
        entry(status=600),
        entry(status="200"),
        entry(cued=None),
        entry(cued="false"),
    ],
)
def test_the_request_schema_refuses_a_malformed_entry(message: dict[str, Any]) -> None:
    assert list(validator(REQUEST).iter_errors(message)) != []
