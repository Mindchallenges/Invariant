"""What a row means for the person: alerts, and markers shown without one.

:func:`classify` is pure. It reads one scanned row and the time it is told,
never a store, a process table or the wall clock, so the watcher and the suite
ask it the same question and get the same answer.

**Alerts** are what IDEA_012 settled the oracle interrupts for: a host waiting
on the person, a gate that refused the turn, a turn gone silent, work finished
and awaiting the next step, and an error. **Markers** are shown and never
interrupt: parked, stranded, a leaked worktree, an orphan host, a host gone
without ``SessionEnd``, an idle "your turn", a merge awaiting teardown, a
host started with variables inherited from its Terminal.app, and a place in a
store's docs-lock queue. A docs-lock unit passed to a session alerts.

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
    HostView,
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
#: A store's docs lock passed units to the session by another's release, and
#: it has not looked since (ADR-014 D5, increment 4b): an alert, since a
#: session that stopped while it waited can go on.
DOCS_GRANTED = "docs_granted"
#: The session queues for a unit of its store's docs lock: a marker.
DOCS_QUEUED = "docs_queued"
#: A live host started with variables it inherited from its Terminal.app
#: (``HostView.inherited_env``, IDEA_035). A marker: it never alerts.
INHERITED_ENV = "inherited_env"

#: What the person does about :data:`INHERITED_ENV`, in the signal's detail
#: and ``doctor``'s row: a Terminal.app started afresh has a clean
#: environment, and so has every window it opens.
INHERITED_REMEDY = "quit Terminal.app fully and relaunch it from the Dock"

#: An orphan host's class, its ``orphan_host`` marker's cause (IDEA_025,
#: IDEA_027 increment 6). None of them alerts.
ORPHAN_GONE = "gone"
ORPHAN_LEFTOVER = "leftover"
ORPHAN_UNMANAGED = "unmanaged"
ORPHAN_ACTIVE = "active"
ORPHAN_UNKNOWN = "unknown"

#: Whether a host is mid-turn (:func:`turn_state`): working, paused on the
#: person, its main turn over while work it started in the background runs
#: on (BUG_094), or not mid-turn. ``busy``, ``waiting`` and ``idle`` are
#: Claude Code's own ``status`` values, the evidence that outranks the
#: attention ``turn`` when there is one.
TURN_BUSY = "busy"
TURN_WAITING = "waiting"
TURN_BACKGROUND = "background"
TURN_IDLE = "idle"

#: The ``stuck`` signal's cause when the silence is background work's.
STUCK_BACKGROUND = "background"

#: The ``waiting`` signal's cause when only the host's own status says it is
#: waiting on the person, with no prompt the hooks recorded (BUG_087).
WAITING_INPUT = "input"

HIGH = "high"
MEDIUM = "medium"
LOW = "low"
INFO = "info"


#: The two waiting kinds that alert. ``idle`` is "your turn", a marker.
_ASKING = ("permission", "question")
_IDLE = "idle"
_TURN_RUNNING = "running"
_TURN_ENDED = "ended"
_STATUSES = (TURN_BUSY, TURN_WAITING, TURN_IDLE)
#: Claude Code's status while its turn is idle and a background shell runs.
_SHELL = "shell"
_AGENT = "agent"
_COMMAND = "command"
#: The keys of :func:`background_work`'s answer, and of a row's ``background``.
_AGENTS = "agents"
_COMMANDS = "commands"


@dataclass(frozen=True)
class Thresholds:
    """The tunable edges. ``stuck_minutes`` is ``oracle watch --stuck-minutes``.

    A wait older than ``stale_wait_hours`` stays an alert but drops a severity:
    a host that crashed without ``SessionEnd`` and whose pid could not be
    checked would otherwise stay red for ever.
    """

    stuck_minutes: float = 20.0
    stale_wait_hours: float = 6.0
    #: An orphan host heard from more recently than this is ``active``, and
    #: nothing is offered to close it. It is also how long a ``running``
    #: attention turn counts as mid-turn without the host's own status
    #: (:func:`turn_state`, BUG_087).
    orphan_idle_minutes: float = 60.0


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
    #: ``scan`` for error, the orphan class for an orphan host
    #: (:func:`orphan_class`). What the recommender branches on.
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
            cause = orphan_class(row, now, thresholds)
            add(ORPHAN_HOST, INFO, False,
                f"{cause}: a host session with no live invariant session{previous}",
                cause=cause)

    found.sort(key=lambda s: not s.alert)
    return found


def turn_state(
    host: HostView | None, last_activity: float | None, now: float,
    thresholds: Thresholds | None = None,
) -> str:
    """Whether ``host`` is mid-turn: :data:`TURN_BUSY`, :data:`TURN_WAITING` on
    the person, :data:`TURN_BACKGROUND`, or :data:`TURN_IDLE`. The one answer
    the orphan class, the ``waiting`` / ``stuck`` / ``your_turn`` signals, the
    gate cache and the panel's "working" all read, so none of them can
    contradict another (BUG_087).

    1. A host known to be gone is not mid-turn: that is ``host_gone``'s.
    2. Claude Code's own ``status``, from the per-process record the scan
       joined, outranks the attention record while the host is alive by the
       record's check: ``busy``, ``waiting`` and ``idle`` mean what they say,
       and ``shell`` (its turn idle, a background shell running) is
       background work. Claude Code says ``idle`` while a background agent
       runs, so an open agent in the ledger still makes ``idle`` background
       (BUG_094). Without that check (liveness unknown) the record is not
       used.
    3. Without one, the attention record: a permission prompt or a question
       is waiting; a ``running`` turn with no idle prompt after it is busy
       while the host was heard from within ``orphan_idle_minutes``, because
       a missed Stop leaves it ``running`` for ever (no activity known is no
       proof of staleness, so such a turn still counts). Otherwise the turn
       is over, and open background work (:func:`background_work`) makes it
       background, else idle.
    """
    thresholds = thresholds or Thresholds()
    if host is None or host.alive is False:
        return TURN_IDLE
    work = background_work(host, last_activity, now, thresholds)
    if _verified(host):
        if host.status == _SHELL or (host.status == TURN_IDLE and work[_AGENTS]):
            return TURN_BACKGROUND
        return host.status
    record = host.attention if isinstance(host.attention, dict) else {}
    waiting = record.get("waiting") if isinstance(record.get("waiting"), dict) else {}
    if waiting.get("kind") in _ASKING:
        return TURN_WAITING
    stale = (last_activity is not None
             and now - last_activity >= thresholds.orphan_idle_minutes * 60)
    if waiting.get("kind") != _IDLE and record.get("turn") == _TURN_RUNNING and not stale:
        return TURN_BUSY
    return TURN_BACKGROUND if work[_AGENTS] or work[_COMMANDS] else TURN_IDLE


def background_work(
    host: HostView | None, last_activity: float | None, now: float,
    thresholds: Thresholds | None = None,
) -> dict[str, int]:
    """How much background work ``host`` is waiting on: ``{agents, commands}``.

    From the attention record's ledger (BUG_094), whose entries a launch
    opens and a completion notice closes. Claude Code absorbs a notice that
    arrives while a turn runs without firing a hook, so an entry can outlive
    its task: the ledger lapses once the host has been quiet for
    ``stuck_minutes``, and so never reads ``stuck`` on its own. No activity
    known is no proof it lapsed. A verified ``status`` answers for the
    commands, because Claude Code reads ``shell`` exactly while one runs:
    ``shell`` is at least one, anything else none.
    """
    thresholds = thresholds or Thresholds()
    counts = {_AGENTS: 0, _COMMANDS: 0}
    if host is None or host.alive is False:
        return counts
    lapsed = (last_activity is not None
              and now - last_activity >= thresholds.stuck_minutes * 60)
    record = host.attention if isinstance(host.attention, dict) else {}
    ledger = record.get("background")
    if not lapsed and isinstance(ledger, dict):
        for entry in ledger.values():
            kind = entry.get("kind") if isinstance(entry, dict) else None
            if kind == _AGENT:
                counts[_AGENTS] += 1
            elif kind == _COMMAND:
                counts[_COMMANDS] += 1
    if _verified(host):
        counts[_COMMANDS] = max(counts[_COMMANDS], 1) if host.status == _SHELL else 0
    return counts


def _verified(host: HostView) -> bool:
    """Whether Claude Code's own status speaks for ``host``: alive by the
    record's check, with a status this build knows."""
    return host.alive is True and host.status in (*_STATUSES, _SHELL)


