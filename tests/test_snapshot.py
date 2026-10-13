"""The snapshot and verify scripts against spec/snapshots.md, using a local HTTP server.

Each test carries the IDs of the statements it checks, as
@pytest.mark.spec(...); tests/test_spec_coverage.py requires every statement
checked by `verify` or `tests` to have one.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import itertools
import json
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import snapshot
import verify
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

UA = "Test Runner test@example.com"
TOOL = "scripts/snapshot.py@" + "0" * 40
FETCHER = "fetcher@" + "0123456789abcdef" * 2 + "01234567"

Reply = tuple[int, dict[str, str], bytes]
Route = Callable[[dict[str, str]], Reply]


@dataclass
class Server:
    base: str
    routes: dict[str, Route] = field(default_factory=dict)
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    delay: float = 0.0
    body_delay: float = 0.0  # between the headers and the body
    dates: list[str] = field(default_factory=list)  # the next responses' Date, in turn

    def url(self, path: str = "/feed.xml") -> str:
        return self.base + path


@pytest.fixture
def server() -> Iterator[Server]:
    state = Server(base="")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            headers = {k.lower(): v for k, v in self.headers.items()}
            state.requests.append((self.path, headers))
            if state.delay:
                time.sleep(state.delay)
            status, reply_headers, body = state.routes[self.path](headers)
            try:
                self.send_response(status)
                for name, value in reply_headers.items():
                    self.send_header(name, value)
                if "Content-Length" not in reply_headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if state.body_delay:
                    time.sleep(state.body_delay)
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client gave up first, as FETCH-5's test intends

        def date_time_string(self, timestamp: float | None = None) -> str:
            return state.dates.pop(0) if state.dates else super().date_time_string(timestamp)

        def log_message(self, format: str, *args: Any) -> None:
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    state.base = f"http://127.0.0.1:{httpd.server_address[1]}"
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield state
    httpd.shutdown()
    httpd.server_close()


@pytest.fixture(autouse=True)
def env_file(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Where snapshot.py looks for .env.dev (FETCH-15): never the user's own."""
    path = tmp_path_factory.mktemp("env") / ".env.dev"
    monkeypatch.setattr(snapshot, "ENV_FILE", path)
    return path


# The tests' fetchers send requests without FETCH-6's interval, so two could
# be sent within a millisecond, and their records share a requested_at, which
# REC-12 forbids. Their wall clock moves a millisecond at each reading instead.
TICKS = itertools.count()


def wall() -> str:
    when = datetime(2026, 10, 3, tzinfo=UTC) + timedelta(milliseconds=next(TICKS))
    return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def fetcher(sleeps: list[float] | None = None) -> snapshot.Fetcher:
    def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return snapshot.Fetcher(UA, min_interval=0, sleep=sleep, wall=wall)


def allow(data: Path, *urls: str) -> None:
    """Declare the scope, as the owner does before fetching (SCOPE-1)."""
    data.mkdir(parents=True, exist_ok=True)
    entries = [{"url": url, "reason": "Tests."} for url in urls]
    (data / "scope.json").write_text(json.dumps({"urls": entries}))


def scoped(server: Server, data: Path) -> None:
    if not (data / "scope.json").exists():
        allow(data, server.url("/"))


def fetch(
    server: Server, data: Path, sleeps: list[float] | None = None, path: str = "/feed.xml"
) -> dict[str, Any]:
    scoped(server, data)
    return snapshot.snapshot(fetcher(sleeps), data, "notices", server.url(path), tool=TOOL)


def run(server: Server, data: Path, *paths: str) -> int:
    scoped(server, data)
    urls = [server.url(path) for path in paths]
    return snapshot.run(fetcher(), data, "notices", urls, tool=TOOL)


def with_etag(body: bytes, etag: str, last_modified: str | None = None) -> Route:
    def reply(headers: dict[str, str]) -> Reply:
        validators = {"ETag": etag} | ({"Last-Modified": last_modified} if last_modified else {})
        if headers.get("if-none-match") == etag:
            return 304, validators, b""
        return 200, validators | {"Content-Type": "application/xml"}, body

    return reply


def plain(body: bytes) -> Route:
    return lambda headers: (200, {}, body)


def replies(*items: Reply) -> Route:
    queue = iter(items)
    return lambda headers: next(queue)


def log(data: Path, feed: str = "notices") -> list[dict[str, Any]]:
    path = data / "feeds" / feed / "versions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def write_log(data: Path, records: list[dict[str, Any]], feed: str = "notices") -> None:
    path = data / "feeds" / feed / "versions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def later(record: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """Another fetch of the record's URL, a second later, so not a copy (REC-12)."""
    when = datetime.fromisoformat(record["requested_at"]) + timedelta(seconds=1)
    requested_at = when.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return record | {"requested_at": requested_at, "received_at": requested_at} | changes


NOTHING = {"sha256": None, "bytes": None, "object": None}


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def objects(data: Path) -> list[Path]:
    return sorted((data / "objects").rglob("*.gz"))


def ids(problems: list[str]) -> list[str]:
    return [problem.split(" ", 1)[0] for problem in problems]


@pytest.fixture
def stored(server: Server, tmp_path: Path) -> Path:
    server.routes["/feed.xml"] = with_etag(b"<rss>stored</rss>", '"v1"')
    fetch(server, tmp_path)
    return tmp_path


# Objects and records as snapshot.py writes them


