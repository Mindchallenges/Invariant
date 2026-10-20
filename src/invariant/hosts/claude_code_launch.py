"""Starting a NEW Claude Code session: the one place this library spawns a host.

A sibling of ``claude_code`` and ``claude_code_telemetry`` rather than a part of
either, because it is a third relationship with the same host. ``claude_code``
*drives* a running host through hooks and installed files; the telemetry module
*reads back* what a host already wrote; this one *starts* a host that does not
exist yet. Each changes for different reasons, and a change to one must not
touch the others (ADR-005 D3).

What Claude Code offers, and what it does not, decides the shape. Its CLI names
a session (``--name``), enables Remote Control on it (``--remote-control
[name]``) and takes the first prompt as a positional argument. It has no flag
for the working directory and none for a new window, so those two are this
module's: a shell line that changes into the project's main checkout and runs
``claude``, typed into a new Terminal window by AppleScript.

**Quoting happens twice, and each layer has one owner.** The shell line is built
with ``shlex`` — every argument quoted for a POSIX shell, so a note carrying
spaces, colons, a middot, an arrow, an apostrophe or a ``$`` reaches ``claude``
byte for byte. That line is then embedded in an AppleScript string literal,
whose only escapes are the backslash and the double quote. Neither layer
re-implements the other's rules, and the suite proves the composition by
running the line through a real shell and the literal through ``osascript``.

**Nothing here decides anything.** Which session hands off, what the next one
is called and whether the note may be written down are ``domains/session``'s
questions; this module is handed the answers and turns them into a process.
The CLI injects it through one function so a test can replace it, and the
suite does so for every test that reaches the command: no test opens a window.

**The successor starts as a top-level host, not as this one's child.** The
command runs inside a Claude Code session, so its environment carries that
session's markers, and ``osascript`` — a child of it — hands them to a
Terminal.app it starts, which hands them to every window it opens afterwards
(BUG_068). An inherited ``CLAUDE_CODE_CHILD_SESSION`` turns the successor's
transcripts off, and an inherited ``COLORTERM=truecolor`` makes it draw colour
the terminal cannot render. ``HOST_MARKERS`` names them, and they are removed
twice: ``env -u`` on the typed line, which cleans the successor even under a
Terminal.app that is already polluted, and from the environment ``osascript``
is run with, which keeps a Terminal.app started by the launch clean for the
windows the user opens by hand. Everything else — the user's own variables and
``HANDOFF_ENV`` — is left as it is.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..core.errors import InvariantError

#: The binary looked up on PATH. Resolved, never assumed: a bare ``claude`` in
#: the typed line would be resolved by whatever PATH the NEW window's login
#: shell builds, which is not necessarily the one this command ran under.
CLAUDE_BINARY = "claude"

#: The variable set in front of ``claude`` naming the note the new session was
#: launched to pick up (IDEA_008). A hook subprocess inherits the environment
#: its host was started with, so ``claude_code`` reads it back on the
#: PostToolUse that brackets that host's first ``session start`` or
#: ``session resume``, and the session domain marks exactly that note claimed.
HANDOFF_ENV = "INVARIANT_HANDOFF"

#: What a running Claude Code session sets in its children's environment and a
#: new top-level session must not inherit (BUG_068). Named one by one rather
#: than matched by the ``CLAUDE_CODE_`` prefix, because the same prefix carries
#: the user's own configuration — ``CLAUDE_CODE_USE_BEDROCK``,
#: ``CLAUDE_CODE_MAX_OUTPUT_TOKENS`` and their kin — which the new window's
#: login shell re-exports and the typed line runs AFTER: a prefix strip there
#: would erase the user's settings rather than the parent's markers. ``env -u``
#: takes names, not patterns, and a shell loop over the environment is spelled
#: differently in every shell the window might run. A marker Claude Code adds
#: later leaks until it is listed here, which is the behaviour before the list
#: existed, not a new failure. ``COLORTERM`` is here because Terminal.app never
#: sets it, so in the successor's window it can only be the launcher's.
HOST_MARKERS: tuple[str, ...] = (
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_BRIDGE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_CODE_SSE_PORT",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDE_PID",
    "AI_AGENT",
    "COLORTERM",
)


@dataclass(frozen=True)
class LaunchPlan:
    """Everything a launch will do, computed before anything is done.

    ``--dry-run`` prints exactly this, so what is shown and what would run are
    one value rather than two renderings that could disagree.
    """

    #: What ``claude`` receives: the binary, the documented flags, the note.
    argv: tuple[str, ...]
    #: The directory the new session starts in — the main checkout.
    cwd: str
    #: ``cd <cwd> && env -u <marker> ... [NAME=value ...] <argv>``, quoted for
    #: a POSIX shell.
    shell_line: str
    #: The script ``osascript`` runs to type ``shell_line`` into a new window.
    applescript: str
    #: Variables set for ``claude`` alone, as ``(name, value)`` pairs. Empty
    #: where nothing has been recorded for the new session to claim.
    env: tuple[tuple[str, str], ...] = ()


class Launcher(Protocol):
    """The seam the CLI is handed. A test passes a recorder."""

    def launch(self, plan: LaunchPlan) -> None: ...


def host_platform() -> str:
    """``sys.platform``, behind a name a test can replace."""
    return sys.platform


def resolve_claude() -> str | None:
    """The ``claude`` on this process's PATH, or ``None``."""
    return shutil.which(CLAUDE_BINARY)


