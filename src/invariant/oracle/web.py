"""The oracle's web page, served from the Mac over the person's tailnet.

``invariant oracle web`` shows the dashboard on a phone. It is the first
network listener this library runs, so the decisions behind it are recorded
in ADR-008. In short:

* **Only a Tailscale address is bound** (:func:`choose_bind`): the addresses
  in Tailscale's ranges, ``100.64.0.0/10`` and ``fd7a:115c:a1e0::/48``, found
  by asking ``tailscale ip`` and else by reading ``ifconfig``'s tunnel
  interfaces. Never the wildcard. No Tailscale address is a refusal, unless
  ``--allow-loopback`` admits ``127.0.0.1`` for a test on the Mac itself.
* **Every request needs a paired device's token** (``oracle/devices``). Without
  one, every path but the pairing form answers a minimal "not paired" page and
  reads nothing. The ``Host`` header must name a bound address or a MagicDNS
  name, which keeps a DNS-rebinding page from talking to the server.
* **It reads, and acts only on a confirm.** ``GET /`` is the page and ``GET
  /api/state`` the ``oracle list --json`` document, with the view the page
  draws and how fresh it is. ``GET /api/confirm`` shows a row's step exactly
  as the TUI's confirm does, with a nonce; ``POST /api/act`` runs it, carrying
  only the row key and that nonce, from the page's own origin
  (``oracle/webactions``, ADR-008 D8–D12). ``GET /api/job`` is its result.
* **A slow or greedy client is cut off**: a socket timeout per request, a cap
  on headers, and a cap on connections at once.

:class:`WebApp` turns a :class:`Request` into a :class:`Response` and knows
nothing about sockets, so it is tested without one. :class:`WebServer` is the
standard library's ``ThreadingHTTPServer``, one per bound address. The CLI
reaches the address discovery and the server through seams the suite replaces
(:class:`RecordingWeb`).
"""

from __future__ import annotations

import ipaddress
import json
import os
import secrets
import shutil
import socket
import socketserver
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..core.errors import InvariantError
from . import webpage
from .dashboard import AMBER, FLAG_ALERT, RED, Loaded, freshness, lines
from .devices import COOKIE_NAME, Device, DeviceStore, PairingError
from .recommend import RUN, TERMINAL
from .scan import ROW_HOST, ROW_WORKTREE, row_key
from .watcher import STALE_INTERVALS
from .webactions import RemoteActions
from .webpage import MAX_BACKOFF

#: Tailscale's IPv4 range (the CGNAT block) and its IPv6 ULA prefix.
TAILNET_RANGES = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd7a:115c:a1e0::/48"),
)

LOOPBACK = "127.0.0.1"
DEFAULT_PORT = 7341

#: The CLI inside the macOS app, when no ``tailscale`` is on ``PATH``.
MACOS_APP_BINARY = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"

#: Interfaces ``ifconfig`` may name a Tailscale address on: the macOS tunnel,
#: and the Linux daemon's own.
TUNNEL_PREFIXES = ("utun", "tailscale")

#: Seconds a discovery command may take.
PROGRAM_TIMEOUT = 5

#: The largest request body read: the pairing form and an action are a few bytes.
MAX_BODY = 4096

#: Seconds a connection may sit between two reads before it is closed, so a
#: client that sends a request a byte at a time holds no thread for long.
REQUEST_TIMEOUT = 10.0

#: Headers a request may carry. The page's browser sends about a dozen.
MAX_HEADERS = 40

#: Connections served at once; one more is closed without an answer.
MAX_CONNECTIONS = 32

#: A paired cookie lasts as long as browsers let one (400 days, Chrome's cap).
COOKIE_MAX_AGE = 400 * 24 * 3600

Runner = Callable[[list[str]], tuple[int, str]]


class WebError(InvariantError):
    """The server could not start."""


class NoTailnetError(WebError):
    """No Tailscale address to bind, and loopback was not allowed."""


# -- addresses ---------------------------------------------------------------


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None


def is_tailnet(text: str) -> bool:
    address = _address(text)
    return address is not None and any(address in net for net in TAILNET_RANGES)


