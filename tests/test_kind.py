"""spec/deployment.md's rules: how the installed charts behave on kind.

CI's kind job installs the four charts on kind, upgrades them, and runs these
tests (spec/deployment.md, section 3). They run only where
EDGAR_KUBE_CONTEXT names the kube context of a cluster the charts are
installed on, as the owner's can be, and are skipped elsewhere. They reach
the cluster through kubectl and Helm, and nothing else: each check inside it
runs in a pod there. A credential they read from a Secret goes to its pod on
standard input, never in a command's arguments, so a failure never shows it.
Each test carries the IDs of the statements it checks, as
@pytest.mark.spec(...).
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "spec" / "schemas"
CONTEXT = os.environ.get("EDGAR_KUBE_CONTEXT", "")
NAMESPACE = "edgar"
NATS_URL = "nats://nats:4222"

pytestmark = pytest.mark.skipif(
    not CONTEXT, reason="no cluster of the installed charts: EDGAR_KUBE_CONTEXT isn't set"
)

# The four releases, in the order they're installed (section 5).
RELEASES = [
    ("prometheus-crds", "edgar"),
    ("cert-manager", "cert-manager"),
    ("operators", "cnpg-system"),
    ("edgar", "edgar"),
]
# A third-party image, named by its tag and its digest (PIN-4, PIN-5).
PINNED_IMAGE = re.compile(r"^[^\s@]+:[^\s@/:]+@sha256:[0-9a-f]{64}$")


def kubectl(*args: str, input: str | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["kubectl", "--context", CONTEXT, *args],
        input=input,
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        # The command, but no input, which may hold a credential.
        raise AssertionError(f"kubectl {' '.join(args)}: {result.stderr}")
    return result.stdout


def kubectl_json(*args: str) -> Any:
    return json.loads(kubectl(*args, "-o", "json"))


def helm_json(*args: str) -> Any:
    result = subprocess.run(
        ["helm", "--kube-context", CONTEXT, *args, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def apply(manifest: dict[str, Any]) -> None:
    kubectl("apply", "-f", "-", input=json.dumps(manifest))


def values() -> dict[str, Any]:
    loaded = yaml.safe_load((ROOT / "deploy" / "chart" / "values.yaml").read_text())
    assert isinstance(loaded, dict)
    return loaded


def secret(name: str, key: str) -> str:
    data = kubectl_json("get", "secret", "-n", NAMESPACE, name)["data"]
    return base64.b64decode(data[key]).decode()


def wait_until(check: Any, timeout: float, what: str) -> Any:
    """Calls `check` until it returns something true, and returns that."""
    deadline = time.monotonic() + timeout
    while True:
        found = check()
        if found:
            return found
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(5)


def pods(selector: str) -> list[dict[str, Any]]:
    found = kubectl_json("get", "pods", "-n", NAMESPACE, "-l", selector)["items"]
    assert isinstance(found, list)
    return found


def in_nats_box(script: str, *args: str, input: str | None = None, check: bool = True) -> str:
    """Runs a shell script in the NATS chart's nats-box, which has nats, curl, and jq."""
    return kubectl(
        "exec",
        "-i",
        "-n",
        NAMESPACE,
        "deploy/nats-box",
        "--",
        "sh",
        "-c",
        script,
        "sh",
        *args,
        input=input,
        check=check,
    )


def jetstream(subject: str, body: str = "") -> dict[str, Any]:
    """One request to JetStream's API."""
    response = in_nats_box(
        'nats --server "$1" request --raw "$2" "$3"', NATS_URL, f"$JS.API.{subject}", body
    )
    found = json.loads(response)
    assert isinstance(found, dict)
    return found


def garage_admin(endpoint: str) -> Any:
    """A GET of Garage's admin API, with the token given on standard input."""
    response = in_nats_box(
        'read -r token; curl -sf -H "Authorization: Bearer $token" "$1"',
        f"http://garage-admin:3903/v2/{endpoint}",
        input=secret("garage-credentials", "adminToken") + "\n",
    )
    return json.loads(response)


