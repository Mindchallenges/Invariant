"""The work-item domain.

Lifecycle semantics live here, above the storage backend, so switching a
project's backlog format changes where items are written and nothing about
what a status means.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..core import mdconvert, needs
from ..core.errors import GateFailure, TransitionError, UsageError
from ..core.git import resolve_commit
from ..core.items import (
    BODY_MARKDOWN,
    BODY_RST,
    STORES,
    Item,
    NullBackend,
    backend_for,
    differences,
)
from ..core.machines import load_item_machine
from ..core.rules import Evaluated, RuleContext, evaluate
from . import docs as docs_domain

ITEM_TYPES = ("idea", "bug")

#: The rule an item write is checked by (ADR-018 D8).
LINKED_RULE = "items.decisions_linked"

#: The item's own keys that ``item new --field`` must not set, and where each
#: one is set instead. ``id`` is absent: an explicit id is honoured.
_SET_ELSEWHERE = {
    "status": (
        "a new item starts in its initial state ({initial}), and every later "
        "state is reached with `invariant item transition`, through the gates "
        "that state carries (filed -> reproduced needs its evidence)"
    ),
    "type": "the type is the positional argument",
    "title": "the title is --title",
    "residue": "residue is recorded with `invariant item residue <id> --add`",
    "history": "history is written by `invariant item transition`, never by hand",
}

#: An explicit id: one token a file name and a :need: target can both carry.
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _shown(store: Path, root: Path) -> str:
    try:
        return str(store.relative_to(Path(root).resolve()))
    except ValueError:
        return str(store)


def _one_step_between(machine, source: str, target: str) -> str | None:
    """The single state both ``source -> X`` and ``X -> target`` reach, if one.

    Read from the machine rather than named here, so a project's own item
    machine answers for itself. Two candidates are no answer: a ruling records
    one step it vouches for, and choosing between two would be a guess.
    """
    between = [s for s in machine.next_states(source) if machine.can(s, target)]
    return between[0] if len(between) == 1 else None


def _refuse_edge(machine, source: str, target: str, via: str | None) -> None:
    try:
        machine.require_edge(source, target)
    except TransitionError as exc:
        if via is None:
            raise
        raise TransitionError(
            f"{exc}. A ruled route through {via!r} exists: add --ruling "
            f"\"<who, date>\" and --evidence <commit> to record {source} -> "
            f"{via} -> {target} on that ruling"
        ) from None


#: How many read-back differences a refused migration names before the count.
_NAMED = 20

#: A body format as a sentence names it.
_FORMAT_NAMES = {BODY_MARKDOWN: "Markdown", BODY_RST: "rST"}


@dataclass(frozen=True)
class Unconverted:
    """A construct a converted body left as ``.. invariant-unconverted::``:
    the item, the target store and the line it is written on, and why."""

    id: str
    path: Path
    line: int
    reason: str


@dataclass(frozen=True)
class Migration:
    """What ``item migrate`` moved, or would (ADR-018 D14): the target
    backend and its stores, the ids written to each, and how many items the
    target held already and keeps; with ruling 20, the ids whose bodies were
    converted, each construct left unconverted, and the converter's notes."""

    backend: str
    path: Path
    closed_path: Path | None
    open: tuple[str, ...]
    closed: tuple[str, ...]
    kept: int
    dry_run: bool
    converted: tuple[str, ...] = ()
    unconverted: tuple[Unconverted, ...] = ()
    notes: tuple[str, ...] = ()


def _staged(real: Path, stage: Path) -> Path:
    """A copy of ``real`` (a file, or a markdown store's directory) at ``stage``."""
    stage.parent.mkdir(parents=True, exist_ok=True)
    if real.is_dir():
        shutil.copytree(real, stage)
    elif real.is_file():
        shutil.copyfile(real, stage)
    return stage


