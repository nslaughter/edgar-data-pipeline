"""The definitions in spec/schemas/ against spec/controls.md's table of resources.

Each test carries the IDs of the statements it checks, as
@pytest.mark.spec(...); tests/test_spec_coverage.py requires every statement
checked by `tests` or `kind` to have one, or to await a later task.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
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
