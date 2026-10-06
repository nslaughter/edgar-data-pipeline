# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check the snapshot store's invariants (spec/snapshots.md).

Checks every invariant in the specification's index that `verify` checks:
the layout (LAY-1), the scope in data/scope.json (SCOPE-1), the objects
(OBJ-1, OBJ-3), every record in data/feeds/*/versions.jsonl (REC-1 to REC-8,
SCOPE-2), and every import manifest (IMP-1, IMP-2). With --since REF it also
checks that, since the commit REF, logs were only appended to (LOG-1) and
objects and manifests only added (LOG-2). Each problem starts with the ID it
breaks. Exits 1 on any problem.

    uv run scripts/verify.py                 # -v lists each URL's versions
    uv run scripts/verify.py --since origin/main
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import subprocess
import sys
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
TOOL = re.compile(r"scripts/snapshot\.py@(?:[0-9a-f]{40}(?:\+dirty)?|unknown)")


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def is_str(value: Any) -> bool:
    return isinstance(value, str)


def optional(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    return lambda value: value is None or check(value)


def matches(pattern: re.Pattern[str]) -> Callable[[Any], bool]:
    return lambda value: isinstance(value, str) and pattern.fullmatch(value) is not None


# REC-1: every field of the record table, with its type and form.
FIELDS: dict[str, Callable[[Any], bool]] = {
    "feed": matches(NAME),
    "url": is_str,
    "requested_at": matches(TIMESTAMP),
    "attempts": lambda v: is_int(v) and v >= 1,
    "status": optional(is_int),
    "error": optional(is_str),
    "final_url": optional(is_str),
    "etag": optional(is_str),
    "last_modified": optional(is_str),
    "content_type": optional(is_str),
    "sha256": optional(matches(SHA256)),
    "bytes": optional(lambda v: is_int(v) and v >= 0),
    "object": optional(is_str),
    "tool": matches(TOOL),
}
STORED = ("sha256", "bytes", "object")


def object_path(sha256: str) -> str:
    return f"objects/{sha256[:2]}/{sha256}.gz"


def digest(path: Path) -> tuple[str, int] | None:
    """The SHA-256 and length of an object's decompressed bytes, or None if not gzip."""
    sha256 = hashlib.sha256()
    size = 0
    try:
        with gzip.open(path, "rb") as file:
            while chunk := file.read(1 << 16):
                sha256.update(chunk)
                size += len(chunk)
    except (OSError, EOFError, zlib.error):
        return None
    return sha256.hexdigest(), size


def read_scope(data: Path) -> tuple[list[str] | None, list[str]]:
    """The URLs data/scope.json lists, or None if it can't be read, and how it breaks SCOPE-1."""
    try:
        scope = json.loads((data / "scope.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, ["missing; the store declares its scope here"]
    except json.JSONDecodeError as err:
        return None, [f"not JSON ({err})"]
    if not isinstance(scope, dict) or not isinstance(scope.get("urls"), list):
        return None, ["not a JSON object with a list of urls"]
    entries, problems = [], []
    for number, entry in enumerate(scope["urls"], 1):
        reason = entry.get("reason") if isinstance(entry, dict) else None
        if isinstance(entry, dict) and is_str(entry.get("url")) and is_str(reason) and reason:
            entries.append(entry["url"])
        else:
            problems.append(f"entry {number} needs a url and a reason")
    return entries, problems


def covers(scope: list[str], url: str) -> bool:
    """An entry ending in / covers every URL that starts with it; any other covers only itself."""
    return any(url == entry or (entry.endswith("/") and url.startswith(entry)) for entry in scope)


def outcome_problems(record: dict[str, Any], latest: str | None) -> list[tuple[str, str]]:
    """REC-3 to REC-7 for one well-formed record; latest is the URL's latest earlier version."""
    problems = []
    status, error, sha256 = record["status"], record["error"], record["sha256"]
    stored = [record[field] is not None for field in STORED]
    if any(stored) and not all(stored):
        problems.append(("REC-3", "sha256, bytes, and object must all be set or all null"))
    elif sha256 and record["object"] != object_path(sha256):
        problems.append(("REC-3", f"object should be {object_path(sha256)}"))
    if status == 200 and error is None and not sha256:
        problems.append(("REC-4", "a 200 without an error must name its body"))
    if status == 304 and sha256 and sha256 != latest:
        problems.append(("REC-5", "a 304 must name the URL's latest earlier version"))
    if status == 304 and not sha256 and error is None:
        problems.append(("REC-5", "a 304 that names no version must have an error"))
    if sha256 and (status not in (200, 304) or (status == 200 and error is not None)):
        problems.append(("REC-6", f"a {status} with error {error!r} can't name a body"))
    if status is None and error is None:
        problems.append(("REC-7", "a fetch with no response must have an error"))
    return problems


class Checker:
    def __init__(self, data: Path) -> None:
        self.data = data
        self.problems: list[str] = []
        self.digests: dict[Path, tuple[str, int] | None] = {}
        self.named: set[Path] = set()
        self.held: set[tuple[str, str, str]] = set()  # (feed, url, sha256), for IMP-2
        self.scope: list[str] | None = None  # for SCOPE-2; None if scope.json can't be read
        self.versions: dict[str, dict[str, dict[str, list[str]]]] = {}  # for -v

    def report(self, id: str, where: str, message: str) -> None:
        self.problems.append(f"{id} {where}: {message}")

    def digest(self, path: Path) -> tuple[str, int] | None:
        if path not in self.digests:
            self.digests[path] = digest(path)
        return self.digests[path]

    def check_layout(self) -> None:
        """LAY-1, and OBJ-3's unfinished writes."""
        for path in sorted(self.data.rglob("*")):
            relative = path.relative_to(self.data)
            if any(part.startswith(".") for part in relative.parts) or path.is_dir():
                continue
            parts = relative.parts
            if parts in (("README.md",), ("scope.json",)):
                continue
            in_feeds = len(parts) == 3 and parts[0] == "feeds"
            if in_feeds and parts[2] == "versions.jsonl" and NAME.fullmatch(parts[1]):
                continue
            if len(parts) == 2 and parts[0] == "objects" and path.suffix == ".tmp":
                self.report("OBJ-3", str(relative), "an unfinished write; it may be deleted")
                continue
            if len(parts) == 3 and parts[0] == "objects" and parts[2].endswith(".gz"):
                sha256 = parts[2].removesuffix(".gz")
                if SHA256.fullmatch(sha256) and parts[1] == sha256[:2]:
                    continue
            in_imports = len(parts) == 2 and parts[0] == "imports"
            if in_imports and path.suffix == ".json" and NAME.fullmatch(path.stem):
                continue
            self.report("LAY-1", str(relative), "not part of the store's layout")

    def check_scope(self) -> None:
        """SCOPE-1; check_record checks SCOPE-2 against the entries read here."""
        self.scope, problems = read_scope(self.data)
        for problem in problems:
            self.report("SCOPE-1", "scope.json", problem)

    def check_logs(self) -> None:
        for log in sorted((self.data / "feeds").glob("*/versions.jsonl")):
            latest: dict[str, str] = {}
            versions = self.versions.setdefault(log.parent.name, {})
            for number, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
                if line.strip():
                    self.check_record(
                        f"{log.relative_to(self.data)}:{number}", line, log, latest, versions
                    )

    def check_record(
        self,
        where: str,
        line: str,
        log: Path,
        latest: dict[str, str],
        versions: dict[str, dict[str, list[str]]],
    ) -> None:
        try:
            record = json.loads(line)
        except json.JSONDecodeError as err:
            self.report("REC-1", where, f"not JSON ({err})")
            return
        if not isinstance(record, dict):
            self.report("REC-1", where, "not a JSON object")
            return
        wrong = [f for f, ok in FIELDS.items() if f not in record or not ok(record[f])]
        sha256 = record.get("sha256") if "sha256" not in wrong else None
        if sha256:
            # Still the record that names this object, so OBJ-3 shouldn't call it unnamed.
            self.named.add(self.data / object_path(sha256))
        if wrong:
            self.report("REC-1", where, f"missing or malformed {', '.join(wrong)}")
            return
        if record["feed"] != log.parent.name:
            self.report("REC-2", where, f"feed {record['feed']!r} is in the {log.parent.name} log")
        url = record["url"]
        if self.scope is not None and not covers(self.scope, url):
            self.report("SCOPE-2", where, f"{url} is outside the scope in scope.json")
        for id, message in outcome_problems(record, latest.get(url)):
            self.report(id, where, message)
        if not sha256:
            return
        latest[url] = sha256
        self.held.add((record["feed"], url, sha256))
        versions.setdefault(url, {}).setdefault(sha256, []).append(record["requested_at"])
        path = self.data / object_path(sha256)
        if not path.exists():
            self.report("REC-3", where, f"{object_path(sha256)} doesn't exist")
            return
        found = self.digest(path)
        if found and found[0] == sha256 and found[1] != record["bytes"]:
            self.report("REC-8", where, f"the body has {found[1]} bytes, not {record['bytes']}")

    def check_objects(self) -> None:
        """OBJ-1 for every object, and OBJ-3: every object is named."""
        for path in sorted((self.data / "objects").glob("??/*.gz")):
            relative = path.relative_to(self.data)
            sha256 = path.name.removesuffix(".gz")
            found = self.digest(path)
            if found is None:
                self.report("OBJ-1", str(relative), "isn't readable gzip data")
            elif found[0] != sha256:
                self.report("OBJ-1", str(relative), f"decompresses to SHA-256 {found[0]}")
            if path not in self.named:
                self.report("OBJ-3", str(relative), "no record names it; it may be deleted")

    def check_manifests(self) -> None:
        """IMP-1 and IMP-2."""
        for path in sorted((self.data / "imports").glob("*.json")):
            where = str(path.relative_to(self.data))
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as err:
                self.report("IMP-1", where, f"not JSON ({err})")
                continue
            if not isinstance(manifest, dict):
                self.report("IMP-1", where, "not a JSON object")
                continue
            checks: dict[str, Callable[[Any], bool]] = {
                "import": matches(NAME),
                "started_at": matches(TIMESTAMP),
                "tool": lambda v: isinstance(v, str) and v != "",
                "inputs": lambda v: isinstance(v, list),
            }
            wrong = [f for f, ok in checks.items() if f not in manifest or not ok(manifest[f])]
            if wrong:
                self.report("IMP-1", where, f"missing or malformed {', '.join(wrong)}")
                continue
            if manifest["import"] != path.stem:
                self.report("IMP-1", where, f"names import {manifest['import']!r}")
            for number, item in enumerate(manifest["inputs"], 1):
                well_formed = (
                    isinstance(item, dict)
                    and is_str(item.get("feed"))
                    and is_str(item.get("url"))
                    and matches(SHA256)(item.get("sha256"))
                )
                if not well_formed:
                    self.report("IMP-2", where, f"input {number} needs feed, url, and sha256")
                elif (item["feed"], item["url"], item["sha256"]) not in self.held:
                    self.report(
                        "IMP-2",
                        where,
                        f"input {number} names {item['sha256'][:12]}, "
                        f"which no record of {item['feed']} {item['url']} holds",
                    )

    def check_history(self, ref: str) -> None:
        """LOG-1 and LOG-2 against the commit ref."""

        def git(*args: str) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(["git", "-C", str(self.data), *args], capture_output=True)

        top = git("rev-parse", "--show-toplevel")
        listing = git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        if top.returncode or listing.returncode:
            self.report("LOG-1", f"--since {ref}", "not a commit in this repository")
            return
        root = Path(top.stdout.decode().strip()).resolve()
        prefix = self.data.resolve().relative_to(root).as_posix()
        names = git("ls-tree", "-r", "--name-only", "--full-tree", ref, "--", prefix)
        for name in names.stdout.decode().splitlines():
            relative = Path(name).relative_to(prefix)
            parts = relative.parts
            current = self.data / relative
            before = git("show", f"{ref}:{name}").stdout
            now = current.read_bytes() if current.exists() else None
            if len(parts) == 3 and parts[0] == "feeds" and parts[2] == "versions.jsonl":
                if now is None or not now.startswith(before):
                    self.report(
                        "LOG-1", str(relative), f"changed other than by appending since {ref}"
                    )
            elif parts[0] in ("objects", "imports") and now != before:
                state = "removed" if now is None else "changed"
                self.report("LOG-2", str(relative), f"{state} since {ref}")


def verify(data: Path, *, since: str | None = None, verbose: bool = False) -> list[str]:
    checker = Checker(data)
    checker.check_layout()
    checker.check_scope()
    checker.check_logs()
    checker.check_objects()
    checker.check_manifests()
    if since:
        checker.check_history(since)
    if verbose:
        for feed, urls in checker.versions.items():
            print(feed)
            for url, versions in urls.items():
                print(f"  {url}")
                for number, (sha256, times) in enumerate(versions.items(), 1):
                    print(
                        f"    v{number} {sha256[:12]}  first {times[0]}  "
                        f"last {times[-1]}  fetched {len(times)}x"
                    )
        print(f"{len(checker.digests)} objects checked")
    return checker.problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the snapshot store's invariants.")
    parser.add_argument("-v", "--verbose", action="store_true", help="list versions")
    parser.add_argument("--since", metavar="REF", help="also check history since this commit")
    parser.add_argument("--data", type=Path, default=ROOT / "data", help="data directory")
    args = parser.parse_args(argv)
    problems = verify(args.data, since=args.since, verbose=args.verbose)
    for problem in problems:
        print(problem, file=sys.stderr)
    print("ok" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