def applescript_string(text: str) -> str:
    """``text`` as an AppleScript string literal.

    AppleScript has two escapes inside a double-quoted literal — ``\\\\`` and
    ``\\"`` — and the backslash must be doubled FIRST, or the backslash added
    in front of each quote would itself be doubled. Everything else, including
    non-ASCII, is literal: ``osascript`` reads its ``-e`` argument as UTF-8.
    """
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def without_host_markers(environ: dict[str, str]) -> dict[str, str]:
    """``environ`` with every ``HOST_MARKERS`` name removed, nothing else."""
    return {key: value for key, value in environ.items() if key not in HOST_MARKERS}


def plan_launch(
    claude: str,
    name: str,
    note: str,
    cwd: Path | str,
    token: str | None = None,
) -> LaunchPlan:
    """The command, the shell line and the script, for one handoff.

    ``--name`` and ``--remote-control`` both carry the name, so the session is
    the same thing in the terminal title, the session picker and the Remote
    Control list. The note is the LAST argument and is positional: that is the
    initial prompt. ``--remote-control`` takes an optional value, so the name
    is given to it explicitly rather than left for the note to be read as.

    ``token`` names the recorded note, and is set as ``HANDOFF_ENV`` in front
    of ``claude`` — a prefix assignment, so it reaches that one process and the
    hooks it spawns, and not the window's shell. ``None`` sets nothing: a plan
    built before the note is recorded has no note to name.

    ``claude`` is run through ``env``, which first unsets ``HOST_MARKERS``. It
    is a program rather than shell syntax, so the line means the same in
    whichever shell the window opens, and an unset name is not an error.
    """
    argv = (claude, "--name", name, "--remote-control", name, note)
    env = ((HANDOFF_ENV, token),) if token else ()
    unset = "".join(f"-u {name} " for name in HOST_MARKERS)
    assign = "".join(f"{key}={shlex.quote(value)} " for key, value in env)
    shell_line = (
        f"cd {shlex.quote(str(cwd))} && env {unset}{assign}{shlex.join(argv)}"
    )
    applescript = "\n".join(
        [
            'tell application "Terminal"',
            "    activate",
            f"    do script {applescript_string(shell_line)}",
            "end tell",
        ]
    )
    return LaunchPlan(
        argv=argv,
        cwd=str(cwd),
        shell_line=shell_line,
        applescript=applescript,
        env=env,
    )


class TerminalLauncher:
    """Opens a new macOS Terminal window running the plan's shell line.

    ``osascript`` is given the script as ONE argument, with no shell between
    this process and it, so the only quoting in play is the two layers the plan
    already applied. It runs without ``HOST_MARKERS``: a Terminal.app that this
    call starts takes ``osascript``'s environment, and keeps it for every window
    it opens until it is quit.
    """

    def launch(self, plan: LaunchPlan) -> None:
        proc = subprocess.run(
            ["osascript", "-e", plan.applescript],
            capture_output=True,
            text=True,
            check=False,
            env=without_host_markers(dict(os.environ)),
        )
        if proc.returncode != 0:
            raise InvariantError(
                f"osascript could not open a Terminal window: "
                f"{(proc.stderr or proc.stdout).strip() or 'no reason given'}\n"
                f"  The note is recorded. Run it by hand:\n"
                f"    {plan.shell_line}"
            )
