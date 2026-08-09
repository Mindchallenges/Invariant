"""The work-item domain.

Lifecycle semantics live here, above the storage backend, so switching a
project's backlog format changes where items are written and nothing about
what a status means.
"""

from __future__ import annotations

from pathlib import Path

from ..core.errors import GateFailure, UsageError
from ..core.items import Item, backend_for
from ..core.machines import load_item_machine
from ..core.manifest import Level
from ..core.rules import Evaluated, RuleContext, evaluate

ITEM_TYPES = ("idea", "bug")


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
    ) -> tuple[Item, list[Evaluated]]:
        item = self.get(item_id)
        machine = self.machine(item.type)

        structural_override = False
        if item.status != target:
            try:
                machine.require_edge(item.status, target)
            except Exception:
                if not force:
                    raise
                structural_override = True

        gate_ids = list(machine.state(item.status).exit_gates)
        gate_ids += [g for g in machine.state(target).entry_gates if g not in gate_ids]

        ctx = RuleContext(
            manifest=self.manifest,
            cwd=Path(cwd or Path.cwd()),
            extra={"item": item.to_dict(), "target_status": target},
        )
        verdicts = evaluate(gate_ids, ctx)
        blocking = [v for v in verdicts if v.blocks]

        if blocking and not force:
            raise GateFailure(
                f"{len(blocking)} gate(s) block {item.status!r} -> {target!r} "
                f"for {item.id}",
                verdicts=verdicts,
            )
        if force and (blocking or structural_override) and not reason:
            raise UsageError(
                "--force requires --reason: an override that records no "
                "rationale is indistinguishable from the gate never having run"
            )

        item.history.append(
            {
                "from": item.status,
                "to": target,
                "reason": reason,
                "forced": bool(force and (blocking or structural_override)),
            }
        )
        item.status = target
        self.backend.save(item)
        return item, verdicts

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