@pytest.mark.spec("REC-4", "OBJ-1", "FETCH-14")
def test_a_fetch_stores_the_body_received_and_appends_one_record(
    server: Server, tmp_path: Path
) -> None:
    body = b"<rss>first</rss>"
    server.routes["/feed.xml"] = with_etag(body, '"v1"')

    record = fetch(server, tmp_path)

    assert (record["status"], record["error"]) == (200, None)
    assert (record["sha256"], record["bytes"]) == (sha(body), len(body))
    assert record["object"] == f"objects/{sha(body)[:2]}/{sha(body)}.gz"
    assert gzip.decompress((tmp_path / record["object"]).read_bytes()) == body
    assert log(tmp_path) == [record]
    assert verify.verify(tmp_path) == []


@pytest.mark.spec("FETCH-14", "OBJ-2")
def test_the_object_is_in_place_before_its_record(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server.routes["/feed.xml"] = plain(b"<rss>ordered</rss>")
    seen: list[bool] = []
    append = snapshot.append

    def checking_append(path: Path, record: dict[str, Any]) -> None:
        seen.append((tmp_path / record["object"]).exists())
        append(path, record)

    monkeypatch.setattr(snapshot, "append", checking_append)

    fetch(server, tmp_path)

    assert seen == [True]
    assert not list((tmp_path / "objects").glob("*.tmp"))


@pytest.mark.spec("OBJ-2")
def test_identical_bodies_are_stored_once(server: Server, tmp_path: Path) -> None:
    server.routes["/feed.xml"] = plain(b"<rss>plain</rss>")

    fetch(server, tmp_path)
    fetch(server, tmp_path)

    assert [r["sha256"] for r in log(tmp_path)] == [sha(b"<rss>plain</rss>")] * 2
    assert len(objects(tmp_path)) == 1
    assert not list((tmp_path / "objects").glob("*.tmp"))


@pytest.mark.spec("REC-9")
def test_records_keep_the_tables_field_order(server: Server, tmp_path: Path) -> None:
    server.routes["/feed.xml"] = plain(b"<rss/>")

    record = fetch(server, tmp_path)

    assert list(record) == list(verify.FIELDS)


@pytest.mark.spec("REC-9")
def test_readers_ignore_fields_they_dont_know(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record | {"added_later": True}])

    assert verify.verify(stored) == []


@pytest.mark.spec("REC-10")
def test_a_record_keeps_each_responses_date_as_received(server: Server, tmp_path: Path) -> None:
    dates = ["Tue, 13 Oct 2026 01:04:12 +0700", "Tuesday, 13-Oct-26 01:04:13 GMT"]
    server.dates = list(dates)
    server.routes["/feed.xml"] = with_etag(b"<rss/>", '"v1"')

    first = fetch(server, tmp_path)
    second = fetch(server, tmp_path)

    assert [first["status"], second["status"]] == [200, 304]
    assert [first["date"], second["date"]] == dates
    assert verify.verify(tmp_path) == []


@pytest.mark.spec("REC-10", "FETCH-8")
def test_a_retried_fetch_keeps_its_final_responses_date(server: Server, tmp_path: Path) -> None:
    server.dates = ["Tue, 13 Oct 2026 01:04:12 GMT", "Tue, 13 Oct 2026 01:04:14 GMT"]
    server.routes["/feed.xml"] = replies((503, {}, b""), (200, {}, b"<rss/>"))

    record = fetch(server, tmp_path, [])

    assert (record["attempts"], record["date"]) == (2, "Tue, 13 Oct 2026 01:04:14 GMT")


@pytest.mark.spec("REC-10")
def test_received_at_is_when_the_status_and_headers_arrived(server: Server, tmp_path: Path) -> None:
    """After the server's wait before its headers, and before its body."""
    server.delay, server.body_delay = 0.2, 1.0
    server.routes["/feed.xml"] = plain(b"<rss>slow</rss>")
    scoped(server, tmp_path)
    clocked = snapshot.Fetcher(UA, min_interval=0)  # with snapshot.py's own wall clock

    record = snapshot.snapshot(clocked, tmp_path, "notices", server.url(), tool=TOOL)

    sent, arrived = (datetime.fromisoformat(record[f]) for f in ("requested_at", "received_at"))
    assert record["sha256"] == sha(b"<rss>slow</rss>")
    assert 0.15 <= (arrived - sent).total_seconds() < 0.8


# Versions


@pytest.mark.spec("FETCH-4", "REC-5")
def test_an_unchanged_feed_is_confirmed_without_a_copy(server: Server, tmp_path: Path) -> None:
    modified = "Tue, 06 Oct 2026 12:00:00 GMT"
    server.routes["/feed.xml"] = with_etag(b"<rss>same</rss>", '"v1"', modified)
    first = fetch(server, tmp_path)

    second = fetch(server, tmp_path)

    sent = server.requests[1][1]
    assert (sent["if-none-match"], sent["if-modified-since"]) == ('"v1"', modified)
    assert second["status"] == 304
    assert (second["sha256"], second["object"]) == (first["sha256"], first["object"])
    assert len(objects(tmp_path)) == 1
    assert verify.verify(tmp_path) == []


