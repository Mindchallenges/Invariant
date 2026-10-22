"""What the oracle's dashboard shows, with no toolkit in sight.

The Textual app (``oracle/tui``) draws; this module decides. It answers two
questions, both pure apart from reading one file:

* **Where the document comes from.** :func:`load` reads the watcher's
  snapshot while it is fresh — :func:`~invariant.oracle.watcher.snapshot_status`
  says so, the rule ``oracle status`` reports — and otherwise runs the scan it
  is handed, in-process, behind a "watcher not running" banner. Both are the
  :func:`~invariant.oracle.recommend.assess` document, so the dashboard and
  ``oracle list --json`` cannot disagree about a row.
* **How each row becomes a line.** :func:`lines` puts every live session
  first with its hosts nested under it, current first, then the orphan hosts,
  the leaked worktrees and the error rows, project by project. Each line has a
  tone: red for a high alert (a host waiting on a prompt), amber for any other
  alert, dim for markers only.

Nothing here imports Textual, so all of it is tested without a terminal.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .recommend import row_label
from .scan import ROW_ERROR, ROW_HOST, ROW_SESSION, ROW_WORKTREE
from .signals import HIGH
from .watcher import DEFAULT_INTERVAL, STALE_INTERVALS, snapshot_status

SOURCE_SNAPSHOT = "snapshot"
SOURCE_SCAN = "scan"

RED = "red"
AMBER = "amber"
DIM = "dim"
PLAIN = ""

#: The flag a row is shown with, in ``oracle list`` and here alike.
FLAG_ALERT = "ALERT"
FLAG_NOTE = "note"
FLAG_OK = "ok"

#: Live sessions first, then what no session claims, then what cannot be read.
_KIND_ORDER = {ROW_SESSION: 0, ROW_HOST: 1, ROW_WORKTREE: 2, ROW_ERROR: 3}


@dataclass(frozen=True)
class Loaded:
    """One read of the machine, and where it came from."""

    document: dict[str, Any]
    source: str
    #: Seconds between refreshes: the snapshot's own interval, when it has one.
    interval: float
    #: Empty while a watcher is behind the snapshot.
    banner: str = ""


def load(home: Path, now: float, scan: Callable[[], dict[str, Any]]) -> Loaded:
    """The watcher's snapshot if it is fresh, else ``scan()`` behind a banner."""
    status = snapshot_status(home, now)
    interval = status["interval"] or DEFAULT_INTERVAL
    if not status["exists"]:
        reason = "no snapshot has been written"
    elif status["stale"]:
        reason = (
            f"its snapshot is {status['age']:.0f}s old, more than "
            f"{STALE_INTERVALS} intervals"
        )
    else:
        document = _read(Path(status["path"]))
        if document is not None:
            return Loaded(document, SOURCE_SNAPSHOT, interval)
        reason = "its snapshot cannot be read"
    return Loaded(
        scan(),
        SOURCE_SCAN,
        interval,
        banner=(
            f"watcher not running: {reason}; scanned in-process. "
            f"'invariant oracle install' keeps one running."
        ),
    )


def _read(path: Path) -> dict[str, Any] | None:
    try:
        document = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(document, dict) or not isinstance(document.get("projects"), list):
        return None
    return document


# -- tones ---------------------------------------------------------------------


def signal_tone(signal: dict[str, Any]) -> str:
    if not signal.get("alert"):
        return DIM
    return RED if signal.get("severity") == HIGH else AMBER


def row_tone(row: dict[str, Any]) -> str:
    tones = {signal_tone(s) for s in row.get("signals") or []}
    for tone in (RED, AMBER, DIM):
        if tone in tones:
            return tone
    return PLAIN


def status_flag(row: dict[str, Any]) -> str:
    signals = row.get("signals") or []
    if any(s.get("alert") for s in signals):
        return FLAG_ALERT
    return FLAG_NOTE if signals else FLAG_OK


# -- lines ---------------------------------------------------------------------


@dataclass(frozen=True)
class Line:
    """One line of the table."""

    key: str
    project: str
    subject: str
    stage: str
    status: str
    #: Each signal as ``(text, tone)``, alerts first, as ``classify`` orders them.
    signals: tuple[tuple[str, str], ...]
    next: str
    tone: str
    #: The assessed row this line acts for. A nested host line acts for its
    #: session, so it carries the session's row.
    row: dict[str, Any]
    nested: bool = False


def next_text(advice: dict[str, Any] | None) -> str:
    if not advice:
        return ""
    argv = advice.get("argv") or []
    return shlex.join(argv) if argv else advice.get("label", "")


def stage_text(row: dict[str, Any]) -> str:
    kind = row.get("kind")
    if kind == ROW_HOST:
        return "orphan host"
    if kind == ROW_WORKTREE:
        return "leaked worktree"
    if kind == ROW_ERROR:
        return "unreadable"
    stage = row.get("stage") or ""
    pipeline = row.get("pipeline")
    if pipeline:
        return f"{stage} · {pipeline['methodology']}:{pipeline['state']}"
    return stage


def host_status(host: dict[str, Any]) -> str:
    alive = host.get("alive")
    said = "alive" if alive is True else "gone" if alive is False else "pid unknown"
    waiting = (host.get("attention") or {}).get("waiting")
    if waiting:
        said += f", waiting: {waiting.get('kind')}"
    return said


def lines(document: dict[str, Any]) -> list[Line]:
    """Every row of ``document`` as table lines, sessions' hosts nested."""
    out: list[Line] = []
    for project in document.get("projects", []):
        entry = project["project"]
        name = entry.get("name", "")
        rows = sorted(project.get("rows", []), key=lambda r: _KIND_ORDER.get(r.get("kind"), 9))
        for position, row in enumerate(rows):
            key = f"{entry.get('key', name)}#{position}"
            out.append(
                Line(
                    key=key,
                    project=name,
                    subject=row_label(row),
                    stage=stage_text(row),
                    status=status_flag(row),
                    signals=tuple(
                        (f"{s['kind']}: {s['detail']}", signal_tone(s))
                        for s in row.get("signals") or []
                    ),
                    next=next_text(row.get("recommendation")),
                    tone=row_tone(row),
                    row=row,
                )
            )
            if row.get("kind") != ROW_SESSION:
                continue
            for host in row.get("hosts") or []:
                out.append(
                    Line(
                        key=f"{key}/{host['host_session_id']}",
                        project="",
                        subject=f"  └ host {host['host_session_id']}",
                        stage="current host" if host.get("current") else "earlier host",
                        status=host_status(host),
                        signals=(),
                        next="",
                        tone=DIM,
                        row=row,
                        nested=True,
                    )
                )
    return out
