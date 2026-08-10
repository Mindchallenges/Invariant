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

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import git
from .errors import InvariantError
from .machines import load_pipeline_machine, load_session_machine
from .manifest import Level, Manifest
from .privacy import PrivacyScanner

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

#: The terminal state that means "gave up", as opposed to "finished".
#:
#: Every shipped machine spells it this way. It is named rather than derived
#: because a machine records only THAT a state is terminal, not whether
#: arriving there was the point — and a rule about unfinished work has to tell
#: those apart.
ABANDONED = "abandoned"


def _terminal_states(loader, manifest) -> frozenset[str]:
    """A machine's terminal states, read from the machine itself.

    Copying the list into this module would let the two drift, and the copy
    would be the one nobody updates when a machine gains a state.
    """
    try:
        return loader(manifest).terminal
    except InvariantError:
        return frozenset()


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
    def clean(cls, message: str = "") -> "Verdict":
        return cls(ok=True, message=message)

    @classmethod
    def fail(
        cls, message: str, detail: str = "", fixes: Iterable[str] = ()
    ) -> "Verdict":
        return cls(ok=False, message=message, detail=detail, fixes=tuple(fixes))

    @classmethod
    def na(cls, message: str = "not applicable here") -> "Verdict":
        return cls(ok=True, message=message, skipped=True)


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
            marker = "skip"
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


@rule(
    "session.ff_merge_target",
    summary="A fast-forward merge must be run from the main checkout.",
    events=("pre-tool-use",),
)
def _ff_merge_target(ctx: RuleContext) -> Verdict:
    # The session-merge command: landing this branch on the base, which must
    # happen from the main checkout. Unchanged.
    if ctx.extra.get("merging"):
        if not git.in_linked_worktree(ctx.cwd):
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
    if not git.in_linked_worktree(ctx.cwd):
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
    staged = {str(p) for p in git.staged_files(ctx.cwd)}
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
    if ctx.session is None:
        return Verdict.na("no session")
    if ctx.session.get("state") == "registered":
        return Verdict.na("not isolated yet")
    name = str(ctx.session.get("name") or "")
    if name and not name.startswith("session/"):
        return Verdict.clean(f"named {name!r}")
    return Verdict.fail(
        "session has no task-derived name",
        detail="Several sessions on one machine are indistinguishable in a "
        "list when they are all named after their identifier.",
        fixes=("invariant session start <task-name>",),
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
    if state in _terminal_states(load_session_machine, ctx.manifest):
        return Verdict.na(f"session is {state}")

    # "The work is done" is a claim the pipeline makes, not one inferred from
    # the diff. Inferring it would guess, and would guess wrong on the session
    # that is mid-refactor with three commits down.
    pipeline_state = str((ctx.pipeline or {}).get("state") or "")
    if pipeline_state == ABANDONED:
        return Verdict.na("pipeline abandoned — nothing is being claimed as done")
    if pipeline_state not in _terminal_states(load_pipeline_machine, ctx.manifest):
        return Verdict.na(
            f"pipeline is {pipeline_state or 'not started'} — work is still in flight"
        )

    branch = str(ctx.session.get("branch") or "")
    if not branch:
        return Verdict.na("session owns no branch")
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
            "invariant session abandon    # if this work is deliberately dropped",
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
                "invariant session abandon    # if the work really is dropped",
            ),
        )

    try:
        machine = load_pipeline_machine(ctx.manifest)
    except InvariantError as exc:
        return Verdict.na(f"pipeline machine unreadable: {exc}")

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
    changed = git.changed_files(ctx.cwd)
    if not changed:
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
    docs_changed = [p for p in git.changed_files(ctx.cwd) if _is_within(root / p, docs_root)]
    if not docs_changed:
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


@rule("test.suite_runs", summary="The test suite executes at all.", events=())
def _test_suite_runs(ctx: RuleContext) -> Verdict:
    runs = ctx.evidence("test_runs", []) or []
    if not runs:
        return Verdict.fail(
            "the test suite has not been run in this pipeline",
            fixes=("invariant pipeline advance <state>  (runs the configured suite)",),
        )
    last = runs[-1]
    if last.get("executed") == 0:
        return Verdict.fail(
            "the last run executed zero tests",
            detail="A filter that matches nothing exits clean. A green run is "
            "only meaningful alongside a non-zero executed count.",
            fixes=("check the test selector",),
        )
    return Verdict.clean(f"last run executed {last.get('executed', 'some')} test(s)")


@rule(
    "test.new_test_fails",
    summary="A new test failed before the fix existed.",
    events=(),
)
def _test_new_test_fails(ctx: RuleContext) -> Verdict:
    runs = ctx.evidence("test_runs", []) or []
    red = [r for r in runs if r.get("phase") == "red"]
    if not red:
        return Verdict.fail(
            "no failing run was recorded before the implementation",
            detail="A test written after the fix proves the fix compiles, not "
            "that it addresses anything. A green repro has several readings and "
            "only one of them is 'fixed'.",
            fixes=("invariant pipeline advance red  (records the failing run)",),
        )
    if any(not r.get("passed", True) for r in red):
        return Verdict.clean("a red run is on record")
    return Verdict.fail(
        "the run recorded in the red phase passed",
        detail="A test that passes before the change cannot be evidence for it. "
        "Either it does not reach the defect, or the defect is not there.",
        fixes=("make the test fail on the reported symptom first",),
    )