@pytest.mark.spec("VER-1")
def test_versions_are_numbered_by_first_appearance(
    server: Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for body in (b"<rss>A</rss>", b"<rss>B</rss>", b"<rss>A</rss>"):
        server.routes["/feed.xml"] = plain(body)
        fetch(server, tmp_path)

    assert verify.verify(tmp_path, verbose=True) == []
    listing = capsys.readouterr().out
    assert f"v1 {sha(b'<rss>A</rss>')[:12]}" in listing
    assert "fetched 2x" in listing
    assert f"v2 {sha(b'<rss>B</rss>')[:12]}" in listing
    assert len(objects(tmp_path)) == 2


@pytest.mark.spec("REC-5")
def test_a_304_to_a_request_without_validators_names_no_version(
    server: Server, tmp_path: Path
) -> None:
    server.routes["/feed.xml"] = lambda headers: (304, {}, b"")

    record = fetch(server, tmp_path)

    assert (record["status"], record["sha256"]) == (304, None)
    assert record["error"]
    assert verify.verify(tmp_path) == []


# Fetching


@pytest.mark.spec("FETCH-2", "FETCH-3", "ID-1")
def test_the_users_agent_is_sent_and_no_request_header_is_stored(
    server: Server, tmp_path: Path
) -> None:
    server.routes["/feed.xml"] = with_etag(b"<rss/>", '"v1"')

    fetch(server, tmp_path)
    fetch(server, tmp_path)

    sent = server.requests[0][1]
    assert (sent["user-agent"], sent["accept-encoding"]) == (UA, "gzip")
    for path in tmp_path.rglob("*"):
        if path.is_file():
            content = path.read_bytes()
            content = gzip.decompress(content) if path.suffix == ".gz" else content
            assert UA.encode() not in content
            assert b"if-none-match" not in content.lower()


@pytest.mark.spec("FETCH-5", "FETCH-8", "REC-10")
def test_an_attempt_that_times_out_counts_as_no_response(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot, "TIMEOUT", 0.05)
    server.delay = 0.3
    server.routes["/feed.xml"] = plain(b"<rss/>")

    record = fetch(server, tmp_path, [])

    assert (record["status"], record["attempts"], record["sha256"]) == (None, 4, None)
    assert (record["received_at"], record["date"]) == (None, None)
    assert record["error"].startswith("no response")
    assert verify.verify(tmp_path) == []


@pytest.mark.spec("FETCH-6")
def test_requests_are_spaced_by_the_minimum_interval(server: Server) -> None:
    server.routes["/feed.xml"] = plain(b"<rss/>")
    # Each request reads the clock before it and again as it is sent.
    times = iter([10.0, 10.0, 10.05, 10.2])
    sleeps: list[float] = []
    spaced = snapshot.Fetcher(UA, sleep=sleeps.append, clock=lambda: next(times))

    for _ in range(2):
        response = spaced.get(server.url(), {})
        response.body.read(1 << 16)
        response.body.close()

    assert sleeps == [pytest.approx(0.15)]


@pytest.mark.spec("FETCH-6")
def test_records_give_the_time_after_the_wait(server: Server, tmp_path: Path) -> None:
    """requested_at is when the request was sent (§6), after the FETCH-6 wait."""
    server.routes["/a.xml"] = plain(b"<rss/>")
    server.routes["/b.xml"] = plain(b"<rss/>")
    # One fake clock, which only the wait advances, for both the wait and the record.
    seconds = [1_791_000_000.0]

    def sleep(delay: float) -> None:
        seconds[0] += delay

    def wall() -> str:
        when = datetime.fromtimestamp(seconds[0], UTC)
        return when.isoformat(timespec="milliseconds").replace("+00:00", "Z")

    paced = snapshot.Fetcher(UA, sleep=sleep, clock=lambda: seconds[0], wall=wall)
    scoped(server, tmp_path)
    urls = [server.url("/a.xml"), server.url("/b.xml")]

    assert snapshot.run(paced, tmp_path, "notices", urls, tool=TOOL) == 0
    first, second = (datetime.fromisoformat(r["requested_at"]) for r in log(tmp_path))
    assert (second - first).total_seconds() == pytest.approx(0.2, abs=0.001)


@pytest.mark.spec("FETCH-8", "FETCH-14")
def test_a_retry_waits_for_retry_after_and_records_once(server: Server, tmp_path: Path) -> None:
    server.routes["/feed.xml"] = replies((503, {"Retry-After": "7"}, b""), (200, {}, b"<rss/>"))
    sleeps: list[float] = []

    record = fetch(server, tmp_path, sleeps)

    assert (record["status"], record["attempts"]) == (200, 2)
    assert sleeps == [7.0]
    assert len(log(tmp_path)) == 1


@pytest.mark.spec("FETCH-8")
def test_retries_back_off_and_stop_after_four_attempts(server: Server, tmp_path: Path) -> None:
    server.routes["/feed.xml"] = lambda headers: (503, {}, b"")
    sleeps: list[float] = []

    record = fetch(server, tmp_path, sleeps)

    assert sleeps == [2.0, 4.0, 8.0]
    assert (record["status"], record["attempts"], record["sha256"]) == (503, 4, None)
    assert verify.verify(tmp_path) == []


@pytest.mark.spec("FETCH-8")
def test_a_long_retry_after_is_capped(server: Server, tmp_path: Path) -> None:
    server.routes["/feed.xml"] = replies((429, {"Retry-After": "600"}, b""), (200, {}, b"<rss/>"))
    sleeps: list[float] = []

    fetch(server, tmp_path, sleeps)

    assert sleeps == [60.0]


@pytest.mark.spec("FETCH-9")
def test_a_403_is_recorded_and_stops_the_run(server: Server, tmp_path: Path) -> None:
    server.routes["/a.xml"] = lambda headers: (403, {}, b"Request Rate Threshold Exceeded")
    server.routes["/b.xml"] = plain(b"<rss/>")

    assert run(server, tmp_path, "/a.xml", "/b.xml") == 1

    [record] = log(tmp_path)
    assert (record["status"], record["attempts"], record["sha256"]) == (403, 1, None)
    assert [path for path, _ in server.requests] == ["/a.xml"]
    assert verify.verify(tmp_path) == []


@pytest.mark.spec("FETCH-10")
def test_another_status_is_recorded_without_a_retry_and_the_run_continues(
    server: Server, tmp_path: Path
) -> None:
    server.routes["/a.xml"] = lambda headers: (404, {}, b"Not Found")
    server.routes["/b.xml"] = plain(b"<rss/>")

    assert run(server, tmp_path, "/a.xml", "/b.xml") == 1

    missing, found = log(tmp_path)
    assert (missing["status"], missing["attempts"], missing["sha256"]) == (404, 1, None)
    assert found["sha256"] == sha(b"<rss/>")


@pytest.mark.spec("FETCH-10")
def test_a_run_that_stores_every_fetch_succeeds(server: Server, tmp_path: Path) -> None:
    server.routes["/a.xml"] = plain(b"<rss>a</rss>")
    server.routes["/b.xml"] = plain(b"<rss>b</rss>")

    assert run(server, tmp_path, "/a.xml", "/b.xml") == 0


@pytest.mark.spec("FETCH-11")
@pytest.mark.parametrize("encoding", ["gzip", "x-gzip"])
def test_a_gzip_encoded_body_is_stored_decoded(
    encoding: str, server: Server, tmp_path: Path
) -> None:
    body = b"<rss>" + b"x" * 10_000 + b"</rss>"
    server.routes["/feed.xml"] = lambda headers: (
        200,
        {"Content-Encoding": encoding},
        gzip.compress(body),
    )

    record = fetch(server, tmp_path)

    assert (record["sha256"], record["bytes"]) == (sha(body), len(body))


@pytest.mark.spec("FETCH-11")
def test_an_identity_encoded_body_is_stored_as_received(server: Server, tmp_path: Path) -> None:
    server.routes["/feed.xml"] = lambda headers: (200, {"Content-Encoding": "identity"}, b"<rss/>")

    record = fetch(server, tmp_path)

    assert record["sha256"] == sha(b"<rss/>")


@pytest.mark.spec("FETCH-12")
def test_another_encoding_is_recorded_and_not_stored(server: Server, tmp_path: Path) -> None:
    server.routes["/feed.xml"] = lambda headers: (200, {"Content-Encoding": "br"}, b"\x0b\x02")

    record = fetch(server, tmp_path)

    assert (record["status"], record["attempts"], record["sha256"]) == (200, 1, None)
    assert "Content-Encoding" in record["error"]
    assert objects(tmp_path) == []
    assert verify.verify(tmp_path) == []


@pytest.mark.spec("FETCH-13", "FETCH-8")
def test_a_body_cut_short_is_retried(server: Server, tmp_path: Path) -> None:
    server.routes["/feed.xml"] = replies(
        (200, {"Content-Length": "100"}, b"<rss>part"),
        (200, {}, b"<rss>whole</rss>"),
    )

    record = fetch(server, tmp_path, [])

    assert (record["attempts"], record["sha256"]) == (2, sha(b"<rss>whole</rss>"))
    assert len(objects(tmp_path)) == 1
    assert not list((tmp_path / "objects").glob("*.tmp"))


@pytest.mark.spec("FETCH-13")
def test_a_gzip_stream_that_ends_early_is_cut_short(server: Server, tmp_path: Path) -> None:
    whole = gzip.compress(b"<rss>" + b"y" * 5_000 + b"</rss>")
    server.routes["/feed.xml"] = lambda headers: (
        200,
        {"Content-Encoding": "gzip"},
        whole[: len(whole) // 2],
    )

    record = fetch(server, tmp_path, [])

    assert (record["status"], record["attempts"], record["sha256"]) == (200, 4, None)
    assert "cut short" in record["error"]
    assert objects(tmp_path) == []
    assert verify.verify(tmp_path) == []


@pytest.mark.spec("FETCH-2")
def test_the_command_needs_a_user_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)

    status = snapshot.main(["notices", "https://www.sec.gov/feed.xml", "--data", str(tmp_path)])

    assert status == 2
    assert not (tmp_path / "feeds").exists()


def agent_sent(monkeypatch: pytest.MonkeyPatch, data: Path) -> str | None:
    """The user agent the command would fetch with, or None if it refuses to start."""
    agents: list[str] = []

    def starting_run(
        fetcher: snapshot.Fetcher, data: Path, feed: str, urls: list[str], *, tool: str
    ) -> int:
        agents.append(fetcher.user_agent)
        return 0

    monkeypatch.setattr(snapshot, "run", starting_run)
    url = "https://www.sec.gov/feed.xml"
    allow(data, url)
    status = snapshot.main(["notices", url, "--data", str(data)])
    return agents[0] if status == 0 else None


@pytest.mark.spec("FETCH-15")
@pytest.mark.parametrize("environment", [None, "", "  "])
def test_the_user_agent_is_read_from_env_dev_when_the_environment_has_none(
    environment: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env_file: Path
) -> None:
    if environment is None:
        monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    else:
        monkeypatch.setenv("SEC_USER_AGENT", environment)
    env_file.write_text(
        f'\n# The SEC asks for a name and a contact address.\nOTHER=x\nSEC_USER_AGENT="{UA}"\n'
        "# SEC_USER_AGENT=Commented Out out@example.com\nSEC_USER_AGENT\n"
    )

    assert agent_sent(monkeypatch, tmp_path) == UA


@pytest.mark.spec("FETCH-15")
@pytest.mark.parametrize("environment", [UA, f"  {UA}  "])
def test_the_environments_user_agent_is_used_instead_of_env_dev(
    environment: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env_file: Path
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", environment)
    env_file.write_text("SEC_USER_AGENT=From The File file@example.com\n")

    assert agent_sent(monkeypatch, tmp_path) == UA


@pytest.mark.spec("FETCH-2", "FETCH-15")
@pytest.mark.parametrize("text", [None, "", "# SEC_USER_AGENT=Name name@example.com\n"])
def test_the_command_needs_a_user_agent_in_the_environment_or_env_dev(
    text: str | None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env_file: Path
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", "")
    if text is not None:
        env_file.write_text(text)

    assert agent_sent(monkeypatch, tmp_path) is None


@pytest.mark.spec("FETCH-15")
def test_the_repositorys_gitignore_ignores_env_dev() -> None:
    """The committed .gitignore, not only a clone's own .git/info/exclude."""
    root = Path(__file__).resolve().parent.parent
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--verbose", ".env.dev"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert ignored.stdout.startswith(".gitignore:")


@pytest.mark.spec("FETCH-1")
@pytest.mark.parametrize(
    "url",
    [
        "http://www.sec.gov/feed.xml",
        "https://example.com/feed.xml",
        "https://sec.gov.example.com/x",
    ],
)
def test_the_command_fetches_only_from_sec_hosts(
    url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", UA)

    assert snapshot.main(["notices", url, "--data", str(tmp_path)]) == 2
    assert not (tmp_path / "feeds").exists()


@pytest.mark.spec("SCOPE-3")
def test_the_command_fetches_only_urls_in_the_scope(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", UA)
    started: list[list[str]] = []

    def starting_run(
        fetcher: snapshot.Fetcher, data: Path, feed: str, urls: list[str], *, tool: str
    ) -> int:
        started.append(urls)
        return 0

    monkeypatch.setattr(snapshot, "run", starting_run)
    filings = "https://www.sec.gov/Archives/edgar/data/320193/"
    history = "https://data.sec.gov/submissions/CIK0000320193.json"
    allow(tmp_path, filings, history)

    def command(*urls: str) -> int:
        return snapshot.main(["filings", *urls, "--data", str(tmp_path)])

    assert command(filings + "index.json", history) == 0
    assert command(history, "https://www.sec.gov/Archives/edgar/data/3201930/") == 2
    assert command(history + "?page=2") == 2
    malformed = [{"url": history, "reason": "Tests."}, {"url": filings}]
    (tmp_path / "scope.json").write_text(json.dumps({"urls": malformed}))
    assert command(history) == 2
    (tmp_path / "scope.json").unlink()
    assert command(history) == 2
    assert started == [[filings + "index.json", history]]


# Invariants that verify.py checks


@pytest.mark.spec("LAY-1")
def test_verify_finds_a_file_outside_the_layout(stored: Path) -> None:
    (stored / "feeds" / "notices" / "notes.txt").write_text("stray")
    (stored / ".DS_Store").write_bytes(b"")

    assert ids(verify.verify(stored)) == ["LAY-1"]


@pytest.mark.spec("SCOPE-1")
@pytest.mark.parametrize("text", [None, "not json", '["https://www.sec.gov/"]'])
def test_verify_finds_a_scope_it_cant_read(text: str | None, stored: Path) -> None:
    path = stored / "scope.json"
    if text is None:
        path.unlink()
    else:
        path.write_text(text)

    assert ids(verify.verify(stored)) == ["SCOPE-1"]


@pytest.mark.spec("SCOPE-1")
@pytest.mark.parametrize(
    "entry",
    [
        {"url": "https://www.sec.gov/Archives/"},
        {"url": "https://www.sec.gov/Archives/", "reason": ""},
        "https://www.sec.gov/Archives/",
    ],
)
def test_verify_finds_a_scope_entry_without_a_reason(entry: Any, stored: Path) -> None:
    path = stored / "scope.json"
    scope = json.loads(path.read_text())
    scope["urls"].append(entry)
    path.write_text(json.dumps(scope))

    assert ids(verify.verify(stored)) == ["SCOPE-1"]


@pytest.mark.spec("SCOPE-2")
@pytest.mark.parametrize(
    ("entry", "covered"),
    [
        ("/feed.xml", True),
        ("/", True),
        ("/feed", False),
        ("/feed.xml/", False),
        ("/other/", False),
    ],
)
def test_verify_finds_a_record_the_scope_doesnt_cover(
    entry: str, covered: bool, server: Server, stored: Path
) -> None:
    allow(stored, server.url(entry))

    assert ids(verify.verify(stored)) == ([] if covered else ["SCOPE-2"])


@pytest.mark.spec("OBJ-1")
def test_verify_finds_a_changed_object(stored: Path) -> None:
    [record] = log(stored)
    (stored / record["object"]).write_bytes(gzip.compress(b"<rss>tampered</rss>"))

    assert ids(verify.verify(stored)) == ["OBJ-1"]


@pytest.mark.spec("OBJ-1")
def test_verify_finds_an_object_that_isnt_gzip(stored: Path) -> None:
    [record] = log(stored)
    (stored / record["object"]).write_bytes(b"<rss>not compressed</rss>")

    assert ids(verify.verify(stored)) == ["OBJ-1"]


@pytest.mark.spec("OBJ-3")
def test_verify_finds_unnamed_objects_and_unfinished_writes(stored: Path) -> None:
    extra = stored / "objects" / "ab" / ("ab" + "0" * 62 + ".gz")
    extra.parent.mkdir(exist_ok=True)
    extra.write_bytes(gzip.compress(b"orphan"))
    (stored / "objects" / "leftover.tmp").write_bytes(b"")

    problems = verify.verify(stored)

    assert sorted(set(ids(problems))) == ["OBJ-1", "OBJ-3"]
    assert sum("may be deleted" in p for p in problems) == 2


@pytest.mark.spec("REC-1", "REC-14")
@pytest.mark.parametrize(
    ("changes", "fields"),
    [
        ({"requested_at": "2026-10-06 12:00", "attempts": 0}, "requested_at, attempts"),
        ({"received_at": "2026-10-06 12:00", "date": 1791000000}, "received_at, date"),
        ({"tool": "fetcher@0123456"}, "tool"),
        ({"tool": "edgar-asof@0.1.0"}, "tool"),
    ],
)
def test_verify_finds_a_malformed_field(changes: dict[str, Any], fields: str, stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record | changes])

    problems = verify.verify(stored)

    assert sorted(ids(problems)) == ["REC-1", "REC-14"]
    [problem] = [p for p in problems if p.startswith("REC-1 ")]
    assert problem.endswith(f"missing or malformed {fields}")


@pytest.mark.spec("REC-1", "REC-14")
def test_verify_finds_a_line_that_isnt_a_record(stored: Path) -> None:
    path = stored / "feeds" / "notices" / "versions.jsonl"
    path.write_text(path.read_text() + "[1, 2]\nnot json\n")

    assert sorted(ids(verify.verify(stored))) == ["REC-1", "REC-1", "REC-14"]


@pytest.mark.spec("REC-1", "REC-11")
def test_verify_accepts_a_record_written_under_version_0_2_0(stored: Path) -> None:
    """Without received_at and date, which a reader takes as null (section 14)."""
    [record] = log(stored)
    write_log(stored, [{f: v for f, v in record.items() if f not in ("received_at", "date")}])

    assert verify.verify(stored) == []


@pytest.mark.spec("REC-1")
def test_verify_accepts_a_record_the_fetcher_wrote(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record | {"tool": FETCHER}])

    assert verify.verify(stored) == []


@pytest.mark.spec("REC-2")
def test_verify_finds_a_record_in_another_feeds_log(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record | {"feed": "filings"}])

    assert ids(verify.verify(stored)) == ["REC-2"]


@pytest.mark.spec("REC-3")
def test_verify_finds_a_partly_named_body(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record | {"bytes": None}])

    assert "REC-3" in ids(verify.verify(stored))


@pytest.mark.spec("REC-3")
def test_verify_finds_a_missing_object(stored: Path) -> None:
    [record] = log(stored)
    (stored / record["object"]).unlink()

    assert ids(verify.verify(stored)) == ["REC-3"]


@pytest.mark.spec("REC-4")
def test_verify_finds_a_200_that_names_nothing(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record, later(record, **NOTHING)])

    assert ids(verify.verify(stored)) == ["REC-4"]


