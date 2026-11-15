"""Whether this process still runs the invariant that is installed (BUG_089).

The oracle's watcher and its web page are long-lived processes, and the
install is usually editable: a merge on the main checkout changes the code
and the data files under them while they keep the modules they imported when
they started. An older rule registry then reads a newer shipped profile, and
the manifest refuses it, correctly, as naming a rule "this build" does not
carry. Without this module the scan called that a project it cannot read, in
every project at once.

Two signals, both cheap enough for a five-second loop:

* **The stamp** (:func:`stamp`): the relative path, size and modification
  time of every file under the installed package directory, code and
  ``data/`` alike (``__pycache__`` left out), hashed. A :class:`BuildWatch`
  takes one when it is made, which is when the long-lived command starts, and
  compares a fresh one on every :meth:`BuildWatch.check`. About 75 files,
  under a millisecond. The package version is no signal: ``__version__`` is a
  constant in the source and a merge does not move it. A build stamp would
  need a build step, and an editable install has none.
* **The rule gaps**: the rule ids the shipped profiles on
  disk name that this process's rule registry does not carry. It needs no
  start stamp, so it holds for any process, and it names the exact failure
  the bug report saw. The profiles are parsed again only when one of them
  changes.

A changed stamp is :data:`CHANGED`, a gap is :data:`RULES`; either one is a
:class:`Skew`. Only a stamp that has changed and then held still for
:data:`SETTLE_SECONDS` makes a restart due (:meth:`BuildWatch.restart_due`),
so a checkout still writing files is not restarted into, and a restarted
process, whose stamp is the new one, is not restarted again. A gap alone
never makes a restart due: an install that disagrees with itself would
otherwise restart for ever.

:func:`reexec` is the restart itself, the same interpreter and arguments in
the same process, so launchd keeps the job and the pid it watches. The CLI
reaches it through a seam; nothing here calls it.
"""

from __future__ import annotations

import hashlib
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .. import __version__
from ..core import rules as _rules
from ..core.manifest import DATA_DIR

#: The installed package: ``src/invariant`` in an editable install.
PACKAGE_DIR = Path(__file__).resolve().parent.parent

#: The shipped enforcement profiles, whose rule ids must all be known.
PROFILES_DIR = DATA_DIR / "profiles"

#: Seconds a changed stamp must hold still before a restart is due.
SETTLE_SECONDS = 5.0

#: The installed tree moved since this process started.
CHANGED = "changed"
#: The installed profiles name rules this process's registry does not carry.
RULES = "rules"

#: The exit status of a restart that could not re-execute: launchd's
#: ``KeepAlive`` starts the agent again, which is the same restart.
EXIT_RESTART = 75


@dataclass(frozen=True)
class Stamp:
    """What an installed tree looked like: its version, digest and file count."""

    version: str
    digest: str
    files: int

    def to_dict(self) -> dict[str, Any]:
        return {"version": self.version, "digest": self.digest, "files": self.files}


def stamp(root: Path = PACKAGE_DIR) -> Stamp:
    """The installed tree under ``root``, hashed by path, size and mtime."""
    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            path = os.path.join(dirpath, name)
            try:
                info = os.stat(path)
            except OSError:
                continue
            entries.append(f"{os.path.relpath(path, root)}\0{info.st_size}\0{info.st_mtime_ns}")
    digest = hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()[:16]
    return Stamp(__version__, digest, len(entries))