def _publish(stage: Path, real: Path) -> None:
    """Write a staged store over the real one: a file replaced whole, a
    directory's files copied in, each through a temporary beside it."""
    if stage.is_dir():
        for path in sorted(p for p in stage.rglob("*") if p.is_file()):
            _replace(path, real / path.relative_to(stage))
    elif stage.is_file():
        _replace(stage, real)


def _replace(source: Path, real: Path) -> None:
    real.parent.mkdir(parents=True, exist_ok=True)
    temporary = real.with_name(f".{real.name}.migrating")
    shutil.copyfile(source, temporary)
    os.replace(temporary, real)


class WorkItemDomain:
    def __init__(self, manifest) -> None:
        self.manifest = manifest
        self.backend = backend_for(manifest)
        #: What ``items.decisions_linked`` said of the last write without
        #: refusing it (ADR-018 D8): a warning while the project migrates. The
        #: CLI prints them.
        self.warnings: list[Evaluated] = []

    def _check_links(
        self, item_id: str, *texts: str | None, cwd: Path | None = None, force: bool = False
    ) -> None:
        """Refuse a write naming a design id no recorded build holds (D8).

        ``items.decisions_linked``, asked with the text the write adds; a
        blocking verdict refuses the write before anything is saved, and a
        warning is kept on :attr:`warnings`.
        """
        text = "\n".join(t for t in texts if t)
        self.warnings = []
        if not text:
            return
        ctx = RuleContext(
            manifest=self.manifest,
            cwd=Path(cwd or Path.cwd()),
            extra={"item_text": text, "item_id": item_id},
        )
        verdicts = evaluate([LINKED_RULE], ctx)
        blocking = [v for v in verdicts if v.blocks]
        if blocking and force:
            # `item transition --force --reason`: the reasoned route, recorded
            # on the history row the transition writes.
            self.warnings = blocking
            return
        if blocking:
            raise GateFailure(
                f"{item_id or 'the item'} names a design id no recorded docs build "
                f"holds; nothing was written",
                verdicts=blocking,
            )
        self.warnings = [v for v in verdicts if v.warns]

    def machine(self, item_type: str):
        if item_type not in ITEM_TYPES:
            raise UsageError(
                f"unknown item type {item_type!r}; known types: "
                + ", ".join(ITEM_TYPES)
            )
        return load_item_machine(item_type, self.manifest)

    # -- reads ---------------------------------------------------------

    def list(
        self,
        item_type: str | None = None,
        status: str | None = None,
        open_only: bool = False,
    ) -> list[Item]:
        items = self.backend.load_all()
        if item_type:
            items = [i for i in items if i.type == item_type]
        if status:
            items = [i for i in items if i.status == status]
        if open_only:
            items = [
                i for i in items if i.status not in self.machine(i.type).terminal
            ]
        return items

    def get(self, item_id: str) -> Item:
        item = self.backend.get(item_id)
        if item is None:
            raise UsageError(f"no item {item_id!r}")
        return item

    # -- writes --------------------------------------------------------

    def new(
        self,
        item_type: str,
        title: str,
        fields: dict | None = None,
        body: str = "",
        section: str | None = None,
        after: str | None = None,
    ) -> Item:
        """File a new item in its machine's initial state.

        A field naming one of the item's own keys is honoured or refused,
        never dropped: ``id`` replaces the minted id once it is shown to be
        unspent in every store the backend reads, and the rest are refused,
        each pointing at the surface that does set it (BUG_074 (a)).
        ``section`` and ``after`` are placements; the backend honours or
        refuses them (BUG_074 (b)).
        """
        machine = self.machine(item_type)
        fields = dict(fields or {})
        for key in fields:
            if key in _SET_ELSEWHERE:
                raise UsageError(
                    f"--field {key}=... cannot be set by item new: "
                    + _SET_ELSEWHERE[key].format(initial=machine.initial)
                    + ". Nothing was filed."
                )
        if not title.strip():
            raise UsageError("an item needs a title")
        explicit = fields.pop("id", None)
        self._check_links(
            str(explicit or ""), title, body, *(str(v) for v in fields.values())
        )
        item = Item(
            id=(
                self._unspent(str(explicit))
                if explicit is not None
                else self.backend.next_id(item_type, title)
            ),
            type=item_type,
            title=title.strip(),
            status=machine.initial,
            fields=fields,
            body=body,
        )
        self.backend.add(item, section=section, after=after)
        return item

    def _unspent(self, item_id: str) -> str:
        """An explicit id, refused if malformed or already spent anywhere.

        Compared without regard to case: the markdown backend names a file
        after the id, and on a case-insensitive filesystem two ids differing
        only in case are one file — the second save would overwrite the first.
        """
        item_id = item_id.strip()
        if not _ID_RE.match(item_id):
            raise UsageError(
                f"--field id={item_id!r} is not an id: use letters, digits, '_', "
                "'-' and '.', starting with a letter or digit. Nothing was filed."
            )
        for spent, store in self.backend.taken_ids().items():
            if spent.casefold() == item_id.casefold():
                raise UsageError(
                    f"--field id={item_id} clashes with {spent}, already in "
                    f"{_shown(store, self.manifest.root)}; choose another id or "
                    "omit it to have one minted. Nothing was filed."
                )
        return item_id

    def transition(
        self,
        item_id: str,
        target: str,
        reason: str | None = None,
        force: bool = False,
        cwd: Path | None = None,
        ruling: str | None = None,
        evidence: str | None = None,
    ) -> tuple[Item, list[Evaluated]]:
        """Move an item to ``target``, through its machine's gates.

        ``ruling`` and ``evidence`` are a maintainer's ruling and the commit it
        rests on. Given together, they are recorded on every history row this
        call writes, and they buy exactly one thing beyond the record: where
        the machine has no direct edge, but one state lies between that both
        edges reach, the call records that state too, on the ruling's
        authority. That is how bug-reproducer's ALREADY-FIXED verdict closes
        a ``filed`` bug: its test is red with the fix reverted, which is a
        reproduction on the pre-fix tree, so ``filed -> reproduced -> fixed``
        is written as one command and `fixed` is still reached only through
        ``reproduced``. Each step is gated as if walked; nothing is written
        unless every step passes, and then in one save.
        """
        item = self.get(item_id)
        machine = self.machine(item.type)
        provenance = self._provenance(ruling, evidence)
        self._check_links(item.id, reason, ruling, cwd=cwd, force=bool(force and reason))

        path = [target]
        structural_override = False
        if item.status != target and not machine.can(item.status, target):
            via = _one_step_between(machine, item.status, target)
            if via and provenance:
                path = [via, target]
            elif force:
                structural_override = True
            else:
                _refuse_edge(machine, item.status, target, via)

        work = Item.from_dict(item.to_dict())
        verdicts: list[Evaluated] = []
        rows: list[dict] = []
        for step in path:
            step_verdicts = self._gate(machine, work, step, cwd)
            verdicts += step_verdicts
            blocking = [v for v in step_verdicts if v.blocks]
            if blocking and not force:
                raise GateFailure(
                    f"{len(blocking)} gate(s) block {work.status!r} -> {step!r} "
                    f"for {item.id}",
                    verdicts=verdicts,
                )
            overridden = bool(blocking or (structural_override and step == target))
            if force and overridden and not reason:
                raise UsageError(
                    "--force requires --reason: an override that records no "
                    "rationale is indistinguishable from the gate never having run"
                )
            row = {
                "from": work.status,
                "to": step,
                "reason": reason,
                "forced": bool(force and overridden),
            }
            if provenance:
                row.update(provenance)
                if step != target:
                    row["intermediate"] = True
            rows.append(row)
            work.history.append(row)
            work.status = step

        item.history.extend(rows)
        item.status = target
        self.backend.save(item)
        return item, verdicts

    def _gate(self, machine, item: Item, target: str, cwd: Path | None) -> list[Evaluated]:
        gate_ids = list(machine.state(item.status).exit_gates)
        gate_ids += [g for g in machine.state(target).entry_gates if g not in gate_ids]
        ctx = RuleContext(
            manifest=self.manifest,
            cwd=Path(cwd or Path.cwd()),
            extra={"item": item.to_dict(), "target_status": target},
        )
        return evaluate(gate_ids, ctx)

    def _provenance(self, ruling: str | None, evidence: str | None) -> dict | None:
        """The ruling and its commit, both or neither; the commit must resolve."""
        ruling = (ruling or "").strip()
        evidence = (evidence or "").strip()
        if not ruling and not evidence:
            return None
        if not ruling:
            raise UsageError(
                "--evidence needs --ruling: name who ruled and when, so the "
                "record says whose judgement the commit stands on"
            )
        if not evidence:
            raise UsageError(
                "--ruling needs --evidence: name the commit the ruling rests on; "
                "a ruling with no commit is an assertion nothing can check"
            )
        sha = resolve_commit(self.manifest.root, evidence)
        if sha is None:
            raise UsageError(
                f"--evidence {evidence!r} names no commit in this repository; "
                "nothing was recorded"
            )
        return {"ruling": ruling, "evidence": sha}

    def add_residue(self, item_id: str, text: str) -> Item:
        item = self.get(item_id)
        self._check_links(item.id, text)
        if text not in item.residue:
            item.residue.append(text)
        self.backend.save(item)
        return item

    def clear_residue(self, item_id: str, text: str) -> Item:
        item = self.get(item_id)
        before = len(item.residue)
        item.residue = [r for r in item.residue if r != text]
        if len(item.residue) == before:
            raise UsageError(
                f"{item_id} has no residue matching {text!r}; current residue: "
                + ("; ".join(item.residue) or "(none)")
            )
        self.backend.save(item)
        return item

    # -- migration (ADR-018 D14) ------------------------------------------

    def migrate(
        self,
        to: str,
        path: Path | None,
        closed_path: Path | None = None,
        dry_run: bool = False,
        pandoc: mdconvert.Pandoc | None = None,
        reader: str = mdconvert.READER,
    ) -> Migration:
        """Move every item, open and closed, into the ``to`` backend.

        The items are read from the configured store and from its closed
        store (``items.closed_path``) when one is declared. With
        ``closed_path``, an item in a terminal state of its machine goes
        there; otherwise every item goes to ``path``. ``path`` defaults, for
        ``sphinx_needs``, to the docs project's items page.

        Nothing is written until the whole move has been made on a staged
        copy of the target and read back: every item's type, title, status,
        fields, residue, history and body must read back exactly as they were
        read, or the move is refused, naming each difference, and nothing is
        written. Items the target holds already are kept; one whose id the
        move would bring again is refused. The source store is never written,
        and the manifest is not changed: the switch is the migration
        session's last step.

        A body moves as written when both backends hold the same markup.
        From Markdown to rST it is converted by ``docs migrate``'s converter,
        through the docs environment's pandoc (``pandoc`` stands in for it,
        ``reader`` is pandoc's Markdown reader), and the read-back compares
        the converted body (ADR-018 ruling 20). rST to Markdown has no
        converter: a move that would need one is refused.
        """
        if to not in STORES:
            raise UsageError(
                f"item migrate moves items to a store; {to!r} is not a backend that "
                f"stores them (one of {', '.join(STORES)})"
            )
        if isinstance(self.backend, NullBackend):
            raise UsageError(
                "the work-item domain is disabled (items.backend is 'none'): there "
                "are no items to migrate"
            )
        path = self._target_path(to, path)
        closed_path = Path(closed_path).resolve() if closed_path is not None else None
        if closed_path is not None and closed_path == path:
            raise UsageError(
                f"--closed-path names the open store ({_shown(path, self.manifest.root)}); "
                "the closed store is another file"
            )
        source = self._source_stores()
        for target in filter(None, (path, closed_path)):
            if any(target == Path(store.path).resolve() for store in source):
                raise UsageError(
                    f"{_shown(target, self.manifest.root)} is a source store of "
                    "this migration; migrate into another path"
                )
        moved, origin = self._source_items(source)
        prefix = self.manifest.get("items.id_prefix", "") or ""
        strategy = self.manifest.get("items.id_strategy", "numeric") or "numeric"
        cls = STORES[to]
        with tempfile.TemporaryDirectory(prefix="invariant-migrate-") as tmp:
            stage_open = _staged(path, Path(tmp) / "open" / path.name)
            stage_closed = (
                _staged(closed_path, Path(tmp) / "closed" / closed_path.name)
                if closed_path is not None
                else None
            )
            stores = [cls(stage_open, prefix, strategy)]
            if stage_closed is not None:
                stores.append(cls(stage_closed, prefix, strategy))
            held = {i.id for store in stores for i in store.load_all()}
            clash = sorted(i.id for i in moved if i.id in held)
            if clash:
                raise UsageError(
                    f"{', '.join(clash)}: already in the target; an id is never "
                    "spent twice. Nothing was written."
                )
            moved, converted, notes = self._bodies(moved, origin, cls, pandoc, reader)
            opened: list[str] = []
            closed: list[str] = []
            for item in moved:
                into_closed = stage_closed is not None and self._closed(item)
                store = stores[1] if into_closed else stores[0]
                try:
                    store.add(Item.from_dict(item.to_dict()))
                except UsageError as exc:
                    raise UsageError(
                        f"{item.id} cannot be stored by {to}: {exc}. Nothing was written."
                    ) from exc
                (closed if into_closed else opened).append(item.id)
            self._read_back(cls, stores, moved, prefix, strategy, to)
            unconverted = self._unconverted(
                converted, ((stage_open, path), (stage_closed, closed_path))
            )
            if not dry_run:
                _publish(stage_open, path)
                if stage_closed is not None and closed:
                    _publish(stage_closed, closed_path)
        return Migration(
            backend=to,
            path=path,
            closed_path=closed_path,
            open=tuple(opened),
            closed=tuple(closed),
            kept=len(held),
            dry_run=dry_run,
            converted=converted,
            unconverted=unconverted,
            notes=notes,
        )

    def _bodies(
        self, moved: list[Item], origin: dict[str, Path], cls, pandoc, reader
    ) -> tuple[list[Item], tuple[str, ...], tuple[str, ...]]:
        """The items as the target holds them: each body as written when the
        two backends' markup agrees, converted Markdown to rST when it does
        not (ruling 20), and the move refused when it would need rST to
        Markdown. An empty body is never converted."""
        source_format = type(self.backend).body_format
        bodied = [item for item in moved if item.body.strip()]
        if source_format == cls.body_format or not bodied:
            return moved, (), ()
        if (source_format, cls.body_format) != (BODY_MARKDOWN, BODY_RST):
            named = ", ".join(item.id for item in bodied[:_NAMED])
            more = len(bodied) - min(len(bodied), _NAMED)
            raise UsageError(
                f"the source holds bodies as {_FORMAT_NAMES[source_format]} and the "
                f"target as {_FORMAT_NAMES[cls.body_format]}; item migrate converts "
                f"Markdown to rST only (ADR-018 ruling 20), and {len(bodied)} item(s) "
                f"carry a body: {named}"
                + (f" and {more} more" if more else "")
                + ". Nothing was written."
            )
        files = {
            (store / f"{item.id}.md").resolve(): item.id
            for item in moved
            if (store := origin[item.id]).is_dir()
        }
        convert = docs_domain.item_body_converter(
            self.manifest, items=files, pandoc=pandoc, reader=reader
        )
        out: list[Item] = []
        converted: list[str] = []
        notes: list[str] = []
        for item in moved:
            if not item.body.strip():
                out.append(item)
                continue
            store = origin[item.id]
            done = convert(item.body, store if store.is_dir() else store.parent)
            moved_item = Item.from_dict(item.to_dict())
            moved_item.body = done.text.strip("\n")
            out.append(moved_item)
            converted.append(item.id)
            notes += [f"{item.id}: {note}" for note in done.notes]
        return out, tuple(converted), tuple(notes)

    @staticmethod
    def _unconverted(converted, stages) -> tuple[Unconverted, ...]:
        """Each construct the converted bodies left unconverted, by the line
        it is written on in the target store."""
        if not converted:
            return ()
        ids = set(converted)
        found = []
        for stage, real in stages:
            if stage is None or not stage.is_file():
                continue
            text = stage.read_text(encoding="utf-8")
            found += [
                Unconverted(item_id, real, line, reason)
                for item_id, line, reason in docs_domain.unconverted_in(text)
                if item_id in ids
            ]
        return tuple(found)

    def _target_path(self, to: str, path: Path | None) -> Path:
        if path is not None:
            return Path(path).resolve()
        srcdir = needs.srcdir_of(self.manifest) if to == "sphinx_needs" else None
        if srcdir is None:
            raise UsageError(
                f"name the {to} store to migrate into with --path"
                + (
                    " (with no docs project there is no items page to default to)"
                    if to == "sphinx_needs"
                    else ""
                )
            )
        return (srcdir / needs.ITEMS_PAGE).resolve()

    def _source_stores(self) -> list:
        stores = [self.backend]
        closed = self.manifest.get("items.closed_path")
        if closed:
            backend = self.backend
            stores.append(
                type(backend)(
                    (self.manifest.root / closed).resolve(),
                    backend.id_prefix,
                    backend.id_strategy,
                )
            )
        return stores

    def _source_items(self, stores: list) -> tuple[list[Item], dict[str, Path]]:
        """Every item of the source stores, and the store each is read from."""
        items: list[Item] = []
        seen: dict[str, Path] = {}
        for store in stores:
            if store is not stores[0] and not Path(store.path).exists():
                raise UsageError(
                    f"items.closed_path names {_shown(store.path, self.manifest.root)}, "
                    "which does not exist; its items cannot be read, so none is migrated"
                )
            for item in store.load_all():
                if item.id in seen:
                    raise UsageError(
                        f"{item.id} is in both {_shown(seen[item.id], self.manifest.root)} "
                        f"and {_shown(store.path, self.manifest.root)}; settle which one "
                        "holds it before migrating"
                    )
                seen[item.id] = Path(store.path).resolve()
                items.append(item)
        return items, seen

    def _closed(self, item: Item) -> bool:
        return item.status in self.machine(item.type).terminal

    def _read_back(self, cls, stores, moved, prefix, strategy, to) -> None:
        back: dict[str, Item] = {}
        for store in stores:
            for item in cls(store.path, prefix, strategy).load_all():
                back[item.id] = item
        problems = []
        for item in moved:
            named = differences(item, back.get(item.id))
            if named:
                problems.append(f"{item.id}: {', '.join(named)}")
        if problems:
            shown = problems[:_NAMED]
            more = len(problems) - len(shown)
            raise UsageError(
                f"{len(problems)} item(s) would read back differently from {to}, so "
                "nothing was written:\n"
                + "\n".join(f"  {line}" for line in shown)
                + (f"\n  ... and {more} more" if more else "")
            )

    def open_table(self) -> str:
        """A compact table of everything still open, for a report footer."""
        items = self.list(open_only=True)
        if not items:
            return "No open items."
        width = max(len(i.id) for i in items)
        lines = []
        for item in sorted(items, key=lambda i: (i.type, i.id)):
            owed = f"  (+{len(item.residue)} owed)" if item.residue else ""
            lines.append(f"  {item.id:<{width}}  {item.status:<16}  {item.title}{owed}")
        return "\n".join(lines)
