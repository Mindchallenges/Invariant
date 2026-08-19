"""The rule registry.

A rule is a function over a :class:`RuleContext` returning a :class:`Verdict`.
Rules do not know their own enforcement level: level is applied to the verdict
afterwards, from the manifest. That separation is what lets a project retune
policy without forking the library, and it is why the same rule can block in
one project and advise in another with no code change.

Rules read **recorded evidence** rather than re-deriving facts. "Did the test
suite run and fail before the fix" is answered from what the pipeline recorded
when the suite actually ran, not from a fresh guess. A gate that re-derives is
a gate that can be satisfied by accident.
"""

from __future__ import annotations

import codecs
import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..adapters import Adapter, get_adapter
from . import git
from .errors import InvariantError
from .items import backend_for
from .machines import load_item_machine, load_pipeline_machine, load_session_machine
from .manifest import Level, Manifest
from .privacy import EXEMPTION_MARKER, PrivacyScanner

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

#: The normalised hook-payload keys layers above ``hosts/`` are permitted to
#: read. This is the whole contract, and it lives here because ``hosts/``
#: implement the protocols ``core/`` defines.
#:
#: ``overview.md`` used to state a stronger rule — "Nothing above ``hosts/`` is
#: aware of hook schemas" — which the code had already departed from, and which
#: BUG_010's fix then came to DEPEND on departing from: attributing a hook's
#: evidence to the right project needs the path the call itself names, and that
#: is in the payload. A rule the codebase does not keep is worse than a weaker
#: one it does, because a reader enforcing it would "correct" the working fix
#: by reverting it (BUG_022).
#:
#: So the seam is drawn where the code actually holds it: hosts own the hook
#: PROTOCOL — wire format, response shape, settings wiring — and hand upward a
#: dict spelled in these keys. Nothing above reads anything else, and a host
#: adapter for a different agent host has an explicit normalisation obligation
#: rather than an implied one. Widening this tuple is a decision about the
#: layer contract, not a convenience.
NORMALISED_PAYLOAD_KEYS = (
    "tool_name",
    "tool_input",
    "tool_input.file_path",
    "tool_input.notebook_path",
    "stop_hook_active",
)

#: The terminal state that means "gave up", as opposed to "finished".
#:
#: Every shipped machine spells it this way. It is named rather than derived
#: because a machine records only THAT a state is terminal, not whether
#: arriving there was the point — and a rule about unfinished work has to tell
#: those apart.
ABANDONED = "abandoned"


def _terminal_states(loader, manifest, machine_id: str | None = None) -> frozenset[str] | None:
    """A machine's terminal states, read from the machine itself.

    Copying the list into this module would let the two drift, and the copy
    would be the one nobody updates when a machine gains a state.

    ``None`` means the machine could not be loaded — a configuration error,
    not an empty answer. This used to return ``frozenset()``, which callers
    read as "no state is terminal": every state looked mid-flight, and the
    rules asking went silent exactly when the configuration was broken
    (AUD-08). A caller must fail on ``None``, not relax.

    ``machine_id`` pins the load to a specific machine where the caller has
    one recorded — a pipeline's persisted methodology (AUD-02/03).
    """
    try:
        machine = loader(manifest, machine_id) if machine_id else loader(manifest)
        return machine.terminal
    except InvariantError:
        return None


# -- result types ------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    ok: bool
    message: str = ""
    detail: str = ""
    fixes: tuple[str, ...] = ()
    #: True when the rule does not apply here at all. Distinct from a pass:
    #: "nothing to check" and "checked and clean" are different facts, and
    #: conflating them is how a gate comes to look green while inert.
    skipped: bool = False

    @classmethod
    def clean(cls, message: str = "", detail: str = "") -> "Verdict":
        return cls(ok=True, message=message, detail=detail)

    @classmethod
    def fail(
        cls, message: str, detail: str = "", fixes: Iterable[str] = ()
    ) -> "Verdict":
        return cls(ok=False, message=message, detail=detail, fixes=tuple(fixes))

    @classmethod
    def na(cls, message: str = "not applicable here", detail: str = "") -> "Verdict":
        return cls(ok=True, message=message, detail=detail, skipped=True)


@dataclass(frozen=True)
class RuleSpec:
    id: str
    summary: str
    events: tuple[str, ...]
    fn: Callable[["RuleContext"], Verdict]


@dataclass(frozen=True)
class Evaluated:
    rule_id: str
    level: Level
    verdict: Verdict

    @property
    def blocks(self) -> bool:
        return (
            self.level is Level.BLOCK
            and not self.verdict.ok
            and not self.verdict.skipped
        )

    @property
    def warns(self) -> bool:
        return (
            self.level is Level.WARN
            and not self.verdict.ok
            and not self.verdict.skipped
        )

    @property
    def skipped_blocking(self) -> bool:
        """A gate the project chose to ENFORCE did not run.

        `Verdict.skipped` is a property of the rule's reasoning and carries no
        level, so on its own it cannot separate "an advisory gate did not
        apply" from "a gate this project blocks on did not apply". Only the
        second is worth a reader's attention on the default path, and the
        delivery layer is the one that owns that distinction.
        """
        return self.level is Level.BLOCK and self.verdict.skipped

    def render(self) -> str:
        """One line per verdict, led by what HAPPENED.

        The marker used to be the rule's configured *level*, so a passing rule
        in a strict project rendered as ``[BLOCK] coverage.meets_floor: all 3
        file(s) at or above 90%`` — a pass that reads as a failure at a glance,
        and was misread as one. The level is a property of the rule; the
        verdict is what the reader came for. The level still leads on a
        failure, where it is the part that decides whether work stops.
        """
        if self.verdict.skipped:
            # The level rides along on a blocking skip for the same reason it
            # leads on a failure: it is the part that tells the reader whether
            # something they rely on just stopped applying.
            marker = "skip:BLOCK" if self.skipped_blocking else "skip"
        elif self.verdict.ok:
            marker = "ok"
        else:
            marker = {Level.BLOCK: "BLOCK", Level.WARN: "warn", Level.OFF: "off"}[
                self.level
            ]
        lines = [f"[{marker}] {self.rule_id}: {self.verdict.message}"]
        if self.verdict.detail:
            lines.append(f"        {self.verdict.detail}")
        for fix in self.verdict.fixes:
            lines.append(f"        try: {fix}")
        return "\n".join(lines)


@dataclass
class RuleContext:
    """Everything a rule may read. Rules never reach outside this."""

    manifest: Manifest
    cwd: Path
    session: dict | None = None
    pipeline: dict | None = None
    payload: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    #: Live sessions registered here when the directory could not pick one.
    #: A rule that sees ``session is None`` must consult this before advising
    #: the caller to start a session: with candidates present, the answer is
    #: to name one of them, and telling the caller to start another makes the
    #: ambiguity permanently worse.
    ambiguous: list[dict] = field(default_factory=list)

    # -- convenience --------------------------------------------------

    @property
    def tool_name(self) -> str:
        return str(self.payload.get("tool_name", ""))

    @property
    def tool_input(self) -> Mapping[str, Any]:
        value = self.payload.get("tool_input")
        return value if isinstance(value, Mapping) else {}

    @property
    def is_edit(self) -> bool:
        return self.tool_name in EDIT_TOOLS

    @property
    def target_path(self) -> Path | None:
        raw = self.tool_input.get("file_path") or self.tool_input.get("notebook_path")
        return Path(str(raw)) if raw else None

    def evidence(self, key: str, default: Any = None) -> Any:
        if not self.pipeline:
            return default
        return (self.pipeline.get("evidence") or {}).get(key, default)


# -- registry ----------------------------------------------------------

_REGISTRY: dict[str, RuleSpec] = {}


def rule(rule_id: str, *, summary: str, events: Iterable[str] = ()) -> Callable:
    def decorate(fn: Callable[[RuleContext], Verdict]) -> Callable:
        _REGISTRY[rule_id] = RuleSpec(
            id=rule_id, summary=summary, events=tuple(events), fn=fn
        )
        return fn

    return decorate


def all_rules() -> list[RuleSpec]:
    return [_REGISTRY[k] for k in sorted(_REGISTRY)]


def get_rule(rule_id: str) -> RuleSpec | None:
    return _REGISTRY.get(rule_id)


def rules_for_event(event: str) -> list[RuleSpec]:
    return [spec for spec in all_rules() if event in spec.events]


def evaluate(rule_ids: Iterable[str], ctx: RuleContext) -> list[Evaluated]:
    results: list[Evaluated] = []
    for rule_id in rule_ids:
        spec = _REGISTRY.get(rule_id)
        level = ctx.manifest.rule_level(rule_id)
        if spec is None:
            results.append(
                Evaluated(
                    rule_id,
                    level,
                    Verdict.fail(
                        f"no rule registered with id {rule_id!r}",
                        detail="A gate naming a rule that does not exist would "
                        "otherwise pass silently forever.",
                    ),
                )
            )
            continue
        if level is Level.OFF:
            results.append(Evaluated(rule_id, level, Verdict.na("disabled")))
            continue
        try:
            verdict = spec.fn(ctx)
        except Exception as exc:  # a broken rule must be loud, not absent
            verdict = Verdict.fail(
                f"rule {rule_id!r} raised {type(exc).__name__}: {exc}",
                detail="This is a bug in the rule, not a finding about your work.",
            )
        results.append(Evaluated(rule_id, level, verdict))
    return results


def evaluate_event(event: str, ctx: RuleContext) -> list[Evaluated]:
    return evaluate([spec.id for spec in rules_for_event(event)], ctx)


# -- session rules -----------------------------------------------------


@rule(
    "session.isolated_before_edit",
    summary="No file edits until the session has its own worktree.",
    events=("pre-tool-use",),
)
def _isolated_before_edit(ctx: RuleContext) -> Verdict:
    if not ctx.is_edit:
        return Verdict.na()
    if _is_outside_project(ctx, ctx.target_path):
        return Verdict.na("path is outside this project")
    if _is_exempt(ctx, ctx.target_path):
        return Verdict.na("path is exempt from isolation")
    if ctx.session is None:
        if ctx.ambiguous:
            # Reaching here means the target is inside no session's worktree,
            # since a path that names its owner never gets this far. So the
            # actionable fix is about *where* the edit is aimed, not about
            # naming a session: an environment variable cannot be attached to
            # an edit anyway, which made the old advice unusable by the caller
            # it was shown to.
            worktrees = [s.get("worktree") for s in ctx.ambiguous if s.get("worktree")]
            return Verdict.fail(
                f"{len(ctx.ambiguous)} live sessions are registered here and "
                "this path is inside none of their worktrees",
                detail="An edit is attributed by where it lands. A path in the "
                "shared checkout belongs to no session, so no session can be "
                "held responsible for it.",
                fixes=tuple(
                    [f"aim the edit inside your worktree: {w}" for w in worktrees[:4]]
                    + [
                        "or, if this session has no worktree yet: "
                        "invariant session isolate --execute --id <id>"
                    ]
                ),
            )
        return Verdict.fail(
            "no session is registered, so this edit is not attributable",
            fixes=("invariant session start <task-name>",),
        )
    state = ctx.session.get("state")
    if state == "registered":
        return Verdict.fail(
            "session is registered but not isolated",
            detail="Editing before isolating puts work in a tree another "
            "session may also be writing to.",
            fixes=("invariant session isolate --execute",),
        )
    if state in {"torn_down", "abandoned"}:
        return Verdict.fail(
            f"session is {state}; it has no worktree to edit in",
            fixes=("invariant session start <task-name>",),
        )
    return Verdict.clean("session is isolated")


@rule(
    "session.no_edit_in_main_checkout",
    summary="Edits land in a linked worktree, never the shared main checkout.",
    events=("pre-tool-use",),
)
def _no_edit_in_main_checkout(ctx: RuleContext) -> Verdict:
    if not ctx.is_edit:
        return Verdict.na()
    target = ctx.target_path
    if target is None:
        return Verdict.na("no file path in this call")
    if _is_exempt(ctx, target):
        return Verdict.na("path is exempt")

    main = git.main_checkout(ctx.cwd)
    if main is None:
        return Verdict.na("not a git repository")

    resolved = target if target.is_absolute() else (ctx.cwd / target)
    try:
        resolved = resolved.resolve()
    except OSError:
        return Verdict.na("path is not resolvable")

    # The question is where the TARGET lives, not where the caller stands.
    # Asking about the working directory let an edit to a main-checkout file
    # pass whenever it was issued from inside a worktree — which is every edit
    # a session makes, so the rule was close to inert in exactly the setup it
    # was written for.
    for worktree in git.worktrees(ctx.cwd):
        if worktree.is_main:
            continue
        try:
            worktree_path = worktree.path.resolve()
        except OSError:
            continue
        if resolved == worktree_path or worktree_path in resolved.parents:
            return Verdict.clean(f"inside linked worktree {worktree_path.name}")

    if resolved == main or main in resolved.parents:
        return Verdict.fail(
            "this edit targets the shared main checkout",
            detail="The main checkout must only ever hold merged work. A "
            "concurrent session reading it would see a half-finished change, "
            "and there is no branch to abandon if this goes wrong.",
            fixes=("invariant session isolate --execute",),
        )
    return Verdict.clean("outside the main checkout")