def mid_turn(
    host: HostView | None, last_activity: float | None, now: float,
    thresholds: Thresholds | None = None,
) -> bool:
    """Whether ``host`` is mid-turn, working or waiting on the person."""
    return turn_state(host, last_activity, now, thresholds) != TURN_IDLE


def orphan_class(row: Row, now: float, thresholds: Thresholds) -> str:
    """Which of IDEA_025's classes an orphan host row is in.

    * ``gone`` — its process is known to be gone;
    * ``unknown`` — no pid could be asked (none recorded, or its sources
      disagree), or nothing says when it was last active;
    * ``active`` — alive, and mid-turn (:func:`turn_state`: working, or
      waiting on the person) or heard from within ``orphan_idle_minutes``;
    * ``leftover`` — alive and idle, and the session it was bound to ended;
    * ``unmanaged`` — alive and idle, and never bound to a session here.
    """
    host = row.hosts[0] if row.hosts else None
    alive = host.alive if host else None
    if alive is False:
        return ORPHAN_GONE
    if alive is None or row.last_activity is None:
        return ORPHAN_UNKNOWN
    if (mid_turn(host, row.last_activity, now, thresholds)
            or now - row.last_activity < thresholds.orphan_idle_minutes * 60):
        return ORPHAN_ACTIVE
    return ORPHAN_LEFTOVER if row.previous_session else ORPHAN_UNMANAGED


