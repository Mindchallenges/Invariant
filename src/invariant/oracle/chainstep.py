"""The watcher's chain step: follow, decide, start (ADR-027 increment 2, ruling 9).

Each tick, after the alerts, :meth:`ChainStep.step` takes the ``assess``
document the tick made and, for every project it read:

1. **follows** the chained start the ledger holds: binds it to the invariant
   session its window started (by the window's launched name, else the pid the
   launch returned), marks its entry ``done`` once that session is torn down
   with its plan complete, and treats every other ending as a failure;
2. **pauses** on a ``blocked`` or ``needs-user`` report made after the last
   resume. The pause outlives the session until ``oracle chain resume``;
3. **decides**, only when nothing is live in the project and nothing is
   paused or running: ``chain.decide`` over the project's records and the
   ledger's counts; and **acts**: an ``auto`` start is reserved, marked and
   launched; a proposal and a cap that holds are said once.

**A failure is triaged, not paused** (ruling 9). A failed chained start marks
its entry ``failed`` and queues a triage entry at the head, with one retry of
the failed work behind it; a retry that fails is triaged and not retried. Only
a failed triage pauses: nobody is left to judge. The oracle writes no record:
every queue write goes through the :class:`ChainHands` the CLI injects (D9),
and so does the launch, which is ``oracle start-session``'s own.

**The ledger**, ``<oracle home>/oracle/chain.json``, is machine-local derived
state under ``locks/chain.lock``, as the alert ledger is. Unlike that one, a
ledger that cannot be read is never replaced: replacing it would lift every
pause and reset the caps, so the step does nothing until it is mended and
says so once. A start is reserved under the lock before the launch and the
launch runs outside it, so the daemon, a ``watch --once`` and ``oracle chain
start`` never start one piece of work twice; a reservation whose watcher died,
or one older than :data:`STALE_RESERVATION_SECONDS`, is a failure.

**Increment 2b.** The plan's weekly usage, read through the seam the CLI
injects (``oracle/usage``), holds every auto-start and every proposal while it
is at or over ``chain.weekly_usage_max`` or cannot be read, with one notice
per hold, until a greenlight (:func:`greenlight`) lifts the unknown-reading
hold (ruling 10). A chained session the scan reads ``stalled`` gets one
"continue" typed into its tab through the hands (after its reset, for a usage
limit), a notice if it is still stalled one fuse later, and the user's
Continue, Retry and Close (:meth:`ChainStep.stall_action`, rulings 11 and 12).
A chained session the chain has finished with (failed, closed or retried) is
released: it stays parked, and no longer holds its project (ruling 13).

**Increment 5.** Before it decides for an idle project, at most once a
minute, the step has the hands mirror the backlog onto the queue (ruling 18).
A reproduce-first start (ruling 20) is recorded as one, with the residue its
bug owed; when its session lands, the verdict is read back from the item and
the hands route it (``reproduced``): the fix at the head, the entry done, a
proposal, or one more run.

**Ruling 21 (IDEA_062).** A start that lands leaves its window open, and an
open window holds its project. The step follows that window in the ledger's
``landed``: once it has been quiet ``oracle.chain.close_ask_after`` minutes,
it types one line into the session's own tab through the hands' typer
(:func:`chain.ask_line`), and the session answers with ``invariant oracle
chain consent`` (:func:`consent`), an engine record. On consent the window is
closed through the hands' closer, close-host's path, and the session is
released; on "not yet" it is asked again, ``close_asks`` times in all; on no
answer, or when it cannot be asked, one notice offers Close and Leave open
(:meth:`ChainStep.landed_action`). A window the user typed a turn into since
the teardown is the user's: never asked, never closed, one notice. Every
ask, answer, notice and close is on the record, and an ended one is kept in
``landings``.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from ..core.errors import InvariantError, StateError
from . import chain
from .chain import AUTO, PROPOSE, START, WAIT, Candidate, ChainSettings
from .index import HomeStore
from .notify import Notification, Notifier
from .scan import ROW_HOST, ROW_SESSION
from .signals import TURN_IDLE
from .startsession import open_windows
from .talk import TYPED_SETTLE, typist
from .usage import DEFAULT_WEEKLY_MAX, OVER, PlanUsage, hold, spent

LEDGER_FILE = "oracle/chain.json"
LEDGER_VERSION = 1

#: A chained window with no invariant session bound this long after it
#: appeared has failed (D6).
UNBOUND_SECONDS = 600
#: A chained window seen gone this long, with no teardown and no report, has
#: failed. Longer than a handoff's gap, whose successor is alive first.
GONE_SECONDS = 60
#: A reservation older than this was never finished: the successor watch
#: gives up after 15 s and its grace is 3 s.
STALE_RESERVATION_SECONDS = 120
#: The starts the ledger keeps per project, newest last.
STARTS_KEPT = 100
#: The notices kept owed while none can be delivered.
NOTICES_KEPT = 50
#: The machine cap when the user config sets none (D5).
DEFAULT_MACHINE_CAP = 10

#: A usage limit's nudge waits this long past its reset (D7).
RESET_MARGIN_SECONDS = 60
#: The sessions the ledger remembers as released, per project (ruling 13).
RELEASED_KEPT = 100
#: What the nudge types (D7).
CONTINUE = "continue"
#: The least time between two mirrors of one project's backlog (ruling 18).
MIRROR_SECONDS = 60
#: The stall's own signal kind, as ``oracle/signals`` names it.
_STALLED = "stalled"
_LIMIT = "limit"

LAUNCHING = "launching"
RUNNING = "running"

#: The landed windows the ledger keeps once they ended, per project (ruling 21).
LANDINGS_KEPT = 20
#: A consented close waits at most this long for the turn that answered to end.
CONSENT_GRACE_SECONDS = 60
#: The answers ``oracle chain consent`` records.
ANSWER_CLOSE = "close"
ANSWER_NOT_YET = "not_yet"
#: A landed window's states: watched until quiet, asked (a reservation, then
#: typed), consented and closing, waiting on the user after the notice, left
#: open by the user, or the user's own.
WATCHING = "watching"
ASKING = "asking"
ASKED = "asked"
CONSENTED = "consented"
CLOSING = "closing"
NOTIFIED = "notified"
LEFT_OPEN = "left_open"
USERS = "users"
#: Why the notice was sent.
NO_ANSWER = "no_answer"
NOT_YET = "not_yet"
NOT_TYPED = "not_typed"
UNREAD = "unread"
CLOSE_FAILED = "close_failed"
#: The user's two answers to the notice, as the CLI's verbs name them.
CLOSE_WINDOW = "close-window"
LEAVE_OPEN = "leave-open"
#: How an ended landing ended.
OUT_CLOSED = "closed"
OUT_CLOSED_BY_USER = "closed_by_user"
OUT_GONE = "gone"

#: The short reasons a failed entry carries. They are written in a committed
#: record, so they never quote a path or a launcher's message: the detail is
#: in the ledger, which is machine-local.
LAUNCH_FAILED = "the launch failed"
INTERRUPTED = "the launch was interrupted"
UNBOUND = "no invariant session was bound 10 minutes after the window appeared"
GONE = "the window closed without a teardown or a report"
CLOSED_BY_USER = "closed by the user after a stall"
RETRIED_BY_USER = "retried by the user after a stall"
ENDED = {
    chain.END_OPEN: "the session was torn down with deliverables open",
    chain.END_ABANDONED: "the session was abandoned",
    chain.END_MISSING: "the session's record cannot be found",
}

_SOUND = "Glass"


class LedgerError(StateError):
    """The chain ledger cannot be read; the chain does nothing until it can."""


@dataclass(frozen=True)
class ChainHands:
    """What the CLI hands the step: the launch and the queue's writes (D9).

    * ``start(key, label, prompt)`` opens the window through ``oracle
      start-session``'s path and returns ``{"name", "pid"}``; it raises
      :class:`InvariantError` when no window opened in the main checkout.
    * ``begin(key, entry, ref)`` marks the entry ``started`` (a backlog start,
      ``entry`` ``None``, is minted as one) and returns its id.
    * ``mark(key, entry, state, session, reason)`` moves an entry.
    * ``fail(key, entry, reason, triage, retry)`` marks it failed and queues
      the triage (``triage`` the template, or ``None``) and one retry at the
      head; it returns ``{"failed", "triage", "retry"}``.
    * ``nudge(tty, text)`` types ``text`` into the Terminal tab on ``tty``
      and returns ``(code, detail)``, ``typed`` when it did (increment 2b).
    * ``close(host)`` ends a stalled chained window and its tab, through
      close-host's seams; the lines it did, or :class:`InvariantError`.
    * ``requeue(key, entry)`` puts an entry back at the head, ``queued``.
    * ``mirror(key)`` reconciles the queue with the backlog and returns
      ``{"added": [(entry, ref)], "removed": [(entry, ref)]}`` (increment 5).
    * ``reproduced(key, entry, outcome)`` routes a reproduce-first session's
      verdict (``red``, ``closed``, ``owed`` or ``blind``).

    The optional ones are ``None`` in hands built without them: a stall is
    then never nudged and its verbs are refused, nothing is mirrored, and a
    reproduce-first entry is marked done as any other.
    """

    start: Callable[[str, str, str], dict[str, Any]]
    begin: Callable[[str, str | None, str], str]
    mark: Callable[..., Any]
    fail: Callable[[str, str, str, str | None, bool], dict[str, Any]]
    nudge: Callable[[str, str], tuple[str, str]] | None = None
    close: Callable[[dict[str, Any]], list[str]] | None = None
    requeue: Callable[[str, str], Any] | None = None
    mirror: Callable[[str], dict[str, Any]] | None = None
    reproduced: Callable[[str, str, str], Any] | None = None


def machine_cap(user_config) -> int:
    """``chain.machine_cap`` of the user config, else :data:`DEFAULT_MACHINE_CAP`."""
    return int((user_config.chain or {}).get("machine_cap") or DEFAULT_MACHINE_CAP)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


def local_day(epoch: float) -> str:
    """The machine's local calendar day of ``epoch``: the daily cap's unit."""
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def _pid_alive(pid: int | None) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# -- the ledger ------------------------------------------------------------------


def _read(store: HomeStore) -> dict[str, Any]:
    """The ledger, or :class:`LedgerError` naming why it cannot be used."""
    try:
        document = store.read(LEDGER_FILE)
    except StateError as exc:
        raise LedgerError(f"the chain ledger {store.root / LEDGER_FILE} cannot be read: "
                          f"{exc}") from exc
    if document is None:
        return {"version": LEDGER_VERSION, "projects": {}, "notices": []}
    if not isinstance(document, dict) or not isinstance(document.get("projects", {}), dict):
        raise LedgerError(f"the chain ledger {store.root / LEDGER_FILE} is not a ledger "
                          f"document; fix or delete it (deleting lifts every pause)")
    if document.get("version", LEDGER_VERSION) != LEDGER_VERSION:
        raise LedgerError(f"the chain ledger {store.root / LEDGER_FILE} is version "
                          f"{document.get('version')!r}; this build reads version "
                          f"{LEDGER_VERSION} and does not rewrite a newer one")
    document["version"] = LEDGER_VERSION
    document.setdefault("projects", {})
    if not isinstance(document.get("notices"), list):
        document["notices"] = []
    _usage_state(document)
    return document


def _usage_state(ledger: dict[str, Any]) -> dict[str, Any]:
    """The ledger's machine-wide usage block: the hold said, the greenlight given."""
    block = ledger.get("usage")
    if not isinstance(block, dict):
        block = ledger["usage"] = {}
    for name in ("held", "greenlight"):
        if not isinstance(block.get(name), dict):
            block[name] = None
    return block