def _profile_rules(directory: Path) -> set[str]:
    names: set[str] = set()
    for path in sorted(directory.glob("*.yaml")):
        try:
            raw = yaml.safe_load(path.read_text("utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        rules = raw.get("rules") if isinstance(raw, dict) else None
        if isinstance(rules, dict):
            names.update(str(rule_id) for rule_id in rules)
    return names


@dataclass(frozen=True)
class Skew:
    """This process is older than the invariant installed under it."""

    reasons: tuple[str, ...]
    started: Stamp
    installed: Stamp
    #: The rule ids the installed profiles name that this build lacks.
    gaps: tuple[str, ...] = ()

    @property
    def detail(self) -> str:
        parts = []
        if CHANGED in self.reasons:
            parts.append(f"the installed invariant changed since this process started "
                         f"({self.started.digest} -> {self.installed.digest})")
        if RULES in self.reasons:
            parts.append("the installed profiles name rules this build does not carry: "
                         + ", ".join(self.gaps))
        return "; ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {"reasons": list(self.reasons), "started": self.started.to_dict(),
                "installed": self.installed.to_dict(), "rules": list(self.gaps),
                "detail": self.detail}


class BuildWatch:
    """The stamp this process started from, compared on every :meth:`check`."""

    def __init__(
        self,
        root: Path = PACKAGE_DIR,
        profiles: Path = PROFILES_DIR,
        stamper: Callable[[Path], Stamp] = stamp,
        known: Callable[[str], object] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        settle: float = SETTLE_SECONDS,
    ) -> None:
        self.root = root
        self.profiles = profiles
        self.stamper = stamper
        self.known = known
        self.monotonic = monotonic
        self.settle = settle
        self.started = stamper(root)
        #: The changed stamp last seen, and since when it has held still.
        self._moved: Stamp | None = None
        self._since = 0.0
        self._gap_key: tuple[Any, ...] | None = None
        self._gap_names: set[str] = set()

    def check(self) -> Skew | None:
        """Look again: the skew, if this process is older than the install."""
        installed = self.stamper(self.root)
        reasons: list[str] = []
        if installed.digest != self.started.digest:
            reasons.append(CHANGED)
            if self._moved is None or self._moved.digest != installed.digest:
                self._moved, self._since = installed, self.monotonic()
        else:
            self._moved = None
        gaps = self._gaps()
        if gaps:
            reasons.append(RULES)
        return Skew(tuple(reasons), self.started, installed, gaps) if reasons else None

    @property
    def pending(self) -> bool:
        """The tree has moved, and the last :meth:`check` saw it moving still."""
        return self._moved is not None and not self.restart_due()

    def restart_due(self) -> bool:
        """The tree moved, and has held still since for :attr:`settle` seconds."""
        return self._moved is not None and self.monotonic() - self._since >= self.settle

    def _gaps(self) -> tuple[str, ...]:
        try:
            key = tuple((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                        for p in sorted(self.profiles.glob("*.yaml")))
        except OSError:
            key = None
        if key is None or key != self._gap_key:
            self._gap_key, self._gap_names = key, _profile_rules(self.profiles)
        known = self.known or _rules.get_rule
        return tuple(sorted(r for r in self._gap_names if known(r) is None))


def behind(reported: Any, installed: Stamp) -> bool | None:
    """Whether a process that reported ``reported`` runs an older tree.

    ``None`` when nothing was reported at all. A report with no stamp is
    older: every build that writes reports stamps them.
    """
    if reported is None:
        return None
    if not isinstance(reported, dict) or not reported.get("digest"):
        return True
    return reported["digest"] != installed.digest


def build_text(reported: Any, installed: Stamp, who: str) -> str:
    """One line on whether ``who`` runs the installed invariant."""
    older = behind(reported, installed)
    if older is None:
        return "not reported"
    if not older:
        return f"current ({installed.digest})"
    started = reported.get("digest") if isinstance(reported, dict) else None
    since = f"started from {started}" if started else "it reports no build stamp"
    return (f"OLDER than the installed invariant ({since}, installed {installed.digest}): "
            f"{who} restarts itself once it notices; 'invariant oracle install' "
            f"restarts it now")


def reexec(
    argv: Sequence[str] | None = None,
    execv: Callable[[str, list[str]], Any] = os.execv,
    executable: str | None = None,
) -> int:
    """Run this command again in this process, from the installed code.

    Returns only when the exec failed: :data:`EXIT_RESTART`, so an agent's
    ``KeepAlive`` starts it again instead.
    """
    original = list(getattr(sys, "orig_argv", None) or [sys.executable, *sys.argv])
    args = list(argv) if argv is not None else original[1:]
    program = executable or sys.executable
    sys.stdout.flush()
    sys.stderr.flush()
    try:
        execv(program, [program, *args])
    except OSError as exc:
        print(f"could not restart ({exc}); exiting for launchd to start it again",
              file=sys.stderr, flush=True)
    return EXIT_RESTART