@pytest.mark.spec("REC-5")
def test_verify_finds_a_304_naming_the_wrong_version(stored: Path) -> None:
    [record] = log(stored)
    other = sha(b"other")
    wrong = {"status": 304, "sha256": other, "object": f"objects/{other[:2]}/{other}.gz"}
    write_log(stored, [record, later(record, **wrong)])

    assert "REC-5" in ids(verify.verify(stored))


@pytest.mark.spec("REC-6")
def test_verify_finds_a_failure_that_names_a_body(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record, later(record, status=404)])

    assert ids(verify.verify(stored)) == ["REC-6"]


@pytest.mark.spec("REC-7")
def test_verify_finds_a_missing_response_without_an_error(stored: Path) -> None:
    [record] = log(stored)
    nothing = later(record, **NOTHING, status=None, error=None, received_at=None, date=None)
    write_log(stored, [record, nothing])

    assert ids(verify.verify(stored)) == ["REC-7"]


@pytest.mark.spec("REC-8")
def test_verify_finds_a_wrong_length(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record | {"bytes": record["bytes"] + 1}])

    assert ids(verify.verify(stored)) == ["REC-8"]


def two_versions(server: Server, data: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Two records of one URL, each with its own body."""
    for body in (b"<rss>A</rss>", b"<rss>B</rss>"):
        server.routes["/feed.xml"] = plain(body)
        fetch(server, data)
    first, second = log(data)
    return first, second


def naming(record: dict[str, Any]) -> dict[str, Any]:
    return {field: record[field] for field in ("sha256", "bytes", "object")}


@pytest.mark.spec("REC-5")
@pytest.mark.parametrize(("tool", "found"), [(TOOL, ["REC-5"]), (FETCHER, [])])
def test_verify_leaves_the_version_a_copied_304_names_to_the_cluster_store(
    tool: str, found: list[str], server: Server, tmp_path: Path
) -> None:
    """The fetcher's 304, which the transfer copied, names a version of the
    cluster store, which STORE-3 checks there, not the log's latest."""
    first, second = two_versions(server, tmp_path)
    confirmed = later(second, status=304, tool=tool, **naming(first))
    write_log(tmp_path, [first, second, confirmed])

    assert ids(verify.verify(tmp_path)) == found


@pytest.mark.spec("REC-5")
def test_verify_finds_a_copied_304_that_names_nothing_without_an_error(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record, later(record, **NOTHING, status=304, tool=FETCHER)])

    assert ids(verify.verify(stored)) == ["REC-5"]


