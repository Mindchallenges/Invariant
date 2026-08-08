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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from . import git
from .manifest import Level, Manifest
from .privacy import PrivacyScanner

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


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
        marker = {Level.BLOCK: "BLOCK", Level.WARN: "warn", Level.OFF: "off"}[self.level]
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
            return Verdict.fail(
                f"{len(ctx.ambiguous)} live sessions are registered here and "
                "this directory cannot say which one is editing",
                detail="Naming one resolves it; starting another does not — "
                "it adds a fourth candidate to the same question.",
                fixes=tuple(
                    f"INVARIANT_SESSION_ID={s.get('id')} invariant session "
                    f"isolate --execute  ({s.get('name')})"
                    for s in ctx.ambiguous
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
    if "git push" not in command and not ctx.extra.get("pushing"):
        return Verdict.na()
    if ctx.session is None:
        return Verdict.fail(
            "a push with no registered session cannot be counted against the "
            "one-push barrier",
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
    "session.ff_merge_target",
    summary="A fast-forward merge must be run from the main checkout.",
    events=("pre-tool-use",),
)
def _ff_merge_target(ctx: RuleContext) -> Verdict:
    command = str(ctx.tool_input.get("command", "")) if ctx.tool_name == "Bash" else ""
    if "merge" not in command and not ctx.extra.get("merging"):
        return Verdict.na()
    if not git.in_linked_worktree(ctx.cwd):
        return Verdict.clean("running from the main checkout")
    return Verdict.fail(
        "this merge is being run from inside a linked worktree",
        detail="Merging a branch while standing in that branch's own worktree "
        "merges it into itself, and the follow-up push then pushes an "
        "unchanged target. Both commands exit zero, so nothing reports the "
        "failure. Verify with 'git branch -r --contains' afterwards.",
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
    "session.teardown_complete",
    summary="A pushed session's worktree is removed.",
    events=(),
)
def _teardown_complete(ctx: RuleContext) -> Verdict:
    if ctx.session is None or ctx.session.get("state") != "pushed":
        return Verdict.na()
    worktree = ctx.session.get("worktree")
    if worktree and Path(worktree).exists():
        return Verdict.fail(
            "the session pushed but its worktree still exists",
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
        return Verdict.fail(
            f"the last test run failed ({last.get('failed', '?')} failing)",
            fixes=("fix the failures, then re-run",),
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
    coverage = ctx.evidence("coverage", {}) or {}
    if not coverage:
        return Verdict.fail(
            "no coverage was recorded",
            fixes=("run the configured coverage command",),
        )
    below = {f: pct for f, pct in coverage.items() if float(pct) < floor}
    if not below:
        return Verdict.clean(f"all {len(coverage)} file(s) at or above {floor:g}%")
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
    patterns = ctx.manifest.get("session.exempt_paths") or []
    if not patterns:
        return False

    for rel in _project_relative_forms(ctx, target):
        if any(fnmatch.fnmatch(rel, pattern) for pattern in patterns):
            return True
    return False


def _project_relative_forms(ctx: RuleContext, target: Path) -> list[str]:
    """Every plausible project-relative spelling of ``target``.

    Exempt patterns are written project-relative (``.claude/**``), but the
    target may sit in any worktree of the repository — the main checkout, this
    session's worktree, or a peer's. Resolving against a single base produces an
    absolute path whenever the two disagree, and an absolute path matches no
    project-relative pattern, so the exemption silently stops applying exactly
    when several worktrees exist. That is the situation this library is for, so
    every known base is tried.
    """
    try:
        absolute = Path(target).resolve()
    except OSError:
        return [str(target)]

    bases = [ctx.manifest.root, git.toplevel(ctx.cwd), git.main_checkout(ctx.cwd)]
    forms: list[str] = []
    for base in bases:
        if base is None:
            continue
        try:
            forms.append(str(absolute.relative_to(Path(base).resolve())))
        except (ValueError, OSError):
            continue

    # A path in a peer's worktree matches none of the bases above. Its
    # project-relative form is still recoverable from the worktree list.
    if not forms:
        for worktree in git.worktrees(ctx.cwd):
            try:
                forms.append(str(absolute.relative_to(worktree.path.resolve())))
            except (ValueError, OSError):
                continue

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
