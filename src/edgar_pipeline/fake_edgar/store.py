"""The stored versions the fake EDGAR serves, read once from a git store (SERVE-1).

A stored URL is one that a record in data/feeds/*/versions.jsonl names a
version of, by its sha256 (spec/fake-edgar.md, section 2). Its latest version
is the one its latest such record names, by requested_at, and each version
carries the Content-Type, ETag, and Last-Modified of the latest record of the
URL that names it (SERVE-3). Each version's object is read into memory as
stored, gzip data (OBJ-1), so nothing under data/ is read again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SEC_HOSTS = ("www.sec.gov", "data.sec.gov")  # FETCH-1


class StoreError(Exception):
    """A git store the fake EDGAR refuses to serve."""


@dataclass(frozen=True)
class Version:
    """One version of a stored URL, with its record's headers (SERVE-3)."""

    sha256: str
    object: bytes  # gzip data, as stored (OBJ-1)
    content_type: str | None
    etag: str | None
    last_modified: str | None


@dataclass(frozen=True)
class StoredURL:
    url: str
    latest: str  # the sha256 of its latest version
    versions: dict[str, Version]


def split(url: str) -> tuple[str, str] | None:
    """A URL on one of the SEC's hosts as its host and the rest, else None."""
    for host in SEC_HOSTS:
        prefix = f"https://{host}"
        if url.startswith(prefix) and url[len(prefix) :].startswith("/"):
            return host, url[len(prefix) :]
    return None


def records(data: Path) -> list[dict[str, Any]]:
    """Every record of every feed's log, by feed name, in each log's order."""
    found = []
    for log in sorted((data / "feeds").glob("*/versions.jsonl")):
        for number, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as err:
                raise StoreError(f"{log}:{number}: not JSON: {err}") from err
            if not isinstance(record, dict):
                raise StoreError(f"{log}:{number}: not a record")
            found.append(record)
    return found


def load(data: Path) -> dict[str, StoredURL]:
    """Each stored URL on one of the SEC's hosts, by its URL.

    Raises StoreError if data isn't a directory, if a version's object is
    missing, or if two stored URLs differ only in their host, since a
    request's target would then name both (SERVE-1).
    """
    if not data.is_dir():
        raise StoreError(f"{data} is not a directory")
    # For each URL, its latest record that names a version, and for each of
    # its versions the latest record that names that version.
    latest: dict[str, tuple[datetime, dict[str, Any]]] = {}
    naming: dict[tuple[str, str], tuple[datetime, dict[str, Any]]] = {}
    for record in records(data):
        url, sha256 = record.get("url"), record.get("sha256")
        if not isinstance(url, str) or not isinstance(sha256, str) or split(url) is None:
            continue
        try:
            when = datetime.fromisoformat(str(record["requested_at"]))
        except (KeyError, ValueError) as err:
            raise StoreError(f"{url}: a record without a valid requested_at") from err
        # Later in the logs wins a tie, as a later line of one log is later.
        if url not in latest or when >= latest[url][0]:
            latest[url] = (when, record)
        if (url, sha256) not in naming or when >= naming[(url, sha256)][0]:
            naming[(url, sha256)] = (when, record)

    by_path: dict[str, str] = {}
    for url in latest:
        _, path = split(url) or ("", url)
        if path in by_path:
            raise StoreError(f"{by_path[path]} and {url} differ only in their host")
        by_path[path] = url

    objects: dict[str, bytes] = {}
    versions: dict[str, dict[str, Version]] = {url: {} for url in latest}
    for (url, sha256), (_, record) in naming.items():
        if sha256 not in objects:
            stored = data / str(record.get("object"))
            try:
                objects[sha256] = stored.read_bytes()
            except OSError as err:
                raise StoreError(f"{url}: its version {sha256} has no object at {stored}") from err
        versions[url][sha256] = Version(
            sha256=sha256,
            object=objects[sha256],
            content_type=record.get("content_type"),
            etag=record.get("etag"),
            last_modified=record.get("last_modified"),
        )
    return {
        url: StoredURL(url=url, latest=str(record["sha256"]), versions=versions[url])
        for url, (_, record) in latest.items()
    }
