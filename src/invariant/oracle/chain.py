"""What runs next in a project: the oracle's pure decision (ADR-027 D3, D12).

``decide`` takes one project's queue, its ready backlog items, its chain
settings, the live sessions and Claude windows the scan saw, a pause and a
clock, and returns one :class:`Verdict`: ``start`` (with ``auto`` or
``propose``), ``wait``, ``paused`` or ``idle``, and every reason behind it. It
reads nothing and starts nothing; ``invariant oracle next`` prints it, and the
watcher's chain step (increment 2) acts on it.

The readers beside it (:func:`ready_items`, :func:`live_sessions`,
:func:`read_inputs`, :func:`session_end`) gather those inputs read-only: the
backlog through ``domains/workitem``, the session rows from the scan's
``assess`` document, an ended session's record through ``domains/session``
over an inert store. The queue itself is read through ``core/queue``.

Increment 2 adds what the watcher's chain step (``oracle/chainstep``) needs
to act without deciding anything twice: the caps and the resume time in
``decide``, an entry's own ``propose`` mode, the first prompt and the window's
label (:func:`render_prompt`, :func:`label_for`), and ruling 9's answer to a
failed start (:func:`on_failure`). Nothing here imports ``hosts/`` (D9).

Increment 5 (rulings 18 to 20): an entry the backlog mirror wrote
(``added_by: backlog``) starts under ``oracle.chain.backlog``, not
``oracle.chain.queue``; a queued entry whose ref is a bug in a ``reproduce``
state starts reproduce-first, with ``oracle.chain.reproduce_prompt``
(:func:`reproducing`); and :func:`reproduce_outcome` reads that session's
verdict back from the item once it has landed.

Ruling 21 (IDEA_062): a landed session's window is asked whether it may
close. The line typed into its tab is :func:`ask_line`; how long the window
must be quiet, when it is asked again, how many times and how long an answer
is waited for are ``oracle.chain.close_*`` (:class:`ChainSettings`); and
:func:`session_landed` reads when the session was torn down, against which a
turn the user typed since is told.
"""

from __future__ import annotations

import string
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.manifest import (
    DEFAULT_CHAIN_PROMPT,
    DEFAULT_REPRODUCE_PROMPT,
    DEFAULT_TRIAGE_PROMPT,
)
from ..core.queue import (
    BACKLOG,
    OPEN_STATES,
    QUEUED,
    REPRO_BLIND,
    REPRO_CLOSED,
    REPRO_OWED,
    REPRO_RED,
    Queue,
    queue_path,
    rank_key,
    read_queue,
)
from ..core.queue import PROPOSE as ENTRY_PROPOSE
from .scan import ROW_SESSION

START = "start"
WAIT = "wait"
PAUSED = "paused"
IDLE = "idle"

AUTO = "auto"
PROPOSE = "propose"
OFF = "off"

#: The state role an item machine marks ready work with (D12).
READY_ROLE = "ready"
#: The state role marking an item that is reproduced before it is fixed
#: (ruling 20): the shipped bug machine's ``filed``.
REPRODUCE_ROLE = "reproduce"
#: The report outcomes that stop a project's chain (D1).
STOPS = ("blocked", "needs-user")
#: The caps a wait may name (D5).
DAILY_CAP = "daily_cap"
MACHINE_CAP = "machine_cap"
#: The plan's weekly usage, which holds a proposal too (ruling 10).
WEEKLY_USAGE = "weekly_usage"
#: A chained window's label is the work's ref, cut to this many characters.
LABEL_MAX = 40
#: How an ended session ended, as :func:`session_end` answers.
END_LIVE = "live"
END_DONE = "done"
END_OPEN = "open"
END_ABANDONED = "abandoned"
END_MISSING = "missing"

#: The line typed into a landed session's tab (ruling 21): the user's words,
#: with the project's registered name for ``{project}``.
ASK_LINE = (
    "invariant oracle: this session has landed and the chain wants its window back. "
    "If you have nothing more to do, run: invariant oracle chain consent {project} --close"
    "   Otherwise run: invariant oracle chain consent {project} --not-yet --note "
    "\"<why, one line>\"   Answer only with that command."
)


