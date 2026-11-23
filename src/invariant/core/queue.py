"""The queue: one project's ordered record of the work that runs next.

ADR-027 D2 and D10. A record type with a location (``records.queue``,
``paths.queue``), committed like the backlog, so it holds no clock time. This
module owns the format, reading it, writing it, and the pure edits on it; which
``ref`` may go on it and how a ``brief`` is vetted are ``domains/queue``'s. The
oracle reads it and never writes it (D9).

The file is one JSON document::

    {"version": 1, "next": 3,
     "entries": [{"id": "Q1", "ref": "IDEA_057", "added_by": "user",
                  "state": "queued", "brief": "..."}]}

An entry's position in ``entries`` is its place in the queue. ``next`` is the
number the next id is minted from, so a removed id is never issued again. A key
this build does not know, on the document or on an entry, is written back as
it was read (``core/store.PreservesUnknownKeys``, BUG_061).

Increment 5 (ADR-027 rulings 18 to 20) adds what the backlog mirror and the
reproduce-first start need: an entry ``added_by`` :data:`BACKLOG`, an entry's
``serves`` (the work item a free label serves) and ``blind`` (the reproduce
runs that recorded nothing), the document's ``mirrored`` list (the items the
mirror has put on the queue in their current open spell), the backlog's rank
(:func:`rank_key`, D12's, which the engine now needs too) and the words a
reproduction's verdict is routed by.

ADR-032 increment 6 (D11) adds the plan page's text and where it lives: the
record's digest (:func:`queue_digest`), the page (:func:`render_page`) under
the managed marker the rules page carries, naming that digest, and how a page
on disk stands against the record (:func:`page_standing`), which
``docs.plan_current`` asks. Writing it is ``domains/queue``'s.
"""

from __future__ import annotations

import json
import re
import string
from collections.abc import Callable, Container, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import git, needs
from . import guidelines as managed
from . import plan as plan_reader
from .errors import StateError, UsageError
from .store import UNKNOWN_KEYS, JsonStore, PreservesUnknownKeys

VERSION = 1
#: Where the queue lives when ``paths.queue`` is not set (D10).
DEFAULT_PATH = "docs/queue.json"
#: The lock a read-modify-write holds, in the project's state root.
LOCK = "queue"

QUEUED = "queued"
STARTED = "started"
DONE = "done"
FAILED = "failed"
#: Every state an entry may be in (D2).
STATES = (QUEUED, STARTED, DONE, FAILED)
#: The states in which an entry still holds its piece of work.
OPEN_STATES = (QUEUED, STARTED)

#: An entry's own mode (ADR-027 ruling 9): ``propose`` is never started
#: automatically, whatever ``oracle.chain.queue`` says; ``oracle chain start``
#: starts it. Unset, the project's setting decides.
PROPOSE = "propose"
MODES = (PROPOSE,)

#: Who added an entry the backlog mirror wrote (ADR-027 ruling 18).
BACKLOG = "backlog"

#: What a reproduce-first session's end says of its bug (ruling 20), read from
#: the item by the oracle and routed by ``domains/queue.after_reproduce``:
#: proven red (the fix runs next), closed by the verdict, a live-environment
#: leg owed (offered to the user), or nothing recorded (run once more).
REPRO_RED = "red"
REPRO_CLOSED = "closed"
REPRO_OWED = "owed"
REPRO_BLIND = "blind"
REPRO_OUTCOMES = (REPRO_RED, REPRO_CLOSED, REPRO_OWED, REPRO_BLIND)
#: The reproduce runs that may record nothing before the entry is offered
#: instead of run again (ruling 20: a second HARNESS-BLIND is a proposal).
BLIND_RUNS = 2

#: Priorities in rank order; any other comes after them (D12).
PRIORITIES = ("high", "medium", "low")

#: The optional keys an entry leaves out of its document while they are unset.
#: ``mode``, ``triage_of`` and ``retry_of`` came with increment 2; an older
#: build keeps them as unknown keys (BUG_061), so it writes them back intact.
_OPTIONAL = ("brief", "session", "reason", "mode", "triage_of", "retry_of", "serves",
             "blind")
_ID = re.compile(r"^Q(\d+)$")