def greenlit(ledger: dict[str, Any]) -> float | None:
    given = _usage_state(ledger)["greenlight"]
    value = given.get("at_epoch") if given else None
    return float(value) if isinstance(value, (int, float)) else None


def _project(ledger: dict[str, Any], key: str) -> dict[str, Any]:
    state = ledger["projects"].setdefault(key, {})
    for name in ("paused", "resumed", "current"):
        state.setdefault(name, None)
    for name in ("proposed", "held"):
        if not isinstance(state.get(name), dict):
            state[name] = {}
    if not isinstance(state.get("starts"), list):
        state["starts"] = []
    if not isinstance(state.get("released"), list):
        state["released"] = []
    if not isinstance(state.get("landed"), dict):
        state["landed"] = None
    if not isinstance(state.get("landings"), list):
        state["landings"] = []
    return state


def _release(state: dict[str, Any], current: dict[str, Any]) -> None:
    """Ruling 13: the chain is finished with this start's session; it holds nothing."""
    session = current.get("session")
    if session and session not in state["released"]:
        state["released"].append(session)
        del state["released"][:-RELEASED_KEPT]


def _end_landed(state: dict[str, Any], landed: dict[str, Any], outcome: str, now: float,
                release: bool = True) -> None:
    """A landed window's watch is over: kept in ``landings``, its session released."""
    landed = dict(landed, outcome=outcome, ended=_iso(now))
    state.setdefault("landings", []).append(landed)
    del state["landings"][:-LANDINGS_KEPT]
    if state.get("landed") is not None:
        state["landed"] = None
    if release:
        _release(state, landed)


def _typed(landed: dict[str, Any]) -> int:
    """The asks typed, or being typed, into a landed window."""
    return sum(1 for ask in landed.get("asks") or []
               if isinstance(ask, dict) and ask.get("code") in ("typed", "sending"))


def consent(home: Path, key: str, name: str, answer: str, note: str | None,
            pid: int | None, now: float) -> dict[str, Any]:
    """Record a landed session's answer to the oracle's ask (ruling 21).

    ``answer`` is ``close`` or ``not_yet`` (with its note, vetted by the
    caller). ``pid`` is the Claude process the command ran under, when it
    knows one: an answer from another window is refused. Refused (an
    :class:`InvariantError` that exits 2) without a landed window the oracle
    has asked, or one that is the user's. A ``close`` is closed by the
    watcher's next tick; a "not yet" schedules the next ask, or, after the
    last, the notice. An answer after the notice is kept: a ``close`` still
    closes, a "not yet" asks nothing more.
    """
    if answer not in (ANSWER_CLOSE, ANSWER_NOT_YET):
        raise ValueError(f"not a consent answer: {answer!r}")
    store = HomeStore(home)
    with store.lock(LEDGER_FILE):
        ledger = _read(store)
        state = _project(ledger, key)
        landed = state["landed"]
        if not landed:
            raise _Busy(f"{name} has no landed window asking whether it may close; nothing "
                        f"was recorded")
        what = f"{landed.get('entry')} {landed.get('ref')}"
        if landed.get("state") in (USERS, LEFT_OPEN):
            raise _Busy(f"the window of {what} is the user's to close: the user typed in "
                        f"it, or chose to leave it open; nothing was recorded")
        if not _typed(landed):
            raise _Busy(f"the oracle has not asked the window of {what} yet; nothing was "
                        f"recorded")
        mine = landed.get("pid")
        if pid is not None and isinstance(mine, int) and pid != mine:
            raise _Busy(f"this command runs in another window (Claude {pid}) than the one "
                        f"asked (Claude {mine}); nothing was recorded")
        if answer == ANSWER_CLOSE and landed.get("state") in (CONSENTED, CLOSING):
            return {"state": landed["state"], "already": True}
        landed.setdefault("answers", []).append(
            {"answer": answer, "note": note, "at": _iso(now), "at_epoch": now})
        if answer == ANSWER_CLOSE:
            landed.update(state=CONSENTED, consented_epoch=now)
        elif landed.get("state") != NOTIFIED:
            limits = landed.get("limits") or {}
            if _typed(landed) >= int(limits.get("asks") or 1):
                _notify_landed(ledger, name, landed, NOT_YET, now, note=note)
            else:
                landed.update(state=WATCHING,
                              ask_at_epoch=now + float(limits.get("ask_again") or 0))
        store.write(LEDGER_FILE, ledger)
        return {"state": landed["state"], "already": False, "entry": landed.get("entry"),
                "ref": landed.get("ref"), "name": landed.get("name"),
                "ask_at_epoch": landed.get("ask_at_epoch")}


def _notify_landed(ledger: dict[str, Any], name: str, landed: dict[str, Any], why: str,
                   now: float, note: str | None = None, detail: str | None = None) -> str:
    """The one notice of a landed window that still holds its project: Close or Leave open."""
    landed["state"] = NOTIFIED
    landed["notice"] = {"why": why, "at": _iso(now), "note": note, "detail": detail}
    what = f"{landed.get('entry')} {landed.get('ref')}"
    limits = landed.get("limits") or {}
    reasons = {
        NO_ANSWER: f"it did not answer the oracle's ask within "
                   f"{float(limits.get('answer_within') or 0) / 60:g} minutes",
        NOT_YET: f"it said not yet {_typed(landed)} times, last: {note}",
        NOT_TYPED: "the oracle could not type its ask into the window"
                   + (f" ({detail})" if detail else ""),
        UNREAD: "the oracle cannot tell whether you typed in it since",
        CLOSE_FAILED: "closing it failed" + (f" ({detail})" if detail else ""),
    }
    text = (f"{what} landed, but its window {landed.get('name')} still holds {name}: "
            f"{reasons[why]}. Close it or leave it open: invariant oracle chain close-window "
            f"{name}, or leave-open {name}")
    ChainStep._notice(ledger, name, "window open", text, sound=_SOUND)
    return text