@dataclass(frozen=True)
class ChainSettings:
    """``oracle.chain``: what may start from the queue and from the backlog."""

    queue: str = AUTO
    #: Work from the backlog: the mirror's entries and the fallback (ruling 19).
    backlog: str = AUTO
    #: ``None`` for always, else ``(start, end)`` whole local hours.
    hours: tuple[int, int] | None = None
    #: The most chained starts one local day may make (D5).
    daily_cap: int = 20
    #: A start's first prompt when its entry carries no brief.
    prompt: str = DEFAULT_CHAIN_PROMPT
    #: The brief of the triage entry a failed start queues (ruling 9).
    triage_prompt: str = DEFAULT_TRIAGE_PROMPT
    #: Whether open backlog items are mirrored onto the queue (ruling 18).
    mirror: bool = True
    #: Whether a filed bug's entry starts reproduce-first (ruling 20).
    reproduce_first: bool = True
    #: The first prompt of a reproduce-first start.
    reproduce_prompt: str = DEFAULT_REPRODUCE_PROMPT
    #: Ruling 21, in minutes but ``close_asks``: how long a landed window is
    #: quiet before it is asked, when it is asked again after a "not yet",
    #: how many asks in all, and how long an answer is waited for.
    close_ask_after: float = 5
    close_ask_again: float = 30
    close_asks: int = 3
    close_answer_within: float = 10

    @classmethod
    def from_manifest(cls, manifest) -> ChainSettings:
        hours = manifest.get("oracle.chain.hours")
        cap = manifest.get("oracle.chain.daily_cap")
        return cls(
            queue=str(manifest.get("oracle.chain.queue") or AUTO),
            backlog=str(manifest.get("oracle.chain.backlog") or AUTO),
            hours=tuple(hours) if hours else None,
            daily_cap=int(cap) if cap else 20,
            prompt=manifest.get("oracle.chain.prompt") or DEFAULT_CHAIN_PROMPT,
            triage_prompt=manifest.get("oracle.chain.triage_prompt") or DEFAULT_TRIAGE_PROMPT,
            mirror=manifest.get("oracle.chain.mirror") is not False,
            reproduce_first=manifest.get("oracle.chain.reproduce_first") is not False,
            reproduce_prompt=(manifest.get("oracle.chain.reproduce_prompt")
                              or DEFAULT_REPRODUCE_PROMPT),
            close_ask_after=_number(manifest.get("oracle.chain.close_ask_after"), 5),
            close_ask_again=_number(manifest.get("oracle.chain.close_ask_again"), 30),
            close_asks=int(_number(manifest.get("oracle.chain.close_asks"), 3)),
            close_answer_within=_number(manifest.get("oracle.chain.close_answer_within"), 10),
        )


def _number(value: Any, default: float) -> float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else default


@dataclass(frozen=True)
class Live:
    """A live session the scan saw: its id, name, state and latest report."""

    session_id: str
    name: str | None
    state: str | None
    report: dict[str, Any] | None = None


@dataclass(frozen=True)
class Ready:
    """A backlog item in a ready state, with what it is ranked by."""

    id: str
    title: str
    status: str
    tier: Any = None
    priority: Any = None


@dataclass(frozen=True)
class Candidate:
    """The work a verdict is about."""

    source: str  # "queue" or "backlog"
    ref: str
    entry: str | None = None
    brief: str | None = None
    title: str | None = None
    #: On a triage entry, the failed entry it triages; on a retry, the entry
    #: whose work it tries again (ruling 9).
    triage_of: str | None = None
    retry_of: str | None = None
    #: A reproduce-first start (ruling 20), and the residue its item owed
    #: then, against which a live leg recorded by the session is told.
    reproduce: bool = False
    residue: int | None = None
    #: The entry's reproduce runs that recorded nothing so far.
    blind: int = 0


