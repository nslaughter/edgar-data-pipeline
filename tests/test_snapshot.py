"""The snapshot and verify scripts against spec/snapshots.md, using a local HTTP server.

Each test carries the IDs of the statements it checks, as
@pytest.mark.spec(...); tests/test_spec_coverage.py requires every statement
checked by `verify` or `tests` to have one.
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import json
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
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

Reply = tuple[int, dict[str, str], bytes]
Route = Callable[[dict[str, str]], Reply]


@dataclass
class Server:
    base: str
    routes: dict[str, Route] = field(default_factory=dict)
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    delay: float = 0.0

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
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client gave up first, as FETCH-5's test intends

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


def fetcher(sleeps: list[float] | None = None) -> snapshot.Fetcher:
    def sleep(seconds: float) -> None:
        if sleeps is not None:
            sleeps.append(seconds)

    return snapshot.Fetcher(UA, min_interval=0, sleep=sleep)


def allow(data: Path, *urls: str) -> None:
    """Declare the scope, as the operator does before fetching (SCOPE-1)."""
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


@pytest.mark.spec("FETCH-5", "FETCH-8")
def test_an_attempt_that_times_out_counts_as_no_response(
    server: Server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(snapshot, "TIMEOUT", 0.05)
    server.delay = 0.3
    server.routes["/feed.xml"] = plain(b"<rss/>")

    record = fetch(server, tmp_path, [])

    assert (record["status"], record["attempts"], record["sha256"]) == (None, 4, None)
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


@pytest.mark.spec("REC-1")
def test_verify_finds_a_malformed_field(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record | {"requested_at": "2026-10-06 12:00", "attempts": 0}])

    [problem] = verify.verify(stored)

    assert problem.startswith("REC-1 ")
    assert "requested_at, attempts" in problem


@pytest.mark.spec("REC-1")
def test_verify_finds_a_line_that_isnt_a_record(stored: Path) -> None:
    path = stored / "feeds" / "notices" / "versions.jsonl"
    path.write_text(path.read_text() + "[1, 2]\nnot json\n")

    assert ids(verify.verify(stored)) == ["REC-1", "REC-1"]


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
    write_log(stored, [record, record | {"sha256": None, "bytes": None, "object": None}])

    assert ids(verify.verify(stored)) == ["REC-4"]


@pytest.mark.spec("REC-5")
def test_verify_finds_a_304_naming_the_wrong_version(stored: Path) -> None:
    [record] = log(stored)
    other = sha(b"other")
    wrong = {"status": 304, "sha256": other, "object": f"objects/{other[:2]}/{other}.gz"}
    write_log(stored, [record, record | wrong])

    assert "REC-5" in ids(verify.verify(stored))


@pytest.mark.spec("REC-6")
def test_verify_finds_a_failure_that_names_a_body(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record, record | {"status": 404}])

    assert ids(verify.verify(stored)) == ["REC-6"]


@pytest.mark.spec("REC-7")
def test_verify_finds_a_missing_response_without_an_error(stored: Path) -> None:
    [record] = log(stored)
    empty = {"sha256": None, "bytes": None, "object": None}
    write_log(stored, [record, record | empty | {"status": None, "error": None}])

    assert ids(verify.verify(stored)) == ["REC-7"]


@pytest.mark.spec("REC-8")
def test_verify_finds_a_wrong_length(stored: Path) -> None:
    [record] = log(stored)
    write_log(stored, [record | {"bytes": record["bytes"] + 1}])

    assert ids(verify.verify(stored)) == ["REC-8"]


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
