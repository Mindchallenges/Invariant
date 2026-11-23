"""The chain as the phone sees it, and the actions it offers (ADR-027 increment 3).

The phone panel shows each project's chain and acts on it (rulings 14 to 16).
This module is the one place that says what it shows and what may be done:

* **The read** (:class:`ChainReader`): one read of the chain ledger (without
  its lock, as ``oracle next`` reads it), each registered project's queue and
  the plan's usage hold, at the moment the page or an action asks. Nothing is
  written. The CLI builds the reader with the usage seam and the cap it hands
  the chain step (``_oracle_chain_reader``), and gives it to the page's
  ``RemoteActions``; a server without one shows no chain and offers nothing.
* **What is offered** (``offered`` on each project, and the machine's): the
  queue's up, down and remove, the proposals' start, pause or resume, and the
  stall's Continue, Retry and Close where D29's verbs accept them, a landed
  window's Close and Leave open once its notice was sent (ruling 21), and the
  greenlight only while the hold is ``unknown`` (D25). ``oracle/panel`` words
  what is offered and draws a button for nothing else.
* **The step** (:func:`derive`): the ``invariant`` argv an offered action runs,
  with its folder and the facts its confirm names, derived from a fresh read.
  A move or a remove carries the position the read saw (``--if-queued-at``),
  so the queue refuses it under its lock if the entry moved since.

An action is named by a key, ``<project key>|chain:<action>[:<entry or item>]``,
which no row of the scan can have; the greenlight's project is ``oracle``,
since it is the machine's. Nothing here imports ``hosts/`` or runs anything.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.errors import InvariantError
from ..core.queue import PROPOSE as ENTRY_PROPOSE
from ..core.queue import QUEUED, Queue
from .scan import ROW_HOST, ROW_SESSION
from .usage import DEFAULT_WEEKLY_MAX, UNKNOWN, PlanUsage, hold, spent

CLI = "invariant"
#: The subject an action key carries after the project: ``chain:<action>``.
SUBJECT = "chain"
#: The greenlight's project: it holds or lets go every project at once.
MACHINE = "oracle"

MOVE_UP = "move_up"
MOVE_DOWN = "move_down"
REMOVE = "remove"
START = "start"
START_ITEM = "start_item"
PAUSE = "pause"
RESUME = "resume"
GREENLIGHT = "greenlight"
CONTINUE = "continue"
RETRY = "retry"
CLOSE = "close"
STALL_ACTIONS = (CONTINUE, RETRY, CLOSE)
#: A landed window's answers to its notice (ruling 21), named as the CLI's verbs.
CLOSE_WINDOW = "close-window"
LEAVE_OPEN = "leave-open"
LANDED_ACTIONS = (CLOSE_WINDOW, LEAVE_OPEN)
_WITH_ARG = frozenset({MOVE_UP, MOVE_DOWN, REMOVE, START, START_ITEM})
_BARE = frozenset({PAUSE, RESUME, GREENLIGHT, *STALL_ACTIONS, *LANDED_ACTIONS})

#: A project's chain, in one word: what the Projects row and the screen say.
RUNNING = "running"
STARTING = "starting"
PAUSED = "paused"
HELD = "held"
WAITING = "waiting"
IDLE = "idle"
UNREAD = "unread"

#: Where an unreadable project's error came from.
FROM_LEDGER = "ledger"
FROM_QUEUE = "queue"

#: A queue's path is found again after this many seconds: finding it reads
#: the manifest and asks git for the main checkout, too much for every read.
PATH_SECONDS = 60.0

#: A chained start still opening its window (``oracle/chainstep``).
_LAUNCHING = "launching"
#: A landed window waiting on the user's Close or Leave open (``oracle/chainstep``).
_NOTIFIED = "notified"


def action_key(project: str, action: str, arg: str | None = None) -> str:
    """The key an action is confirmed and run by."""
    return f"{project}|{SUBJECT}:{action}" + (f":{arg}" if arg else "")


def parse_key(key: str) -> tuple[str, str, str | None] | None:
    """``(project, action, arg)`` for an action key; ``None`` for any other key."""
    project, sep, subject = key.rpartition("|")
    if not sep or not subject.startswith(f"{SUBJECT}:"):
        return None
    action, _, arg = subject[len(SUBJECT) + 1:].partition(":")
    if action in _WITH_ARG and arg:
        return project, action, arg
    if action in _BARE and not arg:
        return project, action, None
    return None


def job_row(key: str) -> str:
    """What a chain action's job is keyed by: one chain action per project at a time."""
    return f"{key.rpartition('|')[0]}|{SUBJECT}"


