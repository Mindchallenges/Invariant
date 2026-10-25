"""Close a leftover Claude window: ``invariant oracle close-host`` (IDEA_027 inc 6).

The panel offers "Close…" on a leftover host only when every guard holds
(``recommend``; ADR-009 D7, D15). The command the confirm runs does not take
the page's word for any of it. At the moment it runs it checks again:

* Claude Code's per-process record for the pid still exists, names this host
  session, and carries the start the confirm was made against;
* the pid is alive (``kill 0``), ``ps`` says it is ``claude``, it started at
  that start, and it runs on the terminal the confirm named;
* no live invariant session in any registered project lists the host.

Any mismatch is a plain refusal, and nothing is signalled. Then it asks
Claude to end with ONE SIGTERM and waits up to :data:`GRACE_SECONDS` for the
process to go. It never escalates to SIGKILL: a Claude that does not exit is
reported, and its window is left as it is. Only once Claude has gone, and
only for a host that ran in macOS's Terminal.app, the tab it ran in is
closed, when nothing but its shell is left there (the window, when that tab
was its only one). Anything else leaves the window open and says why.

Every fact and every effect is a seam (:class:`HostSeams`): the record
reader, ``ps``, ``kill 0``, the signal, which app owns the window, and the
AppleScript runner. The oracle may not import ``hosts/``; the CLI joins the
Claude Code ones here (ADR-007 D11), and the suite passes fakes.
"""

from __future__ import annotations

import os
import re
import signal
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..core.errors import InvariantError
from .scan import ROW_SESSION, HostProcess, ProcessFacts

#: Seconds to wait for Claude to exit after SIGTERM.
GRACE_SECONDS = 10.0

#: How often the exit is looked for while waiting.
POLL_SECONDS = 0.25

#: A host session id is a plain name before it reaches any output.
_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

#: What the run ended in, by the tab closer's answer. The last line the
#: command prints, which the panel's result sheet shows as what happened.
OUTCOMES: dict[str, str] = {
    "closed_tab": "Claude ended, and its Terminal tab closed.",
    "closed_window": "Claude ended, and its Terminal window closed.",
    "not_allowed": (
        "Claude ended; macOS has not allowed the oracle to control Terminal, so the window "
        "was left open. Allow it in System Settings > Privacy & Security > Automation "
        "(Terminal, under the app that runs the oracle), then close this tab by hand."
    ),
}

#: "Claude ended; the window was left open because …", by the reason.
LEFT_OPEN = "Claude ended; the window was left open because {why}."
_WHY_LEFT = {
    "no_tty": "it has no terminal (it runs over Remote Control or inside an editor)",
    "not_terminal": "it does not run in Terminal (another app, such as VS Code or iTerm, "
                    "owns it)",
    "unknown_app": "invariant could not tell which app owns it",
    "busy": "something else is running in its tab ({detail})",
    "no_tab": "no Terminal tab is on {tty}",
    "not_running": "no Terminal tab is on {tty}",
    "tab_not_closable": "Terminal would not close one tab of several from a script ({detail})",
    "failed": "Terminal could not be asked ({detail})",
}

#: The prefix of every refusal: nothing was signalled or closed.
NOTHING_DONE = "nothing was done: "

DID_NOT_EXIT = (
    "Claude did not exit within {seconds} s of being asked to; nothing else was done. Its "
    "window is still open, and it was not forced."
)


@dataclass(frozen=True)
class CloseRequest:
    """What the confirm agreed to close, every fact to be checked again."""

    host_id: str
    pid: int
    start: str
    tty: str | None = None


@dataclass
class HostSeams:
    """The host facts and effects closing needs. All injected."""

    #: Claude Code's per-process records, read afresh.
    records: Callable[[], Iterable[HostProcess]]
    #: ``ps`` on one pid: is it ``claude``, its start token, its tty.
    facts: Callable[[int], ProcessFacts | None]
    #: ``kill 0``: whether the pid exists.
    alive: Callable[[int], bool]
    #: Send SIGTERM to the pid.
    terminate: Callable[[int], None]
    #: Whether the pid runs under Terminal.app; ``None`` when unknown.
    in_terminal: Callable[[int], bool | None]
    #: Close the Terminal tab on a tty: ``(code, detail)``.
    close_tab: Callable[[str], tuple[str, str]]
    sleep: Callable[[float], None] = time.sleep
    monotonic: Callable[[], float] = time.monotonic