@dataclass
class Verdict:
    """What ``decide`` answered: the verdict, the mode of a start or a wait,
    the work it is about, and every reason, in the order they were judged."""

    verdict: str
    mode: str | None = None
    candidate: Candidate | None = None
    reasons: list[str] = field(default_factory=list)
    #: The limit a ``wait`` is held by, ``daily_cap``, ``machine_cap`` or
    #: ``weekly_usage``; else ``None``.
    limit: str | None = None
    #: Queued entries whose own mode is ``propose``: offered, never auto-started.
    proposals: list[Candidate] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "mode": self.mode,
            "candidate": asdict(self.candidate) if self.candidate else None,
            "reasons": list(self.reasons),
            "limit": self.limit,
            "proposals": [asdict(p) for p in self.proposals],
        }


# -- the decision ---------------------------------------------------------------


def decide(
    *,
    queue: Queue,
    ready: Sequence[Ready],
    settings: ChainSettings,
    live: Sequence[Live],
    windows: int,
    now: datetime,
    paused: str | None = None,
    resumed: float | None = None,
    started_today: int = 0,
    machine_live: int = 0,
    machine_cap: int | None = None,
    usage_hold: str | None = None,
    unreproduced: Mapping[str, int] | None = None,
) -> Verdict:
    """One verdict for one project, and why.

    In order: a pause (the ledger's, then a live session that reported it is
    blocked or needs the user after the last ``resumed``); the candidate (the
    first queued entry whose own mode is not ``propose``, else the
    highest-ranked ready item no queue entry names) and whether its setting is
    ``off``; one at a time (a live session or window holds it); the caps,
    which hold an ``auto`` start (``started_today`` against the daily cap,
    ``machine_live`` against ``machine_cap``); then the active hours, which
    turn an ``auto`` start into a proposal. Entries of mode ``propose`` ride
    every verdict as ``proposals``.

    ``usage_hold`` is why the plan's weekly usage holds the chain (ruling 10,
    ``oracle/usage.hold``). Judged after one at a time and before the caps,
    it holds an ``auto`` start and a proposal alike, as a ``wait`` naming
    ``weekly_usage``, even with only proposals to make; with nothing to start
    or propose it changes nothing.

    An entry the backlog mirror wrote takes ``oracle.chain.backlog``: under
    ``propose`` the first such entry rides ``proposals`` and the walk goes on,
    under ``off`` it is passed over; hand-queued work behind it runs either
    way (ruling 19). ``unreproduced`` maps each bug that is reproduced first
    to the residue it owes now: an entry naming one starts reproduce-first
    (ruling 20).
    """
    proposals = [entry_candidate(e) for e in queue.entries
                 if e.state == QUEUED and e.mode == ENTRY_PROPOSE]
    offer = _mirror_offer(queue, settings)
    if offer is not None:
        proposals.append(offer)
    if paused:
        return Verdict(PAUSED, reasons=[f"the chain is paused: {paused}"],
                       proposals=proposals)
    for session in live:
        report = session.report or {}
        if report.get("outcome") in STOPS and not _before(report, resumed):
            note = f": {report['note']}" if report.get("note") else ""
            return Verdict(
                PAUSED,
                reasons=[
                    f"session {_label(session)} reported {report['outcome']}{note}",
                    "the chain stays paused while that session is live",
                ],
                proposals=proposals,
            )

    candidate, mode, reasons = _candidate(queue, ready, settings)
    if candidate is not None:
        marked = reproducing(candidate, unreproduced or {}, settings)
        if marked.reproduce:
            reasons.append(f"{candidate.ref} is not reproduced yet, so it starts "
                           f"reproduce-first (oracle.chain.reproduce_first)")
        candidate = marked
    if proposals:
        reasons.append(
            "proposed, started only by hand: " + ", ".join(f"{p.entry} {p.ref}" for p in proposals)
        )
    startable = candidate if mode != OFF else None
    if usage_hold and startable is None and proposals:
        return Verdict(WAIT, reasons=reasons + [usage_hold], limit=WEEKLY_USAGE,
                       proposals=proposals)
    if candidate is None:
        return Verdict(IDLE, reasons=reasons, proposals=proposals)
    if mode == OFF:
        return Verdict(IDLE, candidate=candidate, reasons=reasons, proposals=proposals)

    holding = _one_at_a_time(live, windows)
    if holding:
        return Verdict(WAIT, mode=mode, candidate=candidate, reasons=reasons + holding,
                       proposals=proposals)

    if usage_hold:
        return Verdict(WAIT, mode=mode, candidate=candidate, reasons=reasons + [usage_hold],
                       limit=WEEKLY_USAGE, proposals=proposals)

    if mode == AUTO:
        if started_today >= settings.daily_cap:
            reasons.append(
                f"the daily cap holds it: {started_today} chained starts today, "
                f"oracle.chain.daily_cap is {settings.daily_cap}"
            )
            return Verdict(WAIT, mode=mode, candidate=candidate, reasons=reasons,
                           limit=DAILY_CAP, proposals=proposals)
        if machine_cap is not None and machine_live >= machine_cap:
            reasons.append(
                f"the machine cap holds it: {machine_live} chained sessions live on this "
                f"machine, chain.machine_cap is {machine_cap}"
            )
            return Verdict(WAIT, mode=mode, candidate=candidate, reasons=reasons,
                           limit=MACHINE_CAP, proposals=proposals)

    if mode == AUTO and not in_hours(settings.hours, now):
        start, end = settings.hours or (0, 24)
        reasons.append(
            f"outside oracle.chain.hours ({start}-{end}), so it is proposed, not "
            f"started"
        )
        mode = PROPOSE
    return Verdict(START, mode=mode, candidate=candidate, reasons=reasons,
                   proposals=proposals)


