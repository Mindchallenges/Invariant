"""The attention sidecar: what one host session is waiting on, and since when.

One document per host session, ``<state_root>/attention/<host-id>.json``::

    {"host_session_id": "<id>", "pid": 4242 | null,
     "waiting": {"kind": "permission|question|idle", "message", "since"} | null,
     "blocked": {"rules": [...], "surface": "stop|cli", "since"} | null,
     "turn": "running|ended" | null, "turn_since": "<iso>" | null,
     "ended": false,
     "degraded": {"event", "error", "since"} | null}

It answers the questions the session record cannot: is this host sitting on a
permission prompt, did its last turn end on a refusal, is it still alive. No
hook could see a permission prompt before the ``Notification`` event was
wired, and none of the rest belongs on the session record, which is per
INVARIANT session and written under the registry's discipline.

**One writer, no lock.** A file is written only by its own host's hooks, which
the host runs one at a time, so atomic replacement is enough — the same
argument as ``sessions/<sid>.json`` (state_model.md, "Concurrency"). The one
other writer is a CLI gate refusal, which writes the record of the host bound
to the refused session; that host is the caller, so the writes still come from
one process tree in sequence.

**Clock times, scoped.** ``since`` and ``turn_since`` are wall-clock times, the
first this library stores outside the stats records' bare dates. They are
machine-local and derived — under the state root, never committed, never
rendered into a document — and they exist because "how long has this been
waiting" is the oracle's whole question (ADR-007 D9).

**Keys this build does not know are kept** (the BUG_061 contract), and a record
that cannot be read is replaced by the next write rather than wedging its host:
it is derived state, and the next event re-derives it.

**Nothing here may change a verdict.** The callers in ``cli/`` wrap every call
in a guard; this module raises freely, and they swallow (ADR-007 D10).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..core.errors import StateError
from ..core.store import JsonStore, PreservesUnknownKeys

ATTENTION_DIR = "attention"

#: What a host can be waiting on. ``permission`` and ``question`` are the two
#: the oracle alerts on; ``idle`` is "your turn", shown but not alerted.
WAITING_KINDS = ("permission", "question", "idle")

#: Where a refusal was recorded: the Stop hook's closeout, or a CLI command.
SURFACE_STOP = "stop"
SURFACE_CLI = "cli"

TURN_RUNNING = "running"
TURN_ENDED = "ended"

#: A host id becomes a file name, so only a plain name is accepted: no
#: separator, no leading dot, bounded length.
_HOST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

#: Answers the pid to record, given the one already recorded (or ``None``).
PidSource = Callable[[int | None], int | None]


def utc_now() -> str:
    """The wall clock, UTC, to the second, in ISO 8601."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class AttentionRecord(PreservesUnknownKeys):
    """One host session's attention state, as this build reads it."""

    host_session_id: str = ""
    pid: int | None = None
    waiting: dict[str, Any] | None = None
    blocked: dict[str, Any] | None = None
    turn: str | None = None
    turn_since: str | None = None
    ended: bool = False
    degraded: dict[str, Any] | None = None
    _extra: dict[str, Any] = field(default_factory=dict)


