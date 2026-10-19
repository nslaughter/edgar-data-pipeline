"""The fake EDGAR against spec/fake-edgar.md: its schemas, and the service on one machine.

The cue's JSON Schema has the fields of section 5's tables, and holds a
response to the combinations the section gives; and the request log's has
the fields of section 6's table. The fake EDGAR, run in this process on a
data/ of each test's own, serves what that holds, gives what a test cues,
logs every request to its server, and answers its admin port as sections 4
to 7 say; and the checks of section 8 report what breaks the fetching rules,
and pass snapshot.py's requests to it. The tests reach no network: the fake
EDGAR answers on 127.0.0.1. Each test carries the IDs of the statements it
checks, as @pytest.mark.spec(...); tests/test_spec_coverage.py requires every
statement checked by `tests` or `kind` to have one, or to await a later task.
"""

from __future__ import annotations

import copy
import gzip
import hashlib
import http.client
import io
import json
import os
import re
import signal
import socket
import ssl
import struct
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import pytest
import snapshot
from hypothesis import given, settings
from hypothesis import strategies as st
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import extend

from edgar_pipeline.fake_edgar import checks, cues
from edgar_pipeline.fake_edgar.server import FakeEdgar
from edgar_pipeline.fake_edgar.store import StoreError

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


# Cues the schema accepts, and malformed ones it refuses, which the admin
# port's tests give it too (ADMIN-1).
ACCEPTED = [
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
]
REFUSED = [
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
]


@pytest.mark.spec("CUE-3")
@pytest.mark.parametrize("message", ACCEPTED)
def test_the_cue_schema_accepts_a_cue(message: dict[str, Any]) -> None:
    assert list(validator(CUE).iter_errors(message)) == []


@pytest.mark.spec("CUE-3")
@pytest.mark.parametrize("message", REFUSED)
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


# THE FAKE EDGAR, ON ONE MACHINE
#
# Each test runs the fake EDGAR in this process, on a data/ of its own, and
# sends it requests at 127.0.0.1, over plain HTTP unless it gives the fake
# EDGAR a certificate (SERVE-5).

T0 = datetime(2026, 10, 7, 17, 40, tzinfo=UTC)
SUBMISSIONS = URL
INDEX = "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR4/index.json"
FACTS = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627_htm.xml"
FILING = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/index.htm"
FIRST = b'{"cik": "0000320193", "filings": "first"}'
SECOND = b'{"cik": "0000320193", "filings": "second"}'
LISTING = b'{"directory": {"item": ["master.20261001.idx"]}}'
# Large enough that half its bytes, gzip-encoded or not, are many.
INSTANCE = "\n".join(
    f'<us-gaap:Assets id="f{i}">{i * 7919 % 10007}</us-gaap:Assets>' for i in range(2000)
).encode()
PAGE = b"<html><body>Filing index</body></html>"
ETAG = '"8a9eedb1e2028f772a195680c9484120-gzip"'
MODIFIED = "Wed, 07 Oct 2026 05:45:15 GMT"
UA = "Test Runner test@example.com"
TOOL = "scripts/snapshot.py@" + "0" * 40


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def fetched(
    data: Path,
    url: str,
    body: bytes,
    *,
    at: float,
    status: int = 200,
    feed: str = "feed",
    etag: str | None = None,
    last_modified: str | None = None,
    content_type: str | None = "application/json",
) -> None:
    """Adds a fetch's record and object to data/, as snapshot.py would: a
    200 that got body, or a 304 that confirmed it, at seconds after T0."""
    digest = sha(body)
    path = data / "objects" / digest[:2] / f"{digest}.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(body, mtime=0))
    when = (T0 + timedelta(seconds=at)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    record = {
        "feed": feed,
        "url": url,
        "requested_at": when,
        "received_at": when,
        "attempts": 1,
        "status": status,
        "error": None,
        "final_url": None,
        "date": None,
        "etag": etag,
        "last_modified": last_modified,
        "content_type": content_type,
        "sha256": digest,
        "bytes": len(body),
        "object": path.relative_to(data).as_posix(),
        "tool": TOOL,
    }
    log = data / "feeds" / feed / "versions.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record) + "\n")


def store(data: Path) -> Path:
    """A git store of four URLs, one of them with two versions."""
    fetched(data, SUBMISSIONS, FIRST, at=0, feed="submissions")
    fetched(data, SUBMISSIONS, SECOND, at=60, feed="submissions")
    fetched(data, INDEX, LISTING, at=10, feed="daily-index", etag=ETAG, last_modified=MODIFIED)
    fetched(data, FACTS, INSTANCE, at=20, feed="filings", content_type="text/xml")
    fetched(data, FILING, PAGE, at=30, feed="filings", content_type="text/html")
    return data


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return store(tmp_path / "data")


@pytest.fixture
def fake(data: Path) -> Iterator[FakeEdgar]:
    edgar = FakeEdgar(data)
    edgar.start()
    yield edgar
    edgar.stop()


def target(url: str) -> str:
    return re.sub(r"^https://[^/]+", "", url)


def local(fake: FakeEdgar, url: str) -> str:
    """The URL at the fake EDGAR's own address, as a test on one machine sends it."""
    return f"http://127.0.0.1:{fake.port}{target(url)}"


@dataclass
class Answer:
    status: int | None  # None if no response arrived
    headers: dict[str, str]  # by name in lower case
    names: list[str]  # the headers' names, as sent
    body: bytes
    cut: bool = False  # the body ended before its Content-Length


def get(
    port: int, path: str, headers: dict[str, str] | None = None, *, method: str = "GET"
) -> Answer:
    """One request, with only the headers given, and Host unless they name one."""
    given = headers or {}
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.putrequest(
            method,
            path,
            skip_host="host" in {name.lower() for name in given},
            skip_accept_encoding=True,
        )
        for name, value in given.items():
            connection.putheader(name, value)
        connection.endheaders()
        try:
            response = connection.getresponse()
        except (http.client.RemoteDisconnected, ConnectionError):
            return Answer(None, {}, [], b"")
        try:
            body, cut = response.read(), False
        except http.client.IncompleteRead as err:
            body, cut = err.partial, True
        sent = response.getheaders()
        return Answer(
            response.status, {k.lower(): v for k, v in sent}, [k for k, _ in sent], body, cut
        )
    finally:
        connection.close()


def admin(port: int, method: str, path: str, body: bytes | None = None) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body=body)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def add_cue(fake: FakeEdgar, cue: Any) -> int:
    return admin(fake.admin_port, "POST", "/cues", json.dumps(cue).encode())[0]


def requests(port: int, after: int | None = None) -> list[dict[str, Any]]:
    path = "/requests" if after is None else f"/requests?after={after}"
    status, body = admin(port, "GET", path)
    assert status == 200, body
    entries = json.loads(body)
    assert isinstance(entries, list)
    return entries


def tree(path: Path) -> dict[str, tuple[bytes, int]]:
    """Every file under path, with its bytes and its time of modification."""
    return {
        file.relative_to(path).as_posix(): (file.read_bytes(), file.stat().st_mtime_ns)
        for file in sorted(path.rglob("*"))
        if file.is_file()
    }