def _before(report: dict[str, Any], resumed: float | None) -> bool:
    """Was ``report`` made at or before the last resume, so it no longer pauses?"""
    if resumed is None:
        return False
    at = report.get("at")
    return isinstance(at, (int, float)) and not isinstance(at, bool) and at <= resumed


def entry_candidate(entry) -> Candidate:
    """A queue entry as the work a verdict, or a start by hand, is about."""
    return Candidate("queue", entry.ref, entry=entry.id, brief=entry.brief,
                     triage_of=entry.triage_of, retry_of=entry.retry_of,
                     blind=entry.blind or 0)


def reproducing(candidate: Candidate, unreproduced: Mapping[str, int],
                settings: ChainSettings) -> Candidate:
    """``candidate`` as a reproduce-first start when its ref is a bug not yet
    reproduced and ``oracle.chain.reproduce_first`` is on (ruling 20).

    Only a queue entry naming the item itself is: a backlog start is of a
    ready item, which is reproduced already. The decision and ``oracle chain
    start`` both mark through here, so a start the user asks for reproduces
    first too.
    """
    if (not settings.reproduce_first or candidate.source != "queue"
            or candidate.ref not in unreproduced):
        return candidate
    return replace(candidate, reproduce=True, residue=int(unreproduced[candidate.ref]))


def _mirror_offer(queue: Queue, settings: ChainSettings) -> Candidate | None:
    """Under ``oracle.chain.backlog: propose``, the first queued mirrored entry."""
    if settings.backlog != PROPOSE:
        return None
    first = next((e for e in queue.entries if e.state == QUEUED
                  and e.mode != ENTRY_PROPOSE and e.added_by == BACKLOG), None)
    return entry_candidate(first) if first is not None else None


