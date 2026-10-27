"""The web page, served always: the loop behind ``oracle web --wait``.

``oracle install --web`` installs a second LaunchAgent,
``dev.invariant.oracle.web``, that runs ``oracle web --wait`` (ADR-008 D15).
A login usually comes before Tailscale is up, and the page binds only a
Tailscale address (D1), so a server that refused to start would be restarted
by launchd in a loop. :class:`WebService` waits instead (D16):

* **No tailnet, no socket.** It asks for the Tailscale addresses every
  :data:`WAIT_SECONDS` and binds nothing until one appears. The answer is
  filtered to Tailscale's ranges again here, so the loop cannot bind anything
  else whatever discovery returns.
* **It follows the tailnet.** While serving, it asks again every
  :data:`RECHECK_SECONDS`. When the addresses change it closes the listener
  and binds the new ones; when they are gone it closes and waits.
* **A failure waits too.** A port in use, or a bind refused, is logged once
  and tried again, so nothing here exits for a condition time can mend.

* **It keeps the certificate** (IDEA_026, D19-D24). With a
  :class:`~invariant.oracle.tls.Certificates`, each look at the tailnet also
  asks whether the certificate is due, and asks Tailscale for one when it is.
  A renewal is swapped into the running listener, which serves it from the
  next connection, with no restart. A first certificate, a lost one, or one
  for a new name means listening again, over HTTPS or, without one, over
  plain HTTP with a warning.

* **It restarts itself when invariant changes under it** (BUG_089). With a
  :class:`~invariant.oracle.build.BuildWatch`, each step first compares the
  installed tree with the one this process started from. Once it has moved
  and held still, the page takes no new action (:meth:`RemoteActions.close`),
  waits for the running ones to finish, and :meth:`WebService.run` returns
  with :attr:`WebService.restarting` set, so the command runs itself again
  from the installed code. Paired devices, the audit log and the certificate
  are files and survive it; a confirm not yet acted on is asked again.

It says what it is doing in ``<oracle home>/oracle/web.json`` (D17), the
watcher's derived directory: a report, not a record, removed when the loop
stops and safe to delete. :func:`web_status` reads it, with the paired-device
count and the audit log's last line, for ``oracle status`` and ``doctor``.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ..core.errors import InvariantError, StateError
from .build import BuildWatch
from .devices import DeviceStore
from .index import HomeStore
from .scan import pid_exists
from .tls import (
    RENEW_BEFORE,
    WARN_BEFORE,
    CertError,
    Certificates,
    Served,
    expires_text,
)
from .web import WebError, tailnet_addresses
from .webactions import AuditLog, RemoteActions

#: The report of the loop that serves the page, under the oracle home.
WEB_STATE_FILE = "oracle/web.json"
STATE_VERSION = 1

#: With no Tailscale address, or after a failed bind, look again this often.
WAIT_SECONDS = 10.0

#: While serving, compare the tailnet's addresses with the bound ones this often.
RECHECK_SECONDS = 30.0

#: While the installed tree is moving, or running actions drain before a
#: restart, look again this often.
BUILD_SECONDS = 5.0

#: Why an action is refused while the page drains to restart.
CLOSING = ("the oracle is restarting to pick up an updated invariant; nothing ran. "
           "Try again in a minute")

WAITING = "waiting"
SERVING = "serving"

DAY = 86400.0


class Listener(Protocol):
    def start(self) -> list[str]: ...

    def serve_in_background(self) -> None: ...

    def close(self) -> None: ...


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _print(line: str) -> None:
    print(line, flush=True)


class WebService:
    """Serve the page on the tailnet's addresses, whenever there are any.

    ``discover`` answers the addresses; ``serve(addresses, served)`` makes a
    listener for a set of them, over HTTPS when ``served`` is a certificate;
    ``wait(seconds)`` pauses and answers ``True`` to stop. Without
    ``certificates`` the page is plain HTTP and nothing is asked of Tailscale.
    With ``build``, it restarts once invariant changes under it, after
    ``remote``'s running actions have finished.
    """

    def __init__(
        self,
        discover: Callable[[], list[str]],
        serve: Callable[[list[str], Served | None], Listener],
        home: Path,
        *,
        port: int,
        wait: Callable[[float], bool],
        log: Callable[[str], None] = _print,
        clock: Callable[[], float] = time.time,
        pid: int | None = None,
        certificates: Certificates | None = None,
        build: BuildWatch | None = None,
        remote: RemoteActions | None = None,
    ) -> None:
        self.discover = discover
        self.serve = serve
        self.port = port
        self.wait = wait
        self.log = log
        self.clock = clock
        self.pid = os.getpid() if pid is None else pid
        self.store = HomeStore(Path(home))
        self.certificates = certificates
        self.server: Listener | None = None
        self.bound: list[str] = []
        self.urls: list[str] = []
        #: The certificate to serve, when there is one; ``listening`` is the one
        #: the open listener was made with.
        self.served: Served | None = None
        self.listening: Served | None = None
        self._said: tuple[Any, ...] | None = None
        self._reported: tuple[Any, ...] | None = None
        self._cert_said: str | None = None
        self.build = build
        self.remote = remote
        #: Set when :meth:`run` returned because invariant changed under it.
        self.restarting = False
        self._draining: int | None = None

    def run(self) -> None:
        """Step, then wait, until ``wait`` says stop or a restart is due.

        The listener is closed and the report removed on the way out.
        """
        try:
            while True:
                pause = self.step()
                if self.restarting or self.wait(pause):
                    return
        finally:
            self.close()

    def step(self) -> float:
        """One look at the tailnet and the certificate; the seconds until the next."""
        if self._restart_due():
            return BUILD_SECONDS
        pause = self._look()
        if self.build is not None and self.build.pending:
            return min(pause, BUILD_SECONDS)
        return pause

    def _restart_due(self) -> bool:
        """Whether invariant changed under this process: drain, then restart."""
        if self.build is None:
            return False
        self.build.check()
        if not self.build.restart_due():
            return False
        running = self.remote.close(CLOSING) if self.remote is not None else 0
        if running:
            if running != self._draining:
                self.log(f"invariant changed under this page; restarting once {running} "
                         f"running action{'s' if running != 1 else ''} finish")
            self._draining = running
            return True
        self.log("invariant changed under this page; restarting to run the installed code")
        self.restarting = True
        return True

    def _look(self) -> float:
        found = self._addresses()
        if found:
            self._certificate()
        if self.server is not None:
            if found == self.bound and self.served is self.listening:
                # Steady: only a renewed certificate is news, and it is reported.
                self._enter(SERVING, None, "")
                return RECHECK_SECONDS
            if not found:
                self.log("the tailnet went away; closing the page until it is back")
            elif found != self.bound:
                self.log(f"the tailnet addresses changed ({', '.join(self.bound)} -> "
                         f"{', '.join(found)}); listening again")
            elif self.served is not None:
                self.log(f"listening again over https as {self.served.name}")
            else:
                self.log("listening again over plain http: no certificate to serve")
            self._stop()
        if not found:
            self._enter(WAITING, None, "no Tailscale address on this Mac; waiting for one, "
                        f"looking every {WAIT_SECONDS:g} s. The page is served only on "
                        "the tailnet.")
            return WAIT_SECONDS
        served = self.served
        server = self.serve(found, served)
        try:
            urls = server.start()
        except WebError as exc:
            self._enter(WAITING, str(exc), f"{exc}; trying again every {WAIT_SECONDS:g} s")
            return WAIT_SECONDS
        server.serve_in_background()
        self.server, self.bound, self.urls, self.listening = server, found, urls, served
        self._enter(SERVING, None, "\n".join(f"serving the oracle on {url}" for url in urls))
        return RECHECK_SECONDS

    def close(self) -> None:
        """Stop listening and take the report down, if it is still this process's."""
        self._stop()
        try:
            report = self.store.read(WEB_STATE_FILE)
        except StateError:
            report = None
        if isinstance(report, dict) and report.get("pid") == self.pid:
            self.store.delete(WEB_STATE_FILE)

    # -- internals ----------------------------------------------------------

    def _addresses(self) -> list[str]:
        try:
            return tailnet_addresses(self.discover())
        except Exception as exc:  # noqa: BLE001 - discovery failing is "no tailnet yet"
            self.log(f"could not ask for the Tailscale addresses: {exc}")
            return []

    def _certificate(self) -> None:
        """Ask for a certificate when one is due, and decide which one to serve.

        The same name renewed is swapped into :attr:`served` in place, so the
        listener serves it from its next connection. A first one, another
        name, or none at all replaces :attr:`served`, which the caller turns
        into listening again.
        """
        if self.certificates is None:
            return
        certificate = self.certificates.ensure()
        served = self.served
        try:
            if certificate is None:
                self.served = None
            elif served is not None and certificate.name == served.name:
                if certificate.fingerprint != served.certificate.fingerprint:
                    served.renew(certificate, self.certificates.context(certificate))
                    self.log(f"renewed the certificate for {certificate.name}; it expires "
                             f"{expires_text(certificate.not_after)}")
            else:
                self.served = Served(certificate, self.certificates.context(certificate))
                self.log(f"the certificate for {certificate.name} expires "
                         f"{expires_text(certificate.not_after)}")
        except CertError:
            # A context that will not load: keep serving what already loaded.
            self.served = served
        error = self.certificates.error
        if error != self._cert_said:
            self._cert_said = error
            if error and self.served is None:
                self.log(f"no certificate, so the page is served over plain http: {error}")
            elif error:
                self.log(f"could not renew the certificate: {error}")

    def _stop(self) -> None:
        if self.server is not None:
            self.server.close()
        self.server, self.bound, self.urls, self.listening = None, [], [], None

    def _tls_report(self) -> dict[str, Any] | None:
        if self.certificates is None:
            return None
        served = self.listening
        return {
            "name": served.name if served is not None else None,
            "not_after": served.not_after if served is not None else None,
            "expires": expires_text(served.not_after) if served is not None else None,
            "error": self.certificates.error,
        }

    def _enter(self, state: str, error: str | None, message: str) -> None:
        """Report ``state`` when it, or the certificate served, changed; log the first."""
        said = (state, error, tuple(self.bound))
        tls = self._tls_report()
        reported = (said, json.dumps(tls, sort_keys=True))
        if reported == self._reported:
            return
        if said != self._said and message:
            self.log(message)
        self._said, self._reported = said, reported
        report = {
            "version": STATE_VERSION, "pid": self.pid, "state": state,
            "addresses": list(self.bound), "port": self.port, "urls": list(self.urls),
            "since": _iso(self.clock()), "error": error,
            "scheme": "https" if self.listening is not None else "http",
            "certificate": tls,
            "build": self.build.started.to_dict() if self.build is not None else None,
        }
        try:
            self.store.write(WEB_STATE_FILE, report)
        except StateError as exc:
            self.log(f"could not write the report {WEB_STATE_FILE}: {exc}")


