"""Runs the fake EDGAR until it's stopped (spec/fake-edgar.md).

    python -m edgar_pipeline.fake_edgar --data data
    python -m edgar_pipeline.fake_edgar --data data --host 0.0.0.0 \\
        --certificate tls.crt --key tls.key

It reads the git store at --data once, then answers as EDGAR at --port, over
HTTPS with --certificate and --key and over plain HTTP without them, and
takes cues and gives its request log at --admin-port, over plain HTTP
(SERVE-1, SERVE-5, ADMIN-1). It writes nothing about a request to its output
(REQ-2).
"""

from __future__ import annotations

import argparse
import signal
import ssl
import sys
from pathlib import Path
from types import FrameType

from .server import FakeEdgar
from .store import StoreError


def stop(signum: int, frame: FrameType | None) -> None:
    # As the first process of its container, it gets no default action on
    # SIGTERM, which Kubernetes sends to stop it.
    raise SystemExit(0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m edgar_pipeline.fake_edgar",
        description="Answer as EDGAR from the stored versions in a git store.",
    )
    parser.add_argument("--data", type=Path, required=True, help="the git store's data/")
    parser.add_argument("--host", default="127.0.0.1", help="the address both ports listen on")
    parser.add_argument("--port", type=int, default=8443, help="the server's port")
    parser.add_argument("--admin-port", type=int, default=8080, help="the admin port")
    parser.add_argument("--certificate", type=Path, help="the server's certificate, for HTTPS")
    parser.add_argument("--key", type=Path, help="the certificate's key")
    args = parser.parse_args(argv)
    if (args.certificate is None) != (args.key is None):
        parser.error("--certificate and --key come together")

    try:
        fake = FakeEdgar(
            args.data,
            host=args.host,
            port=args.port,
            admin_port=args.admin_port,
            certificate=args.certificate,
            key=args.key,
        )
    except (StoreError, OSError, ssl.SSLError) as err:
        print(f"The fake EDGAR can't start: {err}", file=sys.stderr)
        return 1
    signal.signal(signal.SIGTERM, stop)
    scheme = "HTTPS" if args.certificate else "plain HTTP"
    print(
        f"Serving {len(fake.stored)} stored URLs from {args.data} over {scheme} at port "
        f"{fake.port}, with the admin port at {fake.admin_port}.",
        flush=True,
    )
    fake.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
