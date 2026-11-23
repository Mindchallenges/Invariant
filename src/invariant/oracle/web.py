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
  draws (``oracle/panel``'s) and how fresh it is. ``GET /api/confirm`` shows
  a row's step as the TUI's confirm does, with a nonce and the sheet's words;
  ``POST /api/act`` runs it, carrying only the row key and that nonce, from
  the page's own origin (``oracle/webactions``, ADR-008 D8–D12). ``GET
  /api/job`` is its result. ``GET /api/start/confirm`` and ``POST
  /api/start`` are the "New session" sheet's confirm and start, the same way:
  a registered project's key and a nonce, never a path (IDEA_030, ADR-009
  D16). A chain action (ADR-027 increment 3) is confirmed and run on the same
  two routes, named by its chain key, and ``/api/state`` carries the chain's
  read in the view. The home-screen app's manifest and icons
  (ADR-009 D10) are served under the same token, and read nothing.
* **A live window's last message is read for the view, never the document**
  (ADR-029 increment 1, ADR-009 D17). With a ``SessionTalk`` (``oracle/talk``,
  injected by the CLI), ``/api/state`` reads each live window's conversation
  through it, once the token has verified, and hands the panel each row's
  last message. The ``assess`` document it serves beside the view never holds
  it, the response is ``no-store`` like every other, and nothing here keeps
  it after the response. A read that fails shows nothing for that window.
* **A live window's conversation is read on demand** (ADR-029 increment 2).
  ``GET /api/session?row=<key>[&after=<cursor>]``, behind the token like
  every route, reads that one window through the same seam when the page asks:
  its newest turns (``oracle/talk.turns``), the screen's head
  (``oracle/panel.conversation_head``) and what the page may do (nothing, in
  this increment, and why). ``after`` is the cursor the last answer carried;
  a read that finds the same answers ``unchanged`` and no text. A response is
  at most :data:`SESSION_RESPONSE_CAP`, the oldest turns dropped to fit. A
  read that fails is "cannot read", never an error page, and a window that is
  gone reads nothing.
* **A paired device sends a live window a message** (ADR-029 increment 3).
  With a :class:`~invariant.oracle.talk.Talker` (``sender``), ``/api/session``
  also carries what the page may send and why not, this device's sends to the
  row with their states, and a one-tap talk nonce bound to the fingerprint
  that read saw; ``POST /api/talk`` holds a message for its Undo window and
  ``POST /api/talk/cancel`` undoes it, both from the page's own origin, with a
  body of at most :data:`~invariant.oracle.talk.BODY_CAP`. ``/api/state``
  carries the sender's boot id, so a page holding a send from an older boot
  shows it dropped. The text is held in memory until typed, and never logged.
* **A paired device stops a running turn** (ADR-029 increment 4). The same
  read offers ``interrupt`` while a turn runs and issues the nonce for it even
  where send is off; ``POST /api/talk`` with ``kind: interrupt`` and no text
  holds it for the same Undo window.