@dataclass
class Entry(PreservesUnknownKeys):
    """One row of the queue."""

    id: str
    ref: str
    added_by: str = "user"
    state: str = QUEUED
    #: The first prompt for the session that takes it up.
    brief: str | None = None
    #: The session a ``started`` entry started.
    session: str | None = None
    #: Why a ``failed`` entry failed.
    reason: str | None = None
    #: ``propose``, or ``None`` for the project's setting.
    mode: str | None = None
    #: On a triage entry: the failed entry it triages.
    triage_of: str | None = None
    #: On a retry: the entry whose work it tries again.
    retry_of: str | None = None
    #: The work item this entry serves when its ref is not that item's id,
    #: e.g. a label ``ADR-029 inc 1`` serving ``IDEA_060`` (ruling 18).
    serves: str | None = None
    #: The reproduce-first runs of this entry that recorded nothing (ruling 20).
    blind: int | None = None
    _extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        body = super().to_dict()
        for key in _OPTIONAL:
            if body.get(key) is None:
                body.pop(key, None)
        return body


@dataclass
class Queue:
    """The whole record: its entries in order, and the next id's number."""

    entries: list[Entry] = field(default_factory=list)
    next: int = 1
    #: The items the backlog mirror has put on the queue in their current open
    #: spell, so an entry the user removed is not mirrored again (ruling 18).
    mirrored: list[str] = field(default_factory=list)
    _extra: dict[str, Any] = field(default_factory=dict)

    # -- questions -------------------------------------------------------

    def head(self) -> Entry | None:
        """The first ``queued`` entry: what runs next."""
        return next((e for e in self.entries if e.state == QUEUED), None)

    def holding(self, ref: str) -> Entry | None:
        """The ``queued`` or ``started`` entry holding ``ref``, if any."""
        return next(
            (e for e in self.entries if e.ref == ref and e.state in OPEN_STATES), None
        )

    def serving(self, item: str) -> Entry | None:
        """The ``queued`` or ``started`` entry naming ``item`` or serving it, if any."""
        return next((e for e in self.entries if e.state in OPEN_STATES
                     and item in (e.ref, e.serves)), None)

    def find(self, entry_id: str) -> Entry:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
        known = ", ".join(e.id for e in self.entries) or "none"
        raise UsageError(f"the queue has no entry {entry_id!r}; its entries: {known}")

    def expect(self, entry_id: str, position: int) -> Entry:
        """Entry ``entry_id``, refused unless it is still ``queued`` at ``position``.

        The guard a confirmed edit from the phone carries (ADR-027 increment 3):
        the queue may have changed between the confirm and the edit, and an
        edit computed from a position no longer true would move or remove the
        wrong place. Positions count from 1 over the whole list.
        """
        entry = self.find(entry_id)
        where = self.entries.index(entry) + 1
        if entry.state != QUEUED or where != position:
            raise UsageError(
                f"{entry_id} is no longer queued at position {position}: it is {entry.state} "
                f"at position {where}. The queue changed since this was confirmed"
            )
        return entry

    # -- edits -----------------------------------------------------------

    def add(
        self,
        ref: str,
        *,
        added_by: str,
        brief: str | None = None,
        at: int | None = None,
        mode: str | None = None,
        state: str = QUEUED,
        triage_of: str | None = None,
        retry_of: str | None = None,
        serves: str | None = None,
    ) -> Entry:
        """Mint an entry for ``ref`` and place it, at the end or at ``at``."""
        if mode is not None and mode not in MODES:
            raise UsageError(f"an entry's mode is {', '.join(MODES)} or unset, not {mode!r}")
        if state not in STATES:
            raise UsageError(f"an entry's state is one of {', '.join(STATES)}, not {state!r}")
        index = len(self.entries) if at is None else self._index(at, len(self.entries) + 1)
        entry = Entry(id=f"Q{self.next}", ref=ref, added_by=added_by, brief=brief, mode=mode,
                      state=state, triage_of=triage_of, retry_of=retry_of, serves=serves)
        self.next += 1
        self.entries.insert(index, entry)
        return entry

    def remove(self, entry_id: str) -> Entry:
        entry = self.find(entry_id)
        self.entries.remove(entry)
        return entry

    def mark(
        self,
        entry_id: str,
        state: str,
        *,
        session: str | None = None,
        reason: str | None = None,
    ) -> Entry:
        """Move entry ``entry_id`` to ``state``; ``session`` and ``reason`` set when given.

        The chain's writes (ADR-027 D4, increment 2): ``started`` with the
        session once one is bound, ``done``, or ``failed`` with the reason.
        A reason is dropped when the entry leaves ``failed``.
        """
        if state not in STATES:
            raise UsageError(f"an entry's state is one of {', '.join(STATES)}, not {state!r}")
        entry = self.find(entry_id)
        entry.state = state
        if session is not None:
            entry.session = session
        if reason is not None:
            entry.reason = reason
        elif state != FAILED:
            entry.reason = None
        return entry

    def move(self, entry_id: str, to: int) -> Entry:
        entry = self.find(entry_id)
        index = self._index(to, len(self.entries))
        self.entries.remove(entry)
        self.entries.insert(index, entry)
        return entry

    @staticmethod
    def _index(position: int, last: int) -> int:
        """A position counted from 1, as a list index; refused outside 1..last."""
        if not 1 <= position <= last:
            raise UsageError(
                f"position {position} is outside the queue: use 1 to {last}"
            )
        return position - 1

    # -- the document ----------------------------------------------------

    def to_dict(self) -> dict:
        body = {
            **self._extra,
            "version": VERSION,
            "next": self.next,
            "entries": [entry.to_dict() for entry in self.entries],
        }
        if self.mirrored:
            body["mirrored"] = sorted(set(self.mirrored), key=natural)
        return body


