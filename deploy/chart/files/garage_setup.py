"""Sets Garage up as the umbrella chart's values describe, through its admin API.

The chart runs it as a hook Job after each install and upgrade
(spec/deployment.md, GARAGE-2). It assigns each node a zone of its own and a
capacity in the layout, and applies it; imports each key; creates each
bucket; and grants each key its buckets, and nothing more. What already
exists is left as it is, so it succeeds when it runs again.

It reads the admin API's URL and token from the environment, the values from
$CONFIG, a JSON object of `nodes`, `capacity`, and `buckets`, each bucket's
keys with their permissions, and each key's ID and secret from the files
$KEYS/NAME/AWS_ACCESS_KEY_ID and $KEYS/NAME/AWS_SECRET_ACCESS_KEY.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

# How long to wait for Garage's nodes to find each other, and between tries.
WAIT_SECONDS = 600
RETRY_SECONDS = 5


class AdminAPI:
    """Garage's admin API, version 2."""

    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/")
        self.token = token

    def call(self, endpoint: str, body: Any = None, *, query: dict[str, str] | None = None) -> Any:
        """Calls an endpoint: POST with a body, GET without one. None for a 404."""
        url = f"{self.url}/v2/{endpoint}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(
            url,
            data=data,
            method="GET" if body is None else "POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                text = response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise RuntimeError(f"{endpoint}: {error.code} {error.read().decode()}") from error
        return json.loads(text) if text else {}


def wait_for_nodes(api: AdminAPI, count: int) -> list[dict[str, Any]]:
    """Waits until the cluster has `count` nodes up, and returns them."""
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            nodes = [n for n in api.call("GetClusterStatus")["nodes"] if n["isUp"]]
        except (OSError, RuntimeError) as error:
            nodes, reason = [], str(error)
        else:
            reason = f"{len(nodes)} of {count} nodes are up"
        if len(nodes) >= count:
            return nodes
        if time.monotonic() > deadline:
            raise RuntimeError(f"Garage wasn't ready: {reason}")
        print(f"Waiting for Garage: {reason}", flush=True)
        time.sleep(RETRY_SECONDS)


def set_layout(api: AdminAPI, nodes: list[dict[str, Any]], capacity: int) -> None:
    """Gives each node without a role a zone of its own, its hostname, and applies it."""
    layout = api.call("GetClusterLayout")
    assigned = {role["id"] for role in layout["roles"]}
    roles = [
        {"id": node["id"], "zone": node["hostname"], "capacity": capacity, "tags": []}
        for node in sorted(nodes, key=lambda n: n["hostname"])
        if node["id"] not in assigned
    ]
    if not roles:
        print(f"The layout already holds the nodes, at version {layout['version']}")
        return
    api.call("UpdateClusterLayout", {"roles": roles})
    api.call("ApplyClusterLayout", {"version": layout["version"] + 1})
    print(f"Applied layout version {layout['version'] + 1}, with {len(roles)} new nodes")


def import_key(api: AdminAPI, name: str, keys: Path) -> str:
    """Imports a key, unless Garage holds it, and returns its ID."""
    key_id = (keys / name / "AWS_ACCESS_KEY_ID").read_text().strip()
    if api.call("GetKeyInfo", query={"id": key_id}) is not None:
        print(f"Key {name} exists")
        return key_id
    secret = (keys / name / "AWS_SECRET_ACCESS_KEY").read_text().strip()
    api.call("ImportKey", {"accessKeyId": key_id, "secretAccessKey": secret, "name": name})
    print(f"Imported key {name}")
    return key_id


def create_bucket(api: AdminAPI, name: str) -> str:
    """Creates a bucket, unless it exists, and returns its ID."""
    bucket = api.call("GetBucketInfo", query={"globalAlias": name})
    if bucket is not None:
        print(f"Bucket {name} exists")
        return str(bucket["id"])
    bucket = api.call("CreateBucket", {"globalAlias": name})
    print(f"Created bucket {name}")
    return str(bucket["id"])


def grant(api: AdminAPI, bucket_id: str, key_id: str, read: bool, write: bool) -> None:
    """Allows a key what the values give it on a bucket, and denies it the rest.

    No key is allowed owner, which would let it change the bucket's
    configuration (GARAGE-4).
    """
    allowed = {"read": read, "write": write, "owner": False}
    denied = {permission: not given for permission, given in allowed.items()}
    body = {"bucketId": bucket_id, "accessKeyId": key_id}
    if any(allowed.values()):
        api.call("AllowBucketKey", {**body, "permissions": allowed})
    api.call("DenyBucketKey", {**body, "permissions": denied})


def main() -> None:
    api = AdminAPI(os.environ["GARAGE_ADMIN_URL"], os.environ["GARAGE_ADMIN_TOKEN"])
    config = json.loads(Path(os.environ.get("CONFIG", "/config/garage.json")).read_text())
    keys = Path(os.environ.get("KEYS", "/keys"))

    nodes = wait_for_nodes(api, config["nodes"])
    set_layout(api, nodes, config["capacity"])
    names = sorted({name for grants in config["buckets"].values() for name in grants})
    key_ids = {name: import_key(api, name, keys) for name in names}
    bucket_ids = {bucket: create_bucket(api, bucket) for bucket in config["buckets"]}
    for bucket, grants in config["buckets"].items():
        for name, permissions in grants.items():
            grant(api, bucket_ids[bucket], key_ids[name], permissions["read"], permissions["write"])
            print(f"Granted key {name} on bucket {bucket}: {permissions}")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print(error, file=sys.stderr)
        sys.exit(1)