def _candidate(
    queue: Queue, ready: Sequence[Ready], settings: ChainSettings
) -> tuple[Candidate | None, str | None, list[str]]:
    passed = 0
    head = None
    for e in queue.entries:
        if e.state != QUEUED or e.mode == ENTRY_PROPOSE:
            continue
        if e.added_by == BACKLOG and settings.backlog != AUTO:
            passed += 1
            continue
        head = e
        break
    if head is not None and head.added_by == BACKLOG:
        candidate = entry_candidate(head)
        return candidate, AUTO, [
            f"the queue's head is {head.id}, {head.ref}, mirrored from the backlog",
            "oracle.chain.backlog is auto",
        ]
    if head is not None:
        candidate = entry_candidate(head)
        reasons = [f"the queue's head is {head.id}, {head.ref}"]
        if settings.queue == OFF:
            reasons.append(
                "oracle.chain.queue is off: the queue is started by hand, and the "
                "backlog is not consulted ahead of it"
            )
        else:
            reasons.append(f"oracle.chain.queue is {settings.queue}")
        return candidate, settings.queue, reasons

    held = _held(queue)
    ranked = [item for item in rank(ready) if item.id not in held]
    noun = "entry" if passed == 1 else "entries"
    said = (f"{passed} {noun} mirrored from the backlog wait: oracle.chain.backlog is "
            f"{settings.backlog}")
    waiting = [said] if passed else []
    if not ranked:
        return None, None, [*waiting, "nothing is queued, and no backlog item is ready to work"]
    item = ranked[0]
    candidate = Candidate("backlog", item.id, title=item.title)
    reasons = waiting + [
        "the queue is empty",
        (
            f"the backlog's first ready item is {item.id} ({item.status}; tier "
            f"{_shown(item.tier)}, priority {_shown(item.priority)})"
        ),
    ]
    if settings.backlog == OFF:
        reasons.append("oracle.chain.backlog is off: backlog work is started by hand")
    else:
        reasons.append(f"oracle.chain.backlog is {settings.backlog}")
    return candidate, settings.backlog, reasons


def _one_at_a_time(live: Sequence[Live], windows: int) -> list[str]:
    reasons: list[str] = []
    for session in live:
        report = session.report or {}
        if report.get("outcome") == "done":
            reasons.append(
                f"one at a time: session {_label(session)} reported done and waits "
                f"for its teardown"
            )
        else:
            reasons.append(
                f"one at a time: session {_label(session)} is live ({session.state})"
            )
    if windows:
        plural = "s" if windows != 1 else ""
        reasons.append(f"one at a time: {windows} Claude window{plural} open in the project")
    return reasons


def _label(session: Live) -> str:
    return session.name or session.session_id


def _shown(value: Any) -> str:
    return "none" if value is None else str(value)


def by_hand(queue: Queue, ready: Sequence[Ready]) -> Candidate | None:
    """What ``oracle chain start`` starts with no entry named: the user's start.

    The queue's first queued entry whatever its mode (a proposal is what the
    user accepts), else the backlog's first ready item no entry names. No
    setting and no limit is consulted: a start the user asks for is never
    held (D5).
    """
    head = queue.head()
    if head is not None:
        return entry_candidate(head)
    return backlog_candidate(queue, ready)


def backlog_candidate(queue: Queue, ready: Sequence[Ready],
                      item: str | None = None) -> Candidate | None:
    """A ready backlog item no queue entry names, in any state (D18).

    The first by rank, or the one named ``item`` (``oracle chain start
    --item``, a backlog proposal accepted from the phone); ``None`` when there
    is none, or ``item`` is not ready or is named by an entry.
    """
    held = _held(queue)
    ranked = [i for i in rank(ready) if i.id not in held and (item is None or i.id == item)]
    if not ranked:
        return None
    return Candidate("backlog", ranked[0].id, title=ranked[0].title)


def _held(queue: Queue) -> set[str]:
    """The items the backlog fallback passes over: any entry names it, in any
    state (D18), or a live entry serves it (ruling 18)."""
    return ({e.ref for e in queue.entries}
            | {e.serves for e in queue.entries if e.state in OPEN_STATES and e.serves})


# -- the rank and the hours -------------------------------------------------------


def rank(items: Sequence[Ready]) -> list[Ready]:
    """Tier ascending, then priority, then id in natural order (D12)."""
    return sorted(items, key=lambda i: rank_key(i.id, i.tier, i.priority))