def primary() -> str:
    [pod] = pods("cnpg.io/cluster=postgres,cnpg.io/instanceRole=primary")
    return str(pod["metadata"]["name"])


def psql(role: str, sql: str) -> subprocess.CompletedProcess[str]:
    """Runs SQL in edgar as a role, through the Service of the primary, with
    the role's password from its Secret given on standard input."""
    return subprocess.run(
        [
            "kubectl",
            "--context",
            CONTEXT,
            "exec",
            "-i",
            "-n",
            NAMESPACE,
            primary(),
            "-c",
            "postgres",
            "--",
            "sh",
            "-c",
            'read -r PGPASSWORD; export PGPASSWORD; exec psql "$1" -X -qAt -c "$2"',
            "sh",
            f"host=postgres-rw dbname=edgar user={role} sslmode=require",
            sql,
        ],
        input=secret(f"postgres-{role}", "password") + "\n",
        capture_output=True,
        text=True,
        check=False,
    )


def attempt(role: str, statement: str) -> subprocess.CompletedProcess[str]:
    """Runs a write as a role, in a transaction that is rolled back, so the
    write changes nothing even when the role may make it."""
    return psql(role, f"BEGIN; {statement}; ROLLBACK;")


def rerun_job(name: str) -> None:
    """Runs a hook Job again, as an upgrade does, and waits for it to succeed."""
    job = kubectl_json("get", "job", "-n", NAMESPACE, name)
    template = job["spec"]["template"]
    template["metadata"] = {}
    again: dict[str, Any] = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": f"{name}-again-{uuid.uuid4().hex[:6]}", "namespace": NAMESPACE},
        "spec": {"backoffLimit": 0, "template": template},
    }
    apply(again)
    try:
        kubectl(
            "wait",
            "-n",
            NAMESPACE,
            f"job/{again['metadata']['name']}",
            "--for=condition=Complete",
            "--timeout=300s",
        )
    finally:
        kubectl("delete", "job", "-n", NAMESPACE, again["metadata"]["name"], "--wait=false")


# CHART


def helm_time(given: str) -> datetime:
    """A time as Helm gives it, its fraction cut to microseconds."""
    return datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", given))


@pytest.mark.spec("CHART-4")
def test_the_four_releases_are_installed_and_upgraded_in_order() -> None:
    histories = [helm_json("history", release, "-n", namespace) for release, namespace in RELEASES]

    for (release, _), history in zip(RELEASES, histories, strict=True):
        assert history[-1]["status"] == "deployed", release
    revisions = min(len(history) for history in histories)
    for revision in range(revisions):
        started = [helm_time(history[revision]["updated"]) for history in histories]
        assert started == sorted(started), revision


# PIN


@pytest.mark.spec("PIN-5")
def test_every_container_of_the_charts_runs_an_image_named_by_digest() -> None:
    namespaces = sorted({namespace for _, namespace in RELEASES})
    unpinned = [
        (pod["metadata"]["namespace"], pod["metadata"]["name"], container["image"])
        for namespace in namespaces
        for pod in kubectl_json("get", "pods", "-n", namespace)["items"]
        for container in [*pod["spec"].get("initContainers", []), *pod["spec"]["containers"]]
        if not PINNED_IMAGE.match(container["image"])
    ]

    assert unpinned == []


# PG


@pytest.mark.spec("PG-2")
def test_every_instance_is_postgresql_18() -> None:
    instances = pods("cnpg.io/cluster=postgres,cnpg.io/podRole=instance")

    assert len(instances) == 3
    for pod in instances:
        version = kubectl(
            "exec",
            "-n",
            NAMESPACE,
            pod["metadata"]["name"],
            "-c",
            "postgres",
            "--",
            "psql",
            "-X",
            "-At",
            "-c",
            "SHOW server_version_num",
        )
        assert 180000 <= int(version) <= 189999, pod["metadata"]["name"]