def send_sigterm(pid: int) -> None:
    """SIGTERM to one process. Never 0, a negative pid or a bool, which name a group."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise ValueError(f"not a single process: {pid!r}")
    os.kill(pid, signal.SIGTERM)


def binding_refusal(document: Mapping[str, Any], host_id: str) -> str | None:
    """Why ``host_id`` may not be closed: a live session somewhere lists it.

    ``document`` is a fresh :func:`~invariant.oracle.recommend.assess` of
    every registered project. Its session rows are the live sessions, and
    each lists every host it has had; any of them is one that may still be
    wanted, including BUG_040's host working on another repository's session.
    """
    for project in document.get("projects", []):
        for row in project.get("rows", []):
            if row.get("kind") != ROW_SESSION:
                continue
            if any(h.get("host_session_id") == host_id for h in row.get("hosts") or []):
                name = row.get("project_name") or (project.get("project") or {}).get("name")
                session = str(row.get("session_id") or "")[:8]
                return (f"a live invariant session uses this Claude window now ({name}, "
                        f"session {session})")
    return None


def _refuse(why: str) -> InvariantError:
    return InvariantError(NOTHING_DONE + why)


def _check(request: CloseRequest, seams: HostSeams, bound: Callable[[], str | None]) -> None:
    """Every guard, again, at the moment of the close. Raises on the first that fails."""
    pid = request.pid
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise _refuse(f"a process id must be a positive number, not {pid!r}")
    if not _PLAIN.fullmatch(request.host_id or ""):
        raise _refuse(f"{request.host_id!r} is not a host id")
    try:
        records = [r for r in seams.records() if r.pid == pid]
    except Exception as exc:  # an unreadable source is a refusal, never a guess
        raise _refuse(f"Claude Code's per-process records cannot be read ({exc})") from exc
    if not records:
        raise _refuse(f"Claude Code has no record of process {pid} any more")
    record = records[0]
    if record.session_id != request.host_id:
        raise _refuse(f"process {pid} now belongs to another Claude session")
    if record.start != request.start:
        raise _refuse(f"process {pid} is a newer process than the Claude window confirmed")
    if not seams.alive(pid):
        raise _refuse(f"process {pid} has already exited")
    facts = seams.facts(pid)
    if facts is None:
        raise _refuse(f"the process table cannot describe process {pid}")
    if not facts.is_host:
        raise _refuse(f"process {pid} is not Claude any more")
    if facts.start != request.start:
        raise _refuse(f"process {pid} started at another time than the Claude window "
                      f"confirmed, so it is another process")
    if facts.tty != request.tty:
        raise _refuse(f"process {pid} runs on another terminal than the one confirmed")
    why = bound()
    if why:
        raise _refuse(why)


def _wait_for_exit(pid: int, seams: HostSeams) -> bool:
    deadline = seams.monotonic() + GRACE_SECONDS
    while True:
        if not seams.alive(pid):
            return True
        if seams.monotonic() >= deadline:
            return False
        seams.sleep(POLL_SECONDS)


def _tab_outcome(request: CloseRequest, app: bool | None, seams: HostSeams) -> str:
    """The last line: the tab closed, or why the window was left open."""
    if not request.tty:
        return LEFT_OPEN.format(why=_WHY_LEFT["no_tty"])
    if app is not True:
        return LEFT_OPEN.format(why=_WHY_LEFT["not_terminal" if app is False else "unknown_app"])
    code, detail = seams.close_tab(request.tty)
    if code in OUTCOMES:
        return OUTCOMES[code]
    reason = _WHY_LEFT.get(code, _WHY_LEFT["failed"])
    why = reason.format(detail=detail or code, tty=request.tty)
    return LEFT_OPEN.format(why=why)


def close_host(request: CloseRequest, seams: HostSeams,
               bound: Callable[[], str | None]) -> list[str]:
    """End the confirmed Claude, then close its Terminal tab. The lines to print.

    ``bound`` answers whether a live session anywhere lists the host
    (:func:`binding_refusal` over a fresh read). A failed check raises before
    anything is signalled; a Claude that does not exit raises after the one
    SIGTERM, and nothing else is done.
    """
    _check(request, seams, bound)
    pid = request.pid
    app = seams.in_terminal(pid) if request.tty else None
    lines = [
        f"checked just now: process {pid} is Claude, window {request.host_id[:8]}, "
        f"started {request.start}" + (f", on {request.tty}" if request.tty else "")
    ]
    began = seams.monotonic()
    try:
        seams.terminate(pid)
    except ProcessLookupError:
        lines.append(f"process {pid} had already exited")
    except PermissionError as exc:
        raise _refuse(f"this user may not signal process {pid}") from exc
    else:
        if not _wait_for_exit(pid, seams):
            raise InvariantError(DID_NOT_EXIT.format(seconds=int(GRACE_SECONDS)))
        lines.append(f"asked Claude (process {pid}) to end; it exited after "
                     f"{seams.monotonic() - began:.1f} s")
    lines.append(_tab_outcome(request, app, seams))
    return lines