* **A paired device answers a question** (ADR-029 increment 5). While the
  window's record says a question waits, the read carries it as ``pending``
  (the host's read, as plain words) and offers ``answer``, its nonce bound to
  that question; ``POST /api/talk`` with ``kind: question`` and ``answers``,
  each question's choices by index and an optional "Other", holds it for the
  same Undo window. ``/api/state`` reads each waiting question too, for its
  card's note and "Answer…".
* **A device may opt in to web push** (ADR-027 increment 4, ruling 17). Over
  HTTPS, with ``cryptography`` on the Mac, ``/api/state`` carries the VAPID
  public key and whether this device is subscribed; ``GET /sw.js`` is the
  service worker that shows a push, and ``POST /api/push/subscribe`` and
  ``/api/push/unsubscribe`` keep or drop this device's subscription in
  ``oracle/push``'s book, from the page's own origin only. The browser's own
  permission prompt is the confirm: the subscription is the device's setting
  and runs nothing on the Mac.
* **A slow or greedy client is cut off**: a socket timeout per request, a cap
  on headers, and a cap on connections at once.
* **Over HTTPS once there is a certificate** (IDEA_026, ADR-008 D19-D24).
  With a :class:`~invariant.oracle.tls.Served`, each listener speaks TLS with
  Tailscale's certificate for the Mac's MagicDNS name, answers only that name
  in ``Host``, wants the ``https`` origin, and marks the cookie ``Secure``. A
  plain-HTTP request to it gets a redirect to the https name and nothing else.
  Without one the page is plain HTTP, as before, with a warning on it.

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
import ssl
import subprocess
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from ..core.errors import InvariantError, StateError
from . import panel, webpage
from .dashboard import Loaded, freshness
from .devices import COOKIE_NAME, Device, DeviceStore, PairingError
from .push import PushBook, PushError, check_subscription
from .scan import row_key
from .talk import (
    BODY_CAP,
    KIND_QUESTION,
    QuestionRead,
    SessionTalk,
    Talker,
    TalkHost,
    Turns,
    cursor,
    fingerprint,
    first_line,
    last_message,
    offered,
    pending_view,
    read_question,
    read_turns,
)
from .watcher import STALE_INTERVALS
from .webactions import RemoteActions
from .webpage import MAX_BACKOFF

if TYPE_CHECKING:
    from .tls import Served

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

#: The terminal type a program is given when its environment names none. The
#: Tailscale app's CLI starts its GUI instead of answering when ``TERM`` is
#: missing, as it is under launchd (BUG_084, ADR-008 D19).
PROGRAM_TERM = "dumb"

#: The largest request body read: the pairing form, an action and a push
#: subscription are a few hundred bytes.
MAX_BODY = 4096

#: Where the page reads a live window's conversation (ADR-029 increment 2), and
#: the most a response may be (ADR-009 D17).
SESSION_PATH = "/api/session"
SESSION_RESPONSE_CAP = 512 << 10

#: The talk path's routes (ADR-029 D5, increment 3).
TALK_PATH = "/api/talk"
TALK_CANCEL_PATH = "/api/talk/cancel"

#: Where a device keeps or drops its push subscription (ADR-027 increment 4).
SUBSCRIBE_PATH = "/api/push/subscribe"
UNSUBSCRIBE_PATH = "/api/push/unsubscribe"

#: Seconds a connection may sit between two reads before it is closed, so a
#: client that sends a request a byte at a time holds no thread for long.
REQUEST_TIMEOUT = 10.0

#: Headers a request may carry. The page's browser sends about a dozen.
MAX_HEADERS = 40

#: Connections served at once; one more is closed without an answer.
MAX_CONNECTIONS = 32

#: A paired cookie lasts as long as browsers let one (400 days, Chrome's cap).
COOKIE_MAX_AGE = 400 * 24 * 3600

#: The first byte of a TLS connection: a handshake record. Anything else on a
#: TLS listener is plain HTTP, or nothing a browser sends.
TLS_HANDSHAKE = 0x16

#: What a plain-HTTP request to a TLS listener gets: a redirect to the https
#: name, uncached, with no body. ``{location}`` is built from the certificate's
#: name, never from the request's ``Host``.
_MOVED = (
    "HTTP/1.1 307 Temporary Redirect\r\nLocation: {location}\r\nContent-Length: 0\r\n"
    "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
)

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


def run_program(argv: list[str], timeout: float = PROGRAM_TIMEOUT) -> tuple[int, str]:
    """``argv``'s exit status and output; 127 when it cannot be started.

    The program inherits this process's environment, with ``TERM`` set to
    ``PROGRAM_TERM`` when it has none, or an empty one.
    """
    env = dict(os.environ)
    if not env.get("TERM"):
        env["TERM"] = PROGRAM_TERM
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, check=False, timeout=timeout, env=env
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


def url_for(address: str, port: int, path: str = "/", scheme: str = "http") -> str:
    host = f"[{address}]" if ":" in address else address
    return f"{scheme}://{host}:{port}{path}"


def _split_host(header: str | None) -> tuple[str, str | None] | None:
    """A ``Host`` header's host, lower case without a trailing dot, and its port."""
    if not header:
        return None
    header = header.strip().lower()
    if header.startswith("["):
        end = header.find("]")
        if end < 0:
            return None
        host, rest = header[1:end], header[end + 1:]
        port = rest[1:] if rest.startswith(":") else None
    elif header.count(":") == 1:
        host, port = header.split(":", 1)
    else:
        host, port = header, None
    return host.rstrip("."), port or None


def host_allowed(header: str | None, bound: list[str], name: str | None = None) -> bool:
    """Whether a request's ``Host`` names this server.

    Over HTTPS, the certificate's ``name`` and nothing else: an address would
    not match the certificate, so a request naming one came past a browser's
    warning. Over plain HTTP, a bound address, a MagicDNS name (``*.ts.net``),
    or, when loopback is bound, ``localhost``. Anything else is a page that
    resolved its own name to this address, and gets nothing.
    """
    split = _split_host(header)
    if split is None:
        return False
    host = split[0]
    if name is not None:
        return host == name
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


def view(loaded: Loaded, acting: bool = False,
         jobs: Iterable[Mapping[str, Any]] = (), starting: bool = False,
         chains: Mapping[str, Any] | None = None,
         push: Mapping[str, Any] | None = None,
         excerpts: Mapping[str, Mapping[str, Any]] | None = None,
         talking: bool = False, sending: bool = False,
         prompts: Mapping[str, Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """What the page draws: ``oracle/panel``'s view of the document.

    The groups, the strip's counts, the leftovers and each session's detail,
    every word from the panel's one table (ADR-009 D2, D3). With ``acting``, a
    row whose step runs gets its button; ``jobs`` are the device's recent
    actions, listed on their session, its starts under Working. With
    ``starting``, the projects a session may be started in. ``chains`` is the
    chain's read, for its cards, lines and screens (ADR-027 increment 3).
    ``push`` is this device's web push, for its Notifications row (increment 4).
    ``excerpts`` is each row's live window's last message (:func:`excerpts`);
    with ``talking`` a live window's detail offers its conversation, and with
    ``sending`` a Needs you card waiting for input offers "Reply…". ``prompts``
    is each waiting question's read (:func:`prompts`), for its card's note and
    "Answer…".
    """
    return panel.view(loaded.document, acting=acting, jobs=jobs, starting=starting,
                      chains=chains, push=push, excerpts=excerpts, talking=talking,
                      sending=sending, prompts=prompts)


def excerpts(document: Mapping[str, Any], talk: SessionTalk) -> dict[str, dict[str, Any]]:
    """Each row's live window's last message, read now, by row key.

    The windows are the panel's (:func:`panel.excerpt_hosts`); each is read
    through ``talk`` on its own, so one that fails, for any reason, shows
    nothing and costs the others nothing (ADR-029 D11). A window with no text
    from Claude has no entry.
    """
    found: dict[str, dict[str, Any]] = {}
    for key, host_id in panel.excerpt_hosts(document).items():
        try:
            last = last_message(talk.conversation(TalkHost(host_id)))
        except Exception:  # noqa: BLE001, S112 - fail closed: that window shows nothing
            continue
        if last is not None:
            found[key] = last.to_dict()
    return found


def _row(document: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    return next((r for p in document.get("projects", []) for r in p.get("rows", [])
                 if row_key(r) == key), None)


def _asked(document: Mapping[str, Any], key: str, host_id: str,
           talk: SessionTalk) -> QuestionRead | None:
    """The question the row's live window waits on, read now; ``None`` when
    its attention record says no question waits (ADR-029 D44)."""
    fp = fingerprint(_row(document, key))
    if fp is None or fp.host != host_id or fp.waiting != KIND_QUESTION:
        return None
    return read_question(talk, TalkHost(host_id, fp.tty, fp.pid))


def prompts(document: Mapping[str, Any], talk: SessionTalk) -> dict[str, dict[str, Any]]:
    """Each row whose live window waits on a question: the question's first
    line, or that it cannot be read, by row key (ADR-029 D45). Each read on
    its own: one that fails costs only its own card."""
    found: dict[str, dict[str, Any]] = {}
    for key, host_id in panel.excerpt_hosts(document).items():
        asked = _asked(document, key, host_id, talk)
        if asked is None:
            continue
        line = first_line(asked)
        found[key] = {"text": line, "unreadable": line is None}
    return found


def conversation(document: Mapping[str, Any], key: str, talk: SessionTalk,
                 after: str | None = None,
                 cap: int = SESSION_RESPONSE_CAP, sender: Talker | None = None,
                 device: Device | None = None) -> tuple[int, dict[str, Any]]:
    """``/api/session``'s status and body for the row ``key``, read now.

    Only the row's live window is read (:func:`panel.excerpt_hosts`); a row
    with none is ``live: false`` and reads nothing, and a key no row has is
    404. The body is cut to ``cap`` bytes as served, oldest turns first, and
    is ``unchanged`` with no turns when its cursor equals ``after``. With a
    ``sender`` it also says what ``device`` may send and why not, lists its
    sends to the row, and carries a talk nonce bound to this read's
    fingerprint when a send or a stop is offered (ADR-029 increments 3 and 4),
    and the question the window waits on, bound to the nonce when an answer
    is offered (increment 5).
    """
    head = panel.conversation_head(document, key)
    if head is None:
        return HTTPStatus.NOT_FOUND, {"error": "no such session"}
    host_id = panel.excerpt_hosts(document).get(key)
    read = (read_turns(talk, TalkHost(host_id)) if host_id is not None
            else Turns((), False))
    live = host_id is not None
    body: dict[str, Any] = {"row": key, "head": head, "live": live, **read.to_dict(),
                            "offered": offered(live, read.unreadable),
                            "cursor": "0" * 24}
    if sender is not None and device is not None:
        asked = _asked(document, key, host_id, talk) if host_id is not None else None
        body.update(_sending(document, key, head, live, read.unreadable, sender, device,
                             asked))
    body["turns"] = _fit(body, cap)
    body["cursor"] = cursor({"live": live, "turns": body["turns"],
                             "unreadable": body["unreadable"],
                             "pending": body.get("pending")})
    if after is not None and after == body["cursor"]:
        body = {k: v for k, v in body.items() if k not in ("turns", "unreadable", "pending")}
        body["unchanged"] = True
    return HTTPStatus.OK, body


def _sending(document: Mapping[str, Any], key: str, head: Mapping[str, Any], live: bool,
             unreadable: bool, sender: Talker, device: Device,
             asked: QuestionRead | None = None) -> dict[str, Any]:
    """What a server that sends adds to ``/api/session`` (ADR-029 D5): what
    is offered, this device's sends, the nonce, and the pending question."""
    row = _row(document, key)
    fp = fingerprint(row) if live else None
    shown = sender.offered(fp, live, unreadable, asked)
    added: dict[str, Any] = {"offered": shown, "sends": sender.sends(device.id, key),
                             "boot": sender.boot, "nonce": None,
                             "pending": pending_view(asked)}
    if ((shown["send"] or shown["interrupt"] or shown["answer"]) and fp is not None
            and row is not None):
        project = next((p for p in document.get("projects", [])
                        if row in p.get("rows", [])), {})
        name = (project.get("project") or {}).get("name") or row.get("project_name") or ""
        added["nonce"] = sender.issue(device.id, key, fp, str(row.get("project") or ""),
                                      name, str(head.get("title") or ""),
                                      pending=asked.question if shown["answer"] and asked
                                      else None)
    return added


def _fit(body: dict[str, Any], cap: int) -> list[dict[str, Any]]:
    """``body``'s turns, the oldest dropped until the body encodes to ``cap``
    bytes or fewer.

    Each turn is measured once, on its own. Dropping one shortens the body by
    that much and its separator besides, so the count never falls short.
    """
    turns = list(body["turns"])
    over = len(_encoded(body)) - cap
    start = 0
    while over > 0 and start < len(turns):
        over -= len(_encoded(turns[start]))
        start += 1
    return turns[start:]


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
    # Not no-referrer: under it a browser sends Origin: null on the page's own
    # form POST, which the origin checks refuse (BUG_079). same-origin still
    # keeps a referrer, and a /pair?code= in it, from ever leaving the origin.
    ("Referrer-Policy", "same-origin"),
    ("X-Frame-Options", "DENY"),
]


def _csp(nonce: str) -> str:
    """Only the page's own nonce-tagged script and style, and its own fetches,
    manifest and icons (ADR-009 D10) and service worker (ADR-027 increment 4):
    nothing from another origin."""
    return (
        f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        f"connect-src 'self'; manifest-src 'self'; img-src 'self'; worker-src 'self'; "
        f"form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    )


#: The service worker's own policy: it fetches nothing, and a notification's
#: icon is the page's own.
WORKER_CSP = "default-src 'none'; img-src 'self'"


def _html(status: int, text: str, nonce: str, extra: list[tuple[str, str]] | None = None):
    return Response(
        status,
        text.encode("utf-8"),
        [("Content-Type", "text/html; charset=utf-8"),
         ("Content-Security-Policy", _csp(nonce)), *_BASE_HEADERS, *(extra or [])],
    )


def _encoded(body: dict[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, default=str).encode("utf-8")


def _json(status: int, body: dict[str, Any]) -> Response:
    return Response(status, _encoded(body), [("Content-Type", "application/json"),
                                             *_BASE_HEADERS])


def _asset(content_type: str, body: bytes) -> Response:
    """The manifest or an icon: data-free, served only past the token."""
    return Response(HTTPStatus.OK, body, [("Content-Type", content_type), *_BASE_HEADERS])


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


def _same_origin(headers: Mapping[str, str], scheme: str) -> bool:
    """No ``Origin``, or one naming the host the request was sent to.

    The pairing form's rule: a missing ``Origin`` is let through there, and
    only there (ADR-008 D11).
    """
    origin = headers.get("origin")
    if origin is None:
        return True
    return _origin_names_host(origin, headers, scheme)


def _origin_names_host(origin: str, headers: Mapping[str, str], scheme: str) -> bool:
    """``origin`` is this page's: the scheme it is served over, and the ``Host``."""
    target = urlsplit(origin)
    return target.scheme == scheme and (
        target.netloc.lower() == headers.get("host", "").strip().lower()
    )


def _origin_required(headers: Mapping[str, str], scheme: str) -> bool:
    """An action's rule: an ``Origin`` naming this server, never a missing one.

    A browser sends ``Origin`` with every ``POST`` a page makes, its own
    origin's included, so a request without one did not come from the page.
    A ``Sec-Fetch-Site`` other than ``same-origin``, where a browser sends it,
    is refused as well.
    """
    origin = headers.get("origin")
    if not origin or not _origin_names_host(origin, headers, scheme):
        return False
    return headers.get("sec-fetch-site", "same-origin") == "same-origin"


def _action_fields(headers: Mapping[str, str], body: bytes,
                   subject: str = "row") -> tuple[str, str] | int:
    """The ``subject`` (the row key, or a start's project) and the nonce, else a status."""
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
    row, nonce = sent.get(subject), sent.get("nonce")
    if not isinstance(row, str) or not isinstance(nonce, str) or not row:
        return HTTPStatus.BAD_REQUEST
    return row, nonce


def _talk_fields(headers: Mapping[str, str], body: bytes) -> dict[str, Any] | int:
    """A talk request's fields, as JSON or a form, else a status."""
    if len(body) > BODY_CAP:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    text = body.decode("utf-8", "replace")
    if content_type == "application/json":
        try:
            sent = json.loads(text)
        except ValueError:
            return HTTPStatus.BAD_REQUEST
        return sent if isinstance(sent, dict) else HTTPStatus.BAD_REQUEST
    if content_type == "application/x-www-form-urlencoded":
        return {name: values[0] for name, values in parse_qs(text).items()}
    return HTTPStatus.UNSUPPORTED_MEDIA_TYPE


class WebApp:
    """Request in, response out. Reads the machine only for a paired device.

    Without ``remote`` it only reads: no line offers an action and the action
    routes are not there. With ``served`` it is the page over HTTPS: the
    certificate's name is the only ``Host``, the origin is ``https`` and the
    cookie ``Secure``. ``insecure`` is why a tailnet page is plain HTTP, shown
    on the page and the pairing form.
    """

    def __init__(
        self,
        devices: DeviceStore,
        load: Callable[[], Loaded],
        bound: list[str],
        clock: Callable[[], float] = time.time,
        nonce: Callable[[], str] = lambda: secrets.token_urlsafe(16),
        remote: RemoteActions | None = None,
        served: Served | None = None,
        insecure: str | None = None,
        push: PushBook | None = None,
        push_why: str | None = None,
        talk: SessionTalk | None = None,
        sender: Talker | None = None,
    ) -> None:
        self.devices = devices
        self.load = load
        self.bound = list(bound)
        self.clock = clock
        self.nonce = nonce
        self.remote = remote
        self.served = served
        self.insecure = insecure
        #: Web push's book, and why there is none: ``extra`` when the Mac lacks
        #: ``cryptography``. Without either, the page shows no Notifications row.
        self.push = push
        self.push_why = push_why
        #: How a live window's last message is read (ADR-029 increment 1);
        #: without one, the view carries none.
        self.talk = talk
        #: The talk path that sends (ADR-029 increment 3); without one, or
        #: without ``talk``, the page only reads.
        self.sender = sender if talk is not None else None
        self.scheme = "https" if served is not None else "http"
        # One read at a time: a phone and a laptop open at once share it.
        self._reading = threading.Lock()

    def handle(self, request: Request) -> Response:
        headers = {k.lower(): v for k, v in request.headers.items()}
        nonce = self.nonce()
        name = self.served.name if self.served is not None else None
        if not host_allowed(headers.get("host"), self.bound, name):
            return self._misdirected(headers.get("host"), name)
        target = urlsplit(request.path)
        if target.path == "/pair":
            return self._pair(request, headers, target.query, nonce)
        # The token first: an unpaired request learns nothing, not even which
        # methods exist.
        device = self.devices.verify(_token(headers))
        if device is None:
            return _html(HTTPStatus.UNAUTHORIZED, webpage.not_paired(nonce), nonce)
        acting = self.remote is not None
        starting = acting and self.remote.starter is not None  # type: ignore[union-attr]
        if acting and target.path == "/api/act":
            return self._act(request, headers, device)
        if starting and target.path == "/api/start":
            return self._start(request, headers, device)
        if self.push is not None and target.path in (SUBSCRIBE_PATH, UNSUBSCRIBE_PATH):
            return self._subscription(request, headers, device, target.path)
        if self.sender is not None and target.path in (TALK_PATH, TALK_CANCEL_PATH):
            return self._talk(request, headers, device, target.path)
        if request.method not in ("GET", "HEAD"):
            return _html(HTTPStatus.METHOD_NOT_ALLOWED, webpage.message(
                nonce, "not allowed", "Only a confirmed action is sent here."), nonce,
                [("Allow", "GET")])
        if target.path == "/":
            return _html(HTTPStatus.OK, webpage.page(nonce, panel.WORDS, warning=self.insecure),
                         nonce)
        if target.path == "/api/state":
            return self._state(device)
        if self.talk is not None and target.path == SESSION_PATH:
            return self._session(target.query, device)
        if target.path == webpage.MANIFEST_PATH:
            return _asset("application/manifest+json",
                          webpage.manifest(panel.WORDS).encode("utf-8"))
        if target.path in webpage.ICONS:
            return _asset("image/png", webpage.icon(webpage.ICONS[target.path]))
        if target.path == webpage.WORKER_PATH and self._push_state()["available"]:
            return Response(HTTPStatus.OK, webpage.service_worker().encode("utf-8"), [
                ("Content-Type", "text/javascript; charset=utf-8"),
                ("Content-Security-Policy", WORKER_CSP), *_BASE_HEADERS])
        query = parse_qs(target.query, keep_blank_values=True)
        if acting and target.path == "/api/confirm":
            row = (query.get("row") or [""])[0]
            if not row:
                return _json(HTTPStatus.BAD_REQUEST, {"error": "name a row: ?row=<key>"})
            answer = self.remote.confirm(device, row)
            return _json(answer.status, answer.body)
        if starting and target.path == "/api/start/confirm":
            project = (query.get("project") or [""])[0]
            if not project:
                return _json(HTTPStatus.BAD_REQUEST,
                             {"error": "name a project: ?project=<key>"})
            answer = self.remote.confirm_start(  # type: ignore[union-attr]
                device, project, (query.get("label") or [""])[0],
                (query.get("prompt") or [""])[0], (query.get("model") or [""])[0])
            return _json(answer.status, answer.body)
        if acting and target.path == "/api/job":
            job = self.remote.job(device, (query.get("id") or [""])[0])
            if job is None:
                return _json(HTTPStatus.NOT_FOUND, {"error": "no such action for this device"})
            return _json(HTTPStatus.OK, job)
        return _html(HTTPStatus.NOT_FOUND, webpage.message(nonce, "not found", target.path),
                     nonce)

    def _misdirected(self, header: str | None, name: str | None) -> Response:
        """421. Over HTTPS, the one place the page is: its name, on this port."""
        text = "not this server\n"
        if name is not None:
            port = (_split_host(header) or ("", None))[1]
            text = f"not this server; open https://{name}{f':{port}' if port else ''}/\n"
        return Response(HTTPStatus.MISDIRECTED_REQUEST, text.encode(),
                        [("Content-Type", "text/plain"), *_BASE_HEADERS])

    def _state(self, device: Device) -> Response:
        try:
            with self._reading:
                loaded = self.load()
        except Exception as exc:  # noqa: BLE001 - shown to the page; the next read runs
            return _json(HTTPStatus.SERVICE_UNAVAILABLE,
                         {"error": f"{type(exc).__name__}: {exc}"})
        jobs: list[dict[str, Any]] = []
        chains = None
        if self.remote is not None:
            self.remote.observe(loaded.document)
            jobs = self.remote.recent(device)
            chains = self.remote.read_chains(loaded.document)
        starting = self.remote is not None and self.remote.starter is not None
        texts = excerpts(loaded.document, self.talk) if self.talk is not None else None
        asking = prompts(loaded.document, self.talk) if self.talk is not None else None
        body = {
            "document": loaded.document,
            "view": view(loaded, acting=self.remote is not None, jobs=jobs, starting=starting,
                         chains=chains, push=self._push_device(device), excerpts=texts,
                         talking=self.talk is not None, sending=self.sender is not None,
                         prompts=asking),
            "freshness": freshness_fields(loaded, self.clock()),
            "jobs": jobs,
        }
        if self.sender is not None:
            body["boot"] = self.sender.boot
        return _json(HTTPStatus.OK, body)

    def _session(self, query: str, device: Device) -> Response:
        """``GET /api/session``: one row's conversation, read now (ADR-029 increment 2)."""
        sent = parse_qs(query, keep_blank_values=True)
        row = (sent.get("row") or [""])[0]
        if not row:
            return _json(HTTPStatus.BAD_REQUEST, {"error": "name a row: ?row=<key>"})
        try:
            with self._reading:
                loaded = self.load()
        except Exception as exc:  # noqa: BLE001 - shown to the page; the next read runs
            return _json(HTTPStatus.SERVICE_UNAVAILABLE,
                         {"error": f"{type(exc).__name__}: {exc}"})
        after = (sent.get("after") or [None])[0]
        status, body = conversation(loaded.document, row, self.talk,  # type: ignore[arg-type]
                                    after, sender=self.sender, device=device)
        return _json(status, body)

    def _talk(self, request: Request, headers: Mapping[str, str], device: Device,
              path: str) -> Response:
        """``POST /api/talk`` or ``/api/talk/cancel``: a message held for its Undo
        window, or its Undo, from this page only (ADR-029 D2, D5)."""
        if request.method != "POST":
            return _json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "POST a message"})
        if not _origin_required(headers, self.scheme):
            return _json(HTTPStatus.FORBIDDEN, {
                "error": "a message must come from this page (its Origin); nothing was sent"})
        sent = _talk_fields(headers, request.body)
        if isinstance(sent, int):
            return _json(sent, {"error": "send the message as JSON or a form, at most "
                                         f"{BODY_CAP // 1024} KiB; nothing was sent"})
        sender: Talker = self.sender  # type: ignore[assignment]
        if path == TALK_CANCEL_PATH:
            reply = sender.cancel(device, sent.get("id"))
        else:
            row = sent.get("row")
            if not isinstance(row, str) or not row:
                return _json(HTTPStatus.BAD_REQUEST, {"error": "name a row; nothing was sent"})
            reply = sender.post(device, row, str(sent.get("nonce") or ""), sent.get("kind"),
                                sent.get("text"), answers=sent.get("answers"))
        return _json(reply.status, reply.body)

    def _push_state(self) -> dict[str, Any]:
        """Whether this server offers push now: ``{available, why, key}``.

        Push needs the book (``cryptography`` on the Mac) and HTTPS (a service
        worker and the Push API need a secure context). The key is made here
        on first need, so the page always has one to subscribe with.
        """
        if self.push is None:
            return {"available": False, "why": self.push_why or "extra"}
        if self.served is None:
            return {"available": False, "why": "http"}
        try:
            return {"available": True, "key": self.push.ensure_key()}
        except StateError:
            return {"available": False, "why": "read"}

    def _push_device(self, device: Device) -> dict[str, Any] | None:
        """This device's push, for its Notifications row; ``None`` with no push at all."""
        if self.push is None and self.push_why is None:
            return None
        state = self._push_state()
        if not state["available"]:
            return state
        try:
            mine = self.push.for_device(device.id)  # type: ignore[union-attr]
        except StateError:
            return {"available": False, "why": "read"}
        return {**state, "on": mine["on"], "error": mine["error"]}

    def _subscription(self, request: Request, headers: Mapping[str, str], device: Device,
                      path: str) -> Response:
        """``POST /api/push/subscribe`` or ``unsubscribe``: this device's own setting.

        From the page's origin only, like an action. Subscribing needs push to
        be offered now; leaving never does, so a device can always turn it off.
        """
        if request.method != "POST":
            return _json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "POST a subscription"})
        if not _origin_required(headers, self.scheme):
            return _json(HTTPStatus.FORBIDDEN, {
                "error": "a subscription must come from this page (its Origin)"})
        if len(request.body) > MAX_BODY:
            return _json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "too large"})
        try:
            sent = json.loads(request.body.decode("utf-8", "replace"))
        except ValueError:
            sent = None
        if not isinstance(sent, dict):
            return _json(HTTPStatus.BAD_REQUEST, {"error": "send the subscription as JSON"})
        book: PushBook = self.push  # type: ignore[assignment]
        try:
            if path == UNSUBSCRIBE_PATH:
                endpoint = sent.get("endpoint")
                removed = book.unsubscribe(device.id,
                                           endpoint if isinstance(endpoint, str) else None)
                return _json(HTTPStatus.OK, {"removed": removed})
            if not self._push_state()["available"]:
                return _json(HTTPStatus.CONFLICT, {
                    "error": "the Mac does not offer push to this page now"})
            subscription = check_subscription(sent)
            book.subscribe(device.id, subscription, f"https://{headers.get('host', '')}")
        except PushError as exc:
            return _json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except StateError as exc:
            return _json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
        return _json(HTTPStatus.OK, {"on": True})

    def _act(self, request: Request, headers: Mapping[str, str], device: Device) -> Response:
        """``POST /api/act``: the row key and the nonce, from this page only."""
        if request.method != "POST":
            return _json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "POST a confirmed action"})
        if not _origin_required(headers, self.scheme):
            return _json(HTTPStatus.FORBIDDEN, {
                "error": "an action must come from this page (its Origin); nothing ran"})
        fields = _action_fields(headers, request.body)
        if isinstance(fields, int):
            return _json(fields, {"error": "send the row and the nonce, as JSON or a form; "
                                           "nothing ran"})
        row, nonce = fields
        answer = self.remote.act(device, row, nonce)  # type: ignore[union-attr]
        return _json(answer.status, answer.body)

    def _start(self, request: Request, headers: Mapping[str, str], device: Device) -> Response:
        """``POST /api/start``: a project's key and the nonce its confirm issued."""
        if request.method != "POST":
            return _json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "POST a confirmed start"})
        if not _origin_required(headers, self.scheme):
            return _json(HTTPStatus.FORBIDDEN, {
                "error": "a start must come from this page (its Origin); nothing ran"})
        fields = _action_fields(headers, request.body, subject="project")
        if isinstance(fields, int):
            return _json(fields, {"error": "send the project and the nonce, as JSON or a "
                                           "form; nothing ran"})
        project, nonce = fields
        answer = self.remote.start(device, project, nonce)  # type: ignore[union-attr]
        return _json(answer.status, answer.body)

    def _pair(self, request: Request, headers: Mapping[str, str], query: str,
              nonce: str) -> Response:
        if request.method == "GET":
            code = (parse_qs(query).get("code") or [""])[0]
            return _html(HTTPStatus.OK, webpage.pair_form(nonce, code, warning=self.insecure),
                         nonce)
        if request.method != "POST":
            return _html(HTTPStatus.METHOD_NOT_ALLOWED,
                         webpage.message(nonce, "not allowed", "GET or POST only."), nonce,
                         [("Allow", "GET, POST")])
        content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            return _html(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                         webpage.message(nonce, "not paired", "Send the pairing form."), nonce)
        if not _same_origin(headers, self.scheme):
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
        if self.served is not None:
            cookie += "; Secure"
        return Response(HTTPStatus.SEE_OTHER, b"",
                        [("Location", "/"), ("Set-Cookie", cookie), *_BASE_HEADERS])