@rule(
    "session.push_barrier",
    summary="One push per session, and only once the work is merged.",
    events=("pre-tool-use",),
)
def _push_barrier(ctx: RuleContext) -> Verdict:
    command = str(ctx.tool_input.get("command", "")) if ctx.tool_name == "Bash" else ""
    if not _GIT_PUSH.search(command) and not ctx.extra.get("pushing"):
        return Verdict.na()
    if not ctx.manifest.get("session.push", False):
        # Nothing to count. This barrier rations a session's *one* push; where
        # publishing is not a session step, there is no session push to be the
        # second of, and a rule that fired anyway would be gating a human's
        # decision under the name of a session policy.
        return Verdict.na(
            "publishing is not a session step here (session.push is off)"
        )
    if ctx.session is None:
        # Exempt paths — configuration and the knowledge store — commit
        # straight to the shared branch by design; that is what the exemption
        # in session.exempt_paths means. Such a commit can therefore never
        # acquire a session, so demanding one here does not enforce the
        # barrier, it makes those commits permanently unpushable. The barrier
        # counts *session* work, and a push carrying none has nothing to count.
        #
        # Deliberately narrow: one non-exempt path in the unpushed set and the
        # barrier applies in full, and an unanswerable question (no upstream,
        # detached head) is not treated as an answer.
        unpushed = git.unpushed_files(ctx.cwd)
        # git reports these repo-relative; anchor them to the repository before
        # asking, or they resolve against the process working directory and
        # match nothing — an exemption that silently stops applying.
        top = git.toplevel(ctx.cwd)
        if unpushed and top is not None:
            unpushed = [Path(top) / path for path in unpushed]
        if unpushed and all(_is_exempt(ctx, path) for path in unpushed):
            return Verdict.na(
                f"{len(unpushed)} unpushed path(s), all exempt from session "
                "isolation — this push carries no session work"
            )
        return Verdict.fail(
            "a push with no registered session cannot be counted against the "
            "one-push barrier",
            detail="Exempt paths are the exception, and every unpushed commit "
            "here touches something outside them.",
            fixes=("invariant session start <task-name>",),
        )
    already = int(ctx.session.get("push_count", 0))
    if already:
        return Verdict.fail(
            f"this session has already pushed {already} time(s)",
            detail="A push is a remote event that costs a CI run. Worktrees "
            "share one object database, so a peer sees local commits without "
            "one — a push is never needed for peer visibility.",
            fixes=("commit locally and let the teardown push carry it",),
        )
    state = ctx.session.get("state")
    if state not in {"merged", "reviewed"}:
        return Verdict.fail(
            f"session is {state}; the push barrier opens after the merge",
            fixes=("invariant session review", "invariant session merge"),
        )
    return Verdict.clean("first push, post-merge")


@rule(
    "session.push_enabled",
    summary="A session only publishes where the project asks it to.",
    events=(),
)
def _push_enabled(ctx: RuleContext) -> Verdict:
    """Guards entry to the ``pushed`` state.

    Held as a gate rather than by removing the edge, so that enabling
    publishing is one manifest line and never a fork of the state machine.
    """
    if ctx.manifest.get("session.push", False):
        return Verdict.clean("publishing is part of this project's sessions")
    return Verdict.fail(
        "publishing is not part of this project's session lifecycle",
        detail="A session ends at the merge: its work is on the base branch "
        "and every worktree already sees it, because they share one object "
        "database. Publishing costs a CI run and is a decision about the "
        "project, so it is left to whoever is making that decision.",
        fixes=(
            "invariant session teardown  (the normal end of a session)",
            "set session.push: true in the manifest to make it a session step",
        ),
    )


def _unanswerable_worktree_question(ctx: RuleContext) -> Verdict:
    """Git could not say whether this is a linked worktree. Do not relax.

    This gate guards the self-merge whose failure mode is "both commands exit
    zero", so answering "could not ask git" as "running from the main
    checkout" — which is what the old ``False`` default did — was a clean pass
    on an unreadable repository (AUD-07).
    """
    return Verdict.fail(
        "cannot tell whether this directory is a linked worktree",
        detail=f"git could not answer from {ctx.cwd}. The merge this gate "
        "guards fails silently — merging a branch from inside its own "
        "worktree merges it into itself and both commands exit zero — so an "
        "unanswered question here is not a pass.",
        fixes=("run it from the main checkout of the repository",),
    )


@rule(
    "session.ff_merge_target",
    summary="A fast-forward merge must be run from the main checkout.",
    events=("pre-tool-use",),
)
def _ff_merge_target(ctx: RuleContext) -> Verdict:
    # The session-merge command: landing this branch on the base, which must
    # happen from the main checkout. Unchanged.
    if ctx.extra.get("merging"):
        linked = git.in_linked_worktree(ctx.cwd)
        if linked is None:
            return _unanswerable_worktree_question(ctx)
        if not linked:
            return Verdict.clean("running from the main checkout")
        return Verdict.fail(
            "this merge is being run from inside a linked worktree",
            detail="Merging a branch while standing in that branch's own "
            "worktree merges it into itself, and the follow-up push then "
            "pushes an unchanged target. Both commands exit zero, so nothing "
            "reports the failure. Verify with 'git branch -r --contains'.",
            fixes=("run it from the main checkout",),
        )

    command = str(ctx.tool_input.get("command", "")) if ctx.tool_name == "Bash" else ""
    target = _merge_target(command)
    if target is None:
        return Verdict.na()
    linked = git.in_linked_worktree(ctx.cwd)
    if linked is None:
        return _unanswerable_worktree_question(ctx)
    if not linked:
        return Verdict.clean("running from the main checkout")

    # The trap is merging a branch INTO ITSELF, which is what happens when the
    # argument names the branch you are standing on. `git merge master` from a
    # feature branch is the opposite operation — integrating the mainline so
    # conflicts surface here, with tests around them, instead of at the final
    # merge. Blocking that pushed conflict resolution to a worse place and gave
    # a correct-sounding reason for it.
    current = git.current_branch(ctx.cwd)
    if not current or _short_ref(target) != _short_ref(current):
        return Verdict.na(
            f"merging {target!r} into {current or 'HEAD'} — not a self-merge"
        )

    return Verdict.fail(
        f"merging {target!r} into itself from its own worktree",
        detail="This merges the branch into itself and a follow-up push then "
        "pushes an unchanged target. Both commands exit zero, so nothing "
        "reports the failure. Verify with 'git branch -r --contains'.",
        fixes=("invariant session merge",),
    )


@rule(
    "session.own_file_set_only",
    summary="Commits touch only the files this session declared.",
    events=("pre-tool-use",),
)
def _own_file_set_only(ctx: RuleContext) -> Verdict:
    command = str(ctx.tool_input.get("command", "")) if ctx.tool_name == "Bash" else ""
    if "git commit" not in command and not ctx.extra.get("committing"):
        return Verdict.na()
    if ctx.session is None:
        return Verdict.na("no session to compare against")
    declared = {str(p) for p in ctx.session.get("files", [])}
    if not declared:
        return Verdict.na("this session has declared no file set")
    listed = git.staged_files(ctx.cwd)
    if listed is None:
        # "Could not ask git" is not "nothing is staged". The old `[]` default
        # reported exactly that — "0 staged file(s), all declared" — on an
        # unreadable repository (AUD-07).
        return Verdict.fail(
            "cannot read the staged set for this commit",
            detail=f"git could not answer from {ctx.cwd}, so whether this "
            "commit stays inside the session's declared file set is unknown — "
            "and an unanswered question is not a pass.",
            fixes=("run the commit from inside the session's worktree",),
        )
    staged = {str(p) for p in listed}
    stray = sorted(staged - declared)
    if not stray:
        return Verdict.clean(f"{len(staged)} staged file(s), all declared")
    return Verdict.fail(
        f"{len(stray)} staged file(s) are not in this session's declared set",
        detail="In a shared tree a staged path you did not touch rides into "
        "your commit and out of a peer's. Stray: " + ", ".join(stray[:8]),
        fixes=("invariant session files add <path>", "git restore --staged <path>"),
    )


@rule(
    "session.named_after_isolating",
    summary="An isolated session carries a task-derived name.",
    events=(),
)
def _named_after_isolating(ctx: RuleContext) -> Verdict:
    # No abstain on `registered`, deliberately. This is the entry gate of
    # `isolated`, and an entry gate runs while the record still reads the
    # PREVIOUS state — so abstaining on `registered` made the gate unable to
    # fire on the only edge it guards, a second layer of deadness on top of
    # AUD-05's "no code path evaluates it" (AUD-14). A registered session
    # already carries its name, so the question is answerable there.
    if ctx.session is None:
        return Verdict.na("no session")
    name = str(ctx.session.get("name") or "")
    if name and not name.startswith("session/"):
        return Verdict.clean(f"named {name!r}")
    return Verdict.fail(
        "session has no task-derived name",
        detail="Several sessions on one machine are indistinguishable in a "
        "list when they are all named after their identifier.",
        fixes=("invariant session start <task-name>",),
    )


def _machine_unloadable_failure(which: str) -> Verdict:
    """A gate whose machine cannot be loaded fails; it does not step aside.

    A configuration error is exactly when a gate's answer matters most, and a
    skip on a gate a project holds at ``block`` reads as a pass (AUD-08).
    """
    return Verdict.fail(
        f"the {which} machine could not be loaded, so this gate cannot answer",
        detail="An unanswerable question is not a negative answer. Until the "
        "configuration loads, nothing is verifying this invariant.",
        fixes=(
            "invariant doctor",
            "check methodology.pipeline and methodology.extra_machines in the manifest",
        ),
    )


@rule(
    "session.work_would_be_stranded",
    summary="A turn does not end with committed work sitting on an unmerged branch.",
    events=("stop",),
)
def _work_would_be_stranded(ctx: RuleContext) -> Verdict:
    """The last moment at which the Definition of Done can still be demanded.

    Everything else in this file fires while work is happening — before a tool
    call, on entering a state. The closeout is the one obligation that comes
    due when work *stops*, and nothing fired there at all: the pipeline's
    terminal state is ``committed``, which hands off to the session machine,
    and the session machine was never asked again. ``SessionEnd`` is the hook
    that carries the right name and it is the one hook whose output is
    discarded, so a demand made there reaches nobody.

    The trigger is deliberately narrow — a pipeline that declared itself done
    AND a branch holding commits the base does not have. Both halves matter. A
    session mid-work is ahead of its base constantly and blocking on that alone
    would fire on every turn until it learned to be ignored, which is how a
    blocking gate becomes an obstacle instead of a control. And a pipeline that
    reached ``committed`` with nothing ahead of the base has already merged;
    what it owes then is tidiness, not rescue, and that is a different rule.
    """
    # Asked once already this turn and the caller is acting on it. Asking again
    # would be a loop, and a gate that cannot be satisfied gets forced.
    if ctx.payload.get("stop_hook_active"):
        return Verdict.na("already raised this turn")
    if ctx.session is None:
        return Verdict.na("no session")

    state = str(ctx.session.get("state") or "")
    session_terminal = _terminal_states(load_session_machine, ctx.manifest)
    if session_terminal is None:
        return _machine_unloadable_failure("session")
    if state in session_terminal:
        return Verdict.na(f"session is {state}")

    # "The work is done" is a claim the pipeline makes, not one inferred from
    # the diff. Inferring it would guess, and would guess wrong on the session
    # that is mid-refactor with three commits down. The machine judging that
    # claim is the one recorded ON the pipeline at start, not whatever the
    # manifest names today (AUD-02/03).
    pipeline_state = str((ctx.pipeline or {}).get("state") or "")
    if pipeline_state == ABANDONED:
        return Verdict.na("pipeline abandoned — nothing is being claimed as done")
    pipeline_terminal = _terminal_states(
        load_pipeline_machine,
        ctx.manifest,
        str((ctx.pipeline or {}).get("methodology") or "") or None,
    )
    if pipeline_terminal is None:
        # A skip here was the old behavior — "work is still in flight" on a
        # machine that could not even be loaded — which switched the closeout
        # gate off on a mistyped manifest (AUD-08).
        return _machine_unloadable_failure("pipeline")
    if pipeline_state not in pipeline_terminal:
        return Verdict.na(
            f"pipeline is {pipeline_state or 'not started'} — work is still in flight"
        )

    branch = str(ctx.session.get("branch") or "")
    if not branch:
        return Verdict.na("session owns no branch")
    # The `"master"` fallback here and the `""` in `_changed_set` read the
    # same field of the same record and answer differently, which looks like
    # drift and is not. The two questions have opposite failure polarities.
    # Here, a guessed base that is wrong costs a comparison against the wrong
    # ref, and the verdict NAMES the base it used, so a wrong guess is on
    # screen; guessing nothing would silently stop asking whether the work is
    # stranded, which is the whole point of the rule. In `_changed_set` a
    # guessed base would manufacture a touched set out of an unrelated
    # branch's history and hand it to a gate as fact, where answering `None`
    # leaves the declared set carrying the answer and, failing that, an
    # explicit refusal. `Session.base` also defaults to `"master"` in the
    # domain, so a record reaching either reader without one is a record
    # about which nothing is known — the case for refusing, not for guessing.
    base = str(ctx.session.get("base") or "master")

    ahead = git.commits_ahead(_repo_root(ctx), branch, base)
    if ahead is None:
        return Verdict.na(f"could not compare {branch} against {base}")
    if ahead == 0:
        return Verdict.clean(f"{branch} holds nothing {base} does not")

    return Verdict.fail(
        f"{ahead} commit(s) on {branch} are not on {base}",
        detail="The pipeline says this work is finished and the turn is "
        "ending, but the commits live only on the session branch. Nobody "
        "outside this session is looking at that branch, so stopping here "
        "leaves the work where the next person to need it will not find it. "
        "Merging is the step that makes it real; teardown is the step that "
        "makes the session over.",
        fixes=(
            "invariant session review",
            "invariant session merge      # from the main checkout",
            "invariant session teardown   # removes the worktree AND the branch",
            "invariant session transition abandoned --reason '<why>'  # if dropped",
        ),
    )


