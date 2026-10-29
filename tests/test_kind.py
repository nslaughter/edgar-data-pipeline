"""spec/deployment.md's rules: how the installed charts behave on kind.

CI's kind job installs the four charts on kind, upgrades them, and runs these
tests (spec/deployment.md, section 3). They run only where
EDGAR_KUBE_CONTEXT names the kube context of a cluster the charts are
installed on, as the owner's can be, and are skipped elsewhere. Where
EDGAR_CHARTS_UPGRADED is true, as in CI, each release must also have been
upgraded since it was installed, which a cluster only installed hasn't; and
where EDGAR_FAKE_EDGAR is true, as in CI, the fake EDGAR must be installed,
which only CI installs (FAKE-4). They reach the cluster through kubectl and
Helm, and nothing else: each check inside it runs in a pod there. The one
exception is the transfer, which they build and run as the owner does,
outside the cluster, through the API server of the context they're given. A
credential they read from a Secret goes to its pod on standard input, never
in a command's arguments, so a failure never shows it. Each test carries
the IDs of the statements it checks, as @pytest.mark.spec(...).
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import verify
import yaml

from edgar_pipeline.fake_edgar.checks import check
from edgar_pipeline.fake_edgar.store import SEC_HOSTS, load

ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "spec" / "schemas"
CONTEXT = os.environ.get("EDGAR_KUBE_CONTEXT", "")
# Whether the releases were upgraded after they were installed, as CI's are.
UPGRADED = os.environ.get("EDGAR_CHARTS_UPGRADED", "") == "true"
# Whether the fake EDGAR is installed, as only CI installs it (FAKE-1, FAKE-4).
FAKE_EDGAR = os.environ.get("EDGAR_FAKE_EDGAR", "") == "true"
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
# The description of the umbrella release's upgrade with which a test
# acknowledges a stop, as the owner does (CHECK-3), which CHART-4's test
# leaves out of CI's rounds of installs and upgrades.
ACKNOWLEDGING = "The tests on kind acknowledge a stop (CHECK-3)"
# A third-party image, named by its tag and its digest (PIN-4, PIN-5).
PINNED_IMAGE = re.compile(r"^[^\s@]+:[^\s@/:]+@sha256:[0-9a-f]{64}$")
# One of the pipeline's own images, named by the commit it's built from (IMG-1).
OWN_IMAGE = re.compile(r"^edgar/[a-z0-9][a-z0-9-]*:[0-9a-f]{40}$")


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
    histories = [
        [
            revision
            for revision in helm_json("history", release, "-n", namespace)
            if revision["description"] != ACKNOWLEDGING
        ]
        for release, namespace in RELEASES
    ]

    for (release, _), history in zip(RELEASES, histories, strict=True):
        assert history[-1]["status"] == "deployed", release
        if UPGRADED:
            # Installed, then upgraded at least once, and nothing else.
            [installed, *upgraded] = [revision["description"] for revision in history]
            assert installed == "Install complete", release
            assert upgraded, release
            assert set(upgraded) == {"Upgrade complete"}, release
    if UPGRADED:
        # Each round installs or upgrades every release, so each revision of
        # one release is compared with the same revision of the others.
        assert len({len(history) for history in histories}) == 1
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
        if not PINNED_IMAGE.match(container["image"]) and not OWN_IMAGE.match(container["image"])
    ]

    assert unpinned == []


# IMG


def checked_out() -> str:
    """The commit checked out, from which CI builds the images and installs the charts."""
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def release_value(*keys: str) -> Any:
    """One value the umbrella chart's release was given, and nothing else of
    them, since they hold its credentials, which a failure would show."""
    found = helm_json("get", "values", "edgar", "-n", NAMESPACE)
    for key in keys:
        found = found.get(key) if isinstance(found, dict) else None
    return found


def own_containers() -> list[tuple[str, dict[str, Any]]]:
    """Each container of the charts' pods that runs one of the pipeline's images."""
    namespaces = sorted({namespace for _, namespace in RELEASES})
    return [
        (pod["metadata"]["name"], container)
        for namespace in namespaces
        for pod in kubectl_json("get", "pods", "-n", namespace)["items"]
        for container in [*pod["spec"].get("initContainers", []), *pod["spec"]["containers"]]
        if OWN_IMAGE.match(container["image"])
    ]


@pytest.mark.spec("IMG-3")
def test_each_of_the_pipelines_images_runs_as_built_from_the_commit_installed() -> None:
    commit = checked_out()
    found = own_containers()

    assert release_value("images", "commit") == commit
    if FAKE_EDGAR:
        assert found
    for pod, container in found:
        assert container["image"].endswith(f":{commit}"), pod
        # Never pulled, so only the image built and loaded can run (IMG-1, IMG-2).
        assert container["imagePullPolicy"] == "Never", pod


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


@pytest.mark.spec("PG-9", "LOG-3")
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


@pytest.mark.spec("PG-9", "LOG-3")
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


@pytest.mark.spec("PG-9", "LOG-3")
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


# SCHED


SCHEDULER = "app.kubernetes.io/name=scheduler,app.kubernetes.io/instance=edgar"


def scheduler_lease() -> dict[str, Any]:
    lease = kubectl_json("get", "lease", "-n", NAMESPACE, "scheduler")
    assert isinstance(lease, dict)
    return lease


def holder(lease: dict[str, Any]) -> str:
    """The pod that holds the Lease: its identity is the pod's name, an
    underscore, and an ID of its own."""
    identity = lease["spec"].get("holderIdentity") or ""
    return str(identity).split("_", 1)[0]


def lease_time(value: str) -> datetime:
    """A Lease's time, RFC 3339 with microseconds."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def running(selector: str) -> list[dict[str, Any]]:
    return [
        pod
        for pod in pods(selector)
        if pod["status"].get("phase") == "Running" and "deletionTimestamp" not in pod["metadata"]
    ]