# -- what status and doctor read -----------------------------------------------------


def web_status(home: Path, alive: Callable[[int | None], bool] = pid_exists) -> dict[str, Any]:
    """The loop's report, whether its process lives, the devices and the last action."""
    home = Path(home)
    status: dict[str, Any] = {
        "path": str(home / WEB_STATE_FILE), "exists": (home / WEB_STATE_FILE).is_file(),
        "state": None, "pid": None, "running": False, "addresses": [], "port": None,
        "urls": [], "since": None, "error": None, "unreadable": None,
        "scheme": None, "certificate": None, "build": None,
    }
    try:
        report = HomeStore(home).read(WEB_STATE_FILE)
    except StateError as exc:
        report, status["unreadable"] = None, str(exc)
    if report is not None and not isinstance(report, dict):
        status["unreadable"] = f"{WEB_STATE_FILE} is not an object"
    elif isinstance(report, dict):
        for key in ("state", "pid", "addresses", "port", "urls", "since", "error", "scheme",
                    "certificate", "build"):
            if key in report:
                status[key] = report[key]
        status["running"] = bool(alive(status["pid"]))
    try:
        status["devices"], status["devices_error"] = len(DeviceStore(home).devices()), None
    except InvariantError as exc:
        status["devices"], status["devices_error"] = None, str(exc)
    status["last_action"] = AuditLog(home).last()
    return status