@rule(
    "session.teardown_complete",
    summary="A finished session's worktree is removed.",
    events=("stop",),
)
def _teardown_complete(ctx: RuleContext) -> Verdict:
    # Both ends of the lifecycle, because publishing is optional: a session
    # that merged and stopped there is just as finished as one that published,
    # and checking only the published state would exempt the common case.
    if ctx.session is None or ctx.session.get("state") not in {"merged", "pushed"}:
        return Verdict.na()
    state = ctx.session.get("state")
    worktree = ctx.session.get("worktree")
    if worktree and Path(worktree).exists():
        return Verdict.fail(
            f"the session {state} but its worktree still exists",
            detail="A worktree left behind keeps a branch alive and costs "
            "disk; the next session then works around it rather than in it.",
            fixes=("invariant session teardown",),
        )
    return Verdict.clean("worktree removed")


# -- pipeline rules ----------------------------------------------------


@rule(
    "pipeline.started_before_edit",
    summary="Editing happens inside a started pipeline.",
    events=("pre-tool-use",),
)
def _pipeline_started_before_edit(ctx: RuleContext) -> Verdict:
    if not ctx.is_edit:
        return Verdict.na()
    if _is_outside_project(ctx, ctx.target_path):
        return Verdict.na("path is outside this project")
    if _is_exempt(ctx, ctx.target_path):
        return Verdict.na("path is exempt")
    if ctx.pipeline and ctx.pipeline.get("state"):
        return Verdict.clean(f"pipeline is in {ctx.pipeline['state']!r}")
    return Verdict.fail(
        "no pipeline is running, so this change has no recorded methodology",
        fixes=("invariant pipeline start --task '<what you are doing>'",),
    )


@rule(
    "pipeline.complete",
    summary="The methodology ran to its end before the work lands.",
    events=(),
)
def _pipeline_complete(ctx: RuleContext) -> Verdict:
    """The gate that makes every other pipeline gate mandatory.

    ``pipeline.started_before_edit`` proved a pipeline exists. Nothing proved
    it ever moved. Starting one in ``designing`` satisfied that rule for the
    rest of the session, and two sessions merged and tore down with their
    pipeline still in its initial state — which silently skipped every gate the
    methodology owns, because all of them are EXIT gates on states nobody was
    required to pass through: the red state's failing test, the green state's
    green suite, the review state's coverage, privacy, docs and agent checks.

    One gate closes all of it. The machine's ``next`` lists already forbid
    skipping a state, so requiring the pipeline to be TERMINAL before the work
    lands transitively requires every exit gate along the way. Nothing here
    re-derives what those gates decided; it requires that they were asked.
    """
    if ctx.pipeline is None or not ctx.pipeline.get("state"):
        return Verdict.fail(
            "no pipeline ran for this work",
            detail="The methodology is what makes the review gates apply. "
            "Without one, nothing recorded how this change was arrived at.",
            fixes=("invariant pipeline start --task '<what you are doing>'",),
        )

    state = str(ctx.pipeline.get("state"))
    if state == ABANDONED:
        return Verdict.fail(
            "the pipeline was abandoned",
            detail="Abandoning is how work is dropped, not how it lands. A "
            "session carrying an abandoned pipeline is asking to merge work "
            "whose methodology was called off.",
            fixes=(
                "invariant pipeline start --task '<what you are doing>'",
                "invariant session transition abandoned --reason '<why>'  # if dropped",
            ),
        )

    try:
        # The machine recorded on the pipeline at start, falling back to the
        # manifest for records that predate the pin (AUD-02/03).
        machine = load_pipeline_machine(
            ctx.manifest, str(ctx.pipeline.get("methodology") or "") or None
        )
    except InvariantError as exc:
        # This used to skip, non-blocking — the gate whose own docstring calls
        # it "the gate that makes every other pipeline gate mandatory" stepped
        # aside on a mistyped `methodology.pipeline` (AUD-08).
        return Verdict.fail(
            f"the pipeline machine could not be loaded: {exc}",
            detail="Whether this methodology ran to its end cannot be judged "
            "without its machine, and an unanswerable question is not a pass.",
            fixes=(
                "invariant doctor",
                "check methodology.pipeline and methodology.extra_machines in the manifest",
            ),
        )

    if state in machine.terminal:
        return Verdict.clean(f"pipeline reached {state!r}")

    remaining = ", ".join(machine.next_states(state)) or "(none)"
    return Verdict.fail(
        f"the pipeline is still in {state!r}",
        detail="Every gate this methodology owns is an exit gate on a state "
        "further along, so landing from here skips all of them rather than "
        "passing them. Walk it forward, or abandon it deliberately.",
        fixes=(
            f"invariant pipeline gates      # what {state!r} owes",
            f"invariant pipeline advance <{remaining}>",
        ),
    )


# -- agent rules -------------------------------------------------------
#
# Installing an agent and using one are different events, and only the second
# is worth anything. These gates read the invocations the host recorded, in the
# same spirit as the docs-first gate: what happened, not what was intended.


def _agent_role_gate(ctx: RuleContext, role: str, purpose: str) -> Verdict:
    configured = ctx.manifest.get(f"agents.roles.{role}") or []
    configured = [str(a) for a in configured]
    if not configured:
        return Verdict.na(f"no agent is configured for the {role!r} role")

    ran = [str(a) for a in (ctx.evidence("agents_run", []) or [])]
    matched = [a for a in ran if a in configured]
    if matched:
        return Verdict.clean(f"{', '.join(matched)} ran in this pipeline")

    detail = (
        f"{purpose} A subagent carries the project's own procedure; doing the "
        f"work inline reaches the same files by a route nothing checked."
    )
    if ran:
        detail += f" Agents that did run here: {', '.join(ran)}."
    return Verdict.fail(
        f"no {role} agent ran in this pipeline",
        detail=detail,
        fixes=(
            f"dispatch one of: {', '.join(configured)}",
            f"or retune agents.roles.{role} if this project does not use one",
        ),
    )


@rule(
    "agents.design_agent_ran",
    summary="A design agent was dispatched before the design state was left.",
    events=(),
)
def _agents_design_ran(ctx: RuleContext) -> Verdict:
    return _agent_role_gate(
        ctx,
        "design",
        "The design agent is what reads the architecture docs before planning "
        "against them.",
    )


@rule(
    "agents.review_agent_ran",
    summary="A review agent was dispatched before the review state was left.",
    events=(),
)
def _agents_review_ran(ctx: RuleContext) -> Verdict:
    return _agent_role_gate(
        ctx,
        "review",
        "The review agent is what checks the change against the project's "
        "compliance checklist before it is committed.",
    )


# -- docs rules --------------------------------------------------------


@rule(
    "docs.read_architecture",
    summary="The architecture docs were read before designing the change.",
    events=(),
)
def _docs_read_architecture(ctx: RuleContext) -> Verdict:
    docs_root = _docs_root(ctx)
    if not docs_root.exists():
        return Verdict.fail(
            f"no architecture docs at {_rel(docs_root, _repo_root(ctx))}",
            detail="Dependency knowledge derived from file search is guesswork "
            "that goes stale silently. This library treats a maintained "
            "architecture doc as a precondition rather than a nicety.",
            fixes=(f"create {_rel(docs_root, _repo_root(ctx))} and describe the layers",),
        )
    read = [Path(p) for p in ctx.evidence("docs_read", []) or []]
    inside = [p for p in read if _is_within(p, docs_root)]
    if inside:
        return Verdict.clean(f"{len(inside)} architecture doc(s) read")
    return Verdict.fail(
        "no architecture doc has been read in this pipeline",
        detail="The diagrams are the authoritative dependency map; reading "
        "them is what makes the plan trustworthy.",
        fixes=(f"read the relevant doc under {_rel(docs_root, _repo_root(ctx))}",),
    )


@rule(
    "docs.updated",
    summary="A structural change updates the architecture docs.",
    events=(),
)
def _docs_updated(ctx: RuleContext) -> Verdict:
    docs_root = _docs_root(ctx)
    # This is an EXIT gate on the review state, so it runs when the work is
    # already committed and `git diff` reports nothing. Reading the working
    # tree alone made the one gate a methodology cannot opt out of return
    # "nothing changed" on every real end-of-phase evaluation (BUG_012).
    touched = _changed_set(ctx)
    changed = touched.paths
    if not changed:
        if not touched.answerable:
            return _unestablished_changed_set(touched)
        return Verdict.na("nothing changed")

    root = _repo_root(ctx)
    source_changed = [p for p in changed if not _is_within(root / p, docs_root)]
    docs_changed = [p for p in changed if _is_within(root / p, docs_root)]

    if not source_changed:
        return Verdict.clean("docs-only change")
    if docs_changed:
        return Verdict.clean(f"{len(docs_changed)} doc file(s) updated alongside")
    return Verdict.fail(
        "source changed but no architecture doc did",
        detail="If this change adds a component, a dependency, or a data flow, "
        "the diagram is now wrong. If it genuinely changes no structure, say so "
        "and override.",
        fixes=(
            f"update the relevant doc under {_rel(docs_root, root)}",
            "invariant pipeline advance <state> --force --reason 'no structural change'",
        ),
    )


@rule(
    "docs.diagrams_current",
    summary="A doc whose structure changed has its diagram updated too.",
    events=(),
)
def _docs_diagrams_current(ctx: RuleContext) -> Verdict:
    if not ctx.manifest.get("docs.require_diagram_update", True):
        return Verdict.na("diagram updates not required")
    fmt = ctx.manifest.get("docs.diagrams")
    if fmt in {None, "none"}:
        return Verdict.na("no diagram format configured")

    # A methodology that declares itself documentation is declaring there is no
    # structural change, and this gate fires on structural change. Read from
    # the pipeline's DECLARED kind rather than inferred from file extensions:
    # a caller stating what kind of work this is has made a claim that can be
    # wrong and reviewed, where a rule reading the diff would be guessing —
    # and would guess wrong on the change that adds a doc and a schema at once.
    if (ctx.pipeline or {}).get("methodology") == "docs":
        return Verdict.na(
            "documentation pipeline — no structural change is being claimed"
        )

    docs_root = _docs_root(ctx)
    root = _repo_root(ctx)
    # Same three sources as `docs.updated`, for the same reason: a doc change
    # committed in this phase is invisible to the working tree by the time
    # this gate is asked about it.
    touched = _changed_set(ctx)
    docs_changed = [p for p in touched.paths if _is_within(root / p, docs_root)]
    if not docs_changed:
        # "No doc changed" is a real answer; "nobody could tell what changed"
        # is not, and only the second may not be reported as a skip.
        if not touched.answerable:
            return _unestablished_changed_set(touched)
        return Verdict.na("no doc changed")

    markers = {"mermaid": "```mermaid", "plantuml": "@startuml"}
    marker = markers.get(str(fmt), "")
    with_diagrams = []
    for rel in docs_changed:
        try:
            if marker and marker in (root / rel).read_text(encoding="utf-8"):
                with_diagrams.append(rel)
        except OSError:
            continue
    if with_diagrams:
        return Verdict.clean(f"{len(with_diagrams)} changed doc(s) carry a diagram")
    return Verdict.fail(
        "the changed docs contain no diagram in the configured format",
        detail=f"Configured format is {fmt}. A structural doc without a diagram "
        "describes the map in prose, which is the thing diagrams exist to "
        "replace.",
        fixes=(f"add a {fmt} diagram to the changed doc",),
    )