# SERVE


@pytest.mark.spec("SERVE-1")
def test_it_serves_the_store_as_it_read_it_at_its_start(fake: FakeEdgar, data: Path) -> None:
    fetched(data, SUBMISSIONS, b'{"filings": "third"}', at=120, feed="submissions")
    for path in (data / "objects").rglob("*.gz"):
        path.unlink()

    answer = get(fake.port, target(SUBMISSIONS))

    assert (answer.status, answer.body) == (200, SECOND)
    assert get(fake.port, target(INDEX)).body == LISTING


@pytest.mark.spec("SERVE-1")
def test_it_changes_nothing_in_the_store(data: Path) -> None:
    before = tree(data)
    edgar = FakeEdgar(data)
    edgar.start()
    try:
        assert add_cue(edgar, {"url": FACTS, "responses": [{"cut": True}, {"drop": True}]}) == 204
        for path in (SUBMISSIONS, INDEX, FACTS, FACTS, FACTS, FILING, "/missing"):
            get(edgar.port, target(path), {"Accept-Encoding": "gzip"})
    finally:
        edgar.stop()

    assert tree(data) == before


@pytest.mark.spec("SERVE-1")
def test_it_refuses_a_store_whose_urls_differ_only_in_their_host(data: Path) -> None:
    fetched(data, "https://www.sec.gov/submissions/CIK0000320193.json", FIRST, at=90)

    with pytest.raises(StoreError, match="differ only in their host"):
        FakeEdgar(data)


@pytest.mark.spec("SERVE-2")
@pytest.mark.parametrize(
    ("host", "served"),
    [
        (None, True),
        ("data.sec.gov", True),
        ("DATA.sec.gov:443", True),
        ("www.sec.gov", False),
        ("127.0.0.1:8443", True),
        ("example.com", True),
    ],
)
def test_a_get_is_for_the_stored_url_it_names(
    fake: FakeEdgar, host: str | None, served: bool
) -> None:
    headers = {} if host is None else {"Host": host}

    answer = get(fake.port, target(SUBMISSIONS), headers)

    assert answer.status == (200 if served else 404)
    assert requests(fake.admin_port)[-1]["url"] == (SUBMISSIONS if served else None)


@pytest.mark.spec("SERVE-2")
@pytest.mark.parametrize("path", ["/missing.json", "/submissions/", "/", target(INDEX) + "?x=1"])
def test_a_get_for_no_stored_url_gets_a_404_with_no_body(fake: FakeEdgar, path: str) -> None:
    answer = get(fake.port, path)

    assert (answer.status, answer.body) == (404, b"")
    assert requests(fake.admin_port)[-1]["url"] is None


