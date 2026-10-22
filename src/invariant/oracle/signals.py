"""What a row means for the person: alerts, and markers shown without one.

:func:`classify` is pure. It reads one scanned row and the time it is told,
never a store, a process table or the wall clock, so the watcher and the suite
ask it the same question and get the same answer.

**Alerts** are what IDEA_012 settled the oracle interrupts for: a host waiting
on the person, a gate that refused the turn, a turn gone silent, work finished
and awaiting the next step, and an error. **Markers** are shown and never
interrupt: parked, stranded, a leaked worktree, an orphan host, a host gone
without ``SessionEnd``, an idle "your turn", and a merge awaiting teardown.

**Every signal has a stable key**, ``project|subject|kind|discriminator``. The
discriminator changes exactly when the condition is a new occurrence rather
than the same one seen again — a new prompt, a different set of refusing rules,
another recorded test run, a new turn — which is what increment 4's de-dup
ledger needs to alert once per rising edge.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .scan import (
    ROW_ERROR,
    ROW_HOST,
    ROW_SESSION,
    ROW_WORKTREE,
    STAGE_LANDED,
    STAGE_REVIEWED,
    STAGE_WORKING,
    Row,
)

WAITING = "waiting"
GATE_BLOCKED = "gate_blocked"
STUCK = "stuck"
FINISHED = "finished"
ERROR = "error"

PARKED = "parked"
STRANDED = "stranded"
LEAKED_WORKTREE = "leaked_worktree"
ORPHAN_HOST = "orphan_host"
HOST_GONE = "host_gone"
YOUR_TURN = "your_turn"
TEARDOWN_PENDING = "teardown_pending"

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
INFO = "info"


#: The two waiting kinds that alert. ``idle`` is "your turn", a marker.
_ASKING = ("permission", "question")
_IDLE = "idle"
_TURN_RUNNING = "running"
_TURN_ENDED = "ended"


@dataclass(frozen=True)
class Thresholds:
    """The tunable edges. ``stuck_minutes`` is ``oracle watch --stuck-minutes``.

    A wait older than ``stale_wait_hours`` stays an alert but drops a severity:
    a host that crashed without ``SessionEnd`` and whose pid could not be
    checked would otherwise stay red for ever.
    """

    stuck_minutes: float = 20.0
    stale_wait_hours: float = 6.0


@dataclass(frozen=True)
class Signal:
    """One condition on one row."""

    kind: str
    severity: str
    alert: bool
    detail: str
    key: str
    #: Which flavour of ``kind``: ``permission`` or ``question`` for waiting,
    #: ``reviewed`` or ``ready`` for finished, ``tests``, ``degraded`` or
    #: ``scan`` for error. What the recommender branches on.
    cause: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def signal_key(row: Row, kind: str, discriminator: str) -> str:
    return f"{row.project}|{row.subject}|{kind}|{discriminator}"


def classify(row: Row, now: float, thresholds: Thresholds | None = None) -> list[Signal]:
    """Every signal ``row`` raises at ``now``, alerts first."""
    thresholds = thresholds or Thresholds()
    found: list[Signal] = []

    def add(kind, severity, alert, detail, disc="", cause=""):
        found.append(Signal(kind, severity, alert, detail, signal_key(row, kind, disc), cause))

    if row.kind == ROW_ERROR:
        add(ERROR, HIGH, True, f"cannot be read: {row.error}", "scan", "scan")
    elif row.kind == ROW_WORKTREE:
        add(LEAKED_WORKTREE, INFO, False, f"no live session owns {row.path}")
    else:
        _host_signals(row, now, thresholds, add)
        if row.kind == ROW_SESSION:
            _session_signals(row, add)
        elif row.kind == ROW_HOST:
            previous = f", last bound to {row.previous_session}" if row.previous_session else ""
            add(ORPHAN_HOST, INFO, False,
                f"a host session with no live invariant session{previous}")

    found.sort(key=lambda s: not s.alert)
    return found


def _host_signals(row: Row, now: float, thresholds: Thresholds, add) -> None:
    """What the current host's attention record says, read against its liveness."""
    host = row.current_host
    record = host.attention if host else None
    if not record:
        return
    gone = host.alive is False
    waiting = record.get("waiting") if isinstance(record.get("waiting"), dict) else None
    wait_kind = waiting.get("kind") if waiting else None

    if not gone and wait_kind in _ASKING:
        since = waiting.get("since")
        age = _age(since, now)
        stale = age is not None and age > thresholds.stale_wait_hours * 3600
        message = waiting.get("message")
        detail = f"waiting on a {wait_kind}" + (f": {message}" if message else "")
        if age is not None:
            detail += f" ({_minutes(age)})"
        add(WAITING, MEDIUM if stale else HIGH, True, detail, f"{wait_kind}@{since}", wait_kind)
    elif not gone and wait_kind == _IDLE:
        add(YOUR_TURN, INFO, False, "idle at the prompt: your turn")

    blocked = record.get("blocked")
    if isinstance(blocked, dict) and record.get("turn") == _TURN_ENDED:
        rules = sorted(r for r in (blocked.get("rules") or []) if isinstance(r, str))
        surface = blocked.get("surface") or "?"
        add(
            GATE_BLOCKED, MEDIUM, True,
            f"the turn ended refused at {surface}: {', '.join(rules) or '(no rule named)'}",
            ",".join(rules),
        )

    idle = None if row.last_activity is None else now - row.last_activity
    if (
        not gone
        and record.get("turn") == _TURN_RUNNING
        and waiting is None
        and idle is not None
        and idle > thresholds.stuck_minutes * 60
    ):
        add(STUCK, MEDIUM, True, f"a running turn, silent for {_minutes(idle)}",
            str(record.get("turn_since")))

    degraded = record.get("degraded")
    if isinstance(degraded, dict):
        add(
            ERROR, HIGH, True,
            f"the {degraded.get('event')} hook failed: {degraded.get('error')}",
            f"degraded@{degraded.get('since')}", "degraded",
        )

    if gone and not row.host_ended and not record.get("ended"):
        add(HOST_GONE, INFO, False, f"host pid {host.pid} is gone without a SessionEnd")


def _session_signals(row: Row, add) -> None:
    pipeline = row.pipeline
    run = pipeline.last_test if pipeline else None
    if (
        run
        and run.get("passed") is False
        and not run.get("read_as_red")
        and run.get("phase") != pipeline.red_phase
    ):
        add(ERROR, HIGH, True, f"the last test run failed ({run.get('failed', '?')} failed)",
            f"tests:{pipeline.test_runs}", "tests")

    if row.stage == STAGE_REVIEWED:
        add(FINISHED, LOW, True, f"{row.state}: awaiting merge", "reviewed", "reviewed")
    elif (
        row.stage == STAGE_WORKING
        and row.plan_complete
        and pipeline is not None
        and pipeline.landed
    ):
        add(FINISHED, LOW, True,
            f"plan {row.plan_done}/{row.plan_total} done and the pipeline landed: "
            "ready for review",
            "ready", "ready")
    elif row.stage == STAGE_LANDED:
        add(TEARDOWN_PENDING, INFO, False, f"{row.state}: teardown pending")

    if row.stranded:
        add(STRANDED, INFO, False, f"its worktree {row.worktree} is gone")
    elif row.host_ended:
        add(PARKED, INFO, False, "its host ended; waiting to be resumed")


def _age(since: Any, now: float) -> float | None:
    if not isinstance(since, str):
        return None
    try:
        return now - datetime.fromisoformat(since).timestamp()
    except ValueError:
        return None


def _minutes(seconds: float) -> str:
    return f"{int(seconds // 60)} min"
