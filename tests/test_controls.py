"""spec/controls.md's invariants: the definitions in spec/schemas/ against its
table of resources, and what the charts render from the umbrella chart's
values, the controls and the grants on them.

The charts are rendered with `helm template`, with values of these tests' own
for what the umbrella chart requires, so the tests need Helm, but neither a
cluster nor the network. The Go tests check the definitions generated from the
scheduler's types (API-2), and the API server's refusals (API-3). Each test
carries the IDs of the statements it checks, as @pytest.mark.spec(...);
tests/test_spec_coverage.py requires every statement checked by `tests` or
`kind` to have one, or to await a later task.
"""

from __future__ import annotations

import copy
import functools
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
SPEC = (ROOT / "spec" / "controls.md").read_text(encoding="utf-8")
GROUP = re.compile(r"in the API group `([^`]+)`")
# A row of section 4's table: kind, plural, scope, and the definition's path.
RESOURCE = re.compile(
    r"^\| `(\w+)` \| `(\w+)` \| (Namespaced|Cluster) \| \[`[^`]+`\]\((schemas/[^)]+)\) \|", re.M
)


def group() -> str:
    [found] = GROUP.findall(SPEC)
    return str(found)


def resources() -> list[tuple[str, str, str, str]]:
    return RESOURCE.findall(SPEC)


def definition(path: Path) -> dict[str, Any]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


@pytest.mark.spec("API-1")
def test_the_table_names_the_three_resources() -> None:
    assert sorted(kind for kind, _, _, _ in resources()) == ["Backfill", "SecAccess", "Source"]


@pytest.mark.spec("API-1")
@pytest.mark.parametrize(("kind", "plural", "scope", "path"), resources())
def test_each_definition_is_its_rows_at_v1alpha1_with_status(
    kind: str, plural: str, scope: str, path: str
) -> None:
    crd = definition(ROOT / "spec" / path)

    assert crd["apiVersion"] == "apiextensions.k8s.io/v1"
    assert crd["kind"] == "CustomResourceDefinition"
    assert crd["metadata"]["name"] == f"{plural}.{group()}"
    assert crd["spec"]["group"] == group()
    assert crd["spec"]["names"]["kind"] == kind
    assert crd["spec"]["names"]["plural"] == plural
    assert crd["spec"]["scope"] == scope
    [version] = crd["spec"]["versions"]
    assert version["name"] == "v1alpha1"
    assert version["served"] is True
    assert version["storage"] is True
    assert version["subresources"] == {"status": {}}


@pytest.mark.spec("API-1")
def test_no_other_definition_is_in_the_schemas() -> None:
    defined = {
        path.relative_to(ROOT / "spec").as_posix()
        for path in (ROOT / "spec" / "schemas").glob("*.yaml")
        if definition(path).get("kind") == "CustomResourceDefinition"
    }

    assert defined == {path for _, _, _, path in resources()}


# The charts, as their releases, in the order they're installed
# (spec/deployment.md, section 5): each chart's directory, release, and
# namespace.
RELEASES = [
    ("prometheus-crds", "prometheus-crds", "edgar"),
    ("cert-manager", "cert-manager", "cert-manager"),
    ("operators", "operators", "cnpg-system"),
    ("chart", "edgar", "edgar"),
]
NAMESPACE = "edgar"
# What the umbrella chart requires that its values don't set: any credentials
# of the right forms, the archive's name, and a commit (SECRET-1, IMG-1).
REQUIRED: dict[str, Any] = {
    "credentials": {
        "garage": {"rpcSecret": "0" * 64, "adminToken": "t" * 32},
        "garageKeys": {
            key: {"id": "GK" + "0" * 24, "secret": "0" * 64}
            for key in ("database-backups", "fetcher", "discoverer")
        },
        "database": {role: "p" * 16 for role in ("fetcher", "discoverer", "grafana")},
        "grafana": {"adminPassword": "p" * 16},
    },
    "database": {"backups": {"serverName": "test"}},
    "images": {"commit": "0" * 40},
}
KINDS = {"Source", "Backfill", "SecAccess"}


def kube_version() -> str:
    """The Kubernetes version of kind's nodes, which the charts render for."""
    config = yaml.safe_load((DEPLOY / "kind.yaml").read_text())
    match = re.match(r"kindest/node:(v[\d.]+)@", config["nodes"][0]["image"])
    assert match
    return match.group(1)


