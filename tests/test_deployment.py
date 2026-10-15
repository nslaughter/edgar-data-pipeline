"""spec/deployment.md's invariants: the charts and the kind configuration as
committed, and what the charts render.

Each chart is rendered with `helm template`, with the values CI installs it
with, apart from the credentials and the database archive's server name, for
which these tests give values of their own (spec/deployment.md, section 3).
They need Helm, and jq for the JetStream Job's script; they reach neither a
cluster nor the network. The hook Jobs' scripts run against fakes: of the
`nats` and `psql` commands, and of Garage's admin API on a local server.
Each test carries the IDs of the statements it checks, as
@pytest.mark.spec(...).
"""

from __future__ import annotations

import base64
import copy
import functools
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
SCHEMAS = ROOT / "spec" / "schemas"

# The four releases, in the order they're installed (section 5): each chart's
# directory under deploy/, its release, and its namespace.
RELEASES = [
    ("prometheus-crds", "prometheus-crds", "edgar"),
    ("cert-manager", "cert-manager", "cert-manager"),
    ("operators", "operators", "cnpg-system"),
    ("chart", "edgar", "edgar"),
]
CHARTS = [chart for chart, _, _ in RELEASES]
UMBRELLA = "chart"

# Each chart's dependencies, as section 5's second table gives them: name and
# repository, with None for Garage's chart, which is committed (PIN-3).
DEPENDENCIES = {
    "prometheus-crds": {
        "prometheus-operator-crds": "oci://ghcr.io/prometheus-community/charts",
    },
    "cert-manager": {"cert-manager": "oci://quay.io/jetstack/charts"},
    "operators": {
        "cloudnative-pg": "https://cloudnative-pg.github.io/charts",
        "plugin-barman-cloud": "https://cloudnative-pg.github.io/charts",
    },
    "chart": {
        "nats": "https://nats-io.github.io/k8s/helm/charts/",
        "garage": None,
        "kube-prometheus-stack": "oci://ghcr.io/prometheus-community/charts",
    },
}

# The credentials and the archive's server name the tests render with, each
# a string found nowhere else, so where it lands can be seen (SECRET-3).
CREDENTIALS: dict[str, Any] = {
    "garage": {
        "rpcSecret": "5ec0" * 16,
        "adminToken": "test-garage-admin-token-a7f3c2e1b9d84",
    },
    "garageKeys": {
        "database-backups": {"id": "GK" + "ba0c" * 6, "secret": "ba05" * 16},
        "fetcher": {"id": "GK" + "fe7c" * 6, "secret": "fe75" * 16},
        "discoverer": {"id": "GK" + "d15c" * 6, "secret": "d155" * 16},
    },
    "database": {
        "fetcher": "test-fetcher-password-3b7e1d",
        "discoverer": "test-discoverer-password-8c2f4a",
        "grafana": "test-grafana-password-5d9a6e",
    },
    "grafana": {"adminPassword": "test-grafana-admin-password-1e4b7c"},
}
SERVER_NAME = "test-server-name"
TEST_VALUES: dict[str, Any] = {
    "credentials": CREDENTIALS,
    "database": {"backups": {"serverName": SERVER_NAME}},
}

# A third-party image, named by its tag and its digest (PIN-4).
PINNED_IMAGE = re.compile(r"^[^\s@]+:[^\s@/:]+@sha256:[0-9a-f]{64}$")
SEMVER = re.compile(r"^v?\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")
# The API groups Kubernetes itself serves, whose kinds need no definition.
BUILT_IN_GROUPS = {
    "",
    "admissionregistration.k8s.io",
    "apiextensions.k8s.io",
    "apps",
    "autoscaling",
    "batch",
    "coordination.k8s.io",
    "discovery.k8s.io",
    "networking.k8s.io",
    "policy",
    "rbac.authorization.k8s.io",
    "scheduling.k8s.io",
    "storage.k8s.io",
}


@pytest.fixture(scope="session", autouse=True)
def helm() -> None:
    if shutil.which("helm") is None:
        pytest.fail("The deployment's tests need Helm, at major version 4 (PIN-7)")


def kube_version() -> str:
    """The Kubernetes version of kind's nodes, which the charts render for."""
    [node, *_] = kind_config()["nodes"]
    match = re.match(r"kindest/node:(v[\d.]+)@", node["image"])
    assert match, node["image"]
    return match.group(1)


