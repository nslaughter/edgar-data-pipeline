# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Build the pipeline's own images from the commit checked out, and load them into kind.

Implements spec/deployment.md's IMG-2 and IMG-5. Each directory images/NAME/
holds the Dockerfile of the image edgar/NAME. Each is built from the files of
the commit checked out, as `git archive` gives them, and tagged with that
commit's 40-character hash, which the umbrella chart's value `images.commit`
names (IMG-1). So the tag names what's built, the script refuses a working
tree with uncommitted changes, untracked files among them. Each image is
then loaded into every node of the kind cluster with `kind load
docker-image`; none is pushed to a registry or pulled from one.

    uv run scripts/build_images.py                  # the cluster deploy/kind.yaml names
    uv run scripts/build_images.py --cluster NAME
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
REPOSITORY = "edgar"  # each image is edgar/NAME
CLUSTER = "edgar"  # deploy/kind.yaml's name
COMMIT = re.compile(r"[0-9a-f]{40}")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True
    ).stdout


def names() -> list[str]:
    """Each image's name: each directory of images/ that holds a Dockerfile."""
    return sorted(path.parent.name for path in IMAGES.glob("*/Dockerfile"))


def build(name: str, commit: str) -> str:
    """Builds images/NAME/Dockerfile from the commit's files, tagged with the commit."""
    image = f"{REPOSITORY}/{name}:{commit}"
    archive = subprocess.Popen(
        ["git", "-C", str(ROOT), "archive", "--format=tar", commit], stdout=subprocess.PIPE
    )
    try:
        docker = subprocess.run(
            ["docker", "build", "--tag", image, "--file", f"images/{name}/Dockerfile", "-"],
            stdin=archive.stdout,
            check=False,
        )
    finally:
        assert archive.stdout is not None
        archive.stdout.close()
    if archive.wait() != 0 or docker.returncode != 0:
        raise SystemExit(f"Building {image} failed.")
    return image


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cluster", default=CLUSTER, help="the kind cluster to load them into")
    args = parser.parse_args(argv)

    if git("status", "--porcelain", "--untracked-files=normal").strip():
        print(
            "The working tree has uncommitted changes. Commit them, or set them "
            "aside, so that each image's tag names the commit it's built from.",
            file=sys.stderr,
        )
        return 1
    commit = git("rev-parse", "HEAD").strip()
    assert COMMIT.fullmatch(commit), commit
    images = [build(name, commit) for name in names()]
    for image in images:
        subprocess.run(["kind", "load", "docker-image", image, "--name", args.cluster], check=True)
    print(f"Built and loaded {', '.join(images)}.")
    print(f"Install the umbrella chart with images.commit set to {commit}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