# -- test rules --------------------------------------------------------


# ``test.suite_runs`` was registered here and graded by all three profiles,
# and no machine named it and no event reached it — an enforcement level
# reported for a rule that never ran once. Both questions it asked are asked
# by ``test.all_pass`` below, which fails on no recorded run and fails on a
# clean exit that executed nothing. Retired rather than wired, because there
# is no state where "a run happened, green or not" is the useful demand: the
# red state already needs a *failing* run (``test.new_test_fails``) and every
# other state needs a passing one.


def _red_phase(ctx: RuleContext) -> tuple[str | None, str]:
    """Which state this pipeline's machine calls its proof-of-red state.

    Returns ``(phase, error)``; ``error`` non-empty means the machine could
    not be loaded and the question is unanswerable.

    The name used to be the constant ``"red"``, which is a state ``tdd`` has
    and ``bugfix`` does not — and ``bugfix`` declares this gate (BUG_030).
    A pipeline with no record at all keeps the old default: there is no
    machine to ask, and the gate is about to fail on the absent evidence
    anyway.
    """
    if not ctx.pipeline:
        return "red", ""
    try:
        machine = load_pipeline_machine(
            ctx.manifest, str(ctx.pipeline.get("methodology") or "") or None
        )
    except InvariantError as exc:
        return None, str(exc)
    return machine.red_phase or "red", ""


def _why_not_proof(run: Mapping[str, Any]) -> str:
    """Why this failing run does not prove a TEST failed, or "" if it does.

    ``passed`` is derived from the exit status, and a compile error, a signing
    failure, a missing simulator and a host crash all exit non-zero. The
    gate's name is a claim about a test, so the predicate is a parsed,
    non-zero failure count — the same distinction ``test.all_pass`` already
    draws one rule below, where a clean exit that executed nothing is refused
    (BUG_029).

    Hazards are deliberately NOT a rejection here, and the asymmetry with
    ``test.all_pass`` is the point. A hazard casts doubt on a green because
    the doubt is "tests may not have run"; against a counted, non-zero
    failure there is positive evidence that one ran and failed. Rejecting on
    a hazard would make the gate unsatisfiable in the ecosystem it was written
    for, which is the failure mode this tracker files most often.
    """
    executed = run.get("executed")
    failed = run.get("failed")
    # `executed is None` is not zero: it means this adapter does not count
    # tests, which is the generic adapter's permanent state. Holding a missing
    # adapter feature against a real run is the mistake TestResult.trustworthy
    # already documents not making.
    if executed == 0:
        return "it executed no tests"
    if failed is None:
        return "no failure count could be parsed from its output"
    if failed == 0:
        return "it exited non-zero with no test failures counted"
    return ""


@rule(
    "test.new_test_fails",
    summary="A new test failed before the fix existed.",
    events=(),
)
def _test_new_test_fails(ctx: RuleContext) -> Verdict:
    phase, error = _red_phase(ctx)
    if phase is None:
        return Verdict.fail(
            f"the pipeline machine could not be loaded: {error}",
            detail="Which state this methodology records its failing run in is "
            "the machine's answer to give, and an unanswerable question is not "
            "a pass.",
            fixes=("fix methodology.pipeline, or the machine file it names",),
        )
    runs = ctx.evidence("test_runs", []) or []
    red = [r for r in runs if r.get("phase") == phase]
    if not red:
        return Verdict.fail(
            "no failing run was recorded before the implementation",
            detail="A test written after the fix proves the fix compiles, not "
            "that it addresses anything. A green repro has several readings and "
            "only one of them is 'fixed'.",
            # `advance` evaluates gates; it does not run anything. Naming it
            # here sent people to the command that reads the evidence in
            # search of the command that writes it. The phase named is this
            # machine's own, so the advice stopped being a workaround that
            # teaches a session to label a run with a state it was never in.
            fixes=(
                f"invariant pipeline test --phase {phase}  (records the failing run)",
            ),
        )
    failing = [r for r in red if not r.get("passed", True)]
    if not failing:
        return Verdict.fail(
            f"the run recorded in the {phase} phase passed",
            detail="A test that passes before the change cannot be evidence for it. "
            "Either it does not reach the defect, or the defect is not there.",
            fixes=("make the test fail on the reported symptom first",),
        )
    rejected = []
    for run in failing:
        reason = _why_not_proof(run)
        if not reason:
            return Verdict.clean("a red run is on record")
        rejected.append((run, reason))
    # "Not yet proven", not "cannot be proven". A `Verdict.fail` here says the
    # gate has not passed, and a genuine red still satisfies it — so this does
    # not join the family of gates in this tracker that no correct work can
    # clear. `Verdict.na` was the other candidate and is wrong for BUG_020's
    # reason: a skip a reader cannot tell from a pass is worse than a refusal.
    return Verdict.fail(
        f"the failing run in the {phase} phase does not show a test failing",
        detail="; ".join(
            # Naming the run is the whole difference between "go find the
            # compile error" and "go hunt a second red".
            "{}: {} (exit {})".format(
                run.get("command") or "the recorded run", reason, run.get("exit_code")
            )
            for run, reason in rejected
        ),
        fixes=(
            (
                "read the run's output — a build or launch failure exits "
                "non-zero having tested nothing"
            ),
            (
                "then record a run in which a test fails: "
                f"invariant pipeline test --phase {phase}"
            ),
        ),
    )


@rule("test.all_pass", summary="The suite is green.", events=())
def _test_all_pass(ctx: RuleContext) -> Verdict:
    runs = ctx.evidence("test_runs", []) or []
    if not runs:
        return Verdict.fail(
            "no test run recorded",
            detail="Running the suite outside the pipeline leaves no record, "
            "and this gate can only answer from the record.",
            fixes=("invariant pipeline test",),
        )
    last = runs[-1]
    if not last.get("passed"):
        failed = last.get("failed")
        # A failure count of None means the run failed and the count could not
        # be parsed. Rendering that as "0 failing" would read as green prose
        # attached to a red verdict.
        count = "an unknown number of" if failed is None else failed
        return Verdict.fail(
            f"the last test run failed ({count} failing)",
            detail=str(last.get("note") or "") or "",
            fixes=("fix the failures, then re-run",),
        )
    if last.get("executed") == 0:
        return Verdict.fail(
            "the last run exited clean having executed zero tests",
            detail="A filter that matches nothing exits zero. 'The suite is "
            "green' and 'the suite did not run' are the same exit status, and "
            "only the executed count separates them.",
            fixes=("check the test selector",),
        )
    hazards = last.get("hazards") or []
    if hazards:
        return Verdict.fail(
            "the last run passed but carries a hazard that makes the pass "
            "untrustworthy",
            detail="; ".join(str(h) for h in hazards),
            fixes=("re-run without the hazard condition",),
        )
    return Verdict.clean("suite is green and the pass is trustworthy")


@rule(
    "coverage.meets_floor",
    summary="Touched files meet the configured coverage floor.",
    events=(),
)
def _coverage_meets_floor(ctx: RuleContext) -> Verdict:
    floor = float(ctx.manifest.get("methodology.gates.coverage_min", 0) or 0)
    if floor <= 0:
        return Verdict.na("no coverage floor configured")
    recorded = ctx.evidence("coverage", {}) or {}

    # Reporters emit absolute paths; changed files are repo-relative. Comparing
    # the two spellings directly yields an empty intersection, and an empty
    # intersection would report "all files at or above the floor" — a gate
    # passing vacuously, which is worse than the blocking it replaced.
    roots = _coverage_roots(ctx)

    scope = str(ctx.manifest.get("methodology.gates.coverage_scope", "touched"))
    excluded: list[str] = []
    source_changed: set[str] = set()
    if scope == "touched":
        # The union of the three sources that can know what changed. This
        # gate is where that union was first worked out; it now lives in
        # `_changed_set`, which the two docs-first gates read too, because
        # keeping a second copy here is exactly how they came to be wrong
        # while this one was right (BUG_012).
        touched_set = _changed_set(ctx)
        changed = {_relative_to_any(path, roots) for path in touched_set.paths}
        source_changed = {p for p in changed if not _looks_like_docs(p)}

        # Whether anything here is MEASURABLE is a different question from
        # whether anything was measured, and only the first is answerable
        # without a run. It is asked HERE, ahead of the recording guard,
        # because that guard used to fire first and made the N/A written for
        # this case unreachable. Everything else still queues behind the
        # guard: when nothing was recorded AND the scope cannot be
        # established, "run coverage" is the more actionable of the two
        # refusals, and it often makes the scope answerable as a side effect.
        if source_changed:
            _unmeasurable = _nothing_a_run_could_measure(
                ctx, recorded, source_changed, roots
            )
            if _unmeasurable is not None:
                return _unmeasurable

    if not recorded:
        return Verdict.fail(
            "no coverage was recorded",
            detail="The gate reads what a coverage run measured; nothing has "
            "measured anything in this pipeline.",
            fixes=("invariant pipeline coverage",),
        )

    coverage = {_relative_to_any(path, roots): pct for path, pct in recorded.items()}

    if scope == "touched":
        # An empty touched set used to fall through to the full measured set,
        # silently converting a touched-scope floor into a project-wide one.
        # Every legacy file below the floor then blocks a change that never
        # went near it, and the verdict names those files — so it reads as a
        # coverage failure rather than as the scoping failure it is, and the
        # author goes looking in the wrong place. "Nothing was touched" and
        # "everything was touched" are opposite answers; defaulting to the
        # second because the first could not be established is the widest
        # possible reading of the narrowest possible evidence.
        if not source_changed:
            if not touched_set.answerable:
                return Verdict.fail(
                    "the touched set could not be established",
                    detail=(
                        "Scope is 'touched', but the working tree is clean, no "
                        "file set was declared, and the branch's commits could "
                        f"not be read{f' against base {touched_set.base!r}' if touched_set.base else ' — no base recorded'}. "
                        "The gate will not answer a question it could not "
                        "scope: holding the whole project to the floor would "
                        "block on files nobody edited, and skipping the floor "
                        "would pass work it never measured."
                    ),
                    fixes=(
                        "invariant session files add <path>",
                        "or check the session's base branch is reachable",
                    ),
                )
            return Verdict.na(
                "no source file was touched; a touched-scope floor has nothing "
                "to answer for"
            )

        touched: dict[str, float] = {}
        unmatched: list[str] = []
        for wanted in sorted(source_changed):
            hits = {f: pct for f, pct in coverage.items() if _same_file(wanted, f)}
            if hits:
                touched.update(hits)
            else:
                unmatched.append(wanted)

        # An absent row has two causes and they are not the same fact. A file
        # the run never measured is exactly what this gate exists to catch. A
        # file with nothing to measure — a bare protocol, an enum of cases, a
        # file this change DELETED — can never earn a row, so demanding one is
        # a gate no test can clear, and a gate no test can clear is one people
        # route around. Only the second is set aside, only on positive
        # evidence, and every file set aside is NAMED below.
        excluded, unmatched = _explain_absent_rows(ctx, unmatched, roots)
        set_aside = _excluded_detail(excluded)

        if not touched and unmatched:
            return Verdict.fail(
                "coverage was measured, but none of it covers the changed files",
                detail=(
                    f"{len(coverage)} file(s) measured, "
                    f"{len(source_changed)} changed, no overlap. Usually a "
                    f"path-spelling mismatch between the coverage reporter "
                    f"and git. Reporting this as a pass would be the gate "
                    f"approving a measurement of something else."
                    + (f"  {set_aside}" if set_aside else "")
                ),
                fixes=("check the coverage command covers the changed target",),
            )

        # A partial intersection is the dangerous middle. Left unreported,
        # the gate answers for the files it happened to match and stays
        # silent about the ones it could not — which reads as a pass for
        # all of them. Not knowing is a different answer from knowing it
        # is fine, and this is where that distinction has teeth.
        if unmatched:
            return Verdict.fail(
                f"{len(unmatched)} changed source file(s) have no coverage row",
                detail=(
                    ", ".join(unmatched[:6])
                    + ". Either they are outside the measured target, or the "
                    "path spellings still disagree. The gate cannot answer "
                    "for a file it did not find, and answering anyway for "
                    "the rest would report a pass it has not established."
                    + (f"  {set_aside}" if set_aside else "")
                ),
                fixes=(
                    "check these files are in the coverage target",
                    "or check the reporter and git spell the path the same way",
                    "or teach the ecosystem adapter to answer for this file type",
                ),
            )

        # Held out of the FLOOR, never out of the MEASUREMENT, and only after
        # the no-row check above has already run. Both orderings matter. A
        # file set aside here still had to produce a row, so suppressing a
        # path from the reporter cannot buy a pass through this door — the
        # guard that catches a path-spelling mismatch stays exactly as sharp.
        # And the number is kept and reported alongside the exclusion, so a
        # suite that quietly stops executing is still legible in the verdict
        # rather than disappearing from it.
        #
        # Two kinds of exclusion, and they are different claims. A glob is a
        # POLICY the manifest states out loud ("test targets are not held to
        # the production floor"). A comment-only diff is a FACT about this
        # change: it altered no executable line in that file, so the file's
        # coverage is not something this change can be asked to answer for.
        # Neither one forgives a file that got worse — a diff that touches
        # code is judged on the number, whatever its path.
        touched, policy_aside = _excluded_by_manifest_globs(ctx, touched)
        touched, comment_aside = _excluded_as_comment_only(ctx, touched, roots)
        excluded.extend(policy_aside)
        excluded.extend(comment_aside)
        set_aside = _excluded_detail(excluded)

        if not touched:
            return Verdict.na(
                "every touched source file has nothing a coverage run could "
                "measure",
                detail=set_aside,
            )
        coverage = touched

    # How the measurement was obtained, carried into the verdict. A clean pass
    # measured on a run with a live hazard is not the same claim as one
    # measured on a clean run, and a reader cannot tell them apart afterwards
    # unless the verdict says so.
    run = ctx.evidence("coverage_run", {}) or {}

    # A measurement that can only read LOW may be annotated: it blocks work
    # that was fine, which is visible and self-correcting, because being
    # blocked is what sends someone to look. A measurement that can read HIGH
    # must block: it passes work that was never covered, silently, and nobody
    # goes to look. Same evidence, opposite handling, because the two failures
    # are not symmetrical.
    overstating = run.get("overstating_hazards") or []
    if overstating:
        return Verdict.fail(
            "the coverage measurement cannot be trusted upward",
            detail=(
                "Live on the measuring run: "
                + ", ".join(overstating)
                + ". These can make coverage read HIGHER than reality, so a "
                "pass here would be the gate approving work it has no evidence "
                "about. A hazard that can only understate is annotated instead."
            ),
            fixes=("re-measure once the hazard is resolved",),
        )

    caveats = []
    if run.get("hazards"):
        caveats.append("hazards on the measuring run: " + ", ".join(run["hazards"]))
    if run.get("suite_passed") is False:
        caveats.append(
            f"measured on a run where {run.get('failed', '?')} test(s) failed"
        )
    suffix = ("  (" + "; ".join(caveats) + ")") if caveats else ""

    below = {f: pct for f, pct in coverage.items() if float(pct) < floor}

    # The ratchet, asked LAST and only of what the floor already rejected.
    # Position is the whole of its safety. Everything above still applies in
    # full: a file with no coverage row never reaches here, so a recorded
    # baseline cannot buy a pass for a path the reporter stopped emitting, and
    # a file at or above the floor is never measured against a record at all.
    #
    # The floor is right for new code and wrong for old, and a single gate
    # cannot be both without being told which is which. A file months below
    # the floor blocks a two-line, fully covered, net-negative edit — so the
    # gate refuses exactly the change that improves it, and the only ways past
    # are to close a gap that was never this change's or to route around the
    # gate. A recorded number is the project saying out loud which shortfalls
    # it has already accounted for; nothing else relaxes, and the record may
    # only ever be met or beaten.
    baseline, unreadable = _coverage_baseline(ctx)
    if unreadable is not None:
        return unreadable
    below, ratcheted, regressed = _ratcheted_against_baseline(below, baseline, floor)

    # Whatever the floor decides, the gate reports what it declined to hold to
    # the floor. A pass that quietly narrowed its own scope is the vacuous
    # green this gate exists to prevent, and the narrowing is only reviewable
    # if it is written down where the verdict is read.
    aside = _excluded_detail(excluded)
    ratchet_aside = _ratchet_detail(ratcheted)
    if ratchet_aside:
        aside = f"{aside}  {ratchet_aside}" if aside else ratchet_aside

    if regressed:
        return Verdict.fail(
            f"{len(regressed)} file(s) fell below a recorded coverage baseline",
            detail=", ".join(regressed[:6])
            + ". This is the ratchet, not the floor: the shortfall itself is "
            "already accounted for, but a recorded number may only be met or "
            "beaten. Restore what the change removed — or, if the number is "
            "honestly lower because a repaired test stopped overstating it, "
            "lower the recorded baseline in the same commit, where the reason "
            "is reviewable."
            + (f"  {aside}" if aside else ""),
            fixes=(
                "restore the coverage the baseline recorded",
                "or lower the recorded baseline deliberately, with the reason",
            ),
        )

    if not below:
        held = len(coverage) - len(ratcheted)
        # Never "all N at or above the floor" when some of them are not. A
        # ratcheted file is a pass this gate GRANTED, not a number it read.
        return Verdict.clean(
            f"all {held} file(s) at or above {floor:g}%{suffix}"
            if not ratcheted
            else (
                f"{held} file(s) at or above {floor:g}%, {len(ratcheted)} below "
                f"it and at or above a recorded baseline{suffix}"
            ),
            detail=aside,
        )
    listed = ", ".join(f"{f} {pct:g}%" for f, pct in sorted(below.items())[:6])
    return Verdict.fail(
        f"{len(below)} file(s) below the {floor:g}% floor",
        detail=listed
        + ". Coverage is a floor, not proof — repairing an inert test can lower "
        "it while making the suite more honest, so read a drop before acting "
        "on it."
        + (f"  {aside}" if aside else ""),
        fixes=("add tests for the uncovered branches",),
    )


