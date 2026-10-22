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
transcripts off, an inherited ``COLORTERM=truecolor`` makes it draw colour
the terminal cannot render, and an inherited ``GIT_EDITOR=true`` makes git skip
the editor (BUG_069). ``HOST_MARKERS`` names them, and they are removed
twice: ``env -u`` on the typed line, which cleans the successor even under a
Terminal.app that is already polluted, and from the environment ``osascript``
is run with, which keeps a Terminal.app started by the launch clean for the
windows the user opens by hand. Everything else — the user's own variables and
``HANDOFF_ENV`` — is left as it is.

**Once the successor is running, the old host ends (IDEA_009).** Claude Code
names its own process in ``CLAUDE_PID`` for the tools it runs, and after a
successful launch the CLI hands that pid to a ``HostCloser``. The real one
starts ``close_script``: a detached ``sleep``, a ``ps`` check that the pid
still names ``claude``, and a SIGTERM — the check at kill time, because a pid
can be reused while the script sleeps. It signals the process rather than
closing a window, so it works in any terminal and over Remote Control, and the
window stays at a shell prompt. The CLI reaches the closer through one
replaceable function, the way it reaches the launcher, and the suite replaces
it everywhere: no test signals a process.

**The note never rides the typed line (BUG_075).** ``do script`` TYPES the
line into the new window's tty before its shell has started line editing, and
a tty in canonical mode holds at most ``MAX_CANON`` bytes of one line — 1024
on macOS — so a long note was cut mid-quote and no ``claude`` ever ran. The
plan writes a *launch file* instead: a three-line ``/bin/sh`` script holding
the whole ``claude`` command, in a private directory outside the project
(``launch_dir``, mode 0700, the file 0600). The typed line only names it —
``cd <main> && env -u … INVARIANT_HANDOFF=<token> /bin/sh <launch file>`` — so
its length no longer depends on the note, and ``plan_launch`` refuses a line
that would still not fit rather than let the tty cut it. The script removes
itself first and then ``exec``-s ``claude``, so the note is gone from disk the
moment the successor holds it, the note stays ONE positional argument quoted
by ``shlex`` exactly as before, and the successor keeps the script's pid. It
is a file ``/bin/sh`` reads, not syntax the window's shell parses, so the line
means the same in zsh, bash or fish. A file nobody ran — a launch that failed
— is removed by the CLI, and any older than ``LAUNCH_FILE_TTL_SECONDS`` is
swept on the next staging.