@pytest.mark.spec("FETCH-4", "REC-5")
def test_snapshots_304_after_a_copied_record_names_the_copied_version(
    server: Server, tmp_path: Path
) -> None:
    """snapshot.py asks about the latest earlier line of the log with a
    version, whoever wrote it, so its 304 names the version copied there."""
    for body, etag in ((b"<rss>A</rss>", '"a"'), (b"<rss>B</rss>", '"b"')):
        server.routes["/feed.xml"] = with_etag(body, etag)
        fetch(server, tmp_path)
    first, second = log(tmp_path)
    copied = second | {"tool": FETCHER}
    write_log(tmp_path, [first, copied])

    confirmed = fetch(server, tmp_path)

    assert server.requests[-1][1].get("if-none-match") == '"b"'
    assert (confirmed["status"], confirmed["tool"]) == (304, TOOL)
    assert naming(confirmed) == naming(copied)
    assert verify.verify(tmp_path) == []


@pytest.mark.spec("REC-5")
@pytest.mark.parametrize(("names", "found"), [("copied", []), ("earlier", ["REC-5"])])
def test_verify_holds_snapshots_304_after_a_copied_record_to_the_copied_version(
    names: str, found: list[str], server: Server, tmp_path: Path
) -> None:
    """The latest earlier line of the log with a version, whoever wrote it."""
    first, second = two_versions(server, tmp_path)
    copied = second | {"tool": FETCHER}
    confirmed = later(
        copied, status=304, tool=TOOL, **naming(copied if names == "copied" else first)
    )
    write_log(tmp_path, [first, copied, confirmed])

    assert ids(verify.verify(tmp_path)) == found