def page_ok(status: dict[str, Any]) -> bool:
    """Whether the page is as it should be: its process lives and nothing failed."""
    return bool(status["running"]) and not status["error"] and not status["unreadable"]


def page_text(status: dict[str, Any]) -> str:
    """One line: where it serves, or why it does not."""
    if status["unreadable"]:
        return f"cannot read the report: {status['unreadable']}"
    if not status["exists"]:
        return f"no report yet; the page has not started ({status['path']})"
    pid = status["pid"]
    if not status["running"]:
        return (f"NOT running: the process that reported last (pid {pid}) is gone; "
                f"see the web log")
    if status["state"] == SERVING:
        return f"serving {', '.join(status['urls'])} (pid {pid}, since {status['since']})"
    if status["error"]:
        return f"waiting: {status['error']} (pid {pid}, since {status['since']})"
    return f"waiting for a Tailscale address (pid {pid}, since {status['since']})"


def _certificate(status: dict[str, Any]) -> dict[str, Any] | None:
    cert = status.get("certificate")
    return cert if isinstance(cert, dict) else None


def cert_ok(status: dict[str, Any], now: float) -> bool:
    """Whether the page is served over HTTPS with time to spare.

    Fails over plain HTTP, and once fewer than two weeks remain: renewal starts
    a month ahead, so by then it has been failing for two weeks.
    """
    cert = _certificate(status)
    not_after = cert.get("not_after") if cert else None
    return (status.get("scheme") == "https" and isinstance(not_after, (int, float))
            and not_after - now > WARN_BEFORE)


def cert_text(status: dict[str, Any], now: float) -> str:
    """The certificate in one line: its name and expiry, or why there is none."""
    cert = _certificate(status)
    if cert is None:
        return "not reported"
    error = cert.get("error")
    not_after = cert.get("not_after")
    if not cert.get("name") or not isinstance(not_after, (int, float)):
        return f"NONE: serving plain http; {error or 'no certificate'}"
    left = not_after - now
    if left <= 0:
        text = f"{cert['name']} EXPIRED {cert.get('expires')}"
    else:
        text = f"{cert['name']} expires {cert.get('expires')} (in {int(left // DAY)} days)"
        if left <= RENEW_BEFORE:
            text += "; renewing"
    if error:
        text += f"; last renewal failed: {error}"
    return text


def last_action_text(entry: dict[str, Any] | None) -> str:
    """The audit log's last line, said in a few words."""
    if entry is None:
        return "none yet"
    argv = entry.get("argv")
    command = " ".join(str(a) for a in argv) if isinstance(argv, list) else "?"
    at = entry.get("at", "?")
    if entry.get("event") != "done":
        return f"{at}: {command} (still running, or the server stopped during it)"
    if entry.get("exit") is None:
        return f"{at}: {command} (launched)" if entry.get("ok") else f"{at}: {command} (failed)"
    return f"{at}: {command} (exit {entry['exit']})"