@pytest.mark.spec("PG-5")
def test_archiving_to_garage_works_and_a_base_backup_completes() -> None:
    def archiving() -> bool:
        cluster = kubectl_json("get", "clusters.postgresql.cnpg.io", "-n", NAMESPACE, "postgres")
        conditions = {c["type"]: c["status"] for c in cluster["status"].get("conditions", [])}
        return conditions.get("ContinuousArchiving") == "True"

    wait_until(archiving, 300, "continuous archiving")
    name = f"pg-5-{uuid.uuid4().hex[:6]}"
    apply(
        {
            "apiVersion": "postgresql.cnpg.io/v1",
            "kind": "Backup",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "spec": {
                "cluster": {"name": "postgres"},
                "method": "plugin",
                "pluginConfiguration": {"name": "barman-cloud.cloudnative-pg.io"},
            },
        }
    )
    try:

        def phase() -> str:
            backup = kubectl_json("get", "backups.postgresql.cnpg.io", "-n", NAMESPACE, name)
            found = str(backup.get("status", {}).get("phase", ""))
            return found if found in {"completed", "failed"} else ""

        assert wait_until(phase, 600, "the base backup") == "completed"
    finally:
        kubectl("delete", "backups.postgresql.cnpg.io", "-n", NAMESPACE, name, "--wait=false")


TABLE = re.compile(r"^CREATE TABLE (\w+) \((.*?)^\);", re.M | re.S)
COLUMN = re.compile(r"^    ([a-z_][a-z0-9_]*) +(.+?),?$", re.M)
VIEW = re.compile(r"^CREATE VIEW ([\w.]+) \((.*?)\) AS$", re.M | re.S)


def defined(file: str) -> dict[str, list[str]]:
    """Each table and view a file of spec/schemas/ creates, with its columns."""
    sql = (SCHEMAS / file).read_text(encoding="utf-8")
    found = {name: COLUMN.findall(body) for name, body in TABLE.findall(sql)}
    found = {name: [column for column, _ in columns] for name, columns in found.items()}
    for name, columns in VIEW.findall(sql):
        found[name] = [column.strip() for column in columns.split(",")]
    return found


def held(names: list[str]) -> dict[str, list[str]]:
    """The columns the database holds for each table or view, in order."""
    held_columns: dict[str, list[str]] = {}
    for name in names:
        schema, _, relation = name.rpartition(".")
        result = psql(
            "discoverer" if not schema else "grafana",
            "SELECT column_name FROM information_schema.columns"
            f" WHERE table_schema = '{schema or 'public'}' AND table_name = '{relation}'"
            " ORDER BY ordinal_position",
        )
        assert result.returncode == 0, result.stderr
        held_columns[name] = result.stdout.split()
    return held_columns


def privilege(role: str, relation: str, privilege: str) -> bool:
    result = psql(role, f"SELECT has_table_privilege('{relation}', '{privilege}')")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip() == "t"


WRITES = ["INSERT", "UPDATE", "DELETE", "TRUNCATE"]


@pytest.mark.spec("PG-9")
def test_records_is_as_records_sql_defines_it() -> None:
    assert held(list(defined("records.sql"))) == defined("records.sql")