def tailnet_addresses(candidates: Iterable[str]) -> list[str]:
    """The candidates in Tailscale's ranges, IPv4 first, once each, no zone."""
    found: list[str] = []
    for text in candidates:
        address = _address(text)
        if address is None or not any(address in net for net in TAILNET_RANGES):
            continue
        if str(address) not in found:
            found.append(str(address))
    return sorted(found, key=lambda a: ":" in a)


def parse_ifconfig(text: str) -> list[str]:
    """Every ``inet``/``inet6`` address ``ifconfig`` lists on a tunnel interface."""
    found: list[str] = []
    interface = ""
    for line in text.splitlines():
        if line and not line[0].isspace():
            interface = line.split(":", 1)[0]
            continue
        words = line.split()
        if len(words) >= 2 and words[0] in ("inet", "inet6") and interface.startswith(
            TUNNEL_PREFIXES
        ):
            found.append(words[1])
    return found


def tailscale_binaries(which: Callable[[str], str | None] = shutil.which) -> list[str]:
    found = [path for path in (which("tailscale"),) if path]
    if os.path.isfile(MACOS_APP_BINARY):
        found.append(MACOS_APP_BINARY)
    return found


def run_program(argv: list[str]) -> tuple[int, str]:
    """``argv``'s exit status and output; 127 when it cannot be started."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=PROGRAM_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, str(exc)
    return proc.returncode, proc.stdout or proc.stderr or ""


def discover_addresses(run: Runner, binaries: list[str] | None = None) -> list[str]:
    """This machine's Tailscale addresses: ``tailscale ip``, else ``ifconfig``.

    Both answers are filtered to Tailscale's ranges, so neither can hand the
    server an address on another network.
    """
    for binary in tailscale_binaries() if binaries is None else binaries:
        status, text = run([binary, "ip"])
        if status == 0:
            found = tailnet_addresses(text.split())
            if found:
                return found
    status, text = run(["ifconfig"])
    if status != 0:
        return []
    return tailnet_addresses(parse_ifconfig(text))


def choose_bind(found: list[str], allow_loopback: bool) -> list[str]:
    """The addresses to listen on: the tailnet's, else loopback if allowed."""
    found = tailnet_addresses(found)
    if found:
        return found
    if allow_loopback:
        return [LOOPBACK]
    raise NoTailnetError(
        "no Tailscale address on this Mac, so there is nothing safe to listen on. "
        "The page is served only on the tailnet, never on every interface.\n"
        "  Start Tailscale and run this again, or pass --allow-loopback to serve "
        "on 127.0.0.1 for a test on this Mac."
    )


def url_for(address: str, port: int, path: str = "/") -> str:
    host = f"[{address}]" if ":" in address else address
    return f"http://{host}:{port}{path}"


def host_allowed(header: str | None, bound: list[str]) -> bool:
    """Whether a request's ``Host`` names this server.

    A bound address, a MagicDNS name (``*.ts.net``), or, when loopback is
    bound, ``localhost``. Anything else is a page that resolved its own name
    to this address, and gets nothing.
    """
    if not header:
        return False
    header = header.strip().lower()
    if header.startswith("["):
        end = header.find("]")
        if end < 0:
            return False
        host = header[1:end]
    else:
        host = header.rsplit(":", 1)[0] if header.count(":") == 1 else header
    if host in bound:
        return True
    if LOOPBACK in bound:
        return host == "localhost"
    return host.endswith(".ts.net")


# -- what the page draws -----------------------------------------------------------


def retry_delay(interval: float, failures: int) -> float:
    """Seconds to the next refresh: the interval, doubled per failure, capped.

    The page's script computes the same; this is its specification.
    """
    if failures <= 0:
        return interval
    return min(interval * 2**failures, MAX_BACKOFF)


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _line(line, acting: bool) -> dict[str, Any]:
    advice = line.row.get("recommendation") or {}
    kind = advice.get("kind")
    return {
        "key": line.key,
        "row": row_key(line.row),
        "action": kind if acting and not line.nested and kind in (RUN, TERMINAL) else None,
        "kind": line.row.get("kind"),
        "subject": line.subject,
        "stage": line.stage,
        "status": line.status,
        "tone": line.tone,
        "signals": [{"text": text, "tone": tone} for text, tone in line.signals],
        "next": line.next,
        "label": "" if line.nested else advice.get("label", ""),
        "detail": "" if line.nested else advice.get("detail") or "",
        "where": "" if line.nested else advice.get("cwd") or "",
        "nested": line.nested,
    }