# -- privacy rule ------------------------------------------------------


@rule(
    "privacy.clean",
    summary="No personal or operational data in what is being written down.",
    events=("pre-tool-use",),
)
def _privacy_clean(ctx: RuleContext) -> Verdict:
    if not ctx.manifest.get("privacy.enabled", True):
        return Verdict.na("privacy scanning disabled")
    scanner = PrivacyScanner.from_manifest(ctx.manifest)
    scope = set(ctx.manifest.get("privacy.scope") or [])

    findings = []
    # Tracked separately from the findings: "nothing was in scope" and
    # "everything in scope was clean" are different facts, and reporting the
    # first as the second is how a gate comes to look green while inert.
    scanned: list[str] = []

    if ctx.is_edit:
        target = ctx.target_path
        content = _edit_content(ctx.tool_input)
        if target is not None and content and _in_privacy_scope(ctx, target, scope):
            scanned.append(str(target))
            findings.extend(scanner.scan_text(content, path=target))

    if "commit_messages" in scope:
        message = ctx.extra.get("commit_message")
        problem = None
        if not message and ctx.tool_name == "Bash":
            message, problem = _extract_commit_message(
                str(ctx.tool_input.get("command", "")), cwd=ctx.cwd
            )
        if problem:
            # Fail closed, per the do-not-relax convention every other
            # unanswerable question here follows (AUD-07/08): a message this
            # gate is configured to scan and cannot read is not a clean one.
            return Verdict.fail(
                "the commit message could not be scanned",
                detail=problem,
                fixes=("write the message inline with -m '<message>'",),
            )
        if message:
            scanned.append("commit message")
            findings.extend(scanner.scan_text(message))

    if not scanned:
        return Verdict.na("nothing in privacy scope was written here")
    if not findings:
        return Verdict.clean(f"scanned {', '.join(scanned)}: no matches")

    shown = "; ".join(f.describe() for f in findings[:6])
    return Verdict.fail(
        f"{len(findings)} personal-data match(es) in content being written down",
        detail=shown
        + ". Matches are shown redacted on purpose: a report that quotes what "
        "it found is a second copy of it.",
        fixes=(
            "describe by characteristics rather than identity",
            "resolve operational identifiers at runtime instead of writing them down",
            (
                f"if this is a false positive, add '{EXEMPTION_MARKER}' on the "
                "line — or, in markup with no inline comment, on a comment line "
                "of its own, which covers the rest of that line's block and the "
                "whole of the next one, and nothing beyond"
            ),
        ),
    )


# -- memory rules ------------------------------------------------------
#
# All four hang off ``stop`` and nothing else. They were bound to no event and
# named by no machine gate, so no code path reached them: instrumenting the
# whole suite found them only ever skipping, which made them the four
# counterexamples to the README's promise that a gate is a function with a
# fixture proving it fails on a known-positive input (AUD-14).
#
# ``stop`` is where they belong rather than a pipeline gate. Memory hygiene is
# a property of a knowledge store that every session shares, not of the change
# under review — holding a merge hostage to a stale index in a file the branch
# never touched would be a gate answering about someone else's work. Turn end
# is when drift is worth naming and cheap to fix.


@rule("memory.schema_valid", summary="Every memory file parses.", events=("stop",))
def _memory_schema_valid(ctx: RuleContext) -> Verdict:
    return _memory_check(ctx, "schema")


@rule(
    "memory.index_current",
    summary="The memory index matches the directory.",
    events=("stop",),
)
def _memory_index_current(ctx: RuleContext) -> Verdict:
    return _memory_check(ctx, "index")


@rule("memory.no_dead_links", summary="Every memory link resolves.", events=("stop",))
def _memory_no_dead_links(ctx: RuleContext) -> Verdict:
    return _memory_check(ctx, "links")


@rule(
    "memory.no_duplicates",
    summary="No two memories cover the same fact.",
    events=("stop",),
)
def _memory_no_duplicates(ctx: RuleContext) -> Verdict:
    return _memory_check(ctx, "duplicates")


def _memory_check(ctx: RuleContext, aspect: str) -> Verdict:
    if ctx.payload.get("stop_hook_active"):
        return Verdict.na("already raised this turn")
    if not ctx.manifest.get("memory.managed", True):
        return Verdict.na("memory is not managed by this library")
    from ..domains.memorystore import MemoryStore  # local: avoids a cycle

    store = MemoryStore.from_manifest(ctx.manifest)
    if not store.root.exists():
        return Verdict.na("no memory directory")
    report = store.check()
    problems = getattr(report, aspect)
    if not problems:
        return Verdict.clean(f"{aspect}: clean ({report.count} memories)")
    return Verdict.fail(
        f"{aspect}: {len(problems)} problem(s)",
        detail="; ".join(problems[:6]),
        fixes=("invariant memory check", "invariant memory index"),
    )


# -- work-item rules ---------------------------------------------------


@rule(
    "item.bug_reproduced_before_fix",
    summary="A bug is proven real before it is fixed.",
    events=(),
)
def _bug_reproduced_before_fix(ctx: RuleContext) -> Verdict:
    item = ctx.extra.get("item")
    if not item or item.get("type") != "bug":
        return Verdict.na("not a bug transition")
    target = ctx.extra.get("target_status")
    if target != "fixed":
        return Verdict.na()
    history = [h.get("to") for h in item.get("history", [])]
    if "reproduced" in history or item.get("status") == "reproduced":
        return Verdict.clean("a reproduction is on record")
    return Verdict.fail(
        "this bug has never been reproduced",
        detail="A fix for an unreproduced defect proves nothing: a red test "
        "proves your mechanism is real, never that it is the one that was "
        "reported. Without one, neither claim is available.",
        fixes=("invariant item transition <id> reproduced",),
    )


@rule(
    "item.residue_matches_status",
    summary="A 'done' item does not still owe work.",
    events=(),
)
def _residue_matches_status(ctx: RuleContext) -> Verdict:
    item = ctx.extra.get("item")
    if not item:
        return Verdict.na()
    target = ctx.extra.get("target_status") or item.get("status")
    residue = [r for r in (item.get("residue") or []) if r]
    if target in {"closed", "done"} and residue:
        return Verdict.fail(
            f"status {target!r} but {len(residue)} item(s) still owed",
            detail="; ".join(residue[:4])
            + ". Prose and status must agree on what was proven; a close that "
            "hides an owed leg claims more than was demonstrated.",
            fixes=(
                "invariant item residue <id> --clear <text>",
                "transition to 'verified' instead and keep the residue visible",
            ),
        )
    return Verdict.clean("status and residue agree")