def _host_signals(row: Row, now: float, thresholds: Thresholds, add) -> None:
    """What the current host's records say, read against its liveness.

    Whether it waits, works or sits idle is :func:`turn_state`'s answer, the
    one the orphan class reads too; the rest is the attention record's.
    """
    host = row.current_host
    if host is None:
        return
    state = turn_state(host, row.last_activity, now, thresholds)
    record = host.attention or {}
    gone = host.alive is False
    waiting = record.get("waiting") if isinstance(record.get("waiting"), dict) else None
    wait_kind = waiting.get("kind") if waiting else None

    if state == TURN_WAITING and wait_kind in _ASKING:
        since = waiting.get("since")
        age = _age(since, now)
        stale = age is not None and age > thresholds.stale_wait_hours * 3600
        message = waiting.get("message")
        detail = f"waiting on a {wait_kind}" + (f": {message}" if message else "")
        if age is not None:
            detail += f" ({_minutes(age)})"
        add(WAITING, MEDIUM if stale else HIGH, True, detail, f"{wait_kind}@{since}", wait_kind)
    elif state == TURN_WAITING:
        add(WAITING, HIGH, True, "waiting on you: Claude's own status says so",
            WAITING_INPUT, WAITING_INPUT)
    elif state == TURN_IDLE and not gone and wait_kind == _IDLE:
        add(YOUR_TURN, INFO, False, "idle at the prompt: your turn")
    if host.alive is True and host.inherited_env:
        names = ", ".join(host.inherited_env)
        add(INHERITED_ENV, INFO, False,
            f"started with {names} inherited from its Terminal.app: {INHERITED_REMEDY}",
            ",".join(host.inherited_env))
    if not record:
        return

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
    silent = idle is not None and idle > thresholds.stuck_minutes * 60
    if state == TURN_BUSY and record.get("turn") == _TURN_RUNNING and silent:
        add(STUCK, MEDIUM, True, f"a running turn, silent for {_minutes(idle)}",
            str(record.get("turn_since")))
    elif state == TURN_BACKGROUND and silent:
        add(STUCK, MEDIUM, True, f"background work, silent for {_minutes(idle)}",
            str(record.get("turn_since")), STUCK_BACKGROUND)

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

    _docs_lock_signals(row, add)

    if row.stranded:
        add(STRANDED, INFO, False, f"its worktree {row.worktree} is gone")
    elif row.host_ended:
        add(PARKED, INFO, False, "its host ended; waiting to be resumed")


def _docs_lock_signals(row: Row, add) -> None:
    """The store's docs lock on a session row (ADR-014 D5, increment 4b).

    A unit passed to the session since it last looked alerts once per
    hand-over: the discriminator is each new unit with the time it was
    passed. A queue position is a marker.
    """
    docs = row.docs_lock or {}
    new = [h for h in docs.get("held") or [] if h.get("new")]
    if new:
        units = ", ".join(h["unit"] for h in new)
        add(DOCS_GRANTED, LOW, True,
            f"the docs lock passed {units} to it; it can read and design on them",
            ",".join(f"{h['unit']}@{h.get('since')}" for h in new))
    waiting = docs.get("waiting")
    if waiting:
        add(DOCS_QUEUED, INFO, False,
            f"queued for {waiting.get('unit')} at position {waiting.get('position')} "
            f"behind session {waiting.get('holder')}")


def _age(since: Any, now: float) -> float | None:
    if not isinstance(since, str):
        return None
    try:
        return now - datetime.fromisoformat(since).timestamp()
    except ValueError:
        return None


def _minutes(seconds: float) -> str:
    return f"{int(seconds // 60)} min"