def _noise(line) -> bool:
    """An orphan host or a leaked worktree that raises no alert."""
    return (
        not line.nested
        and line.row.get("kind") in (ROW_HOST, ROW_WORKTREE)
        and line.tone not in (RED, AMBER)
    )


def view(loaded: Loaded, acting: bool = False) -> dict[str, Any]:
    """The rows as the page draws them, project by project.

    Each project's quiet orphan hosts and leaked worktrees are folded into one
    summary line, because a project that tore down many sessions has many of
    them and they bury the rows that matter. One that raises an alert stays.
    Each line names its row by its stable key; with ``acting``, a row whose
    step is ``run`` or ``terminal`` says so, and the page offers its confirm.
    """
    projects = []
    alerts = 0
    for project in loaded.document.get("projects", []):
        entry = project.get("project", {})
        shown, folded = [], []
        for line in lines({"projects": [project]}):
            (folded if _noise(line) else shown).append(_line(line, acting))
            if not line.nested and line.status == FLAG_ALERT:
                alerts += 1
        counts = {ROW_HOST: 0, ROW_WORKTREE: 0}
        for line in folded:
            counts[line["kind"]] += 1
        summary = ", ".join(
            _plural(count, noun)
            for count, noun in ((counts[ROW_HOST], "orphan host"),
                                (counts[ROW_WORKTREE], "leaked worktree"))
            if count
        )
        projects.append({
            "name": entry.get("name", ""),
            "root": entry.get("root", ""),
            "elapsed": project.get("elapsed"),
            "lines": shown,
            "noise": {"summary": summary, "lines": folded},
        })
    return {"projects": projects, "alerts": alerts}


def freshness_fields(loaded: Loaded, served_at: float) -> dict[str, Any]:
    """How old the rows are, and when they count as stale.

    ``age`` is the rows' age when served; the page adds the time since. Stale
    is the snapshot rule: older than ``STALE_INTERVALS`` intervals.
    """
    text, stale = freshness(loaded.age, loaded.interval)
    return {
        "source": loaded.source,
        "age": loaded.age,
        "interval": loaded.interval,
        "stale_after": STALE_INTERVALS * loaded.interval,
        "stale": stale,
        "text": text,
        "served_at": served_at,
        "banner": loaded.banner,
    }


# -- requests and responses ---------------------------------------------------------


@dataclass(frozen=True)
class Request:
    method: str
    #: The request target: path and query.
    path: str
    #: Header names in lower case.
    headers: Mapping[str, str]
    body: bytes = b""


@dataclass
class Response:
    status: int
    body: bytes = b""
    headers: list[tuple[str, str]] = field(default_factory=list)

    def header(self, name: str) -> str | None:
        name = name.lower()
        return next((v for k, v in self.headers if k.lower() == name), None)


_BASE_HEADERS = [
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
]


def _csp(nonce: str) -> str:
    return (
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        f"connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )


def _html(status: int, text: str, nonce: str, extra: list[tuple[str, str]] | None = None):
    return Response(
        status,
        text.encode("utf-8"),
        [("Content-Type", "text/html; charset=utf-8"),
         ("Content-Security-Policy", _csp(nonce)), *_BASE_HEADERS, *(extra or [])],
    )


def _json(status: int, body: dict[str, Any]) -> Response:
    return Response(
        status,
        json.dumps(body, sort_keys=True, default=str).encode("utf-8"),
        [("Content-Type", "application/json"), *_BASE_HEADERS],
    )


def _token(headers: Mapping[str, str]) -> str | None:
    authorization = headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("cookie", ""))
    except CookieError:
        return None
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else None