def in_hours(hours: tuple[int, int] | None, now: datetime) -> bool:
    """Is ``now`` inside ``[start, end)``, wrapping past midnight? ``None`` is always."""
    if hours is None:
        return True
    start, end = hours
    hour = now.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


# -- the inputs, read ---------------------------------------------------------------


def live_sessions(project: dict[str, Any]) -> list[Live]:
    """The live sessions of one project of the ``assess`` document."""
    return [
        Live(row.get("session_id") or "", row.get("name"), row.get("state"), row.get("report"))
        for row in project.get("rows") or []
        if row.get("kind") == ROW_SESSION
    ]


def ready_items(items, item_list) -> list[Ready]:
    """``item_list``'s open items in a ready state of their machine (D12).

    ``items`` is the project's ``WorkItemDomain``, asked for each type's
    machine; the list is read once by the caller.
    """
    ready: dict[str, tuple[str, ...]] = {}
    out: list[Ready] = []
    for item in item_list:
        if item.type not in ready:
            ready[item.type] = items.machine(item.type).states_with_role(READY_ROLE)
        if item.status in ready[item.type]:
            fields = item.fields or {}
            out.append(
                Ready(item.id, item.title, item.status, fields.get("tier"), fields.get("priority"))
            )
    return out


def unreproduced_items(items, item_list) -> dict[str, int]:
    """``item_list``'s items in a ``reproduce`` state, with the residue each owes."""
    roles: dict[str, tuple[str, ...]] = {}
    out: dict[str, int] = {}
    for item in item_list:
        if item.type not in roles:
            roles[item.type] = items.machine(item.type).states_with_role(REPRODUCE_ROLE)
        if item.status in roles[item.type]:
            out[item.id] = len(item.residue or [])
    return out


def read_inputs(root: str | Path) -> Inputs:
    """One project's queue, ready backlog items and chain settings, read-only.

    What ``oracle next`` and the watcher's chain step both decide over: the
    queue from the main checkout (or the store, D10), the backlog through
    ``domains/workitem`` with each type's ``ready`` states, ``oracle.chain``
    from the manifest at ``root``.
    """
    from ..core.manifest import Manifest
    from ..domains.workitem import WorkItemDomain

    manifest = Manifest.locate(Path(root))
    items = WorkItemDomain(manifest)
    listed = items.list(open_only=True)
    settings = ChainSettings.from_manifest(manifest)
    return Inputs(
        queue=read_queue(queue_path(manifest)),
        ready=ready_items(items, listed),
        settings=settings,
        unreproduced=unreproduced_items(items, listed) if settings.reproduce_first else {},
    )


@dataclass(frozen=True)
class Inputs:
    """What :func:`decide` needs from one project's own records."""

    queue: Queue
    ready: list[Ready]
    settings: ChainSettings
    #: The bugs reproduced first, with the residue each owes (ruling 20).
    unreproduced: dict[str, int] = field(default_factory=dict)


def reproduce_outcome(root: str | Path, item_id: str, residue: int | None) -> str:
    """What a reproduce-first session left on its bug, read once it has landed.

    ``red`` when the item is in a ``ready`` state (the bug machine's
    ``reproduced``: RED-CONFIRMED); ``closed`` when it is in no ``reproduce``
    state any more (ALREADY-FIXED or not reproducible closed it), or cannot
    be found; ``owed`` when it is still unreproduced and owes more residue
    than ``residue``, what it owed when the session started (DEVICE-ONLY
    records its live leg so); else ``blind`` (HARNESS-BLIND records only a
    note). Read-only, through ``domains/workitem``.
    """
    from ..core.errors import UsageError
    from ..core.manifest import Manifest
    from ..domains.workitem import WorkItemDomain

    items = WorkItemDomain(Manifest.locate(Path(root)))
    try:
        item = items.get(item_id)
    except UsageError:
        return REPRO_CLOSED
    machine = items.machine(item.type)
    if item.status in machine.states_with_role(READY_ROLE):
        return REPRO_RED
    if item.status not in machine.states_with_role(REPRODUCE_ROLE):
        return REPRO_CLOSED
    if len(item.residue or []) > (residue or 0):
        return REPRO_OWED
    return REPRO_BLIND