@rule(
    "item.history_matches_status",
    summary="An item's status is explained by its own recorded history.",
    events=("stop",),
)
def _history_matches_status(ctx: RuleContext) -> Verdict:
    """Drift detection over a store no gate can sit in front of.

    The item store is data, and data is exempt from session isolation — a
    deliberate decision, and the right one: every session touches the backlog,
    and routing it through a branch produces conflicts with no review value.
    But the exemption has a cost the FSM cannot pay for. ``filed -> fixed`` is
    an edge the bug machine does not have, and ``item.bug_reproduced_before_fix``
    blocks it; both are enforced only for callers who go through the CLI.
    Seven bugs in this library's own backlog reached ``fixed`` with
    ``history: []``, by writing the file (AUD-10). The machine was never asked,
    so it never refused, and nothing was recorded as having been skipped.

    A gate cannot close that — there is no transition to attach it to. What can
    is re-deriving the status from the history and reporting the ones that
    disagree, which is the same shape as the memory domain's index check: not
    "you may not do this" but "this store no longer says what it means".

    A ``forced`` entry is not drift. An override went through the machine, was
    refused, and recorded a written reason; flagging it would report the audit
    trail working as if it were the failure.
    """
    if ctx.payload.get("stop_hook_active"):
        return Verdict.na("already raised this turn")

    try:
        items = backend_for(ctx.manifest).load_all()
    except (InvariantError, OSError) as exc:
        return Verdict.na(f"the item store could not be read: {exc}")
    if not items:
        return Verdict.na("no work items")

    machines: dict[str, Any] = {}
    problems: list[str] = []
    checked = 0
    for item in items:
        if item.type not in machines:
            # A project may keep other kinds of entry in the same store. This
            # rule can only speak for the types it has a machine for, and a
            # missing machine is that answer rather than a finding.
            try:
                machines[item.type] = load_item_machine(item.type, ctx.manifest)
            except (InvariantError, OSError):
                machines[item.type] = None
        machine = machines[item.type]
        if machine is None:
            continue
        checked += 1
        problems.extend(_history_problems(item, machine))

    if not checked:
        return Verdict.na("no items of a type this library has a machine for")
    if not problems:
        return Verdict.clean(f"{checked} item(s): every status is explained")
    return Verdict.fail(
        f"{len(problems)} item(s) hold a status their history does not explain",
        detail="; ".join(problems[:6]),
        fixes=(
            "invariant item show <id>   # read what the record actually says",
            "record the missing transitions, or correct the status to what was proven",
        ),
    )


def _history_problems(item, machine) -> list[str]:
    """Walk the recorded history and report where it stops explaining status."""
    at = machine.initial
    for entry in item.history or []:
        target = str(entry.get("to") or "")
        if not target:
            continue
        source = str(entry.get("from") or at)
        if entry.get("forced"):
            at = target
            continue
        if source != at:
            return [f"{item.id}: history resumes at {source!r} after {at!r}"]
        if not machine.can(source, target):
            unknown = (
                f"{item.id}: history records the edge {source} -> {target}, "
                f"which the {machine.id} machine does not have"
            )
            return [unknown]
        at = target
    if at != item.status:
        if not item.history:
            unrecorded = (
                f"{item.id}: status {item.status!r} with no recorded history "
                f"(the machine starts at {machine.initial!r})"
            )
            return [unrecorded]
        return [f"{item.id}: status {item.status!r} but its history ends at {at!r}"]
    return []


# -- helpers -----------------------------------------------------------


@dataclass(frozen=True)
class _ChangedSet:
    """What this change touched, and whether that could be established at all.

    ``answerable`` is the part that cannot be inferred from ``paths``. An
    empty set means "nothing was touched" only when something was in a
    position to say so; otherwise it means "nobody could tell", and the two
    demand opposite handling. A caller reading the second as the first turns
    an unanswerable query into an open gate — see ``git.branch_files``.
    """

    paths: tuple[Path, ...]
    answerable: bool
    base: str


def _changed_set(ctx: RuleContext) -> _ChangedSet:
    """Every path this change touched, from the three sources that can know.

    Three, because each one alone goes empty in a state the others survive:

    - the working tree and the index answer for work in progress, and a
      commit empties both — so at an END-OF-PHASE gate, where the work is
      committed by construction, this source alone always says "nothing";
    - the branch's own commits answer for exactly that state, and answer
      ``None`` when there is no base, an unknown ref, or an unreadable
      repository;
    - the session's DECLARED file set answers only for sessions that declare
      one, which is a habit a project acquires rather than has — and is the
      only carrier left when ``branch_files`` returns ``None``.

    This union lived only inside ``coverage.meets_floor`` while
    ``docs.updated`` and ``docs.diagrams_current`` read the working tree
    alone, so the two docs-first gates went inert on every committed tree —
    BUG_012. The insight was not missing, it was unpropagated; sharing one
    implementation is what stops a fourth rule asking the question a fourth
    way.

    The base is read WITHOUT the ``or "master"`` fallback that
    ``session.work_would_be_stranded`` applies to the same field, and that
    difference is deliberate rather than an oversight — see the comment at
    that rule.
    """
    declared = [Path(p) for p in (ctx.session or {}).get("files") or []]
    base = str((ctx.session or {}).get("base") or "")
    committed = git.branch_files(ctx.cwd, base)
    seen: dict[str, Path] = {}
    for path in [*git.changed_files(ctx.cwd), *(committed or []), *declared]:
        seen[str(path)] = Path(path)
    return _ChangedSet(
        paths=tuple(sorted(seen.values(), key=str)),
        # An empty union is a real answer only if some source was able to
        # give one. Both legs that can fail have failed when the commits are
        # unreadable AND nothing was declared; the working tree cannot be
        # told apart from a clean one, so it never counts as an answer here.
        answerable=committed is not None or bool(declared),
        base=base,
    )


def _unestablished_changed_set(changed: _ChangedSet) -> Verdict:
    """The verdict for "the question could not be asked".

    Never a skip. A gate that reports an unanswerable query as "nothing to
    check" is the defect BUG_012 records, reintroduced somewhere new.
    """
    return Verdict.fail(
        "the changed set could not be established",
        detail=(
            "The working tree is clean, no file set was declared, and the "
            "branch's commits could not be read"
            + (
                f" against base {changed.base!r}"
                if changed.base
                else " — no base recorded"
            )
            + ". An unanswered question is not a negative answer, and reading "
            "it as one is how this gate comes to look green while inert."
        ),
        fixes=(
            "invariant session files add <path>",
            "or check the session's base branch is reachable",
        ),
    )


def _repo_root(ctx: RuleContext) -> Path:
    """The working tree the rule is actually running against.

    Not the same thing as ``manifest.root``: a session works in a linked
    worktree, and a rule that resolves changed paths against the checkout the
    manifest was found in reads a different copy of the file than the one that
    changed. Multi-worktree is the normal case for this library, so the git
    top-level wins whenever the two disagree.
    """
    return git.toplevel(ctx.cwd) or ctx.manifest.root


def _docs_root(ctx: RuleContext) -> Path:
    """The docs directory inside the working tree the rule is running against."""
    configured = ctx.manifest.path_for("docs")
    try:
        relative = configured.relative_to(ctx.manifest.root)
    except ValueError:
        return configured  # an absolute path outside the project: use as given
    return (_repo_root(ctx) / relative).resolve()


def _is_outside_project(ctx: RuleContext, target: Path | None) -> bool:
    """True when the path belongs to a different repository entirely.

    A host runs one project's hooks for every edit in the session, including
    edits to a sibling checkout. Session and pipeline rules answer from state
    alone, so without this they refuse work in an unrelated repository and the
    only escape is to disable them — which disables them for the project they
    were meant to govern too.

    Errs toward governing: unless the path is provably in another repository,
    it is treated as in scope.
    """
    if target is None:
        return False
    try:
        absolute = Path(target).resolve()
    except OSError:
        return False

    for base in (ctx.manifest.root, git.toplevel(ctx.cwd), git.main_checkout(ctx.cwd)):
        if base is None:
            continue
        try:
            base = Path(base).resolve()
        except OSError:
            continue
        if absolute == base or base in absolute.parents:
            return False

    for worktree in git.worktrees(ctx.cwd):
        try:
            path = worktree.path.resolve()
        except OSError:
            continue
        if absolute == path or path in absolute.parents:
            return False

    return True


def _is_exempt(ctx: RuleContext, target: Path | None) -> bool:
    """Paths a project has declared exempt from session isolation.

    Configuration and knowledge files are the motivating case: they are data
    rather than code, they are edited by every session, and routing them
    through a branch produces conflicts with no review value.
    """
    if target is None:
        return False
    patterns = list(ctx.manifest.get("session.exempt_paths") or [])

    # The work-item store is exempt for the same stated reason as config and
    # memory: it is data every session touches, and routing it through a branch
    # produces conflicts with no review value. It is not in the default list
    # because its path is configurable — the library knows where it put it, so
    # it adds it here rather than asking every project to repeat it.
    #
    # A project that wants backlog edits reviewed can say so with
    # items.exempt: false.
    if ctx.manifest.get("items.exempt", True):
        items_path = ctx.manifest.get("items.path")
        if items_path:
            patterns.extend([str(items_path), f"{str(items_path).rstrip('/')}/**"])

    # The report store, for the same reason and by the same mechanism (ADR-002
    # D4). The write scope is "the state root and the stores the manifest
    # declares", so a declared store is writable by construction rather than by
    # being added to a list of exceptions — the alternative turns a crisp
    # guarantee into an enumeration that grows on every feature.
    #
    # The report is a GENERATED artifact besides: a session that regenerates it
    # is running a command, not authoring code, and blocking that would make
    # the isolation rule refuse the one edit it has no review value over.
    reports_path = ctx.manifest.get("paths.reports")
    if reports_path:
        patterns.extend([str(reports_path), f"{str(reports_path).rstrip('/')}/**"])

    if not patterns:
        return False

    for rel in _project_relative_forms(ctx, target):
        if any(fnmatch.fnmatch(rel, pattern) for pattern in patterns):
            return True
    return False


def _coverage_roots(ctx: RuleContext) -> list[Path]:
    """Every root a measured path could be relative to, best first.

    An isolated session has TWO. State lives in the main checkout, because
    worktrees share one ``.git``, while compilation happens in the session's
    worktree — so the absolute paths a coverage tool emits carry the WORKTREE
    prefix, and the rule evaluating them may well be running from the main
    checkout, because that is where hooks run from. Stripping only the root the
    rule happens to see removes nothing, the relative form never appears, and
    the intersection is empty for a reason that has nothing to do with
    coverage. The session record names its own worktree, which is the one fact
    here that does not depend on who is asking.
    """
    roots: list[Path] = []
    worktree = (ctx.session or {}).get("worktree")
    if worktree:
        roots.append(Path(worktree))
    roots.append(_repo_root(ctx))
    roots.append(ctx.manifest.root)
    return roots


def _nothing_a_run_could_measure(
    ctx: RuleContext,
    recorded: dict,
    source_changed: set[str],
    roots: list[Path],
) -> Verdict | None:
    """N/A when no touched file could ever carry a coverage row, else ``None``.

    Answers the one question about an unmeasured change that does not need a
    measurement. ``coverage.meets_floor`` already declines to hold a file to
    the floor when something positively says the file cannot produce a row —
    deleted by this change, outside the reporter's configured scope, a test
    file, free of executable lines, or not text at all. That verdict simply
    sat behind the
    "no coverage was recorded" guard, so a change touching only such files
    could never reach it.

    The demand that created was not merely redundant, it was destructive.
    ``invariant pipeline coverage`` takes no selector, so the only way to
    satisfy the guard is a whole-scheme run; a coverage run is recorded as a
    TEST run; and on a project whose suite is not green that banks a failing
    entry and flips ``test.all_pass`` from green to red — all to produce
    numbers the rule then discards, having excluded every one of those files
    from the floor anyway. A gate whose only remedy destroys a passing gate is
    not strict, it is a gate people route around.

    Returns ``None`` the moment ONE file could have had a row, so the ordinary
    "you forgot to run coverage" refusal is untouched. Only positive evidence
    unlocks the skip; every "cannot tell" keeps blocking, exactly as
    :func:`_explain_absent_rows` already decides for the measured path.
    """
    if recorded:
        return None
    set_aside, still_owed = _explain_absent_rows(ctx, sorted(source_changed), roots)
    if still_owed:
        return None
    return Verdict.na(
        "no touched source file is one a coverage run could measure",
        detail=_excluded_detail(set_aside),
    )