**A launch counts only once a successor is seen alive.** Terminal accepting
the script proves nothing about what the window then runs, so the CLI asks a
``SuccessorWatch`` — a third seam, beside the launcher and the closer — for a
NEW process whose argv carries ``--name <slug>`` and ``--remote-control
<slug>``, still alive ``SUCCESSOR_GRACE_SECONDS`` after it was first seen.
Without one the handoff fails, and the launching host is never signalled.
"""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from itertools import pairwise
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
#: ``CLAUDE_EFFORT`` and ``GIT_EDITOR`` are Claude Code's exports for its own
#: tool shells (BUG_069): the first would pin the successor's effort level, and
#: ``GIT_EDITOR=true`` makes git skip the editor, so a ``git commit`` without
#: ``-m`` in the new window would finish silently. Unsetting it lets git fall
#: back to the user's own ``core.editor`` / ``VISUAL`` / ``EDITOR``.
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
    "CLAUDE_EFFORT",
    "GIT_EDITOR",
)


#: The variable naming the launching Claude Code process, which Claude Code
#: exports to the tools it runs (IDEA_009). It is also one of ``HOST_MARKERS``,
#: for the opposite reason: there it is a value the successor must not inherit,
#: here it is the process this handoff ends. Two concerns, two names, one
#: spelling — a rename on either side must not silently move the other.
HOST_PID_ENV = "CLAUDE_PID"

#: Seconds between a successful launch and the SIGTERM to the launching host.
#: The note is claimed and recorded BEFORE the launch (IDEA_008), so two things
#: are still in flight when the command returns: its output, which the host
#: shows as soon as the tool call ends, and the PostToolUse hook the host runs
#: on that call, which records it. The hook is bounded by the timeout this
#: library installs it with — 15 seconds — after which the host abandons it
#: anyway, so the delay is that budget plus a margin. A shorter one could end
#: the host while its hook is still writing; the suite pins the relation by
#: reading the installed timeout back.
CLOSE_DELAY_SECONDS = 20

#: The longest line the new window's tty accepts in canonical mode, newline
#: included: macOS ``MAX_CANON``. ``do script`` types the line before the
#: window's shell has switched its tty to line editing, so a longer one is cut
#: and the cut line runs nothing (BUG_075). ``plan_launch`` refuses to build a
#: plan whose typed line would not fit.
MAX_CANON = 1024

#: The program that runs the launch file. Named in full, and a program rather
#: than the window's own shell, so the typed line means the same whatever
#: shell the window opens.
LAUNCH_SHELL = "/bin/sh"

#: How long a launch file nobody ran may stay on disk. One the successor ran
#: has already removed itself, and one whose launch failed the CLI removes; this
#: bounds what a crash between the two leaves behind. An hour is far longer
#: than any window takes to open, and far shorter than a note stays useful.
LAUNCH_FILE_TTL_SECONDS = 3600

#: Seconds to wait for the successor ``claude`` to appear after the launch: a
#: Terminal window opening and its login shell starting, which can be slow
#: under a heavy shell profile.
SUCCESSOR_TIMEOUT_SECONDS = 15

#: Seconds a successor must stay alive after it is first seen. A ``claude``
#: that refuses its arguments or its login exits within this, and a handoff to
#: it is a handoff to nothing.
SUCCESSOR_GRACE_SECONDS = 3

#: Seconds between two reads of the process table while waiting.
SUCCESSOR_POLL_SECONDS = 0.5


@dataclass(frozen=True)
class LaunchPlan:
    """Everything a launch will do, computed before anything is done.

    ``--dry-run`` prints exactly this, so what is shown and what would run are
    one value rather than two renderings that could disagree.

    A plan cannot be built without Remote Control: ``argv`` must carry
    ``--remote-control <name>``, the way a successor is reached from the phone
    the handoff was usually started from. Construction refuses one that does
    not, so no later edit to ``plan_launch`` can drop it silently.
    """

    #: What ``claude`` receives: the binary, the documented flags, the note.
    argv: tuple[str, ...]
    #: The directory the new session starts in — the main checkout.
    cwd: str
    #: The line typed into the new window: ``cd <cwd> && env -u <marker> ...
    #: [NAME=value ...] /bin/sh <launch_file>``, quoted for a POSIX shell. It
    #: carries no note, so its length does not grow with one.
    shell_line: str
    #: The script ``osascript`` runs to type ``shell_line`` into a new window.
    applescript: str
    #: Variables set for ``claude`` alone, as ``(name, value)`` pairs. Empty
    #: where nothing has been recorded for the new session to claim.
    env: tuple[tuple[str, str], ...] = ()
    #: The session name, given to ``--name`` and ``--remote-control`` alike.
    name: str = ""
    #: The launch file ``shell_line`` runs: the whole ``claude`` command.
    launch_file: str = ""
    #: What the launch file holds. Written to ``launch_file`` only when the
    #: plan was staged.
    script: str = ""
    #: Whether ``launch_file`` was written. A preview — a dry run, a refusal —
    #: writes nothing, and names the file a real run would write.
    staged: bool = False
    #: The same command as one self-contained line, the note inline, to run by
    #: hand in a shell that is already up — where line editing, not the tty,
    #: reads the input, so its length does not matter.
    by_hand: str = ""

    def __post_init__(self) -> None:
        require_remote_control(self.argv, self.name)

    def discard(self) -> None:
        """Remove the launch file, if this plan wrote one and it is still there."""
        if self.staged and self.launch_file:
            Path(self.launch_file).unlink(missing_ok=True)


def require_remote_control(argv, name: str) -> None:
    """Refuse an argv that does not enable Remote Control under ``name``.

    The pair must be adjacent and the name non-empty: ``--remote-control``
    takes an OPTIONAL value, so a bare flag followed by anything else would
    name the session after whatever came next.
    """
    argv = tuple(argv)
    pairs = set(pairwise(argv))
    if not name or ("--remote-control", name) not in pairs:
        raise InvariantError(
            f"a handoff launch must enable Remote Control: the command carries "
            f"no '--remote-control {name or '<name>'}'"
        )


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


def launch_dir() -> Path:
    """Where launch files are written: private to this user, outside any tree.

    Under the system temporary directory — per user on macOS — and never under
    the project, so a launch file, which holds the note, cannot be committed.
    A function, so a test can point it somewhere of its own.
    """
    return Path(tempfile.gettempdir()) / f"invariant-handoff-{os.getuid()}"


def _private_launch_dir() -> Path:
    """``launch_dir()``, created mode 0700 and proved to be this user's own.

    A directory under a shared temporary root could have been made by someone
    else, or be a link to somewhere else; a note is not written into either.
    Stale launch files are swept on the way.
    """
    path = launch_dir()
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise InvariantError(
            f"refusing to write the handoff note under {path}: it is not a "
            f"directory this user owns"
        )
    os.chmod(path, 0o700)
    sweep_launch_files(path)
    return path


def sweep_launch_files(path: Path, now: float | None = None) -> list[Path]:
    """Remove launch files in ``path`` older than ``LAUNCH_FILE_TTL_SECONDS``.

    Returns what was removed. Only this module's own files — ``handoff-*.sh``
    — are touched, and a file that vanishes mid-sweep is not an error.
    """
    cutoff = (time.time() if now is None else now) - LAUNCH_FILE_TTL_SECONDS
    removed = []
    for item in path.glob("handoff-*.sh"):
        try:
            if item.stat().st_mtime < cutoff:
                item.unlink()
                removed.append(item)
        except FileNotFoundError:
            continue
    return removed


def launch_script(launch_file: str, argv: tuple[str, ...]) -> str:
    """The launch file: remove itself, then become ``claude``.

    ``rm`` comes first, so the note leaves the disk before ``claude`` starts;
    ``/bin/sh`` already holds the file open and reads the rest from that open
    file. ``exec`` keeps the pid, so the process the successor watch finds is
    the one ``/bin/sh`` became. The note is one ``shlex``-quoted positional
    argument, exactly as on the typed line before BUG_075.
    """
    return "\n".join(
        [
            "#!/bin/sh",
            f"rm -f -- {shlex.quote(launch_file)}",
            f"exec {shlex.join(argv)}",
            "",
        ]
    )


def plan_launch(
    claude: str,
    name: str,
    note: str,
    cwd: Path | str,
    token: str | None = None,
    *,
    stage: bool = True,
) -> LaunchPlan:
    """The command, the launch file, the typed line and the script, for one handoff.

    ``--name`` and ``--remote-control`` both carry the name, so the session is
    the same thing in the terminal title, the session picker and the Remote
    Control list. The note is the LAST argument and is positional: that is the
    initial prompt. ``--remote-control`` takes an optional value, so the name
    is given to it explicitly rather than left for the note to be read as.
    ``LaunchPlan`` refuses an argv without that pair.

    ``token`` names the recorded note, and is set as ``HANDOFF_ENV`` in front
    of the launch shell — a prefix assignment through ``env``, so it reaches
    that process, the ``claude`` it becomes and the hooks that spawns, and not
    the window's shell. ``None`` sets nothing: a plan built before the note is
    recorded has no note to name.

    The launch shell is run through ``env``, which first unsets
    ``HOST_MARKERS``. It is a program rather than shell syntax, so the line
    means the same in whichever shell the window opens, and an unset name is
    not an error.

    ``stage`` writes the launch file. ``False`` builds a preview that writes
    nothing and names a file a real run would create. A typed line over
    ``MAX_CANON`` — only a very long main-checkout path can make one now — is
    refused before anything is written.
    """
    argv = (claude, "--name", name, "--remote-control", name, note)
    require_remote_control(argv, name)
    env = ((HANDOFF_ENV, token),) if token else ()
    unset = "".join(f"-u {marker} " for marker in HOST_MARKERS)
    assign = "".join(f"{key}={shlex.quote(value)} " for key, value in env)
    prefix = f"cd {shlex.quote(str(cwd))} && env {unset}{assign}"
    by_hand = prefix + shlex.join(argv)

    # The preview path is exactly as long as the one ``mkstemp`` returns (its
    # random part is eight characters), so the length check below runs before
    # anything is written and holds for the staged line too.
    launch_file = launch_dir() / "handoff-XXXXXXXX.sh"
    typed = len((prefix + shlex.join((LAUNCH_SHELL, str(launch_file))) + "\n").encode())
    if typed > MAX_CANON:
        raise InvariantError(
            f"the line typed into the new window would be {typed} bytes, and a "
            f"terminal accepts {MAX_CANON}; the main checkout path is too long "
            f"to launch from. Run it by hand:\n    {by_hand}"
        )
    if stage:
        # mkstemp creates the file 0600 and exclusively: nobody else can have
        # it open, or have put a link where it lands.
        fd, raw = tempfile.mkstemp(
            prefix="handoff-", suffix=".sh", dir=_private_launch_dir()
        )
        launch_file = Path(raw)
        script = launch_script(raw, argv)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(script)
    else:
        script = launch_script(str(launch_file), argv)
    shell_line = prefix + shlex.join((LAUNCH_SHELL, str(launch_file)))

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
        name=name,
        launch_file=str(launch_file),
        script=script,
        staged=stage,
        by_hand=by_hand,
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
                f"    {plan.by_hand}"
            )


def host_pid(environ) -> int | None:
    """The launching Claude Code process named by ``HOST_PID_ENV``, or ``None``.

    Only a positive integer names a process. ``0`` and a negative value are
    refused rather than passed on, because ``kill`` reads both as a process
    GROUP — a signal to every process the user owns, in the ``-1`` case.
    """
    raw = (environ.get(HOST_PID_ENV) or "").strip()
    if not raw.isdigit():
        return None
    pid = int(raw)
    return pid if pid > 0 else None


def is_claude_process_name(name: str | None) -> bool:
    """Whether ``ps -o comm=`` output names a ``claude`` executable.

    Linux prints the bare name and macOS may print the full path, so the last
    path component is compared, exactly. ``close_script`` makes the same test
    in the shell, with a ``case`` over the same two spellings.
    """
    if not name:
        return False
    return name.strip().rsplit("/", 1)[-1] == CLAUDE_BINARY


def close_script(pid: int, delay: int) -> str:
    """The detached shell line that ends the launching host: sleep, check, TERM.

    The identity check runs immediately before the signal, not only when the
    close is scheduled: the pid could be reused in between, and a process that
    is not ``claude`` any more is left alone. ``sleep`` bounds the script's
    life, and it exits on its own whatever the check answers.
    """
    return (
        f"sleep {int(delay)}; "
        f'case "$(ps -p {int(pid)} -o comm= 2>/dev/null)" in '
        f"{CLAUDE_BINARY}|*/{CLAUDE_BINARY}) kill -TERM {int(pid)} ;; esac"
    )


class HostCloser(Protocol):
    """The seam that ends the launching host. A test passes an inert recorder."""

    def process_name(self, pid: int) -> str | None: ...

    def close(self, pid: int, delay: int) -> None: ...


class DetachedCloser:
    """Ends the launching host through a short, detached ``close_script``.

    The script runs in its own session (``start_new_session``), so it outlives
    this command and is not in the process group the host's tool runner tears
    down when the command returns. It holds none of this command's streams: an
    inherited stdout would keep the host's tool call open until the sleep
    ended, which is the output being held back rather than printed first.
    """

    def process_name(self, pid: int) -> str | None:
        try:
            proc = subprocess.run(
                ["ps", "-p", str(pid), "-o", "comm="],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
        name = proc.stdout.strip()
        return name if proc.returncode == 0 and name else None

    def close(self, pid: int, delay: int) -> None:
        subprocess.Popen(
            ["/bin/sh", "-c", close_script(pid, delay)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def carries_successor_flags(command: str, name: str) -> bool:
    """Whether a ``ps -o command=`` line is a ``claude`` named ``name`` with Remote Control.

    The line is argv joined by spaces, which a slug — no spaces by
    construction — survives. A word naming ``claude`` must come before the
    flags, so the shell that runs the launch file, a ``grep`` for the slug and
    a note that merely mentions the flags are not taken for the successor; a
    ``claude`` run by an interpreter (``node …/claude --name …``) still is.
    """
    words = command.split()
    for start, word in enumerate(words):
        if is_claude_process_name(word):
            rest = words[start + 1 :]
            pairs = set(pairwise(rest))
            return ("--name", name) in pairs and ("--remote-control", name) in pairs
    return False


def successor_pids(table: str, name: str) -> set[int]:
    """The pids in a ``ps -axo pid=,command=`` listing that carry ``name``'s flags."""
    found = set()
    for line in table.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and carries_successor_flags(command, name):
            found.add(int(pid))
    return found


