"""The queue's operations: what may go on it, and the edits (ADR-027 D2, D10).

Not an FSM. ``core/queue`` owns the record; this domain decides which ``ref``
an entry may name, vets the ``ref`` and the ``brief`` the way a handoff note
is vetted (``domains/session.vet_line``), and makes each edit under the
queue's lock. The CLI writes through it; the oracle only reads the record.

Increment 2 adds the chain's writes, which the CLI makes on the watcher's
behalf (D9): :meth:`QueueDomain.begin` (an entry ``started``, or a backlog
start minted as one), :meth:`QueueDomain.mark` and :meth:`QueueDomain.fail`,
which marks a failed start and queues ruling 9's triage, and its one retry,
at the head in the same locked edit.

Increment 3 adds the guard the phone's confirmed edits carry: ``move`` and
``remove`` with ``expect_at`` refuse, writing nothing, unless the entry is
still queued at the position the confirm saw, checked under the same lock.

Increment 5 (ADR-027 rulings 18 to 20) adds the backlog mirror,
:meth:`QueueDomain.mirror`: one idempotent reconcile that puts every open
item in a mirrored state on the queue once, by backlog rank, and takes a
queued mirrored entry off when its item closes or another live entry serves
it. It reads the backlog of the main checkout (or the store), so an item filed
in a worktree reaches the queue when it lands. An entry may name the item it
serves (``serves``, :meth:`QueueDomain.link`). :meth:`QueueDomain.after_reproduce`
routes a reproduce-first session's verdict, which the oracle read from the
item, in one locked edit.

ADR-032 increment 6 (D11) adds the plan page's write, :meth:`QueueDomain.render`:
``core/queue`` renders the page beside the queue and this domain writes it,
under the queue's lock, with the need ids the docs project declares and the
live sessions' plans it reads. Every edit above renders it again in the same
lock, after the record is written; a page that cannot be written never fails
the edit, and why is kept in :attr:`QueueDomain.notes`.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ..core import needs as needs_reader
from ..core import queue as record
from ..core.docformat import rst_directives
from ..core.errors import InvariantError, ManifestError, UsageError
from ..core.guidelines import managed_state
from ..core.privacy import PrivacyScanner
from ..core.queue import (
    BACKLOG,
    BLIND_RUNS,
    DONE,
    FAILED,
    OPEN_STATES,
    PROPOSE,
    QUEUED,
    REPRO_BLIND,
    REPRO_CLOSED,
    REPRO_OUTCOMES,
    REPRO_OWED,
    REPRO_RED,
    STARTED,
    Entry,
    Queue,
    rank_key,
)
from ..core.store import InertStore, JsonStore
from .session import HANDOFF_NOTE_MAX, SessionDomain, vet_line
from .workitem import ITEM_TYPES, WorkItemDomain

#: A ``ref`` is a name, not a prompt.
REF_MAX = 200
#: A ``brief`` is a first prompt, bounded as a handoff note is.
BRIEF_MAX = HANDOFF_NOTE_MAX
#: Who added an entry the chain wrote: a backlog start, a triage, a retry.
ORACLE = "oracle"
#: A triage entry's ``ref``: the failed entry it looks at.
TRIAGE_REF = "triage {entry}"
#: The state role an item machine marks the states the mirror puts on the
#: queue with (ADR-027 ruling 18), when ``oracle.chain.mirror_states`` is null.
MIRROR_ROLE = "mirror"
#: A source directory's folders that are never pages of it.
_NOT_PAGES = frozenset({"_build", "__pycache__"})


@dataclass(frozen=True)
class PageRendered:
    """What one render of the plan page did: a value (ADR-032 increment 6).

    ``status`` is ``new``, ``current`` (not written), ``stale`` or ``edited``
    (written over the hand edit); ``none`` with no page home, ``reason``
    saying why. ``relpath`` is the page spelled from the checkout it is in.
    """

    path: Path | None
    status: str
    written: bool
    relpath: str | None = None
    reason: str | None = None


class QueueDomain:
    """One project's queue, read and edited through its lock."""

    def __init__(self, manifest, store: JsonStore,
                 today: Callable[[], str] | None = None) -> None:
        self.manifest = manifest
        #: The state root's store: its ``locks/queue.lock`` guards each edit.
        self.store = store
        #: The date the plan page's session part is dated with.
        self.today = today or (lambda: datetime.now(UTC).astimezone().date().isoformat())
        #: Why an edit's render of the plan page wrote nothing, one line each.
        self.notes: list[str] = []

    @property
    def path(self) -> Path:
        return record.queue_path(self.manifest)

    @property
    def items(self) -> WorkItemDomain:
        return WorkItemDomain(self.manifest)

    def list(self) -> Queue:
        return record.read_queue(self.path)

    def add(
        self,
        ref: str,
        brief: str | None = None,
        added_by: str = "user",
        at: int | None = None,
        mode: str | None = None,
        serves: str | None = None,
    ) -> Entry:
        """Put ``ref`` on the queue, at the end or at position ``at``.

        Everything is checked before the file is touched: the ``ref`` and the
        ``brief`` vetted, a work-item ``ref`` found open, ``serves`` an open
        work item, and no open entry already holding the same ``ref``.
        """
        ref = vet_line(
            self.manifest, ref, noun="queue ref",
            one_line="a queue ref is one line: a work item id or a short label.",
            refused="it is not queued", again="queue it again", bound=REF_MAX,
        )
        if brief is not None:
            brief = self._vet_brief(brief)
        if mode is not None and mode not in record.MODES:
            raise UsageError(
                f"an entry's mode is {', '.join(record.MODES)} or unset, not {mode!r}"
            )
        self._check_item(ref)
        if serves is not None:
            self._check_served(serves)
        with record.editing(self.path, self.store, after=self._rerender) as queue:
            held = queue.holding(ref)
            if held is not None:
                raise UsageError(
                    f"{ref} is already on the queue as {held.id} ({held.state}); "
                    f"move it with 'invariant queue move {held.id} --to N'"
                )
            return queue.add(ref, added_by=added_by, brief=brief, at=at, mode=mode,
                             serves=serves)

    def link(self, entry_id: str, item: str | None) -> Entry:
        """Say entry ``entry_id`` serves work item ``item``, or nothing with ``None``.

        The link the mirror reads (ADR-027 ruling 18): an item a live entry
        serves is not mirrored, so a label such as ``ADR-029 inc 1`` serving
        ``IDEA_060`` keeps that idea off the queue while the label is queued
        or started. ``item`` must be an open work item.
        """
        if item is not None:
            self._check_served(item)
        with record.editing(self.path, self.store, after=self._rerender) as queue:
            entry = queue.find(entry_id)
            entry.serves = item
            return entry

    # -- the chain's writes (increment 2) -----------------------------------

    def begin(self, entry_id: str | None, ref: str) -> str:
        """A chained start is about to launch: its entry ``started``; its id.

        ``entry_id`` ``None`` is a backlog start, recorded as an entry of its
        own (``added_by`` ``oracle``), so every chained start is on the queue
        (D4) and the backlog fallback skips its item from then on.
        """
        with record.editing(self.path, self.store, after=self._rerender) as queue:
            if entry_id is None:
                return queue.add(ref, added_by=ORACLE, state=STARTED).id
            queue.mark(entry_id, STARTED)
            return entry_id

    def mark(self, entry_id: str, state: str, session: str | None = None,
             reason: str | None = None) -> Entry:
        """Move an entry to ``state``: the chain's ``started`` (with its session),
        ``done`` or ``failed``."""
        with record.editing(self.path, self.store, after=self._rerender) as queue:
            return queue.mark(entry_id, state, session=session, reason=reason)

    def fail(self, entry_id: str, reason: str, triage: str | None,
             retry: bool) -> dict[str, str | None]:
        """Mark ``entry_id`` failed and queue what ruling 9 owes, in one edit.

        With ``retry``, the failed work goes back on the queue once, at the
        head, as an entry naming the one it retries (``retry_of``, the first
        entry of that work). With a ``triage`` template, a triage entry goes
        in front of it, its brief the template filled with ``{entry}``,
        ``{ref}``, ``{reason}`` and ``{retry}`` (the retry's id, or ``none``)
        and vetted as any brief is. A refusal writes nothing.
        """
        with record.editing(self.path, self.store, after=self._rerender) as queue:
            failed = queue.mark(entry_id, FAILED, reason=reason)
            retried = None
            if retry:
                retried = queue.add(failed.ref, added_by=ORACLE, brief=failed.brief, at=1,
                                    mode=failed.mode, retry_of=failed.retry_of or failed.id)
            triaged = None
            if triage is not None:
                brief = self._vet_brief(triage.format(
                    entry=failed.id, ref=failed.ref, reason=reason,
                    retry=retried.id if retried else "none",
                ))
                triaged = queue.add(TRIAGE_REF.format(entry=failed.id), added_by=ORACLE,
                                    brief=brief, at=1, triage_of=failed.id)
            return {"failed": failed.id, "triage": triaged.id if triaged else None,
                    "retry": retried.id if retried else None}

    def requeue(self, entry_id: str) -> Entry:
        """Put an entry back at the head, ``queued`` with no session, in one edit.

        A user's Retry of a stalled chained start (ADR-027 ruling 12): the same
        entry, started fresh on the next tick. It is not ruling 9's one retry,
        so the entry keeps its own ``retry_of`` and ``triage_of`` as they were.
        """
        with record.editing(self.path, self.store, after=self._rerender) as queue:
            entry = queue.mark(entry_id, QUEUED)
            entry.session = None
            queue.move(entry_id, 1)
            return entry

    def remove(self, entry_id: str, expect_at: int | None = None) -> Entry:
        """Take an entry off; with ``expect_at``, only while it is queued there.

        The check is made under the queue's lock, in the same edit, so a
        confirm from the phone never removes an entry that moved or started
        since (ADR-027 increment 3). A refusal writes nothing.
        """
        with record.editing(self.path, self.store, after=self._rerender) as queue:
            if expect_at is not None:
                self._expect(queue, entry_id, expect_at, "removed")
            return queue.remove(entry_id)

    def move(self, entry_id: str, to: int, expect_at: int | None = None) -> Queue:
        """Put an entry at position ``to``; with ``expect_at``, only from there."""
        with record.editing(self.path, self.store, after=self._rerender) as queue:
            if expect_at is not None:
                self._expect(queue, entry_id, expect_at, "moved")
            queue.move(entry_id, to)
            return queue

    @staticmethod
    def _expect(queue: Queue, entry_id: str, position: int, verb: str) -> None:
        try:
            queue.expect(entry_id, position)
        except UsageError as exc:
            raise UsageError(f"{exc}; nothing was {verb}") from exc

    # -- the backlog mirror (increment 5) ----------------------------------

    def mirror(self) -> dict:
        """Reconcile the queue with the backlog (ADR-027 ruling 18); what changed.

        Every open item in a mirrored state gets one entry, ``added_by``
        ``backlog``, placed by backlog rank among the queued mirrored entries
        (before the first whose item ranks after it, else at the end), so
        hand-queued entries keep their places and a first run goes behind
        whatever is queued, in rank order. An item is skipped while any
        entry names it in any state (D18: work that reached the queue is not
        put there again; a retry is ruling 9's and a successor D8's), while a
        live entry serves it, while a live session names it, and once the
        mirror has put it on the queue in its current open spell (so an entry
        the user removed stays off). A queued mirrored entry leaves when its
        item has left the mirrored states, or another live entry serves it;
        one whose item cannot be found is left alone. Nothing is written when
        nothing changed. Returns ``{"added": [(entry, ref)], "removed":
        [(entry, ref)]}``, with ``"off": True`` when ``oracle.chain.mirror`` is
        off.
        """
        manifest = self._backlog_manifest()
        if manifest.get("oracle.chain.mirror") is False:
            return {"added": [], "removed": [], "off": True}
        items = WorkItemDomain(manifest)
        every = items.list()
        states = self._mirror_states(items, manifest)
        by_id = {item.id: item for item in every}
        wanted = {item.id for item in every if item.status in states.get(item.type, ())}
        working = self._worked_on(manifest, wanted)
        added: list[tuple[str, str]] = []
        removed: list[tuple[str, str]] = []
        with self.store.lock(record.LOCK):
            queue = record.read_queue(self.path)
            before = json.dumps(queue.to_dict(), sort_keys=True)
            self._unmirror(queue, by_id, wanted, removed)
            named = {e.ref for e in queue.entries}
            served = {e.serves for e in queue.entries if e.state in OPEN_STATES and e.serves}
            remembered = set(queue.mirrored)
            fresh = [item for item in every if item.id in wanted
                     and item.id not in named | served | remembered | working]
            for item in sorted(fresh, key=lambda i: _rank(i)):
                at = _place(queue, _rank(item), by_id)
                entry = queue.add(item.id, added_by=BACKLOG, at=at)
                queue.mirrored.append(item.id)
                added.append((entry.id, item.id))
            if json.dumps(queue.to_dict(), sort_keys=True) != before:
                record.write_queue(self.path, queue)
                self._rerender(queue)
        return {"added": added, "removed": removed}

    @staticmethod
    def _unmirror(queue: Queue, by_id: dict, wanted: set[str],
                  removed: list[tuple[str, str]]) -> None:
        for item_id in list(queue.mirrored):
            if item_id in by_id and item_id not in wanted:
                queue.mirrored.remove(item_id)
        for entry in list(queue.entries):
            if entry.state != QUEUED or entry.added_by != BACKLOG:
                continue
            closed = entry.ref in by_id and entry.ref not in wanted
            served = any(other is not entry and other.state in OPEN_STATES
                         and entry.ref in (other.ref, other.serves)
                         for other in queue.entries)
            if closed or served:
                queue.entries.remove(entry)
                if entry.ref in queue.mirrored:
                    queue.mirrored.remove(entry.ref)
                removed.append((entry.id, entry.ref))

    def _backlog_manifest(self):
        """The manifest of the main checkout (or the store), whose backlog the
        oracle reads: an item filed in a worktree reaches the queue when it
        lands there (D10)."""
        from ..core.manifest import Manifest

        here = Path(self.manifest.root)
        main = record.main_root(here)
        try:
            if main.resolve() == here.resolve():
                return self.manifest
            return Manifest.locate(main)
        except (ManifestError, OSError):
            return self.manifest

    @staticmethod
    def _mirror_states(items: WorkItemDomain, manifest) -> dict[str, tuple[str, ...]]:
        """Item type -> the states it is mirrored in: ``oracle.chain.mirror_states``,
        each checked against its machine, else each machine's ``mirror`` role."""
        configured = manifest.get("oracle.chain.mirror_states")
        if configured is None:
            return {t: items.machine(t).states_with_role(MIRROR_ROLE) for t in ITEM_TYPES}
        out: dict[str, tuple[str, ...]] = {}
        for item_type, names in configured.items():
            machine = items.machine(item_type)
            unknown = [n for n in names if n not in machine.states or n in machine.terminal]
            if unknown:
                open_states = [s for s in machine.states if s not in machine.terminal]
                raise UsageError(
                    f"oracle.chain.mirror_states.{item_type} names {', '.join(unknown)}, "
                    f"which is no open state of the {item_type} machine; its open states "
                    f"are {', '.join(open_states)}. Nothing was mirrored"
                )
            out[item_type] = tuple(names)
        return out

    def _worked_on(self, manifest, wanted: set[str]) -> set[str]:
        """The mirrored items a live session names, by its name or an open
        deliverable: the engine's only link from a session to an item."""
        if not wanted:
            return set()
        try:
            sessions = SessionDomain(manifest, store=InertStore(Path(manifest.state_root)))
            live = sessions.live()
        except (OSError, ValueError, ManifestError):
            return set()
        texts = []
        for session in live:
            texts.append(session.name or "")
            texts.extend(str(d.get("title") or "") for d in session.plan_open)
        haystack = "\n".join(_normal(t) for t in texts)
        return {item for item in wanted
                if re.search(rf"(?<![a-z0-9]){re.escape(_normal(item))}(?![a-z0-9])",
                             haystack)}

    def after_reproduce(self, entry_id: str, outcome: str) -> Entry:
        """Route a reproduce-first session's verdict (ADR-027 ruling 20), in one edit.

        ``red``: the same entry back at the head, ``queued`` with no session,
        so the fix starts on the next tick. ``closed``: the verdict closed the
        item, so the entry is ``done``. ``owed``: a live-environment leg is
        owed, so the entry stays where it is as a proposal for the user.
        ``blind``: nothing was recorded, so it runs once more at the head; a
        second such run makes it a proposal where it is. Neither pauses the
        chain.
        """
        if outcome not in REPRO_OUTCOMES:
            raise UsageError(
                f"a reproduction's outcome is one of {', '.join(REPRO_OUTCOMES)}, "
                f"not {outcome!r}"
            )
        with record.editing(self.path, self.store, after=self._rerender) as queue:
            if outcome == REPRO_CLOSED:
                return queue.mark(entry_id, DONE)
            entry = queue.mark(entry_id, QUEUED)
            entry.session = None
            if outcome == REPRO_RED:
                entry.mode = None
                queue.move(entry_id, 1)
            elif outcome == REPRO_OWED:
                entry.mode = PROPOSE
            elif outcome == REPRO_BLIND:
                entry.blind = (entry.blind or 0) + 1
                if entry.blind >= BLIND_RUNS:
                    entry.mode = PROPOSE
                else:
                    queue.move(entry_id, 1)
            return entry

    # -- the plan page (ADR-032 increment 6) -------------------------------

    def render(self) -> PageRendered:
        """Render the plan page beside the queue now, under the queue's lock.

        Raises :class:`core.queue.PageRefused`, writing nothing, on a privacy
        finding in the text or a file there that carries no managed marker.
        """
        with self.store.lock(record.LOCK):
            return self._render(record.read_queue(self.path))

    def _rerender(self, queue: record.Queue) -> None:
        """An edit's render: never raises, so the edit it follows stands."""
        try:
            self._render(queue)
        except (InvariantError, OSError) as exc:
            self.notes.append(f"plan page not rendered: {exc}")

    def _render(self, queue: record.Queue) -> PageRendered:
        page, why = record.plan_page(self.manifest)
        if page is None:
            return PageRendered(None, "none", False, reason=why)
        relpath = self._spelled(page)
        text = record.render_page(
            queue,
            source=self._spelled(self.path),
            needs=self.declared_needs(page.parent, needs_reader.types_of(self.manifest)),
            is_item=self._item_shaped,
            sessions=self._open_plans(),
            on=self.today(),
        )
        found = PrivacyScanner.from_manifest(self.manifest).scan_text(text, path=Path(relpath))
        if found:
            named = ", ".join(f"line {f.line} {f.pattern} {f.redacted}" for f in found)
            raise record.PageRefused(
                f"the plan page {relpath} fails the privacy scan ({named}); nothing was "
                f"written. Change what the queue or the session plan says, then "
                f"'invariant queue render'"
            )
        existing = page.read_text(encoding="utf-8") if page.is_file() else None
        state = managed_state(existing, text, "sphinx")
        if state == "unmanaged":
            raise record.PageRefused(
                f"{relpath} exists and carries no invariant-managed marker, so it is a "
                f"hand-written page and is never overwritten; move it, and the plan page "
                f"is rendered there"
            )
        if state == "current":
            return PageRendered(page, state, False, relpath)
        temporary = page.with_name(f".{page.name}.{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, page)
        return PageRendered(page, state, True, relpath)

    def _spelled(self, path: Path) -> str:
        """``path`` from the checkout it is in: the main checkout, or the store."""
        bases = [record.main_root(Path(self.manifest.root)),
                 getattr(self.manifest, "store_root", None)]
        for base in bases:
            if base is None:
                continue
            try:
                return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
            except (ValueError, OSError):
                continue
        return Path(path).name

    @staticmethod
    def declared_needs(srcdir: Path, types: Iterable[needs_reader.NeedType]) -> frozenset[str]:
        """Every need id the docs project at ``srcdir`` declares: the ``:id:`` of
        each directive of a declared type, in each ``.rst`` file outside a
        hidden folder and ``_build``."""
        directives = {t.directive for t in types}
        found: set[str] = set()
        for folder, dirnames, filenames in os.walk(srcdir):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in _NOT_PAGES]
            for name in filenames:
                if not name.endswith(".rst"):
                    continue
                try:
                    text = (Path(folder) / name).read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                found.update(
                    d.options["id"] for d in rst_directives(text)
                    if d.name in directives and d.options.get("id")
                )
        return frozenset(found)

    def _open_plans(self) -> list[record.SessionPlan]:
        """Each live session's deliverables, read through an inert store."""
        try:
            live = SessionDomain(
                self.manifest, store=InertStore(Path(self.manifest.state_root))
            ).live()
        except (OSError, ValueError, ManifestError):
            return []
        return [record.SessionPlan(id=s.id, name=s.name or "", plan=tuple(s.plan or ()))
                for s in live]

    def _check_served(self, item: str) -> None:
        if not self._item_shaped(item):
            raise UsageError(
                f"an entry serves a work item, by its id; {item!r} is not one"
            )
        self._check_item(item)

    def _vet_brief(self, brief: str) -> str:
        return vet_line(
            self.manifest, brief, noun="queue brief",
            one_line="a queue brief is one line, the next session's first prompt. "
                     "Join it with ' · '.",
            refused="it is not queued", again="queue it again", bound=BRIEF_MAX,
        )

    def _check_item(self, ref: str) -> None:
        """A ``ref`` shaped like a work-item id must name an open item (D10)."""
        if not self._item_shaped(ref):
            return
        items = self.items
        item = items.get(ref)
        if item.status in items.machine(item.type).terminal:
            raise UsageError(
                f"{ref} is {item.status}, a closed state; only open work goes on "
                f"the queue"
            )

    def _item_shaped(self, ref: str) -> bool:
        prefix = re.escape(self.manifest.get("items.id_prefix") or "")
        types = "|".join(t.upper() for t in ITEM_TYPES)
        return re.fullmatch(rf"{prefix}(?:{types})_[A-Za-z0-9_.-]+", ref) is not None


def _normal(text: str) -> str:
    return text.lower().replace("_", "-")


def _rank(item) -> tuple:
    fields = item.fields or {}
    return rank_key(item.id, fields.get("tier"), fields.get("priority"))


#: Where an entry whose item is not found ranks: after every item.
_LAST = ((2, 0.0), 99, [])


def _place(queue: Queue, key: tuple, by_id: dict) -> int | None:
    """The position a mirrored item of rank ``key`` goes in: before the first
    queued mirrored entry whose item ranks after it; ``None`` for the end."""
    for position, entry in enumerate(queue.entries, start=1):
        if entry.state != QUEUED or entry.added_by != BACKLOG:
            continue
        other = by_id.get(entry.ref)
        if (_rank(other) if other is not None else _LAST) > key:
            return position
    return None