@pytest.mark.spec("PG-9")
def test_the_fetchers_role_can_add_a_record_and_read_it() -> None:
    url = f"https://data.sec.gov/kind-test/{uuid.uuid4()}"
    result = psql(
        "fetcher",
        "BEGIN;"
        " INSERT INTO records (job, feed, url, requested_at, received_at, attempts, status, tool)"
        f" VALUES (gen_random_uuid(), 'submissions', '{url}', now(), now(), 1, 404,"
        " 'fetcher@' || repeat('0', 40));"
        f" SELECT count(*) FROM records WHERE url = '{url}';"
        " ROLLBACK;",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[-1] == "1"


@pytest.mark.spec("PG-9")
@pytest.mark.parametrize("role", ["fetcher", "discoverer"])
def test_no_services_role_can_change_a_record(role: str) -> None:
    for statement in (
        "UPDATE records SET attempts = attempts",
        "DELETE FROM records",
        "TRUNCATE records",
    ):
        result = attempt(role, statement)
        assert result.returncode != 0, statement
        assert "permission denied" in result.stderr, result.stderr


@pytest.mark.spec("PG-9")
def test_every_other_services_role_can_only_read_records() -> None:
    insert = attempt(
        "discoverer",
        "INSERT INTO records (job, feed, url, requested_at, attempts, error, tool)"
        " VALUES (gen_random_uuid(), 'submissions', 'https://data.sec.gov/kind-test', now(), 1,"
        " 'test', 'fetcher@' || repeat('0', 40))",
    )

    assert psql("discoverer", "SELECT count(*) FROM records").returncode == 0
    assert insert.returncode != 0
    assert "permission denied" in insert.stderr


def notices() -> dict[str, list[str]]:
    return defined("notices.sql")


def notices_tables() -> list[str]:
    sql = (SCHEMAS / "notices.sql").read_text(encoding="utf-8")
    return [name for name, _ in TABLE.findall(sql)]


@pytest.mark.spec("PG-14")
def test_the_notices_and_dashboards_are_as_their_files_define_them() -> None:
    assert held(list(notices())) == notices()
    assert held(list(defined("dashboards.sql"))) == defined("dashboards.sql")


@pytest.mark.spec("PG-14")
def test_only_the_discoverers_role_may_add_to_the_notices_tables() -> None:
    for table in notices_tables():
        assert privilege("discoverer", table, "INSERT"), table
        assert not privilege("fetcher", table, "INSERT"), table
    insert = attempt("fetcher", f"INSERT INTO {notices_tables()[0]} DEFAULT VALUES")
    assert insert.returncode != 0
    assert "permission denied" in insert.stderr


@pytest.mark.spec("PG-14")
@pytest.mark.parametrize("role", ["fetcher", "discoverer"])
def test_each_services_role_reads_the_notices_and_changes_no_row(role: str) -> None:
    for relation in notices():
        result = psql(role, f"SELECT count(*) FROM {relation}")
        assert result.returncode == 0, (relation, result.stderr)
    for table in notices_tables():
        for write in WRITES[1:]:
            assert not privilege(role, table, write), (table, write)
    truncate = attempt(role, f"TRUNCATE {notices_tables()[0]}")
    assert truncate.returncode != 0
    assert "permission denied" in truncate.stderr


@pytest.mark.spec("PG-14")
def test_grafanas_role_reads_the_dashboards_views_and_nothing_else() -> None:
    for view in defined("dashboards.sql"):
        result = psql("grafana", f"SELECT count(*) FROM {view}")
        assert result.returncode == 0, (view, result.stderr)
    for relation in [*defined("records.sql"), *notices()]:
        assert not privilege("grafana", relation, "SELECT"), relation
        for write in WRITES:
            assert not privilege("grafana", relation, write), (relation, write)
    read = psql("grafana", "SELECT count(*) FROM records")
    assert read.returncode != 0
    assert "permission denied" in read.stderr


# CRD


@pytest.mark.spec("CRD-6")
def test_every_definition_belongs_to_one_of_the_four_releases() -> None:
    owners = [
        (
            crd["metadata"]["name"],
            crd["metadata"].get("annotations", {}).get("meta.helm.sh/release-name"),
            crd["metadata"].get("annotations", {}).get("meta.helm.sh/release-namespace"),
        )
        for crd in kubectl_json("get", "customresourcedefinitions")["items"]
    ]

    assert owners
    assert [owner for owner in owners if (owner[1], owner[2]) not in RELEASES] == []


# VOL


@pytest.mark.spec("VOL-3")
def test_a_new_claim_of_the_same_name_finds_a_deleted_claims_data() -> None:
    namespace = f"vol-3-{uuid.uuid4().hex[:6]}"
    garage = values()["garage"]["initImage"]
    image = f"{garage['repository']}:{garage['tag']}"
    marker = uuid.uuid4().hex

    def claim() -> dict[str, Any]:
        return {
            "apiVersion": "v1",
            "kind": "PersistentVolumeClaim",
            "metadata": {"name": "claim", "namespace": namespace},
            "spec": {
                "storageClassName": "edgar-retained",
                "accessModes": ["ReadWriteOnce"],
                "resources": {"requests": {"storage": "1Mi"}},
            },
        }

    def run(name: str, script: str) -> str:
        apply(
            {
                "apiVersion": "v1",
                "kind": "Pod",
                "metadata": {"name": name, "namespace": namespace},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": name,
                            "image": image,
                            "command": ["sh", "-c", script],
                            "volumeMounts": [{"name": "claim", "mountPath": "/data"}],
                        }
                    ],
                    "volumes": [{"name": "claim", "persistentVolumeClaim": {"claimName": "claim"}}],
                },
            }
        )
        kubectl(
            "wait",
            "-n",
            namespace,
            f"pod/{name}",
            "--for=jsonpath={.status.phase}=Succeeded",
            "--timeout=180s",
        )
        return kubectl("logs", "-n", namespace, name)

    kubectl("create", "namespace", namespace)
    try:
        apply(claim())
        run("writer", f"echo {marker} > /data/marker")
        kubectl("delete", "pod", "-n", namespace, "writer")
        kubectl("delete", "pvc", "-n", namespace, "claim")
        apply(claim())

        assert run("reader", "cat /data/marker").strip() == marker
    finally:
        kubectl("delete", "namespace", namespace, "--wait=true", "--timeout=120s")
        for volume in kubectl_json("get", "persistentvolumes")["items"]:
            if volume["spec"].get("claimRef", {}).get("namespace") == namespace:
                kubectl("delete", "persistentvolume", volume["metadata"]["name"], "--wait=false")


