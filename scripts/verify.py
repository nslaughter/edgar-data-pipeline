# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check the snapshot store's invariants (spec/snapshots.md).

Checks every invariant in the specification's index that `verify` checks:
the layout (LAY-1), the scope in data/scope.json (SCOPE-1), the objects
(OBJ-1, OBJ-3), every record in data/feeds/*/versions.jsonl (REC-1 to REC-8,
REC-11, REC-12, SCOPE-2), each against spec/schemas/record.schema.json
(REC-14), every import manifest (IMP-1, IMP-2), and the store's budget
(SCOPE-4, SCOPE-5). With --since REF it also checks that, since the commit
REF, logs were only appended to (LOG-1) and objects and manifests only added
(LOG-2). Each problem starts with the ID it breaks. Exits 1 on any problem.

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
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "spec" / "schemas" / "record.schema.json"  # REC-14
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
SHA256 = re.compile(r"[0-9a-f]{64}")
TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
FETCHER = "fetcher@"  # the fetcher's records, which only the transfer copies here
TOOL = re.compile(
    r"scripts/snapshot\.py@(?:[0-9a-f]{40}(?:\+dirty)?|unknown)|" + FETCHER + r"[0-9a-f]{40}"
)
FILE_LIMIT = 50 * 1024 * 1024  # SCOPE-4: GitHub warns of a file over 50 MiB
TOTAL_LIMIT = 1_000_000_000  # SCOPE-5: GitHub recommends a repository under 1 GB


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
    "received_at": optional(matches(TIMESTAMP)),
    "attempts": lambda v: is_int(v) and v >= 1,
    "status": optional(is_int),
    "error": optional(is_str),
    "final_url": optional(is_str),
    "date": optional(is_str),
    "etag": optional(is_str),
    "last_modified": optional(is_str),
    "content_type": optional(is_str),
    "sha256": optional(matches(SHA256)),
    "bytes": optional(lambda v: is_int(v) and v >= 0),
    "object": optional(is_str),
    "tool": matches(TOOL),
}
# REC-1: the fields a record written under version 0.2.0 lacks, read as null.
ADDED = ("received_at", "date")
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


# REC-14: the keywords of JSON Schema's draft 2020-12 that schema_errors reads,
# those the record's schema uses. A schema with any other fails the check, so
# the schema can't outgrow it unnoticed.
ANNOTATIONS = frozenset({"$schema", "$id", "$comment", "$defs", "title", "description"})
KEYWORDS = ANNOTATIONS | {
    "$ref",
    "type",
    "anyOf",
    "required",
    "properties",
    "pattern",
    "minimum",
    "format",
}
TYPES: dict[str, Callable[[Any], bool]] = {
    "null": lambda v: v is None,
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: is_int(v) or (isinstance(v, float) and v.is_integer()),
    "number": lambda v: is_int(v) or isinstance(v, float),
    "string": is_str,
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def unread(schema: Any) -> list[str]:
    """What in schema and its subschemas schema_errors doesn't read."""
    if not isinstance(schema, dict):
        return [f"the schema {json.dumps(schema)}"]
    found = [keyword for keyword in schema if keyword not in KEYWORDS]
    types = schema.get("type", [])
    found += [
        f"type {t}" for t in (types if isinstance(types, list) else [types]) if t not in TYPES
    ]
    if schema.get("format", "date-time") != "date-time":
        found.append(f"format {schema['format']}")
    if not str(schema.get("$ref", "#/")).startswith("#/"):
        found.append(f"$ref {schema['$ref']}")
    for sub in [
        *schema.get("properties", {}).values(),
        *schema.get("$defs", {}).values(),
        *schema.get("anyOf", []),
    ]:
        found += unread(sub)
    return found


def read_schema(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """The record's JSON Schema, or None if schema_errors can't read it, and why (REC-14)."""
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return None, [f"can't be read ({err})"]
    if found := unread(schema):
        return None, [f"uses {', '.join(found)}, which verify.py doesn't read"]
    assert isinstance(schema, dict)  # unread found nothing
    return schema, []


def pointer(root: dict[str, Any], ref: str) -> Any:
    """The subschema of root that a $ref of the form #/a/b names."""
    found: Any = root
    for part in ref.removeprefix("#/").split("/"):
        found = found[part.replace("~1", "/").replace("~0", "~")]
    return found


def ecma(pattern: str) -> str:
    r"""A pattern's final $ as ECMA-262 reads it, at the end of the input only.

    Python's $ also matches before a final newline, so it becomes \Z, as the
    tests' validator reads it (tests/test_store_schemas.py).
    """
    ends = pattern.endswith("$") and not pattern.endswith(r"\$")
    return pattern[:-1] + r"\Z" if ends else pattern


def is_date_time(value: str) -> bool:
    """The format date-time: a time with its offset, on a day its month has."""
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def schema_errors(
    instance: Any, schema: dict[str, Any], root: dict[str, Any] | None = None, at: str = ""
) -> list[str]:
    """How instance fails schema, as JSON Schema's draft 2020-12 reads it (REC-14).

    It asserts formats, and reads a pattern as ECMA-262 does. root is the
    schema each $ref points into, and at is where instance is in the record.
    """
    root = schema if root is None else root
    what = f"{at} {json.dumps(instance, ensure_ascii=False)}" if at else "the record"
    errors: list[str] = []
    if "$ref" in schema:
        errors += schema_errors(instance, pointer(root, schema["$ref"]), root, at)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(TYPES[t](instance) for t in types):
            errors.append(f"{what} isn't of type {' or '.join(types)}")
    if "anyOf" in schema:
        failed = [schema_errors(instance, sub, root, at) for sub in schema["anyOf"]]
        if all(failed):
            alternatives = "; ".join(error for each in failed for error in each)
            errors.append(f"{what} matches none of anyOf: {alternatives}")
    if isinstance(instance, dict):
        errors += [
            f"{what} lacks {field}" for field in schema.get("required", []) if field not in instance
        ]
        for name, sub in schema.get("properties", {}).items():
            if name in instance:
                errors += schema_errors(instance[name], sub, root, f"{at}.{name}" if at else name)
    if isinstance(instance, str):
        if "pattern" in schema and not re.search(ecma(schema["pattern"]), instance):
            errors.append(f"{what} doesn't match {schema['pattern']}")
        if "format" in schema and not is_date_time(instance):
            errors.append(f"{what} isn't a {schema['format']}")
    if TYPES["number"](instance) and "minimum" in schema and instance < schema["minimum"]:
        errors.append(f"{what} is under its minimum, {schema['minimum']}")
    return errors


def outcome_problems(record: dict[str, Any], latest: str | None) -> list[tuple[str, str]]:
    """REC-3 to REC-7 and REC-11 for one well-formed record.

    latest is the URL's latest earlier version in the log. A 304 the transfer
    copied names one in the cluster store, which STORE-3 checks there (REC-5).
    """
    problems = []
    status, error, sha256 = record["status"], record["error"], record["sha256"]
    copied = record["tool"].startswith(FETCHER)
    stored = [record[field] is not None for field in STORED]
    if any(stored) and not all(stored):
        problems.append(("REC-3", "sha256, bytes, and object must all be set or all null"))
    elif sha256 and record["object"] != object_path(sha256):
        problems.append(("REC-3", f"object should be {object_path(sha256)}"))
    if status == 200 and error is None and not sha256:
        problems.append(("REC-4", "a 200 without an error must name its body"))
    if status == 304 and sha256 and sha256 != latest and not copied:
        problems.append(("REC-5", "a 304 must name the URL's latest earlier version"))
    if status == 304 and not sha256 and error is None:
        problems.append(("REC-5", "a 304 that names no version must have an error"))
    if sha256 and (status not in (200, 304) or (status == 200 and error is not None)):
        problems.append(("REC-6", f"a {status} with error {error!r} can't name a body"))
    if status is None and error is None:
        problems.append(("REC-7", "a fetch with no response must have an error"))
    # A record without received_at, as each written under version 0.2.0, is
    # held only to the second part.
    if "received_at" in record and (record["received_at"] is None) != (status is None):
        problems.append(("REC-11", "received_at must be set exactly when status is"))
    if status is None and record.get("date") is not None:
        problems.append(("REC-11", "a fetch with no response can't have a date"))
    return problems


class Checker:
    def __init__(self, data: Path) -> None:
        self.data = data
        self.problems: list[str] = []
        self.digests: dict[Path, tuple[str, int] | None] = {}
        self.named: set[Path] = set()
        self.held: set[tuple[str, str, str]] = set()  # (feed, url, sha256), for IMP-2
        self.scope: list[str] | None = None  # for SCOPE-2; None if scope.json can't be read
        self.schema: dict[str, Any] | None = None  # for REC-14; None if it can't be read
        self.fetches: dict[tuple[str, str, str], str] = {}  # (url, requested_at, tool), for REC-12
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
        self.schema, problems = read_schema(SCHEMA)
        for problem in problems:
            self.report("REC-14", SCHEMA.name, problem)
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
        if self.schema is not None and (errors := schema_errors(record, self.schema)):
            self.report("REC-14", where, f"against {SCHEMA.name}, {'; '.join(errors)}")
        if not isinstance(record, dict):
            self.report("REC-1", where, "not a JSON object")
            return
        wrong = [
            f for f, ok in FIELDS.items() if not (ok(record[f]) if f in record else f in ADDED)
        ]
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
        fetch = (url, record["requested_at"], record["tool"])
        if fetch in self.fetches:
            self.report(
                "REC-12", where, f"the same url, requested_at, and tool as {self.fetches[fetch]}"
            )
        self.fetches.setdefault(fetch, where)
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

    def check_budget(self) -> None:
        """SCOPE-4 and SCOPE-5, over every file under data/, hidden ones too."""
        total = 0
        for path in sorted(self.data.rglob("*")):
            if path.is_file():
                size = path.stat().st_size
                total += size
                if size > FILE_LIMIT:
                    where = str(path.relative_to(self.data))
                    self.report("SCOPE-4", where, f"{size:,} bytes, over {FILE_LIMIT:,}")
        if total > TOTAL_LIMIT:
            self.report(
                "SCOPE-5", "data/", f"its files total {total:,} bytes, over {TOTAL_LIMIT:,}"
            )

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
    checker.check_budget()
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
