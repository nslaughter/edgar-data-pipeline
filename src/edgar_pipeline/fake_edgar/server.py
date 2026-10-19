"""The fake EDGAR's server and its admin port (spec/fake-edgar.md, sections 4 to 7).

The server answers as EDGAR, one request at a time, each on a connection of
its own, over HTTPS when it's given a certificate and its key (SERVE-5). The
admin port, plain HTTP, adds and removes cues and gives the request log
(ADMIN-1). Both share a State: the cues kept and the log, which is in memory
only (CUE-1, REQ-1, REQ-2).
"""

from __future__ import annotations

import contextlib
import gzip
import json
import re
import socket
import socketserver
import ssl
import sys
import threading
from datetime import UTC, datetime
from email.message import Message
from email.utils import formatdate, parsedate_to_datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import cues
from .store import SEC_HOSTS, StoredURL, Version, load

# How long a client may take to send its request, or to read the answer,
# before the fake EDGAR gives up on it, so one slow client can't hold the
# server, which answers one request at a time, for long.
TIMEOUT = 10.0
MAX_LINE = 65536
MAX_DISCARD = 1 << 20  # the most of a request's body read only to discard it
# An entity tag, weak or strong, as RFC 9110 gives it: its opaque tag never
# holds a double quote, but may hold a comma.
ETAG = re.compile(r'(?:W/)?"[^"]*"')
QVALUE = re.compile(r"0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?")
AFTER = re.compile(r"[0-9]+")


def now() -> str:
    """The fake EDGAR's clock, as a timestamp in UTC with milliseconds (REQ-1)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def http_date(value: str) -> datetime | None:
    """An HTTP-date in any of RFC 9110's three forms, or None if it isn't one."""
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def opaque(tag: str) -> str:
    """An entity tag without its weakness, for weak comparison (RFC 9110, 8.8.3.2)."""
    return tag[2:] if tag.startswith("W/") else tag


def accepts_gzip(values: list[str] | None) -> bool:
    """Whether an Accept-Encoding accepts gzip, as RFC 9110, 12.5.3, reads it.

    A coding listed with a q-value of 0 isn't acceptable, `x-gzip` is gzip,
    and `*` stands for gzip unless gzip is listed. No Accept-Encoding accepts
    no coding here, so the body is sent as it is (SERVE-3).
    """
    if values is None:
        return False
    weights: dict[str, float] = {}
    for member in ",".join(values).split(","):
        name, *parameters = (part.strip() for part in member.split(";"))
        weight: float | None = 1.0
        for parameter in parameters:
            key, _, value = parameter.partition("=")
            if key.strip().lower() == "q":
                weight = float(value.strip()) if QVALUE.fullmatch(value.strip()) else None
        if name and weight is not None:
            weights.setdefault("gzip" if name.lower() == "x-gzip" else name.lower(), weight)
    weight = weights.get("gzip", weights.get("*"))
    return weight is not None and weight > 0


def joined(headers: Message) -> dict[str, str]:
    """Each header by its name in lower case, a repeated one's values joined (REQ-1)."""
    found: dict[str, str] = {}
    for name, value in headers.items():
        key = name.lower()
        found[key] = f"{found[key]}, {value}" if key in found else str(value)
    return found


def hostname(host: str | None) -> str | None:
    """A Host header's host, without its port, in lower case."""
    if host is None:
        return None
    host = host.strip().lower()
    if host.startswith("["):
        return host.split("]", 1)[0] + "]"
    return host.rsplit(":", 1)[0]


