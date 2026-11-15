"""Ending a Claude window: Claude Code's seams, and the handoff's detached closer.

Two commands end a Claude window, and both end it one way,
:func:`~invariant.oracle.closehost.end_then_close_tab`: one SIGTERM, a
bounded wait, and only once Claude has gone, only on a tty and only under
Terminal.app, the tab closed when nothing but its shell is left, asked again
while it settles. ``oracle close-host`` (IDEA_027 increment 6) reaches it
after checking a leftover window again; ``session handoff`` (IDEA_009,
BUG_100) reaches it for the host that ran the handoff, once its successor is
alive.

The oracle may not import ``hosts/`` and ``hosts/`` may not import the
oracle, so the join lives here, in the CLI layer: :func:`claude_code_seams`
is the one place Claude Code's process facts and effects are put into
:class:`~invariant.oracle.closehost.HostSeams`. The handoff's closer cannot
run in the command that schedules it, which returns at once so its output is
shown; :class:`DetachedCloser` starts this module as a process of its own,
in a session of its own, and :func:`main` is that process.
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections.abc import Callable
from typing import Protocol

from ..core.errors import InvariantError
from ..hosts import claude_code_launch as launch
from ..oracle.closehost import HostSeams, end_then_close_tab, send_sigterm
from ..oracle.scan import pid_exists


def claude_code_seams() -> HostSeams:
    """Claude Code's facts and effects for ending a window: the real ones.

    Its per-process records and ``ps`` through ``ClaudeCodeProbe``, ``kill
    0``, SIGTERM, the parent chain that says whether Terminal owns the window,
    and the AppleScript that closes its tab. The suite never calls this
    outside a child process whose ``ps``, ``osascript`` and signals it fakes.
    """
    probe = launch.ClaudeCodeProbe()
    return HostSeams(
        records=probe.host_processes,
        facts=probe.process_facts,
        alive=pid_exists,
        terminate=send_sigterm,
        in_terminal=launch.runs_in_terminal_app,
        close_tab=launch.TerminalTabCloser().close,
    )


def end_handed_off_host(pid: int, delay: float, seams: HostSeams,
                        sleep: Callable[[float], None] = time.sleep) -> list[str]:
    """Wait ``delay`` seconds, then end the Claude that ran a handoff, and its tab.

    ``ps`` describes the pid at once, while the handoff that scheduled this has
    just seen it as ``claude``, and again after the wait. Only the same
    process is ended: still ``claude``, started at the same moment, on the
    same terminal. A pid reused in the meantime, or one ``ps`` cannot
    describe, is left alone and nothing is signalled. The rest is
    :func:`end_then_close_tab`, the panel's Close's own rules.
    """
    before = seams.facts(pid)
    sleep(delay)
    after = seams.facts(pid)
    if before is None or after is None or not (before.is_host and after.is_host):
        return [f"left process {pid} alone: it is not Claude any more"]
    if after.start != before.start or after.tty != before.tty:
        return [f"left process {pid} alone: it is another process than the one handed off"]
    return end_then_close_tab(pid, after.tty, seams)


class HostCloser(Protocol):
    """The seam that ends the launching host. A test passes an inert recorder."""

    def process_name(self, pid: int) -> str | None: ...

    def close(self, pid: int, delay: int) -> None: ...


class DetachedCloser:
    """Ends the launching host through this module, run as a detached process.

    The process runs in its own session (``start_new_session``), so it
    outlives this command, is not in the process group the host's tool runner
    tears down when the command returns, and is not on the terminal whose tab
    it closes. It holds none of this command's streams: an inherited stdout
    would keep the host's tool call open until the wait ended, which is the
    output being held back rather than printed first. It is this
    interpreter, so it runs this installation of invariant.
    """

    def process_name(self, pid: int) -> str | None:
        return launch.process_name(pid)

    def close(self, pid: int, delay: int) -> None:
        subprocess.Popen(
            [sys.executable, "-m", __name__, str(int(pid)), str(int(delay))],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def main(argv: list[str] | None = None,
         seams: Callable[[], HostSeams] = claude_code_seams) -> int:
    """The detached closer: ``<pid> <delay>``. Prints what it did; nobody reads it.

    Anything but two non-negative numbers, a pid above 0, is refused before
    anything is read or signalled.
    """
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or not all(a.isdigit() for a in args) or int(args[0]) <= 0:
        print("usage: host_close <pid> <delay>", file=sys.stderr)
        return 2
    pid, delay = int(args[0]), int(args[1])
    try:
        lines = end_handed_off_host(pid, delay, seams())
    except InvariantError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