def _same_origin(headers: Mapping[str, str]) -> bool:
    """No ``Origin``, or one naming the host the request was sent to.

    The pairing form's rule: a missing ``Origin`` is let through there, and
    only there (ADR-008 D11).
    """
    origin = headers.get("origin")
    if origin is None:
        return True
    return _origin_names_host(origin, headers)


def _origin_names_host(origin: str, headers: Mapping[str, str]) -> bool:
    target = urlsplit(origin)
    return target.scheme == "http" and (
        target.netloc.lower() == headers.get("host", "").strip().lower()
    )


def _origin_required(headers: Mapping[str, str]) -> bool:
    """An action's rule: an ``Origin`` naming this server, never a missing one.

    A browser sends ``Origin`` with every ``POST`` a page makes, its own
    origin's included, so a request without one did not come from the page.
    A ``Sec-Fetch-Site`` other than ``same-origin``, where a browser sends it,
    is refused as well.
    """
    origin = headers.get("origin")
    if not origin or not _origin_names_host(origin, headers):
        return False
    return headers.get("sec-fetch-site", "same-origin") == "same-origin"


def _action_fields(headers: Mapping[str, str], body: bytes) -> tuple[str, str] | int:
    """The row key and the nonce from a JSON or form body, else a status."""
    if len(body) > MAX_BODY:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    text = body.decode("utf-8", "replace")
    if content_type == "application/json":
        try:
            sent = json.loads(text)
        except ValueError:
            return HTTPStatus.BAD_REQUEST
        if not isinstance(sent, dict):
            return HTTPStatus.BAD_REQUEST
    elif content_type == "application/x-www-form-urlencoded":
        sent = {name: values[0] for name, values in parse_qs(text).items()}
    else:
        return HTTPStatus.UNSUPPORTED_MEDIA_TYPE
    row, nonce = sent.get("row"), sent.get("nonce")
    if not isinstance(row, str) or not isinstance(nonce, str) or not row:
        return HTTPStatus.BAD_REQUEST
    return row, nonce