def _offer(project: str, action: str, arg: str | None = None) -> dict[str, Any]:
    return {"action": action, "arg": arg, "key": action_key(project, action, arg)}


# -- the readers the CLI's reader uses by default ---------------------------------------


def _queue_path(root: str) -> Path:
    from ..core.manifest import Manifest
    from ..core.queue import queue_path

    return queue_path(Manifest.locate(Path(root)))


def item_title(root: str, ref: str) -> str | None:
    """A backlog item's title in the project at ``root``; ``None`` when it cannot be read."""
    from ..core.manifest import Manifest
    from ..domains.workitem import WorkItemDomain

    try:
        return WorkItemDomain(Manifest.locate(Path(root))).get(ref).title
    except (InvariantError, OSError, ValueError):
        return None


def _read_ledger(home: Path) -> dict[str, Any]:
    from .chainstep import read_ledger

    return read_ledger(home)


# -- the read -------------------------------------------------------------------------


@dataclass
class ChainReader:
    """Reads the chain for the phone: the ledger, the queues, the usage hold.

    ``usage`` is the plan's last reading, the seam the chain step reads it
    through; ``cap`` is ``chain.weekly_usage_max``. ``queues``, ``titles``
    and ``ledger`` replace the project readers in a test.
    """

    home: Path
    usage: Callable[[], PlanUsage | None]
    cap: float = DEFAULT_WEEKLY_MAX
    clock: Callable[[], float] = time.time
    queues: Callable[[str], Queue] | None = None
    titles: Callable[[str, str], str | None] = item_title
    ledger: Callable[[Path], dict[str, Any]] = _read_ledger
    _paths: dict[str, tuple[Path, float]] = field(default_factory=dict, repr=False)

    def queue_of(self, root: str) -> Queue:
        """The queue of the project at ``root``, its path found again once a minute."""
        if self.queues is not None:
            return self.queues(root)
        from ..core.queue import read_queue

        now = self.clock()
        found = self._paths.get(root)
        if found is None or now - found[1] >= PATH_SECONDS:
            found = self._paths[root] = (_queue_path(root), now)
        return read_queue(found[0])

    def read(self, document: Mapping[str, Any], only: str | None = None) -> dict[str, Any]:
        """The chain of every project ``document`` read (or ``only`` that one).

        ``error`` is why the ledger cannot be read; then nothing is offered
        anywhere. ``usage`` is the hold and the reading behind it, ``offered``
        the machine's own action (the greenlight), ``projects`` each project's
        chain by its key.
        """
        now = self.clock()
        reading = self.usage()
        try:
            ledger: dict[str, Any] | None = self.ledger(self.home)
            error = None
        except (InvariantError, OSError) as exc:
            ledger, error = None, f"the chain ledger cannot be read: {exc}"
        usage = self._usage(reading, ledger, now)
        projects: dict[str, dict[str, Any]] = {}
        for project in document.get("projects") or []:
            entry = project.get("project") or {}
            key, root = entry.get("key"), entry.get("root")
            if not key or not root or project.get("unread") is not None:
                continue
            if only is not None and key != only:
                continue
            projects[key] = self._project(entry, ledger, error, usage)
        offered = ([_offer(MACHINE, GREENLIGHT)]
                   if error is None and usage["hold"] == UNKNOWN else [])
        return {"now": now, "error": error, "usage": usage, "offered": offered,
                "projects": projects}

    def _usage(self, reading: PlanUsage | None, ledger: dict[str, Any] | None,
               now: float) -> dict[str, Any]:
        block = (ledger or {}).get("usage") or {}
        given = block.get("greenlight") if isinstance(block.get("greenlight"), dict) else None
        at = given.get("at_epoch") if given else None
        greenlight = (float(at) if isinstance(at, (int, float)) and not isinstance(at, bool)
                      else None)
        if spent(reading, greenlight):
            greenlight = None
        held = hold(reading, now, self.cap, greenlight)
        why = age = None
        if held is not None and held.kind == UNKNOWN:
            if reading is None:
                why = "missing"
            elif reading.weekly is None:
                why = "no_weekly"
            else:
                why, age = "stale", now - reading.read_at
        return {
            "hold": held.kind if held else None,
            "weekly": reading.weekly if reading else None,
            "five_hour": reading.five_hour if reading else None,
            "read_at": reading.read_at if reading else None,
            "cap": self.cap,
            "greenlight": given.get("at") if given and greenlight is not None else None,
            "held": isinstance(block.get("held"), dict),
            "why": why,
            "age": age,
        }

    def _project(self, entry: Mapping[str, Any], ledger: dict[str, Any] | None,
                 error: str | None, usage: dict[str, Any]) -> dict[str, Any]:
        key, root = entry["key"], entry["root"]
        out: dict[str, Any] = {
            "key": key, "name": entry.get("name") or "", "root": root, "status": UNREAD,
            "error": error, "error_from": FROM_LEDGER if error else None, "entries": [],
            "queued": [], "paused": None, "current": None, "proposals": [], "offered": [],
            "landed": None,
        }
        if ledger is None:
            return out
        try:
            queue = self.queue_of(root)
        except (InvariantError, OSError, ValueError) as exc:
            out.update(error=str(exc), error_from=FROM_QUEUE)
            return out
        state = ledger["projects"].get(key) or {}
        paused = state.get("paused") if isinstance(state.get("paused"), dict) else None
        current = state.get("current") if isinstance(state.get("current"), dict) else None
        landed = state.get("landed") if isinstance(state.get("landed"), dict) else None
        entries = [_entry(e, n) for n, e in enumerate(queue.entries, start=1)]
        queued = [e for e in entries if e["state"] == QUEUED]
        proposals = self._proposals(root, queue, entries, state.get("proposed") or {})
        out.update(
            entries=entries, queued=[e["id"] for e in queued], paused=paused,
            current=_current(current) if current else None, proposals=proposals,
            offered=_offers(key, queued, paused, current, proposals, landed),
            status=_status(paused, current, queued, usage),
            landed=_landed(landed) if landed else None,
        )
        return out

    def _proposals(self, root: str, queue: Queue, entries: list[dict[str, Any]],
                   proposed: Mapping[str, Any]) -> list[dict[str, Any]]:
        """The proposals the step made that are still open, queue order then backlog."""
        found: list[dict[str, Any]] = []
        for entry in entries:
            if entry["state"] != QUEUED:
                continue
            token = next((t for t in (f"entry:{entry['id']}", f"queue:{entry['id']}")
                          if t in proposed), None)
            if token is not None:
                found.append({"source": "queue", "entry": entry["id"], "ref": entry["ref"],
                              "title": None, "brief": entry["brief"],
                              "since": _since(proposed[token])})
        named = {e.ref for e in queue.entries}
        for token in sorted(proposed):
            source, _, ref = token.partition(":")
            if source != "backlog" or not ref or ref in named:
                continue
            found.append({"source": "backlog", "entry": None, "ref": ref,
                          "title": self.titles(root, ref), "brief": None,
                          "since": _since(proposed[token])})
        return found