def natural(text: str) -> list[tuple[int, Any]]:
    """``text`` split into numbers and the rest, so ``IDEA_9`` sorts before ``IDEA_10``."""
    return [(0, int(part)) if part.isdigit() else (1, part)
            for part in re.split(r"(\d+)", text)]


def rank_key(item_id: str, tier: Any, priority: Any) -> tuple:
    """The backlog's rank (ADR-027 D12): tier ascending (a missing or non-numeric
    tier last), then priority ``high``, ``medium``, ``low`` (any other last),
    then the id in natural order. What the oracle's fallback ranks by and where
    the mirror inserts a newly filed item (ruling 18)."""
    if isinstance(tier, bool):
        tier_key: tuple[int, float] = (1, 0.0)
    else:
        try:
            tier_key = (0, float(tier))
        except (TypeError, ValueError):
            tier_key = (1, 0.0)
    text = str(priority).strip().lower() if priority is not None else ""
    priority_key = PRIORITIES.index(text) if text in PRIORITIES else len(PRIORITIES)
    return (tier_key, priority_key, natural(item_id))


def parse(raw: Any, where: str = "the queue") -> Queue:
    """A document as a :class:`Queue`, or a refusal naming what is wrong."""
    if not isinstance(raw, dict):
        raise StateError(f"{where} is not a queue document: a JSON object is expected")
    version = raw.get("version", VERSION)
    if version != VERSION:
        raise StateError(
            f"{where} is version {version!r}; this build reads version {VERSION}"
        )
    entries = raw.get("entries", [])
    if not isinstance(entries, list):
        raise StateError(f"{where}: entries must be a list")
    counter = raw.get("next", 1)
    if isinstance(counter, bool) or not isinstance(counter, int) or counter < 1:
        raise StateError(f"{where}: next must be a positive whole number, got {counter!r}")
    mirrored = raw.get("mirrored", [])
    if not isinstance(mirrored, list) or not all(isinstance(i, str) for i in mirrored):
        raise StateError(f"{where}: mirrored must be a list of work item ids, got {mirrored!r}")
    parsed = [_entry(row, where) for row in entries]
    minted = [int(m.group(1)) for e in parsed if (m := _ID.match(e.id))]
    extra = {k: v for k, v in raw.items()
             if k not in ("version", "next", "entries", "mirrored")}
    return Queue(entries=parsed, next=max([counter, *(n + 1 for n in minted)]),
                 mirrored=list(mirrored), _extra=extra)


def _entry(row: Any, where: str) -> Entry:
    if not isinstance(row, dict):
        raise StateError(f"{where}: an entry must be an object, got {row!r}")
    for key in ("id", "ref"):
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise StateError(f"{where}: an entry's {key} must be text, got {value!r}")
    mode = row.get("mode")
    if mode is not None and mode not in MODES:
        raise StateError(
            f"{where}: entry {row['id']} has mode {mode!r}; an entry's mode is "
            f"{', '.join(MODES)} or unset"
        )
    state = row.get("state", QUEUED)
    if state not in STATES:
        raise StateError(
            f"{where}: entry {row['id']} has state {state!r}; the states are "
            f"{', '.join(STATES)}"
        )
    held = {k: v for k, v in row.items() if k != UNKNOWN_KEYS}
    return Entry.from_dict(held)


def read_queue(path: Path) -> Queue:
    """The queue at ``path``; an empty one when no file is there yet."""
    if not Path(path).is_file():
        return Queue()
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"the queue at {path} is not valid JSON ({exc})") from exc
    except OSError as exc:
        raise StateError(f"cannot read the queue at {path}: {exc}") from exc
    return parse(raw, f"the queue at {path}")


def write_queue(path: Path, queue: Queue) -> None:
    """Replace the file at ``path`` atomically, with sorted keys."""
    path = Path(path)
    JsonStore(path.parent).write(path.name, queue.to_dict())