def _explain_absent_rows(
    ctx: RuleContext, missing: list[str], roots: list[Path]
) -> tuple[list[str], list[str]]:
    """Split "no row could exist" from "no row was produced".

    Returns ``(excluded, unexplained)``. Only the first is set aside, and only
    where something positively says so: git reporting the path deleted, an
    adapter reporting the file carries no executable lines, or the file's own
    bytes proving it is not text at all. Everything else —
    including every "cannot tell" — stays in the second list and keeps
    blocking, because the two mistakes are not symmetrical. Calling a live
    file inert passes unmeasured code silently; calling an inert file live
    costs a spurious block, which is visible and sends someone to look.
    """
    if not missing:
        return [], []

    # Deletions are read from the SESSION's worktree when it records one, for
    # the same reason `_coverage_roots` prefers it: the commits that removed
    # the file are on the session's branch, and the rule itself may well be
    # running from the main checkout, where that branch is not checked out.
    base = str((ctx.session or {}).get("base") or "")
    deleted = git.deleted_files(_session_worktree(ctx) or ctx.cwd, base) or []
    deleted_forms = [_relative_to_any(path, roots) for path in deleted]
    adapters = _ecosystem_adapters(ctx)

    excluded: list[str] = []
    unexplained: list[str] = []
    for rel in missing:
        located = _first_existing(rel, roots)
        if located is None:
            # Absence alone proves nothing — a misspelt path is absent too.
            # Git has to say the change removed it.
            if any(_same_file(rel, form) for form in deleted_forms):
                excluded.append(f"{rel} (deleted by this change)")
            else:
                unexplained.append(rel)
            continue
        root, found = located
        # Asked before the per-file question because it answers a different
        # one: not "has this file anything to measure" but "was the reporter
        # ever pointed at it". A test file answers YES to the first and NO to
        # the second, which is why the first alone could not settle BUG_019 —
        # and why overriding `carries_executable_code` to say a test file has
        # no code would have been a lie told to relax a gate.
        if _outside_measured_scope(adapters, root, found):
            excluded.append(f"{rel} (outside the coverage reporter's configured scope)")
        # The conventional half of the same question, for a project whose
        # coverage configuration the adapter could not read: a test file is
        # what measures, not what is measured, and the usual configuration is
        # defined never to produce a row for one. Asked on the repo-relative
        # spelling, not on `found`: the directory convention is what carries
        # the answer, and an absolute path can pick up a stray `tests`
        # component from somewhere above the project root.
        elif _is_test_file(adapters, Path(rel)) is True:
            excluded.append(f"{rel} (a test file; not what a coverage floor measures)")
        elif _carries_executable_code(adapters, found) is False:
            excluded.append(f"{rel} (no executable lines to measure)")
        # Asked last, so an adapter that HAS an opinion about the file always
        # answers first. This one is the fact no adapter is positioned to
        # state: both hooks above are keyed on a language suffix and return
        # `None` for anything that is not source, so a committed asset — a
        # golden PNG from a snapshot suite is the case that surfaced it — was
        # unanswerable by every branch and blocked forever, with no test
        # anybody could write to clear it. Bytes that are not text carry no
        # executable lines in any ecosystem, which is the same shape of
        # positive evidence the branches above demand rather than a relaxation
        # of what they demand.
        elif _is_binary_file(found):
            excluded.append(f"{rel} (a binary file; no executable lines to measure)")
        else:
            unexplained.append(rel)
    return excluded, unexplained


def _excluded_by_manifest_globs(
    ctx: RuleContext, touched: dict[str, float]
) -> tuple[dict[str, float], list[str]]:
    """Hold the manifest's `coverage_exclude` paths out of the floor.

    A POLICY, not a measurement, and the split is the point: the manifest says
    out loud which paths this project has decided not to hold to a production
    floor, and the verdict repeats it back with the number still attached. The
    alternative this replaces is a rule that quietly knows about test
    directories, which is the same decision taken by nobody and reviewable
    nowhere.

    The number rides along in the exclusion text on purpose. A path dropped
    from a floor is still measured, and a suite that stops executing shows up
    as a collapsing percentage next to its own exemption instead of vanishing
    from the verdict entirely.
    """
    patterns = ctx.manifest.get("methodology.gates.coverage_exclude") or []
    if not patterns:
        return touched, []

    kept: dict[str, float] = {}
    excluded: list[str] = []
    for rel, pct in touched.items():
        matched = next(
            (p for p in patterns if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, f"{p}/*")),
            None,
        )
        if matched is None:
            kept[rel] = pct
        else:
            excluded.append(f"{rel} {float(pct):g}% (coverage_exclude {matched!r})")
    return kept, excluded


def _excluded_as_comment_only(
    ctx: RuleContext, touched: dict[str, float], roots: list[Path]
) -> tuple[dict[str, float], list[str]]:
    """Hold out files this change edited without touching a single code line.

    A FACT about the change, not a policy about the path. Coverage is a
    property of executable lines; a diff that rewrote only comments moved no
    number and can move none, so the floor has nothing about it to adjudicate
    and the work it demands cannot affect the thing demanded. That is the
    shape of a gate people force past — and it arrives constantly on exactly
    the changes that deserve the least friction, because a change that DELETES
    a symbol must rewrite every comment that cited it.

    Positive evidence only, and it takes all four of: a base revision to
    compare against, both revisions readable, an adapter that can reduce this
    language to its executable text, and the two reductions equal. Any
    "cannot tell" leaves the file in the floor, per the asymmetry every one of
    these answers carries: a spurious block sends someone to look, a wrongly
    excused file passes unmeasured code in silence.
    """
    base = str((ctx.session or {}).get("base") or "")
    if not base or not touched:
        return touched, []

    cwd = _session_worktree(ctx) or ctx.cwd
    adapters = _ecosystem_adapters(ctx)
    if not adapters:
        return touched, []

    kept: dict[str, float] = {}
    excluded: list[str] = []
    for rel, pct in touched.items():
        located = _first_existing(rel, roots)
        before = git.file_at(cwd, base, rel)
        if located is None or before is None:
            kept[rel] = pct
            continue
        try:
            after = located[1].read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError):
            kept[rel] = pct
            continue
        reduced_before = _executable_text(adapters, before)
        reduced_after = _executable_text(adapters, after)
        if (
            reduced_before is not None
            and reduced_after is not None
            and reduced_before == reduced_after
        ):
            excluded.append(f"{rel} {float(pct):g}% (this change altered no code in it)")
        else:
            kept[rel] = pct
    return kept, excluded


#: How far under a recorded baseline still counts as "at" it. Half of the last
#: decimal place a baseline is written with, because a reporter that emits
#: 70.98999 for the number recorded as 70.99 is stating the same measurement,
#: and a ratchet that fired on that would report a regression of zero points.
#: Deliberately smaller than any drop a real change produces.
_BASELINE_TOLERANCE = 0.005


def _coverage_baseline(
    ctx: RuleContext,
) -> tuple[dict[str, float] | None, Verdict | None]:
    """The recorded per-file shortfall the floor may be ratcheted against.

    Returns ``(baseline, None)`` when the manifest names one and it reads, or
    ``(None, None)`` when it names none — the default, under which this whole
    mechanism is absent and the floor behaves exactly as it did before.
    Anything else returns ``(None, verdict)`` and the caller fails the gate on
    it, which is the entire discipline of the format.

    A baseline is EVIDENCE that a shortfall predates the change being judged.
    Evidence read only in part is worse than none: a line the parser cannot
    make sense of would drop an entry, and a dropped entry is invisible in
    both directions — the debt it recorded silently stops being enforced, and
    the file it named silently stops being forgiven. That is the grandfathered
    baseline that hides a live defect, so a line that will not parse, an entry
    recorded twice, and a file the manifest names but no root holds are all
    errors rather than a smaller record.

    Liberal about the shape of a line, because ragged whitespace and a
    trailing note are how a human maintains a file like this: any run of
    whitespace separates the two fields, ``#`` starts a comment anywhere, and
    blank lines are nothing.
    """
    configured = ctx.manifest.get("methodology.gates.coverage_baseline")
    if not configured:
        return None, None

    located = _first_existing(str(configured), _coverage_roots(ctx))
    if located is None:
        return None, Verdict.fail(
            "the recorded coverage baseline is missing",
            detail=(
                f"methodology.gates.coverage_baseline names {configured!r}, "
                "which no candidate root holds. An absent file is not an empty "
                "record — it cannot be told apart from a ratchet that was "
                "never wired, so the gate will not read either a relaxation or "
                "its absence out of a file it could not find. The manifest key "
                "and the file it names belong in the same commit."
            ),
            fixes=(
                f"create {configured}",
                "or clear methodology.gates.coverage_baseline",
            ),
        )

    try:
        text = located[1].read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, Verdict.fail(
            "the recorded coverage baseline could not be read",
            detail=f"{configured}: {exc}.",
            fixes=(f"check {configured} is readable UTF-8 text",),
        )

    def unreadable(number: int, line: str, why: str) -> Verdict:
        return Verdict.fail(
            "the recorded coverage baseline could not be read",
            detail=(
                f"{configured} line {number}: {line.strip()!r} — {why}. "
                "Each entry is '<repo-relative-path> <pct>'. The gate will not "
                "answer from a record it read only part of: the entry it "
                "dropped would stop being enforced AND stop being honoured, "
                "with nothing in any verdict to say so."
            ),
            fixes=(f"fix or delete {configured} line {number}",),
        )

    baseline: dict[str, float] = {}
    seen: dict[str, int] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 2:
            return None, unreadable(number, raw, "expected exactly two fields")
        rel, recorded = fields
        try:
            pct = float(recorded)
        except ValueError:
            return None, unreadable(number, raw, f"{recorded!r} is not a number")
        if not 0.0 <= pct <= 100.0:
            return None, unreadable(number, raw, f"{pct:g} is not a percentage")
        if rel in seen:
            return None, unreadable(
                number, raw, f"already recorded on line {seen[rel]}"
            )
        seen[rel] = number
        baseline[rel] = pct
    return baseline, None


def _ratcheted_against_baseline(
    below: dict[str, float], baseline: dict[str, float] | None, floor: float
) -> tuple[dict[str, float], list[str], list[str]]:
    """Split a below-floor set by what the recorded baseline says about it.

    Returns ``(still_below, ratcheted, regressed)``. Asked ONLY of files the
    floor has already rejected, and that restriction is the mechanism: a file
    at or above the floor is never measured against a record, or a stale entry
    would quietly become a second and higher floor nobody agreed to.

    Three answers, and they are three different claims. At or above the record
    is a PRE-EXISTING shortfall this change did not create and, at 70.99
    against a recorded 69.17, has just reduced — held out of the floor with
    both numbers attached, exactly as :func:`_excluded_by_manifest_globs`
    holds out a policy path. Below the record is a REGRESSION and gets its own
    sentence, because "you are below the floor" sends an author to write
    several hundred lines of test for a gap that predates them while "you lost
    four points against a number this project recorded" sends them to the
    lines they just deleted. No record at all keeps the original refusal
    untouched: an absent entry is the ordinary case for every file in the
    project, so reading it as permission would turn a ratchet into an amnesty.
    """
    if not baseline:
        return below, [], []

    still: dict[str, float] = {}
    ratcheted: list[str] = []
    regressed: list[str] = []
    for rel, measured in sorted(below.items()):
        pct = float(measured)
        recorded = next(
            (v for k, v in baseline.items() if _same_file(k, rel)), None
        )
        if recorded is None:
            still[rel] = measured
        elif pct + _BASELINE_TOLERANCE >= recorded:
            ratcheted.append(
                f"{rel} {pct:g}% — below the {floor:g}% floor but at or above "
                f"its recorded baseline {recorded:g}% (pre-existing shortfall, "
                f"ratcheted)"
            )
        else:
            regressed.append(
                f"{rel} {pct:g}%, recorded baseline {recorded:g}% "
                f"(down {recorded - pct:g} point(s))"
            )
    return still, ratcheted, regressed


def _ratchet_detail(ratcheted: list[str]) -> str:
    if not ratcheted:
        return ""
    return (
        "Not held to the floor, because a recorded baseline says the shortfall "
        "predates this change: " + "; ".join(ratcheted) + "."
    )


def _executable_text(adapters: list[Adapter], source: str) -> str | None:
    """The first adapter with an opinion wins; no opinion answers ``None``."""
    for adapter in adapters:
        reduced = adapter.executable_text(source)
        if reduced is not None:
            return reduced
    return None


def _outside_measured_scope(adapters: list[Adapter], root: Path, path: Path) -> bool:
    """Is this file one the project's coverage reporter is never pointed at?

    Only ``True`` on a positive answer from an adapter that could resolve the
    whole scope. Every "cannot tell" — no adapter with an opinion, an
    unresolvable config — returns ``False`` and leaves the demand standing,
    because this answer is used to SHRINK what the gate asks for.

    The scope is asked of the root the file was actually found under. Roots
    come from `_coverage_roots`, which may list a session worktree and the main
    checkout, and a file is only outside a scope that was computed where the
    file lives.
    """
    for adapter in adapters:
        scope = adapter.measured_scope(root)
        if scope is None:
            continue
        return not any(_is_within(path, directory) for directory in scope)
    return False


#: How much of a file settles whether it is text. Bounded because the
#: alternative is reading an arbitrarily large asset to answer a question its
#: first kilobytes already answer, and 8 KiB is the prefix `git diff` itself
#: inspects for the same decision.
_TEXT_SNIFF_BYTES = 8192


def _is_binary_file(path: Path) -> bool:
    """Whether the file's own bytes prove it is not text.

    ``True`` only on a successful read whose content cannot be text. Every
    other outcome — a read that failed, an empty file, anything that decodes —
    returns ``False`` and leaves the demand standing, because this answer is
    used to SHRINK what the coverage floor asks for and the asymmetry
    :func:`_explain_absent_rows` documents applies here unchanged: calling a
    live file inert passes unmeasured code silently, while calling an inert
    file live costs a visible block.

    Two tells, and the second is the definition rather than the shortcut. A
    NUL byte cannot occur in UTF-8 text and settles the common formats
    immediately; a prefix that will not decode settles the rest, including the
    formats that happen to carry no NUL early on. The decode is INCREMENTAL on
    purpose — a bounded read can cut a multi-byte character in half, and a
    decoder that reported that truncation as an error would call a live UTF-8
    source file binary, which is precisely the direction that must stay shut.
    """
    try:
        with path.open("rb") as handle:
            prefix = handle.read(_TEXT_SNIFF_BYTES)
    except (OSError, ValueError):
        return False  # cannot tell, so nothing is set aside
    if not prefix:
        return False  # an empty file proves nothing about what belongs in it
    if b"\x00" in prefix:
        return True
    try:
        codecs.getincrementaldecoder("utf-8")().decode(prefix, final=False)
    except UnicodeDecodeError:
        return True
    return False