def _started_today(state: dict[str, Any], now: float) -> int:
    today = local_day(now)
    return sum(1 for s in state.get("starts") or [] if isinstance(s, dict)
               and s.get("day") == today)


def _machine_live(ledger: dict[str, Any]) -> int:
    return sum(1 for state in ledger["projects"].values()
               if isinstance(state, dict) and state.get("current"))


def _resumed_epoch(state: dict[str, Any]) -> float | None:
    value = state.get("resumed_epoch")
    return float(value) if isinstance(value, (int, float)) else None


def pause(home: Path, key: str, reason: str, now: float, by: str = "user") -> dict[str, Any]:
    """Pause ``key``'s chain by hand. A chain already paused keeps its first reason."""
    store = HomeStore(home)
    with store.lock(LEDGER_FILE):
        ledger = _read(store)
        state = _project(ledger, key)
        if not state["paused"]:
            state["paused"] = {"reason": reason, "by": by, "since": _iso(now)}
        store.write(LEDGER_FILE, ledger)
        return {"paused": state["paused"]}


def resume(home: Path, key: str, now: float) -> dict[str, Any]:
    """Lift ``key``'s pause; a stop report made before now no longer pauses.

    Nothing is retried: ruling 9's triage owns retries. ``was`` is the pause
    lifted, ``None`` when the chain was not paused.
    """
    store = HomeStore(home)
    with store.lock(LEDGER_FILE):
        ledger = _read(store)
        state = _project(ledger, key)
        was = state["paused"]
        state["paused"] = None
        state["resumed"] = _iso(now)
        state["resumed_epoch"] = now
        store.write(LEDGER_FILE, ledger)
        return {"was": was}


def status(home: Path, now: float) -> dict[str, Any]:
    """The ledger as ``oracle chain status`` shows it; raises :class:`LedgerError`."""
    ledger = _read(HomeStore(home))
    projects = {}
    for key in ledger["projects"]:
        state = _project(ledger, key)
        projects[key] = {
            "paused": state["paused"],
            "resumed": state["resumed"],
            "current": state["current"],
            "proposed": sorted(state["proposed"]),
            "started_today": _started_today(state, now),
            "starts": state["starts"][-5:],
            "landed": state["landed"],
        }
    return {"projects": projects, "machine_live": _machine_live(ledger),
            "notices_owed": len(ledger["notices"]), "usage": _usage_state(ledger)}


def read_ledger(home: Path) -> dict[str, Any]:
    """The whole ledger, read without its lock, every project filled in.

    What the phone's chain view reads (``oracle/chainview``, ADR-027
    increment 3): nothing is written. Raises :class:`LedgerError` when the
    ledger cannot be read, as :func:`status` does.
    """
    ledger = _read(HomeStore(home))
    _usage_state(ledger)
    for key in list(ledger["projects"]):
        _project(ledger, key)
    return ledger


def limits(home: Path, key: str, now: float, reading: PlanUsage | None = None,
           cap: float | None = None) -> dict[str, Any]:
    """What ``oracle next`` hands ``decide`` from the ledger, read without a lock.

    An unreadable ledger reads as a pause, as the step treats it. ``usage``
    is the weekly-usage hold's reason for ``reading`` against ``cap`` and the
    ledger's greenlight (``None`` without a ``cap``); ``released`` the
    sessions the chain has finished with (ruling 13).
    """
    try:
        ledger = _read(HomeStore(home))
    except LedgerError as exc:
        return {"paused": f"the chain ledger cannot be read: {exc}", "resumed": None,
                "started_today": 0, "machine_live": 0, "current": None, "usage": None,
                "released": []}
    state = _project(ledger, key)
    paused = state["paused"]
    held = hold(reading, now, cap, greenlit(ledger)) if cap is not None else None
    return {
        "paused": paused.get("reason") if isinstance(paused, dict) else None,
        "resumed": _resumed_epoch(state),
        "started_today": _started_today(state, now),
        "machine_live": _machine_live(ledger),
        "current": state["current"],
        "usage": held.reason if held else None,
        "released": list(state["released"]),
    }


def greenlight(home: Path, now: float, reading: PlanUsage | None, cap: float) -> dict[str, Any]:
    """Let the chain go ahead while the plan's weekly usage cannot be read (ruling 10).

    Recorded in the ledger, and spent once a reading newer than it arrives.
    Refused (an :class:`InvariantError` that exits 2) while a fresh reading is
    at or over ``cap``: the greenlight lifts only the unknown-reading hold.
    With nothing held, nothing is recorded: ``greenlit`` is ``False``.
    """
    store = HomeStore(home)
    with store.lock(LEDGER_FILE):
        ledger = _read(store)
        held = hold(reading, now, cap, None)
        if held is not None and held.kind == OVER:
            raise _Busy(f"{held.reason}. A greenlight lifts only an unknown reading; "
                        f"`invariant oracle chain start <project>` starts one by hand")
        if held is None:
            return {"greenlit": False, "why": "nothing is held: the weekly usage reads "
                                              "under the cap"}
        block = _usage_state(ledger)
        block["greenlight"] = {"at": _iso(now), "at_epoch": now}
        store.write(LEDGER_FILE, ledger)
        return {"greenlit": True, "was": held.reason}


# -- the step --------------------------------------------------------------------


@dataclass
class _Launch:
    key: str
    name: str
    root: str
    candidate: Candidate
    settings: ChainSettings
    mode: str
    reservation: dict[str, Any]


@dataclass
class _Nudge:
    key: str
    name: str
    tty: str
    start: str
    since: Any
    what: str


@dataclass
class _Ask:
    key: str
    name: str
    tty: str
    landed: str
    what: str


@dataclass
class _Close:
    key: str
    name: str
    host: dict[str, Any]
    landed: str
    what: str