def render(
    chart: str, values: dict[str, Any] | None = None, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """`helm template` of a chart, as its release, with the tests' values."""
    [(release, namespace)] = [(r, n) for c, r, n in RELEASES if c == chart]
    command = ["helm", "template", release, str(DEPLOY / chart), "--namespace", namespace]
    command += ["--kube-version", kube_version(), "--values", "-", *args]
    given = TEST_VALUES if values is None and chart == UMBRELLA else values or {}
    return subprocess.run(
        command, input=json.dumps(given), capture_output=True, text=True, check=check
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
    """What a chart renders with the tests' values, its hooks among them."""
    return tuple(documents(render(chart).stdout))


def rendered_all() -> list[tuple[str, dict[str, Any]]]:
    return [(chart, doc) for chart in CHARTS for doc in rendered(chart)]


def of_kind(chart: str, kind: str) -> list[dict[str, Any]]:
    return [doc for doc in rendered(chart) if doc["kind"] == kind]


def named(chart: str, kind: str, name: str) -> dict[str, Any]:
    [doc] = [doc for doc in of_kind(chart, kind) if doc["metadata"]["name"] == name]
    return doc


def group(doc: dict[str, Any]) -> str:
    api_version = str(doc["apiVersion"])
    return api_version.rsplit("/", 1)[0] if "/" in api_version else ""


def chart_yaml(chart: str) -> dict[str, Any]:
    loaded = yaml.safe_load((DEPLOY / chart / "Chart.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def chart_lock(chart: str) -> dict[str, Any]:
    loaded = yaml.safe_load((DEPLOY / chart / "Chart.lock").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def values(chart: str) -> dict[str, Any]:
    loaded = yaml.safe_load((DEPLOY / chart / "values.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def kind_config() -> dict[str, Any]:
    loaded = yaml.safe_load((DEPLOY / "kind.yaml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def images(node: Any) -> Iterator[str]:
    """The value of every field named `image` in a manifest, wherever it is."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "image" and isinstance(value, str):
                yield value
            else:
                yield from images(value)
    elif isinstance(node, list):
        for item in node:
            yield from images(item)


def pod_spec(doc: dict[str, Any]) -> dict[str, Any]:
    spec = doc["spec"]["template"]["spec"]
    assert isinstance(spec, dict)
    return spec


def containers(doc: dict[str, Any]) -> list[dict[str, Any]]:
    spec = pod_spec(doc)
    return [*spec.get("initContainers", []), *spec["containers"]]


def definitions(chart: str) -> list[dict[str, Any]]:
    return of_kind(chart, "CustomResourceDefinition")


def packaged_charts(chart: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Each Chart.yaml in a chart's committed dependencies, nested ones too."""
    for path in sorted((DEPLOY / chart / "charts").iterdir()):
        if path.suffix == ".tgz":
            with tarfile.open(path) as archive:
                for member in archive.getmembers():
                    if member.name.endswith("/Chart.yaml"):
                        file = archive.extractfile(member)
                        assert file is not None
                        yield f"{path.name}:{member.name}", yaml.safe_load(file.read())
        else:
            for found in sorted(path.rglob("Chart.yaml")):
                yield str(found.relative_to(DEPLOY)), yaml.safe_load(found.read_text())


def config_map_data(chart: str, name: str) -> dict[str, str]:
    data = named(chart, "ConfigMap", name)["data"]
    assert isinstance(data, dict)
    return data


def hook(doc: dict[str, Any]) -> set[str]:
    return set(doc["metadata"].get("annotations", {}).get("helm.sh/hook", "").split(","))


# CHART


@pytest.mark.spec("CHART-1")
@pytest.mark.parametrize("chart", CHARTS)
def test_each_chart_is_at_its_path_with_its_files(chart: str) -> None:
    for name in ("Chart.yaml", "Chart.lock", "values.yaml"):
        assert (DEPLOY / chart / name).is_file(), name


@pytest.mark.spec("CHART-1")
def test_the_kind_configuration_is_at_its_path() -> None:
    assert (DEPLOY / "kind.yaml").is_file()


@pytest.mark.spec("CHART-2")
@pytest.mark.parametrize("chart", CHARTS)
def test_each_charts_dependencies_are_the_tables(chart: str) -> None:
    given = {d["name"]: d.get("repository") or None for d in chart_yaml(chart)["dependencies"]}

    assert given == DEPENDENCIES[chart]
    assert all("alias" not in d for d in chart_yaml(chart)["dependencies"])


@pytest.mark.spec("CHART-3")
@pytest.mark.parametrize("chart", CHARTS)
def test_no_dependency_comes_from_bitnami(chart: str) -> None:
    repositories = [str(d.get("repository", "")) for d in chart_yaml(chart)["dependencies"]]
    for _, nested in packaged_charts(chart):
        repositories += [str(d.get("repository", "")) for d in nested.get("dependencies", [])]

    assert [r for r in repositories if "bitnami" in r.lower()] == []


@pytest.mark.spec("CHART-3")
def test_no_image_comes_from_bitnami() -> None:
    found = [image for _, doc in rendered_all() for image in images(doc)]

    assert found
    assert [image for image in found if "bitnami" in image.lower()] == []


# PIN


@pytest.mark.spec("PIN-1")
@pytest.mark.parametrize("chart", CHARTS)
def test_each_dependency_names_one_exact_version(chart: str) -> None:
    for dependency in chart_yaml(chart)["dependencies"]:
        assert SEMVER.match(str(dependency["version"])), dependency


@pytest.mark.spec("PIN-1")
@pytest.mark.parametrize("chart", CHARTS)
def test_each_lock_lists_the_same_dependencies(chart: str) -> None:
    def listed(dependencies: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
        return {(d["name"], str(d["version"]), d.get("repository") or "") for d in dependencies}

    assert listed(chart_lock(chart)["dependencies"]) == listed(chart_yaml(chart)["dependencies"])


@pytest.mark.spec("PIN-2")
@pytest.mark.parametrize("chart", CHARTS)
def test_each_dependency_from_a_repository_is_committed_packaged(chart: str) -> None:
    for dependency in chart_yaml(chart)["dependencies"]:
        if not dependency.get("repository"):
            continue
        name, version = dependency["name"], dependency["version"]
        path = DEPLOY / chart / "charts" / f"{name}-{version}.tgz"
        with tarfile.open(path) as archive:
            file = archive.extractfile(f"{name}/Chart.yaml")
            assert file is not None
            packaged = yaml.safe_load(file.read())
        assert (packaged["name"], str(packaged["version"])) == (name, str(version))


@pytest.mark.spec("PIN-2")
@pytest.mark.parametrize("chart", CHARTS)
def test_each_chart_holds_only_its_dependencies(chart: str) -> None:
    expected = {
        f"{d['name']}-{d['version']}.tgz" if d.get("repository") else d["name"]
        for d in chart_yaml(chart)["dependencies"]
    }

    assert {path.name for path in (DEPLOY / chart / "charts").iterdir()} == expected


@pytest.mark.spec("PIN-3")
def test_garages_chart_is_committed_as_a_directory_at_its_version() -> None:
    [dependency] = [d for d in chart_yaml(UMBRELLA)["dependencies"] if d["name"] == "garage"]
    committed = yaml.safe_load((DEPLOY / UMBRELLA / "charts" / "garage" / "Chart.yaml").read_text())

    assert "repository" not in dependency
    assert committed["name"] == "garage"
    assert str(committed["version"]) == str(dependency["version"])
    # Garage's release tag, which its chart's appVersion names, is the one
    # whose image the chart runs and whose definition is committed (CRD-5).
    assert re.fullmatch(r"v\d+\.\d+\.\d+", str(committed["appVersion"]))


@pytest.mark.spec("PIN-4")
@pytest.mark.parametrize("chart", CHARTS)
def test_each_image_is_named_by_tag_and_digest(chart: str) -> None:
    found = [image for doc in rendered(chart) for image in images(doc)]

    assert [image for image in found if not PINNED_IMAGE.match(image)] == []


@pytest.mark.spec("PIN-4")
def test_the_images_operators_name_in_arguments_and_configuration_are_pinned() -> None:
    # Images an operator runs without a field named `image`: the Prometheus
    # operator's config reloader, and the Barman Cloud plugin's sidecar
    # (PIN-5 checks them on the cluster).
    [operator] = [d for d in of_kind(UMBRELLA, "Deployment") if "operator" in d["metadata"]["name"]]
    args = [a for c in containers(operator) for a in c.get("args", [])]
    [reloader] = [a.split("=", 1)[1] for a in args if a.startswith("--prometheus-config-reloader=")]
    sidecar = config_map_data("operators", "plugin-barman-cloud-config")["SIDECAR_IMAGE"]
    env = {
        e["name"]: e.get("value")
        for d in of_kind("operators", "Deployment")
        for c in containers(d)
        for e in c.get("env", [])
    }

    for image in (reloader, sidecar, env["OPERATOR_IMAGE_NAME"]):
        assert PINNED_IMAGE.match(image), image


@pytest.mark.spec("PIN-6")
def test_each_kind_node_image_is_named_by_digest() -> None:
    for node in kind_config()["nodes"]:
        assert re.fullmatch(r"kindest/node:v[\d.]+@sha256:[0-9a-f]{64}", node["image"]), node


@pytest.mark.spec("PIN-7")
def test_ci_installs_helm_4_and_kind_at_exact_versions() -> None:
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    helms = [s["with"]["version"] for s in steps if "azure/setup-helm" in s.get("uses", "")]
    kinds = [s["with"]["version"] for s in steps if "helm/kind-action" in s.get("uses", "")]

    assert helms
    assert kinds
    for version in helms:
        assert re.fullmatch(r"v4\.\d+\.\d+", str(version)), version
    for version in kinds:
        assert re.fullmatch(r"v\d+\.\d+\.\d+", str(version)), version
    for step in steps:
        if "helm/kind-action" in step.get("uses", ""):
            # The action would install a Helm of its own otherwise.
            assert step["with"].get("install_only") is True


# PG


@pytest.mark.spec("PG-1")
def test_one_image_catalog_names_postgresql_18_by_digest() -> None:
    catalogs = [(chart, doc) for chart, doc in rendered_all() if doc["kind"] == "ImageCatalog"]

    assert [chart for chart, _ in catalogs] == [UMBRELLA]
    [(_, catalog)] = catalogs
    [entry] = catalog["spec"]["images"]
    assert entry["major"] == 18
    assert re.fullmatch(
        r"ghcr\.io/cloudnative-pg/postgresql:18\.[^@]*@sha256:[0-9a-f]{64}", entry["image"]
    )


@pytest.mark.spec("PG-1")
def test_every_cluster_names_its_image_through_the_catalog() -> None:
    [catalog] = of_kind(UMBRELLA, "ImageCatalog")
    clusters = [doc for _, doc in rendered_all() if doc["kind"] == "Cluster"]

    assert clusters
    for cluster in clusters:
        assert cluster["spec"]["imageCatalogRef"] == {
            "apiGroup": "postgresql.cnpg.io",
            "kind": "ImageCatalog",
            "name": catalog["metadata"]["name"],
            "major": 18,
        }
        assert "imageName" not in cluster["spec"]


@pytest.mark.spec("PG-3")
def test_one_cluster_of_three_instances_on_kinds_standard_class() -> None:
    clusters = [(chart, doc) for chart, doc in rendered_all() if doc["kind"] == "Cluster"]

    assert [chart for chart, _ in clusters] == [UMBRELLA]
    [(_, cluster)] = clusters
    assert cluster["spec"]["instances"] == 3
    assert cluster["spec"]["storage"]["storageClass"] == "standard"


def the_cluster() -> dict[str, Any]:
    [cluster] = of_kind(UMBRELLA, "Cluster")
    return cluster


@pytest.mark.spec("PG-4")
def test_the_cluster_archives_through_the_plugin_to_garages_bucket() -> None:
    [plugin] = the_cluster()["spec"]["plugins"]
    [store] = of_kind(UMBRELLA, "ObjectStore")
    configuration = store["spec"]["configuration"]
    credentials = configuration["s3Credentials"]

    assert plugin["name"] == "barman-cloud.cloudnative-pg.io"
    assert plugin["isWALArchiver"] is True
    assert plugin["parameters"]["barmanObjectName"] == store["metadata"]["name"]
    assert configuration["destinationPath"] == "s3://database-backups/"
    assert urlparse(configuration["endpointURL"]).hostname == "garage"
    assert {credentials[k]["name"] for k in ("accessKeyId", "secretAccessKey")} == {
        "garage-key-database-backups"
    }
    assert "database-backups" in values(UMBRELLA)["garageSetup"]["buckets"]


@pytest.mark.spec("PG-4", "PG-10")
def test_the_archive_is_kept_under_the_required_server_name() -> None:
    [plugin] = the_cluster()["spec"]["plugins"]
    without = copy.deepcopy(TEST_VALUES)
    del without["database"]["backups"]["serverName"]

    assert plugin["parameters"]["serverName"] == SERVER_NAME
    assert "serverName" not in values(UMBRELLA)["database"]["backups"]
    assert render(UMBRELLA, without, check=False).returncode != 0


@pytest.mark.spec("PG-4")
def test_a_scheduled_backup_through_the_plugin_each_day() -> None:
    [scheduled] = of_kind(UMBRELLA, "ScheduledBackup")
    spec = scheduled["spec"]
    seconds, minutes, hours, day, month, weekday = spec["schedule"].split()

    assert spec["method"] == "plugin"
    assert spec["pluginConfiguration"]["name"] == "barman-cloud.cloudnative-pg.io"
    assert spec["cluster"]["name"] == the_cluster()["metadata"]["name"]
    assert (day, month, weekday) == ("*", "*", "*")
    assert all(field.isdigit() for field in (seconds, minutes, hours))


@pytest.mark.spec("PG-6")
def test_one_database_edgar_owned_by_edgar_without_superuser_access() -> None:
    spec = the_cluster()["spec"]

    assert spec["bootstrap"] == {"initdb": {"database": "edgar", "owner": "edgar"}}
    assert spec["enableSuperuserAccess"] is False


@pytest.mark.spec("PG-6")
def test_only_the_database_job_connects_as_the_owner() -> None:
    owners_secret = f"{the_cluster()['metadata']['name']}-app"
    users = [
        doc["metadata"]["name"]
        for doc in rendered(UMBRELLA)
        if doc["kind"] in {"Deployment", "StatefulSet", "DaemonSet", "Job"}
        and owners_secret in json.dumps(pod_spec(doc))
    ]

    assert users == ["database-setup"]


def roles() -> dict[str, dict[str, Any]]:
    return {role["name"]: role for role in the_cluster()["spec"]["managed"]["roles"]}


def role_secret(role: str) -> dict[str, Any]:
    return named(UMBRELLA, "Secret", roles()[role]["passwordSecret"]["name"])


@pytest.mark.spec("PG-7", "PG-11")
@pytest.mark.parametrize("role", ["fetcher", "discoverer", "grafana"])
def test_each_role_logs_in_without_special_attributes(role: str) -> None:
    given = roles()[role]

    assert given["login"] is True
    for attribute in ("superuser", "createdb", "createrole", "replication", "bypassrls"):
        assert given[attribute] is False, attribute


@pytest.mark.spec("PG-7", "PG-11", "SECRET-1")
@pytest.mark.parametrize("role", ["fetcher", "discoverer", "grafana"])
def test_each_roles_password_is_a_required_value_in_a_secret(role: str) -> None:
    secret = role_secret(role)
    without = copy.deepcopy(TEST_VALUES)
    del without["credentials"]["database"][role]

    assert secret["type"] == "kubernetes.io/basic-auth"
    assert secret["stringData"] == {"username": role, "password": CREDENTIALS["database"][role]}
    assert render(UMBRELLA, without, check=False).returncode != 0


@pytest.mark.spec("PG-7")
def test_the_roles_are_each_services_and_grafanas() -> None:
    services = values(UMBRELLA)["database"]["services"]

    assert services == ["fetcher", "discoverer"]
    assert sorted(roles()) == sorted([*services, "grafana"])


# The SQL the database Job applies, and its grants.

GRANT = re.compile(
    r"^GRANT (?P<privileges>[A-Z, ]+) ON (?P<kind>TABLE|SCHEMA) (?P<objects>[a-z_., ]+)"
    r" TO (?P<roles>[a-z_, ]+);$",
    re.M,
)


def setup_sql() -> str:
    return config_map_data(UMBRELLA, "database-setup")["setup.sql"]


def grants() -> dict[str, dict[str, set[str]]]:
    """Each object's grants in the Job's SQL: the privileges of each role on it."""
    found: dict[str, dict[str, set[str]]] = {}
    for match in GRANT.finditer(setup_sql()):
        privileges = {p.strip() for p in match["privileges"].split(",")}
        for name in match["objects"].split(","):
            for role in match["roles"].split(","):
                found.setdefault(name.strip(), {}).setdefault(role.strip(), set()).update(
                    privileges
                )
    return found


def created(sql: str, what: str) -> list[str]:
    return re.findall(rf"(?m)^CREATE {what} ([a-z_.]+)", sql)


def spec_sql(name: str) -> str:
    return (SCHEMAS / name).read_text(encoding="utf-8")


def applied_files() -> list[tuple[str, str]]:
    """Each file the Job applies, with the condition it's applied under."""
    block = re.compile(
        r"^SELECT (?P<condition>.+?) AS absent \\gset\n\\if :absent\nBEGIN;\n\\ir (?P<file>\S+)\n"
        r"COMMIT;\n\\endif$",
        re.M | re.S,
    )
    return [(m["file"], m["condition"]) for m in block.finditer(setup_sql())]


def database_job() -> dict[str, Any]:
    return named(UMBRELLA, "Job", "database-setup")


@pytest.mark.spec("PG-8", "PG-12", "PG-13")
@pytest.mark.parametrize("name", ["records.sql", "notices.sql", "dashboards.sql"])
def test_the_charts_sql_is_a_copy_of_the_specs(name: str) -> None:
    copied = (DEPLOY / UMBRELLA / "files" / name).read_text(encoding="utf-8")

    assert copied == spec_sql(name)
    assert config_map_data(UMBRELLA, "database-setup")[name].strip() == copied.strip()


@pytest.mark.spec("PG-8")
def test_the_database_job_is_a_hook_after_each_install_and_upgrade() -> None:
    assert hook(database_job()) == {"post-install", "post-upgrade"}


@pytest.mark.spec("PG-8")
def test_the_database_job_connects_to_edgar_as_its_owner() -> None:
    [container] = containers(database_job())
    env = {e["name"]: e for e in container["env"]}
    owners_secret = f"{the_cluster()['metadata']['name']}-app"

    assert env["PGDATABASE"]["value"] == "edgar"
    assert env["PGHOST"]["value"] == f"{the_cluster()['metadata']['name']}-rw"
    for name, key in (("PGUSER", "username"), ("PGPASSWORD", "password")):
        assert env[name]["valueFrom"]["secretKeyRef"] == {"name": owners_secret, "key": key}


# A fake of `psql`, as the database Job runs it. It applies no file, but logs
# it. It answers a count of the roles in pg_roles from the roles that exist,
# as its state's `roles` list gives them for each count in turn, the last of
# them for the rest; or, where the list gives null, fails as psql does when
# the database refuses its connection.
FAKE_PSQL = """\
import json, re, sys
STATE = sys.argv[1]
args = sys.argv[2:]
with open(STATE) as f:
    state = json.load(f)
if "-f" in args:
    state["log"].append(["apply", args[args.index("-f") + 1]])
    status = 0
else:
    [query] = [args[i + 1] for i, a in enumerate(args[:-1]) if re.fullmatch("-[A-Za-z]*c", a)]
    state["log"].append(["count", query])
    existing = state["roles"].pop(0) if len(state["roles"]) > 1 else state["roles"][0]
    asked = re.search(
        r"FROM pg_roles WHERE rolname = ANY [(]string_to_array[(]'([^']*)', ' '[)][)]", query
    )
    if existing is None:
        print("psql: error: connection to server failed: Connection refused", file=sys.stderr)
        status = 2
    elif asked is None:
        print(f"psql: unexpected query {query}", file=sys.stderr)
        status = 1
    else:
        print(len([role for role in asked.group(1).split() if role in existing]))
        status = 0
with open(STATE, "w") as f:
    json.dump(state, f)
sys.exit(status)
"""


def run_database_job(tmp_path: Path, existing: list[list[str] | None]) -> tuple[int, list[Any]]:
    """Runs the database Job's script against a fake `psql`, without its
    waits, with the roles that exist at each count, and gives its exit status
    and the fake's log."""
    [container] = containers(database_job())
    env = {e["name"]: e["value"] for e in container["env"] if "value" in e}
    sql = tmp_path / "sql"
    sql.mkdir()
    for name, content in config_map_data(UMBRELLA, "database-setup").items():
        (sql / name).write_text(content)
    script = sql / "setup.sh"
    script.write_text(script.read_text().replace("sleep 5", "sleep 0"))
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"roles": existing, "log": []}))
    bin = tmp_path / "bin"
    bin.mkdir()
    (tmp_path / "fake_psql.py").write_text(FAKE_PSQL)
    psql = bin / "psql"
    psql.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -I -S "{tmp_path / "fake_psql.py"}" "{state}" "$@"\n'
    )
    psql.chmod(0o755)
    result = subprocess.run(
        ["sh", str(script)],
        env={
            **os.environ,
            "PATH": f"{bin}{os.pathsep}{os.environ['PATH']}",
            "SQL": str(sql),
            "ROLES": env["ROLES"],
        },
        capture_output=True,
        text=True,
    )
    log = json.loads(state.read_text())["log"]
    assert isinstance(log, list)
    return result.returncode, log


@pytest.mark.spec("PG-8", "PG-13")
def test_the_database_job_waits_for_each_role(tmp_path: Path) -> None:
    [container] = containers(database_job())
    env = {e["name"]: e.get("value") for e in container["env"]}
    every = sorted(roles())

    status, log = run_database_job(tmp_path, [None, [], every[:1], every[1:], every])

    assert sorted(str(env["ROLES"]).split()) == every
    assert status == 0
    assert [entry[0] for entry in log] == ["count"] * 5 + ["apply"]
    assert log[-1] == ["apply", str(tmp_path / "sql" / "setup.sql")]


@pytest.mark.spec("PG-8", "PG-13")
def test_the_database_job_fails_when_a_role_never_exists(tmp_path: Path) -> None:
    every = sorted(roles())

    status, log = run_database_job(tmp_path, [every[1:]])

    assert status != 0
    assert len(log) > 1
    assert {entry[0] for entry in log} == {"count"}


@pytest.mark.spec("PG-8", "PG-12", "PG-13")
def test_each_file_is_applied_in_one_transaction_unless_it_was() -> None:
    files = applied_files()
    tables = created(spec_sql("notices.sql"), "TABLE")

    assert [file for file, _ in files] == ["records.sql", "notices.sql", "dashboards.sql"]
    [records, notices, dashboards] = [condition for _, condition in files]
    assert records == "to_regclass('public.records') IS NULL"
    assert "NOT EXISTS" in notices
    assert re.findall(r"'([a-z_]+)'", notices.split("relname IN", 1)[1]) == tables
    assert dashboards == "to_regnamespace('dashboards') IS NULL"


@pytest.mark.spec("PG-8", "PG-11")
def test_only_the_fetchers_role_may_add_records() -> None:
    assert grants()["records"] == {"fetcher": {"INSERT", "SELECT"}, "discoverer": {"SELECT"}}


@pytest.mark.spec("PG-12")
def test_only_the_discoverers_role_may_add_to_the_notices_tables() -> None:
    found = grants()
    tables = created(spec_sql("notices.sql"), "TABLE")

    assert tables
    for table in tables:
        assert found[table] == {"discoverer": {"INSERT", "SELECT"}, "fetcher": {"SELECT"}}


@pytest.mark.spec("PG-12")
def test_each_service_may_read_the_notices_views() -> None:
    found = grants()
    views = created(spec_sql("notices.sql"), "VIEW")

    assert views
    for view in views:
        assert found[view] == {"discoverer": {"SELECT"}, "fetcher": {"SELECT"}}


@pytest.mark.spec("PG-12")
def test_the_job_revokes_nothing() -> None:
    assert "REVOKE" not in setup_sql().upper()


@pytest.mark.spec("PG-13", "PG-11")
def test_only_grafanas_role_may_use_the_dashboards_schema_and_read_its_views() -> None:
    found = grants()
    views = created(spec_sql("dashboards.sql"), "VIEW")

    assert views
    assert found["dashboards"] == {"grafana": {"USAGE"}}
    for view in views:
        assert found[view] == {"grafana": {"SELECT"}}
    assert set(found) == {
        "records",
        *created(spec_sql("notices.sql"), "TABLE"),
        *created(spec_sql("notices.sql"), "VIEW"),
        "dashboards",
        *views,
    }


# CRD


@pytest.mark.spec("CRD-1")
@pytest.mark.parametrize("chart", CHARTS)
def test_no_definition_comes_from_a_crds_directory(chart: str) -> None:
    assert render(chart, None, "--include-crds").stdout == render(chart).stdout


# The definitions each chart renders, by API group (CRD-2).
DEFINED_GROUPS = {
    "prometheus-crds": {"monitoring.coreos.com"},
    "cert-manager": {"cert-manager.io", "acme.cert-manager.io"},
    "operators": {
        "postgresql.cnpg.io",
        "barmancloud.cnpg.io",
        "deuxfleurs.fr",
        "edgar.nathanslaughter.com",
    },
    "chart": set(),
}


@pytest.mark.spec("CRD-2")
@pytest.mark.parametrize("chart", CHARTS)
def test_each_chart_renders_its_tables_definitions(chart: str) -> None:
    assert {d["spec"]["group"] for d in definitions(chart)} == DEFINED_GROUPS[chart]


@pytest.mark.spec("CRD-2")
def test_each_definition_is_rendered_by_one_chart() -> None:
    names = [d["metadata"]["name"] for chart in CHARTS for d in definitions(chart)]

    assert len(names) == len(set(names))


@pytest.mark.spec("CRD-3")
@pytest.mark.parametrize("position", range(len(CHARTS)))
def test_each_resource_is_of_a_kind_an_earlier_chart_defines(position: int) -> None:
    earlier = {
        (d["spec"]["group"], d["spec"]["names"]["kind"])
        for chart in CHARTS[:position]
        for d in definitions(chart)
    }
    custom = [
        (group(doc), doc["kind"])
        for doc in rendered(CHARTS[position])
        if group(doc) not in BUILT_IN_GROUPS
    ]

    assert [kind for kind in custom if kind not in earlier] == []


def admits(rule: dict[str, Any], doc: dict[str, Any]) -> bool:
    plural = doc["kind"].lower() + "s"
    return (
        any(g in ("*", group(doc)) for g in rule.get("apiGroups", []))
        and any(r == "*" or r.split("/")[0] == plural for r in rule.get("resources", []))
        and any(o in ("*", "CREATE") for o in rule.get("operations", []))
    )


@pytest.mark.spec("CRD-3")
@pytest.mark.parametrize("chart", CHARTS)
def test_no_webhook_that_fails_closed_admits_its_own_charts_resources(chart: str) -> None:
    webhooks = [
        webhook
        for doc in rendered(chart)
        if doc["kind"] in {"ValidatingWebhookConfiguration", "MutatingWebhookConfiguration"}
        for webhook in doc["webhooks"]
        if webhook.get("failurePolicy", "Fail") == "Fail"
    ]
    admitted = [
        (webhook["name"], doc["kind"])
        for webhook in webhooks
        for rule in webhook.get("rules", [])
        for doc in rendered(chart)
        if admits(rule, doc)
    ]

    assert admitted == []


@pytest.mark.spec("CRD-4")
@pytest.mark.parametrize("chart", CHARTS)
def test_every_definition_is_kept(chart: str) -> None:
    for definition in definitions(chart):
        annotations = definition["metadata"].get("annotations", {})
        assert annotations.get("helm.sh/resource-policy") == "keep", definition["metadata"]


def without_keep(definition: dict[str, Any]) -> dict[str, Any]:
    stripped = copy.deepcopy(definition)
    annotations = stripped["metadata"].pop("annotations")
    assert annotations.pop("helm.sh/resource-policy") == "keep"
    if annotations:
        stripped["metadata"]["annotations"] = annotations
    return stripped


@pytest.mark.spec("CRD-5")
def test_the_prometheus_operators_definitions_are_of_its_image() -> None:
    [dependency] = chart_yaml("prometheus-crds")["dependencies"]
    path = (
        DEPLOY / "prometheus-crds" / "charts" / f"{dependency['name']}-{dependency['version']}.tgz"
    )
    with tarfile.open(path) as archive:
        file = archive.extractfile(f"{dependency['name']}/Chart.yaml")
        assert file is not None
        app_version = yaml.safe_load(file.read())["appVersion"]
    [operator] = [
        image
        for doc in rendered(UMBRELLA)
        for image in images(doc)
        if image.startswith("quay.io/prometheus-operator/prometheus-operator:")
    ]

    assert operator.split(":", 1)[1].split("@")[0] == app_version


@pytest.mark.spec("CRD-5")
def test_garages_definition_is_its_file_with_only_the_annotation_added() -> None:
    path = DEPLOY / "operators" / "files" / "garagenodes.deuxfleurs.fr.yaml"
    [definition] = [d for d in definitions("operators") if d["spec"]["group"] == "deuxfleurs.fr"]

    assert without_keep(definition) == yaml.safe_load(path.read_text())


@pytest.mark.spec("CRD-5")
def test_garages_definition_is_of_its_charts_tag() -> None:
    garage = yaml.safe_load((DEPLOY / UMBRELLA / "charts" / "garage" / "Chart.yaml").read_text())
    image = values(UMBRELLA)["garage"]["image"]["tag"]

    assert image.split("@")[0] == garage["appVersion"]


@pytest.mark.spec("CRD-5")
@pytest.mark.parametrize("name", ["sources.yaml", "backfills.yaml", "secaccesses.yaml"])
def test_the_pipelines_definitions_are_those_in_the_spec(name: str) -> None:
    copied = DEPLOY / "operators" / "files" / name
    spec = yaml.safe_load((SCHEMAS / name).read_text())
    [definition] = [
        d for d in definitions("operators") if d["metadata"]["name"] == spec["metadata"]["name"]
    ]

    assert copied.read_text() == (SCHEMAS / name).read_text()
    assert without_keep(definition) == spec


# KIND


@pytest.mark.spec("KIND-1")
def test_kind_has_one_control_plane_and_three_workers() -> None:
    roles = [node["role"] for node in kind_config()["nodes"]]

    assert sorted(roles) == ["control-plane", "worker", "worker", "worker"]


@pytest.mark.spec("KIND-2")
def test_every_worker_keeps_its_volumes_in_one_host_directory() -> None:
    workers = [node for node in kind_config()["nodes"] if node["role"] == "worker"]
    mounts = [
        mount["hostPath"]
        for node in workers
        for mount in node.get("extraMounts", [])
        if mount["containerPath"] == "/var/local-path-provisioner"
    ]

    assert len(mounts) == len(workers)
    [directory] = set(mounts)
    assert Path(directory).is_absolute()
    assert not Path(directory).is_relative_to(ROOT)


# VOL

RETAINED = "edgar-retained"


@pytest.mark.spec("VOL-1")
def test_one_class_keeps_each_volume_at_a_path_named_by_its_claim() -> None:
    classes = [doc for _, doc in rendered_all() if doc["kind"] == "StorageClass"]

    assert [c["metadata"]["name"] for c in classes] == [RETAINED]
    [storage_class] = classes
    assert storage_class["provisioner"] == "rancher.io/local-path"
    assert storage_class["reclaimPolicy"] == "Retain"
    assert storage_class["volumeBindingMode"] == "WaitForFirstConsumer"
    pattern = storage_class["parameters"]["pathPattern"]
    assert pattern.startswith("{{ .PVC.Namespace }}/{{ .PVC.Name }}")
    assert [named_by for named_by in ("{{ .PVC.Namespace }}", "{{ .PVC.Name }}")] == [
        p for p in re.findall(r"\{\{[^}]+\}\}", pattern)
    ]


def stateful_set(name: str) -> dict[str, Any]:
    return named(UMBRELLA, "StatefulSet", name)


@pytest.mark.spec("VOL-2", "NATS-1", "GARAGE-1")
@pytest.mark.parametrize("name", ["nats", "garage"])
def test_nats_and_garage_claim_the_retained_class(name: str) -> None:
    claims = stateful_set(name)["spec"]["volumeClaimTemplates"]

    assert claims
    for claim in claims:
        assert claim["spec"]["storageClassName"] == RETAINED


@pytest.mark.spec("VOL-2", "MON-4")
@pytest.mark.parametrize("kind", ["Prometheus", "Alertmanager"])
def test_prometheus_and_alertmanager_claim_the_retained_class(kind: str) -> None:
    [resource] = of_kind(UMBRELLA, kind)
    claim = resource["spec"]["storage"]["volumeClaimTemplate"]

    assert claim["spec"]["storageClassName"] == RETAINED


@pytest.mark.spec("VOL-2")
def test_no_other_claim_is_rendered() -> None:
    assert [doc for _, doc in rendered_all() if doc["kind"] == "PersistentVolumeClaim"] == []


# PLACE


def required_spread(doc: dict[str, Any]) -> bool:
    """Whether a workload's pods each need a node of their own, from the others."""
    spec = pod_spec(doc)
    labels = doc["spec"]["template"]["metadata"]["labels"]

    def selects(selector: dict[str, Any]) -> bool:
        return all(labels.get(k) == v for k, v in selector["matchLabels"].items())

    spread = [
        c
        for c in spec.get("topologySpreadConstraints", [])
        if c["topologyKey"] == "kubernetes.io/hostname"
        and c["whenUnsatisfiable"] == "DoNotSchedule"
        and c["maxSkew"] == 1
        and selects(c["labelSelector"])
    ]
    anti = [
        term
        for term in spec.get("affinity", {})
        .get("podAntiAffinity", {})
        .get("requiredDuringSchedulingIgnoredDuringExecution", [])
        if term["topologyKey"] == "kubernetes.io/hostname" and selects(term["labelSelector"])
    ]
    return bool(spread or anti)


@pytest.mark.spec("PLACE-1")
@pytest.mark.parametrize("name", ["nats", "garage"])
def test_nats_and_garages_pods_each_need_a_node_of_their_own(name: str) -> None:
    assert required_spread(stateful_set(name))


@pytest.mark.spec("PLACE-1")
def test_the_databases_instances_each_need_a_node_of_their_own() -> None:
    assert the_cluster()["spec"]["affinity"] == {
        "podAntiAffinityType": "required",
        "topologyKey": "kubernetes.io/hostname",
    }


# NATS


def nats_config() -> str:
    return config_map_data(UMBRELLA, "nats-config")["nats.conf"]


@pytest.mark.spec("NATS-1")
def test_nats_is_one_cluster_of_three_servers_with_jetstream_in_files() -> None:
    config = nats_config()
    claims = {c["metadata"]["name"] for c in stateful_set("nats")["spec"]["volumeClaimTemplates"]}

    assert stateful_set("nats")["spec"]["replicas"] == 3
    # The configuration's cluster routes to each of the three servers.
    cluster = re.search(r'"cluster": \{(.*?)\n  \}', config, re.S)
    assert cluster
    assert len(re.findall(r'"nats://', cluster.group(1))) == 3
    assert re.search(r'"jetstream": \{[^}]*"store_dir": "/data"', config)
    [claim] = [c for c in claims if c.endswith("-js")]
    [mount] = [
        m
        for c in containers(stateful_set("nats"))
        for m in c.get("volumeMounts", [])
        if m["name"] == claim
    ]
    assert mount["mountPath"] == "/data"


def streams() -> list[dict[str, Any]]:
    given = values(UMBRELLA)["jetstream"]["streams"]
    assert isinstance(given, list)
    return given


# Section 5's tables of streams and durable consumers.
STREAMS = {
    "JOBS": (["edgar.jobs.poll", "edgar.jobs.filing", "edgar.jobs.backfill"], "workqueue"),
    "RECORDS": (
        ["edgar.records.poll", "edgar.records.filing", "edgar.records.backfill"],
        "interest",
    ),
    "ORDERS": (["edgar.orders.backfill"], "workqueue"),
    "NOTICES": (
        ["edgar.notices.filing", "edgar.notices.gap", "edgar.notices.backfill"],
        "interest",
    ),
}
CONSUMERS = {
    "discoverer-polls": ("RECORDS", ["edgar.records.poll"], 1),
    "discoverer-jobs": ("RECORDS", ["edgar.records.filing", "edgar.records.backfill"], 1),
    "discoverer-orders": ("ORDERS", ["edgar.orders.backfill"], None),
    "scheduler-notices": ("NOTICES", ["edgar.notices.gap", "edgar.notices.backfill"], None),
    "parse-workers-notices": ("NOTICES", ["edgar.notices.filing"], None),
}


def jetstream_files() -> dict[str, str]:
    return config_map_data(UMBRELLA, "jetstream-setup")


@pytest.mark.spec("NATS-3")
def test_the_jetstream_job_is_a_hook_after_each_install_and_upgrade() -> None:
    assert hook(named(UMBRELLA, "Job", "jetstream-setup")) == {"post-install", "post-upgrade"}


@pytest.mark.spec("NATS-3")
def test_the_values_list_the_tables_streams_in_files_with_three_replicas() -> None:
    files = jetstream_files()

    assert {s["name"] for s in streams()} == set(STREAMS)
    for stream in streams():
        config = json.loads(files[f"stream-{stream['name']}.json"])
        subjects, retention = STREAMS[stream["name"]]
        assert config == {
            "name": stream["name"],
            "subjects": subjects,
            "retention": retention,
            "storage": "file",
            "num_replicas": 3,
        }


@pytest.mark.spec("NATS-5")
def test_the_streams_are_made_notices_first_and_jobs_last() -> None:
    order = ["NOTICES", "RECORDS", "ORDERS", "JOBS"]

    assert [s["name"] for s in streams()] == order
    assert jetstream_files()["streams"].split() == order


@pytest.mark.spec("NATS-5")
def test_the_values_list_the_tables_durable_consumers() -> None:
    files = jetstream_files()
    listed = {
        consumer: stream["name"]
        for stream in streams()
        for consumer in files[f"consumers-{stream['name']}"].split()
    }

    assert listed == {name: stream for name, (stream, _, _) in CONSUMERS.items()}
    for name, (stream, subjects, most) in CONSUMERS.items():
        request = json.loads(files[f"consumer-{stream}-{name}.json"])
        expected = {
            "durable_name": name,
            "name": name,
            "filter_subjects": subjects,
            "ack_policy": "explicit",
            "deliver_policy": "all",
            "max_deliver": -1,
            "num_replicas": 3,
        }
        if most is not None:
            expected["max_ack_pending"] = most
        assert request == {"stream_name": stream, "config": expected}


# A fake of the `nats` command, as the JetStream Job runs it: a request to
# JetStream's API, answered from a state it keeps in a file, which also logs
# each request. Its first requests for JetStream's information fail as its
# state's `failures` list: `unavailable`, with JetStream's error; `exit`, as
# the command does when it reaches no server; or `garbled`, with a response
# that isn't JSON.
FAKE_NATS = """\
import json, sys
STATE = sys.argv[1]
args = sys.argv[2:]
assert "request" in args, args
rest = [a for a in args[args.index("request") + 1:] if a != "--raw"]
subject, body = rest[0], rest[1]
with open(STATE) as f:
    state = json.load(f)
request = json.loads(body) if body else None
state["log"].append([subject, request])
api = subject.removeprefix("$JS.API.").split(".")

def error(code, err_code, description):
    return {"error": {"code": code, "err_code": err_code, "description": description}}

def save():
    with open(STATE, "w") as f:
        json.dump(state, f)

if api == ["INFO"]:
    failure = state["failures"].pop(0) if state["failures"] else None
    if failure == "exit":
        save()
        sys.exit("nats: error: nats: no servers available for connection")
    elif failure == "garbled":
        save()
        print("nats: error: nats: timeout", end="")
        sys.exit(0)
    elif failure == "unavailable":
        response = error(503, 10008, "JetStream system temporarily unavailable")
    else:
        response = {"type": "io.nats.jetstream.api.v1.account_info_response"}
elif api[:2] == ["STREAM", "INFO"]:
    stream = state["streams"].get(api[2])
    response = {"config": stream} if stream else error(404, 10059, "stream not found")
elif api[:2] == ["STREAM", "CREATE"]:
    config = dict(request)
    config.setdefault("subjects", [api[2]])
    assert config["name"] == api[2]
    existing = state["streams"].get(api[2])
    if existing and existing != config:
        response = error(400, 10058, "stream name already in use")
    else:
        state["streams"][api[2]] = config
        response = {"config": config}
elif api[:2] == ["STREAM", "UPDATE"]:
    if api[2] not in state["streams"]:
        response = error(404, 10059, "stream not found")
    else:
        config = dict(request)
        config.setdefault("subjects", [api[2]])
        state["streams"][api[2]] = config
        response = {"config": config}
elif api[:2] == ["CONSUMER", "CREATE"]:
    stream, name = api[2], api[3]
    assert request["stream_name"] == stream and request["config"]["durable_name"] == name
    if stream not in state["streams"]:
        response = error(404, 10059, "stream not found")
    else:
        state["consumers"].setdefault(stream, {})[name] = request["config"]
        response = {"config": request["config"]}
else:
    raise SystemExit(f"unexpected request {subject}")
save()
print(json.dumps(response), end="")
"""


@pytest.fixture
def jetstream(tmp_path: Path) -> dict[str, Any]:
    """The JetStream Job's files, a fake `nats`, and its state."""
    if shutil.which("jq") is None:
        pytest.fail("The JetStream Job's tests need jq, which its image has")
    config = tmp_path / "config"
    config.mkdir()
    for name, content in jetstream_files().items():
        (config / name).write_text(content)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"streams": {}, "consumers": {}, "log": [], "failures": []}))
    bin = tmp_path / "bin"
    bin.mkdir()
    (tmp_path / "fake_nats.py").write_text(FAKE_NATS)
    nats = bin / "nats"
    nats.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{tmp_path / "fake_nats.py"}" "{state}" "$@"\n'
    )
    nats.chmod(0o755)
    return {"config": config, "state": state, "bin": bin}


def run_jetstream_job(jetstream: dict[str, Any]) -> dict[str, Any]:
    env = {
        **os.environ,
        "PATH": f"{jetstream['bin']}{os.pathsep}{os.environ['PATH']}",
        "CONFIG": str(jetstream["config"]),
        "NATS_URL": "nats://nats.test:4222",
    }
    subprocess.run(
        ["sh", str(jetstream["config"] / "setup.sh")], env=env, check=True, capture_output=True
    )
    state = json.loads(jetstream["state"].read_text())
    assert isinstance(state, dict)
    return state


def made(log: list[Any]) -> list[tuple[str, ...]]:
    """The log's requests that make or change a stream or consumer, in order."""
    found: list[tuple[str, ...]] = []
    for subject, request in log:
        api = subject.removeprefix("$JS.API.").split(".")
        if api[0] == "STREAM" and api[1] in {"CREATE", "UPDATE"}:
            found.append((api[1], api[2], ",".join(request.get("subjects", []))))
        elif api[0] == "CONSUMER":
            found.append(("CONSUMER", api[2], api[3]))
    return found


@pytest.mark.spec("NATS-3", "NATS-5")
def test_the_job_makes_each_stream_and_consumer_as_the_values_say(
    jetstream: dict[str, Any],
) -> None:
    state = run_jetstream_job(jetstream)

    for stream in streams():
        files = jetstream_files()
        assert state["streams"][stream["name"]] == json.loads(
            files[f"stream-{stream['name']}.json"]
        )
        for consumer in stream["consumers"]:
            request = json.loads(files[f"consumer-{stream['name']}-{consumer['name']}.json"])
            assert state["consumers"][stream["name"]][consumer["name"]] == request["config"]


@pytest.mark.spec("NATS-3")
@pytest.mark.parametrize("failure", ["unavailable", "exit", "garbled"])
def test_the_job_waits_until_jetstream_accepts_its_requests(
    jetstream: dict[str, Any], failure: str
) -> None:
    state = json.loads(jetstream["state"].read_text())
    state["failures"] = [failure] * 2
    jetstream["state"].write_text(json.dumps(state))
    script = (jetstream["config"] / "setup.sh").read_text()
    (jetstream["config"] / "setup.sh").write_text(script.replace("sleep 5", "sleep 0"))

    state = run_jetstream_job(jetstream)

    subjects = [subject for subject, _ in state["log"]]
    assert subjects[:3] == ["$JS.API.INFO"] * 3
    assert set(state["streams"]) == set(STREAMS)


@pytest.mark.spec("NATS-5")
def test_each_stream_by_interest_takes_its_subjects_only_once_its_consumers_exist(
    jetstream: dict[str, Any],
) -> None:
    log = made(run_jetstream_job(jetstream)["log"])

    assert log == [
        ("CREATE", "NOTICES", ""),
        ("CONSUMER", "NOTICES", "scheduler-notices"),
        ("CONSUMER", "NOTICES", "parse-workers-notices"),
        ("UPDATE", "NOTICES", ",".join(STREAMS["NOTICES"][0])),
        ("CREATE", "RECORDS", ""),
        ("CONSUMER", "RECORDS", "discoverer-polls"),
        ("CONSUMER", "RECORDS", "discoverer-jobs"),
        ("UPDATE", "RECORDS", ",".join(STREAMS["RECORDS"][0])),
        ("CREATE", "ORDERS", ",".join(STREAMS["ORDERS"][0])),
        ("CONSUMER", "ORDERS", "discoverer-orders"),
        ("CREATE", "JOBS", ",".join(STREAMS["JOBS"][0])),
    ]


@pytest.mark.spec("NATS-3", "NATS-5")
def test_the_job_succeeds_again_and_changes_nothing(jetstream: dict[str, Any]) -> None:
    first = run_jetstream_job(jetstream)
    again = run_jetstream_job(jetstream)

    assert again["streams"] == first["streams"]
    assert again["consumers"] == first["consumers"]
    log = made(again["log"][len(first["log"]) :])
    assert [entry for entry in log if entry[0] == "CREATE"] == []
    # A stream by interest that exists keeps its subjects throughout.
    assert all(entry[2] for entry in log if entry[0] == "UPDATE")


@pytest.mark.spec("NATS-5")
def test_a_stream_by_interest_that_exists_takes_a_new_subject_after_its_consumer(
    jetstream: dict[str, Any],
) -> None:
    first = run_jetstream_job(jetstream)
    config = jetstream["config"]
    stream = json.loads((config / "stream-NOTICES.json").read_text())
    stream["subjects"].append("edgar.notices.new")
    (config / "stream-NOTICES.json").write_text(json.dumps(stream))
    consumers = config / "consumers-NOTICES"
    consumers.write_text(consumers.read_text() + "new-notices\n")
    (config / "consumer-NOTICES-new-notices.json").write_text(
        json.dumps(
            {
                "stream_name": "NOTICES",
                "config": {"durable_name": "new-notices", "filter_subjects": ["edgar.notices.new"]},
            }
        )
    )

    log = made(run_jetstream_job(jetstream)["log"][len(first["log"]) :])

    notices = [entry for entry in log if entry[1] == "NOTICES"]
    taken = [i for i, entry in enumerate(notices) if "edgar.notices.new" in entry[2]]
    assert taken
    assert notices.index(("CONSUMER", "NOTICES", "new-notices")) < taken[0]


@pytest.mark.spec("NATS-3")
def test_a_work_queue_that_exists_is_updated_to_the_values(jetstream: dict[str, Any]) -> None:
    run_jetstream_job(jetstream)
    config = jetstream["config"]
    queues = [s["name"] for s in streams() if s["retention"] == "workqueue"]
    changed: dict[str, Any] = {}
    for name in queues:
        stream = json.loads((config / f"stream-{name}.json").read_text())
        stream["subjects"].append(f"edgar.{name.lower()}.new")
        (config / f"stream-{name}.json").write_text(json.dumps(stream))
        changed[name] = stream

    state = run_jetstream_job(jetstream)

    assert queues
    for name in queues:
        assert state["streams"][name] == changed[name]


# GARAGE


def garage_values() -> dict[str, Any]:
    given = values(UMBRELLA)["garage"]
    assert isinstance(given, dict)
    return given


@pytest.mark.spec("GARAGE-1")
def test_garage_is_three_nodes_with_a_replication_factor_of_3() -> None:
    config = config_map_data(UMBRELLA, "garage-config")["garage.toml"]

    assert stateful_set("garage")["spec"]["replicas"] == 3
    assert re.search(r"(?m)^replication_factor = 3$", config)
    claims = {c["metadata"]["name"] for c in stateful_set("garage")["spec"]["volumeClaimTemplates"]}
    assert claims == {"meta", "data"}


@pytest.mark.spec("GARAGE-1", "CRD-6")
def test_garage_finds_its_peers_without_a_definition_or_cluster_role() -> None:
    config = config_map_data(UMBRELLA, "garage-config")["garage.toml"]
    garages = [
        doc
        for doc in rendered(UMBRELLA)
        if doc["kind"] in {"ClusterRole", "ClusterRoleBinding", "Role"}
        and "garagenodes" in json.dumps(doc)
    ]

    assert garage_values()["garage"]["kubernetesSkipCrd"] is True
    assert garage_values()["garage"]["noClusterRole"] is True
    assert re.search(r"(?m)^skip_crd = true$", config)
    assert [doc["kind"] for doc in garages] == ["Role"]


def garage_setup() -> dict[str, Any]:
    given = values(UMBRELLA)["garageSetup"]
    assert isinstance(given, dict)
    return given


@pytest.mark.spec("GARAGE-2")
def test_the_garage_job_is_a_hook_after_each_install_and_upgrade() -> None:
    assert hook(named(UMBRELLA, "Job", "garage-setup")) == {"post-install", "post-upgrade"}


@pytest.mark.spec("GARAGE-2", "GARAGE-4")
def test_the_values_list_two_buckets_and_their_keys() -> None:
    assert garage_setup()["buckets"] == {
        "database-backups": {"database-backups": {"read": True, "write": True}},
        "snapshots": {
            "fetcher": {"read": True, "write": True},
            "discoverer": {"read": True, "write": False},
        },
    }


@pytest.mark.spec("GARAGE-4")
def test_each_key_may_use_one_bucket() -> None:
    keys = [name for grants in garage_setup()["buckets"].values() for name in grants]

    assert len(keys) == len(set(keys))


@pytest.mark.spec("GARAGE-2", "SECRET-1")
def test_the_garage_job_reads_each_key_from_its_secret() -> None:
    job = named(UMBRELLA, "Job", "garage-setup")
    volumes = {v["name"]: v for v in pod_spec(job)["volumes"] if "secret" in v}
    keys = {name for grants in garage_setup()["buckets"].values() for name in grants}

    assert {v["secret"]["secretName"] for v in volumes.values()} == {
        f"garage-key-{name}" for name in keys
    }
    for name in keys:
        secret = named(UMBRELLA, "Secret", f"garage-key-{name}")["stringData"]
        assert secret["AWS_ACCESS_KEY_ID"] == CREDENTIALS["garageKeys"][name]["id"]
        assert secret["AWS_SECRET_ACCESS_KEY"] == CREDENTIALS["garageKeys"][name]["secret"]


def load_garage_setup() -> ModuleType:
    path = DEPLOY / UMBRELLA / "files" / "garage_setup.py"
    spec = importlib.util.spec_from_file_location("garage_setup", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    setattr(module, "RETRY_SECONDS", 0)  # noqa: B010 - the module's own constant
    return module


class FakeGarage:
    """Garage's admin API, as far as the Job uses it, with the state it changes."""

    def __init__(self, token: str, nodes: int) -> None:
        self.token = token
        self.nodes: list[dict[str, Any]] = [
            {"id": f"{i:064x}", "hostname": f"garage-{i}", "isUp": True} for i in range(nodes)
        ]
        self.up_after = 0
        self.layout: dict[str, Any] = {"version": 0, "roles": []}
        self.staged: list[dict[str, Any]] = []
        self.keys: dict[str, dict[str, str]] = {}
        self.buckets: dict[str, dict[str, Any]] = {}
        self.changes: list[str] = []

    def handle(self, method: str, path: str, query: dict[str, str], body: Any) -> Any:
        endpoint = path.removeprefix("/v2/")
        if endpoint == "GetClusterStatus":
            self.up_after -= 1
            up = self.nodes if self.up_after < 0 else self.nodes[:-1]
            return {"layoutVersion": self.layout["version"], "nodes": up}
        if endpoint == "GetClusterLayout":
            return {**self.layout, "stagedRoleChanges": self.staged}
        if endpoint == "UpdateClusterLayout":
            self.staged += body["roles"]
            self.changes.append(endpoint)
            return {}
        if endpoint == "ApplyClusterLayout":
            assert body["version"] == self.layout["version"] + 1
            self.layout = {"version": body["version"], "roles": self.layout["roles"] + self.staged}
            self.staged = []
            self.changes.append(endpoint)
            return {}
        if endpoint == "GetKeyInfo":
            key = self.keys.get(query["id"])
            return None if key is None else {"accessKeyId": query["id"], "name": key["name"]}
        if endpoint == "ImportKey":
            self.keys[body["accessKeyId"]] = {
                "name": body["name"],
                "secret": body["secretAccessKey"],
            }
            self.changes.append(endpoint)
            return {"accessKeyId": body["accessKeyId"]}
        if endpoint == "GetBucketInfo":
            bucket = self.buckets.get(query["globalAlias"])
            return None if bucket is None else {"id": bucket["id"]}
        if endpoint == "CreateBucket":
            bucket = {"id": f"bucket-{body['globalAlias']}", "keys": {}}
            self.buckets[body["globalAlias"]] = bucket
            self.changes.append(endpoint)
            return {"id": bucket["id"]}
        if endpoint in {"AllowBucketKey", "DenyBucketKey"}:
            [bucket] = [b for b in self.buckets.values() if b["id"] == body["bucketId"]]
            permissions = bucket["keys"].setdefault(
                body["accessKeyId"], {"read": False, "write": False, "owner": False}
            )
            for permission, given in body["permissions"].items():
                if given:
                    permissions[permission] = endpoint == "AllowBucketKey"
            return {}
        raise AssertionError(f"unexpected {method} {path}")


@pytest.fixture
def garage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGarage]:
    """A fake of Garage's admin API on a local server, and the Job's environment."""
    token = CREDENTIALS["garage"]["adminToken"]
    fake = FakeGarage(token, garage_values()["deployment"]["replicaCount"])

    class Handler(BaseHTTPRequestHandler):
        def respond(self) -> None:
            if self.headers["Authorization"] != f"Bearer {token}":
                self.send_response(403)
                self.end_headers()
                return
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length)) if length else None
            result = fake.handle(self.command, url.path, query, body)
            self.send_response(404 if result is None else 200)
            self.end_headers()
            if result is not None:
                self.wfile.write(json.dumps(result).encode())

        do_GET = do_POST = respond

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = tmp_path / "garage.json"
    config.write_text(config_map_data(UMBRELLA, "garage-setup")["garage.json"])
    for name, key in CREDENTIALS["garageKeys"].items():
        (tmp_path / "keys" / name).mkdir(parents=True)
        (tmp_path / "keys" / name / "AWS_ACCESS_KEY_ID").write_text(key["id"])
        (tmp_path / "keys" / name / "AWS_SECRET_ACCESS_KEY").write_text(key["secret"])
    monkeypatch.setenv("GARAGE_ADMIN_URL", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("GARAGE_ADMIN_TOKEN", token)
    monkeypatch.setenv("CONFIG", str(config))
    monkeypatch.setenv("KEYS", str(tmp_path / "keys"))
    yield fake
    server.shutdown()
    server.server_close()


@pytest.mark.spec("GARAGE-2")
def test_the_garage_job_is_the_charts_script() -> None:
    script = config_map_data(UMBRELLA, "garage-setup")["garage_setup.py"]

    assert script.strip() == (DEPLOY / UMBRELLA / "files" / "garage_setup.py").read_text().strip()


@pytest.mark.spec("GARAGE-2")
def test_the_garage_job_gives_each_node_a_zone_and_a_capacity(garage: FakeGarage) -> None:
    garage.up_after = 2

    load_garage_setup().main()

    roles = garage.layout["roles"]
    assert garage.layout["version"] == 1
    assert sorted(r["id"] for r in roles) == sorted(n["id"] for n in garage.nodes)
    assert len({r["zone"] for r in roles}) == len(roles) == 3
    assert {r["capacity"] for r in roles} == {garage_setup()["capacity"]}


@pytest.mark.spec("GARAGE-2", "GARAGE-4")
def test_the_garage_job_imports_each_key_and_grants_its_buckets(garage: FakeGarage) -> None:
    load_garage_setup().main()

    keys = CREDENTIALS["garageKeys"]
    assert garage.keys == {
        key["id"]: {"name": name, "secret": key["secret"]} for name, key in keys.items()
    }
    assert {
        bucket: {key: p for key, p in b["keys"].items()} for bucket, b in garage.buckets.items()
    } == {
        bucket: {
            keys[name]["id"]: {**permissions, "owner": False}
            for name, permissions in grants.items()
        }
        for bucket, grants in garage_setup()["buckets"].items()
    }


@pytest.mark.spec("GARAGE-2")
def test_the_garage_job_succeeds_again_and_changes_nothing(garage: FakeGarage) -> None:
    load_garage_setup().main()
    first = (
        copy.deepcopy(garage.layout),
        copy.deepcopy(garage.keys),
        copy.deepcopy(garage.buckets),
    )
    changes = len(garage.changes)

    load_garage_setup().main()

    assert (garage.layout, garage.keys, garage.buckets) == first
    assert garage.changes[changes:] == []


@pytest.mark.spec("GARAGE-4")
def test_the_garage_job_takes_back_what_the_values_dont_grant(garage: FakeGarage) -> None:
    load_garage_setup().main()
    discoverer = CREDENTIALS["garageKeys"]["discoverer"]["id"]
    garage.buckets["snapshots"]["keys"][discoverer] = {"read": True, "write": True, "owner": True}

    load_garage_setup().main()

    assert garage.buckets["snapshots"]["keys"][discoverer] == {
        "read": True,
        "write": False,
        "owner": False,
    }


# MON


@pytest.mark.spec("MON-1")
def test_nothing_kind_doesnt_expose_is_scraped() -> None:
    hidden = re.compile(r"controller-manager|kube-scheduler|etcd|kube-proxy", re.I)
    found = [
        doc["metadata"]["name"]
        for doc in rendered(UMBRELLA)
        if doc["kind"] in {"ServiceMonitor", "PodMonitor", "PrometheusRule", "Service"}
        and hidden.search(doc["metadata"]["name"])
    ]
    groups = [
        g["name"]
        for doc in of_kind(UMBRELLA, "PrometheusRule")
        for g in doc["spec"]["groups"]
        if hidden.search(g["name"])
    ]

    assert found == []
    assert groups == []


def grafana() -> dict[str, Any]:
    return named(UMBRELLA, "Deployment", "grafana")


def grafana_ini() -> str:
    return config_map_data(UMBRELLA, "grafana")["grafana.ini"]


def ini_section(ini: str, section: str) -> dict[str, str]:
    found: dict[str, str] = {}
    current = None
    for line in ini.splitlines():
        line = line.strip()
        if line.startswith("["):
            current = line.strip("[]")
        elif "=" in line and current == section:
            key, value = (part.strip() for part in line.split("=", 1))
            found[key] = value
    return found


@pytest.mark.spec("MON-2")
def test_grafana_admits_anonymous_viewers_only_and_no_sign_in() -> None:
    ini = grafana_ini()

    assert ini_section(ini, "auth.anonymous") == {"enabled": "true", "org_role": "Viewer"}
    assert ini_section(ini, "auth")["disable_login_form"] == "true"
    assert ini_section(ini, "auth.basic")["enabled"] == "false"
    assert ini_section(ini, "users")["allow_sign_up"] == "false"


@pytest.mark.spec("MON-2")
def test_grafanas_data_sources_come_only_from_its_value_which_restarts_it() -> None:
    changed = copy.deepcopy(TEST_VALUES)
    changed["kube-prometheus-stack"] = {
        "grafana": copy.deepcopy(values(UMBRELLA)["kube-prometheus-stack"]["grafana"])
    }
    sources = changed["kube-prometheus-stack"]["grafana"]["datasources"]["datasources.yaml"]
    sources["datasources"][0]["url"] = "http://changed:9090/"
    [deployment] = [
        d
        for d in documents(render(UMBRELLA, changed).stdout)
        if d["metadata"]["name"] == "grafana" and d["kind"] == "Deployment"
    ]
    checksum = "checksum/config"

    assert "datasources.yaml" in config_map_data(UMBRELLA, "grafana")
    labelled = [
        d["metadata"]["name"]
        for d in of_kind(UMBRELLA, "ConfigMap")
        if "grafana_datasource" in d["metadata"].get("labels", {})
    ]
    assert labelled == []
    annotations = grafana()["spec"]["template"]["metadata"]["annotations"]
    assert (
        deployment["spec"]["template"]["metadata"]["annotations"][checksum] != annotations[checksum]
    )


@pytest.mark.spec("MON-2")
def test_grafanas_sidecar_provisions_dashboards_only_without_a_reload() -> None:
    sidecars = [c for c in containers(grafana()) if c["name"] != "grafana"]

    assert [c["name"] for c in sidecars] == ["grafana-sc-dashboard"]
    [sidecar] = sidecars
    assert "REQ_URL" not in {e["name"] for e in sidecar.get("env", [])}


@pytest.mark.spec("MON-2")
def test_a_provisioned_dashboard_cant_be_saved_from_grafana() -> None:
    providers = yaml.safe_load(
        config_map_data(UMBRELLA, "grafana-config-dashboards")["provider.yaml"]
    )

    assert [p["allowUiUpdates"] for p in providers["providers"]] == [False]


@pytest.mark.spec("MON-3")
def test_alertmanager_sends_nothing_outside_the_cluster() -> None:
    [secret] = [
        d for d in of_kind(UMBRELLA, "Secret") if d["metadata"]["name"].startswith("alertmanager-")
    ]
    config = yaml.safe_load(
        secret.get("stringData", {}).get("alertmanager.yaml")
        or base64.b64decode(secret["data"]["alertmanager.yaml"]).decode()
    )

    assert config["receivers"] == [{"name": "null"}]
    assert config["route"]["receiver"] == "null"


@pytest.mark.spec("MON-4")
def test_prometheus_alertmanager_and_grafana_run_one_replica() -> None:
    for kind in ("Prometheus", "Alertmanager"):
        [resource] = of_kind(UMBRELLA, kind)
        assert resource["spec"]["replicas"] == 1, kind
    assert grafana()["spec"]["replicas"] == 1


@pytest.mark.spec("MON-4")
def test_grafana_keeps_no_data() -> None:
    claims = [v for v in pod_spec(grafana())["volumes"] if "persistentVolumeClaim" in v]

    assert claims == []


def data_sources() -> list[dict[str, Any]]:
    given = yaml.safe_load(config_map_data(UMBRELLA, "grafana")["datasources.yaml"])
    sources = given["datasources"]
    assert isinstance(sources, list)
    return sources


@pytest.mark.spec("MON-5")
def test_grafana_reads_the_databases_replicas_as_its_role() -> None:
    [source] = [s for s in data_sources() if "postgres" in s["type"]]
    host, port = source["url"].split(":")

    assert source["editable"] is False
    assert host == f"{the_cluster()['metadata']['name']}-ro"
    assert port == "5432"
    assert source["user"] == "grafana"
    assert source["jsonData"]["database"] == "edgar"


@pytest.mark.spec("MON-5", "SECRET-3")
def test_grafanas_database_password_is_from_its_roles_secret() -> None:
    [source] = [s for s in data_sources() if "postgres" in s["type"]]
    password = source["secureJsonData"]["password"]
    [container] = [c for c in containers(grafana()) if c["name"] == "grafana"]
    env = {e["name"]: e for e in container["env"]}
    variable = password.removeprefix("$").strip("{}")

    assert password.startswith("$")
    assert env[variable]["valueFrom"]["secretKeyRef"] == {
        "name": roles()["grafana"]["passwordSecret"]["name"],
        "key": "password",
    }


@pytest.mark.spec("MON-5")
def test_grafana_verifies_the_server_against_the_clusters_authority_alone() -> None:
    [source] = [s for s in data_sources() if "postgres" in s["type"]]
    [container] = [c for c in containers(grafana()) if c["name"] == "grafana"]
    root = Path(source["jsonData"]["sslRootCertFile"])
    [mount] = [m for m in container["volumeMounts"] if Path(m["mountPath"]) == root.parent]
    [volume] = [v for v in pod_spec(grafana())["volumes"] if v["name"] == mount["name"]]

    assert source["jsonData"]["sslmode"] == "verify-full"
    assert volume["secret"]["secretName"] == f"{the_cluster()['metadata']['name']}-ca"
    assert volume["secret"]["items"] == [{"key": "ca.crt", "path": root.name}]


# SECRET


def credential_values() -> list[str]:
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        else:
            found.append(str(node))

    walk(CREDENTIALS)
    return found


@pytest.mark.spec("SECRET-1")
def test_the_committed_values_set_no_credential() -> None:
    result = render(UMBRELLA, {}, check=False)

    assert "credentials" not in values(UMBRELLA)
    assert result.returncode != 0
    assert "credentials" in result.stderr


@pytest.mark.spec("SECRET-1")
@pytest.mark.parametrize(
    "path",
    [
        ("garage", "rpcSecret"),
        ("garage", "adminToken"),
        ("garageKeys", "database-backups"),
        ("garageKeys", "fetcher"),
        ("garageKeys", "discoverer"),
        ("database", "fetcher"),
        ("database", "discoverer"),
        ("database", "grafana"),
        ("grafana", "adminPassword"),
    ],
)
def test_each_credential_is_required(path: tuple[str, str]) -> None:
    without = copy.deepcopy(TEST_VALUES)
    del without["credentials"][path[0]][path[1]]

    assert render(UMBRELLA, without, check=False).returncode != 0


@pytest.mark.spec("SECRET-1")
@pytest.mark.parametrize("credential", ["database", "garageKeys"])
def test_a_role_or_key_the_values_add_requires_its_credential(credential: str) -> None:
    given = copy.deepcopy(TEST_VALUES)
    if credential == "database":
        given["database"]["services"] = [*values(UMBRELLA)["database"]["services"], "added"]
    else:
        given["garageSetup"] = {"buckets": {"snapshots": {"added": {"read": True, "write": False}}}}
    with_it = copy.deepcopy(given)
    with_it["credentials"][credential]["added"] = CREDENTIALS[credential]["fetcher"]

    without = render(UMBRELLA, given, check=False)

    assert without.returncode != 0
    assert f"credentials.{credential}.added is required" in without.stderr
    assert render(UMBRELLA, with_it, check=False).returncode == 0


@pytest.mark.spec("SECRET-1")
def test_each_credential_is_rendered_into_a_secret() -> None:
    secrets = json.dumps([d for d in of_kind(UMBRELLA, "Secret")])

    for value in credential_values():
        assert value in secrets, value


@pytest.mark.spec("SECRET-1")
def test_garage_reads_its_credentials_from_the_charts_secret() -> None:
    [garage_container] = [c for c in containers(stateful_set("garage")) if c["name"] == "garage"]
    env = {e["name"]: e for e in garage_container["env"]}
    secret = named(UMBRELLA, "Secret", "garage-credentials")["stringData"]

    assert garage_values()["garage"]["existingRpcSecret"] == "garage-credentials"
    assert env["GARAGE_ADMIN_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "garage-credentials",
        "key": "adminToken",
    }
    assert secret == CREDENTIALS["garage"]


@pytest.mark.spec("SECRET-1")
def test_grafanas_admin_password_is_from_the_charts_secret() -> None:
    admin = values(UMBRELLA)["kube-prometheus-stack"]["grafana"]["admin"]
    secret = named(UMBRELLA, "Secret", admin["existingSecret"])["stringData"]

    assert secret[admin["passwordKey"]] == CREDENTIALS["grafana"]["adminPassword"]


@pytest.mark.spec("SECRET-2")
@pytest.mark.parametrize("chart", CHARTS)
def test_the_same_values_render_the_same_manifests(chart: str) -> None:
    assert render(chart).stdout == render(chart).stdout


@pytest.mark.spec("SECRET-3")
@pytest.mark.parametrize("chart", CHARTS)
def test_a_credential_appears_only_in_a_secret(chart: str) -> None:
    for doc in rendered(chart):
        if doc["kind"] == "Secret":
            continue
        text = json.dumps(doc)
        assert [v for v in credential_values() if v in text] == [], (
            doc["kind"],
            doc["metadata"]["name"],
        )