# PLACE


@pytest.mark.spec("PLACE-2")
@pytest.mark.parametrize(
    "selector",
    [
        "app.kubernetes.io/name=nats,app.kubernetes.io/component=nats",
        "app.kubernetes.io/name=garage",
        "cnpg.io/cluster=postgres,cnpg.io/podRole=instance",
    ],
)
def test_each_replica_runs_on_a_worker_node_of_its_own(selector: str) -> None:
    workers = {
        node["metadata"]["name"]
        for node in kubectl_json("get", "nodes")["items"]
        if "node-role.kubernetes.io/control-plane" not in node["metadata"]["labels"]
    }
    nodes = [pod["spec"].get("nodeName") for pod in pods(selector)]

    assert len(workers) == 3
    assert sorted(nodes) == sorted(workers)


# NATS


def nats_monitor(server: str, endpoint: str) -> dict[str, Any]:
    found = json.loads(
        in_nats_box('curl -sf "$1"', f"http://{server}.nats-headless:8222/{endpoint}")
    )
    assert isinstance(found, dict)
    return found


@pytest.mark.spec("NATS-2")
def test_the_three_servers_form_one_jetstream_cluster_with_a_leader() -> None:
    servers = ["nats-0", "nats-1", "nats-2"]
    leaders = set()
    for server in servers:
        meta = nats_monitor(server, "jsz")["meta_cluster"]
        routes = nats_monitor(server, "routez")["routes"]
        assert meta["cluster_size"] == 3
        assert meta["leader"] in servers
        leaders.add(meta["leader"])
        assert {route["remote_name"] for route in routes} == set(servers) - {server}
    assert len(leaders) == 1


def streams() -> list[dict[str, Any]]:
    found = values()["jetstream"]["streams"]
    assert isinstance(found, list)
    return found


def on_three_servers(cluster: dict[str, Any]) -> bool:
    replicas = {replica["name"] for replica in cluster.get("replicas", [])}
    return bool(cluster.get("leader")) and len(replicas | {cluster["leader"]}) == 3


