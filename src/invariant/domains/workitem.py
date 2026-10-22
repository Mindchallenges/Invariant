"""The work-item domain.

Lifecycle semantics live here, above the storage backend, so switching a
project's backlog format changes where items are written and nothing about
what a status means.
"""

from __future__ import annotations

from pathlib import Path

from ..core.errors import GateFailure, TransitionError, UsageError
from ..core.git import resolve_commit
from ..core.items import Item, backend_for
from ..core.machines import load_item_machine
from ..core.rules import Evaluated, RuleContext, evaluate

ITEM_TYPES = ("idea", "bug")


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


class WorkItemDomain:
    def __init__(self, manifest) -> None:
        self.manifest = manifest
        self.backend = backend_for(manifest)

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
    ) -> Item:
        machine = self.machine(item_type)
        item = Item(
            id=self.backend.next_id(item_type, title),
            type=item_type,
            title=title.strip(),
            status=machine.initial,
            fields=dict(fields or {}),
            body=body,
        )
        if not item.title:
            raise UsageError("an item needs a title")
        self.backend.save(item)
        return item

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