class AttentionStore:
    """Reads and writes the sidecar through a project's :class:`JsonStore`.

    Built on the store the caller already holds, so ``doctor``'s probe — whose
    store is the inert one — discards these writes with every other.
    """

    def __init__(self, store: JsonStore, clock: Callable[[], str] = utc_now) -> None:
        self.store = store
        self.clock = clock

    @staticmethod
    def relpath(host_id: str) -> str:
        if not isinstance(host_id, str) or not _HOST_ID.match(host_id):
            raise ValueError(f"{host_id!r} is not a host session id")
        return f"{ATTENTION_DIR}/{host_id}.json"

    def read(self, host_id: str) -> AttentionRecord | None:
        """The stored record, ``None`` when there is none; a malformed one raises."""
        raw = self.store.read(self.relpath(host_id))
        if raw is None:
            return None
        return AttentionRecord.from_dict(raw)

    # -- events --------------------------------------------------------

    def wait(
        self,
        host_id: str,
        kind: str,
        message: str | None,
        pid_source: PidSource | None = None,
    ) -> None:
        """The host is waiting on the person: a prompt, a question, or idle."""
        if kind not in WAITING_KINDS:
            raise ValueError(f"{kind!r} is not one of {', '.join(WAITING_KINDS)}")
        now = self.clock()

        def mutate(record: AttentionRecord) -> None:
            record.waiting = {"kind": kind, "message": message, "since": now}

        self._update(host_id, mutate, pid_source)

    def answered(self, host_id: str, pid_source: PidSource | None = None) -> bool:
        """A tool finished, so whatever it waited on was answered.

        Fires after every tool call, so it reads first and writes only when
        something is waiting. Returns whether it wrote.
        """
        def mutate(record: AttentionRecord) -> None:
            record.waiting = None

        return self._update(
            host_id, mutate, pid_source, only_if=lambda r: r.waiting is not None
        )

    def prompt(self, host_id: str, pid_source: PidSource | None = None) -> None:
        """The person spoke: a turn starts, and every earlier ask is answered."""
        now = self.clock()

        def mutate(record: AttentionRecord) -> None:
            record.turn = TURN_RUNNING
            record.turn_since = now
            record.waiting = None
            record.blocked = None
            record.degraded = None
            record.ended = False

        self._update(host_id, mutate, pid_source)

    def turn_ended(
        self,
        host_id: str,
        rules: Iterable[str],
        pid_source: PidSource | None = None,
    ) -> None:
        """The turn ended, refused by ``rules`` at the closeout, or cleanly.

        A clean stop clears only a block the Stop recorded. A CLI refusal made
        during the turn is the thing the oracle reports once the turn is over,
        so the Stop that ends the turn must not be what erases it; the next
        prompt does.
        """
        rules = list(rules)
        now = self.clock()

        def mutate(record: AttentionRecord) -> None:
            record.turn = TURN_ENDED
            record.turn_since = now
            record.waiting = None
            if rules:
                record.blocked = {"rules": rules, "surface": SURFACE_STOP, "since": now}
            elif _surface(record.blocked) == SURFACE_STOP:
                record.blocked = None

        self._update(host_id, mutate, pid_source)

    def host_ended(self, host_id: str) -> None:
        """The host session is over. Nothing it was waiting on is owed."""
        now = self.clock()

        def mutate(record: AttentionRecord) -> None:
            record.ended = True
            record.turn = TURN_ENDED
            record.turn_since = now
            record.waiting = None
            record.blocked = None
            record.degraded = None
            record.pid = None

        self._update(host_id, mutate, None)

    def refused(self, host_id: str, rules: Iterable[str]) -> None:
        """A CLI command's gates refused the session this host is bound to."""
        rules = list(rules)
        now = self.clock()

        def mutate(record: AttentionRecord) -> None:
            record.blocked = {"rules": rules, "surface": SURFACE_CLI, "since": now}

        self._update(host_id, mutate, None)

    def refusal_lifted(self, host_id: str) -> bool:
        """A CLI transition went through, so a CLI refusal no longer stands."""
        def mutate(record: AttentionRecord) -> None:
            record.blocked = None

        return self._update(
            host_id, mutate, None, only_if=lambda r: _surface(r.blocked) == SURFACE_CLI
        )

    def degraded(
        self,
        host_id: str,
        event: str,
        error: str,
        pid_source: PidSource | None = None,
    ) -> None:
        """The hook for ``event`` failed and answered with enforcement off."""
        now = self.clock()

        def mutate(record: AttentionRecord) -> None:
            record.degraded = {"event": event, "error": error, "since": now}

        self._update(host_id, mutate, pid_source)

    # -- the one write path ----------------------------------------------

    def _update(
        self,
        host_id: str,
        mutate: Callable[[AttentionRecord], None],
        pid_source: PidSource | None,
        only_if: Callable[[AttentionRecord], bool] | None = None,
    ) -> bool:
        rel = self.relpath(host_id)
        record = self._load_for_write(host_id)
        if only_if is not None and (record is None or not only_if(record)):
            return False
        if record is None:
            record = AttentionRecord(host_session_id=host_id)
        mutate(record)
        if pid_source is not None:
            pid = pid_source(record.pid)
            if pid is not None:
                record.pid = pid
        record.host_session_id = host_id
        self.store.write(rel, record.to_dict())
        return True

    def _load_for_write(self, host_id: str) -> AttentionRecord | None:
        """The stored record, or ``None`` when absent or unreadable.

        Unreadable is treated as absent here, and only here: the record is
        derived from events, so the next write starting over loses less than a
        host whose every hook trips on one bad file.
        """
        try:
            return self.read(host_id)
        except StateError:
            return None


def _surface(blocked: Any) -> str | None:
    return blocked.get("surface") if isinstance(blocked, dict) else None