NO_RESPONSE = {**NOTHING, "status": None, "error": "no response: timeout"}


@pytest.mark.spec("REC-11")
@pytest.mark.parametrize(
    ("changes", "without"),
    [
        ({"received_at": None}, ()),
        (NO_RESPONSE | {"date": None}, ()),
        (NO_RESPONSE | {"received_at": None}, ()),
        (NO_RESPONSE, ("received_at",)),
    ],
    ids=[
        "status-without-received_at",
        "received_at-without-status",
        "date-without-status",
        "0.2.0",
    ],
)
def test_verify_finds_received_at_or_date_that_its_status_doesnt_match(
    changes: dict[str, Any], without: tuple[str, ...], stored: Path
) -> None:
    [record] = log(stored)
    mismatched = {f: v for f, v in later(record, **changes).items() if f not in without}
    write_log(stored, [record, mismatched])

    assert ids(verify.verify(stored)) == ["REC-11"]


@pytest.mark.spec("REC-12")
@pytest.mark.parametrize(
    ("again", "found"),
    [
        (lambda record: record, ["REC-12"]),
        (lambda record: record | {"tool": FETCHER}, []),
        (later, []),
    ],
    ids=["same", "other-tool", "later"],
)
def test_verify_finds_a_record_held_twice(
    again: Callable[[dict[str, Any]], dict[str, Any]], found: list[str], stored: Path
) -> None:
    """In any two logs, since the URL, requested_at, and tool identify a fetch."""
    [record] = log(stored)
    write_log(stored, [again(record) | {"feed": "submissions"}], feed="submissions")

    problems = verify.verify(stored)

    assert ids(problems) == found
    assert all(p.endswith("as feeds/notices/versions.jsonl:1") for p in problems)


