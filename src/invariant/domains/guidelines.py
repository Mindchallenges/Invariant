"""The guidelines domain: the effects the ruleset has on the tree (ADR-012 D12).

Not an FSM, like ``stats`` and ``sweep``: there is no lifecycle to advance.
It owns two effects. Rendering: the human checklist (A2 increment 3) and the
managed ``guidelines/index`` block in each instruction file (increment 4).
And the ratchet baseline (A3 increment 5, D6): ``baseline`` records or
prunes the file ``guidelines.baseline`` names, writing what
``core.rules.plan_baseline`` decided, the same shape as render.
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

The third effect is the verdict submission (A4 increment 1, ADR-012 D5): a
cited verdict on a judgement guideline, checked by ``core.rules.plan_verdicts``
against D5's predicates and recorded into the running pipeline's evidence
through ``PipelineDomain.record_verdicts``. It writes nothing into the tree.
A submission is recorded whole or refused whole (DN-11), and a refusal is
``VerdictRefused``, naming each entry's failed predicate and its fix. An
accepted submission puts one random token on every record it writes and
returns it, so a host that can name the acting agent can find those records
again from the command's output and stamp the agent on them (A4 increment 8,
the user's rulings a to c). The token is the engine's alone: a submitter
naming it is refused as an unknown key.

It also shows how the verdicts stand without writing anything (A4 increment
4): ``verdict_standings`` returns ``core.rules.read_standings``, the read
``guidelines.verdicts_complete`` judges, so the command and the gate cannot
disagree.

And it shows the rules one phase selects (A5 increment 1, ADR-012 D7):
``select`` resolves the file scope (named files, the session's declared plus
changed files, or both) and asks ``core/guidelines.select`` and
``render_selected``, which decide everything else. It writes nothing. An
unknown phase, ``touched`` with no session and a named file outside the
project are each ``SelectionRefused``.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core import guidelines
from ..core.errors import InvariantError
from ..core.rules import (
    BaselineChange,
    BlockRendered,
    RuleContext,
    StandingsRead,
    StandingsRefused,
    VerdictRefused,
    guidelines_relpath,
    plan_baseline,
    plan_render,
    plan_verdicts,
    read_standings,
    ruleset_or_refuse,
    touched_paths,
)

if TYPE_CHECKING:
    from .pipeline import PipelineDomain


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


@dataclass(frozen=True)
class Selected:
    """What ``guidelines show --phase`` prints; ``text`` is ``None`` with no ruleset."""

    phase: str
    #: The rule phases the phase sees (``core/guidelines.phases_for``).
    phases: tuple[str, ...]
    #: The project-relative file scope; empty is no file filter.
    files: tuple[str, ...]
    text: str | None = None
    ruleset: str | None = None
    #: The selected rule ids, in declared order.
    rules: tuple[str, ...] = ()


class SelectionRefused(InvariantError):
    """``guidelines show --phase`` cannot select (exit 1). Names the fix."""

    exit_code = 1

    def __init__(self, message: str, fix: str) -> None:
        super().__init__(f"{message}\n  {fix}")
        self.reason = message
        self.fix = fix


@dataclass(frozen=True)
class VerdictsRecorded:
    """What one verdict submission recorded."""

    records: tuple[dict, ...] = ()
    ruleset: str | None = None
    state: str | None = None
    #: The submission's token, on every record above (A4.8). The command
    #: prints it, and a host that can name the acting agent reads it back
    #: out of that output to stamp these records.
    token: str | None = None


def new_submission_token() -> str:
    """One submission's token: 32 lowercase hex digits, unguessable (A4.8).

    Random rather than derived, so a call that did not make the submission
    cannot compute it, and a host stamping the agent finds exactly the
    records one command wrote.
    """
    return secrets.token_hex(16)


class GuidelinesDomain:
    def __init__(self, ctx: RuleContext, pipelines: PipelineDomain | None = None) -> None:
        #: The rule context carries the host adapter's instruction files,
        #: the default of ``guidelines.instruction_files``; the CLI supplies
        #: them, because no domain imports a host.
        self.ctx = ctx
        self.manifest = ctx.manifest
        #: Where an accepted verdict is recorded (``GDOM --> PIPELINE``, on
        #: the ``stats --> session`` precedent). Only the submission needs it.
        self.pipelines = pipelines

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

    def select(
        self, phase: str, *, files: tuple[str, ...] = (), touched: bool = False
    ) -> Selected:
        """The full text of the rules ``phase`` selects over a file scope.

        ``phase`` is a state role (ADR-012 D7). The scope is ``files``, read
        against the working directory and spelled project-relative, together
        with, when ``touched``, the declared plus changed files of the session
        resolved from the working directory. An empty scope is no file filter.
        Markdown whatever ``docs.format`` says, as :meth:`show` is. Writes
        nothing.
        """
        if phase not in guidelines.ROLE_PHASES:
            raise SelectionRefused(
                f"unknown phase {phase!r}",
                f"name one of {', '.join(guidelines.ROLE_PHASES)}",
            )
        session = self.ctx.session
        if touched and (not session or not session.get("id")):
            raise SelectionRefused(
                "--touched: no session is active here, so there are no declared "
                "or changed files to read",
                "invariant session start <task-name>, or name the files with --files",
            )
        scope = {self._project_file(path) for path in files}
        if touched:
            scope.update(touched_paths(self.ctx))
        paths = tuple(sorted(scope))
        phases = guidelines.phases_for((phase,))
        ruleset = ruleset_or_refuse(self.ctx)
        if ruleset is None:
            return Selected(phase=phase, phases=phases, files=paths)
        chosen = guidelines.select(ruleset, (phase,), paths)
        source = guidelines_relpath(self.ctx, self.manifest.get("guidelines.ruleset"))
        return Selected(
            phase=phase, phases=phases, files=paths,
            text=guidelines.render_selected(
                ruleset, chosen, source=source, phases=phases, files=paths
            ),
            ruleset=ruleset.digest,
            rules=tuple(rule.id for rule in chosen),
        )

    def _project_file(self, path: str) -> str:
        """A named file, read against the working directory, project-relative."""
        rel = guidelines_relpath(self.ctx, str(Path(self.ctx.cwd) / path))
        if rel is None:
            raise SelectionRefused(
                f"--files {path}: names nothing inside the project",
                "name a file in this project, relative to the working directory",
            )
        return rel

    def baseline(
        self,
        *,
        mode: str,
        rules: tuple[str, ...] = (),
        dry_run: bool = False,
    ) -> Baselined:
        """Record or prune the ratchet baseline (ADR-012 D6), or with
        ``dry_run`` say what would be written.

        ``core.rules.plan_baseline`` decides the entries, checks the target
        and privacy-scans the text; this writes what it decided, into the
        tree in hand, and nothing else. The file carries no managed marker.
        """
        plan = plan_baseline(self.ctx, mode=mode, rules=rules)
        if plan is None:
            return Baselined(status="no-ruleset", written=False, mode=mode)
        if not dry_run:
            plan.path.parent.mkdir(parents=True, exist_ok=True)
            plan.path.write_text(plan.text, encoding="utf-8")
        return Baselined(
            status=plan.status, written=not dry_run, mode=mode, relpath=plan.relpath,
            ruleset=plan.ruleset, entries=len(plan.entries), changes=plan.changes,
        )

    def submit_verdicts(self, submission: Any) -> VerdictsRecorded:
        """Check a verdict submission and record it whole, or refuse it whole.

        ADR-012 D5 and the A4 rulings. ``submission`` is a decoded list of D5
        objects; ``guidelines verdict`` builds a list of one. The checks are
        ``core.rules.plan_verdicts``', the one plan the gate will ask too.
        Any problem in any entry raises :class:`VerdictRefused` naming every
        problem per entry, and nothing is recorded (DN-11, ruling 6). The
        session is the rule context's, resolved by working directory.
        """
        session = self.ctx.session
        if not session or not session.get("id"):
            raise VerdictRefused.precondition(
                "pipeline: no session is active here, so a verdict has no pipeline "
                "to enter; fix: invariant session start <task-name>"
            )
        if self.pipelines is None:
            raise VerdictRefused.precondition(
                "pipeline: this guidelines domain was built without the pipeline "
                "domain a verdict is recorded in; fix: submit through the CLI"
            )
        plan = plan_verdicts(self.ctx, submission)
        if not plan.ok:
            raise plan.refusal()
        token = new_submission_token()
        records = tuple(replace(record, token=token).record() for record in plan.records)
        self.pipelines.record_verdicts(str(session["id"]), records)
        return VerdictsRecorded(
            records=records, ruleset=plan.ruleset, state=plan.state, token=token
        )

    def verdict_standings(self) -> StandingsRead:
        """How each judgement guideline the change owes a verdict stands, as
        ``guidelines.verdicts_complete`` sees it (A4 increment 4). Writes nothing.

        The read is ``core.rules.read_standings``, the gate's own, so the two
        cannot disagree. The session is the rule context's, resolved by
        working directory as for a submission. Where the gate fails with no
        standings to show, this raises :class:`StandingsRefused` carrying the
        gate's message and fixes.
        """
        session = self.ctx.session
        if not session or not session.get("id"):
            raise StandingsRefused(
                "no session is active here, so there is no pipeline to read",
                fixes=("invariant session start <task-name>",),
            )
        read = read_standings(self.ctx)
        if read.failure is not None:
            raise StandingsRefused(
                read.failure.message, read.failure.detail, read.failure.fixes
            )
        return read


@dataclass(frozen=True)
class Baselined:
    """What one ``guidelines baseline`` did, or with ``--dry-run`` would do."""

    #: ``new``, ``current``, ``changed``, or ``no-ruleset``.
    status: str
    written: bool
    #: ``record`` or ``prune``.
    mode: str
    relpath: str | None = None
    #: The ruleset digest the findings were evaluated under.
    ruleset: str | None = None
    #: How many entries the file holds after the write.
    entries: int = 0
    #: Each entry lowered or dropped, and why.
    changes: tuple[BaselineChange, ...] = ()