@dataclass
class ChainStep:
    """Follow, decide and start, one project at a time, under the chain lock."""

    home: Path
    hands: ChainHands
    notifier: Notifier | None
    machine_cap: int | None = DEFAULT_MACHINE_CAP
    reader: Callable[[str], Any] = chain.read_inputs
    session_end: Callable[[str, str], str] = chain.session_end
    pid_alive: Callable[[int | None], bool] = _pid_alive
    log: Callable[[str], None] = field(
        default=lambda message: print(message, file=sys.stderr, flush=True))
    #: The plan's last usage reading, the seam the CLI injects (ruling 10).
    #: ``None`` is a step built without one, whose chain is never held by usage;
    #: the CLI always injects it, and its missing reading holds.
    usage: Callable[[], PlanUsage | None] | None = None
    weekly_max: float = DEFAULT_WEEKLY_MAX
    #: What a reproduce-first session left on its bug (increment 5).
    outcome: Callable[[str, str, int | None], str] = chain.reproduce_outcome
    #: When a landed session was torn down (ruling 21).
    landed_at: Callable[[str, str], float | None] = chain.session_landed
    #: The half second the typist's lock is kept after a nudge (ADR-029 D6).
    pause: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        self.store = HomeStore(self.home)
        self._unreadable_said = False
        self._hold = None
        self._held_by_usage = False
        #: Each project's root by key, as the last document named it.
        self._roots: dict[str, str] = {}

    # -- one tick ------------------------------------------------------

    def step(self, document: dict[str, Any], now: float) -> list[dict[str, Any]]:
        """Act on one tick's ``assess`` document; the actions taken, in order."""
        actions: list[dict[str, Any]] = []
        launches: list[_Launch] = []
        nudges: list[_Nudge] = []
        self._asks: list[_Ask] = []
        self._closes: list[_Close] = []
        try:
            with self.store.lock(LEDGER_FILE):
                ledger = _read(self.store)
                self._unreadable_said = False
                self._usage_hold(ledger, now)
                for project in document.get("projects") or []:
                    self._project(project, ledger, now, actions, launches, nudges)
                self._usage_notice(ledger, now, actions)
                self.store.write(LEDGER_FILE, ledger)
        except LedgerError as exc:
            self.log(f"oracle watch: the chain does nothing this tick: {exc}")
            if not self._unreadable_said and self.notifier is not None:
                self._unreadable_said = True
                try:
                    self.notifier.notify(Notification(
                        title="invariant: chain", subtitle="the chain is halted",
                        message=str(exc), sound=_SOUND))
                except Exception as error:  # noqa: BLE001 - said in the log
                    self.log(f"oracle watch: could not notify: {error}")
            return []
        for launch in launches:
            self._launch(launch, now, actions)
        for nudge in nudges:
            self._send(nudge, now, actions)
        for ask in self._asks:
            self._send_ask(ask, now, actions)
        for close in self._closes:
            self._send_close(close, now, actions)
        self._deliver()
        return actions

    # -- the weekly usage (ruling 10) -------------------------------------

    def _usage_hold(self, ledger: dict[str, Any], now: float) -> None:
        """This tick's hold, once for the machine; a greenlight a newer reading spent goes."""
        self._held_by_usage = False
        if self.usage is None:
            self._hold = None
            return
        reading = self.usage()
        block = _usage_state(ledger)
        if spent(reading, greenlit(ledger)):
            block["greenlight"] = None
        self._hold = hold(reading, now, self.weekly_max, greenlit(ledger))

    def _usage_notice(self, ledger: dict[str, Any], now: float, actions) -> None:
        """One notice per hold, said when it first holds something; cleared when it lifts."""
        block = _usage_state(ledger)
        if self._hold is None:
            block["held"] = None
            return
        if not self._held_by_usage:
            return
        said = block["held"]
        if said and said.get("kind") == self._hold.kind:
            return
        block["held"] = {"kind": self._hold.kind, "since": _iso(now)}
        actions.append(self._action("chain", "held", self._hold.reason))
        self._notice(ledger, "chain", "held", self._hold.reason, sound=_SOUND)

    # -- one project -------------------------------------------------------

    def _project(self, project: dict[str, Any], ledger: dict[str, Any], now: float,
                 actions: list[dict[str, Any]], launches: list[_Launch],
                 nudges: list[_Nudge] | None = None) -> None:
        entry = project.get("project") or {}
        key, name, root = entry.get("key"), entry.get("name") or "?", entry.get("root")
        if not key or not root or project.get("unread") is not None:
            return
        self._roots[key] = root
        state = _project(ledger, key)
        if state["current"]:
            self._follow(project, ledger, state, now, actions, nudges)
        if state["landed"]:
            self._landed(project, ledger, state, now, actions)
        if self._report_pause(project, ledger, state, now, actions):
            return
        if state["paused"] or state["current"]:
            return
        if any(r.get("kind") == ROW_SESSION and r.get("session_id") not in state["released"]
               for r in project.get("rows") or []):
            return
        if open_windows(project):
            return
        self._mirror(name, key, state, now, actions)
        try:
            inputs = self.reader(root)
        except (InvariantError, OSError, ValueError) as exc:
            self.log(f"oracle watch: the chain cannot read {name}: {exc}")
            return
        verdict = chain.decide(
            queue=inputs.queue, ready=inputs.ready, settings=inputs.settings, live=[],
            windows=0, now=datetime.fromtimestamp(now, timezone.utc).astimezone(), resumed=_resumed_epoch(state),
            started_today=_started_today(state, now), machine_live=_machine_live(ledger),
            machine_cap=self.machine_cap,
            usage_hold=self._hold.reason if self._hold else None,
            unreproduced=getattr(inputs, "unreproduced", None),
        )
        if verdict.limit == chain.WEEKLY_USAGE:
            self._held_by_usage = True
            return
        self._proposals(name, key, state, verdict, now, actions, ledger)
        if verdict.verdict == START and verdict.mode == AUTO:
            launch = self._reserve(ledger, state, key, name, root, verdict.candidate,
                                   inputs.settings, AUTO, now, actions)
            if launch is not None:
                launches.append(launch)
        elif verdict.verdict == WAIT and verdict.limit:
            held = f"{verdict.limit}|{local_day(now)}"
            if held not in state["held"]:
                state["held"][held] = {"since": _iso(now)}
                detail = verdict.reasons[-1]
                actions.append(self._action(name, "held", detail))
                self._notice(ledger, name, "held", detail, sound=None)

    # -- the backlog mirror (increment 5) --------------------------------

    def _mirror(self, name: str, key: str, state: dict[str, Any], now: float,
                actions: list[dict[str, Any]]) -> None:
        """Have the hands mirror the backlog onto the queue, once a minute at most.

        A mirror that fails is logged and the step goes on: the queue as it is
        still decides. What it changed is an action, with no notice: an item
        reaching the queue is not news the user has to act on.
        """
        if self.hands.mirror is None:
            return
        last = state.get("mirrored_epoch")
        if isinstance(last, (int, float)) and now - last < MIRROR_SECONDS:
            return
        state["mirrored_epoch"] = now
        try:
            out = self.hands.mirror(key) or {}
        except (InvariantError, OSError, ValueError) as exc:
            self.log(f"oracle watch: the chain cannot mirror {name}'s backlog: {exc}")
            return
        parts = []
        if out.get("added"):
            parts.append("added " + ", ".join(f"{e} {r}" for e, r in out["added"]))
        if out.get("removed"):
            parts.append("removed " + ", ".join(f"{e} {r}" for e, r in out["removed"]))
        if parts:
            actions.append(self._action(name, "mirrored", "; ".join(parts)))

    # -- proposals -----------------------------------------------------

    def _proposals(self, name: str, key: str, state: dict[str, Any], verdict, now: float,
                   actions: list[dict[str, Any]], ledger: dict[str, Any]) -> None:
        offered: dict[str, Candidate] = {f"entry:{p.entry}": p for p in verdict.proposals}
        if verdict.verdict == START and verdict.mode == PROPOSE and verdict.candidate:
            c = verdict.candidate
            offered[f"{c.source}:{c.entry or c.ref}"] = c
        kept = {}
        for token, candidate in offered.items():
            kept[token] = state["proposed"].get(token) or {"since": _iso(now)}
            if token in state["proposed"]:
                continue
            what = f"{candidate.entry} {candidate.ref}" if candidate.entry else candidate.ref
            detail = (f"proposes {what}; start it with: invariant oracle chain start {name}"
                      + (f" --entry {candidate.entry}" if candidate.entry else ""))
            actions.append(self._action(name, "proposed", detail))
            self._notice(ledger, name, "proposed", detail, sound=_SOUND)
        state["proposed"] = kept

    # -- starting --------------------------------------------------------

    def _reserve(self, ledger, state, key, name, root, candidate: Candidate,
                 settings: ChainSettings, mode: str, now: float,
                 actions: list[dict[str, Any]]) -> _Launch | None:
        """Mark the entry started and hold the project's one start slot, under the lock."""
        try:
            entry = self.hands.begin(key, candidate.entry, candidate.ref)
        except InvariantError as exc:
            self.log(f"oracle watch: the chain cannot start {candidate.ref} in {name}: {exc}")
            return None
        ledger["next"] = int(ledger.get("next") or 1) + 1
        reservation = {
            "id": f"C{ledger['next'] - 1}", "state": LAUNCHING, "source": candidate.source,
            "ref": candidate.ref, "entry": entry, "mode": mode, "name": None, "pid": None,
            "by_pid": os.getpid(), "at": _iso(now), "at_epoch": now, "appeared_epoch": None,
            "session": None, "gone_since_epoch": None,
            "triage_of": candidate.triage_of, "retry_of": candidate.retry_of,
            "reproduce": candidate.reproduce, "residue": candidate.residue,
        }
        state["current"] = reservation
        state["starts"].append({"id": reservation["id"], "ref": candidate.ref, "entry": entry,
                                "at": _iso(now), "day": local_day(now), "mode": mode,
                                "session": None, "outcome": None})
        del state["starts"][:-STARTS_KEPT]
        resolved = replace(candidate, entry=entry)
        return _Launch(key, name, root, resolved, settings, mode, reservation)

    def _launch(self, launch: _Launch, now: float, actions: list[dict[str, Any]]) -> dict | None:
        """Open the window outside the lock, then record what happened under it."""
        candidate = launch.candidate
        error: str | None = None
        opened: dict[str, Any] | None = None
        try:
            opened = self.hands.start(launch.key, chain.label_for(candidate),
                                      chain.render_prompt(launch.settings, candidate))
        except Exception as exc:  # noqa: BLE001 - any launch failure is triaged
            error = str(exc) or type(exc).__name__
        with self.store.lock(LEDGER_FILE):
            ledger = _read(self.store)
            state = _project(ledger, launch.key)
            current = state["current"]
            if not current or current.get("id") != launch.reservation["id"]:
                current = state["current"] = dict(launch.reservation)
            if opened is None:
                self._record_start(state, current, detail=error)
                self._fail(launch.name, launch.key, ledger, state, current, LAUNCH_FAILED,
                           launch.settings, now, actions, detail=error)
            else:
                current.update(state=RUNNING, name=opened.get("name"), pid=opened.get("pid"),
                               appeared_epoch=now)
                what = f"{candidate.entry} {candidate.ref}"
                verb = "to reproduce" if candidate.reproduce else "for"
                detail = f"started {opened.get('name')} {verb} {what}"
                actions.append(self._action(launch.name, "started", detail))
                self._notice(ledger, launch.name, "started", detail, sound=None)
            self.store.write(LEDGER_FILE, ledger)
        return opened

    def start_now(self, key: str, name: str, root: str, candidate: Candidate,
                  settings: ChainSettings, now: float) -> tuple[list[dict], dict | None]:
        """A user's start (``oracle chain start``): recorded as chained, never held.

        Refused (an :class:`InvariantError` that exits 2) only while the
        project already has a chained start of its own, because the ledger
        follows one at a time, or when its entry cannot be marked started.
        Returns the actions and what opened: ``None`` when the launch failed,
        which is then triaged as any failure is.
        """
        actions: list[dict[str, Any]] = []
        self._roots[key] = root
        with self.store.lock(LEDGER_FILE):
            ledger = _read(self.store)
            state = _project(ledger, key)
            current = state["current"]
            if current:
                raise _Busy(
                    f"{name} already has a chained start, {current.get('name') or current.get('ref')} "
                    f"({current.get('state')}); the chain follows one at a time. "
                    f"`invariant oracle start-session {name}` opens another window by hand"
                )
            launch = self._reserve(ledger, state, key, name, root, candidate, settings,
                                   "accepted", now, actions)
            self.store.write(LEDGER_FILE, ledger)
        if launch is None:
            raise _Busy(f"{candidate.ref} could not be marked started in {name}'s queue")
        opened = self._launch(launch, now, actions)
        self._deliver()
        return actions, opened

    def current(self, key: str) -> dict[str, Any] | None:
        """The chained start the ledger follows in ``key``, read without a lock."""
        try:
            return _project(_read(self.store), key)["current"]
        except LedgerError:
            return None

    # -- following -------------------------------------------------------

    def _follow(self, project, ledger, state, now: float, actions, nudges=None) -> None:
        name = (project.get("project") or {}).get("name") or "?"
        key = (project.get("project") or {}).get("key")
        root = (project.get("project") or {}).get("root")
        current = state["current"]
        settings = None  # read from the project only if a failure needs its triage prompt
        if current.get("state") == LAUNCHING:
            at = current.get("at_epoch")
            age = now - at if isinstance(at, (int, float)) else STALE_RESERVATION_SECONDS
            mine = current.get("by_pid") == os.getpid()
            if age >= STALE_RESERVATION_SECONDS or (
                    not mine and not self.pid_alive(current.get("by_pid"))):
                self._record_start(state, current, detail=INTERRUPTED)
                self._fail(name, key, ledger, state, current, INTERRUPTED, settings, now,
                           actions)
            return
        rows = project.get("rows") or []
        bound = self._bound(rows, current)
        if bound is not None and bound != current.get("session"):
            current["session"] = bound
            self._mark(key, current.get("entry"), "started", session=bound)
            actions.append(self._action(
                name, "bound", f"{current.get('entry')} {current.get('ref')} is session {bound}"))
        session = current.get("session")
        if session:
            row = next((r for r in rows if r.get("kind") == ROW_SESSION
                        and r.get("session_id") == session), None)
            if row is None:
                end = self.session_end(root, session)
                if end == chain.END_LIVE:
                    return
                if end == chain.END_DONE:
                    self._done(name, key, state, current, actions, root, ledger, now)
                    return
                self._record_start(state, current, detail=ENDED[end])
                self._fail(name, key, ledger, state, current, ENDED[end], settings, now,
                           actions)
                return
            if ((row.get("report") or {}).get("outcome")) in ("done", *chain.STOPS):
                current["gone_since_epoch"] = None
                return
            self._stall(name, key, ledger, current, row, now, actions, nudges)
            hosts = row.get("hosts") or []
            gone = not any(h.get("alive") is not False for h in hosts)
        else:
            appeared = current.get("appeared_epoch")
            if isinstance(appeared, (int, float)) and now - appeared >= UNBOUND_SECONDS:
                self._record_start(state, current, detail=UNBOUND)
                self._fail(name, key, ledger, state, current, UNBOUND, settings, now, actions)
                return
            hosts = self._hosts_of(rows, current)
            gone = bool(hosts) and all(h.get("alive") is False for h in hosts)
        if gone and not open_windows(project):
            since = current.get("gone_since_epoch")
            if not isinstance(since, (int, float)):
                current["gone_since_epoch"] = now
            elif now - since >= GONE_SECONDS:
                self._record_start(state, current, detail=GONE)
                self._fail(name, key, ledger, state, current, GONE, settings, now, actions)
        else:
            current["gone_since_epoch"] = None

    @staticmethod
    def _hosts_of(rows, current) -> list[dict[str, Any]]:
        found = []
        for row in rows:
            if row.get("kind") not in (ROW_SESSION, ROW_HOST):
                continue
            for host in row.get("hosts") or []:
                if _is_started(host, current):
                    found.append(host)
        return found

    @staticmethod
    def _bound(rows, current) -> str | None:
        for row in rows:
            if row.get("kind") != ROW_SESSION:
                continue
            if any(_is_started(h, current) for h in row.get("hosts") or []):
                return row.get("session_id")
        return None

    # -- stalls (rulings 11 and 12) ---------------------------------------

    def _stall(self, name, key, ledger, current, row, now: float, actions, nudges) -> None:
        """A chained session the scan reads ``stalled``: one nudge, then a notice.

        The stall is kept on ``current["stall"]`` while the signal lasts. The
        nudge is reserved here, under the lock, and typed outside it. A stall
        whose quiet began within one fuse of the last nudge is the same stall
        going on: it gets the "still stalled" notice, not a second nudge.
        """
        signal = next((s for s in row.get("signals") or [] if s.get("kind") == _STALLED), None)
        if signal is None:
            current["stall"] = None
            return
        since = row.get("last_activity")
        fuse = float(row.get("stall_after") or 2.0) * 3600
        host = _current_host(row)
        what = f"{current.get('entry')} {current.get('ref')}"
        stall = current.get("stall")
        if not isinstance(stall, dict) or stall.get("since_epoch") != since:
            last = current.get("nudged_epoch")
            still = (isinstance(last, (int, float)) and isinstance(since, (int, float))
                     and since < last + fuse)
            end = (host or {}).get("turn_end") or {}
            cause = signal.get("cause") or "unknown"
            stall = current["stall"] = {
                "since_epoch": since, "cause": cause,
                "resets_at": end.get("resets_at") if cause == _LIMIT else None,
                "nudge": None, "nudged_epoch": None, "still": still, "still_said": False,
                "waiting_said": False,
            }
        if stall["still"]:
            self._still(name, ledger, stall, what, actions)
            return
        if stall["nudge"] is None:
            resets = stall.get("resets_at")
            if (stall["cause"] == _LIMIT and isinstance(resets, (int, float))
                    and now < resets + RESET_MARGIN_SECONDS):
                if not stall["waiting_said"]:
                    stall["waiting_said"] = True
                    detail = (f"{what} stalled at a usage limit; it is nudged with "
                              f"\"{CONTINUE}\" after its reset")
                    actions.append(self._action(name, "stalled", detail))
                    self._notice(ledger, name, "stalled", detail, sound=None)
                return
            tty = (host or {}).get("tty")
            if not tty or self.hands.nudge is None:
                stall["nudge"] = "no_tab"
                self._not_nudged(name, ledger, what, "its Terminal tab cannot be found",
                                 actions)
                return
            stall["nudge"] = "sending"
            stall["nudged_epoch"] = now
            current["nudged_epoch"] = now
            if nudges is not None:
                nudges.append(_Nudge(key, name, tty, current.get("id"), since, what))
            return
        nudged = stall.get("nudged_epoch")
        if (stall["nudge"] in ("typed", "sending") and isinstance(nudged, (int, float))
                and now - nudged >= fuse):
            self._still(name, ledger, stall, what, actions)

    def _still(self, name, ledger, stall, what, actions) -> None:
        if stall["still_said"]:
            return
        stall["still_said"] = True
        detail = (f"{what} is still stalled after one nudge: invariant oracle chain retry "
                  f"{name}, continue {name} or close {name}")
        actions.append(self._action(name, "still stalled", detail))
        self._notice(ledger, name, "still stalled", detail, sound=_SOUND)

    def _not_nudged(self, name, ledger, what, why, actions) -> None:
        detail = (f"{what} is stalled and was not nudged: {why}. invariant oracle chain "
                  f"retry {name}, continue {name} or close {name}")
        actions.append(self._action(name, "not nudged", detail))
        self._notice(ledger, name, "not nudged", detail, sound=_SOUND)

    def _type(self, tty: str) -> tuple[str, str]:
        """Type the "continue" under the tab's typist lock, which the page's
        talk path takes too (ADR-029 D6): one typist per tab. A tab whose lock
        another typist holds past :data:`talk.TYPIST_WAIT` is not nudged."""
        if self.hands.nudge is None:
            return "failed", "this chain cannot type into a window"
        try:
            with typist(self.home, tty):
                code, detail = self.hands.nudge(tty, CONTINUE)
                if code == "typed":
                    self.pause(TYPED_SETTLE)
                return code, detail
        except StateError as exc:
            return "busy", str(exc)
        except Exception as exc:  # noqa: BLE001 - a nudge that fails is said
            return "failed", str(exc) or type(exc).__name__

    def _send(self, nudge: _Nudge, now: float, actions) -> None:
        """Type the reserved "continue" outside the lock, then record what happened."""
        code, detail = self._type(nudge.tty)
        with self.store.lock(LEDGER_FILE):
            ledger = _read(self.store)
            state = _project(ledger, nudge.key)
            current = state["current"]
            stall = (current or {}).get("stall")
            if not current or current.get("id") != nudge.start or not isinstance(stall, dict):
                return
            stall["nudge"] = code
            if code == "typed":
                detail_line = f"typed \"{CONTINUE}\" into {current.get('name')} for {nudge.what}"
                actions.append(self._action(nudge.name, "nudged", detail_line))
                self._notice(ledger, nudge.name, "nudged", detail_line, sound=None)
            else:
                self._not_nudged(nudge.name, ledger, nudge.what,
                                 f"typing into its tab failed ({code}"
                                 + (f": {detail})" if detail else ")"), actions)
            self.store.write(LEDGER_FILE, ledger)

    def stall_action(self, key: str, name: str, action: str, host: dict[str, Any] | None,
                     now: float) -> list[dict[str, Any]]:
        """The user's answer to a stalled chained session (ruling 12).

        ``continue`` types one more "continue" and re-arms the fuse; ``retry``
        closes the window and puts the same entry back at the head, queued,
        for the next tick (not ruling 9's one retry); ``close`` closes the
        window and marks the entry failed, with no triage. Both release the
        session (ruling 13). ``host`` is the session's live window as a fresh
        scan saw it, ``None`` when it is gone. Refused (exit 2) without a
        chained start whose stall is recorded; a window that cannot be closed
        or typed into raises, and the ledger and the queue are left as they
        were.
        """
        if action not in ("continue", "retry", "close"):
            raise ValueError(f"not a stall action: {action!r}")
        current = self.current(key)
        if not current:
            raise _Busy(f"{name} has no chained start to act on")
        if not isinstance(current.get("stall"), dict):
            raise _Busy(f"{name}'s chained start {current.get('entry')} {current.get('ref')} "
                        f"is not stalled; nothing was done")
        what = f"{current.get('entry')} {current.get('ref')}"
        actions: list[dict[str, Any]] = []
        if action == "continue":
            tty = (host or {}).get("tty")
            if not tty:
                raise _Busy(f"{what}'s Terminal tab cannot be found; nothing was typed")
            code, detail = self._type(tty)
            if code != "typed":
                raise InvariantError(f"could not type into {what}'s tab ({code}"
                                     + (f": {detail})" if detail else ")"))
            with self.store.lock(LEDGER_FILE):
                ledger = _read(self.store)
                mine = _project(ledger, key)["current"]
                if mine and mine.get("id") == current.get("id") and mine.get("stall"):
                    mine["nudged_epoch"] = now
                    mine["stall"].update(nudge="typed", nudged_epoch=now, still=False,
                                         still_said=False)
                    self.store.write(LEDGER_FILE, ledger)
            actions.append(self._action(name, "continued",
                                        f"typed \"{CONTINUE}\" into {what}'s tab again"))
            return actions
        lines = (self.hands.close(host) if host and self.hands.close is not None
                 else ["its window had already closed"])
        with self.store.lock(LEDGER_FILE):
            ledger = _read(self.store)
            state = _project(ledger, key)
            mine = state["current"]
            if not mine or mine.get("id") != current.get("id"):
                raise _Busy(f"{name}'s chained start moved on while its window closed")
            entry = mine.get("entry")
            if action == "retry":
                reason = RETRIED_BY_USER
                if entry is not None and self.hands.requeue is not None:
                    self.hands.requeue(key, entry)
                done = f"{what} is back at the head of the queue, started fresh on the next tick"
            else:
                reason = CLOSED_BY_USER
                self._mark(key, entry, "failed", reason=reason)
                done = f"{what} is marked failed ({reason}); the chain goes on"
            self._record_start(state, mine, detail=reason)
            state["current"] = None
            _release(state, mine)
            self.store.write(LEDGER_FILE, ledger)
        word = "retried" if action == "retry" else "closed"
        actions.append(self._action(name, word, "; ".join([*lines, done])))
        return actions

    def _done(self, name, key, state, current, actions, root=None, ledger=None,
              now: float | None = None) -> None:
        if not (current.get("reproduce") and self._reproduced(name, key, current, actions,
                                                               root, ledger)):
            self._mark(key, current.get("entry"), "done")
        self._record_start(state, current, outcome="done")
        state["current"] = None
        if self.hands.close is not None and now is not None:
            state["landed"] = self._landing(key, current, root, now)
        actions.append(self._action(
            name, "done", f"{current.get('entry')} {current.get('ref')} landed "
                          f"(session {current.get('session')} torn down)"))

    # -- the landed window (ruling 21) -----------------------------------------

    def _landing(self, key, current, root, now: float) -> dict[str, Any]:
        """The landed window's record, with the project's ``close_*`` settings of today."""
        root = root or self._roots.get(key)
        settings = self._settings(root)
        ended = None
        if root and current.get("session"):
            try:
                ended = self.landed_at(root, current["session"])
            except (InvariantError, OSError, ValueError) as exc:
                self.log(f"oracle watch: the chain cannot read when "
                         f"{current.get('session')} landed: {exc}")
        landed_epoch = float(ended) if isinstance(ended, (int, float)) else now
        return {
            "id": current.get("id"), "entry": current.get("entry"), "ref": current.get("ref"),
            "session": current.get("session"), "name": current.get("name"),
            "pid": current.get("pid"), "landed_epoch": landed_epoch,
            "landed": _iso(landed_epoch), "state": WATCHING, "host": None, "tty": None,
            "baseline": None, "asks": [], "answers": [], "notice": None,
            "gone_since_epoch": None, "ask_at_epoch": None, "consented_epoch": None,
            "limits": {"ask_after": settings.close_ask_after * 60.0,
                       "ask_again": settings.close_ask_again * 60.0,
                       "asks": int(settings.close_asks),
                       "answer_within": settings.close_answer_within * 60.0},
        }

    @staticmethod
    def _landed_window(rows, landed) -> tuple[dict | None, dict | None]:
        """The landed window's host as the tick saw it, and the row it is on."""
        found: tuple[dict | None, dict | None] = (None, None)
        for row in rows:
            if row.get("kind") not in (ROW_SESSION, ROW_HOST):
                continue
            for host in row.get("hosts") or []:
                if _is_started(host, landed):
                    if host.get("alive") is True:
                        return host, row
                    found = (host, row)
        return found

    def _landed(self, project, ledger, state, now: float, actions) -> None:
        """Follow a landed window: ask it once quiet, act on its answer, or tell the user."""
        name = (project.get("project") or {}).get("name") or "?"
        key = (project.get("project") or {}).get("key")
        landed = state["landed"]
        what = f"{landed.get('entry')} {landed.get('ref')}"
        host, row = self._landed_window(project.get("rows") or [], landed)
        if host is None or host.get("alive") is not True:
            since = landed.get("gone_since_epoch")
            if not isinstance(since, (int, float)):
                landed["gone_since_epoch"] = now
            elif now - since >= GONE_SECONDS:
                _end_landed(state, landed, OUT_GONE, now)
                actions.append(self._action(name, "window closed",
                                            f"the window of {what} closed"))
            return
        landed.update(gone_since_epoch=None, host=host.get("host_session_id"),
                      tty=host.get("tty"))
        phase = landed.get("state")
        if phase in (USERS, LEFT_OPEN):
            return
        record = host.get("attention")
        if self._users(landed, row, record):
            landed["state"] = USERS
            text = (f"{what} landed, and you typed in its window {landed.get('name')} since, "
                    f"so the window is yours: it holds {name} until you close it, and the "
                    f"oracle will not ask it or close it")
            actions.append(self._action(name, "your window", text))
            self._notice(ledger, name, "your window", text, sound=_SOUND)
            return
        if phase == NOTIFIED:
            return
        if phase in (ASKING, CLOSING):
            # typed or closed outside the lock by a watcher; one that stopped
            # half way never said how it went
            at = ((landed.get("asks") or [{}])[-1].get("at_epoch") if phase == ASKING
                  else landed.get("closing_epoch"))
            if not isinstance(at, (int, float)) or now - at >= STALE_RESERVATION_SECONDS:
                if phase == ASKING:
                    landed["asks"][-1]["code"] = "lost"
                    self._tell(name, ledger, landed, NOT_TYPED, now, actions,
                               detail="the watcher typing it stopped")
                else:
                    self._tell(name, ledger, landed, CLOSE_FAILED, now, actions,
                               detail="the watcher closing it stopped")
            return
        if not isinstance(record, dict) or row.get("last_activity") is None:
            self._tell(name, ledger, landed, UNREAD, now, actions)
            return
        limits = landed.get("limits") or {}
        last = row.get("last_activity")
        quiet = (row.get("turn_state") == TURN_IDLE and isinstance(last, (int, float))
                 and now - last >= float(limits.get("ask_after") or 0))
        if phase == WATCHING:
            due = landed.get("ask_at_epoch")
            if isinstance(due, (int, float)) and now < due or not quiet:
                return
            tty = host.get("tty")
            if not tty or self.hands.nudge is None:
                self._tell(name, ledger, landed, NOT_TYPED, now, actions,
                           detail="its Terminal tab cannot be found")
                return
            if landed.get("baseline") is None:
                landed["baseline"] = _count(record)
            landed["asks"].append({"at": _iso(now), "at_epoch": now, "code": "sending"})
            landed["state"] = ASKING
            self._asks.append(_Ask(key, name, tty, landed.get("id"), what))
            return
        if phase == ASKED:
            at = (landed.get("asks") or [{}])[-1].get("at_epoch")
            if (isinstance(at, (int, float))
                    and now - at >= float(limits.get("answer_within") or 0)):
                self._tell(name, ledger, landed, NO_ANSWER, now, actions)
            return
        if phase == CONSENTED:
            consented = landed.get("consented_epoch")
            waited = (now - consented if isinstance(consented, (int, float))
                      else CONSENT_GRACE_SECONDS)
            if row.get("turn_state") != TURN_IDLE and waited < CONSENT_GRACE_SECONDS:
                return
            landed.update(state=CLOSING, closing_epoch=now)
            self._closes.append(_Close(key, name, dict(host), landed.get("id"), what))
            return

    @staticmethod
    def _users(landed, row, record) -> bool:
        """Whether a turn the user typed since the teardown makes the window theirs.

        A live session using the window is someone's turn. Before the first
        ask, a person's prompt after the teardown is; after it, more prompts
        than the oracle's own asks typed are. A record that cannot be read is
        not proof either way: the caller then asks the user instead.
        """
        if row is not None and row.get("kind") == ROW_SESSION:
            return True
        if not isinstance(record, dict):
            return False
        baseline = landed.get("baseline")
        if baseline is None:
            prompted = _epoch(record.get("prompted"))
            landed_epoch = landed.get("landed_epoch")
            return (prompted is not None and isinstance(landed_epoch, (int, float))
                    and prompted > landed_epoch)
        return _count(record) > int(baseline) + _typed(landed)

    def _tell(self, name, ledger, landed, why, now, actions, detail=None) -> None:
        text = _notify_landed(ledger, name, landed, why, now, detail=detail)
        actions.append(self._action(name, "window open", text))

    def _send_ask(self, ask: _Ask, now: float, actions) -> None:
        """Type the reserved ask outside the lock, then record what happened."""
        try:  # reserved only where the hands can type (``_landed``)
            code, detail = self.hands.nudge(ask.tty, chain.ask_line(ask.name))
        except Exception as exc:  # noqa: BLE001 - an ask that fails is said
            code, detail = "failed", str(exc) or type(exc).__name__
        with self.store.lock(LEDGER_FILE):
            ledger = _read(self.store)
            state = _project(ledger, ask.key)
            landed = state["landed"]
            if not landed or landed.get("id") != ask.landed or landed.get("state") != ASKING:
                return
            last = landed["asks"][-1]
            last["code"] = code
            if code == "typed":
                landed["state"] = ASKED
                limits = landed.get("limits") or {}
                actions.append(self._action(
                    ask.name, "asked",
                    f"asked {landed.get('name')}, the window of {ask.what}, whether it may "
                    f"close (ask {_typed(landed)} of {limits.get('asks')})"))
            else:
                if detail:
                    last["detail"] = detail
                self._tell(ask.name, ledger, landed, NOT_TYPED, now, actions,
                           detail=f"{code}: {detail}" if detail else code)
            self.store.write(LEDGER_FILE, ledger)

    def _send_close(self, close: _Close, now: float, actions) -> None:
        """Close a consenting window outside the lock, then record what happened."""
        error = None
        lines: list[str] = []
        try:
            lines = list(self.hands.close(close.host)) if self.hands.close else []
        except Exception as exc:  # noqa: BLE001 - a close that fails is told the user
            error = str(exc) or type(exc).__name__
        with self.store.lock(LEDGER_FILE):
            ledger = _read(self.store)
            state = _project(ledger, close.key)
            landed = state["landed"]
            if not landed or landed.get("id") != close.landed or landed.get("state") != CLOSING:
                return
            if error is not None:
                self._tell(close.name, ledger, landed, CLOSE_FAILED, now, actions,
                           detail=error)
            else:
                landed["closed"] = {"at": _iso(now), "lines": lines}
                _end_landed(state, landed, OUT_CLOSED, now)
                text = (f"closed {landed.get('name')}, the window of {close.what}, with its "
                        f"consent; the chain goes on")
                actions.append(self._action(close.name, "closed", text))
                self._notice(ledger, close.name, "closed", text, sound=None)
            self.store.write(LEDGER_FILE, ledger)

    def landed(self, key: str) -> dict[str, Any] | None:
        """The landed window the ledger follows in ``key``, read without a lock."""
        try:
            return _project(_read(self.store), key)["landed"]
        except LedgerError:
            return None

    def landed_action(self, key: str, name: str, action: str, host: dict[str, Any] | None,
                      now: float) -> list[dict[str, Any]]:
        """The user's answer to a landed window's notice: Close or Leave open (ruling 21).

        ``close-window`` ends the window through the hands' closer
        (close-host's path, every fact checked again) and releases its
        session; ``leave-open`` records that it stays: it holds the project
        and is never asked again. ``host`` is the window as a fresh scan saw
        it, ``None`` when it is gone. Refused (exit 2) unless the landed
        window waits for the user, after its notice.
        """
        if action not in (CLOSE_WINDOW, LEAVE_OPEN):
            raise ValueError(f"not a landed window's action: {action!r}")
        landed = self.landed(key)
        if not landed:
            raise _Busy(f"{name} has no landed window open; nothing was done")
        what = f"{landed.get('entry')} {landed.get('ref')}"
        if landed.get("state") != NOTIFIED:
            raise _Busy(f"no answer waits for you on the window of {what}: "
                        f"it is {landed.get('state')}; nothing was done")
        if action == LEAVE_OPEN:
            with self.store.lock(LEDGER_FILE):
                ledger = _read(self.store)
                mine = _project(ledger, key)["landed"]
                if not mine or mine.get("id") != landed.get("id"):
                    raise _Busy(f"{name}'s landed window moved on; nothing was done")
                mine.update(state=LEFT_OPEN,
                            decided={"what": "leave_open", "by": "user", "at": _iso(now)})
                self.store.write(LEDGER_FILE, ledger)
            return [self._action(name, "left open",
                                 f"the window of {what} is left open; it holds {name} until "
                                 f"you close it, and is not asked again")]
        lines = (self.hands.close(host) if host and self.hands.close is not None
                 else ["its window had already closed"])
        with self.store.lock(LEDGER_FILE):
            ledger = _read(self.store)
            state = _project(ledger, key)
            mine = state["landed"]
            if not mine or mine.get("id") != landed.get("id") or mine.get("state") != NOTIFIED:
                raise _Busy(f"{name}'s landed window moved on while it closed")
            mine["decided"] = {"what": "close", "by": "user", "at": _iso(now)}
            mine["closed"] = {"at": _iso(now), "lines": list(lines)}
            _end_landed(state, mine, OUT_CLOSED_BY_USER, now)
            self.store.write(LEDGER_FILE, ledger)
        return [self._action(name, "closed", "; ".join(
            [*lines, f"the window of {what} is closed; the chain goes on"]))]

    #: What each reproduce verdict says, after "<entry> <ref>".
    _VERDICT_WORDS: ClassVar[dict[str, str]] = {
        "red": "is reproduced red; its fix is at the head of the queue and starts next",
        "closed": "was closed by its reproduction; its entry is done",
        "owed": "owes a live-environment repro; its entry waits for you as a proposal",
        "blind": "was not seen by its reproduction; it runs once more, or waits for you "
                 "after a second such run",
    }

    def _reproduced(self, name, key, current, actions, root, ledger) -> bool:
        """Read a landed reproduce-first session's verdict and have the hands route it.

        ``False`` when it cannot be read or routed: the entry is then marked
        done as any other, and the mirror's own rules say what comes back.
        """
        if self.hands.reproduced is None:
            return False
        entry, ref = current.get("entry"), current.get("ref")
        try:
            outcome = self.outcome(root or self._roots.get(key) or "", ref,
                                   current.get("residue"))
            self.hands.reproduced(key, entry, outcome)
        except (InvariantError, OSError, ValueError) as exc:
            self.log(f"oracle watch: the chain cannot read {ref}'s reproduction in {name}: "
                     f"{exc}")
            return False
        detail = f"{entry} {ref} {self._VERDICT_WORDS.get(outcome, outcome)}"
        actions.append(self._action(name, "reproduced", detail))
        if ledger is not None:
            self._notice(ledger, name, "reproduced", detail, sound=None)
        return True

    def _fail(self, name, key, ledger, state, current, reason: str, settings, now: float,
              actions, detail: str | None = None) -> None:
        """Ruling 9: mark the entry failed and queue its triage, or pause on a triage.

        The session it started, if any, is released (ruling 13).
        """
        state["current"] = None
        _release(state, current)
        entry = current.get("entry")
        action, retry = chain.on_failure(current.get("triage_of"), current.get("retry_of"))
        what = f"{entry} {current.get('ref')}"
        if action == "pause":
            # The pause is owed whether or not the entry can be marked: a
            # refusal raised here would abandon the tick before the ledger is
            # written, and every later tick would fail the same way unpaused.
            try:
                self._fail_entry(key, entry, reason, None, False)
            except InvariantError as exc:
                self.log(f"oracle watch: the chain could not mark {entry} failed: {exc}")
            self._pause(name, state, ledger, now, actions,
                        f"the triage {what} failed: {reason}; nobody is left to judge it")
            return
        template = (settings or self._settings(self._roots.get(key))).triage_prompt
        try:
            out = self._fail_entry(key, entry, reason, template, retry)
        except InvariantError as exc:
            self._pause(name, state, ledger, now, actions,
                        f"{what} failed ({reason}) and its triage could not be queued: {exc}")
            return
        queued = f"triage {out.get('triage')} queued at the head"
        queued += f", retry {out['retry']} behind it" if out.get("retry") else ", no retry left"
        text = f"{what} failed: {reason}" + (f" ({detail})" if detail else "") + f"; {queued}"
        actions.append(self._action(name, "failed", text))
        self._notice(ledger, name, "failed", text, sound=_SOUND)

    def _fail_entry(self, key, entry, reason, template, retry) -> dict[str, Any]:
        if entry is None:
            return {"failed": None, "triage": None, "retry": None}
        return self.hands.fail(key, entry, reason, template, retry)

    def _settings(self, root: str | None) -> ChainSettings:
        """The project's chain settings, for a failure found while following."""
        if root:
            try:
                return self.reader(root).settings
            except (InvariantError, OSError, ValueError) as exc:
                self.log(f"oracle watch: the chain cannot read {root}'s settings: {exc}")
        return ChainSettings()

    def _pause(self, name, state, ledger, now, actions, reason: str, by: str = "failure") -> None:
        state["paused"] = {"reason": reason, "by": by, "since": _iso(now)}
        actions.append(self._action(name, "paused", reason))
        self._notice(ledger, name, "paused", f"the chain is paused: {reason}. Resume it with "
                     f"invariant oracle chain resume {name}", sound=_SOUND)

    def _report_pause(self, project, ledger, state, now, actions) -> bool:
        if state["paused"]:
            return True
        name = (project.get("project") or {}).get("name") or "?"
        resumed = _resumed_epoch(state)
        for row in project.get("rows") or []:
            if row.get("kind") != ROW_SESSION:
                continue
            report = row.get("report") or {}
            if report.get("outcome") not in chain.STOPS or chain._before(report, resumed):
                continue
            note = f": {report['note']}" if report.get("note") else ""
            label = row.get("name") or row.get("session_id")
            self._pause(name, state, ledger, now, actions,
                        f"session {label} reported {report['outcome']}{note}", by="report")
            return True
        return False

    def _mark(self, key, entry, state, session=None, reason=None) -> None:
        if entry is None:
            return
        try:
            self.hands.mark(key, entry, state, session, reason)
        except InvariantError as exc:
            self.log(f"oracle watch: the chain could not mark {entry} {state}: {exc}")

    @staticmethod
    def _record_start(state, current, outcome: str = "failed", detail: str | None = None) -> None:
        for start in reversed(state["starts"]):
            if isinstance(start, dict) and start.get("id") == current.get("id"):
                start["outcome"] = outcome
                start["session"] = current.get("session")
                if detail:
                    start["detail"] = detail
                return

    # -- notices -----------------------------------------------------------

    @staticmethod
    def _action(project: str, action: str, detail: str) -> dict[str, Any]:
        return {"project": project, "action": action, "detail": detail}

    @staticmethod
    def _notice(ledger, project: str, action: str, message: str, sound: str | None) -> None:
        notices = ledger.setdefault("notices", [])
        notices.append({"title": f"invariant: {project}", "subtitle": f"chain {action}",
                        "message": message, "sound": sound})
        del notices[:-NOTICES_KEPT]

    def _deliver(self) -> None:
        """Send every owed notice; what fails stays owed for the next tick."""
        if self.notifier is None:
            return
        with self.store.lock(LEDGER_FILE):
            try:
                ledger = _read(self.store)
            except LedgerError:
                return
            owed = ledger.get("notices") or []
            left = []
            delivering = True
            for notice in owed:
                if delivering:
                    try:
                        self.notifier.notify(Notification(
                            title=notice.get("title", "invariant"),
                            message=notice.get("message", ""),
                            subtitle=notice.get("subtitle", ""),
                            sound=notice.get("sound")))
                        continue
                    except Exception as exc:  # noqa: BLE001 - owed, retried next tick
                        self.log(f"oracle watch: could not notify: {exc}")
                        delivering = False
                left.append(notice)
            if len(left) != len(owed):
                ledger["notices"] = left
                self.store.write(LEDGER_FILE, ledger)


class _Busy(InvariantError):
    """A user's chained start refused: the project already has one to follow."""

    exit_code = 2


def _count(record: dict[str, Any]) -> int:
    """The person's prompts an attention record counts; a record before them, none."""
    value = record.get("prompts")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _epoch(value: Any) -> float | None:
    """An ISO 8601 time as an epoch; ``None`` for anything else."""
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _current_host(row: dict[str, Any]) -> dict[str, Any] | None:
    """A session row's current host, as the ``assess`` document carries it."""
    hosts = row.get("hosts") or []
    return next((h for h in hosts if h.get("current", True)), None)


def _is_started(host: dict[str, Any], current: dict[str, Any]) -> bool:
    name, pid = current.get("name"), current.get("pid")
    if name and host.get("launched_name") == name:
        return True
    return pid is not None and host.get("pid") == pid