@pytest.mark.spec("SCHED-5")
def test_the_schedulers_pods_run_on_two_worker_nodes_and_one_holds_the_lease() -> None:
    workers = {
        node["metadata"]["name"]
        for node in kubectl_json("get", "nodes")["items"]
        if "node-role.kubernetes.io/control-plane" not in node["metadata"]["labels"]
    }
    scheduler = running(SCHEDULER)
    nodes = [pod["spec"]["nodeName"] for pod in scheduler]
    lease = scheduler_lease()

    assert len(scheduler) == 2
    assert len(set(nodes)) == 2
    assert set(nodes) <= workers
    assert holder(lease) in {pod["metadata"]["name"] for pod in scheduler}
    assert (
        lease["spec"]["leaseDurationSeconds"] == values()["scheduler"]["lease"]["durationSeconds"]
    )


@pytest.mark.spec("SCHED-5")
def test_another_pod_holds_the_lease_before_its_duration_when_its_holder_is_deleted() -> None:
    lease = scheduler_lease()
    deleted = holder(lease)
    duration = lease["spec"]["leaseDurationSeconds"]
    renewed = lease_time(lease["spec"]["renewTime"])
    assert deleted

    kubectl("delete", "pod", "-n", NAMESPACE, deleted, "--wait=false")
    try:

        def taken_over() -> dict[str, Any] | None:
            now = scheduler_lease()
            return now if holder(now) not in ("", deleted) else None

        taken = wait_until(taken_over, 90, "another scheduler pod to hold the Lease")
        # The holder renewed it at least as late as renewed, so another pod
        # took it over before its duration passed since its last renewal.
        acquired = lease_time(taken["spec"]["acquireTime"])
        assert acquired < renewed + timedelta(seconds=duration)
    finally:
        kubectl("rollout", "status", "-n", NAMESPACE, "deployment/scheduler", "--timeout=180s")


@pytest.mark.spec("SOURCE-1", "SOURCE-3")
def test_the_scheduler_sets_the_conditions_of_the_charts_sources() -> None:
    # CI allows requests, as the owner decided, where the chart's default
    # values pause the SecAccess (VAL-4).
    paused = release_value("controls", "secAccess", "paused")
    polling = ("False", "RequestsPaused") if paused is not False else ("True", "Polling")
    expected = {
        name: {"Polling": polling, "WithinBudget": ("True", "WithinBudget")}
        for name in values()["controls"]["sources"]
    }

    def set_as_expected() -> bool:
        found = {
            source["metadata"]["name"]: {
                condition["type"]: (condition["status"], condition["reason"])
                for condition in source.get("status", {}).get("conditions", [])
            }
            for source in kubectl_json("get", "sources.edgar.nathanslaughter.com", "-n", NAMESPACE)[
                "items"
            ]
        }
        return found == expected

    wait_until(set_as_expected, 60, "the Sources' conditions")


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


# FAKE


fake_edgar_installed = pytest.mark.skipif(
    not FAKE_EDGAR, reason="only CI installs the fake EDGAR: EDGAR_FAKE_EDGAR isn't true"
)


def service_address() -> str:
    address = kubectl_json("get", "service", "-n", NAMESPACE, "fake-edgar")["spec"]["clusterIP"]
    assert isinstance(address, str)
    return address


@pytest.mark.spec("FAKE-4")
@fake_edgar_installed
def test_ci_installs_the_fake_edgar() -> None:
    assert release_value("fakeEdgar", "enabled") is True


@pytest.mark.spec("FAKE-4")
@fake_edgar_installed
@pytest.mark.parametrize("host", ["www.sec.gov", "data.sec.gov"])
def test_a_pods_query_for_an_sec_host_gets_the_fake_edgars_service(host: str) -> None:
    answer = in_nats_box('nslookup -type=a "$1"', host)
    # The addresses after the name asked for, not the DNS server's own.
    addresses = re.findall(r"Address:\s*([0-9.]+)\s*$", answer.split(host, 1)[1], re.M)

    assert addresses == [service_address()]


@pytest.mark.spec("FAKE-4")
@fake_edgar_installed
def test_the_clusters_dns_pointed_at_the_fake_edgar_before_the_umbrella_chart() -> None:
    # When each writer last changed it is in its managed fields, which
    # kubectl shows only when asked.
    config = kubectl_json(
        "get", "configmap", "-n", "kube-system", "coredns", "--show-managed-fields"
    )
    [installed, *_] = helm_json("history", "edgar", "-n", NAMESPACE)
    changed = max(helm_time(field["time"]) for field in config["metadata"]["managedFields"])

    assert "fake-edgar.edgar.svc.cluster.local" in config["data"]["Corefile"]
    assert changed <= helm_time(installed["updated"])