def _since(mark: Any) -> str | None:
    return mark.get("since") if isinstance(mark, dict) else None


def _entry(entry, position: int) -> dict[str, Any]:
    return {"id": entry.id, "ref": entry.ref, "state": entry.state, "mode": entry.mode,
            "brief": entry.brief, "added_by": entry.added_by, "reason": entry.reason,
            "triage_of": entry.triage_of, "retry_of": entry.retry_of,
            "serves": entry.serves, "blind": entry.blind,
            "session": entry.session, "position": position}


def _current(current: Mapping[str, Any]) -> dict[str, Any]:
    stall = current.get("stall")
    return {"id": current.get("id"), "entry": current.get("entry"), "ref": current.get("ref"),
            "state": current.get("state"), "name": current.get("name"),
            "session": current.get("session"), "stall": stall if isinstance(stall, dict) else None,
            "reproduce": bool(current.get("reproduce"))}


def _landed(landed: Mapping[str, Any]) -> dict[str, Any]:
    """A landed window as the phone shows it: its state, the notice's why, the last note."""
    notice = landed.get("notice") if isinstance(landed.get("notice"), dict) else {}
    answers = [a for a in landed.get("answers") or [] if isinstance(a, dict)]
    notes = [a.get("note") for a in answers if a.get("note")]
    asks = [a for a in landed.get("asks") or [] if isinstance(a, dict)]
    return {"id": landed.get("id"), "entry": landed.get("entry"), "ref": landed.get("ref"),
            "name": landed.get("name"), "pid": landed.get("pid"),
            "session": landed.get("session"), "state": landed.get("state"),
            "why": notice.get("why"), "since": notice.get("at"),
            "note": notes[-1] if notes else None,
            "asks": sum(1 for a in asks if a.get("code") in ("typed", "sending")),
            "answered": len(answers)}