@pytest.mark.spec("REC-14")
@pytest.mark.parametrize(
    "changes",
    [{"requested_at": "2026-02-30T18:04:12.345Z"}, {"received_at": "2026-10-12T24:00:00.000Z"}],
)
def test_verify_finds_a_record_the_schema_refuses(changes: dict[str, Any], stored: Path) -> None:
    """Of REC-1's form, but not the schema's: a day its month lacks, or the hour 24."""
    [record] = log(stored)
    write_log(stored, [record | changes])

    [problem] = verify.verify(stored)

    assert problem.startswith("REC-14 feeds/notices/versions.jsonl:1: against record.schema.json, ")


@pytest.mark.spec("REC-14")
@pytest.mark.parametrize(
    ("text", "message"),
    [
        (None, "can't be read"),
        ('{"type": "object", "additionalProperties": false}', "uses additionalProperties"),
        ('{"properties": {"url": {"format": "uri"}}}', "uses format uri"),
        ('{"$ref": "https://example.com/record.json"}', "uses $ref https://example.com/"),
        ('{"type": "list"}', "uses type list"),
        ('{"$defs": {"timestamp": {"maxLength": 24}}}', "uses maxLength"),
        ('{"anyOf": [{"type": "integer", "maximum": 1}]}', "uses maximum"),
    ],
)
def test_verify_reports_a_schema_it_cant_read(
    text: str | None,
    message: str,
    stored: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So the schema can't gain a keyword that verify.py would ignore."""
    path = tmp_path_factory.mktemp("schemas") / "record.schema.json"
    if text is not None:
        path.write_text(text)
    monkeypatch.setattr(verify, "SCHEMA", path)

    [problem] = verify.verify(stored)

    assert problem.startswith(f"REC-14 record.schema.json: {message}")


def sparse(path: Path, size: int) -> None:
    """A file of size bytes that takes next to no space on disk."""
    with path.open("wb") as file:
        file.truncate(size)


MIB_50 = 50 * 2**20


@pytest.mark.spec("SCOPE-4")
@pytest.mark.parametrize(("size", "found"), [(MIB_50, []), (MIB_50 + 1, ["SCOPE-4"])])
def test_verify_finds_a_file_over_50_mib(size: int, found: list[str], stored: Path) -> None:
    sparse(stored / "README.md", size)

    assert ids(verify.verify(stored)) == found


@pytest.mark.spec("SCOPE-5")
def test_verify_finds_a_store_over_1_gb(stored: Path) -> None:
    """Every file under data/ counts, hidden ones too."""
    held = sum(path.stat().st_size for path in stored.rglob("*") if path.is_file())
    whole, rest = divmod(1_000_000_000 - held, MIB_50)
    for number in range(whole):
        sparse(stored / f".part-{number}", MIB_50)
    sparse(stored / ".rest", rest)

    assert verify.verify(stored) == []

    sparse(stored / ".rest", rest + 1)

    assert ids(verify.verify(stored)) == ["SCOPE-5"]


def manifest(data: Path, name: str, inputs: list[dict[str, str]], **fields: str) -> Path:
    path = data / "imports" / f"{name}.json"
    path.parent.mkdir(exist_ok=True)
    body: dict[str, Any] = {
        "import": name,
        "started_at": "2026-10-06T18:04:12.345Z",
        "tool": "pipeline/import.py@" + "1" * 40,
        "inputs": inputs,
    }
    path.write_text(json.dumps(body | fields))
    return path


@pytest.mark.spec("IMP-1", "IMP-2")
def test_verify_accepts_a_manifest_whose_inputs_are_held(stored: Path) -> None:
    [record] = log(stored)
    item = {"feed": "notices", "url": record["url"], "sha256": record["sha256"]}
    manifest(stored, "first-import", [item])

    assert verify.verify(stored) == []


@pytest.mark.spec("IMP-1")
def test_verify_finds_a_manifest_named_for_another_import(stored: Path) -> None:
    manifest(stored, "first-import", [], **{"import": "second-import"})

    assert ids(verify.verify(stored)) == ["IMP-1"]


@pytest.mark.spec("IMP-1")
def test_verify_finds_a_manifest_without_its_fields(stored: Path) -> None:
    (stored / "imports").mkdir()
    (stored / "imports" / "bare.json").write_text('{"import": "bare"}')

    assert ids(verify.verify(stored)) == ["IMP-1"]


@pytest.mark.spec("IMP-2")
def test_verify_finds_a_manifest_input_no_record_holds(stored: Path) -> None:
    [record] = log(stored)
    item = {"feed": "notices", "url": record["url"], "sha256": sha(b"x")}
    manifest(stored, "first-import", [item])

    assert ids(verify.verify(stored)) == ["IMP-2"]


# History, against a git repository


def git(repo: Path, *args: str) -> None:
    settings = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    settings += ["-c", "commit.gpgsign=false"]
    subprocess.run(["git", *settings, "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def committed(server: Server, tmp_path: Path) -> Path:
    """A repository whose HEAD holds one fetch, a manifest, and their object."""
    data = tmp_path / "data"
    server.routes["/feed.xml"] = with_etag(b"<rss>committed</rss>", '"v1"')
    record = fetch(server, data)
    item = {"feed": "notices", "url": record["url"], "sha256": record["sha256"]}
    manifest(data, "first-import", [item])
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "data")
    git(tmp_path, "commit", "-q", "-m", "Store a fetch")
    return data


@pytest.mark.spec("LOG-1", "LOG-2")
def test_appending_since_a_commit_keeps_history(server: Server, committed: Path) -> None:
    fetch(server, committed)
    server.routes["/feed.xml"] = plain(b"<rss>changed</rss>")
    fetch(server, committed)

    assert verify.verify(committed, since="HEAD") == []


@pytest.mark.spec("LOG-1")
def test_verify_finds_a_log_edited_since_a_commit(committed: Path) -> None:
    [record] = log(committed)
    write_log(committed, [record | {"etag": '"edited"'}])

    assert ids(verify.verify(committed, since="HEAD")) == ["LOG-1"]


@pytest.mark.spec("LOG-2")
def test_verify_finds_an_object_removed_since_a_commit(committed: Path) -> None:
    [record] = log(committed)
    (committed / record["object"]).unlink()

    assert "LOG-2" in ids(verify.verify(committed, since="HEAD"))


@pytest.mark.spec("LOG-2")
def test_verify_finds_a_manifest_changed_since_a_commit(committed: Path) -> None:
    path = committed / "imports" / "first-import.json"
    path.write_text(path.read_text().replace("2026-10-06", "2026-10-07"))

    assert ids(verify.verify(committed, since="HEAD")) == ["LOG-2"]


@pytest.mark.spec("LOG-1")
def test_verify_reports_a_ref_that_isnt_a_commit(committed: Path) -> None:
    [problem] = verify.verify(committed, since="no-such-ref")

    assert problem.startswith("LOG-1 ")


# The invariants after any sequence of fetches

OUTCOMES: dict[str, Callable[[], Route]] = {
    "body-a": lambda: with_etag(b"<rss>a</rss>", '"a"'),
    "body-b": lambda: with_etag(b"<rss>b</rss>", '"b"'),
    "no-validators": lambda: plain(b"<rss>c</rss>"),
    "gzip": lambda: (
        lambda headers: (
            200,
            {"Content-Encoding": "gzip"},
            gzip.compress(b"<rss>a</rss>"),
        )
    ),
    "retry-then-body": lambda: replies((503, {}, b""), (200, {}, b"<rss>b</rss>")),
    "unvalidated-304": lambda: lambda headers: (304, {}, b""),
    "not-found": lambda: lambda headers: (404, {}, b""),
    "forbidden": lambda: lambda headers: (403, {}, b""),
    "other-encoding": lambda: lambda headers: (200, {"Content-Encoding": "br"}, b"x"),
    "cut-short": lambda: lambda headers: (200, {"Content-Length": "50"}, b"<rss>"),
}
STEPS = st.lists(
    st.tuples(st.sampled_from(["/a.xml", "/b.xml"]), st.sampled_from(sorted(OUTCOMES))),
    max_size=8,
)


def contents(data: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in data.rglob("*") if path.is_file()}


@pytest.mark.spec(
    "LAY-1", "OBJ-1", "OBJ-3",
    "REC-1", "REC-2", "REC-3", "REC-4", "REC-5", "REC-6", "REC-7", "REC-8",
    "REC-10", "REC-11", "REC-12", "REC-14",
    "LOG-1", "LOG-2",
)  # fmt: skip
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(steps=STEPS)
def test_the_invariants_hold_after_any_sequence_of_fetches(
    server: Server, steps: list[tuple[str, str]]
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        data = Path(directory)
        for path, outcome in steps:
            before = contents(data)
            server.routes[path] = OUTCOMES[outcome]()
            with contextlib.suppress(snapshot.Stop):
                fetch(server, data, [], path)
            assert verify.verify(data) == []
            after = contents(data)
            for name, old in before.items():
                if name.name == "versions.jsonl":
                    assert after[name].startswith(old)
                else:
                    assert after[name] == old
        for record in snapshot.records(data / "feeds" / "notices" / "versions.jsonl"):
            assert list(record) == list(verify.FIELDS)
