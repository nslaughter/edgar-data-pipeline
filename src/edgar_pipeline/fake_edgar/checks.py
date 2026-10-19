"""The one set of checks of the fetching rules, over the fake EDGAR's request log.

`check` reads entries of the request log, in the log's order, and the user
agent the writer was given, and reports each entry that breaks a fetching
rule (CHECK-1). The tests of snapshot.py run it on its requests (CHECK-2),
and the tests on kind on the fetcher's (CHECK-3), so both writers are held to
one reading of the rules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from typing import Any

# FETCH-6's 0.2 seconds, less 20 milliseconds for the time a request takes
# to arrive (CHECK-1).
INTERVAL = timedelta(milliseconds=180)


def check(entries: Iterable[Mapping[str, Any]], user_agent: str) -> list[str]:
    """Each entry that breaks a fetching rule, as its n, the rule, and why."""
    problems = []
    before: datetime | None = None
    stopped: int | None = None
    for entry in entries:
        n = entry["n"]
        sent = entry["headers"].get("user-agent")
        if sent != user_agent or "@" not in sent:
            problems.append(f"request {n}: FETCH-2: not the user agent given, with an @")
        received = datetime.fromisoformat(entry["received_at"])
        if before is not None and received - before < INTERVAL:
            seconds = (received - before).total_seconds()
            problems.append(f"request {n}: FETCH-6: {seconds:.3f} s after the request before it")
        if stopped is not None:
            problems.append(f"request {n}: FETCH-9: after request {stopped}'s 403")
        elif entry["status"] == 403:
            stopped = n
        before = received
    return problems