@pytest.mark.spec("NATS-4")
def test_each_stream_is_as_the_values_say_on_all_three_servers() -> None:
    for stream in streams():
        info = jetstream(f"STREAM.INFO.{stream['name']}")
        config = info["config"]
        assert config["subjects"] == stream["subjects"]
        assert config["retention"] == stream["retention"]
        assert config["storage"] == stream["storage"]
        assert config["num_replicas"] == stream["replicas"]
        assert on_three_servers(info["cluster"]), stream["name"]


@pytest.mark.spec("NATS-6")
def test_each_consumer_is_as_the_values_say_on_all_three_servers() -> None:
    for stream in streams():
        for consumer in stream["consumers"]:
            info = jetstream(f"CONSUMER.INFO.{stream['name']}.{consumer['name']}")
            config = info["config"]
            assert config["durable_name"] == consumer["name"]
            assert config["filter_subjects"] == consumer["filterSubjects"]
            assert config["ack_policy"] == "explicit"
            assert config["deliver_policy"] == "all"
            assert config["max_deliver"] == -1
            assert "deliver_subject" not in config
            if "maxAckPending" in consumer:
                assert config["max_ack_pending"] == consumer["maxAckPending"]
            assert config["num_replicas"] == 3
            assert on_three_servers(info["cluster"]), consumer["name"]


@pytest.mark.spec("NATS-4", "NATS-6")
def test_the_jetstream_job_succeeds_when_it_runs_again() -> None:
    before = {s["name"]: jetstream(f"STREAM.INFO.{s['name']}")["config"] for s in streams()}

    rerun_job("jetstream-setup")

    after = {s["name"]: jetstream(f"STREAM.INFO.{s['name']}")["config"] for s in streams()}
    assert after == before


# The Job's procedure run on a stream of the test's own, while messages are
# published to its subjects: each is refused, or kept for each consumer
# whose filter matches it (NATS-6).
PUBLISHING = r"""
set -u
mkdir -p /tmp/run
cp /test/* /tmp/run/
( i=0
  while [ ! -f /tmp/run/done ]; do
    i=$((i + 1))
    for subject in a b; do
      if nats --server "$NATS_URL" pub --jetstream "test.nats6.$subject" "m$i" >/dev/null 2>&1; then
        echo "$subject" >>/tmp/run/acked
      fi
    done
  done ) &
publisher=$!
sleep 1
CONFIG=/tmp/run sh /setup/setup.sh >/dev/null
sleep 1
touch /tmp/run/done
wait "$publisher"
echo "acked $(grep -c '^a$' /tmp/run/acked || true) $(grep -c '^b$' /tmp/run/acked || true)"
for consumer in nats6-a nats6-ab; do
  nats --server "$NATS_URL" request --raw "\$JS.API.CONSUMER.INFO.NATS6TEST.$consumer" '' |
    jq -r '"pending \(.name) \(.num_pending)"'
done
"""