@rule("test.all_pass", summary="The suite is green.", events=())
def _test_all_pass(ctx: RuleContext) -> Verdict:
    runs = ctx.evidence("test_runs", []) or []
    if not runs:
        return Verdict.fail(
            "no test run recorded", fixes=("run the configured test command",)
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
    if not recorded:
        return Verdict.fail(
            "no coverage was recorded",
            detail="The gate reads what a coverage run measured; nothing has "
            "measured anything in this pipeline.",
            fixes=("invariant pipeline coverage",),
        )

    # Reporters emit absolute paths; changed files are repo-relative. Comparing
    # the two spellings directly yields an empty intersection, and an empty
    # intersection would report "all files at or above the floor" — a gate
    # passing vacuously, which is worse than the blocking it replaced.
    roots = _coverage_roots(ctx)
    coverage = {_relative_to_any(path, roots): pct for path, pct in recorded.items()}

    scope = str(ctx.manifest.get("methodology.gates.coverage_scope", "touched"))
    if scope == "touched":
        # Three sources, because each one alone goes empty in a state the
        # others survive. The working tree and index answer for work in
        # progress and go empty on commit. The declared file set answers only
        # for sessions that declare one, which is a habit a project acquires
        # rather than has. The branch's own commits answer for exactly the
        # state this gate runs in — after the commit — and are what makes the
        # set non-empty when it matters most.
        declared = set((ctx.session or {}).get("files") or [])
        base = str((ctx.session or {}).get("base") or "")
        committed = git.branch_files(ctx.cwd, base)
        changed = {
            _relative_to_any(path, roots)
            for path in [*git.changed_files(ctx.cwd), *(committed or []), *declared]
        }
        source_changed = {p for p in changed if not _looks_like_docs(p)}

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
            if committed is None and not declared:
                return Verdict.fail(
                    "the touched set could not be established",
                    detail=(
                        "Scope is 'touched', but the working tree is clean, no "
                        "file set was declared, and the branch's commits could "
                        f"not be read{f' against base {base!r}' if base else ' — no base recorded'}. "
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

        if not touched:
            return Verdict.fail(
                "coverage was measured, but none of it covers the changed files",
                detail=(
                    f"{len(coverage)} file(s) measured, "
                    f"{len(source_changed)} changed, no overlap. Usually a "
                    f"path-spelling mismatch between the coverage reporter "
                    f"and git. Reporting this as a pass would be the gate "
                    f"approving a measurement of something else."
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
                ),
                fixes=(
                    "check these files are in the coverage target",
                    "or drop them from the session file set if they are not code",
                ),
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
    if not below:
        return Verdict.clean(
            f"all {len(coverage)} file(s) at or above {floor:g}%{suffix}"
        )
    listed = ", ".join(f"{f} {pct:g}%" for f, pct in sorted(below.items())[:6])
    return Verdict.fail(
        f"{len(below)} file(s) below the {floor:g}% floor",
        detail=listed
        + ". Coverage is a floor, not proof — repairing an inert test can lower "
        "it while making the suite more honest, so read a drop before acting "
        "on it.",
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
        content = ctx.tool_input.get("content") or ctx.tool_input.get("new_string")
        if target is not None and content and _in_privacy_scope(ctx, target, scope):
            scanned.append(str(target))
            findings.extend(scanner.scan_text(str(content), path=target))

    if "commit_messages" in scope:
        message = ctx.extra.get("commit_message")
        if not message and ctx.tool_name == "Bash":
            message = _extract_commit_message(str(ctx.tool_input.get("command", "")))
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
            "add 'invariant: allow-privacy' on the line if this is a false positive",
        ),
    )


# -- memory rules ------------------------------------------------------


@rule("memory.schema_valid", summary="Every memory file parses.", events=())
def _memory_schema_valid(ctx: RuleContext) -> Verdict:
    return _memory_check(ctx, "schema")


@rule("memory.index_current", summary="The memory index matches the directory.", events=())
def _memory_index_current(ctx: RuleContext) -> Verdict:
    return _memory_check(ctx, "index")


@rule("memory.no_dead_links", summary="Every memory link resolves.", events=())
def _memory_no_dead_links(ctx: RuleContext) -> Verdict:
    return _memory_check(ctx, "links")


@rule("memory.no_duplicates", summary="No two memories cover the same fact.", events=())
def _memory_no_duplicates(ctx: RuleContext) -> Verdict:
    return _memory_check(ctx, "duplicates")


def _memory_check(ctx: RuleContext, aspect: str) -> Verdict:
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


# -- helpers -----------------------------------------------------------


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
    for key in ("docs", "memory"):
        if key in scope:
            configured = ctx.manifest.get(f"paths.{key}")
            if configured:
                scoped_prefixes.append(str(Path(configured)))

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


def _extract_commit_message(command: str) -> str:
    """Pull the message out of a ``git commit -m`` invocation."""
    import shlex

    if "git" not in command or "commit" not in command:
        return ""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    for index, token in enumerate(tokens):
        if token in {"-m", "--message"} and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--message="):
            return token.split("=", 1)[1]
    return ""