def _status(paused, current, queued: list[dict[str, Any]], usage: Mapping[str, Any]) -> str:
    if paused:
        return PAUSED
    if current:
        return STARTING if current.get("state") == _LAUNCHING else RUNNING
    if usage["hold"] and any(e["mode"] != ENTRY_PROPOSE for e in queued):
        return HELD
    return WAITING if queued else IDLE


def _offers(key: str, queued: list[dict[str, Any]], paused, current,
            proposals: list[dict[str, Any]],
            landed: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    offered = []
    last = len(queued) - 1
    for n, entry in enumerate(queued):
        if n > 0:
            offered.append(_offer(key, MOVE_UP, entry["id"]))
        if n < last:
            offered.append(_offer(key, MOVE_DOWN, entry["id"]))
        offered.append(_offer(key, REMOVE, entry["id"]))
    offered.append(_offer(key, RESUME if paused else PAUSE))
    if not current:
        startable = {p["entry"] for p in proposals if p["entry"]}
        startable |= {e["id"] for e in queued if e["mode"] == ENTRY_PROPOSE}
        for entry in queued:
            if entry["id"] in startable:
                offered.append(_offer(key, START, entry["id"]))
        for proposal in proposals:
            if proposal["source"] == "backlog":
                offered.append(_offer(key, START_ITEM, proposal["ref"]))
    elif isinstance(current.get("stall"), dict):
        offered.extend(_offer(key, action) for action in STALL_ACTIONS)
    if landed and landed.get("state") == _NOTIFIED:
        offered.extend(_offer(key, action) for action in LANDED_ACTIONS)
    return offered


# -- the step -------------------------------------------------------------------------


@dataclass(frozen=True)
class Derived:
    """An offered action as the step it runs, with what its confirm names."""

    action: str
    arg: str | None
    project: str
    #: The project's name; empty for the machine's greenlight.
    name: str
    argv: tuple[str, ...]
    cwd: str
    facts: dict[str, Any]


def derive(read: Mapping[str, Any], document: Mapping[str, Any], key: str,
           home: Path | str) -> Derived | str:
    """The step ``key`` runs now, from a fresh ``read``; a refusal's reason otherwise.

    Only an action the read offers is derived. ``document`` is the same fresh
    scan, for a stall's window; ``home`` is where the greenlight runs.
    """
    parsed = parse_key(key)
    if parsed is None:
        return f"{key} is not a chain action"
    project, action, arg = parsed
    if read.get("error"):
        return f"{read['error']}; nothing is offered"
    if action == GREENLIGHT:
        if project != MACHINE or not read.get("offered"):
            return ("the weekly usage is not held for an unknown reading; there is nothing "
                    "to go ahead with")
        return Derived(action, None, MACHINE, "", (CLI, "oracle", "chain", GREENLIGHT),
                       str(home), {"usage": dict(read["usage"])})
    state = read["projects"].get(project)
    if state is None:
        return "that project is not registered any more, or the oracle cannot read it now"
    if state["error"]:
        return f"{state['error']}; nothing is offered"
    if (action, arg) not in {(o["action"], o["arg"]) for o in state["offered"]}:
        return _why_not(state, action, arg)
    facts: dict[str, Any] = {"entry": None, "ref": None, "title": None}
    entries = {e["id"]: e for e in state["entries"]}
    if action in (MOVE_UP, MOVE_DOWN, REMOVE, START):
        entry = entries[arg]
        facts.update(entry=arg, ref=entry["ref"], position=entry["position"])
    if action in (MOVE_UP, MOVE_DOWN):
        queued = state["queued"]
        other = entries[queued[queued.index(arg) + (-1 if action == MOVE_UP else 1)]]
        facts.update(other=other["id"], other_ref=other["ref"])
        argv = (CLI, "queue", "move", arg, "--to", str(other["position"]),
                "--if-queued-at", str(facts["position"]))
    elif action == REMOVE:
        argv = (CLI, "queue", "remove", arg, "--if-queued-at", str(facts["position"]))
    elif action == START:
        argv = (CLI, "oracle", "chain", START, project, "--entry", arg)
    elif action == START_ITEM:
        proposal = next(p for p in state["proposals"] if p["ref"] == arg)
        facts.update(ref=arg, title=proposal["title"])
        argv = (CLI, "oracle", "chain", START, project, "--item", arg)
    else:
        argv = (CLI, "oracle", "chain", action, project)
        current = state["current"] or {}
        if action in STALL_ACTIONS:
            facts.update(entry=current.get("entry"), ref=current.get("ref"),
                         **_window(document, project, current.get("session")))
        elif action in LANDED_ACTIONS:
            landed = state["landed"] or {}
            facts.update(entry=landed.get("entry"), ref=landed.get("ref"),
                         **_landed_window(document, project, landed))
    return Derived(action, arg, project, state["name"], argv, state["root"], facts)


def _window(document: Mapping[str, Any], project: str, session: str | None) -> dict[str, Any]:
    """The stalled session's current window, as the fresh scan saw it."""
    for scanned in document.get("projects") or []:
        if (scanned.get("project") or {}).get("key") != project:
            continue
        for row in scanned.get("rows") or []:
            if row.get("kind") != ROW_SESSION or not session or row.get("session_id") != session:
                continue
            hosts = row.get("hosts") or []
            host = next((h for h in hosts if h.get("current", True)), None)
            if host is not None and host.get("alive") is not False:
                return {"window": str(host.get("host_session_id") or "")[:8] or None,
                        "pid": host.get("pid"), "last_activity": row.get("last_activity")}
    return {"window": None, "pid": None, "last_activity": None}


def _landed_window(document: Mapping[str, Any], project: str,
                   landed: Mapping[str, Any]) -> dict[str, Any]:
    """The landed window, by its launched name or pid, as the fresh scan saw it."""
    name, pid = landed.get("name"), landed.get("pid")
    for scanned in document.get("projects") or []:
        if (scanned.get("project") or {}).get("key") != project:
            continue
        for row in scanned.get("rows") or []:
            if row.get("kind") not in (ROW_SESSION, ROW_HOST):
                continue
            for host in row.get("hosts") or []:
                ours = ((name and host.get("launched_name") == name)
                        or (pid is not None and host.get("pid") == pid))
                if ours and host.get("alive") is True:
                    return {"window": str(host.get("host_session_id") or "")[:8] or None,
                            "pid": host.get("pid"), "last_activity": row.get("last_activity")}
    return {"window": None, "pid": None, "last_activity": None}


def _why_not(state: Mapping[str, Any], action: str, arg: str | None) -> str:
    """Why an action is not offered now, in the words a refusal shows."""
    queued = state["queued"]
    current = state["current"]
    if action in (MOVE_UP, MOVE_DOWN, REMOVE):
        if arg not in queued:
            return f"{arg} is not queued any more; nothing was changed"
        if action == MOVE_UP:
            return f"{arg} is already the first queued entry"
        return f"{arg} is already the last queued entry"
    if action == RESUME:
        return "the chain is not paused"
    if action == PAUSE:
        return "the chain is already paused"
    if action in LANDED_ACTIONS:
        landed = state.get("landed")
        if not landed:
            return "no landed window is open in this project; nothing was done"
        return (f"no answer waits for you on the window of {landed.get('entry')} now; "
                f"nothing was done")
    if action in (START, START_ITEM):
        if current:
            return (f"the chain already follows {current.get('entry') or current.get('ref')}; "
                    f"start this once that ends")
        return f"{arg} is not offered to start now"
    if not current:
        return "no stall is recorded: the chain follows no start now"
    return "no stall is recorded on the chain's current start"
