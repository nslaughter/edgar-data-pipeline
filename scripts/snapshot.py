# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fetch EDGAR feed files into the snapshot store.

Implements the rules in spec/snapshots.md. Each fetch appends one record to
data/feeds/FEED/versions.jsonl (FETCH-14). A body is stored once, gzipped,
at data/objects/AA/SHA256.gz, named by the SHA-256 of its decoded bytes
(OBJ-1, OBJ-2), so an unchanged feed adds a record but no copy, and changed
content becomes a new version (VER-1). Nothing stored is overwritten.

Each user supplies their own SEC_USER_AGENT, a name and a contact address;
the script refuses to run without it and never records it (FETCH-2, ID-1).
Requests start at least 0.2 s apart, temporary failures are retried, and a
403 stops the run (FETCH-6, FETCH-8, FETCH-9).

    SEC_USER_AGENT="Name contact@example.com" \\
        uv run scripts/snapshot.py FEED URL [URL ...]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
FEED_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
SEC_HOSTS = frozenset({"www.sec.gov", "data.sec.gov"})
MIN_INTERVAL = 0.2  # FETCH-6: 5 requests a second, half the SEC's maximum
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
ATTEMPTS = 4
MAX_BACKOFF = 60.0
TIMEOUT = 60.0
CHUNK = 1 << 16
GZIP = frozenset({"gzip", "x-gzip"})  # FETCH-11: decoded before hashing
PLAIN = frozenset({"", "identity"})  # FETCH-11: stored as received


class Stop(Exception):
    """A response after which no further request should be made."""


class CutShort(Exception):
    """A body that ended before it was complete (FETCH-13)."""


class Body(Protocol):
    def read(self, amt: int, /) -> bytes: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]  # names lower-cased
    final_url: str
    body: Body


class Fetcher:
    """Sends requests no closer together than min_interval (FETCH-6)."""

    def __init__(
        self,
        user_agent: str,
        *,
        min_interval: float = MIN_INTERVAL,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.sleep = sleep
        self.clock = clock
        self._last = -math.inf

    def get(self, url: str, headers: dict[str, str]) -> Response:
        delay = self._last + self.min_interval - self.clock()
        if delay > 0:
            self.sleep(delay)
        self._last = self.clock()
        request = urllib.request.Request(
            url,
            headers={"User-Agent": self.user_agent, "Accept-Encoding": "gzip", **headers},
        )
        try:
            raw: Any = urllib.request.urlopen(request, timeout=TIMEOUT)
        except urllib.error.HTTPError as err:
            raw = err
        return Response(
            status=int(raw.status),
            headers={k.lower(): v for k, v in raw.headers.items()},
            final_url=str(raw.geturl()),
            body=cast(Body, raw),
        )


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def tool_version() -> str:
    """This script's commit, marked +dirty if the script differs from it (REC-1)."""
    script = Path(__file__).resolve()
    try:
        commit = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(ROOT), "diff", "--quiet", "HEAD", "--", str(script)],
            check=False,
        ).returncode
    except (OSError, subprocess.CalledProcessError):
        return "scripts/snapshot.py@unknown"
    return f"scripts/snapshot.py@{commit}{'+dirty' if dirty else ''}"


def records(path: Path) -> Iterator[dict[str, Any]]:
    if path.exists():
        with path.open(encoding="utf-8") as lines:
            for line in lines:
                if line.strip():
                    yield cast(dict[str, Any], json.loads(line))


def latest_version(path: Path, url: str) -> dict[str, Any] | None:
    """The latest record of this URL with a sha256 (FETCH-4)."""
    found = None
    for record in records(path):
        if record["url"] == url and record.get("sha256"):
            found = record
    return found


def read_chunk(body: Body) -> bytes:
    try:
        return body.read(CHUNK)
    except (http.client.HTTPException, ConnectionError, TimeoutError) as err:
        raise CutShort(f"body cut short: {err!r}") from err