class SuccessorWatch(Protocol):
    """The seam that proves a successor started. A test passes a stub."""

    def running(self, name: str) -> set[int]: ...

    def wait_for(self, name: str, exclude: set[int]) -> int | None: ...


class ProcessTableWatch:
    """Finds the successor in the process table, by the flags it was given.

    ``running`` is read BEFORE the launch, so a session of the same name that
    was already up is never mistaken for the one just launched. ``wait_for``
    polls for a new one until ``timeout``, then waits ``grace`` and reads
    again: only a process seen both times is a live successor.
    """

    def __init__(
        self,
        timeout: float = SUCCESSOR_TIMEOUT_SECONDS,
        grace: float = SUCCESSOR_GRACE_SECONDS,
        poll: float = SUCCESSOR_POLL_SECONDS,
        clock=time.monotonic,
        sleep=time.sleep,
    ) -> None:
        self.timeout = timeout
        self.grace = grace
        self.poll = poll
        self.clock = clock
        self.sleep = sleep

    def table(self) -> str:
        try:
            proc = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return ""
        return proc.stdout if proc.returncode == 0 else ""

    def running(self, name: str) -> set[int]:
        return successor_pids(self.table(), name)

    def wait_for(self, name: str, exclude: set[int]) -> int | None:
        deadline = self.clock() + self.timeout
        while True:
            seen = self.running(name) - set(exclude)
            if seen:
                break
            if self.clock() >= deadline:
                return None
            self.sleep(self.poll)
        self.sleep(self.grace)
        alive = seen & self.running(name)
        return min(alive) if alive else None