class WebApp:
    """Request in, response out. Reads the machine only for a paired device.

    Without ``remote`` it only reads: no line offers an action and the action
    routes are not there.
    """

    def __init__(
        self,
        devices: DeviceStore,
        load: Callable[[], Loaded],
        bound: list[str],
        clock: Callable[[], float] = time.time,
        nonce: Callable[[], str] = lambda: secrets.token_urlsafe(16),
        remote: RemoteActions | None = None,
    ) -> None:
        self.devices = devices
        self.load = load
        self.bound = list(bound)
        self.clock = clock
        self.nonce = nonce
        self.remote = remote
        # One read at a time: a phone and a laptop open at once share it.
        self._reading = threading.Lock()

    def handle(self, request: Request) -> Response:
        headers = {k.lower(): v for k, v in request.headers.items()}
        nonce = self.nonce()
        if not host_allowed(headers.get("host"), self.bound):
            return Response(HTTPStatus.MISDIRECTED_REQUEST, b"not this server\n",
                            [("Content-Type", "text/plain"), *_BASE_HEADERS])
        target = urlsplit(request.path)
        if target.path == "/pair":
            return self._pair(request, headers, target.query, nonce)
        # The token first: an unpaired request learns nothing, not even which
        # methods exist.
        device = self.devices.verify(_token(headers))
        if device is None:
            return _html(HTTPStatus.UNAUTHORIZED, webpage.not_paired(nonce), nonce)
        acting = self.remote is not None
        if acting and target.path == "/api/act":
            return self._act(request, headers, device)
        if request.method not in ("GET", "HEAD"):
            return _html(HTTPStatus.METHOD_NOT_ALLOWED, webpage.message(
                nonce, "not allowed", "Only a confirmed action is sent here."), nonce,
                [("Allow", "GET")])
        if target.path == "/":
            return _html(HTTPStatus.OK, webpage.page(nonce), nonce)
        if target.path == "/api/state":
            return self._state(device)
        query = parse_qs(target.query)
        if acting and target.path == "/api/confirm":
            row = (query.get("row") or [""])[0]
            if not row:
                return _json(HTTPStatus.BAD_REQUEST, {"error": "name a row: ?row=<key>"})
            answer = self.remote.confirm(device, row)
            return _json(answer.status, answer.body)
        if acting and target.path == "/api/job":
            job = self.remote.job(device, (query.get("id") or [""])[0])
            if job is None:
                return _json(HTTPStatus.NOT_FOUND, {"error": "no such action for this device"})
            return _json(HTTPStatus.OK, job)
        return _html(HTTPStatus.NOT_FOUND, webpage.message(nonce, "not found", target.path),
                     nonce)

    def _state(self, device: Device) -> Response:
        try:
            with self._reading:
                loaded = self.load()
        except Exception as exc:  # noqa: BLE001 - shown to the page; the next read runs
            return _json(HTTPStatus.SERVICE_UNAVAILABLE,
                         {"error": f"{type(exc).__name__}: {exc}"})
        return _json(HTTPStatus.OK, {
            "document": loaded.document,
            "view": view(loaded, acting=self.remote is not None),
            "freshness": freshness_fields(loaded, self.clock()),
            "jobs": self.remote.recent(device) if self.remote is not None else [],
        })

    def _act(self, request: Request, headers: Mapping[str, str], device: Device) -> Response:
        """``POST /api/act``: the row key and the nonce, from this page only."""
        if request.method != "POST":
            return _json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "POST a confirmed action"})
        if not _origin_required(headers):
            return _json(HTTPStatus.FORBIDDEN, {
                "error": "an action must come from this page (its Origin); nothing ran"})
        fields = _action_fields(headers, request.body)
        if isinstance(fields, int):
            return _json(fields, {"error": "send the row and the nonce, as JSON or a form; "
                                           "nothing ran"})
        row, nonce = fields
        answer = self.remote.act(device, row, nonce)  # type: ignore[union-attr]
        return _json(answer.status, answer.body)

    def _pair(self, request: Request, headers: Mapping[str, str], query: str,
              nonce: str) -> Response:
        if request.method == "GET":
            code = (parse_qs(query).get("code") or [""])[0]
            return _html(HTTPStatus.OK, webpage.pair_form(nonce, code), nonce)
        if request.method != "POST":
            return _html(HTTPStatus.METHOD_NOT_ALLOWED,
                         webpage.message(nonce, "not allowed", "GET or POST only."), nonce,
                         [("Allow", "GET, POST")])
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            return _html(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                         webpage.message(nonce, "not paired", "Send the pairing form."), nonce)
        if not _same_origin(headers):
            return _html(HTTPStatus.FORBIDDEN, webpage.message(
                nonce, "not paired", "That form did not come from this page."), nonce)
        code = (parse_qs(request.body.decode("utf-8", "replace")).get("code") or [""])[0]
        try:
            _, token = self.devices.redeem(code)
        except PairingError as exc:
            return _html(HTTPStatus.FORBIDDEN, webpage.message(nonce, "not paired", str(exc)),
                         nonce)
        cookie = (f"{COOKIE_NAME}={token}; Path=/; Max-Age={COOKIE_MAX_AGE}; "
                  f"HttpOnly; SameSite=Strict")
        return Response(HTTPStatus.SEE_OTHER, b"",
                        [("Location", "/"), ("Set-Cookie", cookie), *_BASE_HEADERS])