class State:
    """What the server and the admin port share: the cues kept, and the request log."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        # Held while the server answers a request and logs it, so the admin
        # port gives the log as it is between requests: once a client has its
        # answer, the log holds its request (REQ-1).
        self.answering = threading.Lock()
        self.cues: list[cues.Cue] = []
        self.log: list[dict[str, Any]] = []

    def add(self, cue: cues.Cue) -> None:
        with self.lock:
            self.cues.append(cue)

    def clear(self) -> None:
        with self.lock:
            self.cues.clear()

    def take(self, urls: list[str]) -> cues.Response | None:
        """The next response of the first cue for one of urls, else of the
        first for any URL, each given once (CUE-1, CUE-2)."""
        with self.lock:
            matching = [cue for cue in self.cues if cue.url in urls]
            matching = matching or [cue for cue in self.cues if cue.url is None]
            if not matching:
                return None
            cue = matching[0]
            response = cue.responses.popleft()
            if not cue.responses:
                self.cues.remove(cue)
            return response

    def append(self, entry: dict[str, Any]) -> None:
        with self.lock:
            self.log.append({"n": len(self.log) + 1, **entry})

    def entries(self, after: int) -> list[dict[str, Any]]:
        with self.answering, self.lock:
            return [entry for entry in self.log if entry["n"] > after]


class Server(HTTPServer):
    """An HTTPServer that asks no DNS server for its own name, so the fake
    EDGAR sends nothing to anything (SERVE-1)."""

    request_queue_size = 64

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)

    def handle_error(self, request: Any, client_address: Any) -> None:
        # No traceback, whose values could hold a request header (REQ-2).
        kind = sys.exc_info()[0]
        print(f"A request failed: {kind.__name__ if kind else 'unknown'}.", file=sys.stderr)


class EdgarServer(Server):
    """Answers as EDGAR, one request at a time, in the order they arrive (SERVE-5)."""

    def __init__(
        self,
        address: tuple[str, int],
        stored: dict[str, StoredURL],
        state: State,
        context: ssl.SSLContext | None,
    ) -> None:
        self.stored = stored
        self.state = state
        super().__init__(address, EdgarHandler)
        if context is not None:
            # The handshake happens in the handler, under its timeout.
            self.socket = context.wrap_socket(
                self.socket, server_side=True, do_handshake_on_connect=False
            )

    def named(self, target: str, host: str | None) -> list[str]:
        """The URLs a request names: its target on its Host, or on either
        SEC host when its Host is neither (spec/fake-edgar.md, section 2)."""
        name = hostname(host)
        hosts = [name] if name in SEC_HOSTS else list(SEC_HOSTS)
        return [f"https://{each}{target}" for each in hosts]


class Handler(BaseHTTPRequestHandler):
    """Writes each response's head itself, so it sends only the headers given."""

    timeout = TIMEOUT
    # Its errors to a request too malformed to name a version are HTTP/1.0's,
    # with a status line, as its every other answer is.
    default_request_version = "HTTP/1.0"
    status: int | None = None

    def send_response_only(self, code: int, message: str | None = None) -> None:
        # BaseHTTPRequestHandler's own errors, such as a 400 to a malformed
        # request, still name their status.
        self.status = code
        super().send_response_only(code, message)

    def write_head(self, status: int, headers: list[tuple[str, str]]) -> None:
        try:
            phrase = HTTPStatus(status).phrase
        except ValueError:
            phrase = ""
        lines = [f"{self.protocol_version} {status} {phrase}\r\n"]
        lines += [f"{name}: {value}\r\n" for name, value in headers]
        self.status = int(status)
        # A cue's header is any string (CUE-3), so it's sent as UTF-8.
        self.wfile.write("".join([*lines, "\r\n"]).encode("utf-8", "surrogatepass"))

    def reply(self, status: int, headers: dict[str, str], body: bytes) -> None:
        """A status with headers and a body, adding Date and Content-Length
        unless the headers have them (CUE-2)."""
        given = {name.lower() for name in headers}
        added = [] if "date" in given else [("Date", formatdate(usegmt=True))]
        if "content-length" not in given:
            added.append(("Content-Length", str(len(body))))
        self.write_head(status, [*headers.items(), *added])
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # REQ-2: nothing about a request is written to the output