# -- the socket -----------------------------------------------------------------------


def body_cap(target: str) -> int:
    """The most a request to ``target`` may send: a message's route takes
    :data:`~invariant.oracle.talk.BODY_CAP`, every other :data:`MAX_BODY`."""
    path = urlsplit(target).path
    return BODY_CAP if path in (TALK_PATH, TALK_CANCEL_PATH) else MAX_BODY


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
        if length > body_cap(self.path):
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
    served: Served | None = None

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

    def finish_request(self, request, client_address) -> None:
        """Over HTTPS, the handshake, in this connection's own thread.

        The context is read once, here, so a renewal reaches the next
        connection. A connection that does not open with a TLS handshake gets
        :data:`_MOVED` and is closed: plain HTTP is never served here. One
        that says nothing within the timeout, or fails its handshake, is
        closed without a word.
        """
        served = self.served
        if served is None:
            super().finish_request(request, client_address)
            return
        request.settimeout(self.request_timeout)
        try:
            first = request.recv(1, socket.MSG_PEEK)
            if not first:
                return
            if first[0] != TLS_HANDSHAKE:
                request.recv(MAX_BODY)
                port = self.server_address[1]
                request.sendall(_MOVED.format(location=f"https://{served.name}:{port}/")
                                .encode("ascii"))
                return
            connection = served.context.wrap_socket(request, server_side=True)
        except (OSError, ssl.SSLError):
            return
        try:
            self.RequestHandlerClass(connection, client_address, self)
        finally:
            connection.close()

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
    """One ``ThreadingHTTPServer`` per bound address, all serving one app.

    With ``served``, each speaks TLS with its certificate, read per connection.
    Everything after the port is keyword-only, and ``RecordingWeb`` takes the
    same arguments, so a caller cannot hand one option in another's place
    (BUG_083).
    """

    def __init__(
        self,
        app: WebApp,
        addresses: list[str],
        port: int,
        *,
        served: Served | None = None,
        request_timeout: float = REQUEST_TIMEOUT,
        max_connections: int = MAX_CONNECTIONS,
    ) -> None:
        self.app = app
        self.addresses = list(addresses)
        self.port = port
        self.request_timeout = request_timeout
        self.max_connections = max_connections
        self.served = served
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
                server.served = self.served
                self.servers.append(server)
                port = server.server_address[1]
        except OSError as exc:
            self.close()
            raise WebError(f"cannot listen on {address} port {port}: {exc}") from exc
        self.port = port
        return urls_for(self.addresses, port, self.served)

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


