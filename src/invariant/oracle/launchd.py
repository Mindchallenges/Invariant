"""The launchd agent that keeps ``invariant oracle watch`` running.

The oracle's watcher is useful only while it runs, so on macOS it is a user
LaunchAgent, ``~/Library/LaunchAgents/dev.invariant.oracle.plist``, loaded
into the person's GUI domain. It is the first long-lived process this library
starts, and launchd shapes it in three ways (ADR-007 D17):

* **launchd's environment is minimal.** Its ``PATH`` finds neither a
  Homebrew git nor anything a shell profile adds. So the plist names an
  absolute interpreter, runs ``-m invariant`` rather than a console script,
  and carries an explicit ``PATH`` built from where ``git`` and ``osascript``
  are found now, then the system directories. ``INVARIANT_HOME`` is pinned
  only when it was set, so an unset one keeps meaning ``~/.invariant``.
* **The interpreter is the one running now, unresolved.** A virtual
  environment's ``python`` is a symlink, and the environment is found from the
  symlink's own path; resolving it would lose the environment. An editable
  install can move, which ``doctor`` and ``oracle status`` check.
* **Replacing a loaded agent is ``bootout``, then ``bootstrap``.** launchd
  refuses to bootstrap a label that is still loaded, and ``bootout`` returns
  before the old process is gone, so the installer waits for ``print`` to
  stop finding it first.

``launchctl`` is reached only through an injected runner. The CLI passes
:func:`run_launchctl`; the suite passes a :class:`RecordingLaunchctl`, and a
guard in ``tests/conftest.py`` refuses the real program by any route.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import InvariantError

LABEL = "dev.invariant.oracle"
PLIST_NAME = f"{LABEL}.plist"

#: Where both of the watcher's output streams go, under the oracle home.
LOG_FILE = "oracle/watcher.log"

#: Absolute, because the installing shell's PATH is not what matters.
LAUNCHCTL = "/bin/launchctl"

#: The tail of the agent's PATH, after the directories git and osascript are in.
SYSTEM_PATH = ("/usr/bin", "/bin", "/usr/sbin", "/sbin")

#: The programs the watcher needs on its PATH.
NEEDED_PROGRAMS = ("git", "osascript")

#: How long ``launchctl`` may take to answer one call.
LAUNCHCTL_TIMEOUT_SECONDS = 30

#: How long a booted-out agent may take to leave, and how often to look.
SETTLE_SECONDS = 5.0
SETTLE_POLL_SECONDS = 0.1

#: What ``launchctl bootout`` exits with for a service that is not loaded.
_ESRCH = 3

#: ``(argv) -> (exit status, its text)``.
Launchctl = Callable[[list[str]], tuple[int, str]]


class LaunchdError(InvariantError):
    """launchctl refused, or the plist cannot be read or built."""


def agents_dir() -> Path:
    """The person's LaunchAgents directory."""
    return Path.home() / "Library" / "LaunchAgents"


def search_path(which: Callable[[str], str | None] = shutil.which) -> str:
    """A ``PATH`` that finds git and osascript, then the system directories."""
    dirs: list[str] = []
    for program in NEEDED_PROGRAMS:
        found = which(program)
        if found:
            dirs.append(str(Path(found).parent))
    dirs.extend(SYSTEM_PATH)
    return ":".join(dict.fromkeys(dirs))


def _interval_text(interval: float) -> str:
    return f"{interval:g}"


def plist_for(
    python: str, home: Path, interval: float, *, path: str, pin_home: bool
) -> dict[str, Any]:
    """The agent's property list: ``python -m invariant oracle watch``, kept alive."""
    if not os.path.isabs(python):
        raise LaunchdError(f"the interpreter must be an absolute path, not {python!r}")
    if not interval > 0:
        raise LaunchdError(f"the interval must be positive, not {interval:g}")
    log = str(home / LOG_FILE)
    environment = {"PATH": path}
    if pin_home:
        environment["INVARIANT_HOME"] = str(home)
    return {
        "Label": LABEL,
        "ProgramArguments": [
            python, "-m", "invariant", "oracle", "watch",
            "--interval", _interval_text(interval),
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "EnvironmentVariables": environment,
    }


def plist_bytes(plist: dict[str, Any]) -> bytes:
    """The XML property list, as launchd reads it."""
    return plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)