def queue_path(manifest) -> Path:
    """Where ``manifest``'s project keeps its queue (D10).

    A store record's path is already joined onto the store root, which is the
    store's main checkout. A repository record is read in the repository's
    main checkout, at the project's place in it, whatever worktree the caller
    stands in: a project has one queue, the one the oracle reads.
    """
    value = Path(manifest.get("paths.queue") or DEFAULT_PATH)
    if value.is_absolute():
        return value
    return _main_root(Path(manifest.root)) / value


def main_root(root: Path) -> Path:
    """The project's place in its repository's main checkout (D10); ``root``
    itself outside git or when the main checkout cannot be told."""
    return _main_root(Path(root))


def _main_root(root: Path) -> Path:
    main = git.main_checkout(root)
    top = git.toplevel(root)
    if main is None or top is None:
        return root
    try:
        return main / root.resolve().relative_to(top.resolve())
    except ValueError:
        return root


@contextmanager
def editing(
    path: Path, lock_store: JsonStore, after: Callable[[Queue], None] | None = None
) -> Iterator[Queue]:
    """Yield the queue for an edit under its lock; written back on a clean exit.

    The lock is ``lock_store``'s ``locks/queue.lock``, the project's state
    root, so it is never written beside the committed file. An exception
    inside leaves the file as it was. ``after`` is called with the queue once
    it is written, still under the lock: the plan page is rendered there
    (ADR-032 increment 6), so no other edit falls between the two.
    """
    with lock_store.lock(LOCK):
        queue = read_queue(path)
        yield queue
        write_queue(path, queue)
        if after is not None:
            after(queue)


# -- the plan page (ADR-032 D11, increment 6) --------------------------------

#: The plan page's file name, in the docs project's source directory.
PLAN_PAGE = "plan.rst"

#: How a plan page on disk stands against the queue record.
ABSENT = "absent"
UNMANAGED = "unmanaged"
EDITED = "edited"
STALE = "stale"
CURRENT = "current"

#: An entry's kind on the page.
TRIAGE = "triage"
RETRY = "retry"
ITEM = "item"
LABEL = "label"

_PAGE_DIGEST = re.compile(r"^\.\. invariant-queue: ([0-9a-f]{12})$", re.MULTILINE)
_PUNCTUATION = re.compile("([" + re.escape(string.punctuation) + "])")


class PageRefused(UsageError):
    """The plan page cannot be written: nothing was."""


@dataclass(frozen=True)
class SessionPlan:
    """One open session's deliverables, as the page renders them: a value."""

    id: str
    name: str
    plan: tuple[Mapping[str, Any], ...] = ()