def urls_for(addresses: list[str], port: int, served: Served | None) -> list[str]:
    """Where the page is: its https name once, else each address over http."""
    if served is not None:
        return [f"https://{served.name}:{port}/"]
    return [url_for(address, port) for address in addresses]


# -- for tests ------------------------------------------------------------------------


@dataclass
class RecordingServer:
    """A server that binds nothing and records what it was asked to serve."""

    app: WebApp
    addresses: list[str]
    port: int
    served: Served | None = None
    request_timeout: float = REQUEST_TIMEOUT
    max_connections: int = MAX_CONNECTIONS
    started: bool = False
    foreground: bool = False
    background: bool = False
    closed: bool = False

    def start(self) -> list[str]:
        self.started = True
        return urls_for(self.addresses, self.port, self.served)

    def serve(self) -> None:
        self.foreground = True

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

    def _make(
        self,
        app: WebApp,
        addresses: list[str],
        port: int,
        *,
        served: Served | None = None,
        request_timeout: float = REQUEST_TIMEOUT,
        max_connections: int = MAX_CONNECTIONS,
    ) -> RecordingServer:
        """``WebServer``'s own signature, which the suite holds it to."""
        server = RecordingServer(app, list(addresses), port, served=served,
                                 request_timeout=request_timeout,
                                 max_connections=max_connections)
        self.servers.append(server)
        return server