@pytest.mark.spec("SERVE-2")
@pytest.mark.parametrize("method", ["HEAD", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"])
def test_any_other_method_gets_a_405_with_no_body(fake: FakeEdgar, method: str) -> None:
    answer = get(fake.port, target(SUBMISSIONS), method=method)

    assert (answer.status, answer.body) == (405, b"")
    [entry] = requests(fake.admin_port)
    assert (entry["method"], entry["url"], entry["status"]) == (method, None, 405)


@pytest.mark.spec("SERVE-3")
def test_a_get_gets_the_latest_version_with_its_records_headers(fake: FakeEdgar) -> None:
    answer = get(fake.port, target(INDEX))

    assert (answer.status, answer.body) == (200, LISTING)
    assert sorted(answer.names) == [
        "Content-Length",
        "Content-Type",
        "Date",
        "ETag",
        "Last-Modified",
    ]
    assert answer.headers["etag"] == ETAG
    assert answer.headers["last-modified"] == MODIFIED
    assert answer.headers["content-type"] == "application/json"
    assert answer.headers["content-length"] == str(len(LISTING))


@pytest.mark.spec("SERVE-3")
def test_only_the_headers_a_record_has_are_sent(tmp_path: Path) -> None:
    fetched(tmp_path / "data", FILING, PAGE, at=0, content_type=None)
    edgar = FakeEdgar(tmp_path / "data")
    edgar.start()
    try:
        answer = get(edgar.port, target(FILING))
    finally:
        edgar.stop()

    assert (answer.status, answer.body) == (200, PAGE)
    assert sorted(answer.names) == ["Content-Length", "Date"]


@pytest.mark.spec("SERVE-3")
def test_the_latest_version_and_its_headers_are_by_requested_at(tmp_path: Path) -> None:
    data = tmp_path / "data"
    # The later fetches, in the log first; then an earlier version fetched
    # again, without a Content-Type, and confirmed by a 304; then, last in
    # the log but earlier in time, a record of each version.
    fetched(data, INDEX, SECOND, at=200, etag='"second"')
    fetched(data, INDEX, FIRST, at=100, etag='"first"', content_type="text/plain")
    fetched(data, INDEX, FIRST, at=300, etag='"again"', content_type=None)
    fetched(data, INDEX, FIRST, at=400, status=304, etag='"confirmed"', content_type=None)
    fetched(data, INDEX, FIRST, at=350, etag='"stale"', content_type="text/plain")
    fetched(data, INDEX, SECOND, at=250, etag='"second"')
    edgar = FakeEdgar(data)
    edgar.start()
    try:
        answer = get(edgar.port, target(INDEX))
    finally:
        edgar.stop()

    assert answer.body == FIRST
    assert answer.headers["etag"] == '"confirmed"'
    assert "content-type" not in answer.headers


@pytest.mark.spec("SERVE-3")
def test_the_date_is_the_fake_edgars_clock_as_rfc_9110_gives_it(fake: FakeEdgar) -> None:
    before = datetime.now(UTC).replace(microsecond=0)

    date = get(fake.port, target(INDEX)).headers["date"]

    assert re.fullmatch(r"[A-Z][a-z]{2}, \d{2} [A-Z][a-z]{2} \d{4} \d{2}:\d{2}:\d{2} GMT", date)
    assert before <= parsedate_to_datetime(date) <= datetime.now(UTC)


@pytest.mark.spec("SERVE-3")
@pytest.mark.parametrize(
    ("accept", "encoded"),
    [
        (None, False),
        ("gzip", True),
        ("x-gzip", True),
        ("GZIP;q=0.5, br", True),
        ("br, *", True),
        ("deflate, gzip;q=1.0", True),
        ("", False),
        ("identity", False),
        ("deflate, br", False),
        ("gzip;q=0", False),
        ("gzip;q=0.000, *", False),
        ("*;q=0", False),
        ("gzip;q=2", False),
    ],
)
def test_the_body_is_gzip_encoded_only_when_the_request_accepts_it(
    fake: FakeEdgar, accept: str | None, encoded: bool
) -> None:
    headers = {} if accept is None else {"Accept-Encoding": accept}

    answer = get(fake.port, target(FACTS), headers)

    stored = gzip.compress(INSTANCE, mtime=0)
    assert answer.status == 200
    assert answer.body == (stored if encoded else INSTANCE)
    assert answer.headers.get("content-encoding") == ("gzip" if encoded else None)
    assert answer.headers["content-length"] == str(len(answer.body))


@pytest.mark.spec("SERVE-4")
@pytest.mark.parametrize(
    "headers",
    [
        {"If-None-Match": ETAG},
        {"If-None-Match": "W/" + ETAG},
        {"If-None-Match": f'"other", {ETAG}'},
        {"If-None-Match": '"with, a comma", W/' + ETAG},
        {"If-None-Match": "*"},
        {"If-Modified-Since": MODIFIED},
        {"If-Modified-Since": "Thu, 08 Oct 2026 00:00:00 GMT"},
        {"If-Modified-Since": "Wednesday, 07-Oct-26 05:45:15 GMT"},
        {"If-Modified-Since": "Wed Oct  7 05:45:15 2026"},
        {"If-None-Match": ETAG, "If-Modified-Since": "Mon, 01 Jan 2001 00:00:00 GMT"},
    ],
)
def test_a_conditional_get_for_the_version_it_would_get_gets_a_304(
    fake: FakeEdgar, headers: dict[str, str]
) -> None:
    answer = get(fake.port, target(INDEX), headers | {"Accept-Encoding": "gzip"})

    assert (answer.status, answer.body) == (304, b"")
    assert sorted(answer.names) == ["Date", "ETag", "Last-Modified"]
    assert (answer.headers["etag"], answer.headers["last-modified"]) == (ETAG, MODIFIED)


@pytest.mark.spec("SERVE-4")
@pytest.mark.parametrize(
    "headers",
    [
        {"If-None-Match": '"8a9eedb1e2028f772a195680c9484120"'},
        {"If-None-Match": '"other"'},
        {"If-None-Match": "8a9eedb1e2028f772a195680c9484120-gzip"},
        {"If-None-Match": '"other"', "If-Modified-Since": "Thu, 08 Oct 2026 00:00:00 GMT"},
        {"If-Modified-Since": "Wed, 07 Oct 2026 05:45:14 GMT"},
        {"If-Modified-Since": "yesterday"},
    ],
)
def test_a_conditional_get_for_another_version_gets_the_version(
    fake: FakeEdgar, headers: dict[str, str]
) -> None:
    answer = get(fake.port, target(INDEX), headers)

    assert (answer.status, answer.body) == (200, LISTING)


@pytest.mark.spec("SERVE-4")
@pytest.mark.parametrize(
    "headers",
    [
        {"If-None-Match": '"anything"'},
        {"If-Modified-Since": "Thu, 08 Oct 2026 00:00:00 GMT"},
    ],
)
def test_a_version_without_validators_matches_no_list_and_no_date(
    fake: FakeEdgar, headers: dict[str, str]
) -> None:
    answer = get(fake.port, target(SUBMISSIONS), headers)

    assert (answer.status, answer.body) == (200, SECOND)


@pytest.mark.spec("SERVE-4")
def test_any_version_matches_a_star(fake: FakeEdgar) -> None:
    answer = get(fake.port, target(SUBMISSIONS), {"If-None-Match": "*"})

    assert (answer.status, sorted(answer.names)) == (304, ["Date"])


@pytest.mark.spec("SERVE-3", "SERVE-4")
def test_a_validator_beyond_ascii_comes_back_as_its_record_holds_it(tmp_path: Path) -> None:
    # snapshot.py reads a header's octets as ISO-8859-1, as http.client does,
    # so this record's ETag was the octets 22 E9 22.
    fetched(tmp_path / "data", INDEX, LISTING, at=0, etag='"é"')
    edgar = FakeEdgar(tmp_path / "data")
    edgar.start()
    try:
        answer = get(edgar.port, target(INDEX))
        unchanged = get(edgar.port, target(INDEX), {"If-None-Match": answer.headers["etag"]})
    finally:
        edgar.stop()

    assert (answer.status, answer.headers["etag"]) == (200, '"é"')
    assert (unchanged.status, unchanged.headers["etag"]) == (304, '"é"')


@pytest.mark.spec("SERVE-3", "SERVE-4")
@pytest.mark.parametrize("etag", ['"v1" ', 'W/"v1"\t', '"v1" \t '])
def test_a_validator_ending_in_whitespace_comes_back_and_matches(tmp_path: Path, etag: str) -> None:
    # http.client drops the whitespace before a header's value but keeps
    # what follows it, so snapshot.py can store an ETag that ends in some.
    assert http.client.parse_headers(io.BytesIO(f"ETag: {etag}\r\n\r\n".encode()))["etag"] == etag
    fetched(tmp_path / "data", INDEX, LISTING, at=0, etag=etag)
    edgar = FakeEdgar(tmp_path / "data")
    edgar.start()
    try:
        answer = get(edgar.port, target(INDEX))
        unchanged = get(edgar.port, target(INDEX), {"If-None-Match": answer.headers["etag"]})
        stripped = get(edgar.port, target(INDEX), {"If-None-Match": '"v1"'})
    finally:
        edgar.stop()

    assert (answer.status, answer.headers["etag"]) == (200, etag)
    assert (unchanged.status, unchanged.headers["etag"]) == (304, etag)
    assert stripped.status == 304


def certificate(directory: Path) -> tuple[Path, Path, Path]:
    """An authority's certificate, and a certificate and key it issues for
    the SEC's hosts, as the release's authority does in the cluster (FAKE-3)."""
    ca_key, ca, key, csr, crt = (
        directory / name for name in ("ca.key", "ca.crt", "tls.key", "tls.csr", "tls.crt")
    )
    extensions = directory / "extensions.cnf"
    extensions.write_text(
        "subjectAltName=DNS:www.sec.gov,DNS:data.sec.gov\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\n"
        "extendedKeyUsage=serverAuth\n"
        "authorityKeyIdentifier=keyid\n"
        "subjectKeyIdentifier=hash\n"
    )
    curve = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes"]
    commands = [
        [
            *("openssl", "req", "-x509", *curve, "-keyout", ca_key, "-out", ca, "-days", "1"),
            *("-subj", "/CN=Test authority", "-addext", "keyUsage=critical,keyCertSign,cRLSign"),
        ],
        ["openssl", "req", *curve, "-keyout", key, "-out", csr, "-subj", "/CN=www.sec.gov"],
        [
            *("openssl", "x509", "-req", "-in", csr, "-CA", ca, "-CAkey", ca_key),
            *("-set_serial", "1", "-days", "1", "-out", crt, "-extfile", extensions),
        ],
    ]
    for command in commands:
        subprocess.run([str(part) for part in command], capture_output=True, check=True)
    return ca, crt, key


@pytest.mark.spec("SERVE-5")
def test_its_server_speaks_https_with_a_certificate_and_its_admin_port_http(
    data: Path, tmp_path: Path
) -> None:
    ca, crt, key = certificate(tmp_path)
    edgar = FakeEdgar(data, certificate=crt, key=key)
    edgar.start()
    try:
        context = ssl.create_default_context(cafile=ca)
        with (
            socket.create_connection(("127.0.0.1", edgar.port), timeout=10) as raw,
            context.wrap_socket(raw, server_hostname="data.sec.gov") as tls,
        ):
            tls.sendall(
                b"GET /submissions/CIK0000320193.json HTTP/1.1\r\nHost: data.sec.gov\r\n\r\n"
            )
            response = http.client.HTTPResponse(tls)
            response.begin()
            body = response.read()
        plain = get(edgar.port, target(SUBMISSIONS))
        entries = requests(edgar.admin_port)
    finally:
        edgar.stop()

    assert (response.status, body) == (200, SECOND)
    assert plain.status is None
    assert [entry["status"] for entry in entries] == [200]


@pytest.mark.spec("SERVE-5")
def test_its_server_answers_one_request_at_a_time_in_order_and_closes_each(
    fake: FakeEdgar,
) -> None:
    first = socket.create_connection(("127.0.0.1", fake.port), timeout=10)
    second = socket.create_connection(("127.0.0.1", fake.port), timeout=10)
    try:
        second.sendall(f"GET {target(SUBMISSIONS)} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        second.settimeout(0.5)
        with pytest.raises(TimeoutError):
            second.recv(1)  # nothing, while the request that arrived first is unfinished
        first.sendall(f"GET {target(INDEX)} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        replies = []
        for connection in (first, second):
            connection.settimeout(10)
            reply = b""
            while chunk := connection.recv(65536):  # until the server closes it
                reply += chunk
            replies.append(reply)
    finally:
        first.close()
        second.close()

    assert replies[0].startswith(b"HTTP/1.0 200 ")
    assert replies[0].endswith(LISTING)
    assert replies[1].startswith(b"HTTP/1.0 200 ")
    assert replies[1].endswith(SECOND)
    assert [entry["target"] for entry in requests(fake.admin_port)] == [
        target(INDEX),
        target(SUBMISSIONS),
    ]


# CUE


@pytest.mark.spec("CUE-1")
def test_cues_are_kept_in_order_until_each_response_is_given(fake: FakeEdgar) -> None:
    assert add_cue(fake, {"url": INDEX, "responses": [{"status": 429}, {"status": 503}]}) == 204
    assert add_cue(fake, {"url": INDEX, "responses": [{"status": 403}]}) == 204

    statuses = [get(fake.port, target(INDEX)).status for _ in range(5)]

    assert statuses == [429, 503, 403, 200, 200]
    assert [e["cued"] for e in requests(fake.admin_port)] == [True, True, True, False, False]


@pytest.mark.spec("CUE-1", "ADMIN-1")
def test_a_delete_removes_every_cue(fake: FakeEdgar) -> None:
    add_cue(fake, {"url": INDEX, "responses": [{"status": 429}]})
    add_cue(fake, {"responses": [{"status": 503}, {"drop": True}]})

    assert admin(fake.admin_port, "DELETE", "/cues") == (204, b"")
    assert get(fake.port, target(INDEX)).status == 200
    assert get(fake.port, target(SUBMISSIONS)).status == 200


@pytest.mark.spec("CUE-2")
def test_a_cue_for_a_url_answers_before_a_cue_for_any(fake: FakeEdgar) -> None:
    add_cue(fake, {"responses": [{"status": 500}, {"status": 502}]})
    add_cue(fake, {"url": INDEX, "responses": [{"status": 429}]})

    assert get(fake.port, target(INDEX)).status == 429
    assert get(fake.port, target(INDEX)).status == 500
    assert get(fake.port, "/missing.json").status == 502
    assert get(fake.port, target(INDEX)).status == 200


@pytest.mark.spec("CUE-2")
def test_a_cue_answers_only_the_url_it_names(fake: FakeEdgar) -> None:
    add_cue(
        fake, {"url": "https://www.sec.gov" + target(SUBMISSIONS), "responses": [{"status": 403}]}
    )

    assert get(fake.port, target(SUBMISSIONS), {"Host": "data.sec.gov"}).status == 200
    assert get(fake.port, target(INDEX)).status == 200
    # On one machine, a request names its target on both hosts.
    assert get(fake.port, target(SUBMISSIONS)).status == 403


@pytest.mark.spec("CUE-2")
def test_a_cue_answers_only_a_get(fake: FakeEdgar) -> None:
    add_cue(fake, {"responses": [{"status": 403}]})

    assert get(fake.port, target(INDEX), method="HEAD").status == 405
    assert get(fake.port, target(INDEX)).status == 403


@pytest.mark.spec("CUE-2")
def test_a_status_is_given_with_its_headers_and_body(fake: FakeEdgar) -> None:
    add_cue(
        fake,
        {
            "url": INDEX,
            "responses": [{"status": 429, "headers": {"Retry-After": "7"}, "body": "Slow down ✋"}],
        },
    )

    answer = get(fake.port, target(INDEX), {"If-None-Match": ETAG})

    assert (answer.status, answer.body) == (429, "Slow down ✋".encode())
    assert sorted(answer.names) == ["Content-Length", "Date", "Retry-After"]
    assert answer.headers["retry-after"] == "7"
    assert answer.headers["content-length"] == str(len("Slow down ✋".encode()))
    [entry] = requests(fake.admin_port)
    assert (entry["url"], entry["status"], entry["cued"]) == (INDEX, 429, True)


@pytest.mark.spec("CUE-2")
def test_a_status_keeps_the_date_and_length_its_headers_give(fake: FakeEdgar) -> None:
    headers = {"date": "Mon, 05 Oct 2026 00:00:00 GMT", "CONTENT-LENGTH": "0"}
    add_cue(fake, {"responses": [{"status": 503, "headers": headers}]})

    answer = get(fake.port, "/missing.json")

    assert answer.status == 503
    assert sorted(answer.names) == ["CONTENT-LENGTH", "date"]
    assert answer.headers["date"] == "Mon, 05 Oct 2026 00:00:00 GMT"


@pytest.mark.spec("CUE-2")
def test_a_version_on_cue_is_served_as_its_latest_would_be(data: Path) -> None:
    fetched(data, INDEX, LISTING + b" ", at=500, feed="daily-index", etag='"later"')
    edgar = FakeEdgar(data)
    edgar.start()
    try:
        add_cue(edgar, {"url": INDEX, "responses": [{"sha256": sha(LISTING)}] * 2})
        earlier = get(edgar.port, target(INDEX))
        unchanged = get(edgar.port, target(INDEX), {"If-None-Match": ETAG})
        latest = get(edgar.port, target(INDEX))
    finally:
        edgar.stop()

    assert (earlier.status, earlier.body, earlier.headers["etag"]) == (200, LISTING, ETAG)
    assert (unchanged.status, unchanged.headers["etag"]) == (304, ETAG)
    assert (latest.status, latest.body, latest.headers["etag"]) == (200, LISTING + b" ", '"later"')


@pytest.mark.spec("CUE-2")
@pytest.mark.parametrize(
    ("url", "response"),
    [
        (INDEX, {"sha256": sha(SECOND)}),
        ("https://www.sec.gov/missing.json", {"sha256": sha(SECOND)}),
        ("https://www.sec.gov/missing.json", {"cut": True}),
    ],
)
def test_a_version_on_cue_that_isnt_one_gets_a_404(
    fake: FakeEdgar, url: str, response: dict[str, Any]
) -> None:
    add_cue(fake, {"url": url, "responses": [response]})

    answer = get(fake.port, target(url))

    assert (answer.status, answer.body) == (404, b"")
    assert requests(fake.admin_port)[-1]["cued"] is True


@pytest.mark.spec("CUE-2")
@pytest.mark.parametrize("accept", [None, "gzip"])
def test_a_cut_sends_half_the_body_then_closes(fake: FakeEdgar, accept: str | None) -> None:
    add_cue(fake, {"url": FACTS, "responses": [{"cut": True}]})
    # A conditional request that the version matches, which would get a 304
    # without the cut (SERVE-4).
    headers = {"If-None-Match": "*"}
    if accept:
        headers["Accept-Encoding"] = accept

    answer = get(fake.port, target(FACTS), headers)

    whole = gzip.compress(INSTANCE, mtime=0) if accept else INSTANCE
    assert (answer.status, answer.cut) == (200, True)
    assert answer.headers["content-length"] == str(len(whole))
    assert answer.body == whole[: len(whole) // 2]
    assert requests(fake.admin_port)[-1]["status"] == 200


@pytest.mark.spec("CUE-2")
def test_a_cut_of_a_version_ends_its_gzip_encoding_early(fake: FakeEdgar) -> None:
    add_cue(fake, {"url": FACTS, "responses": [{"sha256": sha(INSTANCE), "cut": True}]})

    answer = get(fake.port, target(FACTS), {"Accept-Encoding": "gzip"})

    with pytest.raises(EOFError):
        gzip.decompress(answer.body)


@pytest.mark.spec("CUE-2")
def test_a_drop_sends_no_response(fake: FakeEdgar) -> None:
    add_cue(fake, {"url": INDEX, "responses": [{"drop": True}]})

    answer = get(fake.port, target(INDEX), {"User-Agent": UA})

    assert answer.status is None
    [entry] = requests(fake.admin_port)
    assert (entry["url"], entry["status"], entry["cued"]) == (INDEX, None, True)
    assert entry["headers"]["user-agent"] == UA


# REQ


@pytest.mark.spec("REQ-1", "REQ-3")
def test_each_request_is_one_entry_of_the_log_in_order(fake: FakeEdgar) -> None:
    sent = [
        (target(SUBMISSIONS), {"User-Agent": UA, "Accept-Encoding": "gzip"}),
        (target(INDEX), {"User-Agent": UA, "If-None-Match": ETAG}),
        ("/missing.json?q=1", {"Host": "www.sec.gov", "X-Custom": "one"}),
    ]
    add_cue(fake, {"url": SUBMISSIONS, "responses": [{"status": 503}]})
    before = datetime.now(UTC) - timedelta(milliseconds=1)
    statuses = [get(fake.port, path, headers).status for path, headers in sent]
    after = datetime.now(UTC)

    entries = requests(fake.admin_port)

    assert statuses == [503, 304, 404]
    for entry in entries:
        assert list(validator(REQUEST).iter_errors(entry)) == []
        assert before <= datetime.fromisoformat(entry["received_at"]) <= after
        assert entry["client"] == "127.0.0.1"
        assert entry["method"] == "GET"
    assert [e["n"] for e in entries] == [1, 2, 3]
    assert [e["target"] for e in entries] == [path for path, _ in sent]
    assert [e["url"] for e in entries] == [SUBMISSIONS, INDEX, None]
    assert [e["status"] for e in entries] == statuses
    assert [e["cued"] for e in entries] == [True, False, False]
    assert entries[0]["headers"] == {
        "host": f"127.0.0.1:{fake.port}",
        "user-agent": UA,
        "accept-encoding": "gzip",
    }
    assert entries[2]["headers"] == {"host": "www.sec.gov", "x-custom": "one"}


@pytest.mark.spec("REQ-1")
def test_a_header_sent_more_than_once_has_its_values_joined_in_order(fake: FakeEdgar) -> None:
    with socket.create_connection(("127.0.0.1", fake.port), timeout=10) as connection:
        connection.sendall(
            f"GET {target(INDEX)} HTTP/1.1\r\nHost: x\r\nX-Trace: b\r\n"
            "Accept-Encoding: br\r\nx-trace: a\r\nX-TRACE: c\r\n\r\n".encode()
        )
        while connection.recv(65536):
            pass

    [entry] = requests(fake.admin_port)
    assert entry["headers"] == {"host": "x", "x-trace": "b, a, c", "accept-encoding": "br"}


@pytest.mark.spec("REQ-1")
def test_the_log_holds_a_request_once_its_client_has_the_answer(
    fake: FakeEdgar, monkeypatch: pytest.MonkeyPatch
) -> None:
    append = fake.state.append

    def slowly(entry: dict[str, Any]) -> None:
        """Logs the request well after its client has the answer."""
        time.sleep(0.2)
        append(entry)

    monkeypatch.setattr(fake.state, "append", slowly)
    for n in range(1, 4):
        get(fake.port, target(FACTS), {"Accept-Encoding": "gzip"})

        assert [entry["n"] for entry in requests(fake.admin_port, after=n - 1)] == [n]


@pytest.mark.spec("REQ-1")
def test_a_malformed_request_is_an_entry_too(fake: FakeEdgar) -> None:
    with socket.create_connection(("127.0.0.1", fake.port), timeout=10) as connection:
        connection.sendall(b"GET /submissions/CIK0000320193.json HTTP/9\r\n\r\n")
        reply = b""
        while chunk := connection.recv(65536):
            reply += chunk

    [entry] = requests(fake.admin_port)
    assert reply.startswith(b"HTTP/1.0 400 ")
    assert (entry["target"], entry["url"], entry["status"]) == (target(SUBMISSIONS), None, 400)


@pytest.mark.spec("REQ-1", "REQ-3")
@pytest.mark.parametrize("header", ["X-A,B: 1", "X(1): 2"])
def test_a_request_with_a_header_name_that_isnt_a_token_gets_a_400(
    fake: FakeEdgar, header: str
) -> None:
    with socket.create_connection(("127.0.0.1", fake.port), timeout=10) as connection:
        connection.sendall(f"GET {target(INDEX)} HTTP/1.1\r\nHost: x\r\n{header}\r\n\r\n".encode())
        reply = b""
        while chunk := connection.recv(65536):
            reply += chunk

    [entry] = requests(fake.admin_port)
    assert reply.startswith(b"HTTP/1.0 400 ")
    assert (entry["target"], entry["url"], entry["headers"], entry["status"]) == (
        target(INDEX),
        None,
        {},
        400,
    )
    assert list(validator(REQUEST).iter_errors(entry)) == []


LARGE_URL = "https://www.sec.gov/Archives/edgar/full-index/2026/QTR4/master.idx"


@pytest.mark.spec("REQ-1")
@pytest.mark.parametrize("cued", [False, True])
def test_an_entry_names_the_url_and_the_cue_when_its_client_goes_away(
    data: Path, cued: bool
) -> None:
    # Larger than a connection's buffers hold, at both of its ends, so the
    # fake EDGAR is still sending it when a client that read its head goes.
    large = b"x" * (32 << 20)
    fetched(data, LARGE_URL, large, at=90, content_type="text/plain")
    edgar = FakeEdgar(data)
    edgar.start()
    try:
        if cued:
            add_cue(edgar, {"url": LARGE_URL, "responses": [{"sha256": sha(large)}]})
        with socket.create_connection(("127.0.0.1", edgar.port), timeout=10) as connection:
            connection.sendall(f"GET {target(LARGE_URL)} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
            head = b""
            while b"\r\n\r\n" not in head:
                head += connection.recv(65536)
            # Gone, with the body mostly unsent: its connection reset.
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        [entry] = requests(edgar.admin_port)
    finally:
        edgar.stop()

    assert head.startswith(b"HTTP/1.0 200 ")
    assert (entry["url"], entry["status"], entry["cued"]) == (LARGE_URL, 200, cued)


# A user agent and a header that tests send the fake EDGAR, and look for in
# what it writes (REQ-2).
SECRET_AGENT = "Secret Agent agent-4f1c9e@example.com"
SECRET_HEADER = ("X-Secret-7d2e9a", "token-b81f04c3")
# Runs the fake EDGAR with an audit hook that writes down each attempt to
# reach anything: a connection, a name looked up, or a datagram sent. First,
# it checks that the hook sees one, and marks where the fake EDGAR starts.
RUNNER = """
import runpy, socket, sys

events, *argv = sys.argv[1:]
OUTWARD = {
    "socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
    "socket.gethostbyname_ex", "socket.gethostbyaddr", "socket.getnameinfo",
    "socket.sendto", "socket.sendmsg", "http.client.connect", "urllib.Request",
}

def hook(event, args):
    if event in OUTWARD:
        with open(events, "a") as file:
            file.write(event + "\\n")

sys.addaudithook(hook)
socket.getaddrinfo("127.0.0.1", 80)
with open(events, "a") as file:
    file.write("the fake EDGAR starts\\n")
sys.argv = ["fake_edgar", *argv]
runpy.run_module("edgar_pipeline.fake_edgar", run_name="__main__", alter_sys=True)
"""


@dataclass
class Run:
    """What a run of the fake EDGAR, as its own process, did."""

    data: tuple[dict[str, tuple[bytes, int]], dict[str, tuple[bytes, int]]]
    events: list[str]
    output: str
    files: list[str]
    statuses: list[int | None]
    entries: list[dict[str, Any]]
    returncode: int


@pytest.fixture(scope="module")
def run(tmp_path_factory: pytest.TempPathFactory) -> Run:
    """The fake EDGAR as its own process, sent requests of every kind, then stopped."""
    root = tmp_path_factory.mktemp("run")
    data = store(root / "data")
    cwd = root / "cwd"
    cwd.mkdir()
    runner = root / "runner.py"
    runner.write_text(RUNNER)
    events = root / "events"
    before = tree(data)
    first = ""
    process = subprocess.Popen(
        [
            *(sys.executable, str(runner), str(events), "--data", str(data)),
            *("--port", "0", "--admin-port", "0"),
        ],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    try:
        assert process.stdout is not None
        first = process.stdout.readline()
        started = re.search(r"at port (\d+), with the admin port at (\d+)", first)
        assert started, "the fake EDGAR didn't start"
        port, admin_port = int(started[1]), int(started[2])
        secret = {"User-Agent": SECRET_AGENT, SECRET_HEADER[0]: SECRET_HEADER[1]}
        cued = [{"drop": True}, {"cut": True}, {"status": 403, "body": "Forbidden"}]
        status, _ = admin(
            admin_port, "POST", "/cues", json.dumps({"url": FACTS, "responses": cued}).encode()
        )
        assert status == 204
        assert admin(admin_port, "POST", "/cues", b'{"responses": []}')[0] == 400
        statuses = [
            get(port, target(path), secret | {"Accept-Encoding": "gzip"}, method=method).status
            for method, path in [
                ("GET", SUBMISSIONS),
                ("GET", INDEX),
                ("GET", "/missing"),
                ("POST", INDEX),
                ("GET", FACTS),
                ("GET", FACTS),
                ("GET", FACTS),
            ]
        ]
        with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
            connection.sendall(f"BREW / HTCPCP/1.0\r\nUser-Agent: {SECRET_AGENT}\r\n\r\n".encode())
            connection.recv(65536)
        entries = requests(admin_port)
    finally:
        process.send_signal(signal.SIGTERM)
        output, errors = process.communicate(timeout=30)
    return Run(
        data=(before, tree(data)),
        events=events.read_text().splitlines(),
        output=first + output + errors,
        files=sorted(path.name for path in cwd.iterdir()),
        statuses=statuses,
        entries=entries,
        returncode=process.returncode,
    )


@pytest.mark.spec("SERVE-1")
def test_it_sends_nothing_to_anything(run: Run) -> None:
    assert run.statuses == [200, 200, 404, 405, None, 200, 403]
    assert run.events == ["socket.getaddrinfo", "the fake EDGAR starts"]


@pytest.mark.spec("SERVE-1")
def test_as_its_own_process_it_changes_nothing_in_the_store(run: Run) -> None:
    before, after = run.data
    assert after == before


@pytest.mark.spec("REQ-2")
def test_no_request_header_is_written_to_its_output_or_a_file(run: Run) -> None:
    assert run.returncode == 0
    assert run.output.startswith("Serving 4 stored URLs from ")
    for secret in (SECRET_AGENT, "agent-4f1c9e", *SECRET_HEADER, SECRET_HEADER[0].lower()):
        assert secret not in run.output
    assert run.files == []


@pytest.mark.spec("REQ-2")
def test_its_admin_port_gives_the_request_headers(run: Run) -> None:
    *answered, malformed = run.entries

    assert len(answered) == len(run.statuses)
    for entry in answered:
        assert entry["headers"]["user-agent"] == SECRET_AGENT
        assert entry["headers"][SECRET_HEADER[0].lower()] == SECRET_HEADER[1]
    # A request line too malformed to read on from gets a 400, its headers unread.
    assert (malformed["method"], malformed["status"], malformed["headers"]) == ("BREW", 400, {})


# ADMIN


@pytest.mark.spec("ADMIN-1")
@pytest.mark.parametrize("cue", ACCEPTED)
def test_the_admin_port_keeps_a_cue_that_validates(fake: FakeEdgar, cue: dict[str, Any]) -> None:
    assert admin(fake.admin_port, "POST", "/cues", json.dumps(cue).encode()) == (204, b"")
    assert len(fake.state.cues) == 1


@pytest.mark.spec("ADMIN-1")
@pytest.mark.parametrize(
    "body",
    [
        *(json.dumps(cue).encode() for cue in REFUSED),
        b"",
        b"not JSON",
        b'{"responses": [{"status": NaN}]}',
        b'{"responses": [{"status": Infinity}]}',
        '{"responses": [{"status": 403}], "url": "https://www.sec.gov/é"}'.encode("latin-1"),
        b"[]",
        b"null",
    ],
)
def test_the_admin_port_refuses_a_body_that_isnt_a_cue_and_keeps_nothing(
    fake: FakeEdgar, body: bytes
) -> None:
    status, _ = admin(fake.admin_port, "POST", "/cues", body)

    assert status == 400
    assert fake.state.cues == []
    assert get(fake.port, target(INDEX)).status == 200


# Values of every JSON type, many of them of a cue's or a response's fields.
VALUES = st.recursive(
    st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-1000, max_value=1000),
        st.sampled_from([99, 100, 403, 599, 600, 403.0, 403.5, 1e300]),
        st.sampled_from([URL, "https://www.sec.gov/", "http://data.sec.gov/", SHA256, ""]),
        st.sampled_from([SHA256.upper(), SHA256[:-1], "403", "true"]),
        st.text(max_size=5),
    ),
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(
            st.sampled_from(["status", "headers", "body", "sha256", "cut", "drop", "x"]),
            children,
            max_size=4,
        ),
    ),
    max_leaves=10,
)
HEADERS = st.dictionaries(st.text(max_size=3), st.text(max_size=3), max_size=2)
STATUSES = st.one_of(st.integers(100, 599), st.just(403.0))
# Responses of each form the schema accepts, some with a field it doesn't know:
# a status, with headers and a body or without; sha256, cut, or both; or drop.
RESPONSES = st.one_of(
    st.fixed_dictionaries(
        {"status": STATUSES},
        optional={"headers": HEADERS, "body": st.text(max_size=5), "x": VALUES},
    ),
    st.fixed_dictionaries(
        {"sha256": st.just(SHA256)}, optional={"cut": st.just(True), "x": VALUES}
    ),
    st.fixed_dictionaries({"cut": st.just(True)}, optional={"x": VALUES}),
    st.fixed_dictionaries({"drop": st.just(True)}, optional={"x": VALUES}),
)
VALID_CUES = st.fixed_dictionaries(
    {"responses": st.lists(RESPONSES, min_size=1, max_size=3)},
    optional={
        "url": st.sampled_from([URL, "https://www.sec.gov/", "https://data.sec.gov/x?y=1"]),
        "x": VALUES,
    },
)
# For each field of a cue or a response: values the schema accepts for it,
# then values close to those that it refuses. And for "x", which neither
# has, values that it ignores.
FIELD_VALUES: dict[str, list[Any]] = {
    "url": [
        *(URL, "https://www.sec.gov/"),
        *("https://www.sec.gov", "https://example.com/", "http://data.sec.gov/", None, 0),
    ],
    "responses": [[{"status": 403}], [], [{}], {"status": 403}, None],
    "status": [100, 599, 403.0, 99, 600, 403.5, "403", True, None],
    "headers": [{}, {"Retry-After": "1"}, {"Retry-After": 1}, ["Retry-After: 1"], "", None],
    "body": ["", "refused", 0, None],
    "sha256": [SHA256, SHA256.upper(), SHA256[:-1], SHA256 + "\n", None],
    "cut": [True, False, "true", 1, None],
    "drop": [True, False, "true", 1, None],
    "x": [None, False, {"status": 99}],
}


@st.composite
def changed_cues(draw: st.DrawFn) -> dict[str, Any]:
    """A cue the schema accepts, with one field of it or of one of its
    responses removed, added, or given another value."""
    changed = draw(VALID_CUES)
    place = draw(st.sampled_from([changed, *changed["responses"]]))
    fields = CUE_FIELDS if place is changed else RESPONSE_FIELDS
    name = draw(st.sampled_from([*(field for field, _ in fields), "x"]))
    value = draw(st.one_of(st.sampled_from([ABSENT, *FIELD_VALUES[name]]), VALUES))
    if value is ABSENT:
        place.pop(name, None)
    else:
        place[name] = copy.deepcopy(value)
    return changed


CUES = st.one_of(VALUES, VALID_CUES, changed_cues())


def parses(value: Any) -> bool:
    try:
        cues.parse(value)
    except cues.NotACue:
        return False
    return True


@pytest.mark.spec("ADMIN-1")
@settings(max_examples=500, deadline=None)
@given(CUES)
def test_a_cue_is_a_body_that_validates_against_the_cues_schema(value: Any) -> None:
    assert parses(value) == validator(CUE).is_valid(value)


# A response of each form the schema accepts.
FORMS = [
    {"status": 403},
    {"status": 429, "headers": {"Retry-After": "1"}, "body": "Slow down"},
    {"sha256": SHA256},
    {"cut": True},
    {"sha256": SHA256, "cut": True},
    {"drop": True},
]


def changed_once() -> Iterator[dict[str, Any]]:
    """Each cue one change makes of a cue with a response of each form: a
    field of the cue or of its response removed, added, or given each of
    its values."""
    for form in FORMS:
        for place, fields in (("cue", CUE_FIELDS), ("response", RESPONSE_FIELDS)):
            for name in [*(field for field, _ in fields), "x"]:
                for value in [ABSENT, *FIELD_VALUES[name]]:
                    changed: dict[str, Any] = {"url": URL, "responses": [copy.deepcopy(form)]}
                    where = changed if place == "cue" else changed["responses"][0]
                    if value is ABSENT:
                        where.pop(name, None)
                    else:
                        where[name] = copy.deepcopy(value)
                    yield changed


@pytest.mark.spec("ADMIN-1")
def test_a_cue_changed_once_is_a_cue_only_if_it_validates_against_the_schema() -> None:
    check = validator(CUE)
    changed = list(changed_once())

    assert [value for value in changed if parses(value) != check.is_valid(value)] == []
    assert {check.is_valid(value) for value in changed} == {True, False}


@pytest.mark.spec("ADMIN-1")
def test_the_admin_port_gives_the_log_after_a_place_in_it(fake: FakeEdgar) -> None:
    for path in (SUBMISSIONS, INDEX, FACTS):
        get(fake.port, target(path))

    assert [e["n"] for e in requests(fake.admin_port)] == [1, 2, 3]
    assert [e["n"] for e in requests(fake.admin_port, after=0)] == [1, 2, 3]
    assert [e["n"] for e in requests(fake.admin_port, after=1)] == [2, 3]
    assert requests(fake.admin_port, after=3) == []
    assert requests(fake.admin_port, after=99) == []
    _, body = admin(fake.admin_port, "GET", "/requests")
    assert json.loads(body) == requests(fake.admin_port)


@pytest.mark.spec("ADMIN-1")
@pytest.mark.parametrize("query", ["after=-1", "after=x", "after=", "after=1.5", "after=1&after=2"])
def test_the_admin_port_refuses_an_after_that_isnt_an_integer(fake: FakeEdgar, query: str) -> None:
    assert admin(fake.admin_port, "GET", f"/requests?{query}")[0] == 400


@pytest.mark.spec("ADMIN-1")
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/cues"),
        ("PUT", "/cues"),
        ("POST", "/requests"),
        ("DELETE", "/requests"),
        ("GET", "/"),
        ("GET", target(URL)),
        ("HEAD", "/requests"),
    ],
)
def test_the_admin_port_gives_any_other_request_a_404(
    fake: FakeEdgar, method: str, path: str
) -> None:
    assert (
        admin(fake.admin_port, method, path, b"{}" if method in {"POST", "PUT"} else None)[0] == 404
    )


@pytest.mark.spec("ADMIN-1")
def test_a_request_to_the_admin_port_isnt_one_of_the_logs(fake: FakeEdgar) -> None:
    add_cue(fake, {"responses": [{"status": 403}]})
    admin(fake.admin_port, "DELETE", "/cues")
    admin(fake.admin_port, "GET", "/missing")
    get(fake.port, target(INDEX))

    assert [entry["target"] for entry in requests(fake.admin_port)] == [target(INDEX)]


# CHECK


def entry_at(n: int, seconds: float, **changes: Any) -> dict[str, Any]:
    """An entry of the log, received seconds after T0, from a writer with UA."""
    when = (T0 + timedelta(seconds=seconds)).isoformat(timespec="milliseconds")
    return entry(n=n, received_at=when.replace("+00:00", "Z"), **changes) | (
        {} if "headers" in changes else {"headers": {"user-agent": UA}}
    )


@pytest.mark.spec("CHECK-1")
def test_the_checks_pass_requests_that_keep_the_rules() -> None:
    entries = [entry_at(1, 0), entry_at(2, 0.18), entry_at(3, 0.5, status=403)]

    assert checks.check(entries, UA) == []


@pytest.mark.spec("CHECK-1")
@pytest.mark.parametrize(
    ("headers", "user_agent"),
    [
        ({"user-agent": "Someone else other@example.com"}, UA),
        ({}, UA),
        ({"user-agent": "Test Runner"}, "Test Runner"),
        ({"user-agent": UA.upper()}, UA),
    ],
)
def test_the_checks_report_a_request_without_the_user_agent(
    headers: dict[str, str], user_agent: str
) -> None:
    entries = [entry_at(1, 0, headers={"user-agent": user_agent}), entry_at(2, 1, headers=headers)]

    problems = checks.check(entries, user_agent)

    assert "request 2: FETCH-2: not the user agent given, with an @" in problems
    assert ("request 1" in " ".join(problems)) == ("@" not in user_agent)


@pytest.mark.spec("CHECK-1")
def test_the_checks_report_a_request_less_than_018_seconds_after_the_one_before() -> None:
    entries = [entry_at(1, 0), entry_at(2, 0.179), entry_at(3, 0.359), entry_at(4, 0.6)]

    assert checks.check(entries, UA) == [
        "request 2: FETCH-6: 0.179 s after the request before it",
    ]


@pytest.mark.spec("CHECK-1")
def test_the_checks_time_each_request_from_the_one_before_even_one_reported() -> None:
    entries = [entry_at(1, 0), entry_at(2, 0.1), entry_at(3, 0.2)]

    assert checks.check(entries, UA) == [
        "request 2: FETCH-6: 0.100 s after the request before it",
        "request 3: FETCH-6: 0.100 s after the request before it",
    ]


@pytest.mark.spec("CHECK-1")
def test_the_checks_report_every_request_after_a_403() -> None:
    entries = [
        entry_at(1, 0),
        entry_at(2, 1, status=403),
        entry_at(3, 2, status=None),
        entry_at(4, 3, status=403),
    ]

    assert checks.check(entries, UA) == [
        "request 3: FETCH-9: after request 2's 403",
        "request 4: FETCH-9: after request 2's 403",
    ]


def capped(seconds: float) -> None:
    """snapshot.py's sleep, with each wait cut to FETCH-6's interval, the
    longest the checks read between two requests, so a retry's wait doesn't
    hold the tests up."""
    time.sleep(min(seconds, snapshot.MIN_INTERVAL))


def snapshot_run(
    fake: FakeEdgar, out: Path, urls: list[str], fetcher: snapshot.Fetcher | None = None
) -> int:
    """snapshot.py's run of urls, at the fake EDGAR's own address."""
    fetcher = fetcher or snapshot.Fetcher(UA, sleep=capped)
    return snapshot.run(fetcher, out, "feed", [local(fake, url) for url in urls], tool=TOOL)


@pytest.mark.spec("CHECK-1")
def test_the_checks_report_a_writer_that_sends_requests_too_close(
    fake: FakeEdgar, tmp_path: Path
) -> None:
    fetcher = snapshot.Fetcher(UA, min_interval=0, sleep=capped)
    snapshot_run(fake, tmp_path, [SUBMISSIONS, INDEX, FACTS], fetcher)

    problems = checks.check(requests(fake.admin_port), UA)

    assert [p.split(": ")[1] for p in problems] == ["FETCH-6", "FETCH-6"]


@pytest.mark.spec("CHECK-2")
def test_snapshots_requests_pass_the_checks_with_each_failure_retried(
    fake: FakeEdgar, tmp_path: Path
) -> None:
    add_cue(fake, {"url": FACTS, "responses": [{"cut": True}]})
    add_cue(fake, {"url": INDEX, "responses": [{"status": 429, "headers": {"Retry-After": "1"}}]})
    add_cue(fake, {"url": FILING, "responses": [{"drop": True}]})

    code = snapshot_run(fake, tmp_path, [SUBMISSIONS, FACTS, INDEX, FILING])

    entries = requests(fake.admin_port)
    assert code == 0
    assert [(e["target"], e["status"]) for e in entries] == [
        (target(SUBMISSIONS), 200),
        (target(FACTS), 200),
        (target(FACTS), 200),
        (target(INDEX), 429),
        (target(INDEX), 200),
        (target(FILING), None),
        (target(FILING), 200),
    ]
    assert checks.check(entries, UA) == []
    records = [
        json.loads(line)
        for line in (tmp_path / "feeds" / "feed" / "versions.jsonl").read_text().splitlines()
    ]
    assert [(r["attempts"], r["sha256"]) for r in records] == [
        (1, sha(SECOND)),
        (2, sha(INSTANCE)),
        (2, sha(LISTING)),
        (2, sha(PAGE)),
    ]


@pytest.mark.spec("CHECK-2")
def test_snapshots_requests_pass_the_checks_with_a_stop_on_a_403(
    fake: FakeEdgar, tmp_path: Path
) -> None:
    add_cue(fake, {"url": INDEX, "responses": [{"status": 403}]})

    code = snapshot_run(fake, tmp_path, [SUBMISSIONS, INDEX, FACTS, FILING])

    entries = requests(fake.admin_port)
    assert code == 1
    assert [(e["target"], e["status"]) for e in entries] == [
        (target(SUBMISSIONS), 200),
        (target(INDEX), 403),
    ]
    assert checks.check(entries, UA) == []
