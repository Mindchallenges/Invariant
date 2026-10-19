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
"""

from __future__ import annotations

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
    #: ``cd <cwd> && <argv>``, quoted for a POSIX shell.
    shell_line: str
    #: The script ``osascript`` runs to type ``shell_line`` into a new window.
    applescript: str


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


def plan_launch(claude: str, name: str, note: str, cwd: Path | str) -> LaunchPlan:
    """The command, the shell line and the script, for one handoff.

    ``--name`` and ``--remote-control`` both carry the name, so the session is
    the same thing in the terminal title, the session picker and the Remote
    Control list. The note is the LAST argument and is positional: that is the
    initial prompt. ``--remote-control`` takes an optional value, so the name
    is given to it explicitly rather than left for the note to be read as.
    """
    argv = (claude, "--name", name, "--remote-control", name, note)
    shell_line = f"cd {shlex.quote(str(cwd))} && {shlex.join(argv)}"
    applescript = "\n".join(
        [
            'tell application "Terminal"',
            "    activate",
            f"    do script {applescript_string(shell_line)}",
            "end tell",
        ]
    )
    return LaunchPlan(argv=argv, cwd=str(cwd), shell_line=shell_line, applescript=applescript)


class TerminalLauncher:
    """Opens a new macOS Terminal window running the plan's shell line.

    ``osascript`` is given the script as ONE argument, with no shell between
    this process and it, so the only quoting in play is the two layers the plan
    already applied.
    """

    def launch(self, plan: LaunchPlan) -> None:
        proc = subprocess.run(
            ["osascript", "-e", plan.applescript],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise InvariantError(
                f"osascript could not open a Terminal window: "
                f"{(proc.stderr or proc.stdout).strip() or 'no reason given'}\n"
                f"  The note is recorded. Run it by hand:\n"
                f"    {plan.shell_line}"
            )