@pytest.mark.spec("FAKE-5")
@fake_edgar_installed
def test_the_fake_edgars_pod_is_ready() -> None:
    [pod] = pods("app.kubernetes.io/name=fake-edgar")
    ready = {c["type"]: c["status"] for c in pod["status"]["conditions"]}["Ready"]

    assert ready == "True"


def stored_urls() -> list[str]:
    """Of data/ at the commit checked out, the first stored URL on each SEC host."""
    stored = sorted(load(ROOT / "data"))
    return [next(url for url in stored if url.startswith(f"https://{host}/")) for host in SEC_HOSTS]


@pytest.mark.spec("FAKE-5", "SERVE-5")
@fake_edgar_installed
@pytest.mark.parametrize("url", stored_urls() if FAKE_EDGAR else [])
def test_a_pods_get_of_a_stored_url_reaches_the_fake_edgar_over_https(url: str) -> None:
    authority = base64.b64decode(
        kubectl_json("get", "secret", "-n", NAMESPACE, "fake-edgar-ca")["data"]["ca.crt"]
    ).decode()
    before = len(json.loads(in_nats_box('curl -sf "$1"', "http://fake-edgar:8080/requests")))

    # Checked against the release's authority alone, for the URL's host.
    digest = in_nats_box(
        'dir=$(mktemp -d); cat >"$dir/ca.crt"; '
        'curl -sSf --cacert "$dir/ca.crt" "$1" | sha256sum | cut -d" " -f1',
        url,
        input=authority,
    ).strip()
    entries = json.loads(
        in_nats_box('curl -sf "$1"', f"http://fake-edgar:8080/requests?after={before}")
    )

    assert digest == load(ROOT / "data")[url].latest
    [entry] = [e for e in entries if e["url"] == url]
    assert (entry["status"], entry["cued"]) == (200, False)
    assert entry["headers"]["host"] == url.split("/")[2]


# FETCHER: the fetcher's rules, and the cluster store it writes. Each test
# sends the fetcher's requests to the fake EDGAR, whose authority alone it
# trusts there, so each needs it (FETCHER-12, FETCHER-13). CI installs the
# umbrella chart with requests allowed, as the owner decided, so the
# Sources' polls run through each of them.


FETCHER = "app.kubernetes.io/name=fetcher,app.kubernetes.io/instance=edgar"
# The stored URLs the tests ask the fetcher for, each by one test, none of
# them a Source's: each filing's index, which EDGAR gave no validators; and
# each filing's index page, for a test that needs one more.
INDEXES = sorted(url for url in load(ROOT / "data") if url.endswith("/index.json"))
PAGES = sorted(url for url in load(ROOT / "data") if url.endswith("-index.html"))
TIMESTAMP_SQL = """to_char({} AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"')"""


def stamp(at: datetime) -> str:
    """A time in the store's form (spec/snapshots.md, section 2)."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + f"{at.microsecond // 1000:03d}Z"


def new_job(kind: str, url: str, **fields: Any) -> dict[str, Any]:
    """A job of the kind for a URL, queued now, of a Source or a Backfill
    the chart doesn't render, so no control's status changes."""
    now = datetime.now(UTC)
    job: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "kind": kind,
        "feed": "kind-tests",
        "url": url,
        "queued_at": stamp(now),
    }
    if kind == "poll":
        job |= {"source": "kind-tests", "expires_at": stamp(now + timedelta(hours=1))}
    if kind == "backfill":
        job["backfill"] = "kind-tests"
    return job | fields


def queue(subject: str, body: str) -> int:
    """Publishes a message to JOBS, and gives its sequence in the stream."""
    ack = json.loads(
        in_nats_box('nats --server "$1" request --raw "$2" "$3"', NATS_URL, subject, body)
    )
    assert ack.get("stream") == "JOBS", ack
    return int(ack["seq"])


def queue_job(job: dict[str, Any]) -> int:
    return queue(f"edgar.jobs.{job['kind']}", json.dumps(job))


def in_jobs(seq: int) -> bool:
    """Whether JOBS still holds a job: a work queue drops each job once it's
    acknowledged."""
    return "message" in jetstream("STREAM.MSG.GET.JOBS", json.dumps({"seq": seq}))


def acknowledged(*seqs: int) -> None:
    wait_until(lambda: not any(in_jobs(seq) for seq in seqs), 120, "the jobs' acknowledgement")


def fake_edgar(path: str, method: str = "GET", body: str = "") -> Any:
    """A request to the fake EDGAR's admin port (ADMIN-1)."""
    response = in_nats_box(
        'curl -sf -X "$2" -H "Content-Type: application/json" --data-binary "$3" "$1"',
        f"http://fake-edgar:8080{path}",
        method,
        body,
    )
    return json.loads(response) if response.strip() else None


def own_address() -> str:
    """The address of the pod the tests' own requests come from."""
    return in_nats_box("hostname -i").split()[0]


def fetchers_requests(after: int = 0) -> list[dict[str, Any]]:
    """The entries of the fake EDGAR's request log after the one given, but
    the tests' own: so the fetcher's, which alone sends requests to the SEC's
    hosts (FETCH-18)."""
    tests = own_address()
    entries = fake_edgar(f"/requests?after={after}")
    assert isinstance(entries, list)
    return [entry for entry in entries if entry["client"] != tests]


def requested(url: str, after: int = 0) -> list[dict[str, Any]]:
    return [entry for entry in fetchers_requests(after) if entry["url"] == url]