class EdgarHandler(Handler):
    server: EdgarServer

    def handle(self) -> None:
        # One request on each connection, closed once it's answered (SERVE-5).
        self.close_connection = True
        try:
            line = self.rfile.readline(MAX_LINE + 1)
        except OSError:
            return  # no request arrived, or the TLS handshake failed
        if not line:
            return
        received_at = now()
        words = line.decode("iso-8859-1").split()
        entry: dict[str, Any] = {
            "received_at": received_at,
            "client": str(self.client_address[0]),
            # Its target as the request line gives it, which parse_request
            # may change, as it does a target that begins with //.
            "method": words[0] if words else "",
            "target": words[1] if len(words) > 1 else "",
            "url": None,
            "headers": {},
            "status": None,
            "cued": False,
        }
        with self.server.state.answering:
            try:
                self.raw_requestline = line
                if len(line) > MAX_LINE:
                    self.requestline, self.request_version, self.command = "", "", ""
                    self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
                elif self.parse_request():
                    entry["headers"] = joined(self.headers)
                    self.answer(entry)
            except OSError:
                pass  # the client went away, or was too slow
            finally:
                entry["status"] = self.status
                self.server.state.append(entry)

    def answer(self, entry: dict[str, Any]) -> None:
        """Answers the request. Before it sends anything, it notes in the
        request's entry the stored URL it's for and whether a cue answers
        it, so the entry has them even if the client goes away (SERVE-2,
        CUE-2, REQ-1)."""
        named = self.server.named(entry["target"], self.headers.get("Host"))
        stored = next((self.server.stored[u] for u in named if u in self.server.stored), None)
        if entry["method"] != "GET":
            self.discard_body()
            self.reply(HTTPStatus.METHOD_NOT_ALLOWED, {"Allow": "GET"}, b"")
            return
        entry["url"] = stored.url if stored else None
        cue = self.server.state.take(named)
        if cue is None:
            if stored is None:
                self.reply(HTTPStatus.NOT_FOUND, {}, b"")
            else:
                self.serve(stored.versions[stored.latest])
            return
        entry["cued"] = True
        if cue.drop:
            self.drop()
        elif cue.status is not None:
            self.reply(cue.status, cue.headers, cue.body)
        else:
            version = stored.versions.get(cue.sha256 or stored.latest) if stored else None
            if version is None:
                self.reply(HTTPStatus.NOT_FOUND, {}, b"")
            else:
                self.serve(version, cut=cue.cut)

    def serve(self, version: Version, *, cut: bool = False) -> None:
        """A version as EDGAR gave it, or a 304 if the request's validators
        say it hasn't changed (SERVE-3, SERVE-4); cut, only half its body."""
        validators = [
            (name, value)
            for name, value in (("ETag", version.etag), ("Last-Modified", version.last_modified))
            if value is not None
        ]
        date = ("Date", formatdate(usegmt=True))
        if not cut and self.unchanged(version):
            self.write_head(HTTPStatus.NOT_MODIFIED, [date, *validators])
            return
        encoded = accepts_gzip(self.headers.get_all("Accept-Encoding"))
        body = version.object if encoded else gzip.decompress(version.object)
        headers = [date, *validators]
        if version.content_type is not None:
            headers.append(("Content-Type", version.content_type))
        if encoded:
            headers.append(("Content-Encoding", "gzip"))
        headers.append(("Content-Length", str(len(body))))
        self.write_head(HTTPStatus.OK, headers)
        if cut:
            self.wfile.write(body[: len(body) // 2])
            self.drop()
        else:
            self.wfile.write(body)

    def unchanged(self, version: Version) -> bool:
        """Whether a conditional GET asks about this version, as RFC 9110, 13.2.2, has it."""
        match = self.headers.get_all("If-None-Match")
        if match is not None:
            given = ", ".join(match)
            if given.strip() == "*":
                return True
            etag = version.etag
            return etag is not None and any(
                opaque(tag) == opaque(etag) for tag in ETAG.findall(given)
            )
        since = self.headers.get_all("If-Modified-Since")
        if since is None or len(since) != 1 or version.last_modified is None:
            return False
        asked, modified = http_date(since[0]), http_date(version.last_modified)
        return asked is not None and modified is not None and asked >= modified

    def discard_body(self) -> None:
        """Reads a request's body, so that closing the connection doesn't reset it."""
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if 0 < length <= MAX_DISCARD:
            self.rfile.read(length)

    def drop(self) -> None:
        """Closes the connection, sending nothing more (CUE-2)."""
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)


class AdminServer(socketserver.ThreadingMixIn, Server):
    """The admin port: cues in, the request log out (ADMIN-1)."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], state: State) -> None:
        self.state = state
        super().__init__(address, AdminHandler)


class AdminHandler(Handler):
    server: AdminServer

    def handle_one_request(self) -> None:
        self.close_connection = True
        try:
            self.raw_requestline = self.rfile.readline(MAX_LINE + 1)
        except OSError:
            return
        if not self.raw_requestline:
            return
        if len(self.raw_requestline) > MAX_LINE:
            self.requestline, self.request_version, self.command = "", "", ""
            self.send_error(HTTPStatus.REQUEST_URI_TOO_LONG)
            return
        if not self.parse_request():
            return
        parts = urlsplit(self.path)
        request = (self.command, parts.path)
        if request == ("POST", "/cues"):
            self.add_cue()
        elif request == ("DELETE", "/cues"):
            self.server.state.clear()
            self.reply(HTTPStatus.NO_CONTENT, {}, b"")
        elif request == ("GET", "/requests"):
            self.give_requests(parts.query)
        else:
            self.reply(HTTPStatus.NOT_FOUND, {}, b"")

    def add_cue(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length > 0 else b""
            cue = cues.parse(json.loads(body.decode("utf-8"), parse_constant=not_json))
        except (UnicodeDecodeError, ValueError) as err:
            self.text(HTTPStatus.BAD_REQUEST, f"Not a cue: {err}\n")
            return
        self.server.state.add(cue)
        self.reply(HTTPStatus.NO_CONTENT, {}, b"")

    def give_requests(self, query: str) -> None:
        given = parse_qs(query, keep_blank_values=True).get("after", ["0"])
        if len(given) != 1 or not AFTER.fullmatch(given[0]):
            self.text(HTTPStatus.BAD_REQUEST, "after is not an integer of 0 or more\n")
            return
        body = json.dumps(self.server.state.entries(int(given[0]))).encode("ascii")
        self.reply(HTTPStatus.OK, {"Content-Type": "application/json"}, body)

    def text(self, status: int, message: str) -> None:
        self.reply(status, {"Content-Type": "text/plain; charset=utf-8"}, message.encode())


def not_json(name: str) -> Any:
    """Refuses NaN and the infinities, which JSON doesn't have."""
    raise ValueError(f"{name} is not JSON")


class FakeEdgar:
    """The fake EDGAR: its server and its admin port, over one read of a git
    store (SERVE-1), with the server's port listening before the admin's."""

    def __init__(
        self,
        data: Path,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        admin_port: int = 0,
        certificate: Path | None = None,
        key: Path | None = None,
    ) -> None:
        self.stored = load(data)
        self.state = State()
        context = None
        if certificate is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(certificate, key)
        self.server = EdgarServer((host, port), self.stored, self.state, context)
        try:
            self.admin = AdminServer((host, admin_port), self.state)
        except BaseException:
            self.server.server_close()
            raise
        self.serving: list[Server] = []

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def admin_port(self) -> int:
        return int(self.admin.server_address[1])

    def start(self) -> None:
        """Serves both ports, each from a thread of its own, until stop."""
        for server in (self.server, self.admin):
            threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
            self.serving.append(server)

    def serve_forever(self) -> None:
        """Serves the admin port from a thread, and the server from this one."""
        threading.Thread(target=self.admin.serve_forever, daemon=True).start()
        self.serving += [self.admin, self.server]
        try:
            self.server.serve_forever()
        finally:
            self.stop()

    def stop(self) -> None:
        """Stops serving, and closes both ports."""
        for server in (self.admin, self.server):
            if server in self.serving:
                server.shutdown()
            server.server_close()
        self.serving.clear()