def session_end(root: str | Path, session_id: str) -> str:
    """How session ``session_id`` of the project at ``root`` stands, read-only.

    ``live`` while its machine has not ended it; ``done`` once it is in a
    terminal state that is not the abandon one (``torn_down``: merged) with
    no deliverable open (D1: a complete plan, or none); ``open`` torn down
    with a deliverable still open, which only a forced merge reaches;
    ``abandoned``; ``missing`` when no record is there. Read through an inert
    store, as the scan reads, so nothing is written.
    """
    from ..core.manifest import Manifest
    from ..core.store import InertStore
    from ..domains.session import SessionDomain

    manifest = Manifest.locate(Path(root))
    sessions = SessionDomain(manifest, store=InertStore(Path(manifest.state_root)))
    session = sessions.get(session_id)
    if session is None:
        return END_MISSING
    machine = sessions.machine
    if session.state in machine.states_with_role("abandon"):
        return END_ABANDONED
    if session.state not in machine.terminal:
        return END_LIVE
    return END_OPEN if session.plan_open else END_DONE


def session_landed(root: str | Path, session_id: str) -> float | None:
    """When session ``session_id`` was last stamped: for a torn-down one, its teardown.

    The session record's ``heartbeat``, which every transition stamps, read
    through an inert store as :func:`session_end` reads. ``None`` when the
    record is gone or carries none (ruling 21: a turn typed after it is the
    user's).
    """
    from ..core.manifest import Manifest
    from ..core.store import InertStore
    from ..domains.session import SessionDomain

    manifest = Manifest.locate(Path(root))
    sessions = SessionDomain(manifest, store=InertStore(Path(manifest.state_root)))
    session = sessions.get(session_id)
    beat = session.heartbeat if session is not None else None
    return float(beat) if isinstance(beat, (int, float)) and beat else None


def ask_line(project: str) -> str:
    """The line asking a landed session whether its window may close (ruling 21)."""
    return ASK_LINE.format(project=project)


# -- what a start says, and what a failure owes ----------------------------------------


def render_prompt(settings: ChainSettings, candidate: Candidate) -> str:
    """A chained start's first prompt: its entry's brief, else ``oracle.chain.prompt``.

    The template is filled with ``{ref}``, ``{title}`` (the ref when there is
    no title) and ``{entry}`` (``new`` for a backlog start, whose entry is
    minted at the start). A reproduce-first start takes
    ``oracle.chain.reproduce_prompt`` instead, whatever its brief (which is
    the fix's), with ``{attempt}`` too (ruling 20).
    """
    if candidate.reproduce:
        return string.Formatter().vformat(settings.reproduce_prompt, (), {
            "ref": candidate.ref,
            "title": candidate.title or candidate.ref,
            "entry": candidate.entry or "new",
            "attempt": candidate.blind + 1,
        })
    if candidate.brief:
        return candidate.brief
    return string.Formatter().vformat(settings.prompt, (), {
        "ref": candidate.ref,
        "title": candidate.title or candidate.ref,
        "entry": candidate.entry or "new",
    })


def label_for(candidate: Candidate) -> str:
    """The label a chained window is named by: its ref, cut to :data:`LABEL_MAX`.

    A ref with no letter or digit would be refused as a name, so its entry id
    stands in.
    """
    label = candidate.ref[:LABEL_MAX]
    if not any(ch.isalnum() for ch in label):
        return candidate.entry or "chain"
    return label


def on_failure(triage_of: str | None, retry_of: str | None) -> tuple[str, bool]:
    """What a failed chained start owes (ADR-027 ruling 9): ``(action, retry)``.

    A triage that fails leaves nobody to judge, so it pauses the chain
    (``pause``). Any other failure is triaged (``triage``), and retried once:
    a retry that fails again is triaged but not retried.
    """
    if triage_of:
        return "pause", False
    return "triage", not retry_of