def store(
    body: Body, objects: Path, *, gzip_encoded: bool, expected_length: int | None
) -> tuple[str, int, Path]:
    """Store a body by the SHA-256 of its decoded bytes, unless already stored (OBJ-2).

    expected_length is the Content-Length, which counts the bytes as sent.
    Raises CutShort if the body ends early or its gzip encoding doesn't decode.
    """
    objects.mkdir(parents=True, exist_ok=True)
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if gzip_encoded else None
    digest = hashlib.sha256()
    received = 0
    size = 0
    fd, name = tempfile.mkstemp(dir=objects, suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as file:
            with gzip.GzipFile(filename="", fileobj=file, mode="wb", mtime=0) as out:
                while chunk := read_chunk(body):
                    received += len(chunk)
                    try:
                        data = decoder.decompress(chunk) if decoder else chunk
                    except zlib.error as err:
                        raise CutShort(f"gzip encoding doesn't decode: {err}") from err
                    digest.update(data)
                    size += len(data)
                    out.write(data)
                if decoder:
                    data = decoder.flush()
                    if not decoder.eof:
                        raise CutShort("body cut short: the gzip stream ended early")
                    digest.update(data)
                    size += len(data)
                    out.write(data)
                if expected_length is not None and received != expected_length:
                    raise CutShort(f"body cut short: {received} of {expected_length} bytes arrived")
            file.flush()
            os.fsync(file.fileno())
        sha256 = digest.hexdigest()
        final = objects / sha256[:2] / f"{sha256}.gz"
        if final.exists():
            tmp.unlink()
        else:
            final.parent.mkdir(exist_ok=True)
            os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return sha256, size, final


def content_length(headers: dict[str, str]) -> int | None:
    try:
        return int(headers["content-length"])
    except (KeyError, ValueError):
        return None


def retry_delay(response: Response | None, attempt: int) -> float:
    """Retry-After if the server sent one, else 2 ** attempt; capped (FETCH-8)."""
    value = response.headers.get("retry-after") if response else None
    if value:
        try:
            return min(max(float(value), 0.0), MAX_BACKOFF)
        except ValueError:
            try:
                when = parsedate_to_datetime(value)
                seconds = (when - datetime.now(UTC)).total_seconds()
                return min(max(seconds, 0.0), MAX_BACKOFF)
            except (TypeError, ValueError):
                pass
    return min(2.0**attempt, MAX_BACKOFF)


def append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
        file.flush()
        os.fsync(file.fileno())


def snapshot(fetcher: Fetcher, data: Path, feed: str, url: str, *, tool: str) -> dict[str, Any]:
    """Fetch one URL, store its body if new, and append one record (FETCH-14).

    Raises Stop, after recording, on a 403.
    """
    versions = data / "feeds" / feed / "versions.jsonl"
    previous = latest_version(versions, url)
    conditional = {}
    if previous and previous.get("etag"):
        conditional["If-None-Match"] = previous["etag"]
    if previous and previous.get("last_modified"):
        conditional["If-Modified-Since"] = previous["last_modified"]

    record: dict[str, Any] = {}
    for attempt in range(1, ATTEMPTS + 1):
        requested_at = now()
        response: Response | None = None
        error = None
        try:
            response = fetcher.get(url, conditional)
        except (
            urllib.error.URLError,
            http.client.HTTPException,
            TimeoutError,
            ConnectionError,
        ) as err:
            error = f"no response: {err!r}"
        record = {
            "feed": feed,
            "url": url,
            "requested_at": requested_at,
            "attempts": attempt,
            "status": response.status if response else None,
            "error": error,
            "final_url": None,
            "etag": None,
            "last_modified": None,
            "content_type": None,
            "sha256": None,
            "bytes": None,
            "object": None,
            "tool": tool,
        }
        retry = response is None
        if response:
            try:
                if response.final_url != url:
                    record["final_url"] = response.final_url
                record["etag"] = response.headers.get("etag")
                record["last_modified"] = response.headers.get("last-modified")
                record["content_type"] = response.headers.get("content-type")
                if response.status == 200:
                    retry = store_body(response, data, record)
                elif response.status == 304:
                    confirm(previous, record)
                elif response.status in RETRY_STATUSES:
                    retry = True
            finally:
                response.body.close()
        if not retry:
            break
        if attempt < ATTEMPTS:
            fetcher.sleep(retry_delay(response, attempt))

    append(versions, record)
    if record["status"] == 403:
        raise Stop(f"{url}: 403; the SEC may be limiting this client. Stopping.")
    return record


def store_body(response: Response, data: Path, record: dict[str, Any]) -> bool:
    """Store a 200's body into the record; True if the fetch should be retried."""
    encoding = response.headers.get("content-encoding", "").strip().lower()
    if encoding not in GZIP | PLAIN:
        record["error"] = f"unsupported Content-Encoding: {encoding}"
        return False
    try:
        sha256, size, path = store(
            response.body,
            data / "objects",
            gzip_encoded=encoding in GZIP,
            expected_length=content_length(response.headers),
        )
    except CutShort as err:
        record["error"] = str(err)
        return True
    record |= {"sha256": sha256, "bytes": size, "object": path.relative_to(data).as_posix()}
    return False


def confirm(previous: dict[str, Any] | None, record: dict[str, Any]) -> None:
    """Point a 304 at the version its conditional request named (REC-5)."""
    if previous is None:
        record["error"] = "304 to a request that sent no validators"
        return
    record |= {
        "etag": record["etag"] or previous.get("etag"),
        "last_modified": record["last_modified"] or previous.get("last_modified"),
        "sha256": previous["sha256"],
        "bytes": previous["bytes"],
        "object": previous["object"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch EDGAR feed files into the snapshot store.")
    parser.add_argument("feed", help="feed name: lower-case letters, digits, hyphens")
    parser.add_argument("urls", nargs="+", metavar="url")
    parser.add_argument("--data", type=Path, default=ROOT / "data", help="data directory")
    args = parser.parse_args(argv)

    user_agent = os.environ.get("SEC_USER_AGENT", "").strip()
    if "@" not in user_agent:
        print(
            "Set SEC_USER_AGENT to your name and a contact address, "
            'such as "Name contact@example.com".',
            file=sys.stderr,
        )
        return 2
    if not FEED_NAME.fullmatch(args.feed):
        print(f"{args.feed!r} is not a feed name.", file=sys.stderr)
        return 2
    for url in args.urls:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in SEC_HOSTS:
            print(f"{url} is not an https URL on an SEC host.", file=sys.stderr)
            return 2

    return run(Fetcher(user_agent), args.data, args.feed, args.urls, tool=tool_version())


def run(fetcher: Fetcher, data: Path, feed: str, urls: list[str], *, tool: str) -> int:
    """Fetch each URL in turn; 1 if any fetch stored nothing or a 403 stopped the run.

    FETCH-9, FETCH-10.
    """
    failed = False
    for url in urls:
        try:
            record = snapshot(fetcher, data, feed, url, tool=tool)
        except Stop as stop:
            print(stop, file=sys.stderr)
            return 1
        outcome = record["sha256"][:12] if record["sha256"] else record["error"] or "not stored"
        print(f"{record['status']}  {outcome}  {url}")
        failed |= record["sha256"] is None
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