def queue_digest(queue: Queue) -> str:
    """Twelve hex of the record as canonical JSON: keys sorted, no whitespace.

    A file written with other spacing is the same record; every field the
    record holds, ``next``, ``mirrored`` and keys this build does not know
    included, is part of it.
    """
    canonical = json.dumps(
        queue.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return managed.digest_of(canonical.encode("utf-8"))


def plan_page(manifest) -> tuple[Path | None, str | None]:
    """Where ``manifest``'s project keeps its plan page, or ``None`` and why.

    ``plan.rst`` in the docs project's source directory, read against the
    checkout :func:`queue_path` reads (the repository's main checkout), or the
    store's docs project where the docs and the queue are both kept there.
    The page lives beside the queue so the two are committed together; with
    the docs and the queue in two homes, nothing would commit the page, so
    there is none. Read without a subprocess where the docs are a store's:
    the docs lock's classification asks this on every tool call.
    """
    fmt = manifest.get("docs.format")
    if fmt != "sphinx":
        return None, (
            f"docs.format is {fmt!r}, not sphinx: the plan page is a page of a Sphinx "
            f"docs project"
        )
    docs_home = manifest.get("records.docs") or "repo"
    queue_home = manifest.get("records.queue") or "repo"
    if docs_home != queue_home:
        return None, (
            f"records.docs is {docs_home} and records.queue is {queue_home}: the plan "
            f"page is written beside the queue, so both must name the same home"
        )
    if needs.docs_store_root(manifest) is not None:
        srcdir = needs.srcdir_of(manifest)
    else:
        srcdir = needs.srcdir_of(manifest, main_root(Path(manifest.root)))
    if srcdir is None or not srcdir.is_dir():
        return None, "no docs project source directory resolves, so there is no plan page"
    return srcdir / PLAN_PAGE, None


def render_page(
    queue: Queue,
    *,
    source: str,
    needs: Container[str],
    is_item: Callable[[str], bool],
    sessions: Sequence[SessionPlan] = (),
    on: str | None = None,
) -> str:
    """The plan page for ``queue``, rST, managed marker first (ADR-032 D11).

    The queue in order, each entry's linked item a ``:need:`` reference when
    it is one of ``needs`` and plain text otherwise, so the page builds
    strictly; ``is_item`` says whether a ref is a work item id (the domain's
    test, handed in). Then each of ``sessions`` with a deliverable, as of
    ``on`` (a date, never a clock time); with ``on`` ``None`` the part says it
    was not read. Every free text is escaped, so nothing queued can open
    inline markup.
    """
    digest = queue_digest(queue)
    lines = [
        f".. invariant-queue: {digest}",
        "",
        "Plan",
        "====",
        "",
        (
            f"Generated by ``invariant queue render`` from ``{source}`` (queue "
            f"{digest}). Do not edit this file: the queue changes through "
            f"``invariant queue``, which renders this page again. A hand-written "
            f"release plan or roadmap stays beside it."
        ),
        "",
        "Queue",
        "-----",
        "",
    ]
    lines += _queue_table(queue, needs, is_item)
    lines += ["Session plans", "-------------", ""]
    lines += _session_part(sessions, on)
    return managed.managed_text("\n".join(lines).rstrip("\n") + "\n", "sphinx")


def _queue_table(queue: Queue, needs: Container[str], is_item: Callable[[str], bool]) -> list[str]:
    if not queue.entries:
        return ["The queue is empty.", ""]
    lines = [
        "The queue in order: what runs next is the first queued entry.",
        "",
        ".. list-table::",
        "   :header-rows: 1",
        "",
    ]
    rows = [["#", "Entry", "Ref", "Kind", "Item", "Status"]]
    for position, entry in enumerate(queue.entries, start=1):
        linked = entry.ref if is_item(entry.ref) else entry.serves
        rows.append([
            str(position),
            _plain(entry.id),
            _plain(entry.ref),
            _kind(entry, is_item),
            "none" if not linked else (
                f":need:`{linked}`" if linked in needs else _plain(linked)
            ),
            entry.state + (", proposed" if entry.mode == PROPOSE else ""),
        ])
    for row in rows:
        lines.append(f"   * - {row[0]}")
        lines += [f"     - {cell}" for cell in row[1:]]
    return [*lines, ""]


def _kind(entry: Entry, is_item: Callable[[str], bool]) -> str:
    if entry.triage_of:
        return TRIAGE
    if entry.retry_of:
        return RETRY
    return ITEM if is_item(entry.ref) else LABEL


def _session_part(sessions: Sequence[SessionPlan], on: str | None) -> list[str]:
    if on is None:
        return [
            (
                "Rendered without the session plans: they are engine state, read when "
                "``invariant queue render`` runs."
            ),
        ]
    lines = [
        (
            f"The open sessions' deliverables as of {on}, read from the engine's state "
            f"where the page was rendered. This part is a dated snapshot; "
            f"``docs.plan_current`` checks the queue part only."
        ),
        "",
    ]
    shown = [s for s in sorted(sessions, key=lambda s: (s.name, s.id))
             if plan_reader.entries(list(s.plan))]
    if not shown:
        return [*lines, "No open session records a deliverable."]
    for session in shown:
        title = f"{_plain(session.name or 'unnamed')} ({_plain(session.id)})"
        lines += [title, "~" * len(title), "", "::", ""]
        for line in plan_reader.render_lines(list(session.plan)):
            lines += [f"   {part}" for part in line.splitlines() or [""]]
        lines.append("")
    return lines


def _plain(text: str) -> str:
    """``text`` as inert rST: whitespace collapsed, each punctuation mark escaped."""
    return _PUNCTUATION.sub(r"\\\1", " ".join(str(text).split()))


def page_digest(text: str) -> str | None:
    """The queue digest a plan page names, or ``None`` where it names none."""
    found = _PAGE_DIGEST.search(text)
    return None if found is None else found.group(1)


def page_standing(text: str | None, queue: Queue) -> str:
    """How a plan page's text (``None``: no file) stands against ``queue``.

    Only the queue part is judged: the session plans are a dated snapshot of
    engine state and never make a page stale (ADR-032 D11).
    """
    if text is None:
        return ABSENT
    marker, body = managed.split_managed(text, "sphinx")
    if marker is None:
        return UNMANAGED
    if marker != managed.digest_of(body.encode("utf-8")):
        return EDITED
    return CURRENT if page_digest(body) == queue_digest(queue) else STALE