def render(chart: str, given: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    """`helm template` of a chart, as its release, with the values given."""
    [(release, namespace)] = [(r, n) for c, r, n in RELEASES if c == chart]
    command = ["helm", "template", release, str(DEPLOY / chart), "--namespace", namespace]
    return subprocess.run(
        [*command, "--kube-version", kube_version(), "--values", "-"],
        input=json.dumps(given if given is not None else REQUIRED if chart == "chart" else {}),
        capture_output=True,
        text=True,
        check=False,
    )


class Loader(yaml.SafeLoader):
    """YAML's safe loader, reading a bare `=` as the string Kubernetes reads it as."""


def as_string(loader: yaml.SafeLoader, node: yaml.Node) -> str:
    assert isinstance(node, yaml.ScalarNode)
    return str(loader.construct_scalar(node))


Loader.add_constructor("tag:yaml.org,2002:value", as_string)


def documents(output: str) -> list[dict[str, Any]]:
    return [doc for doc in yaml.load_all(output, Loader=Loader) if doc]


@functools.cache
def rendered(chart: str) -> tuple[dict[str, Any], ...]:
    result = render(chart)
    assert result.returncode == 0, result.stderr
    return tuple(documents(result.stdout))


def default_values() -> dict[str, Any]:
    loaded = yaml.safe_load((DEPLOY / "chart" / "values.yaml").read_text())
    assert isinstance(loaded, dict)
    return loaded


def with_controls(**controls: Any) -> dict[str, Any]:
    """The tests' values, with the controls given in place of the defaults'.
    Helm merges the values it's given into the chart's own, so given Sources
    replace the defaults' only when those are given as null."""
    given = copy.deepcopy(REQUIRED)
    if "sources" in controls:
        defaults = default_values()["controls"]["sources"]
        controls["sources"] = {**dict.fromkeys(defaults), **controls["sources"]}
    given["controls"] = controls
    return given


def controls_rendered(given: dict[str, Any]) -> list[dict[str, Any]]:
    result = render("chart", given)
    assert result.returncode == 0, result.stderr
    return [doc for doc in documents(result.stdout) if doc["kind"] in KINDS]


def refused(given: dict[str, Any]) -> bool:
    return render("chart", given).returncode != 0


def source(cadence: Any = 10, **fields: Any) -> dict[str, Any]:
    spec = {
        "feed": "submissions",
        "url": "https://data.sec.gov/submissions/CIK0000320193.json",
        "cadenceSeconds": cadence,
        **fields,
    }
    return {key: value for key, value in spec.items() if value is not None}


def backfill(**fields: Any) -> dict[str, Any]:
    spec = {"cik": 320193, "filingDateFrom": "2025-01-01", "filingDateTo": "2025-12-31", **fields}
    return {key: value for key, value in spec.items() if value is not None}


# VAL


@pytest.mark.spec("VAL-1")
def test_the_chart_renders_a_source_for_each_entry_and_the_secaccess() -> None:
    controls = default_values()["controls"]
    found = [doc for doc in rendered("chart") if doc["kind"] in KINDS]

    assert sorted((doc["kind"], doc["metadata"]["name"]) for doc in found) == sorted(
        [("SecAccess", "cluster")] + [("Source", name) for name in controls["sources"]]
    )
    for doc in found:
        assert doc["apiVersion"] == "edgar.nathanslaughter.com/v1alpha1"
        assert "status" not in doc
        if doc["kind"] == "SecAccess":
            assert "namespace" not in doc["metadata"]
            assert doc["spec"] == controls["secAccess"]
        else:
            assert doc["metadata"]["namespace"] == NAMESPACE
            assert doc["spec"] == controls["sources"][doc["metadata"]["name"]]


@pytest.mark.spec("VAL-1")
def test_the_chart_renders_a_backfill_for_each_entry_named_by_its_key() -> None:
    backfills = {"apple-2025": backfill(), "walmart-2024": backfill(cik=104169)}
    secaccess = {"paused": False, "acknowledgedStopAt": "2026-10-06T18:04:12.345Z"}
    found = controls_rendered(with_controls(backfills=backfills, secAccess=secaccess))
    by_kind = {(doc["kind"], doc["metadata"]["name"]): doc for doc in found}

    assert {name for kind, name in by_kind if kind == "Backfill"} == set(backfills)
    for name, spec in backfills.items():
        assert by_kind[("Backfill", name)]["spec"] == spec
        assert by_kind[("Backfill", name)]["metadata"]["namespace"] == NAMESPACE
    assert by_kind[("SecAccess", "cluster")]["spec"] == secaccess


@pytest.mark.spec("VAL-1")
def test_no_other_chart_renders_a_control() -> None:
    for chart, _, _ in RELEASES[:-1]:
        assert [doc for doc in rendered(chart) if doc["kind"] in KINDS] == [], chart


# Values each of whose resources the API server refuses (API-3), with what
# the umbrella chart's values would otherwise be.
REFUSED = {
    "a Source without a feed": {"sources": {"s": source(feed=None)}},
    "a Source without a url": {"sources": {"s": source(url=None)}},
    "a Source without a cadence": {"sources": {"s": source(cadence=None)}},
    "a Source whose feed isn't a name": {"sources": {"s": source(feed="Submissions")}},
    "a Source whose url isn't the SEC's": {"sources": {"s": source(url="https://example.com/")}},
    "a Source whose url isn't https": {"sources": {"s": source(url="http://data.sec.gov/x")}},
    "a Source whose cadence is 0": {"sources": {"s": source(0)}},
    "a Source whose cadence is negative": {"sources": {"s": source(-10)}},
    "a Source whose cadence isn't whole": {"sources": {"s": source(10.5)}},
    "a Source whose cadence is a string": {"sources": {"s": source("10")}},
    "a Source whose cadence is beyond 32 bits": {"sources": {"s": source(2147483648)}},
    "a Source whose paused isn't a boolean": {"sources": {"s": source(paused="yes")}},
    "a Source with a field its definition lacks": {"sources": {"s": source(every=10)}},
    "a Source whose name has a capital": {"sources": {"Apple": source()}},
    "a Source whose name has an underscore": {"sources": {"apple_submissions": source()}},
    "a Source whose name starts with a hyphen": {"sources": {"-apple": source()}},
    "a Source whose name is too long": {"sources": {"a" * 254: source()}},
    "a Backfill without a cik": {"backfills": {"b": backfill(cik=None)}},
    "a Backfill without its first date": {"backfills": {"b": backfill(filingDateFrom=None)}},
    "a Backfill without its last date": {"backfills": {"b": backfill(filingDateTo=None)}},
    "a Backfill whose cik is 0": {"backfills": {"b": backfill(cik=0)}},
    "a Backfill whose cik has 11 digits": {"backfills": {"b": backfill(cik=10_000_000_000)}},
    "a Backfill whose cik is a string": {"backfills": {"b": backfill(cik="320193")}},
    "a Backfill whose date isn't in its form": {
        "backfills": {"b": backfill(filingDateFrom="2025-1-01")}
    },
    "a Backfill whose date has no such month": {
        "backfills": {"b": backfill(filingDateFrom="2025-13-01")}
    },
    "a Backfill whose date has no such day": {
        "backfills": {"b": backfill(filingDateTo="2025-04-31")}
    },
    "a Backfill whose February 29th isn't in a leap year": {
        "backfills": {"b": backfill(filingDateFrom="2100-02-29", filingDateTo="2100-03-01")}
    },
    "a Backfill whose range ends before it starts": {
        "backfills": {"b": backfill(filingDateFrom="2025-06-02", filingDateTo="2025-06-01")}
    },
    "a Backfill with a field its definition lacks": {"backfills": {"b": backfill(form="10-K")}},
    "a Backfill whose name has a capital": {"backfills": {"Apple": backfill()}},
    "a SecAccess whose paused isn't a boolean": {"secAccess": {"paused": "no"}},
    "a SecAccess whose acknowledgement has no milliseconds": {
        "secAccess": {"acknowledgedStopAt": "2026-10-06T18:04:12Z"}
    },
    "a SecAccess whose acknowledgement isn't in UTC": {
        "secAccess": {"acknowledgedStopAt": "2026-10-06T18:04:12.345+00:00"}
    },
    "a SecAccess whose acknowledgement has no such day": {
        "secAccess": {"acknowledgedStopAt": "2026-02-29T18:04:12.345Z"}
    },
    "a SecAccess whose acknowledgement has no such hour": {
        "secAccess": {"acknowledgedStopAt": "2026-10-06T24:04:12.345Z"}
    },
    "a SecAccess with a field its definition lacks": {"secAccess": {"stop": {"at": "x"}}},
}


@pytest.mark.spec("VAL-2")
@pytest.mark.parametrize("controls", REFUSED.values(), ids=REFUSED.keys())
def test_the_chart_refuses_values_whose_resources_the_api_server_refuses(
    controls: dict[str, Any],
) -> None:
    assert refused(with_controls(**controls))


@pytest.mark.spec("VAL-2")
@pytest.mark.parametrize(
    "controls",
    [
        {"sources": {"s": source(1)}},
        {"sources": {"s": source(2147483647, paused=True)}},
        {"sources": {"apple.submissions-2": source(url="https://www.sec.gov/x")}},
        {"sources": {"a" * 253: source()}},
        {"backfills": {"b": backfill(filingDateFrom="2024-02-29", filingDateTo="2024-02-29")}},
        {"backfills": {"b": backfill(cik=9_999_999_999, filingDateFrom="2000-02-29")}},
        {"secAccess": {}},
        {"secAccess": {"paused": False, "acknowledgedStopAt": "2028-02-29T23:59:59.999Z"}},
    ],
)
def test_the_chart_renders_values_whose_resources_the_api_server_accepts(
    controls: dict[str, Any],
) -> None:
    assert controls_rendered(with_controls(**controls))


@pytest.mark.spec("VAL-2")
def test_the_values_schema_requires_each_sources_cadence_an_integer_of_at_least_1() -> None:
    schema = json.loads((DEPLOY / "chart" / "values.schema.json").read_text())
    sources = schema["properties"]["controls"]["properties"]["sources"]["additionalProperties"]

    assert "cadenceSeconds" in sources["required"]
    assert sources["properties"]["cadenceSeconds"]["type"] == "integer"
    assert sources["properties"]["cadenceSeconds"]["minimum"] == 1


def sources_at(*cadences: int, paused: tuple[int, ...] = ()) -> dict[str, Any]:
    return {
        f"source-{i}": source(cadence, **({"paused": True} if cadence in paused else {}))
        for i, cadence in enumerate(cadences)
    }


@pytest.mark.spec("VAL-3")
@pytest.mark.parametrize(
    ("cadences", "within"),
    [
        ([10] * 25, True),
        ([10] * 26, False),
        ([1, 1, 2], True),
        ([1, 1, 1], False),
        ([3] * 7 + [6], True),
        ([3] * 7 + [5], False),
        # Sylvester's sequence: 1/3 + 1/7 + 1/43 + 1/1807 = 1/2 - 1/3263442.
        ([1, 1, 3, 7, 43, 1807, 3263443], True),
        ([1, 1, 3, 7, 43, 1807, 3263442], True),
        ([1, 1, 3, 7, 43, 1807, 3263441], False),
    ],
)
def test_the_chart_refuses_sources_that_together_would_poll_faster_than_the_budget(
    cadences: list[int], within: bool
) -> None:
    assert refused(with_controls(sources=sources_at(*cadences))) is not within


@pytest.mark.spec("VAL-3")
def test_paused_sources_dont_count_toward_the_budget() -> None:
    assert not refused(with_controls(sources=sources_at(1, 1, 2, 7, paused=(7,))))
    assert refused(with_controls(sources=sources_at(1, 1, 2, 7)))


# Primes below 2^31, so the exact sum of their reciprocals has a denominator
# too large to hold, and the chart compares it in floating point. The chart
# sums the Sources in the order of their names, so these come first.
PRIMES = [2147483647, 2147483629, 2147483587, 2147483579, 2147483563, 2147483549]


@pytest.mark.spec("VAL-3")
def test_the_budget_holds_for_cadences_whose_exact_sum_is_too_large_to_hold() -> None:
    assert not refused(with_controls(sources=sources_at(*PRIMES, 1, 1)))
    assert refused(with_controls(sources=sources_at(*PRIMES, 1, 1, 1)))


@pytest.mark.spec("VAL-4")
def test_the_default_values_pause_the_secaccess() -> None:
    [secaccess] = [doc for doc in rendered("chart") if doc["kind"] == "SecAccess"]

    assert default_values()["controls"]["secAccess"]["paused"] is True
    assert secaccess["spec"]["paused"] is True


# RBAC


GROUP_OF_CONTROLS = "edgar.nathanslaughter.com"
RESOURCES = ["sources", "backfills", "secaccesses"]
SUBRESOURCES = [*RESOURCES, *(f"{r}/status" for r in RESOURCES)]
# Section 9's table: each ServiceAccount's verbs on each resource and its
# status, of those this version's charts render.
TABLE = {
    ("edgar", "scheduler"): {
        "sources": {"get", "list", "watch"},
        "backfills": {"get", "list", "watch"},
        "sources/status": {"update", "patch"},
        "backfills/status": {"update", "patch"},
        "secaccesses": {"get", "list", "watch"},
    },
}


def all_rendered() -> list[tuple[str, dict[str, Any]]]:
    return [(release, doc) for chart, release, _ in RELEASES for doc in rendered(chart)]


def grants(rule: dict[str, Any]) -> dict[str, set[str]]:
    """The verbs a rule grants on each of the controls and their status."""
    groups = rule.get("apiGroups", [])
    if GROUP_OF_CONTROLS not in groups and "*" not in groups:
        return {}
    found: dict[str, set[str]] = {}
    for resource in SUBRESOURCES:
        status = resource.endswith("/status")
        if any(
            given in (resource, "*") or (status and given == "*/status")
            for given in rule.get("resources", [])
        ):
            found[resource] = set(rule.get("verbs", []))
    return found


def roles() -> dict[tuple[str, str, str], dict[str, Any]]:
    """Each Role and ClusterRole the charts render, by kind, namespace, and name."""
    found = {}
    for chart, _, namespace in RELEASES:
        for doc in rendered(chart):
            if doc["kind"] in ("Role", "ClusterRole"):
                where = doc["metadata"].get("namespace", namespace) if doc["kind"] == "Role" else ""
                found[(doc["kind"], where, doc["metadata"]["name"])] = doc
    return found


def bound(account: tuple[str, str]) -> list[tuple[str, str, str]]:
    """The roles the charts' bindings bind a ServiceAccount to."""
    found = []
    for chart, _, namespace in RELEASES:
        for doc in rendered(chart):
            if doc["kind"] not in ("RoleBinding", "ClusterRoleBinding"):
                continue
            here = doc["metadata"].get("namespace", namespace)
            subjects = {
                (s.get("namespace", here), s["name"])
                for s in doc.get("subjects", [])
                if s["kind"] == "ServiceAccount"
            }
            if account in subjects:
                ref = doc["roleRef"]
                where = here if ref["kind"] == "Role" else ""
                found.append((ref["kind"], where, ref["name"]))
    return found


def granted(account: tuple[str, str]) -> dict[str, set[str]]:
    """What the charts' roles grant a ServiceAccount on the controls."""
    found: dict[str, set[str]] = {}
    all_roles = roles()
    for key in bound(account):
        for rule in all_roles[key].get("rules", []):
            for resource, verbs in grants(rule).items():
                found.setdefault(resource, set()).update(verbs)
    return found


@pytest.mark.spec("RBAC-1")
def test_the_scheduler_may_read_the_controls_and_change_only_their_status() -> None:
    assert granted(("edgar", "scheduler")) == TABLE[("edgar", "scheduler")]


@pytest.mark.spec("RBAC-4")
def test_no_role_grants_anything_on_the_controls_but_the_tables_grants() -> None:
    bindings: dict[tuple[str, str, str], set[tuple[str, str]]] = {}
    for chart, _, namespace in RELEASES:
        for doc in rendered(chart):
            if doc["kind"] in ("RoleBinding", "ClusterRoleBinding"):
                here = doc["metadata"].get("namespace", namespace)
                ref = doc["roleRef"]
                key = (ref["kind"], here if ref["kind"] == "Role" else "", ref["name"])
                bindings.setdefault(key, set()).update(
                    (s.get("namespace", here), s["name"])
                    for s in doc.get("subjects", [])
                    if s["kind"] == "ServiceAccount"
                )
    for key, role in roles().items():
        for rule in role.get("rules", []):
            for resource, verbs in grants(rule).items():
                accounts = bindings.get(key, set())
                assert accounts, (key, rule)
                for account in accounts:
                    assert verbs <= TABLE.get(account, {}).get(resource, set()), (
                        key,
                        account,
                        rule,
                    )
                if "/" not in resource:
                    assert not verbs & {
                        "create",
                        "update",
                        "patch",
                        "delete",
                        "deletecollection",
                        "*",
                    }


@pytest.mark.spec("RBAC-4")
def test_a_wildcard_on_the_controls_counts_as_a_grant() -> None:
    assert grants({"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}) == {
        resource: {"*"} for resource in SUBRESOURCES
    }
    assert set(
        grants({"apiGroups": [GROUP_OF_CONTROLS], "resources": ["*/status"], "verbs": ["patch"]})
    ) == {f"{resource}/status" for resource in RESOURCES}
    assert grants({"apiGroups": ["apps"], "resources": ["*"], "verbs": ["*"]}) == {}
