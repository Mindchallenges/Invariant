"""The next command for a row: the "oracle" in Session Oracle.

:func:`recommend` is pure. It reads a row and the signals :func:`classify`
found on it and names ONE next step: the label a person reads, the exact
``argv`` and ``cwd`` it would run, and its kind —

* ``run``: an ``invariant`` command that can run as it stands;
* ``terminal``: one that belongs in a new host session (resuming);
* ``info``: nothing to run, only something to do.

It recommends; it never runs anything, and it never answers a prompt. A host
waiting on a permission prompt or a question is told to answer it in its own
terminal — the oracle has no business approving a tool call for anybody.

Where a command cannot name its session with ``--id`` (``pipeline gates``,
``pipeline test``), the recommendation carries ``INVARIANT_SESSION_ID`` in
``env`` instead, the explicit route session resolution already honours. Every
``argv`` here is parsed by the real CLI parser in the suite, so a flag that does
not exist cannot reach a confirm dialog.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .scan import ROW_ERROR, ROW_HOST, ROW_SESSION, ROW_WORKTREE, ProjectSnapshot, Row
from .signals import (
    ERROR,
    FINISHED,
    GATE_BLOCKED,
    HOST_GONE,
    PARKED,
    STRANDED,
    STUCK,
    TEARDOWN_PENDING,
    WAITING,
    YOUR_TURN,
    Signal,
    Thresholds,
    classify,
)

RUN = "run"
TERMINAL = "terminal"
INFO = "info"

SESSION_ENV = "INVARIANT_SESSION_ID"
CLI = "invariant"


@dataclass(frozen=True)
class Recommendation:
    """One next step, exactly as a confirm dialog would show it."""

    label: str
    argv: tuple[str, ...] = ()
    cwd: str | None = None
    kind: str = INFO
    env: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["argv"] = list(self.argv)
        return body


def recommend(row: Row, signals: list[Signal]) -> Recommendation | None:
    """The one next step for ``row``, or ``None`` when nothing is owed.

    The first condition that holds wins, in the order a person would want them
    handled: an open question before anything else, then a failure, then a
    refusal, then progress.
    """
    by_kind: dict[str, list[Signal]] = {}
    for signal in signals:
        by_kind.setdefault(signal.kind, []).append(signal)

    def has(kind: str, cause: str | None = None) -> bool:
        return any(cause is None or s.cause == cause for s in by_kind.get(kind, []))

    if has(WAITING):
        return Recommendation("answer it in its own terminal; the oracle never does")
    if row.kind == ROW_ERROR:
        return Recommendation(f"fix what stops the read: {row.error}")
    if row.kind == ROW_WORKTREE:
        return Recommendation(
            f"no session owns {row.path}; inspect it, then remove it with git if it is spent"
        )
    if row.kind == ROW_HOST:
        return _for_orphan(row)
    return _for_session(row, has)


def _for_orphan(row: Row) -> Recommendation:
    """Adopt only a host known to be running, into the only session it could mean.

    A host heard from only through its transcript may be long gone, and a
    project that just tore several sessions down has several such hosts: each
    would be told to adopt the same session.
    """
    host = row.hosts[0] if row.hosts else None
    host_id = host.host_session_id if host else ""
    if host is not None and host.alive is True and len(row.adoptable) == 1:
        target = row.adoptable[0]
        return Recommendation(
            f"bind it to {target}, the one session here with no live host",
            (CLI, "session", "adopt", host_id, "--id", target),
            row.root,
            RUN,
        )
    return Recommendation("start or resume a session from that host's own terminal")


def _for_session(row: Row, has) -> Recommendation | None:
    sid = row.session_id or ""
    here = row.worktree if row.worktree and not row.stranded else row.root
    pinned = {SESSION_ENV: sid}

    if has(ERROR, "tests"):
        return Recommendation("re-run the suite", (CLI, "pipeline", "test"), here, RUN, pinned)
    if has(GATE_BLOCKED):
        return Recommendation(
            "see which gates refused, and what clears them",
            (CLI, "pipeline", "gates"), here, RUN, pinned,
        )
    if has(STUCK):
        return Recommendation(
            "read what the session is being told", (CLI, "context", "--id", sid), here, RUN
        )
    if has(FINISHED, "reviewed"):
        return Recommendation(
            "merge it, from the main checkout",
            (CLI, "session", "merge", "--id", sid), row.root, RUN,
        )
    if has(FINISHED, "ready"):
        return Recommendation(
            "run the review gates", (CLI, "session", "review", "--id", sid), here, RUN
        )
    if has(TEARDOWN_PENDING):
        return Recommendation(
            "tear it down, from the main checkout",
            (CLI, "session", "teardown", "--id", sid), row.root, RUN,
        )
    if has(STRANDED):
        return Recommendation(
            "see what reaping would abandon, before reaping",
            (CLI, "session", "reap", "--dry-run"), row.root, RUN,
        )
    if has(PARKED):
        return Recommendation(
            "resume it in a new host session",
            (CLI, "session", "resume", "--id", sid), row.origin or row.root, TERMINAL,
        )
    if has(HOST_GONE):
        return Recommendation(
            "its host is gone without a SessionEnd; take it over in a new host session",
            (CLI, "session", "resume", "--id", sid, "--force"), row.origin or row.root, TERMINAL,
        )
    if has(ERROR, "degraded"):
        return Recommendation("check the hooks", (CLI, "doctor"), row.root, RUN)
    if has(YOUR_TURN):
        return Recommendation("your turn, in its own terminal")
    return None


def assess(
    snapshots: list[ProjectSnapshot],
    now: float,
    thresholds: Thresholds | None = None,
) -> dict[str, Any]:
    """Snapshots as one document: every row with its signals and its next step.

    What ``oracle list --json`` prints, and what the watcher writes as its
    snapshot.
    """
    projects = []
    for snapshot in snapshots:
        rows = []
        for row in snapshot.rows:
            signals = classify(row, now, thresholds)
            advice = recommend(row, signals)
            body = row.to_dict()
            body["signals"] = [s.to_dict() for s in signals]
            body["recommendation"] = advice.to_dict() if advice else None
            rows.append(body)
        projects.append(
            {
                "project": snapshot.project.to_dict(),
                "scanned_at": snapshot.scanned_at,
                "elapsed": snapshot.elapsed,
                "rows": rows,
            }
        )
    return {"now": now, "projects": projects}


def row_label(row: dict[str, Any]) -> str:
    """How a row of the :func:`assess` document is named to a person.

    One spelling for ``oracle list``'s table and the watcher's notifications,
    so the two always call a row the same thing.
    """
    if row["kind"] == ROW_SESSION:
        return f"{row['name']}  ({row['state']})"
    if row["kind"] == ROW_HOST:
        return f"host {row['hosts'][0]['host_session_id']}" if row["hosts"] else "host"
    if row["kind"] == ROW_WORKTREE:
        return f"worktree {row['path']}"
    return f"error {row['session_id']}" if row.get("session_id") else "error"
