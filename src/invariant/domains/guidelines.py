"""The guidelines domain: the effects the ruleset has on the tree (ADR-012 D12).

Not an FSM, like ``stats`` and ``sweep``: there is no lifecycle to advance.
It owns one effect, rendering: the human checklist (A2 increment 3) and the
managed ``guidelines/index`` block in each instruction file (increment 4).
``core/guidelines`` decides what the text is. Where it goes, whether it may be
written there and what state it is in now is the render plan,
``core.rules.plan_render``: ``guidelines.generated_current`` asks the same
plan, so the drift gate and ``render --dry-run`` cannot disagree about
"current" (A2 increment 5). This module writes what the plan decided. It also
shows the checklist text without writing it, which is where a capped block
points.

It writes one class of file only: a generated file the manifest declares, the
CLI's third write class (ruling 13). That class is never exempt from session
isolation, so a render goes through a session's review like any edit. So a
render is refused, with nothing written, when:

- the ruleset does not load (every gate that needs it fails; so does render);
- the target names nothing inside the project, is the ruleset itself, or is a
  path the project declared exempt from isolation;
- its suffix disagrees with ``docs.format``, or that format has no renderer;
- the file there carries no managed marker, because render did not write it
  and the text is the project's;
- the privacy scan of the rendered text is not clean.

A file render did write is rewritten whatever its state, and its state is
named: current, stale (rendered from an older ruleset), or edited (its body
no longer matches its marker). Hand edits are replaced, because the ruleset is
the source and the fix for drift is "move the change into the ruleset, then
render" (D3). The file is never exempt from isolation, so the replaced text
is still in the branch's diff.

An instruction file is different: only the text between a block's markers is
render's, and the rest is the project's. So a block is never inserted unasked
(``insert`` names it), and a block edited by hand is skipped and named unless
``overwrite_local`` asks for the rendered text back, the three-way rule of
``classify_installed``. The files default to the host adapter's, which the
rule context carries, because no domain imports a host. Refused as well, with
nothing written anywhere:

- an instruction file outside the project, not markdown, exempt from
  isolation, or the same file as the ruleset or the checklist;
- a malformed begin/end pair, named by file and line;
- a block name to insert that render does not know;
- a privacy finding in a rendered block.

Every output is decided, scanned and checked before the first write, so a
refusal leaves the tree as it was. Every refusal is the plan's
``RenderRefused``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core import guidelines
from ..core.rules import (
    BlockRendered,
    RuleContext,
    guidelines_relpath,
    plan_render,
    ruleset_or_refuse,
)


@dataclass(frozen=True)
class Rendered:
    """What one render did, or with ``--dry-run`` would have done."""

    #: ``new``, ``current``, ``stale``, ``edited``, or ``no-ruleset``.
    status: str
    written: bool
    path: Path | None = None
    relpath: str | None = None
    #: The ruleset digest the text was rendered from.
    ruleset: str | None = None
    #: One entry per instruction file and block.
    blocks: tuple[BlockRendered, ...] = ()


@dataclass(frozen=True)
class Shown:
    """The checklist text ``guidelines show`` prints; ``None`` with no ruleset."""

    text: str | None
    ruleset: str | None = None


class GuidelinesDomain:
    def __init__(self, ctx: RuleContext) -> None:
        #: The rule context carries the host adapter's instruction files,
        #: the default of ``guidelines.instruction_files``; the CLI supplies
        #: them, because no domain imports a host.
        self.ctx = ctx
        self.manifest = ctx.manifest

    def render(
        self,
        *,
        dry_run: bool = False,
        insert: tuple[str, ...] = (),
        overwrite_local: bool = False,
    ) -> Rendered:
        plan = plan_render(self.ctx, insert=insert, overwrite_local=overwrite_local)
        if plan is None:
            return Rendered(status="no-ruleset", written=False)
        if not dry_run:
            plan.path.parent.mkdir(parents=True, exist_ok=True)
            plan.path.write_text(plan.text, encoding="utf-8")
            for block in plan.blocks:
                if block.text is not None:
                    block.path.parent.mkdir(parents=True, exist_ok=True)
                    block.path.write_text(block.text, encoding="utf-8")
        return Rendered(
            status=plan.status, written=not dry_run, path=plan.path, relpath=plan.relpath,
            ruleset=plan.ruleset,
            blocks=tuple(
                BlockRendered(
                    block.result.relpath, block.result.name, block.result.status,
                    block.result.written and not dry_run,
                )
                for block in plan.blocks
            ),
        )

    def show(self) -> Shown:
        """The checklist text, in markdown, without its marker. Writes nothing.

        Markdown whatever ``docs.format`` says: it is read by an agent or a
        terminal, not built into the project's docs.
        """
        ruleset = ruleset_or_refuse(self.ctx)
        if ruleset is None:
            return Shown(text=None)
        source = guidelines_relpath(self.ctx, self.manifest.get("guidelines.ruleset"))
        text = guidelines.render_checklist(ruleset, "markdown", source=source)
        _digest, body = guidelines.split_managed(text, "markdown")
        return Shown(text=body.lstrip("\n"), ruleset=ruleset.digest)