def last_entry() -> int:
    entries = fake_edgar("/requests")
    return int(entries[-1]["n"]) if entries else 0


def records(where: str = "true") -> list[dict[str, Any]]:
    """The records the table holds, in the order they were added, each as the
    store gives a record, with its id and its job."""
    fields = ", ".join(
        f"'{field}', " + (TIMESTAMP_SQL.format(field) if field.endswith("_at") else field)
        for field in (
            "id",
            "job",
            "feed",
            "url",
            "requested_at",
            "received_at",
            "attempts",
            "status",
            "error",
            "final_url",
            "date",
            "etag",
            "last_modified",
            "content_type",
            "sha256",
            "bytes",
            "object",
            "tool",
        )
    )
    result = psql(
        "discoverer", f"SELECT json_build_object({fields}) FROM records WHERE {where} ORDER BY id"
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def record_of(job: dict[str, Any]) -> dict[str, Any] | None:
    found = records(f"job = '{job['id']}'")
    return found[0] if found else None


def secaccess() -> dict[str, Any]:
    found = kubectl_json("get", "secaccesses.edgar.nathanslaughter.com", "cluster")
    assert isinstance(found, dict)
    return found


def allowed() -> None:
    """Requests are allowed, as CI's values have them."""
    access = secaccess()
    stop = access.get("status", {}).get("stop")
    assert not access["spec"].get("paused", False), access["spec"]
    assert stop is None or stop["at"] == access["spec"].get("acknowledgedStopAt"), access


def acknowledge(at: str) -> None:
    """Acknowledges a stop, as the owner does: in the umbrella chart's
    values, with helm upgrade (ACCESS-3, RBAC-6)."""
    subprocess.run(
        [
            "helm",
            "--kube-context",
            CONTEXT,
            "upgrade",
            "edgar",
            str(ROOT / "deploy" / "chart"),
            "--namespace",
            NAMESPACE,
            "--reset-then-reuse-values",
            "--set",
            f"controls.secAccess.acknowledgedStopAt={at}",
            "--description",
            ACKNOWLEDGING,
            "--wait",
            "--timeout",
            "15m",
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def s3(
    key: str, method: str, path: str, body: str = "", bucket: str = "snapshots"
) -> tuple[int, dict[str, str], bytes]:
    """A request to Garage's S3 API for a bucket, the store's unless another
    is named, signed with a key's credentials, given on standard input, and
    its status, headers, and body."""
    credentials = "\n".join(
        secret(f"garage-key-{key}", name) for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
    )
    response = in_nats_box(
        "read -r id; read -r secret; head=$(mktemp); out=$(mktemp); "
        "method=$1; region=$2; url=$3; body=$4; shift 4; "
        'set -- --aws-sigv4 "aws:amz:$region:s3" --user "$id:$secret" -s -o "$out" -D "$head" '
        '-w "%{http_code}"; '
        'if [ "$method" = HEAD ]; then code=$(curl "$@" --head "$url"); '
        'elif [ -n "$body" ]; then code=$(curl "$@" -X "$method" --data-binary "$body" "$url"); '
        'else code=$(curl "$@" -X "$method" "$url"); fi; '
        'echo "$code"; base64 "$head" | tr -d "\\n"; echo; base64 "$out" | tr -d "\\n"; echo',
        method,
        values()["garage"]["garage"]["s3"]["api"]["region"],
        f"http://garage:3900/{bucket}{path}",
        body,
        input=credentials + "\n",
    )
    code, head, data = [*response.split("\n"), "", ""][:3]
    headers = {}
    for line in base64.b64decode(head).decode().splitlines()[1:]:
        name, _, value = line.partition(":")
        if value:
            headers[name.strip().lower()] = value.strip()
    return int(code), headers, base64.b64decode(data)


def bucket() -> list[str]:
    """The names of the objects in the store's bucket, read with the
    discoverer's key, which may only read it."""
    status, _, listing = s3("discoverer", "GET", "?list-type=2")
    assert status == 200, listing
    found = re.findall(rb"<Key>([^<]+)</Key>", listing)
    assert b"<IsTruncated>true</IsTruncated>" not in listing
    return [name.decode() for name in found]


fetcher_on_kind = pytest.mark.skipif(
    not FAKE_EDGAR,
    reason="the fetcher's requests reach the fake EDGAR only where CI installs it: "
    "EDGAR_FAKE_EDGAR isn't true",
)


@pytest.mark.spec("LEASE-2", "FETCHER-3")
@fetcher_on_kind
def test_one_of_the_fetchers_pods_on_two_worker_nodes_holds_its_lease() -> None:
    workers = {
        node["metadata"]["name"]
        for node in kubectl_json("get", "nodes")["items"]
        if "node-role.kubernetes.io/control-plane" not in node["metadata"]["labels"]
    }
    fetchers = running(FETCHER)
    nodes = [pod["spec"]["nodeName"] for pod in fetchers]
    lease = kubectl_json("get", "lease", "-n", NAMESPACE, "fetcher")

    assert len(fetchers) == 2
    assert len(set(nodes)) == 2
    assert set(nodes) <= workers
    assert holder(lease) in {pod["metadata"]["name"] for pod in fetchers}
    assert lease["spec"]["leaseDurationSeconds"] == values()["fetcher"]["lease"]["durationSeconds"]


@pytest.mark.spec("FETCH-17")
@fetcher_on_kind
def test_the_fetchers_requests_carry_the_user_agent_from_its_secret() -> None:
    allowed()
    agent = secret("fetcher-user-agent", "SEC_USER_AGENT")
    for pod in running(FETCHER):
        [container] = pod["spec"]["containers"]
        env = {variable["name"]: variable for variable in container["env"]}
        assert env["SEC_USER_AGENT"]["valueFrom"]["secretKeyRef"] == {
            "name": "fetcher-user-agent",
            "key": "SEC_USER_AGENT",
        }
    # The Sources' polls, which CI allows.
    entries = wait_until(fetchers_requests, 120, "the fetcher's requests")

    assert "@" in agent
    assert {entry["headers"].get("user-agent") for entry in entries} == {agent}


@pytest.mark.spec("JOB-5")
@fetcher_on_kind
def test_a_job_that_has_expired_is_acknowledged_without_a_request_or_a_record() -> None:
    allowed()
    url = INDEXES[0]
    before = last_entry()
    job = new_job("poll", url, expires_at=stamp(datetime.now(UTC) - timedelta(minutes=1)))

    acknowledged(queue_job(job))
    time.sleep(5)

    assert record_of(job) is None
    assert requested(url, before) == []


@pytest.mark.spec("JOB-6")
@fetcher_on_kind
def test_a_job_whose_record_is_held_is_announced_again_without_a_request() -> None:
    allowed()
    url = INDEXES[1]
    before = last_entry()
    name = f"kind-job-6-{uuid.uuid4().hex[:6]}"
    consumer = {
        "durable_name": name,
        "name": name,
        "filter_subjects": ["edgar.records.filing"],
        "ack_policy": "explicit",
        "deliver_policy": "new",
    }
    jetstream(
        f"CONSUMER.CREATE.RECORDS.{name}",
        json.dumps({"stream_name": "RECORDS", "config": consumer}),
    )
    try:
        job = new_job("filing", url)
        acknowledged(queue_job(job))
        record = record_of(job)
        assert record is not None
        # The same job, as JetStream delivers it again.
        acknowledged(queue_job(job))

        events = []
        for _ in range(10):
            response = in_nats_box(
                'nats --server "$1" request --raw "$2" "$3"',
                NATS_URL,
                f"$JS.API.CONSUMER.MSG.NEXT.RECORDS.{name}",
                json.dumps({"batch": 1, "no_wait": True}),
                check=False,
            )
            if not response.strip().startswith("{"):
                break
            event = json.loads(response)
            if event["job"]["id"] == job["id"]:
                events.append(event)
        assert [event["id"] for event in events] == [record["id"], record["id"]]
        for event in events:
            assert event["job"] == job
            assert event["record"]["url"] == url
        assert len(requested(url, before)) == 1
        assert len(records(f"url = '{url}' AND job = '{job['id']}'")) == 1
    finally:
        jetstream(f"CONSUMER.DELETE.RECORDS.{name}")


@pytest.mark.spec("JOB-7")
@fetcher_on_kind
def test_a_job_that_doesnt_validate_or_whose_kind_isnt_its_subjects_is_acknowledged_alone() -> None:
    allowed()
    url = INDEXES[2]
    before = last_entry()
    mismatched = new_job("filing", url)
    no_feed = new_job("filing", url, feed="")

    acknowledged(
        queue("edgar.jobs.filing", "not json"),
        queue("edgar.jobs.poll", json.dumps(mismatched)),
        queue_job(no_feed),
    )
    time.sleep(5)

    assert record_of(mismatched) is None
    assert record_of(no_feed) is None
    assert requested(url, before) == []


@pytest.mark.spec("OBJ-4")
@fetcher_on_kind
def test_a_body_is_stored_once_under_its_name_and_never_written_again() -> None:
    allowed()
    url = INDEXES[3]
    first = new_job("filing", url)
    acknowledged(queue_job(first))
    stored = record_of(first)
    assert stored is not None
    assert stored["status"] == 200
    status, headers, data = s3("discoverer", "GET", f"/{stored['object']}")
    assert status == 200
    assert hashlib.sha256(gzip.decompress(data)).hexdigest() == stored["sha256"]

    # The same body again, a second or more later, which Garage would give a
    # new Last-Modified were the object written again.
    time.sleep(2)
    again = new_job("filing", url)
    acknowledged(queue_job(again))
    second = record_of(again)
    assert second is not None
    _, after, _ = s3("discoverer", "HEAD", f"/{stored['object']}")

    assert (second["status"], second["object"]) == (200, stored["object"])
    assert after["last-modified"] == headers["last-modified"]
    assert bucket().count(stored["object"]) == 1


@pytest.mark.spec("CHECK-3", "ACCESS-1", "ACCESS-3", "JOB-4", "BACKFILL-2")
@fetcher_on_kind
def test_the_fetchers_requests_pass_the_checks_with_a_stop_after_a_403_until_acknowledged() -> None:
    allowed()
    agent = secret("fetcher-user-agent", "SEC_USER_AGENT")
    forbidden, backfill, filing, poll = INDEXES[4:8]
    fake_edgar("/cues", "POST", json.dumps({"url": forbidden, "responses": [{"status": 403}]}))
    job = new_job("filing", forbidden)
    queue_job(job)
    try:
        # The stop, recorded in the SecAccess, of the 403's record (FETCH-9).
        def stopped() -> dict[str, Any] | None:
            stop = secaccess().get("status", {}).get("stop")
            return stop if stop and stop["url"] == forbidden else None

        stop = wait_until(stopped, 120, "the stop after the 403")
        record = wait_until(lambda: record_of(job), 60, "the 403's record")
        assert record["status"] == 403
        assert record["requested_at"] == stop["at"]

        # While it holds, the jobs queued wait, and nothing is sent.
        queued = [
            queue_job(new_job("backfill", backfill)),
            queue_job(new_job("filing", filing)),
            queue_job(new_job("poll", poll)),
        ]
        time.sleep(20)
        entries = fetchers_requests()
        [at] = [e["n"] for e in entries if e["url"] == forbidden and e["status"] == 403]
        assert [e for e in entries if e["n"] > at] == []
        assert all(in_jobs(seq) for seq in queued)

        acknowledge(stop["at"])
        acknowledged(*queued)
    finally:
        access = secaccess()
        stop = access.get("status", {}).get("stop")
        if stop and stop["at"] != access["spec"].get("acknowledgedStopAt"):
            acknowledge(stop["at"])

    entries = fetchers_requests()
    up_to = [e for e in entries if e["n"] <= at]
    after = [e for e in entries if e["n"] > at]
    first = {url: min(e["n"] for e in after if e["url"] == url) for url in (poll, filing, backfill)}

    # The poll's job first, then the filing's, then the Backfill's (JOB-4).
    assert first[poll] < first[filing] < first[backfill]
    # Up to the 403, and from the acknowledgement on, they pass the checks;
    # nothing came between (CHECK-1).
    assert check(up_to, agent) == []
    assert check(after, agent) == []
    assert up_to[-1]["status"] == 403


@pytest.mark.spec("JOB-4", "BACKFILL-2")
@fetcher_on_kind
def test_a_poll_queued_while_a_backfills_fetch_waits_to_retry_goes_before_the_retry() -> None:
    allowed()
    backfill, poll = INDEXES[8], PAGES[0]
    before = last_entry()
    # The Backfill's first attempt gets a 503, so its fetch waits 20 seconds
    # before its retry (FETCH-8).
    unavailable = {"status": 503, "headers": {"Retry-After": "20"}}
    fake_edgar("/cues", "POST", json.dumps({"url": backfill, "responses": [unavailable]}))
    backfill_job = new_job("backfill", backfill)
    poll_job = new_job("poll", poll)
    queued = [queue_job(backfill_job)]
    wait_until(lambda: requested(backfill, before), 120, "the Backfill's first request")
    queued.append(queue_job(poll_job))
    acknowledged(*queued)

    attempts = requested(backfill, before)
    [polled] = requested(poll, before)
    # The poll's request came between the Backfill's attempts (JOB-4).
    assert [entry["status"] for entry in attempts] == [503, 200]
    assert attempts[0]["n"] < polled["n"] < attempts[1]["n"]
    record = record_of(backfill_job)
    assert record is not None
    assert (record["attempts"], record["status"]) == (2, 200)
    assert record_of(poll_job) is not None


@pytest.mark.spec("STORE-2")
@fetcher_on_kind
def test_the_database_refuses_a_record_that_isnt_well_formed() -> None:
    good = {
        "job": "gen_random_uuid()",
        "feed": "'kind-tests'",
        "url": "'https://www.sec.gov/kind-tests'",
        "requested_at": "now()",
        "received_at": "now()",
        "attempts": "1",
        "status": "404",
        "error": "NULL",
        "date": "NULL",
        "sha256": "NULL",
        "bytes": "NULL",
        "object": "NULL",
        "tool": "'fetcher@' || repeat('0', 40)",
    }
    digest = "'" + "ab" * 32 + "'"
    named = f"'objects/ab/{'ab' * 32}.gz'"
    stored = {"status": "200", "sha256": digest, "bytes": "5", "object": named}

    def insert(**fields: str) -> str:
        given = good | fields
        return f"INSERT INTO records ({', '.join(given)}) VALUES ({', '.join(given.values())})"

    assert attempt("fetcher", insert()).returncode == 0
    assert attempt("fetcher", insert(**stored)).returncode == 0
    for malformed in [
        {"feed": "'Not A Feed'"},
        {"tool": "'scripts/snapshot.py@' || repeat('0', 40)"},
        {"attempts": "0"},
        {"sha256": "'abc'", "bytes": "5", "object": "'objects/ab/abc.gz'", "status": "200"},
        {"bytes": "-1", "sha256": digest, "object": named, "status": "200"},
        # REC-3
        {"sha256": digest, "status": "200"},
        stored | {"object": f"'objects/cd/{'ab' * 32}.gz'"},
        # REC-4
        {"status": "200"},
        # REC-5
        {"status": "304"},
        # REC-6
        stored | {"status": "404"},
        stored | {"error": "'cut short'"},
        # REC-7
        {"status": "NULL", "received_at": "NULL"},
        # REC-11
        {"status": "NULL", "error": "'no response'"},
        {"status": "NULL", "received_at": "NULL", "error": "'no response'", "date": "'Mon'"},
    ]:
        result = attempt("fetcher", insert(**malformed))
        assert result.returncode != 0, malformed
        assert "violates" in result.stderr, (malformed, result.stderr)
    twice = attempt(
        "fetcher",
        insert(job="'00000000-0000-4000-8000-000000000000'")
        + "; "
        + insert(job="'00000000-0000-4000-8000-000000000000'"),
    )
    assert twice.returncode != 0
    assert "records_job_key" in twice.stderr


@pytest.mark.spec("STORE-3")
@fetcher_on_kind
def test_the_cluster_stores_objects_and_records_meet_the_git_stores_invariants() -> None:
    held_objects = bucket()
    decompressed = {}
    for name in held_objects:
        status, _, data = s3("discoverer", "GET", f"/{name}")
        assert status == 200, name
        body = gzip.decompress(data)
        # OBJ-1
        assert name == verify.object_path(hashlib.sha256(body).hexdigest()), name
        decompressed[name] = len(body)
    schema = json.loads((SCHEMAS / "record.schema.json").read_text(encoding="utf-8"))
    schema["required"] = [*schema["required"], "received_at", "date"]
    found = records()
    latest: dict[str, str] = {}

    assert found
    for row in found:
        record = {field: value for field, value in row.items() if field not in ("id", "job")}
        assert verify.schema_errors(record, schema) == [], row
        assert verify.outcome_problems(record, latest.get(record["url"])) == [], row
        if record["status"] == 304 and record["sha256"]:
            # REC-5, by the order of the records' ids.
            assert record["sha256"] == latest[record["url"]], row
        if record["sha256"]:
            # REC-3 and REC-8
            assert record["object"] in decompressed, row
            assert record["bytes"] == decompressed[record["object"]], row
            latest[record["url"]] = record["sha256"]


@pytest.mark.spec("LOG-4")
@fetcher_on_kind
def test_only_the_fetchers_key_may_write_to_the_bucket() -> None:
    [name, *_] = bucket()
    status, _, data = s3("discoverer", "GET", f"/{name}")
    assert status == 200

    written, _, _ = s3("discoverer", "PUT", "/objects/kind-tests", "written by the tests on kind")
    deleted, _, _ = s3("discoverer", "DELETE", f"/{name}")

    assert written == 403
    assert deleted == 403
    assert s3("discoverer", "GET", f"/{name}")[2] == data
    assert "objects/kind-tests" not in bucket()


@pytest.mark.spec("FETCHER-3", "FETCH-16")
@fetcher_on_kind
def test_another_pod_holds_the_lease_once_its_duration_passes_after_its_holder_is_deleted() -> None:
    allowed()
    lease = kubectl_json("get", "lease", "-n", NAMESPACE, "fetcher")
    deleted = holder(lease)
    duration = timedelta(seconds=lease["spec"]["leaseDurationSeconds"])
    [address] = [p["status"]["podIP"] for p in running(FETCHER) if p["metadata"]["name"] == deleted]
    before = last_entry()

    kubectl("delete", "pod", "-n", NAMESPACE, deleted, "--wait=false")
    renewed = lease_time(
        kubectl_json("get", "lease", "-n", NAMESPACE, "fetcher")["spec"]["renewTime"]
    )
    try:

        def taken_over() -> dict[str, Any] | None:
            now = kubectl_json("get", "lease", "-n", NAMESPACE, "fetcher")
            return now if holder(now) not in ("", deleted) else None

        taken = wait_until(taken_over, 120, "another fetcher pod to hold the Lease")
        acquired = lease_time(taken["spec"]["acquireTime"])
        # The deleted pod didn't release the Lease, which the other took over
        # only once its duration had passed since the last renewal.
        assert acquired >= renewed + duration

        [taker] = [p for p in running(FETCHER) if p["metadata"]["name"] == holder(taken)]
        wait_until(
            lambda: [
                e for e in fetchers_requests(before) if e["client"] == taker["status"]["podIP"]
            ],
            120,
            "a request from the pod that took the Lease over",
        )
        for entry in fetchers_requests(before):
            received = datetime.fromisoformat(entry["received_at"])
            if entry["client"] == address:
                assert received < acquired, entry["n"]
            else:
                assert entry["client"] == taker["status"]["podIP"], entry["n"]
                assert received > acquired, entry["n"]
    finally:
        kubectl("rollout", "status", "-n", NAMESPACE, "deployment/fetcher", "--timeout=180s")


# TRANSFER: the transfer's role and key, and the transfer itself, which the
# owner runs outside the cluster, where data/ is, and which these tests run
# as the owner does, through the API server of the context they're given
# (spec/deployment.md, section 5, the transfer).


@pytest.mark.spec("PG-15", "LOG-5")
def test_the_transfers_role_logs_in_without_special_attributes_and_only_reads_records() -> None:
    attributes = psql(
        "transfer",
        "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls"
        " FROM pg_roles WHERE rolname = current_user",
    )
    insert = (
        "INSERT INTO records (job, feed, url, requested_at, attempts, error, tool)"
        " VALUES (gen_random_uuid(), 'submissions', 'https://data.sec.gov/kind-test', now(), 1,"
        " 'test', 'fetcher@' || repeat('0', 40))"
    )

    assert attributes.returncode == 0, attributes.stderr
    assert attributes.stdout.strip() == "t|f|f|f|f|f"
    assert psql("transfer", "SELECT count(*) FROM records").returncode == 0
    for statement in (
        insert,
        "UPDATE records SET attempts = attempts",
        "DELETE FROM records",
        "TRUNCATE records",
    ):
        result = attempt("transfer", statement)
        assert result.returncode != 0, statement
        assert "permission denied" in result.stderr, (statement, result.stderr)


@pytest.mark.spec("PG-15")
def test_the_transfers_role_reads_neither_the_notices_nor_the_dashboards() -> None:
    for relation in notices():
        for granted in ["SELECT", *WRITES]:
            assert not privilege("transfer", relation, granted), (relation, granted)
    for view in defined("dashboards.sql"):
        read = psql("transfer", f"SELECT count(*) FROM {view}")
        assert read.returncode != 0, view
        assert "permission denied" in read.stderr, (view, read.stderr)


@pytest.mark.spec("GARAGE-5", "LOG-5")
@fetcher_on_kind
def test_the_transfers_key_may_only_read_the_stores_bucket() -> None:
    [name, *_] = bucket()

    listed, _, _ = s3("transfer", "GET", "?list-type=2")
    status, _, data = s3("transfer", "GET", f"/{name}")
    written, _, _ = s3("transfer", "PUT", "/objects/kind-tests-transfer", "written by the tests")
    deleted, _, _ = s3("transfer", "DELETE", f"/{name}")
    elsewhere, _, _ = s3("transfer", "GET", "?list-type=2", bucket="database-backups")

    assert (listed, status) == (200, 200)
    assert (written, deleted, elsewhere) == (403, 403, 403)
    assert s3("discoverer", "GET", f"/{name}")[2] == data
    assert "objects/kind-tests-transfer" not in bucket()


@pytest.fixture(scope="module")
def transfer_command(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The transfer, built from the commit checked out, as the owner runs it."""
    go = shutil.which("go")
    assert go, "the tests of the transfer build it with Go, which isn't installed"
    command = tmp_path_factory.mktemp("transfer") / "transfer"
    subprocess.run([go, "build", "-o", str(command), "./cmd/transfer"], cwd=ROOT, check=True)
    return command


@pytest.mark.spec("TRANSFER-4", "TRANSFER-5", "TRANSFER-6", "XFER-1", "LOG-5")
@fetcher_on_kind
def test_the_transfer_reads_the_range_through_the_api_server_and_copies_nothing_with_the_fake_edgar(
    transfer_command: Path, tmp_path: Path
) -> None:
    found = records()
    assert len(found) >= 2
    # From the first record up to one in the middle, whose own isn't in the
    # range: a record the fetcher adds meanwhile comes after it.
    start, end = found[0]["requested_at"], found[len(found) // 2]["requested_at"]
    in_range = [r for r in found if start <= r["requested_at"] < end]
    scope, problems = verify.read_scope(ROOT / "data")
    assert scope is not None
    assert problems == []
    in_scope = [r for r in in_range if verify.covers(scope, r["url"])]
    objects = {r["object"] for r in in_scope if r["object"]}
    data = tmp_path / "data"
    data.mkdir()
    shutil.copy(ROOT / "data" / "scope.json", data / "scope.json")
    # Credentials in the environment and in the files libpq and S3 clients
    # read, none of them the transfer's, which it must not take (TRANSFER-4).
    decoys = tmp_path / "decoys"
    decoys.mkdir()
    (decoys / "pgpass").write_text("*:*:*:*:not-the-transfers\n")
    (decoys / "pgpass").chmod(0o600)
    (decoys / "aws").write_text(
        "[default]\naws_access_key_id = GKnotthetransfers\naws_secret_access_key = not\n"
    )
    environment = {
        **os.environ,
        "PGUSER": "fetcher",
        "PGPASSWORD": "not-the-transfers",
        "PGPASSFILE": str(decoys / "pgpass"),
        "PGSSLMODE": "disable",
        "PGCHANNELBINDING": "disable",
        "AWS_ACCESS_KEY_ID": "GKnotthetransfers",
        "AWS_SECRET_ACCESS_KEY": "not-the-transfers",
        "AWS_SHARED_CREDENTIALS_FILE": str(decoys / "aws"),
        "AWS_REGION": "us-east-1",
    }

    result = subprocess.run(
        [
            str(transfer_command),
            *("--context", CONTEXT, "--data", str(data), "--from", start, "--until", end),
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=300,
        check=False,
    )

    # It read the range through its port-forwards, as its role and with its
    # key, the objects of its records among it (TRANSFER-5, LOG-5, XFER-1).
    assert objects
    assert f"Read {len(in_range)} records" in result.stdout, (result.stdout, result.stderr)
    assert f"{len(in_scope)} in the scope" in result.stdout, result.stdout
    assert f"To copy: {len(in_scope)} records, with {len(objects)} objects" in result.stdout
    # But copied nothing, since the fake EDGAR answered its fetches (TRANSFER-6).
    assert result.returncode == 3, result.stderr
    assert "the fake EDGAR is installed" in result.stderr
    assert [p.relative_to(data).as_posix() for p in data.rglob("*")] == ["scope.json"]
    # And showed none of its credentials (TRANSFER-4).
    for name, key in [
        ("postgres-transfer", "password"),
        ("garage-key-transfer", "AWS_ACCESS_KEY_ID"),
        ("garage-key-transfer", "AWS_SECRET_ACCESS_KEY"),
    ]:
        assert secret(name, key) not in result.stdout + result.stderr, (name, key)
