"""The guidelines domain: the effects the ruleset has on the tree (ADR-012 D12).

Not an FSM, like ``stats`` and ``sweep``: there is no lifecycle to advance.
It owns two effects. Rendering: the human checklist (A2 increment 3), the
managed ``guidelines/index`` block in each instruction file (increment 4), and
in a sphinx-needs docs project the rules as needs, ``<srcdir>/rules.rst``
(ADR-018 D12), where the checklist itself moves beside it under the mandate.
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

And it writes the user's rulings and the adopted defaults (ADR-032 D7, D8,
increment 2; DEC_ADR_032_I2): ``plan_except`` and ``plan_loosen`` plan a ruled
exception or lowering through ``core/guidelines``' line edits, and
``write_edit`` writes one once the CLI has the user's confirmation at a
terminal; ``plan_adopt`` and ``write_adoption`` are ``guidelines adopt``'s, and
``fresh_defaults`` is the default set's files as a project with no ruleset
gets them, which the docs starter writes too, so ``init`` and ``adopt`` share
one writer.
"""

from __future__ import annotations

import posixpath
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from ..core import guidelines
from ..core import needs as needs_reader
from ..core.errors import InvariantError
from ..core.manifest import project_relative
from ..core.rules import (
    BaselineChange,
    BlockRendered,
    RuleContext,
    StandingsRead,
    StandingsRefused,
    VerdictRefused,
    all_rules,
    guidelines_relpath,
    guidelines_tree,
    plan_baseline,
    plan_render,
    plan_verdicts,
    policy_relpath,
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
    #: The rules as needs, ``<srcdir>/rules.rst`` (ADR-018 D12): its path and
    #: state, both ``None`` where the docs are no docs project.
    rules_relpath: str | None = None
    rules_status: str | None = None
    #: The pattern catalogue's page (ADR-032 D5), where the ruleset names one.
    patterns_relpath: str | None = None
    patterns_status: str | None = None


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


class DefaultsRefused(InvariantError):
    """The defaults could not be merged into the project's ruleset (exit 1),
    with nothing written. Names the fix."""

    exit_code = 1

    def __init__(self, message: str, fix: str) -> None:
        super().__init__(f"{message}\n  {fix}")
        self.reason = message
        self.fix = fix


@dataclass(frozen=True)
class DefaultsCopied:
    """What one defaults merge wrote, or with ``dry_run`` would have written."""

    written: bool
    #: The ruleset's path in the policy tree.
    relpath: str
    merge: guidelines.DefaultsMerged
    #: The catalogue the merged ruleset names, and whether it was copied
    #: there (only where no file was there already).
    catalogue_relpath: str | None = None
    catalogue_written: bool = False


# -- the user's edits and the adopt writer (ADR-032 increment 2) --------------------------


class EditRefused(InvariantError):
    """A ruled exception or lowering cannot be planned or written (exit 2),
    with nothing written. Names the fix."""

    exit_code = 2

    def __init__(self, message: str, fix: str) -> None:
        super().__init__(f"{message}\n  {fix}")
        self.reason = message
        self.fix = fix


@dataclass(frozen=True)
class RulingEdit:
    """One planned edit of the ruleset that records the user's ruling
    (``guidelines except``, ``guidelines loosen``; ADR-032 D7)."""

    #: What the command does, for its first line.
    what: str
    relpath: str
    path: Path
    #: The ruleset as the plan read it; the write refuses a file changed since.
    before: str
    #: The edited ruleset, or ``None`` where the line editor cannot make the
    #: edit in place and ``entry`` is added by hand.
    after: str | None
    #: The lines written, as they would be added by hand.
    entry: str
    #: Why ``after`` is ``None``.
    why: str = ""


#: The baseline file ``adopt`` names where the project configures none.
BASELINE_NAME = "baseline.txt"


@dataclass(frozen=True)
class FreshDefaults:
    """The default set's files as a project with no ruleset gets them
    (ADR-032 D8): one text for ``init``'s starter and ``guidelines adopt``."""

    text: str
    catalogue_rel: str | None
    catalogue_text: str | None
    parsed: guidelines.Ruleset


def fresh_defaults(
    defaults: str,
    catalogue: str,
    *,
    location: str,
    layers: Mapping[str, Sequence[str]] | None = None,
) -> FreshDefaults:
    """The adopt writer's text for a project with no ruleset: ``defaults``
    (``domains/docs.default_ruleset``) with the map ``layers`` fills and its
    origins stamped (``core/guidelines.fill_defaults``), the catalogue named
    beside ``location``, and the ruleset parsed against the registry with that
    catalogue. The docs starter writes exactly this (``DOCSD --> GDOM``).
    Refused (:class:`DefaultsRefused`) where the fill or the parse fails.
    """
    try:
        text = guidelines.fill_defaults(defaults, layers)
    except guidelines.RulesetError as exc:
        raise DefaultsRefused("; ".join(exc.problems), "check the --layer ids") from exc
    named = (yaml.safe_load(text) or {}).get("catalogue")
    catalogue_rel = posixpath.join(posixpath.dirname(location), named) if named else None
    try:
        parsed = guidelines.parse(
            text, registered=[spec.id for spec in all_rules()], location=location,
            read=lambda rel: catalogue.encode("utf-8") if rel == catalogue_rel else None,
        )
    except guidelines.RulesetError as exc:
        raise DefaultsRefused(
            f"the default set does not validate: {'; '.join(exc.problems)}",
            "report it: the shipped defaults should always validate",
        ) from exc
    return FreshDefaults(
        text=text, catalogue_rel=catalogue_rel,
        catalogue_text=catalogue if catalogue_rel else None, parsed=parsed,
    )


@dataclass(frozen=True)
class Adoption:
    """What ``guidelines adopt`` would do (ADR-032 D8, DEC_ADR_032_I2)."""

    #: ``new`` (no ruleset: the defaults written), ``merge`` (added to or
    #: updated in the project's), or ``nothing``.
    status: str
    relpath: str
    #: The ruleset as the plan read it (``None`` where there was none) and
    #: as it will be written (``None`` for ``nothing``).
    before: str | None
    text: str | None
    #: The catalogue copied beside the ruleset, only where none is there.
    catalogue_rel: str | None = None
    catalogue_text: str | None = None
    sections: tuple[str, ...] = ()
    rules: tuple[str, ...] = ()
    layers: bool = False
    #: The map written maps no files: ``--layer`` fills it.
    unmapped: bool = False
    standings: tuple[guidelines.DefaultStanding, ...] = ()
    updated: tuple[str, ...] = ()
    #: The manifest keys the CLI writes at the project's own level.
    settings: Mapping[str, Any] = field(default_factory=dict)
    #: The rules ``baseline --record`` records after the write: ``None`` for
    #: the whole file, empty for no record.
    baseline_rules: tuple[str, ...] | None = ()


def _need_words(by: str, reason: str) -> None:
    """A ruling names who gave it and why (ADR-021 D6's shape)."""
    if not by.strip() or not reason.strip():
        raise EditRefused(
            "a ruling needs --by and --reason, each with some text",
            "name who rules and why",
        )


def _beside(rel: str, name: str) -> str:
    """``name`` in the folder ``rel`` lies in."""
    return posixpath.join(posixpath.dirname(rel), name)


_FINGERPRINT = re.compile(r"[0-9a-f]{12}")


class GuidelinesDomain:
    def __init__(
        self,
        ctx: RuleContext,
        pipelines: PipelineDomain | None = None,
        admit: Callable[[Sequence[Path]], None] | None = None,
    ) -> None:
        #: The rule context carries the host adapter's instruction files,
        #: the default of ``guidelines.instruction_files``; the CLI supplies
        #: them, because no domain imports a host.
        self.ctx = ctx
        self.manifest = ctx.manifest
        #: Where an accepted verdict is recorded (``GDOM --> PIPELINE``, on
        #: the ``stats --> session`` precedent). Only the submission needs it.
        self.pipelines = pipelines
        #: Asked with every path a write is about to make, before the first
        #: one; raises to refuse them all. The CLI hands in the session
        #: domain's docs-lock check (ADR-014 D5, increment 4c-2), so a write
        #: into a store's policy takes its unit on access or is refused
        #: naming ``docs lock``; ``None`` admits every path.
        self.admit = admit

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
            self._admit(
                plan.path,
                *(page.path for page in (plan.rules, plan.patterns) if page is not None),
                *(block.path for block in plan.blocks if block.text is not None),
            )
            plan.path.parent.mkdir(parents=True, exist_ok=True)
            plan.path.write_text(plan.text, encoding="utf-8")
            for page in (plan.rules, plan.patterns):
                if page is not None:
                    page.path.write_text(page.text, encoding="utf-8")
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
            rules_relpath=None if plan.rules is None else plan.rules.relpath,
            rules_status=None if plan.rules is None else plan.rules.status,
            patterns_relpath=None if plan.patterns is None else plan.patterns.relpath,
            patterns_status=None if plan.patterns is None else plan.patterns.status,
        )

    def show(self) -> Shown:
        """The checklist text, in markdown, without its marker. Writes nothing.

        Markdown whatever ``docs.format`` says: it is read by an agent or a
        terminal, not built into the project's docs.
        """
        ruleset = ruleset_or_refuse(self.ctx)
        if ruleset is None:
            return Shown(text=None)
        source = policy_relpath(self.ctx, self.manifest.get("guidelines.ruleset"))
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
        source = policy_relpath(self.ctx, self.manifest.get("guidelines.ruleset"))
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
            self._admit(plan.path)
            plan.path.parent.mkdir(parents=True, exist_ok=True)
            plan.path.write_text(plan.text, encoding="utf-8")
        return Baselined(
            status=plan.status, written=not dry_run, mode=mode, relpath=plan.relpath,
            ruleset=plan.ruleset, entries=len(plan.entries), changes=plan.changes,
        )

    def merge_defaults(
        self,
        defaults: str,
        *,
        catalogue: str | None,
        layers: Mapping[str, Sequence[str]] | None = None,
        dry_run: bool = False,
    ) -> DefaultsCopied:
        """Merge the default set into the configured ruleset (ADR-032
        increment 1b, DEC_ADR_032_I1B), the seed of increment 2's adopt
        writer.

        ``defaults`` is the default set's text and ``catalogue`` the shipped
        catalogue's, both handed in by the caller (``domains/docs``'
        ``default_ruleset``), so this domain imports no other. The merge is
        ``core/guidelines``' ``merge_defaults``, with ``layers`` filling the
        map's ``paths``. The result is validated against the rule registry,
        with the catalogue it names read from the tree or, where none is
        there, from ``catalogue``; then every path is admitted, the ruleset
        written, and the catalogue copied beside it only where none was.
        Every refusal is :class:`DefaultsRefused`, before anything is written.
        """
        configured = self.manifest.get("guidelines.ruleset")
        if not configured:
            raise DefaultsRefused(
                "guidelines.ruleset is not set, so there is no ruleset to merge the "
                "defaults into",
                "set guidelines.ruleset to the project's ruleset",
            )
        rel = policy_relpath(self.ctx, configured)
        if rel is None:
            raise DefaultsRefused(
                f"guidelines.ruleset {configured!r} names nothing inside the project",
                "set guidelines.ruleset to a project-relative path",
            )
        tree = guidelines_tree(self.ctx)
        path = tree / rel
        if not path.is_file():
            raise DefaultsRefused(
                f"the configured guidelines ruleset {rel} does not exist",
                f"create {rel}, or clear guidelines.ruleset",
            )
        try:
            merge = guidelines.merge_defaults(
                path.read_text(encoding="utf-8"), defaults, layers=layers
            )
        except guidelines.RulesetError as exc:
            raise DefaultsRefused("; ".join(exc.problems), f"fix {rel}, then merge again") from exc
        named = yaml.safe_load(merge.text).get("catalogue")
        catalogue_rel = None if not named else posixpath.join(posixpath.dirname(rel), named)
        copied = (
            catalogue_rel is not None and catalogue is not None
            and not (tree / catalogue_rel).exists()
        )

        def read(wanted: str) -> bytes | None:
            if copied and wanted == catalogue_rel:
                return catalogue.encode("utf-8")
            target = tree / wanted
            return target.read_bytes() if target.is_file() else None

        try:
            guidelines.parse(
                merge.text, registered=[spec.id for spec in all_rules()],
                read=read, location=rel,
            )
        except guidelines.RulesetError as exc:
            raise DefaultsRefused(
                f"the merged ruleset does not validate: {'; '.join(exc.problems)}",
                "supply the catalogue the defaults name, or fix the project's ruleset",
            ) from exc
        if not dry_run:
            catalogue_path = None if not copied else tree / catalogue_rel
            self._admit(path, *([] if catalogue_path is None else [catalogue_path]))
            path.write_text(merge.text, encoding="utf-8")
            if catalogue_path is not None:
                catalogue_path.parent.mkdir(parents=True, exist_ok=True)
                catalogue_path.write_text(catalogue, encoding="utf-8")
        return DefaultsCopied(
            written=not dry_run, relpath=rel, merge=merge,
            catalogue_relpath=catalogue_rel, catalogue_written=copied and not dry_run,
        )

    # -- the user's rulings (ADR-032 D7, increment 2) -----------------------------------

    def plan_except(
        self,
        rule_id: str,
        path: str,
        *,
        fingerprint: str | None = None,
        by: str,
        reason: str,
        today: date,
    ) -> RulingEdit:
        """The ruled exception ``guidelines except`` writes (ADR-032 D7): on
        a declared rule its ``exceptions``, on a derived rule its layer's.
        Nothing is written; :meth:`write_edit` writes it once the CLI has
        the user's confirmation. Every refusal is :class:`EditRefused`."""
        ruleset, text, rel, target = self._ruled_source()
        rule = ruleset.any_rule(rule_id)
        if rule is None:
            raise EditRefused(
                f"no guideline {rule_id!r} in the ruleset",
                "'invariant guidelines show' lists the guidelines",
            )
        if rule.check.kind == "engine":
            raise EditRefused(
                f"{rule_id} is enforced by the rule {rule.check.rule}, which no "
                f"exception reaches",
                f"invariant override {rule.check.rule} --reason '<why>'",
            )
        spelled = project_relative(path)
        if spelled in (None, "."):
            raise EditRefused(
                f"{path!r} is not a project-relative path or glob",
                "name the file or glob relative to the project root",
            )
        if fingerprint is not None and not _FINGERPRINT.fullmatch(fingerprint):
            raise EditRefused(
                f"--fingerprint {fingerprint!r} is not twelve lowercase hex digits",
                "copy it from: invariant check guidelines.mechanical_clean --json",
            )
        _need_words(by, reason)
        if any(e.path == spelled and e.fingerprint == fingerprint for e in rule.exceptions):
            raise EditRefused(
                f"{rule_id} already carries an exception for {spelled}"
                + (f" ({fingerprint})" if fingerprint else ""),
                "nothing to record",
            )
        exception = {
            "path": spelled, "reason": reason, "fingerprint": fingerprint,
            "ruled": {"by": by, "date": today.isoformat(), "reason": reason},
        }
        where = f"the layer {rule.layer}'s exceptions" if rule.layer else f"{rule_id}'s exceptions"
        try:
            edit = guidelines.add_exception(
                text, exception,
                rule=None if rule.layer else rule_id, layer=rule.layer,
            )
        except guidelines.EditUnwritable as exc:
            return RulingEdit(
                what=f"except {rule_id} {spelled}, under {where}", relpath=rel, path=target,
                before=text, after=None, entry=exc.entry, why="; ".join(exc.problems),
            )
        except guidelines.RulesetError as exc:
            raise EditRefused("; ".join(exc.problems), f"edit {rel} by hand") from exc
        self._validate(edit.text, rel)
        return RulingEdit(
            what=f"except {rule_id} {spelled}, under {where}", relpath=rel, path=target,
            before=text, after=edit.text, entry=edit.entry,
        )

    def plan_loosen(
        self, target: str, *, to: str, by: str, reason: str, today: date
    ) -> RulingEdit:
        """The ruled lowering ``guidelines loosen`` writes (ADR-032 D7): a
        declared rule, or ``layers`` for the map, to ``warn`` or ``off`` (paused,
        ruling 13), with its ``ruled``. A derived rule's severity is the map's,
        so its id is refused naming ``layers``. Every refusal is
        :class:`EditRefused`."""
        if to not in ("warn", guidelines.OFF):
            raise EditRefused(f"--to {to} is not a level a guideline is lowered to",
                              "--to warn, or --to off to pause it")
        ruleset, text, rel, path = self._ruled_source()
        _need_words(by, reason)
        if target == guidelines.LAYERS_KEY:
            if ruleset.layers is None:
                raise EditRefused("the ruleset has no layer map", "nothing to loosen")
            severity, rule = ruleset.layers.severity, None
        else:
            found = ruleset.any_rule(target)
            if found is None:
                raise EditRefused(
                    f"no guideline {target!r} in the ruleset",
                    "'invariant guidelines show' lists the guidelines",
                )
            if found.layer is not None:
                raise EditRefused(
                    f"{target} is derived from the layer map, whose severity every "
                    f"derived rule takes",
                    "lower the map: invariant guidelines loosen layers --to warn "
                    "--by <who> --reason <why>",
                )
            severity, rule = found.severity, target
        rank = guidelines.SEVERITY_RANK
        if rank.get(severity, 0) <= rank[to]:
            raise EditRefused(
                f"{target} is already at {severity}, not above {to}; raising a severity "
                f"needs nothing",
                "nothing to record",
            )
        ruled = {"by": by, "date": today.isoformat(), "reason": reason}
        what = f"loosen {target} from {severity} to {to}"
        try:
            edit = guidelines.lower_severity(text, ruled, rule=rule, to=to)
        except guidelines.EditUnwritable as exc:
            return RulingEdit(
                what=what, relpath=rel, path=path, before=text, after=None,
                entry=exc.entry, why="; ".join(exc.problems),
            )
        except guidelines.RulesetError as exc:
            raise EditRefused("; ".join(exc.problems), f"edit {rel} by hand") from exc
        self._validate(edit.text, rel)
        return RulingEdit(
            what=what, relpath=rel, path=path, before=text, after=edit.text, entry=edit.entry
        )

    def plan_retire(self, rule_id: str, *, by: str, reason: str, today: date) -> RulingEdit:
        """The rule ``guidelines retire`` takes out for good (ADR-032 ruling
        13): its lines removed and ``{id, ruled}`` appended to ``retired``. A
        derived rule's id is refused naming the map's route. Every refusal is
        :class:`EditRefused`."""
        ruleset, text, rel, path = self._ruled_source()
        _need_words(by, reason)
        rule = ruleset.any_rule(rule_id)
        if rule is None:
            raise EditRefused(
                f"no guideline {rule_id!r} in the ruleset",
                "'invariant guidelines show' lists the guidelines",
            )
        if rule.layer is not None:
            raise EditRefused(
                f"{rule_id} is derived from the layer map, so it is not retired on its own",
                "pause the map: invariant guidelines loosen layers --to off --by <who> "
                "--reason <why>",
            )
        ruled = {"by": by, "date": today.isoformat(), "reason": reason}
        what = f"retire {rule_id}, into retired"
        try:
            edit = guidelines.retire_rule(text, rule_id, ruled)
        except guidelines.EditUnwritable as exc:
            return RulingEdit(
                what=what, relpath=rel, path=path, before=text, after=None,
                entry=exc.entry, why="; ".join(exc.problems),
            )
        except guidelines.RulesetError as exc:
            raise EditRefused("; ".join(exc.problems), f"edit {rel} by hand") from exc
        self._validate(edit.text, rel)
        return RulingEdit(
            what=what, relpath=rel, path=path, before=text, after=edit.text, entry=edit.entry
        )

    def write_edit(self, edit: RulingEdit) -> None:
        """Write a planned ruling, only while the ruleset is still the one the
        plan read, admitted first (the docs lock in a store)."""
        if edit.after is None:
            raise EditRefused(
                f"{edit.relpath} cannot be edited in place: {edit.why}",
                "add the entry by hand",
            )
        self._unchanged(edit.path, edit.before, edit.relpath)
        self._admit(edit.path)
        edit.path.write_text(edit.after, encoding="utf-8")

    def _ruled_source(self) -> tuple[guidelines.Ruleset, str, str, Path]:
        """The ruleset, its text, its policy-tree path and its file."""
        try:
            ruleset = ruleset_or_refuse(self.ctx)
        except InvariantError as exc:
            raise EditRefused(str(exc).splitlines()[0], "fix the ruleset, then try again") from exc
        if ruleset is None:
            raise EditRefused(
                "no guidelines ruleset is configured, so there is no rule to rule on",
                "invariant guidelines adopt",
            )
        rel = policy_relpath(self.ctx, self.manifest.get("guidelines.ruleset"))
        path = guidelines_tree(self.ctx) / str(rel)
        return ruleset, path.read_text(encoding="utf-8"), str(rel), path

    def _validate(self, text: str, rel: str) -> None:
        tree = guidelines_tree(self.ctx)

        def read(wanted: str) -> bytes | None:
            target = tree / wanted
            return target.read_bytes() if target.is_file() else None

        try:
            guidelines.parse(
                text, registered=[spec.id for spec in all_rules()], read=read, location=rel
            )
        except guidelines.RulesetError as exc:
            raise EditRefused(
                f"the edited ruleset does not validate: {'; '.join(exc.problems)}",
                f"edit {rel} by hand",
            ) from exc

    @staticmethod
    def _unchanged(path: Path, before: str | None, rel: str) -> None:
        now = path.read_text(encoding="utf-8") if path.is_file() else None
        if now != before:
            raise EditRefused(
                f"{rel} changed since the plan was made, so it was not written over",
                "run the command again",
            )

    # -- guidelines adopt (ADR-032 D8, increment 2) -------------------------------------

    def plan_adopt(
        self,
        defaults: str,
        catalogue: str,
        *,
        layers: Mapping[str, Sequence[str]] | None = None,
        update: Sequence[str] = (),
    ) -> Adoption:
        """What ``guidelines adopt`` would write (ADR-032 D8): with no ruleset,
        :func:`fresh_defaults` at ADR-020 D10's place; with one,
        :meth:`merge_defaults`' merge, each default's standing, and each
        ``update`` (an updatable rule) replaced by the shipped one. Writes
        nothing. Every refusal is :class:`DefaultsRefused`."""
        if not self.manifest.get("guidelines.ruleset"):
            return self._plan_fresh(defaults, catalogue, layers, update)
        copied = self.merge_defaults(defaults, catalogue=catalogue, layers=layers, dry_run=True)
        merge = copied.merge
        tree = guidelines_tree(self.ctx)
        before = (tree / copied.relpath).read_text(encoding="utf-8")
        standings = guidelines.default_standings(before, defaults)
        states = {s.id: s.standing for s in standings}
        text = merge.text
        for rule_id in update:
            state = states.get(rule_id, "not a default this ruleset holds with an origin")
            if state != guidelines.UPDATABLE_DEFAULT:
                raise DefaultsRefused(
                    f"--update {rule_id}: it is {state}, and only an updatable default "
                    f"is replaced",
                    "drop --update for it; a diverged rule is the project's to change",
                )
            text = guidelines.replace_default(text, defaults, rule_id)
        if update:
            self._validate_adopted(text, copied.relpath, catalogue, copied.catalogue_relpath)
        catalogue_new = (
            copied.catalogue_relpath is not None
            and not (tree / copied.catalogue_relpath).exists()
        )
        changed = text != before or catalogue_new
        parsed = guidelines.parse(
            text, registered=[spec.id for spec in all_rules()], location=copied.relpath,
            read=lambda rel: (
                catalogue.encode("utf-8") if rel == copied.catalogue_relpath and catalogue_new
                else (tree / rel).read_bytes() if (tree / rel).is_file() else None
            ),
        )
        added = set(merge.rules)
        recorded = tuple(
            rule.id for rule in parsed.rules
            if rule.check.kind in guidelines.BASELINE_KINDS
            and (rule.id in added or (merge.layers and rule.layer is not None))
        )
        settings: dict[str, Any] = {}
        if recorded and not self.manifest.get("guidelines.baseline"):
            settings = {"guidelines": {"baseline": _beside(copied.relpath, BASELINE_NAME)}}
        return Adoption(
            status="merge" if changed else "nothing",
            relpath=copied.relpath, before=before, text=text if changed else None,
            catalogue_rel=copied.catalogue_relpath if catalogue_new else None,
            catalogue_text=catalogue if catalogue_new else None,
            sections=merge.sections, rules=merge.rules, layers=merge.layers,
            unmapped=merge.layers and not layers,
            standings=standings, updated=tuple(update), settings=settings,
            baseline_rules=recorded,
        )

    def _plan_fresh(
        self,
        defaults: str,
        catalogue: str,
        layers: Mapping[str, Sequence[str]] | None,
        update: Sequence[str],
    ) -> Adoption:
        if update:
            raise DefaultsRefused(
                "--update replaces a default a ruleset holds, and no ruleset is configured",
                "drop --update",
            )
        location = guidelines.ruleset_location(needs_reader.docs_folder(self.manifest))
        tree = guidelines_tree(self.ctx)
        if (tree / location).exists():
            raise DefaultsRefused(
                f"{location} exists and guidelines.ruleset is not set, so it is not "
                f"written over",
                f"set guidelines.ruleset to {location}, then adopt again",
            )
        fresh = fresh_defaults(defaults, catalogue, location=location, layers=layers)
        settings: dict[str, Any] = {"ruleset": location}
        if not self.manifest.get("guidelines.baseline"):
            settings["baseline"] = _beside(location, BASELINE_NAME)
        catalogue_new = fresh.catalogue_rel is not None and not (tree / fresh.catalogue_rel).exists()
        mapped = fresh.parsed.layers is not None and any(
            layer.paths for layer in fresh.parsed.layers.layers
        )
        return Adoption(
            status="new", relpath=location, before=None, text=fresh.text,
            catalogue_rel=fresh.catalogue_rel if catalogue_new else None,
            catalogue_text=fresh.catalogue_text if catalogue_new else None,
            sections=tuple(s.id for s in fresh.parsed.sections),
            rules=tuple(r.id for r in fresh.parsed.rules if r.layer is None),
            layers=fresh.parsed.layers is not None,
            unmapped=fresh.parsed.layers is not None and not mapped,
            settings={"guidelines": settings}, baseline_rules=None,
        )

    def _validate_adopted(
        self, text: str, rel: str, catalogue: str, catalogue_rel: str | None
    ) -> None:
        tree = guidelines_tree(self.ctx)

        def read(wanted: str) -> bytes | None:
            target = tree / wanted
            if target.is_file():
                return target.read_bytes()
            return catalogue.encode("utf-8") if wanted == catalogue_rel else None

        try:
            guidelines.parse(
                text, registered=[spec.id for spec in all_rules()], read=read, location=rel
            )
        except guidelines.RulesetError as exc:
            raise DefaultsRefused(
                f"the updated ruleset does not validate: {'; '.join(exc.problems)}",
                f"fix {rel}, then adopt again",
            ) from exc

    def write_adoption(self, adoption: Adoption) -> list[Path]:
        """Write the ruleset and the catalogue an adoption planned, only while
        the ruleset is still the one the plan read; the paths written. The
        settings, the baseline and the render are the CLI's to sequence."""
        if adoption.text is None:
            return []
        tree = guidelines_tree(self.ctx)
        path = tree / adoption.relpath
        self._unchanged(path, adoption.before, adoption.relpath)
        catalogue = (
            None if adoption.catalogue_rel is None or adoption.catalogue_text is None
            else tree / adoption.catalogue_rel
        )
        self._admit(path, *([] if catalogue is None else [catalogue]))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(adoption.text, encoding="utf-8")
        written = [path]
        if catalogue is not None and not catalogue.exists():
            catalogue.parent.mkdir(parents=True, exist_ok=True)
            catalogue.write_text(str(adoption.catalogue_text), encoding="utf-8")
            written.append(catalogue)
        return written

    def default_standings(self, defaults: str) -> tuple[guidelines.DefaultStanding, ...]:
        """Each default the configured ruleset holds, and how it stands, for
        ``doctor``; empty where no ruleset is configured or it cannot be read."""
        rel = policy_relpath(self.ctx, self.manifest.get("guidelines.ruleset"))
        if rel is None:
            return ()
        path = guidelines_tree(self.ctx) / rel
        try:
            return guidelines.default_standings(path.read_text(encoding="utf-8"), defaults)
        except (OSError, UnicodeDecodeError, guidelines.RulesetError):
            return ()

    def _admit(self, *paths: Path) -> None:
        """Every path a write is about to make, admitted before the first."""
        if self.admit is not None:
            self.admit(paths)

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