# -- the socket -----------------------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    server_version = "invariant-oracle"
    sys_version = ""

    def setup(self) -> None:
        # StreamRequestHandler applies ``timeout`` to the socket: every read,
        # the request line, each header and the body, waits this long at most.
        self.timeout = self.server.request_timeout  # type: ignore[attr-defined]
        super().setup()

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        if len(self.headers) > MAX_HEADERS:
            self.send_error(HTTPStatus.REQUEST_HEADER_FIELDS_TOO_LARGE,
                            f"more than {MAX_HEADERS} headers")
            return False
        return True

    def _respond(self) -> None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.send_error(HTTPStatus.BAD_REQUEST, "bad Content-Length")
            return
        if length > MAX_BODY:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        body = self.rfile.read(length) if length > 0 else b""
        request = Request(self.command, self.path,
                          {k.lower(): v for k, v in self.headers.items()}, body)
        response = self.server.app.handle(request)  # type: ignore[attr-defined]
        self.send_response(response.status)
        for name, value in response.headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(response.body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = _respond

    def log_message(self, format: str, *args: Any) -> None:
        # Silent: a request line can carry a pairing code.
        return


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False
    request_timeout = REQUEST_TIMEOUT
    slots: threading.BoundedSemaphore

    def process_request(self, request, client_address) -> None:
        # One thread per connection, up to a cap: past it a connection is
        # closed unanswered, so many slow clients cannot exhaust threads.
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def server_bind(self) -> None:
        # HTTPServer.server_bind asks socket.getfqdn for the bound address's
        # name: a reverse lookup that can leave the machine and that nothing
        # here reads. The address is its own name.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), port


class _Server6(_Server):
    address_family = socket.AF_INET6


class WebServer:
    """One ``ThreadingHTTPServer`` per bound address, all serving one app."""

    def __init__(
        self,
        app: WebApp,
        addresses: list[str],
        port: int,
        request_timeout: float = REQUEST_TIMEOUT,
        max_connections: int = MAX_CONNECTIONS,
    ) -> None:
        self.app = app
        self.addresses = list(addresses)
        self.port = port
        self.request_timeout = request_timeout
        self.max_connections = max_connections
        self.servers: list[_Server] = []
        self.stopped = threading.Event()
        self._running: list[_Server] = []

    def start(self) -> list[str]:
        """Bind every address, or none: a failure closes what was opened."""
        port = self.port
        try:
            for address in self.addresses:
                kind = _Server6 if ":" in address else _Server
                server = kind((address, port), _Handler)
                server.app = self.app  # type: ignore[attr-defined]
                server.request_timeout = self.request_timeout
                server.slots = threading.BoundedSemaphore(self.max_connections)
                self.servers.append(server)
                port = server.server_address[1]
        except OSError as exc:
            self.close()
            raise WebError(f"cannot listen on {address} port {port}: {exc}") from exc
        self.port = port
        return [url_for(address, port) for address in self.addresses]

    def serve_in_background(self) -> None:
        for server in self.servers:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self._running.append(server)

    def serve(self) -> None:
        """Serve until interrupted."""
        self.serve_in_background()
        try:
            while not self.stopped.wait(timeout=1.0):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self) -> None:
        for server in self._running:
            server.shutdown()
        for server in self.servers:
            server.server_close()
        self.servers, self._running = [], []
        self.stopped.set()


# -- for tests ------------------------------------------------------------------------


@dataclass
class RecordingServer:
    """A server that binds nothing and records what it was asked to serve."""

    app: WebApp
    addresses: list[str]
    port: int
    started: bool = False
    served: bool = False
    background: bool = False
    closed: bool = False

    def start(self) -> list[str]:
        self.started = True
        return [url_for(address, self.port) for address in self.addresses]

    def serve(self) -> None:
        self.served = True

    def serve_in_background(self) -> None:
        self.background = True

    def close(self) -> None:
        self.closed = True


class RecordingWeb:
    """The CLI's web seams, inert: no tailnet until a test sets one.

    ``wait`` stands in for ``oracle web --wait``'s pause: each call records
    its seconds and runs the next of ``script``, and once the script is spent
    it stops the loop rather than sleeping.
    """

    def __init__(self) -> None:
        self.addresses: list[str] = []
        self.servers: list[RecordingServer] = []
        self.factory = self._make
        self.script: list[Callable[[], None]] = []
        self.waits: list[float] = []

    def wait(self, seconds: float) -> bool:
        self.waits.append(seconds)
        if not self.script:
            return True
        self.script.pop(0)()
        return False

    def _make(self, app: WebApp, addresses: list[str], port: int) -> RecordingServer:
        server = RecordingServer(app, list(addresses), port)
        self.servers.append(server)
        return server