def _excluded_detail(excluded: list[str]) -> str:
    if not excluded:
        return ""
    return (
        "Not held to the floor, because no coverage run could produce a row "
        "for them: " + "; ".join(excluded) + "."
    )


def _session_worktree(ctx: RuleContext) -> Path | None:
    recorded = (ctx.session or {}).get("worktree")
    if not recorded:
        return None
    path = Path(recorded)
    return path if path.exists() else None


def _first_existing(rel: str, roots: Iterable[Path]) -> tuple[Path, Path] | None:
    """The file named by a project-relative path, and the root that has it.

    The root is returned alongside because a caller asking an adapter about the
    file may need to ask it about the file's PROJECT too, and the roots are
    not interchangeable: a session worktree and the main checkout can hold
    different configurations of the same repository.
    """
    for root in roots:
        candidate = Path(root) / rel
        if candidate.is_file():
            return Path(root), candidate
    return None


def _ecosystem_adapters(ctx: RuleContext) -> list[Adapter]:
    return [get_adapter(eco) for eco in ctx.manifest.ecosystems]


def _carries_executable_code(adapters: list[Adapter], path: Path) -> bool | None:
    """The first adapter with an opinion answers; ``None`` if none has one.

    Adapters stack, and one that does not know a language returns ``None`` for
    it rather than a wrong answer — so asking them in the manifest's order and
    taking the first real answer is what makes a mixed-ecosystem project work.
    """
    for adapter in adapters:
        answer = adapter.carries_executable_code(path)
        if answer is not None:
            return answer
    return None


def _is_test_file(adapters: list[Adapter], path: Path) -> bool | None:
    """Same stacking rule as :func:`_carries_executable_code`, same reason.

    Kept a separate question rather than folded into that one: a test file is
    full of executable lines, so answering ``False`` there to get this
    relaxation would assert the thing the base docstring names as the
    dangerous direction. What a coverage floor asks about a test file is not
    "can it be measured" but "is it what we are measuring" (BUG_019).
    """
    for adapter in adapters:
        answer = adapter.is_test_file(path)
        if answer is not None:
            return answer
    return None


#: A real `git push`, not the words "git push" occurring in a command. The
#: substring form refused a heredoc that merely WROTE those words into a test
#: file — a gate blocking work that was not the thing it guards teaches people
#: to route around it, which is the one outcome it cannot afford.
_GIT_PUSH = re.compile(r"\bgit\b[^|;&]*?\bpush\b(?![-\w])")


#: A real `git merge` invocation. `\bmerge\b` alone also matches `merge-base`,
#: `--no-merges`, and any prose containing the word — the same substring
#: over-match that made the push barrier refuse a heredoc that merely WROTE
#: "git push".
_GIT_MERGE = re.compile(r"\bgit\b[^|;&]*?\bmerge\b(?![-\w])")


def _merge_target(command: str) -> str | None:
    """The ref a ``git merge`` names, or ``None`` if this is not one."""
    match = _GIT_MERGE.search(command or "")
    if match is None:
        return None
    rest = command[match.end() :]
    for token in rest.split():
        if token.startswith("-"):
            continue
        if token in {"|", "&&", ";", "||"}:
            break
        return token.strip("'\"")
    return ""  # `git merge` with no ref merges the upstream, never itself


def _short_ref(ref: str) -> str:
    """`origin/session/x` and `session/x` name the same branch here."""
    cleaned = (ref or "").strip().removeprefix("refs/heads/").removeprefix("refs/remotes/")
    for remote_prefix in ("origin/", "upstream/"):
        if cleaned.startswith(remote_prefix):
            cleaned = cleaned[len(remote_prefix) :]
    return cleaned


def _relative_to_root(path, root: Path) -> str:
    """One spelling for a path, so two sources can be compared at all."""
    return _relative_to_any(path, [root])


def _relative_to_any(path, roots: Iterable[Path]) -> str:
    """The shortest project-relative spelling under any candidate root."""
    candidate = Path(str(path))
    if not candidate.is_absolute():
        return str(candidate)
    try:
        resolved = candidate.resolve()
    except OSError:
        return str(candidate)
    for root in roots:
        try:
            return str(resolved.relative_to(Path(root).resolve()))
        except (ValueError, OSError):
            continue
    return str(resolved)


def _same_file(declared: str, measured: str) -> bool:
    """Whether two spellings name the same file.

    Suffix matching is the belt to the braces above: it survives a root this
    code did not think of. It is deliberately paired with "no overlap is a
    failure" — a loose matcher's bug produces PARTIAL intersections rather than
    empty ones, and a partial intersection that passed silently would be the
    same vacuous green one layer down.
    """
    if declared == measured:
        return True
    return measured.endswith("/" + declared) or declared.endswith("/" + measured)


#: Paths whose coverage nobody measures. Counting them as "changed source"
#: would make the no-overlap check fire on a docs-only edit.
_UNMEASURED_SUFFIXES = (".md", ".rst", ".txt", ".yaml", ".yml", ".json", ".toml")


def _looks_like_docs(path: str) -> bool:
    return str(path).lower().endswith(_UNMEASURED_SUFFIXES)


def _project_relative_forms(ctx: RuleContext, target: Path) -> list[str]:
    """Every plausible project-relative spelling of ``target``.

    Exempt patterns are written project-relative (``.claude/**``), but the
    target may sit in any worktree of the repository — the main checkout, this
    session's worktree, or a peer's. Resolving against a single base produces an
    absolute path whenever the two disagree, and an absolute path matches no
    project-relative pattern, so the exemption silently stops applying exactly
    when several worktrees exist. That is the situation this library is for, so
    every known base is tried.

    A file inside a linked worktree is the exception: it is spelled against that
    worktree and nothing else. Otherwise the worktree's own location joins every
    path underneath it, and a worktree sitting under an exempt directory —
    which ``session.worktree_dir`` makes an ordinary configuration, since some
    hosts trust only a location inside the repository — would exempt every
    source file it holds. Since the caller matches with ``any()`` and the
    isolation rule consults the exemption FIRST, that reads as "nothing to
    enforce" rather than as an error: the guarantee would be off, silently, in
    exactly the multi-worktree setup it exists for.
    """
    try:
        absolute = Path(target).resolve()
    except OSError:
        return [str(target)]

    # Most specific first: worktrees may nest, and the innermost one owns the
    # file. The main checkout is skipped because it contains every other.
    owning = sorted(
        (w for w in git.worktrees(ctx.cwd) if not w.is_main),
        key=lambda w: len(str(w.path)),
        reverse=True,
    )
    for worktree in owning:
        try:
            path = worktree.path.resolve()
        except OSError:
            continue
        if absolute == path or path in absolute.parents:
            try:
                return [str(absolute.relative_to(path))]
            except ValueError:  # pragma: no cover - implied by the check above
                continue

    bases = [ctx.manifest.root, git.toplevel(ctx.cwd), git.main_checkout(ctx.cwd)]
    forms: list[str] = []
    for base in bases:
        if base is None:
            continue
        try:
            forms.append(str(absolute.relative_to(Path(base).resolve())))
        except (ValueError, OSError):
            continue

    # A path in a peer's worktree matches none of the bases above, and used to
    # be recovered by a second pass over the worktree list here. The pass above
    # subsumes it: a peer's worktree is a linked worktree, so such a path has
    # already returned its worktree-relative form and never reaches this point.
    return forms or [str(absolute)]


def _edit_content(tool_input: Mapping[str, Any]) -> str:
    """Everything an edit tool is about to write, whatever its payload shape.

    Each of the four ``EDIT_TOOLS`` spells its outgoing text differently:
    ``Write`` uses ``content``, ``Edit`` uses ``new_string``, ``NotebookEdit``
    uses ``new_source``, and ``MultiEdit`` carries a LIST of edits each with
    its own ``new_string``. Only the first two were read, so the same personal
    data that blocked a ``Write`` passed through a ``MultiEdit`` untouched —
    while the isolation rules fired on all four, which made the gap invisible
    (AUD-06).
    """
    pieces = [
        tool_input.get("content"),
        tool_input.get("new_string"),
        tool_input.get("new_source"),
    ]
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        pieces.extend(
            e.get("new_string") for e in edits if isinstance(e, Mapping)
        )
    return "\n".join(str(p) for p in pieces if p)


#: Scope entries with a meaning of their own; everything else in
#: ``privacy.scope`` is a project-relative path prefix (AUD-15).
_PRIVACY_SCOPE_KEYWORDS = {"all", "docs", "memory", "reports", "commit_messages"}

#: What each keyword resolves through. ``memory`` carries the generated index
#: too: the index is a concentrate of exactly the text the memory scope
#: protects, and it was the unscanned second copy (AUD-15).
_PRIVACY_SCOPE_PATHS = {
    "docs": ("paths.docs",),
    "memory": ("paths.memory", "paths.memory_index"),
    "reports": ("paths.reports",),
}


def _in_privacy_scope(ctx: RuleContext, target: Path, scope: set[str]) -> bool:
    """Whether this path is one the privacy gate is configured to scan.

    Compared in PROJECT-RELATIVE space rather than absolute space. Comparing
    absolute paths made scope depend on which worktree the caller happened to
    be in: a memory file in the main checkout fell outside a scope computed
    from a session worktree, and the gate reported nothing to check on exactly
    the files it exists to protect.
    """
    if "all" in scope:
        return True

    scoped_prefixes: list[str] = []
    for key in scope:
        for manifest_key in _PRIVACY_SCOPE_PATHS.get(key, ()):
            configured = ctx.manifest.get(manifest_key)
            if configured:
                scoped_prefixes.append(str(Path(configured)))
        if key not in _PRIVACY_SCOPE_KEYWORDS:
            # A literal project-relative prefix. This is how a project widens
            # scope beyond what paths.* names — e.g. all of ``docs/`` when
            # paths.docs points at one subtree of it (AUD-15).
            scoped_prefixes.append(str(Path(key)))

    if not scoped_prefixes:
        return False

    for rel in _project_relative_forms(ctx, target):
        rel_path = Path(rel)
        if rel_path.is_absolute():
            continue
        for prefix in scoped_prefixes:
            if rel == prefix or rel.startswith(prefix.rstrip("/") + "/"):
                return True
    return False


def _is_within(path: Path, root: Path) -> bool:
    try:
        resolved = Path(path).resolve()
        root = Path(root).resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _rel(path: Path, root: Path) -> str:
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return str(path)


#: A short-option cluster whose LAST letter takes a value — git's own rule for
#: combining flags, which is why ``-am <msg>`` means ``-a -m <msg>``. Matching
#: only the exact ``-m`` token let ``-am`` (the most common combined spelling)
#: carry a message past the scan unread (AUD-09).
_COMMIT_MESSAGE_CLUSTER = re.compile(r"-[a-zA-Z]*m$")
_COMMIT_FILE_CLUSTER = re.compile(r"-[a-zA-Z]*F$")


def _extract_commit_message(command: str, cwd: Path | None = None) -> tuple[str, str | None]:
    """The message a ``git commit`` invocation carries, or why it is unreadable.

    Returns ``(message, problem)``. ``problem`` is set when the command names
    a message this scan cannot read — a ``-F`` file that is absent at hook
    time, or ``-F -`` (stdin, which a hook never sees). The caller fails
    closed on it: the scan's whole subject exists and could not be looked at,
    and an unanswered question is not a pass (AUD-09).
    """
    import shlex

    if "git" not in command or "commit" not in command:
        return "", None
    try:
        tokens = shlex.split(command)
    except ValueError:
        return "", None
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token == "--message" or _COMMIT_MESSAGE_CLUSTER.fullmatch(token):
            return following, None
        if token.startswith("--message="):
            return token.split("=", 1)[1], None
        if token == "--file" or _COMMIT_FILE_CLUSTER.fullmatch(token):
            return _read_commit_message_file(following, cwd)
        if token.startswith("--file="):
            return _read_commit_message_file(token.split("=", 1)[1], cwd)
    return "", None


def _read_commit_message_file(name: str, cwd: Path | None) -> tuple[str, str | None]:
    """A ``git commit -F`` message, read the way git itself would."""
    if not name:
        return "", "-F names no file"
    if name == "-":
        return "", (
            "-F - reads the message from stdin, which a hook cannot see. "
            "The message would land in history unscanned."
        )
    path = Path(name)
    if not path.is_absolute():
        path = Path(cwd or Path.cwd()) / path
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError):
        return "", (
            f"-F names {name!r}, which could not be read at scan time. The "
            "message would land in history unscanned."
        )