@pytest.mark.spec("NATS-6")
def test_a_stream_by_interest_refuses_or_keeps_each_message_while_the_job_makes_it() -> None:
    name = f"nats-6-{uuid.uuid4().hex[:6]}"
    stream = {
        "name": "NATS6TEST",
        "subjects": ["test.nats6.a", "test.nats6.b"],
        "retention": "interest",
        "storage": "file",
        "num_replicas": 3,
    }

    def consumer(durable: str, subjects: list[str]) -> str:
        config = {
            "durable_name": durable,
            "name": durable,
            "filter_subjects": subjects,
            "ack_policy": "explicit",
            "deliver_policy": "all",
            "max_deliver": -1,
            "num_replicas": 3,
        }
        return json.dumps({"stream_name": "NATS6TEST", "config": config})

    apply(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "data": {
                "streams": "NATS6TEST\n",
                "stream-NATS6TEST.json": json.dumps(stream),
                "consumers-NATS6TEST": "nats6-a\nnats6-ab\n",
                "consumer-NATS6TEST-nats6-a.json": consumer("nats6-a", ["test.nats6.a"]),
                "consumer-NATS6TEST-nats6-ab.json": consumer(
                    "nats6-ab", ["test.nats6.a", "test.nats6.b"]
                ),
            },
        }
    )
    image = values()["jetstream"]["setupImage"]
    apply(
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {"name": name, "namespace": NAMESPACE},
            "spec": {
                "restartPolicy": "Never",
                "containers": [
                    {
                        "name": "publish",
                        "image": image,
                        "command": ["sh", "-c", PUBLISHING],
                        "env": [{"name": "NATS_URL", "value": NATS_URL}],
                        "volumeMounts": [
                            {"name": "setup", "mountPath": "/setup"},
                            {"name": "test", "mountPath": "/test"},
                        ],
                    }
                ],
                "volumes": [
                    {"name": "setup", "configMap": {"name": "jetstream-setup"}},
                    {"name": "test", "configMap": {"name": name}},
                ],
            },
        }
    )
    try:
        kubectl(
            "wait",
            "-n",
            NAMESPACE,
            f"pod/{name}",
            "--for=jsonpath={.status.phase}=Succeeded",
            "--timeout=300s",
        )
        report = kubectl("logs", "-n", NAMESPACE, name).split("\n")
        [acked] = [line.split()[1:] for line in report if line.startswith("acked ")]
        pending = {
            line.split()[1]: int(line.split()[2]) for line in report if line.startswith("pending ")
        }
        a, b = (int(count) for count in acked)

        assert a > 0
        assert b > 0
        assert pending == {"nats6-a": a, "nats6-ab": a + b}
    finally:
        jetstream("STREAM.DELETE.NATS6TEST")
        kubectl("delete", "pod", "-n", NAMESPACE, name, "--wait=false")
        kubectl("delete", "configmap", "-n", NAMESPACE, name)


# GARAGE


@pytest.mark.spec("GARAGE-3")
def test_garages_layout_holds_three_nodes_in_three_zones_each_with_every_object() -> None:
    layout = garage_admin("GetClusterLayout")
    roles = layout["roles"]
    partitions = {role["storedPartitions"] for role in roles}

    assert len(roles) == 3
    assert len({role["zone"] for role in roles}) == 3
    # Each node stores every partition, so a copy of every object: Garage
    # divides the objects into 256 partitions.
    assert partitions == {256}


@pytest.mark.spec("GARAGE-3")
def test_each_bucket_and_key_has_the_grants_the_values_give() -> None:
    for bucket, grants in values()["garageSetup"]["buckets"].items():
        info = garage_admin(f"GetBucketInfo?globalAlias={bucket}")
        given = {key["name"]: key["permissions"] for key in info["keys"]}
        assert given == {
            name: {**permissions, "owner": False} for name, permissions in grants.items()
        }, bucket


@pytest.mark.spec("GARAGE-3")
def test_the_garage_job_succeeds_when_it_runs_again() -> None:
    before = garage_admin("GetClusterLayout")["version"]

    rerun_job("garage-setup")

    assert garage_admin("GetClusterLayout")["version"] == before


# MON


@pytest.mark.spec("MON-6")
def test_grafanas_postgresql_data_source_reads_each_dashboards_view() -> None:
    for view in defined("dashboards.sql"):
        query = {
            "queries": [
                {
                    "refId": "A",
                    "datasource": {"uid": "record"},
                    "rawSql": f"SELECT * FROM {view} LIMIT 1",
                    "format": "table",
                }
            ],
            "from": "now-1h",
            "to": "now",
        }
        response = json.loads(
            in_nats_box(
                'curl -s -H "Content-Type: application/json" -d "$2" "$1"',
                "http://grafana.edgar.svc/api/ds/query",
                json.dumps(query),
            )
        )
        result = response["results"]["A"]
        assert result.get("status") == 200, (view, result.get("error"))
        assert "error" not in result, (view, result["error"])
