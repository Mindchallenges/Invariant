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

It says what it is doing in ``<oracle home>/oracle/web.json`` (D17), the
watcher's derived directory: a report, not a record, removed when the loop
stops and safe to delete. :func:`web_status` reads it, with the paired-device
count and the audit log's last line, for ``oracle status`` and ``doctor``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from ..core.errors import InvariantError, StateError
from .devices import DeviceStore
from .index import HomeStore
from .scan import pid_exists
from .web import WebError, tailnet_addresses
from .webactions import AuditLog

#: The report of the loop that serves the page, under the oracle home.
WEB_STATE_FILE = "oracle/web.json"
STATE_VERSION = 1

#: With no Tailscale address, or after a failed bind, look again this often.
WAIT_SECONDS = 10.0

#: While serving, compare the tailnet's addresses with the bound ones this often.
RECHECK_SECONDS = 30.0

WAITING = "waiting"
SERVING = "serving"


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

    ``discover`` answers the addresses; ``serve`` makes a listener for a set
    of them; ``wait(seconds)`` pauses and answers ``True`` to stop.
    """

    def __init__(
        self,
        discover: Callable[[], list[str]],
        serve: Callable[[list[str]], Listener],
        home: Path,
        *,
        port: int,
        wait: Callable[[float], bool],
        log: Callable[[str], None] = _print,
        clock: Callable[[], float] = time.time,
        pid: int | None = None,
    ) -> None:
        self.discover = discover
        self.serve = serve
        self.port = port
        self.wait = wait
        self.log = log
        self.clock = clock
        self.pid = os.getpid() if pid is None else pid
        self.store = HomeStore(Path(home))
        self.server: Listener | None = None
        self.bound: list[str] = []
        self.urls: list[str] = []
        self._said: tuple[Any, ...] | None = None

    def run(self) -> None:
        """Step, then wait, until ``wait`` says stop; close on the way out."""
        try:
            while True:
                if self.wait(self.step()):
                    return
        finally:
            self.close()

    def step(self) -> float:
        """One look at the tailnet; the seconds until the next."""
        found = self._addresses()
        if self.server is not None:
            if found == self.bound:
                return RECHECK_SECONDS
            if found:
                self.log(f"the tailnet addresses changed ({', '.join(self.bound)} -> "
                         f"{', '.join(found)}); listening again")
            else:
                self.log("the tailnet went away; closing the page until it is back")
            self._stop()
        if not found:
            self._enter(WAITING, None, "no Tailscale address on this Mac; waiting for one, "
                        f"looking every {WAIT_SECONDS:g} s. The page is served only on "
                        "the tailnet.")
            return WAIT_SECONDS
        server = self.serve(found)
        try:
            urls = server.start()
        except WebError as exc:
            self._enter(WAITING, str(exc), f"{exc}; trying again every {WAIT_SECONDS:g} s")
            return WAIT_SECONDS
        server.serve_in_background()
        self.server, self.bound, self.urls = server, found, urls
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

    def _stop(self) -> None:
        if self.server is not None:
            self.server.close()
        self.server, self.bound, self.urls = None, [], []

    def _enter(self, state: str, error: str | None, message: str) -> None:
        """Report ``state``, and log it, only when something changed."""
        said = (state, error, tuple(self.bound))
        if said == self._said:
            return
        self._said = said
        self.log(message)
        report = {
            "version": STATE_VERSION, "pid": self.pid, "state": state,
            "addresses": list(self.bound), "port": self.port, "urls": list(self.urls),
            "since": _iso(self.clock()), "error": error,
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
    }
    try:
        report = HomeStore(home).read(WEB_STATE_FILE)
    except StateError as exc:
        report, status["unreadable"] = None, str(exc)
    if report is not None and not isinstance(report, dict):
        status["unreadable"] = f"{WEB_STATE_FILE} is not an object"
    elif isinstance(report, dict):
        for key in ("state", "pid", "addresses", "port", "urls", "since", "error"):
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
