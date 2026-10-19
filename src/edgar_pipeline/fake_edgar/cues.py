"""Cues: responses a test asks the fake EDGAR to give in place of what it serves.

A cue is a JSON object that validates against
spec/schemas/fake-edgar-cue.schema.json (CUE-3, ADMIN-1). The fake EDGAR uses
only the standard library, so `parse` checks the schema's rules by hand, and
the tests hold it to the schema. Like the schema, it ignores fields it
doesn't know.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

URL = re.compile(r"https://(www|data)\.sec\.gov/")  # FETCH-1, matched at the start
SHA256 = re.compile(r"[0-9a-f]{64}")


class NotACue(ValueError):
    """A value that doesn't validate against the cue's schema."""


@dataclass(frozen=True)
class Response:
    """One response of a cue: a status, a version, a body cut short, or none (CUE-2)."""

    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    sha256: str | None = None
    cut: bool = False
    drop: bool = False


@dataclass
class Cue:
    """A cue's URL, or None for any, and the responses it has yet to give (CUE-1)."""

    url: str | None
    responses: deque[Response]


def integer(value: Any) -> int | None:
    """value as JSON Schema's integer, which includes a number such as 403.0."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def response(value: Any, where: str) -> Response:
    if not isinstance(value, dict):
        raise NotACue(f"{where} is not an object")
    status = None
    if "status" in value:
        status = integer(value["status"])
        if status is None or not 100 <= status <= 599:
            raise NotACue(f"{where}.status is not an integer from 100 to 599")
    headers = value.get("headers", {})
    if not isinstance(headers, dict) or not all(isinstance(v, str) for v in headers.values()):
        raise NotACue(f"{where}.headers is not an object of strings")
    body = value.get("body", "")
    if not isinstance(body, str):
        raise NotACue(f"{where}.body is not a string")
    sha256 = value.get("sha256")
    if "sha256" in value and not (isinstance(sha256, str) and SHA256.fullmatch(sha256)):
        raise NotACue(f"{where}.sha256 is not 64 lower-case hexadecimal digits")
    for flag in ("cut", "drop"):
        if flag in value and value[flag] is not True:
            raise NotACue(f"{where}.{flag} is not true")
    forms = [
        "status" in value,
        "sha256" in value or "cut" in value,
        "drop" in value,
    ]
    if forms.count(True) != 1:
        raise NotACue(f"{where} is not one of: a status; sha256, cut, or both; or drop alone")
    if ("headers" in value or "body" in value) and "status" not in value:
        raise NotACue(f"{where} has headers or a body without a status")
    return Response(
        status=status,
        headers=dict(headers),
        body=body.encode("utf-8"),
        sha256=sha256,
        cut="cut" in value,
        drop="drop" in value,
    )


def parse(value: Any) -> Cue:
    """A cue from a JSON value, or NotACue if it doesn't validate against the schema."""
    if not isinstance(value, dict):
        raise NotACue("a cue is an object")
    url = value.get("url")
    if "url" in value and not (isinstance(url, str) and URL.match(url)):
        raise NotACue("url is not a string beginning https://www.sec.gov/ or https://data.sec.gov/")
    if "responses" not in value:
        raise NotACue("responses is required")
    responses = value["responses"]
    if not isinstance(responses, list) or not responses:
        raise NotACue("responses is not a list of one or more responses")
    return Cue(
        url=url,
        responses=deque(response(item, f"responses[{i}]") for i, item in enumerate(responses)),
    )