def run_launchctl(
    argv: list[str], run: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> tuple[int, str]:
    """Run ``launchctl``; its exit status, and stderr (else stdout) stripped."""
    try:
        proc = run(
            argv, capture_output=True, text=True, check=False,
            timeout=LAUNCHCTL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LaunchdError(f"could not run launchctl: {exc}") from exc
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


@dataclass
class LaunchdResult:
    """What an install or uninstall did, or, with ``dry_run``, would do."""

    plist_path: Path
    #: The launchctl calls that change something, in order. ``print`` is a read.
    steps: list[list[str]]
    was_loaded: bool
    dry_run: bool
    plist: dict[str, Any] | None = None
    wrote: bool = False
    removed: bool = False

    @property
    def xml(self) -> bytes:
        return plist_bytes(self.plist) if self.plist is not None else b""

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["plist_path"] = str(self.plist_path)
        return body


@dataclass
class AgentStatus:
    """Whether the agent is installed and loaded, and what it would run."""

    plist_path: Path
    installed: bool
    loaded: bool
    interpreter: str | None = None
    interpreter_exists: bool | None = None
    interval: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["plist_path"] = str(self.plist_path)
        return body


class LaunchdInstaller:
    """Writes the agent's plist and loads it with ``launchctl``, through ``run``."""

    def __init__(
        self,
        agents_dir: Path,
        uid: int,
        run: Launchctl = run_launchctl,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.agents_dir = Path(agents_dir)
        self.uid = uid
        self.run = run
        self.sleep = sleep

    @property
    def plist_path(self) -> Path:
        return self.agents_dir / PLIST_NAME

    @property
    def domain(self) -> str:
        return f"gui/{self.uid}"

    @property
    def service(self) -> str:
        return f"{self.domain}/{LABEL}"

    def _bootout_argv(self) -> list[str]:
        return [LAUNCHCTL, "bootout", self.service]

    def _bootstrap_argv(self) -> list[str]:
        return [LAUNCHCTL, "bootstrap", self.domain, str(self.plist_path)]

    def loaded(self) -> bool:
        """Whether launchd has the agent loaded: a read, ``launchctl print``."""
        return self.run([LAUNCHCTL, "print", self.service])[0] == 0

    def install(self, plist: dict[str, Any], dry_run: bool = False) -> LaunchdResult:
        """Write ``plist`` and bootstrap it, booting a loaded agent out first.

        ``dry_run`` asks launchd whether the agent is loaded, so the steps it
        reports are the ones a real install would run, and changes nothing.
        """
        was_loaded = self.loaded()
        steps = ([self._bootout_argv()] if was_loaded else []) + [self._bootstrap_argv()]
        result = LaunchdResult(self.plist_path, steps, was_loaded, dry_run, plist=plist)
        if dry_run:
            return result
        if was_loaded:
            self._bootout()
        for log in {plist.get("StandardOutPath"), plist.get("StandardErrorPath")}:
            if log:
                Path(log).parent.mkdir(parents=True, exist_ok=True)
        self._write(plist)
        result.wrote = True
        code, text = self.run(self._bootstrap_argv())
        if code != 0:
            raise LaunchdError(
                f"launchctl bootstrap refused the agent ({code}): {text or 'no reason given'}\n"
                f"  The plist is at {self.plist_path}; "
                f"'invariant oracle uninstall' removes it."
            )
        return result

    def uninstall(self, dry_run: bool = False) -> LaunchdResult:
        """Boot the agent out if it is loaded, and remove its plist."""
        was_loaded = self.loaded()
        present = self.plist_path.exists()
        steps = [self._bootout_argv()] if was_loaded else []
        result = LaunchdResult(self.plist_path, steps, was_loaded, dry_run)
        if dry_run:
            return result
        if was_loaded:
            self._bootout()
        if present:
            self.plist_path.unlink()
            result.removed = True
        return result

    def status(self) -> AgentStatus:
        status = AgentStatus(self.plist_path, self.plist_path.exists(), self.loaded())
        if not status.installed:
            return status
        try:
            arguments = self._arguments()
        except LaunchdError as exc:
            status.error = str(exc)
            return status
        if arguments:
            status.interpreter = str(arguments[0])
            status.interpreter_exists = Path(status.interpreter).exists()
        status.interval = _interval_of(arguments)
        return status

    def interpreter_check(self) -> tuple[bool, str] | None:
        """``doctor``'s row: ``None`` with no plist, else whether its interpreter exists.

        Reads the file only; launchd is not asked anything.
        """
        if not self.plist_path.exists():
            return None
        try:
            arguments = self._arguments()
        except LaunchdError as exc:
            return False, str(exc)
        if not arguments:
            return False, f"{self.plist_path} names no interpreter; run 'invariant oracle install'"
        interpreter = str(arguments[0])
        if Path(interpreter).exists():
            return True, interpreter
        return False, (
            f"{interpreter} is gone (moved or reinstalled); "
            f"run 'invariant oracle install' to point the agent at this one"
        )

    # -- internals ------------------------------------------------------

    def _arguments(self) -> list[Any]:
        try:
            plist = plistlib.loads(self.plist_path.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError) as exc:
            raise LaunchdError(f"cannot read {self.plist_path}: {exc}") from exc
        arguments = plist.get("ProgramArguments") if isinstance(plist, dict) else None
        return list(arguments) if isinstance(arguments, list) else []

    def _write(self, plist: dict[str, Any]) -> None:
        self.agents_dir.mkdir(parents=True, exist_ok=True)
        partial = self.plist_path.with_name(f".{PLIST_NAME}.tmp")
        partial.write_bytes(plist_bytes(plist))
        os.replace(partial, self.plist_path)

    def _bootout(self) -> None:
        """``bootout``, then wait until launchd no longer has the agent."""
        code, text = self.run(self._bootout_argv())
        polls = max(1, int(SETTLE_SECONDS / SETTLE_POLL_SECONDS))
        for attempt in range(polls):
            if not self.loaded():
                return
            if code != 0 and code != _ESRCH:
                raise LaunchdError(
                    f"launchctl bootout refused the agent ({code}): {text or 'no reason given'}"
                )
            if attempt + 1 < polls:
                self.sleep(SETTLE_POLL_SECONDS)
        raise LaunchdError(
            f"{self.service} is still loaded {SETTLE_SECONDS:g}s after bootout; "
            f"try again, or run 'launchctl bootout {self.service}' yourself"
        )


def _interval_of(arguments: list[Any]) -> float | None:
    try:
        return float(arguments[arguments.index("--interval") + 1])
    except (ValueError, IndexError, TypeError):
        return None


@dataclass
class RecordingLaunchctl:
    """A fake launchd for tests: records each call and keeps a loaded set.

    ``refuse`` maps a verb to the ``(status, text)`` it answers instead.
    ``linger`` is how many ``print`` calls still find an agent after its
    bootout. ``unload_on_refusal`` makes a refused bootout unload anyway, the
    way a real one does for an agent that left on its own.
    """

    calls: list[list[str]] = field(default_factory=list)
    loaded: set[str] = field(default_factory=set)
    refuse: dict[str, tuple[int, str]] = field(default_factory=dict)
    linger: int = 0
    unload_on_refusal: bool = False
    _lingering: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        self.calls.append(list(argv))
        verb, target = (argv[1], argv[-1]) if len(argv) > 2 else ("", "")
        if verb in self.refuse:
            if verb == "bootout" and self.unload_on_refusal:
                self.loaded.discard(target)
            return self.refuse[verb]
        if verb == "print":
            return self._print(target)
        if verb == "bootstrap":
            self.loaded.add(f"{argv[2]}/{LABEL}")
            return 0, ""
        if verb == "bootout":
            if target not in self.loaded:
                return _ESRCH, "Boot-out failed: 3: No such process"
            self.loaded.discard(target)
            self._lingering[target] = self.linger
            return 0, ""
        return 64, f"unrecognized subcommand: {verb}"

    def _print(self, target: str) -> tuple[int, str]:
        if self._lingering.get(target, 0) > 0:
            self._lingering[target] -= 1
            return 0, "state = exiting"
        if target in self.loaded:
            return 0, "state = running"
        return 113, f'Could not find service "{LABEL}" in domain for user'


def recording_installer(agents_dir: Path, uid: int = 501) -> LaunchdInstaller:
    """An installer over ``agents_dir`` and a :class:`RecordingLaunchctl`. Never sleeps."""
    return LaunchdInstaller(agents_dir, uid, run=RecordingLaunchctl(), sleep=lambda _: None)
