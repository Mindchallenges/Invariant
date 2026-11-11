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
import os
import posixpath
import re
import shlex
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from difflib import get_close_matches
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from ..adapters import Adapter, get_adapter
from ..adapters.base import Reference
from . import doclock, git
from . import findings as findings_reader
from . import guidelines as guidelines_reader
from . import plan as plan_reader
from . import sweep as sweep_reader
from . import verdicts as verdicts_reader
from .errors import InvariantError, ManifestError, StateError
from .items import backend_for
from .machines import load_item_machine, load_pipeline_machine, load_session_machine
from .manifest import MANIFEST_RELPATH, Level, Manifest, glob_matches, project_relative
from .privacy import EXEMPTION_MARKER, PrivacyScanner

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

#: Tools whose invocation means "a subagent ran". The host records one as
#: pipeline evidence at PostToolUse, and ``agents.gated_dispatch_has_pipeline``
#: reads one at PreToolUse. Both hooks ask the same question, so it is answered
#: here, beside ``EDIT_TOOLS``, rather than once in each.
AGENT_TOOLS = frozenset({"Task", "Agent"})

#: The roles an ``agents.*_ran`` gate reads, and the gate that reads each one.
#: A role absent from here is ungated: an agent configured for it is evidence
#: for nothing, so dispatching one without a pipeline loses nothing.
GATED_ROLES = {
    "design": "agents.design_agent_ran",
    "review": "agents.review_agent_ran",
}

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
#:
#: ``agent_type`` is the newest member and the one that widened the contract
#: deliberately. A dispatch is recorded from the PARENT's tool call, which
#: happens before a subagent that starts its own session and pipeline has
#: either — so the design agent that CREATED a pipeline could never be evidence
#: for that pipeline's design gate (BUG_028). Claude Code puts the acting
#: agent's type on every hook payload it sends, including the ones the subagent
#: itself makes, so the actor can be recorded where the dispatch cannot. A host
#: that cannot say who is acting simply omits the key and loses only that
#: route.
NORMALISED_PAYLOAD_KEYS = (
    "tool_name",
    "tool_input",
    "tool_input.file_path",
    "tool_input.notebook_path",
    "agent_type",
    "stop_hook_active",
    "tool_input.subagent_type",
    "tool_input.prompt",
    "tool_input.command",
)
#: ``tool_input.command`` is the third widening (ADR-010 D5). It was already
#: read above ``hosts/`` — ``privacy.clean`` finds a ``git commit`` in it —
#: and ``sweep.no_full_run`` reads it to find a full run of the configured
#: suite made through the shell. The list now says so.
#: ``tool_input.subagent_type`` and ``tool_input.prompt`` joined together
#: (IDEA_020). The first is which agent a dispatch names. The host read it
#: already, but a warning before a gated dispatch is a rule so a project can
#: retune it, and a rule reads only this contract. The second was already
#: read above ``hosts/``: ``Context.resolution_for_payload`` finds the worktree
#: a dispatch's brief names, and this list did not say so.

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
    #: The records a rule computed, in ADR-012 D5's shape, for a rule that
    #: has them (``guidelines.mechanical_clean``). ``None`` for every other
    #: rule, so a payload carries the key only where it means something.
    #: Printed (DN-16), and kept only in a landing's guidelines record
    #: (A4.6, DN-7).
    findings: tuple[Mapping[str, Any], ...] | None = None
    #: Each guideline a live grant passed in this evaluation where it would
    #: otherwise have failed the gate, with what it would have failed on
    #: (ADR-012 ruling 4). Set by the two guideline family gates only, and
    #: read by the delivery surface that spends the grant.
    overridden: tuple[tuple[str, str], ...] = ()
    #: Each owed judgement rule's standing as plain data
    #: (``core/verdicts.Standing.record``), for ``guidelines.verdicts_complete``
    #: when it judged standings; ``None`` for every other rule and outcome.
    #: Kept in a landing's guidelines record (A4.6, DN-7).
    standings: tuple[Mapping[str, Any], ...] | None = None
    #: The ruleset digest a guideline family gate judged against, when it
    #: judged at all: the tree's for ``mechanical_clean``, the pin (or the
    #: tree's, with no pin) for ``verdicts_complete``.
    ruleset: str | None = None
    #: Every live grant a guideline family gate read, as (guideline, reason),
    #: in grant order, used or not; what :func:`grant_outcomes` reads for a
    #: grant that passed nothing.
    grants: tuple[tuple[str, str], ...] = ()

    @classmethod
    def clean(cls, message: str = "", detail: str = "") -> Verdict:
        return cls(ok=True, message=message, detail=detail)

    @classmethod
    def fail(
        cls, message: str, detail: str = "", fixes: Iterable[str] = ()
    ) -> Verdict:
        return cls(ok=False, message=message, detail=detail, fixes=tuple(fixes))

    @classmethod
    def na(cls, message: str = "not applicable here", detail: str = "") -> Verdict:
        return cls(ok=True, message=message, detail=detail, skipped=True)


@dataclass(frozen=True)
class RuleSpec:
    id: str
    summary: str
    events: tuple[str, ...]
    fn: Callable[[RuleContext], Verdict]


#: How each level prints when it is the part that decides whether work stops.
#: `block` shouts because it ends the turn; `ask` does not, because the answer
#: is a question the reader is about to be asked rather than a refusal.
_MARKER = {
    Level.BLOCK: "BLOCK",
    Level.ASK: "ask",
    Level.WARN: "warn",
    Level.OFF: "off",
}


@dataclass(frozen=True)
class Evaluated:
    rule_id: str
    level: Level
    verdict: Verdict

    @property
    def blocks(self) -> bool:
        """Work stops here unless something deliberate intervenes.

        ``ask`` counts. It is a softer DELIVERY, not a softer verdict: at
        ``pre-tool-use`` the host routes the call to a human, and at every
        other surface there is no human in that loop, so the fail-closed
        answer is the only honest one. Callers that can actually ask somebody
        separate the two with :attr:`asks`; everyone else treats an
        unanswered ask as a stop (AUTH-01).
        """
        return (
            self.level in (Level.BLOCK, Level.ASK)
            and not self.verdict.ok
            and not self.verdict.skipped
        )

    @property
    def asks(self) -> bool:
        """This verdict stops the work, and a human may release it in band."""
        return (
            self.level is Level.ASK
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
        delivery layer is the one that owns that distinction. ``ask`` is an
        enforced level by the same test — a rule the project asks about going
        inert is news for the same reason.
        """
        return self.level in (Level.BLOCK, Level.ASK) and self.verdict.skipped

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
            marker = f"skip:{_MARKER[self.level]}" if self.skipped_blocking else "skip"
        elif self.verdict.ok:
            marker = "ok"
        else:
            marker = _MARKER[self.level]
        lines = [f"[{marker}] {self.rule_id}: {self.verdict.message}"]
        if self.verdict.detail:
            lines.append(f"        {self.verdict.detail}")
        for fix in self.verdict.fixes:
            lines.append(f"        try: {fix}")
        return "\n".join(lines)


#: The evidence key holding the audit record (ADR-012 D1 item 2, the A7 ruling
#: 10). Which adapter saw which subagent, and what model it ran on. Written by a
#: host through ``PipelineDomain.record_audit`` and read by ``pipeline status``.
AUDIT_KEY = "audit"


def _without_audit(pipeline: dict | None) -> dict | None:
    """``pipeline`` with no audit record, leaving the caller's dict as it was.

    A record without one comes back as the same object, so this costs nothing
    on the common path and no caller holds a copy it did not expect.
    """
    if not pipeline:
        return pipeline
    evidence = pipeline.get("evidence")
    if not isinstance(evidence, dict) or AUDIT_KEY not in evidence:
        return pipeline
    return {
        **pipeline,
        "evidence": {key: value for key, value in evidence.items() if key != AUDIT_KEY},
    }


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
    #: The host adapter's instruction files: the default of
    #: ``guidelines.instruction_files`` (ADR-012 D3). Which file a host reads
    #: as standing instructions is the host's answer, and nothing below the
    #: CLI imports a host, so the CLI supplies it. Empty means the host names
    #: none, and then only configured files carry a block.
    host_instruction_files: tuple[str, ...] = ()
    #: The host adapter's install plan for the library agents, as a callable
    #: answering one :class:`AgentCopy` per agent: what
    #: ``agents.rendered_current`` reads (ADR-012 D8, the A6 ruling 10). An
    #: install plan is a host's, and nothing below the CLI imports a host, so
    #: the CLI supplies it as it supplies ``host_instruction_files``. ``None``
    #: means ``host.kind`` names no adapter, and then the rule is N/A.
    host_agent_plan: Callable[[], Sequence[AgentCopy]] | None = None

    def __post_init__(self) -> None:
        # Here rather than at each caller, so no rule can see the audit record
        # whoever builds the context, the oracle's gate cache included (the A7
        # ruling 10). A gate's verdict may not depend on which model ran (D1).
        self.pipeline = _without_audit(self.pipeline)

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

    @property
    def dispatched_agent(self) -> str | None:
        return dispatched_agent(self.payload)

    def evidence(self, key: str, default: Any = None) -> Any:
        if not self.pipeline:
            return default
        return (self.pipeline.get("evidence") or {}).get(key, default)


def dispatched_agent(payload: Mapping[str, Any]) -> str | None:
    """The agent type a subagent dispatch names, or ``None`` if this is none.

    ``None`` for a call through any tool outside :data:`AGENT_TOOLS`, and for a
    dispatch that names no agent. Both hooks ask this, so a dispatch the
    PostToolUse recording would file is the same dispatch the PreToolUse
    warning screens.
    """
    if str(payload.get("tool_name", "")) not in AGENT_TOOLS:
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, Mapping):
        return None
    agent = tool_input.get("subagent_type")
    return str(agent) if agent else None


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


#: Rules a blanket stand-down does NOT cover unless the operator names them.
#:
#: One entry, and it earns it: ``privacy.clean`` is the only gate whose failure
#: cannot be undone after the fact. Nothing un-writes an identifier out of a
#: published history. This library has already argued that case twice — the
#: `loose` profile keeps it blocking, and so does the README's opinions section
#: — so going past it stays a separate sentence the operator wrote, rather than
#: a side effect of a switch thrown for an unrelated reason.
HELD_OUT_OF_STANDDOWN = frozenset({"privacy.clean"})


def standdown_in_force(
    session: Mapping[str, Any] | None, pipeline: Mapping[str, Any] | None
) -> Mapping[str, Any] | None:
    """The stand-down governing this evaluation, if one still holds.

    Pure, and asked on every evaluation rather than stored as a flag somebody
    has to remember to clear. Expiry is DERIVED from the state the scope names:
    a stand-down scoped to a pipeline dies when that pipeline reaches a
    terminal state or is replaced, and one scoped to the session lives until
    the session ends or the operator lifts it.

    Deriving it is the point. A stand-down nobody remembers to lift is
    indistinguishable from having uninstalled the tool, and an expiry that
    depends on a later command running is an expiry that does not happen.

    Granting one with no pipeline running records ``pipeline_started`` as
    ``None`` — there is nothing yet to pin it to — and that used to be read as
    "ends the moment any pipeline exists", which ended it at `pipeline start`
    itself rather than at the finish the CLI's own message promises
    (`invariant standdown` prints "lifts when a pipeline starts and
    finishes"). A hold thrown for unrelated work almost always leads straight
    into starting the pipeline that work needed, so expiring on start rather
    than finish undid the grant before anything ran under it, silently: no
    denial pointed at the hold lapsing, because as far as the operator could
    tell nothing had lapsed yet. The fix treats "no pipeline yet" as WAITING,
    not as scoped-and-satisfied: once a pipeline does start, it becomes the one
    this hold rides, on the same terms as a hold granted while one was already
    running.
    """
    record = (session or {}).get("standdown")
    if not record:
        return None
    if str(record.get("until")) == "session":
        return record

    # Scoped to the pipeline that was current when it was granted — or, if
    # none was, to whichever pipeline starts next. A different pipeline than
    # the one it is bound to, or none where one is required, ends it.
    started = record.get("pipeline_started")
    if started is not None and (not pipeline or pipeline.get("started") != started):
        return None
    if started is None and pipeline is None:
        # Still nothing to ride. The hold waits rather than expiring, because
        # a stand-down granted for work outside any pipeline is granted
        # exactly so the pipeline that work leads into does not reopen the
        # gates it was meant to hold.
        return record

    # Either matched by timestamp, or granted with none running and one now
    # exists: `pipeline` is the one this hold rides, until IT reaches a
    # terminal state.
    if pipeline.get("finished") is not None:
        return None
    terminal = _terminal_states(
        load_pipeline_machine,
        None,
        str(pipeline.get("methodology") or "") or None,
    )
    if terminal and str(pipeline.get("state") or "") in terminal:
        return None
    return record


def evaluate(rule_ids: Iterable[str], ctx: RuleContext) -> list[Evaluated]:
    held = standdown_in_force(ctx.session, ctx.pipeline)
    included = set((held or {}).get("include") or ())
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
        if held and (rule_id not in HELD_OUT_OF_STANDDOWN or rule_id in included):
            # A skip and not a pass, deliberately: a block-level rule renders
            # this as `[skip:BLOCK]`, which is on the default verdict stream
            # rather than behind --verbose. Enforcement being held is news
            # about the project's own enforcement, which is exactly the class
            # of fact `skipped_blocking` exists to surface.
            results.append(
                Evaluated(
                    rule_id,
                    level,
                    Verdict.na(f"stood down — {held.get('reason', 'no reason given')}"),
                )
            )
            continue
        try:
            verdict = spec.fn(ctx)
        except Exception as exc:  # noqa: BLE001 - a broken rule must be loud, not absent
            verdict = Verdict.fail(
                f"rule {rule_id!r} raised {type(exc).__name__}: {exc}",
                detail="This is a bug in the rule, not a finding about your work.",
            )
        results.append(Evaluated(rule_id, level, verdict))
    return results


def evaluate_event(event: str, ctx: RuleContext) -> list[Evaluated]:
    return apply_acknowledgements(ctx.pipeline, evaluate(
        [spec.id for spec in rules_for_event(event)], ctx
    ))


def apply_acknowledgements(
    pipeline: Mapping[str, Any] | None, verdicts: list[Evaluated]
) -> list[Evaluated]:
    """Turn an acknowledged advisory failure into a recorded skip.

    Not a silencer. The reason travels with the verdict and shows wherever the
    verdict shows, so "considered and declined" stops being indistinguishable
    from "never read" — which is the real defect a repeating warning has, more
    than the repetition is.

    It lives here rather than in ``domains/pipeline`` because it was written
    there and only ever ran there: hooks reach the registry through
    :func:`evaluate_event`, which never consulted the evidence, so a warn-level
    rule bound to a HOST EVENT could not be acknowledged into silence at all —
    it repeated on every tool call for the life of the run, with the
    acknowledgement recorded and inert (AUTH-01). One function, both routes.

    A ``block``-level verdict is never acknowledged, and neither is an ``ask``:
    both are levels the project chose in order to be stopped, and letting an
    acknowledgement past them would make every level the same one spelled
    differently (ADR-001 D20). Their appeal is ``invariant override``.
    """
    acknowledged = ((pipeline or {}).get("evidence") or {}).get("acknowledged") or {}
    if not acknowledged or not isinstance(acknowledged, Mapping):
        return verdicts

    out: list[Evaluated] = []
    for evaluated in verdicts:
        reason = acknowledged.get(evaluated.rule_id)
        if (
            reason
            and not evaluated.verdict.ok
            and evaluated.level not in (Level.BLOCK, Level.ASK)
        ):
            # A skip with the acknowledgement as its text, keeping what the
            # rule computed (findings, standings, digest, grants): a landing
            # records what the gate found as well as that it was acknowledged
            # (A4.6).
            out.append(
                Evaluated(
                    evaluated.rule_id,
                    evaluated.level,
                    replace(
                        evaluated.verdict, ok=True, skipped=True,
                        message=f"acknowledged — {reason}", detail="", fixes=(),
                    ),
                )
            )
        else:
            out.append(evaluated)
    return out


# -- session rules -----------------------------------------------------

#: What the three rules keyed on ``ctx.is_edit`` say when a call is not one.
#:
#: They used to say NOTHING — a bare ``Verdict.na()``, whose default text is
#: "not applicable here" — on every shell call, which reads exactly like a
#: pass and is how BUG_027's sibling blind spot stayed invisible: on a host
#: whose harness edits through the shell, these three rules are silent for a
#: whole session and never say why.
#:
#: Their ENFORCEMENT is deliberately unchanged. A shell-mediated write really
#: is outside what they screen; teaching them to recognise one is a behaviour
#: change that could refuse work in a live session, and it is its own scoped
#: decision rather than a rider on the privacy fix. What is fixed here is that
#: the silence now states its own limit, so a reader can tell an inapplicable
#: gate from a satisfied one (BUG_020).
_NOT_AN_EDIT = "not screened: this call is not an Edit or a Write"


@rule(
    "session.isolated_before_edit",
    summary="No file edits until the session has its own worktree.",
    events=("pre-tool-use",),
)
def _isolated_before_edit(ctx: RuleContext) -> Verdict:
    if not ctx.is_edit:
        return Verdict.na(_NOT_AN_EDIT)
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
                        (
                            "or, if this session has no worktree yet: "
                            "invariant session isolate --execute --id <id>"
                        )
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
        return Verdict.na(_NOT_AN_EDIT)
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
            "invariant session teardown  # the normal end of a session",
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


def _as_float(value: object, default: float = 0.0) -> float:
    """A stored number, or the default when the document holds rubbish.

    Same tolerance as ``core.plan``, for the same stated reason: the session
    files are promised to be hand-inspectable and hand-fixable, which is the
    same sentence as "a hand can get one wrong". A rule that raised on a
    mistyped timestamp would take a blocking gate down with it.
    """
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _plan_cold_days(ctx: RuleContext) -> float | None:
    """How long an open plan has sat still, or ``None`` if it counts as alive.

    Returns the number rather than a bare boolean so the refusal can NAME the
    days it judged on. A ``_plan_is_cold`` answering ``True`` would have made
    the rule read the clock a second time to print it, and this repository
    already states why one mutation takes exactly one reading of a clock.

    The reading is ``max(plan_touched, heartbeat)``. A session moving through
    its lifecycle is alive whether or not anyone edited the checklist on the
    way, and the plan's own clock alone would call a session that transitioned
    twice today cold.

    **Why ``plan_touched`` is a separate field from ``heartbeat`` at all.** The
    cold branch below BLOCKS, so it must be clearable by the commands its own
    fixes name — and ``plan_done``, ``plan_remove`` and ``plan_reopen`` each
    stamp ``plan_touched``. Had the plan reused ``heartbeat``, the only way to
    clear this gate would have been to stamp the anchor of the per-transition
    ``elapsed`` telemetry: a gate satisfiable only by corrupting a measurement.

    **Both clocks zero reads as FRESH.** Every record written before
    ``plan_touched`` existed carries ``0.0``. Failing on missing data would
    fire a blocking gate on every one of those records simultaneously, at
    ``stop`` — the surface with the least room to be wrong. The two mistakes do
    not cost the same: a false "cold" ends a turn, a false "parked" costs one
    turn's reminder.
    """
    session = ctx.session or {}
    last = max(
        _as_float(session.get("plan_touched")), _as_float(session.get("heartbeat"))
    )
    if last <= 0:
        return None
    days = max(0.0, (time.time() - last) / 86400.0)
    # From the manifest, never a constant here: the distinction being drawn is
    # "paused over a weekend" against "abandoned", and where that line sits is
    # a property of how a project works.
    threshold = _as_float(ctx.manifest.get("session.plan_stale_days"), 7.0)
    return days if days >= threshold else None


@rule(
    "session.work_would_be_stranded",
    summary="A turn does not end with finished work stranded on an unmerged branch, unless an open plan says it is parked.",
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

    # Asked here and nowhere earlier, deliberately: the unstranded path above is
    # untouched, so a merged branch answers exactly as it always did whatever
    # its checklist says.
    #
    # Under the model where a session outlives its host, "terminal pipeline AND
    # commits ahead" is the RESTING state at the end of every increment — a
    # plan spanning four host sessions parks with work on its branch three
    # times before it merges once. Refusing on that alone would fire on every
    # one of those turns until it was ignored, which is the failure this rule's
    # own narrowing to a terminal pipeline exists to avoid, one model change
    # later. What still separates parked from abandoned is whether the plan is
    # being WORKED, so that is the question. A session with no plan, or with a
    # finished one, falls through to the original answer byte for byte: an
    # empty checklist says nothing about where the commits live, and a complete
    # one says the opposite of parked.
    progress = plan_reader.progress(ctx.session.get("plan"))
    if progress.remaining:
        cold_days = _plan_cold_days(ctx)
        if cold_days is None:
            # `clean`, not `na`. The question was asked and answered: this work
            # is parked against a promise somebody recorded. `na` would claim
            # the rule had nothing to say about a session it just examined.
            return Verdict.clean(
                f"parked against an open plan — {progress.describe()}, "
                f"{ahead} commit(s) on {branch}",
                detail=f"Next up: {progress.remaining[0]['title']!r}. The plan "
                "moved recently, so these commits are where the next host "
                "session picks them up rather than where they get lost.",
            )
        return Verdict.fail(
            f"{ahead} commit(s) on {branch}, against a plan nobody has moved "
            f"in {int(cold_days)} day(s)",
            detail=f"{progress.describe()}, and the open entries have sat "
            "still long enough that 'parked' no longer explains them. Either "
            "the plan is out of date or the session is. Both are fixed by "
            "saying so; neither is fixed by the branch sitting there.",
            fixes=(
                "invariant session plan show",
                "invariant session plan done <n>     # it is finished",
                "invariant session plan remove <n>   # it is not going to happen",
                "invariant session review",
                "invariant session transition abandoned --reason '<why>'",
            ),
        )

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
    "session.deliverables_complete",
    summary="A session lands only once the deliverables it recorded are done.",
    events=(),
)
def _deliverables_complete(ctx: RuleContext) -> Verdict:
    """The other half of "a session accumulates work across hosts and merges once".

    The model gained the first half — a plan the session carries between host
    sessions — and nothing enforced the second. The merge was reachable from
    any ``reviewed`` record, so what decided whether the work was finished was
    whichever host session happened to be looking at the time. This gate reads
    the list the operator wrote about their own work and asks whether they
    finished it.

    A machine gate, ``events=()``. Landing is a transition, and attaching this
    to ``stop`` as well would ask, on every turn of a session nowhere near
    merging, a question whose only honest answer then is "not yet".
    """
    if ctx.session is None:
        return Verdict.na("no session")

    state = str(ctx.session.get("state") or "")
    # Through the machine, exactly as `session.work_would_be_stranded` does it.
    # A skip on a gate a project holds at `block` reads as a pass, so a machine
    # that cannot be loaded FAILS here rather than stepping aside (AUD-08).
    session_terminal = _terminal_states(load_session_machine, ctx.manifest)
    if session_terminal is None:
        return _machine_unloadable_failure("session")
    if state in session_terminal:
        return Verdict.na(f"session is {state}")

    progress = plan_reader.progress(ctx.session.get("plan"))
    if not progress.total:
        # `na`, and neither of the two obvious alternatives. `clean` is the
        # vacuous green: an empty plan is not "the plan is finished", it is "no
        # plan was made", and this gate guards the merge. `fail` would make a
        # checklist mandatory for every session including a one-line chore, and
        # a gate that legitimate work must override routinely is what teaches
        # people to reach for `--force`.
        #
        # The message names the command that would ARM the gate rather than
        # saying "not applicable here", which tells a reader a gate went inert
        # and nothing about how to make it mean something (BUG_027).
        return Verdict.na(
            "this session recorded no deliverables, so there is nothing to "
            'finish — invariant session plan add "<what is owed>" arms this gate'
        )

    if progress.remaining:
        return Verdict.fail(
            f"{len(progress.remaining)} of {progress.total} deliverables are "
            "still open",
            detail="This session said what it was for and has not finished "
            "saying it. Open: "
            + ", ".join(f"{e['n']} {e['title']!r}" for e in progress.remaining[:5]),
            # Both recorded routes are named before the forced one: `plan
            # remove` retires the entry and its number permanently, so the
            # checklist itself carries the decision, while forcing the merge
            # records only that somebody went past a gate. Between the two
            # recorded routes, `plan done` leads: this gate fires most often
            # because work is genuinely finished but unticked, so `plan done`
            # is the commoner and more honest answer. The reflex being guarded
            # against is `--force`, and both of the first two avoid it, so the
            # ordering between them follows frequency rather than ceremony.
            fixes=(
                "invariant session plan done <n>     # it is finished",
                "invariant session plan remove <n>   # it is not going to happen",
                "invariant session merge --force     # the override is recorded",
            ),
        )

    return Verdict.clean(f"{progress.done}/{progress.total} deliverables done")


@rule(
    "session.branch_is_current",
    summary="A session branch has not fallen behind the base it will merge into.",
    events=("stop",),
)
def _branch_is_current(ctx: RuleContext) -> Verdict:
    """"Rebase onto the local base after every commit" was prose until now.

    The model has always said it; nothing owned it, so nothing checked it, and
    a branch discovers it is behind at the moment of the fast-forward merge —
    the one step where the fix costs a rebase of work already declared done.

    ``warn`` in every profile, deliberately. A stale branch costs a rebase and
    never data, and a ``block`` here would fire whenever a peer landed
    anything: a gate whose trigger is somebody else's commit is a gate the
    person holding it cannot prevent, and one they will learn to ignore.
    """
    if ctx.session is None:
        return Verdict.na("no session")

    state = str(ctx.session.get("state") or "")
    session_terminal = _terminal_states(load_session_machine, ctx.manifest)
    if session_terminal is None:
        return _machine_unloadable_failure("session")
    if state in session_terminal:
        return Verdict.na(f"session is {state}")

    branch = str(ctx.session.get("branch") or "")
    if not branch:
        return Verdict.na("session owns no branch")
    base = str(ctx.session.get("base") or "master")

    # Argument order is the whole rule. `commits_ahead(cwd, X, Y)` counts what
    # X holds that Y does not, so "behind" is the base measured against the
    # branch — the opposite order to `session.work_would_be_stranded`, which
    # asks the same helper the other question a line away. Getting these the
    # wrong way round reports every session with work on it as stale.
    behind = git.commits_ahead(_repo_root(ctx), base, branch)
    if behind is None:
        # `na`, matching the sibling closeout rule: an unmakeable comparison
        # names the refs it could not compare, and this rule advises rather
        # than blocks, so there is nothing here for a silent skip to relax.
        return Verdict.na(f"could not compare {branch} against {base}")
    if behind == 0:
        return Verdict.clean(f"{branch} carries everything on {base}")

    return Verdict.fail(
        f"{branch} is {behind} commit(s) behind {base}",
        detail="Rebasing now resolves any conflict here, with this session's "
        "tests around it. Rebasing at the merge resolves it against work "
        "already declared reviewed, which is the same conflict at the worst "
        "moment to meet it.",
        fixes=("invariant session sync",),
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
        return Verdict.na(_NOT_AN_EDIT)
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


# -- the store's docs lock (ADR-014 D5, increment 4b) -------------------

#: The tool calls the docs lock governs: a read and every edit.
LOCKED_TOOLS = frozenset({"Read"}) | frozenset(EDIT_TOOLS)


@rule(
    "store.docs_lock_held",
    summary="A store's policy docs are read and written only under the session's docs lock.",
    events=("pre-tool-use",),
)
def _store_docs_lock_held(ctx: RuleContext) -> Verdict:
    """A Read or an edit of a store policy path needs the path's unit held.

    The host has already granted a free unit on the spot (the session
    domain's ``docs_access``) before this runs, so a refusal here means the
    unit is busy, or taking it would break the global order. Memory and the
    other data records are outside the lock (ruling 20), and a call that
    resolves to no session is ungoverned, as every pre-tool-use rule treats
    one. A session resolved by a guess is still checked against the holder:
    holding the lock never turns a guess into a proof, and a guess that does
    not hold the unit is refused like any other (D5, "Attribution").
    """
    if ctx.tool_name not in LOCKED_TOOLS:
        return Verdict.na("not a Read, Edit or Write")
    target = ctx.target_path
    if target is None:
        return Verdict.na("no file path in this call")
    if getattr(ctx.manifest, "store", None) is None:
        return Verdict.na("this project keeps its records in its repository; no docs lock")
    if not target.is_absolute():
        target = ctx.cwd / target
    # Classified before the lock is built: a hook runs this on every tool
    # call, and only a governed path pays for opening the lease record.
    try:
        config = doclock.read_config(doclock.store_of(ctx.manifest))
        kind, unit = doclock.classify(ctx.manifest, config, target)
    except ManifestError as exc:
        return Verdict.fail(str(exc), fixes=("narrow one of the store's docs_lock areas",))
    if kind == doclock.DATA:
        return Verdict.na("a data record kept in the store: outside the docs lock (ADR-014 ruling 20)")
    if unit is None:
        return Verdict.na("not a store policy path")
    if ctx.session is None:
        return Verdict.na("this call resolves to no session, so it is ungoverned (ADR-014 D5)")
    sid = str(ctx.session.get("id"))
    try:
        standing = doclock.DocsLock(ctx.manifest, config=config).standing(sid, unit)
    except StateError as exc:
        return Verdict.fail(
            f"the store's docs lock cannot be read: {exc}",
            fixes=("invariant store status",),
        )
    if standing["held"]:
        return Verdict.clean(f"session {sid} holds {unit}")
    if standing["position"] is not None:
        why = (
            f"session {sid} is queued for {unit} at position {standing['position']}, "
            f"behind session {standing['holder']}"
        )
        fixes = (f"invariant docs lock --wait --id {sid}",)
    elif standing["holder"]:
        why = f"{unit} is held by session {standing['holder']}"
        fixes = (f"invariant docs lock {unit} --id {sid}",)
        if standing["stale"]:
            why += f", whose lease is stale ({standing['stale']})"
            fixes += (f"invariant docs lock --take-over {unit} --reason '<why>' --id {sid}",)
    else:
        why = (
            f"session {sid} does not hold {unit}, which was not taken for it here: "
            "a read takes a free unit only for a session the call proves, and never "
            "ahead of a unit the session waits for"
        )
        fixes = (f"invariant docs lock {unit} --id {sid}",)
    return Verdict.fail(
        why,
        detail="A store's docs are read, designed against and written under one "
        "exclusive lock per area, so no session designs on docs another is about "
        "to change (ADR-014 rulings 13 and 16).",
        fixes=fixes,
    )


@rule(
    "store.docs_lock_released",
    summary="A store's docs lock is released before the change leaves design.",
    events=(),
)
def _store_docs_lock_released(ctx: RuleContext) -> Verdict:
    """The exit gate on a design state in a store project (ADR-014 D5).

    The docs are read, designed against and written in one cycle under one
    lock, and the code work runs in parallel only after the units are
    released, so a session may not leave design while it holds a unit or
    waits for one. ``docs unlock`` refuses a unit with uncommitted changes,
    so passing here also means the doc update is on the store's main line.
    Injected only in a store project (``core/machines``); N/A in-repo.
    """
    if getattr(ctx.manifest, "store", None) is None:
        return Verdict.na("this project keeps its records in its repository; no docs lock")
    if ctx.session is None:
        return Verdict.na("no session, so no lease to release")
    sid = str(ctx.session.get("id"))
    try:
        out = doclock.DocsLock(ctx.manifest).outcome(sid)
    except (StateError, ManifestError) as exc:
        return Verdict.fail(
            f"the store's docs lock cannot be read: {exc}",
            fixes=("invariant store status",),
        )
    if not out.held and not out.waiting and not out.pending:
        return Verdict.clean(f"session {sid} holds and waits for no unit of the store's docs lock")
    parts = []
    if out.held:
        parts.append(f"holds {', '.join(out.held)}")
    if out.waiting:
        parts.append(
            f"is queued for {out.waiting['unit']} at position {out.waiting['position']}"
        )
    elif out.pending:
        parts.append(f"waits for {', '.join(out.pending)}")
    return Verdict.fail(
        f"session {sid} {' and '.join(parts)} of the store's docs lock",
        detail="The doc update is written and committed at the end of design, and "
        "the units released before implementation, so the next session reads the "
        "latest docs while this one's code work runs (ADR-014 D5).",
        fixes=(
            f"invariant docs unlock --id {sid}",
            (
                "commit the doc update in the store first: docs unlock refuses a "
                "unit with uncommitted changes"
            ),
        ),
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


def _role_members(manifest: Manifest, role: str) -> list[str]:
    """The agents ``agents.roles.<role>`` names. The one reading of the mapping."""
    return [str(a) for a in (manifest.get(f"agents.roles.{role}") or [])]


def gated_roles_of(manifest: Manifest, agent: str) -> list[str]:
    """The gated roles ``agent`` is configured for, whose gate is not off.

    Read from the same mapping, through the same helper, that the
    ``agents.*_ran`` gates read, so the warning and the gate cannot disagree
    about which agents count. A role whose gate is ``off`` asks for no
    evidence, so it is left out.
    """
    return [
        role
        for role, gate in GATED_ROLES.items()
        if manifest.rule_level(gate) is not Level.OFF
        and agent in _role_members(manifest, role)
    ]


def _agent_role_gate(ctx: RuleContext, role: str, purpose: str) -> Verdict:
    configured = _role_members(ctx.manifest, role)
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


@rule(
    "agents.gated_dispatch_has_pipeline",
    summary="A design or review agent is dispatched into a running pipeline.",
    events=("pre-tool-use",),
)
def _agents_gated_dispatch_has_pipeline(ctx: RuleContext) -> Verdict:
    """Say, before the dispatch, that it will not count as evidence (IDEA_020).

    The PostToolUse notice says the same thing after the agent has done its
    work, when the only remedy left is to start a pipeline and dispatch again.
    This is the point where starting the pipeline first is still cheap.

    A rule rather than a branch in the host, so a project can retune it like
    any other advisory. Every shipped profile grades it ``warn``: the dispatch
    is legitimate and is never refused, and evidence recorded against the
    session is deliberately NOT carried into a later pipeline.

    Speaks under the same conditions as the PostToolUse notice. No session
    bound to the call means nobody is working under the gates, so it is silent.
    "No pipeline" means the same as ``record_agent_run``'s ``NO_PIPELINE``: no
    current pipeline record at all.
    """
    agent = ctx.dispatched_agent
    if agent is None:
        return Verdict.na("not screened: this call is not an agent dispatch")
    roles = gated_roles_of(ctx.manifest, agent)
    if not roles:
        return Verdict.na(f"{agent} holds no gated role")
    if ctx.session is None:
        return Verdict.na("no session is bound to this dispatch, so no gate waits for it")
    if ctx.pipeline is not None:
        return Verdict.clean(f"pipeline is in {ctx.pipeline.get('state')!r}")

    named = " and ".join(roles)
    methodology = ctx.manifest.get("methodology.pipeline") or "<methodology>"
    return Verdict.fail(
        f"{agent} ({named} role) is being dispatched with no pipeline running, "
        f"so its run will NOT count as evidence for a pipeline started later",
        detail=f"Session {ctx.session.get('id')} has no pipeline. "
        f"{', '.join(GATED_ROLES[r] for r in roles)} will not find this dispatch, "
        "and repeating it is the only way to satisfy them. The dispatch still "
        "goes ahead. If the agent starts the pipeline itself, its own calls "
        "after that are recorded and this does not apply.",
        fixes=(
            (
                f"invariant pipeline start --methodology {methodology} "
                "--task \"<what you are doing>\" first, then dispatch"
            ),
        ),
    )


# -- agents.rendered_current ----------------------------------------------
#
# Whether each library agent's installed copy is what the host would render
# now (ADR-012 D8, the A6 ruling 10). The answer is the host's install plan,
# the one `agents install --dry-run` prints, so "current" means one thing
# everywhere. `core/` may not import a host, so the plan reaches the rule as a
# callable on the rule context. The host's own `doctor` row is the same
# function over the same plan (`agents_currency`), so the row and the rule
# cannot disagree.

#: The command an agent copy is brought current with.
AGENTS_REPAIR = "invariant agents install"

#: The fix for an edited agent copy (ADR-012 D8): an overlay survives every
#: upgrade, and an edit to an installed copy does not.
AGENT_OVERLAY_FIX = (
    "move the edit into an overlay (agents.overlays) so it survives upgrades, "
    "or a model line into agents.tiers and host.tier_map"
)


@dataclass(frozen=True)
class AgentCopy:
    """One library agent's installed copy, as the host's install plan reads it."""

    name: str
    #: The host's word for how the copy stands against the render: current,
    #: absent, edited locally, installed from an older version, unreadable.
    state: str
    #: The host's answer, and all a rule reads of how a copy stands. A difference
    #: only in a capability hint is current (the A7 ruling 6), so no rule can
    #: tell a hint from anything else.
    current: bool
    #: Changed after an install wrote it: the one copy an install skips.
    edited: bool = False
    #: For a stale copy, which input moved: the library, the overlay, both,
    #: or the renderer.
    why: str | None = None


def agents_currency(plan: Callable[[], Sequence[AgentCopy]]) -> Verdict:
    """How the library agents' installed copies stand against the render now.

    The one judgement: ``agents.rendered_current`` and the host's ``library
    agents current`` row both ask it, of the same plan. A plan the host
    refuses (an overlay that does not apply, a shipped source that does not
    parse, an ``agents.library`` name the library does not ship) fails it,
    naming the refusal, rather than raising: the install the fix points at
    refuses the same thing.
    """
    try:
        copies = list(plan())
    except InvariantError as exc:
        return Verdict.fail(
            f"the library agents cannot be checked: {exc}",
            detail=f"This is what `{AGENTS_REPAIR}` refuses, so installing could "
            "not bring them current until it is fixed.",
            fixes=(f"fix what is named, then: {AGENTS_REPAIR}",),
        )
    if not copies:
        return Verdict.na("agents.library names no library agent")
    stale = [copy for copy in copies if not copy.current]
    if not stale:
        return Verdict.clean(f"{len(copies)} library agent(s) current against the render")
    named = "; ".join(
        f"{copy.name} ({copy.state})" + (f" — {copy.why}" if copy.why else "")
        for copy in stale
    )
    edited = any(copy.edited for copy in stale)
    fixes = []
    if any(not copy.edited for copy in stale):
        fixes.append(AGENTS_REPAIR)
    if edited:
        fixes.append(f"{AGENT_OVERLAY_FIX}, then: {AGENTS_REPAIR} --overwrite-local")
    return Verdict.fail(
        f"{len(stale)} of {len(copies)} library agent(s) not current against the "
        f"render: {named}",
        detail=" ".join([
            *(
                ["your edits are kept: an install skips an edited copy and names it."]
                if edited
                else []
            ),
            (
                "Each copy is judged against the host's render of its neutral source "
                "and overlay (ADR-012 D8). This rule reports; it never installs."
            ),
        ]),
        fixes=tuple(fixes),
    )


@rule(
    "agents.rendered_current",
    summary="Every library agent's installed copy is what the host would render now.",
    events=(),
)
def _agents_rendered_current(ctx: RuleContext) -> Verdict:
    """ADR-012 D8 and the A6 ruling 10. It reports and never installs.

    The plan is the host adapter's, handed in by the CLI; with no adapter for
    ``host.kind`` there is nothing rendered to compare, and the rule is N/A.
    """
    if ctx.host_agent_plan is None:
        return Verdict.na(
            f"host.kind {ctx.manifest.get('host.kind')!r} has no host adapter, "
            f"so no library agent is rendered"
        )
    return agents_currency(ctx.host_agent_plan)


# -- docs rules --------------------------------------------------------


#: Rules no machine names and nothing injects, reached instead through a
#: `doctor` row whose level the rule's own decides: `warn` warns, `block`
#: fails the row, `off` removes it (ADR-016 D3). The fourth route a graded
#: rule may be reached by.
DOCTOR_ROW_RULES = frozenset({"docs.assessment"})


@rule(
    "docs.assessment",
    summary="The architecture docs have every required section, name every "
    "source area, and link only to files that exist.",
    events=(),
)
def _docs_assessment(ctx: RuleContext) -> Verdict:
    """ADR-016 D3: the assessment's gaps, for `doctor`'s `docs assessment` row.

    Never injected into a pipeline: `doctor` asks it directly, as it asks the
    guidelines rules, and `invariant check` can. The assessment is the docs
    domain's, read here at call time as the memory rules read theirs.
    """
    from ..domains.docs import assess  # local: avoids a cycle

    assessment = assess(ctx.manifest)
    if not assessment.checks:
        return Verdict.na("every check is off (docs.assess.checks: [])")
    if not assessment.gaps:
        return Verdict.clean(
            f"no gaps in {assessment.location} ({', '.join(assessment.checks)})"
        )
    return Verdict.fail(
        assessment.summary(),
        detail=assessment.row_detail(),
        fixes=("invariant docs assess",),
    )


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
    rows = ctx.evidence("docs_read", []) or []
    if _docs_in_store(ctx.manifest):
        return _docs_read_under_lease(ctx, rows, docs_root)
    read = [Path(p) for p in (_read_path(r) for r in rows) if p]
    # Every tree of this repository, not only the one the CLI is standing in.
    # A session has two copies of each document and the recording step can
    # only ever write the WORKTREE spelling while sessions are live — a
    # main-checkout path resolves by guess and is dropped by the `proven`
    # guard, which is BUG_028's rule and is not the thing to change. Testing
    # against the caller's own tree therefore made the two accepted sets
    # disjoint whenever the gate was run from the main checkout: no read the
    # agent could perform satisfied it, and the remedy line advised the read
    # that had just been discarded (BUG_046).
    roots = docs_roots(ctx.manifest, ctx.cwd)
    inside = [p for p in read if any(_is_within(p, root) for root in roots)]
    if inside:
        return Verdict.clean(f"{len(inside)} architecture doc(s) read")
    return Verdict.fail(
        "no architecture doc has been read in this pipeline",
        detail="The diagrams are the authoritative dependency map; reading "
        "them is what makes the plan trustworthy.",
        fixes=(f"read the relevant doc under {_rel(docs_root, _repo_root(ctx))}",),
    )


def _docs_read_under_lease(ctx: RuleContext, rows: list, docs_root: Path) -> Verdict:
    """``docs.read_architecture`` for docs kept in a store (ADR-014 D5, 4b).

    A read counts only when it was made under a lease the session holds or
    held: its row names a lease one of the session's spans ran under. A read
    of the store's docs without the lock is exactly the stale design ruling
    13 forbids, so it is not evidence, and the failure says how many such
    reads were set aside.
    """
    roots = docs_roots(ctx.manifest, ctx.cwd)
    leases = _session_leases(ctx.session)
    under, without = 0, 0
    for row in rows:
        path = _read_path(row)
        if path is None or not any(_is_within(Path(path), root) for root in roots):
            continue
        lease = row.get("lease") if isinstance(row, Mapping) else None
        if lease and str(lease) in leases:
            under += 1
        else:
            without += 1
    if under:
        return Verdict.clean(f"{under} architecture doc(s) read under the docs lock")
    where = _rel(docs_root, _repo_root(ctx))
    if without:
        return Verdict.fail(
            f"{without} architecture doc read(s) in the store were made without "
            "the docs lock, so none counts",
            detail="A read of the store's docs counts only under a lease this "
            "session holds or held (ADR-014 D5): the read, the design and the "
            "doc update are one cycle under one lock.",
            fixes=(f"invariant docs lock <area or path under {where}>, then read it again",),
        )
    return Verdict.fail(
        "no architecture doc has been read in this pipeline",
        detail="The diagrams are the authoritative dependency map; reading "
        "them is what makes the plan trustworthy. The docs are in the store, "
        "so the read is made under its docs lock.",
        fixes=(f"read the relevant doc under {where}; the read takes its unit when it is free",),
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
    docs_changed = [p for p in changed if _is_within(root / p, docs_root)]
    # Outside the architecture docs is not the same as structural: a backlog
    # record, a release plan or a LICENSE read as "source changed" and blocked
    # the chore and docs lanes on a doc nobody could honestly write (BUG_064).
    # The answer is NARROWER than the coverage gate's on purpose. That one asks
    # whether a run could measure the path; this one asks whether a diagram
    # could now be wrong, and a machine YAML or an API contract changes
    # structure with no code beside it. Prose and repository metadata, then the
    # project's own `non_source` assertion — named, as it is there.
    outside_docs = {
        _relative_to_any(p, _coverage_roots(ctx))
        for p in changed
        if not _is_within(root / p, docs_root)
    }
    source_changed, declared_non_source = _excluded_as_declared_non_source(
        ctx, {p for p in outside_docs if not _is_prose(p)}
    )
    # Test code is the one kind of code held out, and only on an adapter's
    # positive claim. The documented architecture is the product's: its
    # components, seams and flows. A change confined to the suite that
    # measures them moves none of those, so the doc it demanded was one no
    # change could honestly write, and a bugfix whose defect lived in a test
    # harness had `--force` as its only landing (BUG_102). ADR-006 D2 already
    # held a repro-only commit to the same reading. Named in the verdict, as
    # `non_source` is: a harness some project does document is still visible
    # to the reviewer, and a code path beside the tests keeps the block.
    source_changed, held_as_tests = _excluded_as_test_code(ctx, source_changed)

    if not source_changed:
        if outside_docs:
            return Verdict.clean(
                f"no structural change ({len(outside_docs)} non-source file(s))",
                detail="; ".join([*declared_non_source, *held_as_tests]) or None,
            )
        return Verdict.clean("docs-only change")
    if _docs_in_store(ctx.manifest):
        return _docs_updated_in_store(ctx, docs_root)
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


def _docs_updated_in_store(ctx: RuleContext, docs_root: Path) -> Verdict:
    """``docs.updated``'s "docs changed" half, read from the store leg (D9).

    The source changed on the repository leg. A store doc counts as its
    update only when it was COMMITTED under a span of its unit which also
    holds a recorded read in that unit (the read and the write under one
    lease, D5), or when the file did not exist when the unit was taken. An
    uncommitted doc, or one whose span holds no read, is named and does not
    count, so the gate blocks until the store carries the update.
    """
    leg = _store_leg(ctx)
    if not leg.answerable:
        return Verdict.fail(
            "the store leg's changes could not be read, so the doc update cannot be found",
            detail=f"{leg.problem}. An unanswered question is not a negative answer, "
            "and admitting the change on it would be.",
            fixes=("invariant store status", "invariant session status"),
        )
    reads = {
        (str(row.get("lease")), str(row.get("unit")))
        for row in ctx.evidence("docs_read", []) or []
        if isinstance(row, Mapping) and row.get("lease") and row.get("unit")
    }
    counted: set[str] = set()
    set_aside: list[str] = []
    for change in leg.changes:
        if not _is_within(change.path, docs_root):
            continue
        if not change.committed:
            set_aside.append(f"{change.rel}: uncommitted in the store")
        elif (change.lease, change.unit) in reads:
            counted.add(change.rel)
        elif git.exists_at(leg.root, change.start, change.rel) is False:
            counted.add(change.rel)  # new in the span: there was nothing to read
        else:
            set_aside.append(
                f"{change.rel}: committed under {change.unit}, whose span holds no read of it"
            )
    aside = sorted(set(set_aside))
    if counted:
        return Verdict.clean(
            f"{len(counted)} store doc file(s) committed under the docs lock with their read",
            detail="; ".join(aside) or None,
        )
    sid = str((ctx.session or {}).get("id") or "<id>")
    where = _rel(docs_root, _repo_root(ctx))
    return Verdict.fail(
        "source changed but no architecture doc in the store was committed under "
        "the docs lock with its read",
        detail=("; ".join(aside) + ". " if aside else "")
        + "The store's docs are read, designed against and written under one "
        "lease (ADR-014 D5): a doc counts when it is committed on the store's "
        "main line under a span of its unit that also holds a read in it.",
        fixes=(
            f"invariant docs lock <area or path under {where}> --id {sid}",
            "then read the doc, update it and commit it in the store under that lock",
            "invariant pipeline advance <state> --force --reason 'no structural change'",
        ),
    )


@rule(
    "docs.area_documented",
    summary="Each source area the change touches is named in an architecture doc.",
    events=(),
)
def _docs_area_documented(ctx: RuleContext) -> Verdict:
    """ADR-016 D6: the incremental path's landing obligation.

    Armed only by ``docs.onboarding: incremental``. Each source file of the
    changed set is mapped to its source area, and the live assessment of the
    session's tree says whether that area is named. An unnamed, touched area
    is owed; an area already named is never asked. No gap list is recorded:
    the assessment is deterministic, so it is the record, and it cannot go
    stale when a doc is written outside a session.

    "Source" is ``docs.updated``'s reading: outside the docs, not prose, not
    declared ``non_source``, not test code an adapter claims. With
    ``records.docs: store`` the store's docs are read, as the assessment
    reads them; per-leg reading is ADR-014 increment 4.
    """
    from ..domains.docs import assess, owning_area  # local: avoids a cycle

    mode = ctx.manifest.get("docs.onboarding")
    if mode != "incremental":
        return Verdict.na(
            f"docs.onboarding is {mode}; only incremental arms this rule"
        )
    if (ctx.pipeline or {}).get("methodology") == "docs":
        return Verdict.na("documentation pipeline — it changes no source")
    if "areas" not in (ctx.manifest.get("docs.assess.checks") or []):
        return Verdict.na(
            "the areas check is off (docs.assess.checks), so no area can be asked about"
        )

    touched = _changed_set(ctx)
    if not touched.paths:
        if not touched.answerable:
            return _unestablished_changed_set(touched)
        return Verdict.na("nothing changed")
    root = _repo_root(ctx)
    docs_root = _docs_root(ctx)
    outside_docs = {
        _relative_to_any(p, _coverage_roots(ctx))
        for p in touched.paths
        if not _is_within(root / p, docs_root)
    }
    source, _ = _excluded_as_declared_non_source(
        ctx, {p for p in outside_docs if not _is_prose(p)}
    )
    source, _ = _excluded_as_test_code(ctx, source)
    if not source:
        return Verdict.na("no source changed, so no area is touched")

    # The SESSION's tree: a doc written in this change names its area here,
    # while the main checkout has not seen it yet. A store's docs resolve to
    # the store whatever the root, since their path is absolute by then.
    tree = _session_tree(ctx)
    manifest = ctx.manifest
    if tree.resolve() != manifest.root.resolve():
        manifest = replace(manifest, root=tree.resolve())
    assessment = assess(manifest)

    owners = {path: owning_area(assessment.areas, path) for path in sorted(source)}
    unmapped = [path for path, area in owners.items() if area is None]
    underivable = assessment.underivable()
    if underivable and unmapped:
        return Verdict.fail(
            "the source areas cannot be derived, so the touched files cannot "
            "be placed",
            detail="; ".join(gap.render() for gap in underivable)
            + f". Not placed: {', '.join(unmapped)}.",
            fixes=(
                "set docs.assess.areas.from to globs or declared",
                "invariant docs assess",
            ),
        )
    touched_areas = {area.path: area for area in owners.values() if area is not None}
    if not touched_areas:
        return Verdict.clean(
            f"no touched source file lies in a source area ({len(source)} file(s))"
        )
    unnamed = assessment.unnamed()
    owed = [touched_areas[path] for path in sorted(touched_areas) if path in unnamed]
    if not owed:
        return Verdict.clean(
            f"{len(touched_areas)} touched source area(s), each named: "
            + ", ".join(sorted(touched_areas))
        )
    return Verdict.fail(
        f"{len(owed)} touched source area(s) named in no architecture doc: "
        + ", ".join(area.path for area in owed),
        detail="; ".join(
            f"{area.path}: {area.name} is named in no doc under "
            f"{assessment.location}; write {assessment.doc_for(area)}"
            for area in owed
        )
        + ". docs.onboarding: incremental says each area is documented the "
        "first time a change touches it.",
        fixes=(
            *(
                f"document {area.name} in {assessment.doc_for(area)}, or name it "
                f"in an existing doc under {assessment.location}"
                for area in owed
            ),
            "invariant docs assess",
        ),
    )


#: What marks a diagram of each format, in Markdown and in rST (ADR-018 D1):
#: a fenced block or a directive at the start of a line.
DIAGRAM_MARKERS: dict[str, tuple[re.Pattern[str], ...]] = {
    "mermaid": (re.compile(r"```mermaid"), re.compile(r"(?m)^\s*\.\. mermaid::")),
    "plantuml": (re.compile(r"@startuml"), re.compile(r"(?m)^\s*\.\. uml::")),
}


@rule(
    "docs.diagrams_current",
    summary="A doc whose structure changed has its diagram updated too.",
    events=(),
)
def _docs_diagrams_current(ctx: RuleContext) -> Verdict:
    if not ctx.manifest.get("docs.require_diagram_update", True):
        return Verdict.na("diagram updates not required")
    formats = ctx.manifest.diagram_formats()
    if not formats:
        return Verdict.na("no diagram format configured")
    fmt = ", ".join(formats)

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
    if _docs_in_store(ctx.manifest):
        # The doc leg (ADR-014 D9): a store's docs change on the store leg.
        touched = _changed_set(ctx, STORE_LEG)
    docs_changed = [p for p in touched.paths if _is_within(root / p, docs_root)]
    if not docs_changed:
        # "No doc changed" is a real answer; "nobody could tell what changed"
        # is not, and only the second may not be reported as a skip.
        if not touched.answerable:
            return _unestablished_changed_set(touched)
        return Verdict.na("no doc changed")

    markers = [m for name in formats for m in DIAGRAM_MARKERS.get(name, ())]
    with_diagrams = []
    for rel in docs_changed:
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if any(marker.search(text) for marker in markers):
            with_diagrams.append(rel)
    if with_diagrams:
        return Verdict.clean(f"{len(with_diagrams)} changed doc(s) carry a diagram")
    return Verdict.fail(
        "the changed docs contain no diagram in the configured format",
        detail=f"Configured format is {fmt}. A structural doc without a diagram "
        "describes the map in prose, which is the thing diagrams exist to "
        "replace.",
        fixes=(f"add a {fmt} diagram to the changed doc",),
    )


# -- the docs project's strict build (ADR-018 D5, D6, D10) -------------------


#: How many build failures or needs a verdict names before counting the rest.
_DOCS_NAMED = 20


@dataclass(frozen=True)
class _DocsBuild:
    """What both docs-project gates read: the record for the tree in hand."""

    srcdir: str
    digest: str
    record: Any  # core.needs.BuildRecord | None


def _docs_build(ctx: RuleContext) -> _DocsBuild | Verdict:
    """The docs project's recorded build for the session's tree, or the
    verdict that settles the gate before any record is read.

    N/A on a migrating project whose docs are not yet sphinx (D10), and when
    the change touches neither the source directory nor ``docs.needs``. A
    docs project in a store is seen only through ADR-014 D9's store leg, not
    built yet, so it is "not established", a failure, as ``docs.updated``
    answers an unestablished changed set. Runs nothing: the digest is read
    from the files, and the record from the state root.
    """
    from . import needs  # local: one reader of the docs project's needs

    manifest = ctx.manifest
    state = manifest.get("docs.needs.state")
    fmt = manifest.get("docs.format")
    if state != "enforced" and fmt != "sphinx":
        return Verdict.na(
            f"docs.needs.state is {state} on docs.format {fmt}: the strict build "
            f"applies once the docs are sphinx (ADR-018 D10)"
        )
    touched = _changed_set(ctx)
    if not touched.paths:
        if not touched.answerable:
            return _unestablished_changed_set(touched)
        return Verdict.na("nothing changed")
    if manifest.get("records.docs") == "store":
        return Verdict.fail(
            "the changed set could not be established for a docs project in a store",
            detail="A store's docs are seen only through the store leg of the "
            "changed set (ADR-014 D9), which is not built yet. An unanswered "
            "question is not a negative answer.",
            fixes=("invariant pipeline advance <state> --force --reason '<why>'",),
        )
    tree = _session_tree(ctx)
    srcdir = needs.srcdir_of(manifest, tree)
    if srcdir is None:
        if state == "enforced":
            return Verdict.fail(
                "no docs project: no conf.py at or above paths.docs",
                fixes=("set docs.needs.srcdir to the docs project's source directory",),
            )
        return Verdict.na("no docs project: no conf.py at or above paths.docs")
    spelled = _rel(srcdir, tree.resolve())
    inside = [
        p for p in touched.paths if _is_within((tree / p).resolve(), srcdir)
    ]
    if not inside and not _docs_needs_changed(ctx, touched, tree):
        return Verdict.na(f"the change touches neither {spelled}/ nor docs.needs")
    digest = needs.tree_digest(srcdir)
    return _DocsBuild(spelled, digest, needs.read_record(manifest.state_root, digest))


def _docs_needs_changed(ctx: RuleContext, touched: _ChangedSet, tree: Path) -> bool:
    """Did the change move ``docs.needs`` in the repository's manifest?

    Compared as parsed YAML, so a reformatted manifest is no change. A base
    that cannot be read counts as a change: the gate then applies, the side
    an unanswerable question falls on.
    """
    if Path(MANIFEST_RELPATH) not in touched.paths:
        return False
    try:
        now = yaml.safe_load((tree / MANIFEST_RELPATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return True
    before_text = git.file_at(tree, touched.base, MANIFEST_RELPATH) if touched.base else None
    if before_text is None:
        return True
    try:
        before = yaml.safe_load(before_text) or {}
    except yaml.YAMLError:
        return True

    def needs_block(doc: Any) -> Any:
        docs = doc.get("docs") if isinstance(doc, Mapping) else None
        return docs.get("needs") if isinstance(docs, Mapping) else None

    return needs_block(now) != needs_block(before)


def _no_build_recorded(build: _DocsBuild) -> Verdict:
    return Verdict.fail(
        f"no docs build recorded for {build.srcdir}/ at its digest "
        f"{build.digest[:12]}",
        detail="A gate runs no command: the strict build is run and recorded "
        "by 'invariant docs build', and a record for an older tree does not "
        "count.",
        fixes=("invariant docs build",),
    )


@rule(
    "docs.builds_strict",
    summary="A change touching the docs project builds strictly.",
    events=(),
)
def _docs_builds_strict(ctx: RuleContext) -> Verdict:
    """ADR-018 D5: the green record of `invariant docs build` for the tree."""
    build = _docs_build(ctx)
    if isinstance(build, Verdict):
        return build
    record = build.record
    if record is None:
        return _no_build_recorded(build)
    if record.passed:
        documents = f", {record.documents} document(s)" if record.documents else ""
        return Verdict.clean(
            f"strict build green for {build.srcdir}/ at {build.digest[:12]}{documents}"
        )
    shown = list(record.diagnostics[:_DOCS_NAMED])
    more = len(record.diagnostics) - len(shown)
    return Verdict.fail(
        f"the strict build of {build.srcdir}/ failed: "
        f"{len(record.diagnostics)} warning(s) or error(s), exit {record.exit_code}",
        detail="; ".join(shown) + (f"; and {more} more" if more > 0 else "")
        or f"sphinx-build exited {record.exit_code} with nothing parsed",
        fixes=(
            "fix each named file and line, then: invariant docs build",
        ),
    )


@rule(
    "docs.needs_traceable",
    summary="Every need is of a declared type and meets the required-link rules.",
    events=(),
)
def _docs_needs_traceable(ctx: RuleContext) -> Verdict:
    """ADR-018 D6: the green build's needs.json against `docs.needs`."""
    from . import needs  # local: one reader of the docs project's needs

    build = _docs_build(ctx)
    if isinstance(build, Verdict):
        return build
    record = build.record
    if record is None:
        return _no_build_recorded(build)
    if not record.passed:
        return Verdict.fail(
            f"no green build of {build.srcdir}/ at {build.digest[:12]} to read: "
            f"docs.builds_strict names its failures",
            fixes=("fix the build, then: invariant docs build",),
        )
    if not record.needs_json:
        return Verdict.fail(
            f"the green build of {build.srcdir}/ recorded no needs.json",
            detail="Is sphinx_needs in the docs project's extensions?",
            fixes=("invariant docs build --force",),
        )
    try:
        read = needs.load_needs_json(Path(record.needs_json))
    except needs.NeedsJsonError as exc:
        return Verdict.fail(str(exc), fixes=("invariant docs build --force",))
    manifest = ctx.manifest
    problems = needs.check(
        read,
        needs.types_of(manifest),
        needs.parse_link_rules(manifest.get("docs.needs.require_links") or ()),
        manifest.get("docs.needs.decision_statuses") or (),
        prefix=f"{build.srcdir}/",
    )
    if not problems:
        return Verdict.clean(
            f"{len(read.needs)} need(s), each declared and linked as docs.needs requires"
        )
    shown = [p.render() for p in problems[:_DOCS_NAMED]]
    more = len(problems) - len(shown)
    return Verdict.fail(
        f"{len(problems)} need problem(s): "
        + ", ".join(sorted({p.need for p in problems})[:_DOCS_NAMED]),
        detail="; ".join(shown) + (f"; and {more} more" if more > 0 else ""),
        fixes=(
            (
                "link each named need, or map its directive in docs.needs.types, "
                "then: invariant docs build"
            ),
        ),
    )


# -- the refused homes, the cited ids and the switch (ADR-018 D8, D9, D10) ----


#: The suffixes a Markdown file carries, for the refused homes (D8).
_MARKDOWN = (".md", ".markdown")


def _generated_files(ctx: RuleContext) -> set[str]:
    """The generated files invariant declares, project-relative: never refused
    as a home, since only their command writes them (D8)."""
    found: set[str] = set()
    try:
        configured = ctx.manifest.get("guidelines.ruleset")
        source = guidelines_relpath(ctx, configured) if configured else None
        checklist = _checklist_relpath(ctx, source)
    except (InvariantError, OSError, ValueError):
        checklist = None
    if checklist:
        found.add(checklist)
    named = ctx.manifest.get("guidelines.instruction_files")
    for entry in ctx.host_instruction_files if named is None else named:
        rel = guidelines_relpath(ctx, entry)
        if rel:
            found.add(rel)
    return found


def _srcdir_relative(ctx: RuleContext, tree: Path) -> str | None:
    """The docs project's source directory, project-relative, or ``None``."""
    from . import needs  # local: one reader of the docs project's needs

    srcdir = needs.srcdir_of(ctx.manifest, tree)
    if srcdir is None:
        return None
    try:
        return srcdir.resolve().relative_to(tree.resolve()).as_posix()
    except ValueError:
        return None


def _refused_home(ctx: RuleContext, forms: Sequence[str], tree: Path) -> str | None:
    """Why ``forms`` (one path's project-relative spellings) is a refused home
    for design (D8), or ``None`` when it is not one."""
    from . import needs  # local: one reader of the docs project's needs

    configured = ctx.manifest.get("docs.needs.refused_paths")
    globs = needs.DEFAULT_REFUSED_PATHS if configured is None else tuple(configured)
    docs = _docs_relative(ctx.manifest, ctx.cwd)
    srcdir = _srcdir_relative(ctx, tree)
    homes = [h for h in (docs.as_posix() if docs else None,
                         f"{srcdir}/decisions" if srcdir else None) if h]
    for rel in forms:
        for glob in globs:
            if glob_matches(rel, project_relative(glob)):
                return f"it matches docs.needs.refused_paths {glob!r}"
        if PurePosixPath(rel).suffix.lower() in _MARKDOWN:
            for home in homes:
                if rel.startswith(home.rstrip("/") + "/"):
                    return f"it is Markdown under {home}/, where design is written as needs"
    return None


@rule(
    "docs.design_home",
    summary="Design is written as needs in the docs project, never in a refused home.",
    events=("pre-tool-use",),
)
def _docs_design_home(ctx: RuleContext) -> Verdict:
    """ADR-018 D8: a write to a refused home is refused, naming the fix.

    Warns while the project migrates (ruling 4) and blocks when enforced, by
    level alone; the verdict is the same in both states.
    """
    if not ctx.is_edit:
        return Verdict.na(_NOT_AN_EDIT)
    target = ctx.target_path
    if target is None:
        return Verdict.na("no file path in this call")
    if _is_outside_project(ctx, target):
        return Verdict.na("path is outside this project")
    forms = _project_relative_forms(ctx, target)
    tree = _session_tree(ctx)
    why = _refused_home(ctx, forms, tree)
    if why is None:
        return Verdict.clean("not a refused home for design")
    # Asked only of a refused path, and once: resolving the generated files
    # costs git calls this hook would otherwise pay on every edit.
    generated = _generated_files(ctx)
    if any(rel in generated for rel in forms):
        return Verdict.na("a generated file invariant declares; its command writes it")
    srcdir = _srcdir_relative(ctx, tree)
    page = f"{srcdir}/" if srcdir else "the docs project"
    return Verdict.fail(
        f"{forms[0]} is not a home for design: {why} (ADR-018 D8)",
        detail="Architecture and design live only as needs in the docs project, "
        "each decision a need with an id. A user guide, a report under "
        "paths.reports and memory are written freely.",
        fixes=(
            'a decision: invariant docs decision new "<title>"',
            f"other design: write it as a need on the page it belongs on under {page}",
        ),
    )


def _held(ctx: RuleContext, tree: Path, *, green_for_tree: bool) -> tuple[set[str] | None, str]:
    """The need ids a recorded build holds, and what was read, or ``None`` and
    why none could be.

    ``green_for_tree`` asks for the green record of the tree as it stands (the
    landing, D9); otherwise that record, else the last one recorded (a write,
    D8: the tree it lands in has not been built yet).
    """
    from . import needs  # local: one reader of the docs project's needs

    srcdir = needs.srcdir_of(ctx.manifest, tree)
    if srcdir is None:
        return None, "there is no docs project (no conf.py at or above paths.docs)"
    digest = needs.tree_digest(srcdir)
    record = needs.read_record(ctx.manifest.state_root, digest)
    if green_for_tree:
        if record is None or not record.passed:
            return None, f"no green docs build is recorded at {digest[:12]}"
    elif record is None or not record.needs_json:
        record = needs.latest_record(ctx.manifest.state_root)
        if record is None:
            return None, "no docs build is recorded"
    if not record.needs_json:
        return None, f"the build at {record.digest[:12]} kept no needs.json"
    try:
        read = needs.load_needs_json(Path(record.needs_json))
    except needs.NeedsJsonError as exc:
        return None, str(exc)
    return {n.id for n in read.needs}, f"the build at {record.digest[:12]}"


def _design_prefixes(ctx: RuleContext, roles: Iterable[str]) -> tuple[str, ...]:
    from . import needs  # local: one reader of the docs project's needs

    return needs.prefixes_of(needs.types_of(ctx.manifest), roles)


def _unheld_verdict(
    ids: Sequence[str], held: set[str] | None, why: str, *, what: str
) -> Verdict:
    """The verdict over ``ids`` cited in ``what``: each must be a held need."""
    missing = list(ids) if held is None else [i for i in ids if i not in held]
    if not missing:
        return Verdict.clean(f"{len(ids)} cited id(s), each a need {why}")
    shown = ", ".join(missing[:_DOCS_NAMED]) + (
        f" and {len(missing) - _DOCS_NAMED} more" if len(missing) > _DOCS_NAMED else ""
    )
    return Verdict.fail(
        (
            f"{what} names {shown}, and {why}"
            if held is None
            else f"{what} names {shown}, which {why} does not hold"
        ),
        detail="An item keeps the problem, the evidence and the status; a ruling "
        "is a decision need, linked by an id the build holds (ADR-018 D8).",
        fixes=(
            'invariant docs decision new "<title>"',
            "invariant docs build",
        ),
    )


def _edit_texts(ctx: RuleContext) -> list[str]:
    """The text an edit tool call writes: what it would add, nothing it keeps."""
    tool_input = ctx.tool_input
    texts = [tool_input.get(key) for key in ("content", "new_string", "new_source")]
    for edit in tool_input.get("edits") or ():
        if isinstance(edit, Mapping):
            texts.append(edit.get("new_string"))
    return [str(t) for t in texts if t]


def _items_stores(ctx: RuleContext) -> list[str]:
    stores = []
    for key in ("items.path", "items.closed_path"):
        value = ctx.manifest.get(key)
        if value:
            rel = project_relative(str(value))
            if rel:
                stores.append(rel)
    return stores


def _in_store(rel: str, stores: Sequence[str]) -> bool:
    return any(rel == s or rel.startswith(s.rstrip("/") + "/") for s in stores)


@rule(
    "items.decisions_linked",
    summary="An item names only decisions, requirements, screens and rules that exist as needs.",
    events=("pre-tool-use",),
)
def _items_decisions_linked(ctx: RuleContext) -> Verdict:
    """ADR-018 D8, ruling 12: every design id an item names is a held need.

    Three callers: an edit tool writing an items store (the hook), an
    ``invariant item`` write (``extra["item_text"]``, from
    ``domains/workitem``), and the landing, over the lines the session added
    to the items stores.
    """
    prefixes = _design_prefixes(ctx, ("decision", "requirement", "ui", "rule"))
    if "item_text" in ctx.extra:
        ids = _cited(str(ctx.extra["item_text"]), prefixes)
        if not ids:
            return Verdict.na("the item names no decision, requirement, ui or rule id")
        held, why = _held(ctx, _session_tree(ctx), green_for_tree=False)
        return _unheld_verdict(ids, held, why, what=f"the item {ctx.extra.get('item_id', '')}".rstrip())
    stores = _items_stores(ctx)
    if ctx.payload:
        if not ctx.is_edit:
            return Verdict.na(_NOT_AN_EDIT)
        target = ctx.target_path
        if target is None or _is_outside_project(ctx, target):
            return Verdict.na("not a write to this project's items")
        forms = _project_relative_forms(ctx, target)
        if not any(_in_store(rel, stores) for rel in forms):
            return Verdict.na("not a write to the items store")
        ids = _cited("\n".join(_edit_texts(ctx)), prefixes)
        if not ids:
            return Verdict.na("the write names no decision, requirement, ui or rule id")
        held, why = _held(ctx, _session_tree(ctx), green_for_tree=False)
        return _unheld_verdict(ids, held, why, what=f"this write to {forms[0]}")
    # The landing: the lines the session added to the items stores. The tree
    # is resolved here and in the branches above only once it is needed: it
    # costs git calls the hook would otherwise pay on every edit.
    tree = _session_tree(ctx)
    touched = _changed_set(ctx)
    changed = [p.as_posix() for p in touched.paths if _in_store(p.as_posix(), stores)]
    if not changed:
        if not touched.paths and not touched.answerable:
            return _unestablished_changed_set(touched)
        return Verdict.na("the change touches no item")
    added = git.changed_lines(tree, touched.base) if touched.base else None
    texts = []
    for rel in changed:
        if added is not None:
            lines = added.get(rel)
            if lines is None:
                continue
            try:
                body = (tree / rel).read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            if lines.whole:
                texts += body
            else:
                texts += [body[n - 1] for n in sorted(lines.lines) if 0 < n <= len(body)]
        else:
            try:
                texts.append((tree / rel).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
    ids = _cited("\n".join(texts), prefixes)
    if not ids:
        return Verdict.na("the changed items name no decision, requirement, ui or rule id")
    held, why = _held(ctx, tree, green_for_tree=False)
    return _unheld_verdict(ids, held, why, what="the items this change touched")


def _cited(text: str, prefixes: Sequence[str]) -> list[str]:
    from . import needs  # local: one reader of the docs project's needs

    return needs.cited_ids(text, prefixes)


def _session_commit_messages(ctx: RuleContext, tree: Path) -> str:
    """The messages of the session's own commits, from its base."""
    base = str((ctx.session or {}).get("base") or "")
    if not base:
        return ""
    return git.commit_messages(tree, base) or ""


@rule(
    "docs.decisions_recorded",
    summary="Every decision a session made is a need before it lands.",
    events=(),
)
def _docs_decisions_recorded(ctx: RuleContext) -> Verdict:
    """ADR-018 D9: the session's declared decisions are needs at the landing.

    Each entry of ``session decisions`` is a draft (blocks, naming the
    command that makes it a need) or an id the green build of the tree in
    hand must hold; so is each decision id the session's plan and commits
    cite.
    """
    if ctx.session is None:
        return Verdict.na("no session: nothing declared")
    entries = [e for e in (ctx.session.get("decisions") or []) if isinstance(e, Mapping)]
    tree = _session_tree(ctx)
    prefixes = _design_prefixes(ctx, ("decision",))
    cited_by = "\n".join(
        [e["title"] for e in plan_reader.entries(ctx.session.get("plan") or [])]
        + [_session_commit_messages(ctx, tree)]
    )
    declared = [str(e["id"]) for e in entries if e.get("id")]
    ids = list(dict.fromkeys(declared + _cited(cited_by, prefixes)))
    drafts = [e for e in entries if not e.get("id")]
    if not ids and not drafts:
        return Verdict.na("the session declared no decision and cites none")
    problems = [
        f"draft {e.get('n')}: {e.get('draft') or '(no text)'}" for e in drafts
    ]
    held, why = _held(ctx, tree, green_for_tree=True) if ids else (set(), "")
    missing = [i for i in ids if held is None or i not in held]
    problems += [f"{i}: not a need in the green build" for i in missing]
    if not problems:
        return Verdict.clean(f"{len(ids)} decision(s), each a need {why}")
    fixes = []
    if drafts:
        # `<n>` is the draft's number as the detail names it ("draft 1: ...").
        fixes += [
            'invariant docs decision new "<title>"',
            "invariant session decisions resolve <n> <DEC_id>",
        ]
    if missing:
        fixes.append("invariant docs build")
    return Verdict.fail(
        f"{len(drafts)} draft decision(s) and {len(missing)} id(s) not held"
        + (f" ({why})" if held is None and missing else ""),
        detail="; ".join(problems[:_DOCS_NAMED])
        + (f"; and {len(problems) - _DOCS_NAMED} more" if len(problems) > _DOCS_NAMED else ""),
        fixes=tuple(fixes),
    )


@rule(
    "docs.mandate_kept",
    summary="A change does not move docs.needs.state back from enforced.",
    events=(),
)
def _docs_mandate_kept(ctx: RuleContext) -> Verdict:
    """ADR-018 D10: an enforced project stays enforced, but by a reasoned route.

    Compares the repository manifest's ``docs.needs.state`` at the session's
    base with the tree in hand. Blocks in every profile and state; passes
    only with ``pipeline advance --force --reason``.
    """
    base = str((ctx.session or {}).get("base") or "")
    if not base:
        return Verdict.na("no base to compare the manifest with")
    tree = _session_tree(ctx)
    before_text = git.file_at(tree, base, MANIFEST_RELPATH)
    if before_text is None:
        return Verdict.na(f"no manifest at {base} to compare with")

    def state_of(text: str | None) -> str:
        try:
            doc = yaml.safe_load(text or "") or {}
        except yaml.YAMLError:
            return "unreadable"
        docs = doc.get("docs") if isinstance(doc, Mapping) else None
        block = docs.get("needs") if isinstance(docs, Mapping) else None
        state = block.get("state") if isinstance(block, Mapping) else None
        return str(state or "migrating")

    # Checked, and nothing moved back: a pass rather than a skip, since the
    # rule blocks in every profile and a block-level skip on every landing of
    # every migrating project would be news that is not news.
    if state_of(before_text) != "enforced":
        return Verdict.clean(f"docs.needs.state at {base} is not enforced; nothing to keep")
    try:
        now_text = (tree / MANIFEST_RELPATH).read_text(encoding="utf-8")
    except OSError:
        now_text = None
    now = state_of(now_text) if now_text is not None else "absent"
    if now == "enforced":
        return Verdict.clean("docs.needs.state stays enforced")
    return Verdict.fail(
        f"this change moves docs.needs.state from enforced to {now}",
        detail="The mandate is not optional (ADR-018 ruling 1). A project leaves "
        "it only by a recorded, reasoned route (D10).",
        fixes=("invariant pipeline advance <state> --force --reason '<why>'",),
    )


# -- guidelines rules --------------------------------------------------


class RenderRefused(InvariantError):
    """A render that would write somewhere it may not, or text it may not.

    Raised while the render is planned, before anything is written, so the
    command and ``guidelines.generated_current`` refuse the same things.
    """

    exit_code = 1

    def __init__(self, message: str, fix: str) -> None:
        super().__init__(f"{message}\n  {fix}")
        self.reason = message
        self.fix = fix


@rule(
    "guidelines.ruleset_valid",
    summary="The project's guidelines ruleset loads and validates.",
    events=(),
)
def _guidelines_ruleset_valid(ctx: RuleContext) -> Verdict:
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        return failure
    if ruleset is None:
        return Verdict.na("no guidelines ruleset configured")
    return Verdict.clean(
        f"{len(ruleset.rules)} guideline(s) in {len(ruleset.sections)} section(s), "
        f"ruleset {ruleset.digest}"
    )


def load_ruleset(
    ctx: RuleContext,
) -> tuple[guidelines_reader.Ruleset | None, Verdict | None]:
    """The project's ruleset, the failure that stands in for it, or neither.

    ``(None, None)`` means no ruleset is configured, and every ``guidelines.*``
    rule is then N/A (ADR-012 D3). Anything short of a ruleset that validates
    is ``(None, verdict)``, and the caller FAILS on it: a gate that needs the
    ruleset fails rather than skips when it does not load, as a pinned machine
    that cannot load already does. A configured path naming nothing inside the
    project fails for the same reason; dropping it would read as "no ruleset",
    which is a gate switched off by a typo.

    The ruleset is read from the tree in hand: the session's worktree when it
    has one, else the checkout the rule runs in. There is no fallback to
    another checkout's copy, so a ruleset broken in the session cannot pass on
    the main checkout's.
    """
    configured = ctx.manifest.get("guidelines.ruleset")
    if not configured:
        return None, None
    rel = guidelines_relpath(ctx, configured)
    if rel is None:
        return None, Verdict.fail(
            f"guidelines.ruleset {configured!r} names nothing inside the project",
            detail="The ruleset is project content, reviewed like a doc; a path "
            "outside every checkout of this repository is not this project's.",
            fixes=(
                (
                    "set guidelines.ruleset to a project-relative path, such as "
                    + guidelines_example(ctx)
                ),
            ),
        )
    tree = guidelines_tree(ctx)
    path = tree / rel
    if not path.is_file():
        return None, Verdict.fail(
            f"the configured guidelines ruleset {rel} does not exist",
            detail="An absent file is not an empty ruleset: it cannot be told apart "
            "from one that was never wired, so the gates that need it fail. The "
            "manifest key and the file belong in the same change.",
            fixes=(f"create {rel}", "or clear guidelines.ruleset"),
        )
    try:
        # The registries a cochange rule names are read from the same tree
        # (ADR-012 DN-A9), so a registry broken in the session cannot pass
        # on another checkout's copy either.
        return guidelines_reader.load(
            path,
            registered=[spec.id for spec in all_rules()],
            root=tree,
            engines=_offered_engines(_ecosystem_adapters(ctx)),
        ), None
    except guidelines_reader.RulesetError as exc:
        return None, Verdict.fail(
            f"the guidelines ruleset {rel} does not validate "
            f"({len(exc.problems)} problem(s))",
            detail="; ".join(exc.problems),
            fixes=(f"fix {rel}, then: invariant check guidelines.ruleset_valid",),
        )


def ruleset_pin(ctx: RuleContext) -> dict[str, str | None] | None:
    """What a pipeline pins at ``start``: the ruleset's path and digest, or why not.

    ``None`` with no ruleset configured, so nothing is pinned (ADR-012 D3).
    Otherwise ``{"path", "digest", "error"}``. The ruleset is read through
    :func:`load_ruleset`, from the tree in hand and with its registries, so
    the pin is exactly what ``guidelines.ruleset_valid`` reads. The digest is
    the ruleset's own, the hash every verdict records, and a registry is not
    part of it: a registry is read as it stands after the change.

    A ruleset that does not load is RECORDED, with ``digest`` null and the
    failure as ``error`` (ruling DN-D). The pipeline still starts, and the
    landing refuses it through ``guidelines.ruleset_valid``.
    """
    configured = ctx.manifest.get("guidelines.ruleset")
    if not configured:
        return None
    path = guidelines_relpath(ctx, configured) or str(configured)
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        detail = f": {failure.detail}" if failure.detail else ""
        return {"path": path, "digest": None, "error": f"{failure.message}{detail}"}
    return {"path": path, "digest": ruleset.digest, "error": None}


def guidelines_tree(ctx: RuleContext) -> Path:
    """The tree the guidelines are read from and rendered into.

    The session's worktree when it has one, else the checkout the call runs
    in. One answer for the ruleset gate and for `guidelines render`, so the
    command never writes a checklist into a different tree from the one whose
    ruleset it read.
    """
    return _session_tree(ctx)


def _session_tree(ctx: RuleContext) -> Path:
    """The session's worktree when it has one, else the checkout in hand.

    What a rule reading the change's own files reads: from the main checkout
    (``session merge``) the work lives in the session's worktree.
    """
    return _session_worktree(ctx) or _repo_root(ctx)


def guidelines_example(ctx: RuleContext, name: str = guidelines_reader.RULESET_NAME) -> str:
    """The path a fix offers for a guidelines file: inside the project's docs
    project, where a new project's ruleset lives (ADR-020 D10), so the fix
    names ``invariant-docs/guidelines/...`` there and ``docs/guidelines/...``
    where the docs project is ``docs/``."""
    from . import needs  # local: one reader of the docs project's folder

    return guidelines_reader.ruleset_location(needs.docs_folder(ctx.manifest), name)


def guidelines_relpath(ctx: RuleContext, configured: str) -> str | None:
    """The project-relative spelling of a declared guidelines path.

    ``None`` when it names nothing inside the project: outside every checkout
    of this repository, or the project root itself.
    """
    rel = project_relative(
        configured, [ctx.manifest.root, git.toplevel(ctx.cwd), git.main_checkout(ctx.cwd)]
    )
    return None if rel in (None, ".") else rel


def touched_paths(ctx: RuleContext) -> tuple[str, ...]:
    """The session's declared plus changed files, project-relative and sorted.

    The file scope guideline selection reads (ADR-012 D7, the A5 ruling D3):
    ``_changed_set``'s union, so the working tree, the branch's commits and
    the declared set each count. A path not yet, or no longer, a file in the
    tree is kept: a declared file the session is about to create is the one
    whose rules it most needs. Empty when nothing could be established, which
    the selection reads as no file filter, the direction that hides no rule.
    """
    return _touched(ctx, _changed_set(ctx))


def _touched(ctx: RuleContext, changed: _ChangedSet) -> tuple[str, ...]:
    files, gone = _changed_in_tree(ctx, changed, guidelines_tree(ctx))
    return tuple(sorted({*files, *gone}))


# -- the turn index (ADR-012 ruling 8, D7, the A5 rulings D1 to D3, D10, D12) ---------


def turn_guidelines(
    manifest: Manifest, context: Callable[[], RuleContext]
) -> guidelines_reader.TurnIndex | None:
    """What the turn projection says about the guidelines, as plain data.

    ``None`` is silence: no ruleset (decided from the manifest alone, so no
    file is read, no git is asked and ``context`` is never called), and a
    pipeline in a landing or abandon state, where no agent works. With no
    pipeline the index is the always rules alone. Otherwise the state's
    roles are read from the machine the pipeline was started under, the
    file scope is :func:`touched_paths`'s union (the A5 ruling D3), and
    ``core/guidelines.select`` chooses over it. The always rules are listed
    whatever the phase and the scope (D12). The ruleset is read from the
    tree in hand. A pinned digest that differs from the tree's is carried
    as ``pin`` (DN-6); a null pin, or none, compares against the tree's.

    A ruleset that does not load, and any exception inside, is one
    ``problem`` line, so a hook that asks this can never fail on it.
    """
    if not manifest.get("guidelines.ruleset"):
        return None
    try:
        return _turn_guidelines(context())
    except Exception as exc:  # noqa: BLE001 - a projection may never fail its hook
        return guidelines_reader.TurnIndex(
            problem=(
                f"the turn index could not be computed ({type(exc).__name__}); "
                f"the ruleset is printed by: invariant guidelines show"
            )
        )


def _turn_guidelines(ctx: RuleContext) -> guidelines_reader.TurnIndex | None:
    pipeline = ctx.pipeline
    roles: tuple[str, ...] = ()
    if pipeline:
        roles = _state_roles(pipeline, ctx.manifest)
        phases = guidelines_reader.phases_for(roles)
        if not phases:
            return None
        show = f"invariant guidelines show --phase {_show_phase(roles)} --touched"
    else:
        phases = ("always",)
        show = "invariant guidelines show"
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        return guidelines_reader.TurnIndex(
            problem=f"{failure.message}; see invariant check guidelines.ruleset_valid"
        )
    always = tuple(rule for rule in ruleset.rules if "always" in rule.phases)
    files: tuple[str, ...] = ()
    unknown = False
    chosen = always
    if pipeline:
        changed = _changed_set(ctx)
        files = _touched(ctx, changed)
        unknown = not files and not changed.answerable
        chosen += tuple(
            rule for rule in guidelines_reader.select(ruleset, roles, files)
            if "always" not in rule.phases
        )
    pinned = (pipeline or {}).get("ruleset")
    pin = pinned.get("digest") if isinstance(pinned, Mapping) else None
    return guidelines_reader.TurnIndex(
        source=guidelines_relpath(ctx, ctx.manifest.get("guidelines.ruleset")) or "",
        digest=ruleset.digest,
        total=len(ruleset.rules),
        phases=phases,
        rules=chosen,
        files=files,
        scope_unknown=unknown,
        show=show,
        pin=pin if pin and pin != ruleset.digest else None,
    )


def _state_roles(pipeline: Mapping[str, Any], manifest: Manifest) -> tuple[str, ...]:
    """The roles of the pipeline's current state, read from the machine it was
    started under, so a later change to the configured methodology does not
    move a running pipeline (the A5 ruling D1: the machine alone, no history)."""
    machine = load_pipeline_machine(manifest, pipeline.get("methodology") or None)
    return tuple(machine.state(str(pipeline.get("state") or "")).roles)


def _show_phase(roles: Iterable[str]) -> str:
    """The ``--phase`` that reproduces what a state with ``roles`` is shown:
    its first role that selects anything, or ``design`` for a build state,
    which sees what a design state sees."""
    return next(
        (role for role in roles if guidelines_reader.ROLE_PHASES.get(role)), "design"
    )


# -- what `guidelines render` would write --------------------------------------
#
# The render plan: every generated output of the guidelines domain decided,
# checked and privacy-scanned, with nothing written. `domains/guidelines`
# writes what it plans; `guidelines.generated_current` reads its statuses. It
# lives here rather than in the domain because a rule asks it, and `core/`
# may not import a domain (overview.md, dependency rule). One plan is why the
# drift gate and `render --dry-run` cannot disagree about "current".


@dataclass(frozen=True)
class BlockRendered:
    """What one render did, or would do, to one managed block in one file."""

    relpath: str
    name: str
    #: ``new`` (inserted), ``current``, ``stale``, ``edited`` (written only
    #: with ``overwrite_local``), or ``absent`` (no block, and none inserted).
    status: str
    written: bool


@dataclass(frozen=True)
class BlockPlan:
    """A block decided and checked, not yet written."""

    result: BlockRendered
    path: Path
    #: The whole file as it will be written, or ``None`` when it is not.
    text: str | None


@dataclass(frozen=True)
class PagePlan:
    """A generated page beside the checklist, decided and checked, not yet written."""

    #: ``new``, ``current``, ``stale`` or ``edited``, as for the checklist.
    status: str
    path: Path
    relpath: str
    text: str


@dataclass(frozen=True)
class RenderPlan:
    """Everything one render would write, decided before the first write."""

    #: The checklist's state: ``new``, ``current``, ``stale`` or ``edited``.
    status: str
    path: Path
    relpath: str
    #: The ruleset digest the text is rendered from.
    ruleset: str
    #: The checklist as it will be written.
    text: str
    blocks: tuple[BlockPlan, ...] = ()
    #: The ids of the ruleset's ``always`` rules, which the block carries in
    #: full: what the always-layer note counts (A5 ruling D12).
    always: tuple[str, ...] = ()
    #: The rules as needs, ``<srcdir>/rules.rst`` (ADR-018 D12); ``None`` where
    #: the docs are not a docs project in this tree.
    rules: PagePlan | None = None


def plan_render(
    ctx: RuleContext, *, insert: tuple[str, ...] = (), overwrite_local: bool = False
) -> RenderPlan | None:
    """What ``guidelines render`` would write, with nothing written.

    ``None`` with no ruleset configured. Every refusal is a
    :class:`RenderRefused` raised before anything could be written: a ruleset
    that does not load, a target that may not take generated text (outside
    the project, the ruleset itself, a suffix ``docs.format`` disagrees with,
    exempt from isolation, a file render did not write), a malformed block
    pair, and a privacy finding in text that is about to be written. The
    tree is the tree in hand, the one the ruleset is read from.
    """
    unknown = [name for name in insert if name not in guidelines_reader.BLOCKS]
    if unknown:
        raise RenderRefused(
            f"no managed block is named {', '.join(unknown)}",
            f"--insert takes {', '.join(guidelines_reader.BLOCKS)}",
        )
    ruleset = ruleset_or_refuse(ctx)
    if ruleset is None:
        return None

    manifest = ctx.manifest
    source = guidelines_relpath(ctx, manifest.get("guidelines.ruleset"))
    fmt = _checklist_format(manifest)
    tree = guidelines_tree(ctx)
    rel = _checklist_target(ctx, source, fmt, tree)
    path = tree / rel
    if is_exempt_path(manifest, tree, path):
        raise RenderRefused(
            f"the checklist {rel} is exempt from session isolation, and a "
            f"generated file never is (ADR-012 ruling 13)",
            "set guidelines.checklist to a path outside session.exempt_paths "
            "and the declared stores",
        )

    scanner = PrivacyScanner.from_manifest(manifest)
    text = guidelines_reader.render_checklist(ruleset, fmt, source=source)
    findings = scanner.scan_text(text, path=Path(rel))
    if findings:
        named = ", ".join(f"{rel}:{f.line} {f.pattern} {f.redacted}" for f in findings)
        raise RenderRefused(
            f"the rendered checklist fails the privacy scan ({named}); "
            f"nothing was written",
            f"remove it from {source}, then render again",
        )
    status = _generated_status(path, rel, fmt, text, "guidelines.checklist")
    rules = _plan_rules_page(ctx, ruleset, source, tree, scanner)

    body = guidelines_reader.render_index_block(
        ruleset, source=source, max_lines=manifest.get("guidelines.injection.max_lines")
    )
    blocks = [
        _plan_block(tree, target, body, insert, overwrite_local)
        for target in _instruction_files(ctx, source, rel, tree)
    ]
    writing = [block for block in blocks if block.text is not None]
    name = guidelines_reader.INDEX_BLOCK
    rendered = guidelines_reader.block_text(name, body)
    # Scanned once per file it is written into: a path-scoped allow entry
    # reaches the block in the file it names and in no other.
    found = [
        f"{block.result.relpath} ({name} block, line {f.line}) {f.pattern} {f.redacted}"
        for block in writing
        for f in scanner.scan_text(rendered, path=Path(block.result.relpath))
    ]
    if found:
        named = ", ".join(found)
        raise RenderRefused(
            f"the rendered block fails the privacy scan ({named}); nothing was written",
            f"remove it from {source}, then render again",
        )
    return RenderPlan(
        status=status, path=path, relpath=rel, ruleset=ruleset.digest, text=text,
        blocks=tuple(blocks),
        always=tuple(r.id for r in ruleset.rules if "always" in r.phases),
        rules=rules,
    )


def _docs_project_relpath(ctx: RuleContext, tree: Path) -> str | None:
    """The docs project's source directory, spelled against ``tree``, where
    the docs are a sphinx-needs project there (ADR-018 D3); else ``None``.

    ``docs.format: sphinx`` is the test, as for the docs project's gates
    (D10): a Markdown project's ``conf.py`` is no docs project. A source
    directory outside the tree (a store's) is not this command's to write.
    """
    from . import needs  # local: one reader of the docs project's source directory

    if ctx.manifest.get("docs.format") != "sphinx":
        return None
    srcdir = needs.srcdir_of(ctx.manifest, tree)
    if srcdir is None or not srcdir.is_dir():
        return None
    try:
        rel = srcdir.resolve().relative_to(Path(tree).resolve()).as_posix()
    except ValueError:
        return None
    return None if rel == "." else rel


def _plan_rules_page(
    ctx: RuleContext,
    ruleset: guidelines_reader.Ruleset,
    source: str | None,
    tree: Path,
    scanner: PrivacyScanner,
) -> PagePlan | None:
    """``<srcdir>/rules.rst``, the rules as needs (ADR-018 D12), checked as the
    checklist is, or ``None`` where there is no docs project in ``tree``."""
    from . import needs  # local: one reader of the docs project's types

    srcdir = _docs_project_relpath(ctx, tree)
    if srcdir is None:
        return None
    rel = posixpath.join(srcdir, guidelines_reader.RULES_PAGE)
    path = tree / rel
    if is_exempt_path(ctx.manifest, tree, path):
        raise RenderRefused(
            f"the rules page {rel} is exempt from session isolation, and a "
            f"generated file never is (ADR-012 ruling 13)",
            "keep the docs project's source directory outside session.exempt_paths "
            "and the declared stores",
        )
    kind = needs.type_of_role(needs.types_of(ctx.manifest), "rule")
    text = guidelines_reader.render_rule_needs(
        ruleset, source=source, directive=kind.directive, prefix=kind.prefix
    )
    findings = scanner.scan_text(text, path=Path(rel))
    if findings:
        named = ", ".join(f"{rel}:{f.line} {f.pattern} {f.redacted}" for f in findings)
        raise RenderRefused(
            f"the rendered rules page fails the privacy scan ({named}); "
            f"nothing was written",
            f"remove it from {source}, then render again",
        )
    status = _generated_status(path, rel, "sphinx", text, "the docs project's rules page")
    return PagePlan(status=status, path=path, relpath=rel, text=text)


def ruleset_or_refuse(ctx: RuleContext) -> guidelines_reader.Ruleset | None:
    """The ruleset a guidelines command works from, ``None`` with none configured.

    A ruleset that does not load is :class:`RenderRefused`, carrying the
    failure ``guidelines.ruleset_valid`` reports, so every command refuses
    exactly what the gate fails.
    """
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        fixes = "; ".join(failure.fixes) or "fix the ruleset"
        detail = f": {failure.detail}" if failure.detail else ""
        raise RenderRefused(f"{failure.message}{detail}", fixes)
    return ruleset


def _checklist_format(manifest: Manifest) -> str:
    fmt = manifest.get("docs.format")
    if fmt not in guidelines_reader.CHECKLIST_SUFFIXES:
        raise RenderRefused(
            f"docs.format {fmt!r} has no checklist renderer",
            f"set docs.format to one of {', '.join(guidelines_reader.CHECKLIST_SUFFIXES)}",
        )
    return fmt


def _checklist_target(ctx: RuleContext, source: str, fmt: str, tree: Path) -> str:
    suffix = guidelines_reader.CHECKLIST_SUFFIXES[fmt]
    configured = ctx.manifest.get("guidelines.checklist")
    if configured is None:
        return _default_checklist(ctx, source, suffix, tree)
    rel = guidelines_relpath(ctx, configured)
    if rel is None:
        raise RenderRefused(
            f"guidelines.checklist {configured!r} names nothing inside the project",
            "set it to a project-relative path, or null for one beside the ruleset",
        )
    if rel == source:
        raise RenderRefused(
            f"guidelines.checklist {rel} is the ruleset; rendering would "
            f"overwrite the source with its own rendering",
            "point guidelines.checklist somewhere else, or set it to null",
        )
    if PurePosixPath(rel).suffix != suffix:
        raise RenderRefused(
            f"guidelines.checklist {rel} does not end in {suffix}, and "
            f"docs.format is {fmt}",
            f"name a {suffix} file, or set guidelines.checklist to null",
        )
    return rel


def _instruction_files(
    ctx: RuleContext, source: str, checklist: str, tree: Path
) -> list[str]:
    """The instruction files' project-relative paths, each checked, once each.

    ``guidelines.instruction_files``, or with that key null the host
    adapter's default the context carries.
    """
    configured = ctx.manifest.get("guidelines.instruction_files")
    named = ctx.host_instruction_files if configured is None else configured
    found: list[str] = []
    for entry in named:
        rel = guidelines_relpath(ctx, entry)
        if rel is None:
            raise RenderRefused(
                f"the instruction file {entry!r} names nothing inside the project",
                "set guidelines.instruction_files to project-relative paths",
            )
        if PurePosixPath(rel).suffix != ".md":
            raise RenderRefused(
                f"the instruction file {rel} is not a markdown file, and the "
                f"block markers are markdown comments",
                "name .md files in guidelines.instruction_files",
            )
        if rel in (source, checklist):
            what = "ruleset" if rel == source else "checklist"
            raise RenderRefused(
                f"the instruction file {rel} is also the guidelines {what}",
                "name a different file in guidelines.instruction_files",
            )
        if is_exempt_path(ctx.manifest, tree, tree / rel):
            raise RenderRefused(
                f"the instruction file {rel} is exempt from session isolation, "
                f"and a generated file never is (ADR-012 ruling 13)",
                "name a file outside session.exempt_paths and the declared stores",
            )
        if rel not in found:
            found.append(rel)
    return found


def _plan_block(
    tree: Path, rel: str, body: str, insert: tuple[str, ...], overwrite_local: bool
) -> BlockPlan:
    name = guidelines_reader.INDEX_BLOCK
    path = tree / rel
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        block = guidelines_reader.find_blocks(existing).get(name)
    except guidelines_reader.BlockError as exc:
        raise RenderRefused(
            f"{rel} {exc}; nothing was written",
            "repair the pair by hand (a begin and an end line per block, "
            "never nested), then render again",
        ) from exc
    if block is None:
        if name not in insert:
            return BlockPlan(BlockRendered(rel, name, "absent", False), path, None)
        text = guidelines_reader.append_block(existing, name, body)
        return BlockPlan(BlockRendered(rel, name, "new", True), path, text)
    state = guidelines_reader.block_state(block, body)
    if state == "edited" and not overwrite_local:
        return BlockPlan(BlockRendered(rel, name, state, False), path, None)
    text = guidelines_reader.replace_block(existing, block, body)
    return BlockPlan(BlockRendered(rel, name, state, True), path, text)


def _default_checklist(ctx: RuleContext, source: str, suffix: str, tree: Path) -> str:
    """Where a null ``guidelines.checklist`` puts the checklist: beside the
    ruleset, or under the mandate in the docs project's source directory,
    beside ``rules.rst``, where the starter's toctree names both (ADR-018
    D12)."""
    if ctx.manifest.get("docs.needs.state") == "enforced":
        srcdir = _docs_project_relpath(ctx, tree)
        if srcdir is not None:
            return posixpath.join(srcdir, f"checklist{suffix}")
    return posixpath.join(posixpath.dirname(source), f"checklist{suffix}")


def _generated_status(path: Path, rel: str, fmt: str, text: str, setting: str) -> str:
    if not path.exists():
        return "new"
    existing = path.read_text(encoding="utf-8")
    digest, body = guidelines_reader.split_managed(existing, fmt)
    if digest is None:
        raise RenderRefused(
            f"{rel} exists and has no invariant-managed marker, so render "
            f"did not write it and will not overwrite it",
            f"move that file, or point {setting} at another path"
            if setting.startswith("guidelines.")
            else f"move that file: it is {setting}",
        )
    if digest != guidelines_reader.digest_of(body.encode("utf-8")):
        return "edited"
    return "current" if existing == text else "stale"


def _always_layer_gap(plan: RenderPlan, manifest: Manifest) -> tuple[str, str] | None:
    """``(message, fix)`` when always rules exist and no block carries them.

    The A5 ruling D12. The managed block is where a host reads the always
    rules as standing instructions, so a ruleset with always rules and no
    instruction file carrying the block has lost that layer, though the turn
    index still lists them (A5.2). The files are the plan's, so the manifest's
    ``guidelines.instruction_files`` or the host default the context carries;
    nothing here names a host's file. A block that is present counts whatever
    its state: a stale or hand-edited one is drift, the gate's own finding.
    ``None`` with no always rules or with a block present, and with no file
    to carry one under ``records.agents: local``: nothing of invariant's is
    committed there, so no block is written, and the hook context carries the
    always rules by design (ADR-014 D11).
    """
    if not plan.always or any(b.result.status != "absent" for b in plan.blocks):
        return None
    if not plan.blocks and manifest.get("records.agents") == "local":
        return None
    name = guidelines_reader.INDEX_BLOCK
    count = len(plan.always)
    rules = f"{count} always rule{'' if count == 1 else 's'}"
    command = f"invariant guidelines render --insert {name}"
    if not plan.blocks:
        return (
            (
                f"{rules} but no instruction file is configured to carry the {name} "
                f"block; the turn index still lists them on every turn"
            ),
            (
                f"set guidelines.instruction_files to the project's instruction file, "
                f"then: {command}"
            ),
        )
    files = ", ".join(b.result.relpath for b in plan.blocks)
    return (
        (
            f"{rules} but no instruction file carries the {name} block ({files}); "
            f"the turn index still lists them on every turn"
        ),
        command,
    )


def always_layer_summary(ctx: RuleContext) -> str | None:
    """``doctor``'s ``guidelines always layer`` row, ``None`` for no row.

    A warning, never a failure (the A5 ruling D12). No row with no ruleset,
    with no always rules, with a block present, or when the render plan is
    refused: that refusal is the ``guidelines generated`` row's warning, and
    a ruleset that does not load is the ``guidelines ruleset`` row's failure.
    """
    try:
        plan = plan_render(ctx)
    except RenderRefused:
        return None
    gap = None if plan is None else _always_layer_gap(plan, ctx.manifest)
    if gap is None:
        return None
    message, fix = gap
    return f"{message}; try: {fix}"


@rule(
    "guidelines.generated_current",
    summary="Every generated guidelines output matches what render would write now.",
    events=(),
)
def _guidelines_generated_current(ctx: RuleContext) -> Verdict:
    """ADR-012 D3, "Drift is a rule". It reports and never renders.

    The checklist and each ``guidelines/index`` block are compared with the
    render plan, so "current" is what ``guidelines render --dry-run`` says. A
    block nobody inserted is no finding: render never inserts one unasked.
    A refusal fails the rule as well, because drift render refuses to fix
    cannot be fixed by the command this rule points at. Always rules that no
    instruction file's block carries are noted in the detail and never fail
    it (the A5 ruling D12).
    """
    try:
        plan = plan_render(ctx)
    except RenderRefused as exc:
        return Verdict.fail(
            f"the generated guidelines outputs cannot be checked: {exc.reason}",
            detail="This is what `invariant guidelines render` refuses, so drift "
            "here could not be fixed by rendering.",
            fixes=(exc.fix,),
        )
    if plan is None:
        return Verdict.na("no guidelines ruleset configured")

    findings: list[str] = []
    pages = [(plan.relpath, plan.status, "the checklist")]
    if plan.rules is not None:
        pages.append((plan.rules.relpath, plan.rules.status, "the rules page"))
    for relpath, status, what in pages:
        said = {
            "new": f"{relpath} is missing: {what} was never rendered",
            "stale": f"{relpath} is stale: rendered from an older ruleset",
            "edited": f"{relpath} was edited by hand",
        }
        if status in said:
            findings.append(said[status])
    edited_block = False
    absent: list[str] = []
    for block in plan.blocks:
        result = block.result
        where = f"{result.relpath} ({result.name} block)"
        if result.status == "stale":
            findings.append(f"{where} is stale: rendered from an older ruleset")
        elif result.status == "edited":
            findings.append(f"{where} was edited by hand")
            edited_block = True
        elif result.status == "absent":
            absent.append(f"no {result.name} block in {result.relpath} (none inserted)")
    gap = _always_layer_gap(plan, ctx.manifest)
    note = [f"{gap[0]}; try: {gap[1]}"] if gap else []

    if not findings:
        current = len(pages) + len(plan.blocks) - len(absent)
        return Verdict.clean(
            f"{current} generated output(s) current against ruleset {plan.ruleset}",
            detail="; ".join(absent + note) or None,
        )
    hand_edited = any(status == "edited" for _rel, status, _what in pages) or edited_block
    fixes = [
        (
            "move the edit into the ruleset, then: invariant guidelines render"
            if hand_edited
            else "invariant guidelines render"
        )
    ]
    if edited_block:
        fixes.append(
            "invariant guidelines render --overwrite-local  # takes the rendered "
            "block back over the hand edit"
        )
    return Verdict.fail(
        f"{len(findings)} generated guidelines output(s) not current against ruleset "
        f"{plan.ruleset}: " + "; ".join(findings),
        detail=" ".join([
            (
                "The ruleset is the source of every generated output (ADR-012 D3). "
                "This rule reports; it never renders."
            ),
            *(f"Also: {line}" for line in note),
        ]),
        fixes=tuple(fixes),
    )


@rule(
    "guidelines.mechanical_clean",
    summary="No new or unanswerable blocker finding of a pattern, boundary or cochange guideline.",
    events=(),
)
def _guidelines_mechanical_clean(ctx: RuleContext) -> Verdict:
    """ADR-012 D4, D5 and D12, and the A3 rulings DN-2 to DN-4, DN-13, DN-16.

    The ``pattern``, ``boundary`` and ``cochange`` guidelines evaluated over
    the changed files only (D4), read from the tree in hand. ``core/findings``
    evaluates and this rule does every read: the bytes, the mask through the
    declared adapters, the references through them, the changed set, and
    what git knows about the change (the merge base, the lines it added, a
    file's bytes there). A new or unanswerable finding of a blocker rule, or
    of a registry class mapped to blocker, fails it. A warn finding is
    reported inside a pass. Every finding is printed in D5's record shape and
    kept nowhere.

    The baseline (D6, A3 increment 5) is read first, in full or not at all,
    and a baseline that does not read fails the rule. A pattern or boundary
    finding it records is baselined and never counted, whether or not its
    file is touched (ruling 5); a cochange finding never has an entry to be
    in (DN-4). A recorded entry of a touched file that no finding holds is
    stale, and is reported in the detail only: failing it is
    ``guidelines.baseline_only_shrinks``' (DN-5).

    A live grant naming a guideline (A3.7, ruling 4) passes that guideline's
    new blocker findings in this evaluation, as ``overridden``, and the
    verdict names the grant; an unused grant is named too. Every other
    finding stands, an unanswerable one of that guideline included. The
    grant is read here and never spent: the CLI spends it at the advance it
    lets through.
    """
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        return Verdict.fail(
            f"no guideline can be evaluated: {failure.message}",
            detail=failure.detail,
            fixes=failure.fixes,
        )
    if ruleset is None:
        return Verdict.na("no guidelines ruleset configured")
    baseline, broken = load_baseline(ctx, ruleset)
    if broken is not None:
        return Verdict.fail(
            f"no guideline can be evaluated: {broken.message}",
            detail=broken.detail,
            fixes=broken.fixes,
        )
    changed = _changed_set(ctx)
    if not changed.answerable:
        # Stricter than `docs.updated`, which reads a partial set when the
        # working tree is dirty: a finding in a committed file nobody can
        # list would pass unseen.
        return _unestablished_changed_set(changed)

    tree = guidelines_tree(ctx)
    adapters = _ecosystem_adapters(ctx)
    files, gone = _changed_in_tree(ctx, changed, tree)
    evaluation = findings_reader.evaluate(
        ruleset.rules,
        files,
        **_finding_reads(tree, adapters),
        gone=gone,
        history=_change_history(ctx, tree, changed.base, adapters),
        registries=ruleset.registries,
    )
    ratcheted = findings_reader.ratchet(
        evaluation.findings, baseline or (), touched=set(files) | set(gone)
    )
    grants = guideline_grants(ctx, ruleset)
    found = findings_reader.overridden(ratcheted.findings, grants)
    records = tuple(f.record(ruleset.digest) for f in found)
    blocking = [f for f in found if f.blocks]
    warned = [f for f in found if f.counts and not f.blocks]
    baselined = [f for f in found if f.status == "baselined"]
    excepted = [f for f in found if f.status == "excepted"]
    passed = [f for f in found if f.status == "overridden"]
    detail = _mechanical_detail(
        evaluation, warned, excepted, baselined, ratcheted.stale,
        overridden=passed, grants=grants,
    )
    counted = [f"{len(baselined)} baselined"] if baselined else []
    if passed:
        counted.append(f"{len(passed)} passed by override")
    overridden = _passed_findings(passed)
    if blocking:
        held = f" ({', '.join(counted)})" if counted else ""
        return Verdict(
            ok=False,
            message=f"{len(blocking)} new or unanswerable blocker finding(s){held}: "
            + "; ".join(f.describe() for f in blocking),
            detail=detail,
            fixes=_mechanical_fixes(blocking),
            findings=records,
            overridden=overridden,
            ruleset=ruleset.digest,
            grants=tuple(grants.items()),
        )
    return Verdict(
        ok=True,
        message=f"no new blocker finding in {evaluation.scanned} changed file(s) "
        f"under {evaluation.rules} mechanical guideline(s), "
        f"ruleset {ruleset.digest}" + "".join(f"; {part}" for part in counted),
        detail=detail,
        findings=records,
        overridden=overridden,
        ruleset=ruleset.digest,
        grants=tuple(grants.items()),
    )


def _passed_findings(
    passed: Iterable[findings_reader.Finding],
) -> tuple[tuple[str, str], ...]:
    """Each guideline whose new blocker findings a grant passed, with where
    they are: what the gate would have blocked on without the grant."""
    places: dict[str, list[str]] = {}
    for f in passed:
        where = f.path if f.line is None else f"{f.path}:{f.line}"
        places.setdefault(f.rule, []).append(where)
    return tuple((rule_id, ", ".join(found)) for rule_id, found in places.items())


#: The gates a grant naming a guideline passes, one per kind of answer
#: (ADR-012 ruling 4, D12): the findings of a pattern, boundary or cochange
#: guideline, and the verdict of a judgement guideline (A4 increment 5).
MECHANICAL_GATE = "guidelines.mechanical_clean"
VERDICTS_GATE = "guidelines.verdicts_complete"


def guideline_family_gate(guideline: guidelines_reader.Guideline) -> str | None:
    """The gate a grant naming ``guideline`` passes, or ``None`` for one no
    grant passes (an ``engine`` guideline, answered by its own rule)."""
    if guideline.check.kind == "judgement":
        return VERDICTS_GATE
    if guideline.check.kind in findings_reader.EVALUATED_KINDS:
        return MECHANICAL_GATE
    return None


def guideline_gate_of(ctx: RuleContext, guideline_id: str) -> str | None:
    """The family gate a grant naming ``guideline_id`` passes, read from the
    ruleset of the tree in hand; ``None`` when no ruleset loads there or it
    names no such guideline, so a caller says "its family gate"."""
    ruleset, failure = load_ruleset(ctx)
    if failure is not None or ruleset is None:
        return None
    guideline = ruleset.by_id(guideline_id)
    return None if guideline is None else guideline_family_gate(guideline)


def guideline_grants(
    ctx: RuleContext,
    ruleset: guidelines_reader.Ruleset | None = None,
    *,
    judgement: bool = False,
) -> dict[str, str]:
    """The live grants naming a guideline, each with the reason it gives.

    ADR-012 D12, ruling 4. A grant is held in ``session.pending_overrides``
    beside the grants that name a rule, in the same shape; one naming no
    registered rule names a guideline, since no guideline id may be a rule
    id (DN-15). This only READS the grant: spending it is the delivery
    surface's, the CLI's at a pipeline advance, and asking a rule must not
    change the world.

    With ``ruleset``, the grants are split by family gate, so each gate
    names only its own (A4.5): ``judgement=True`` keeps the grants naming a
    judgement guideline, for ``guidelines.verdicts_complete``, and
    ``judgement=False`` every other, for ``guidelines.mechanical_clean``,
    which keeps naming a grant whose guideline left the ruleset (A3.7).
    """
    grants: dict[str, str] = {}
    for held in (ctx.session or {}).get("pending_overrides") or ():
        if not isinstance(held, Mapping):
            continue
        rule_id = held.get("rule")
        if not isinstance(rule_id, str) or rule_id in _REGISTRY:
            continue
        if ruleset is not None:
            guideline = ruleset.by_id(rule_id)
            judged = guideline is not None and guideline.check.kind == "judgement"
            if judged != judgement:
                continue
        grants[rule_id] = str(held.get("reason") or "no reason given")
    return grants


@dataclass(frozen=True)
class GrantOutcome:
    """What one advance did with one grant a guideline family gate read."""

    guideline: str
    gate: str
    #: The grant's reason, as the gate read it; ``None`` for a passed
    #: guideline the verdict names without the grant behind it.
    reason: str | None
    #: The advance relied on it, so it is spent; otherwise it is held.
    spent: bool
    #: What the gate would have failed on without it; ``None`` when it
    #: passed nothing that would have failed the gate.
    passed: str | None
    #: Why it is held; empty when it is spent.
    why: str

    def record(self) -> dict[str, Any]:
        return {
            "guideline": self.guideline, "gate": self.gate, "reason": self.reason,
            "outcome": "spent" if self.spent else "held",
            "passed": self.passed, "why": self.why,
        }


def grant_outcomes(evaluated: Evaluated) -> tuple[GrantOutcome, ...]:
    """Each grant ``evaluated`` read or relied on, and whether an advance
    that succeeded with this verdict spends it (ADR-012 ruling 4, A3.7).

    The one decision: ``cli/main`` spends what it answers spent, and a
    landing records it (A4.6). A grant is spent only where it decided the
    advance: the gate blocks or asks here, passed, and the grant passed a
    guideline that would otherwise have failed it. At ``warn`` nothing was
    stopped, a gate still failing on another guideline was forced past, and
    a grant that passed nothing decided nothing; each is held, with why.
    Grants come in the order the gate read them, then any passed guideline
    the verdict names without one.
    """
    verdict = evaluated.verdict
    gate = evaluated.rule_id
    stopping = evaluated.level in (Level.BLOCK, Level.ASK)
    decided = stopping and verdict.ok and not verdict.skipped
    reasons = dict(verdict.grants)
    passed = dict(verdict.overridden)
    out: list[GrantOutcome] = []
    for guideline in [*reasons, *(g for g in passed if g not in reasons)]:
        stopped_on = passed.get(guideline)
        if stopped_on is None:
            why = f"it passed nothing that would have failed {gate}"
        elif decided:
            why = ""
        elif stopping:
            why = f"{gate} still failed on another guideline, so the grant passed nothing"
        else:
            why = f"{gate} is {evaluated.level.value} here, so nothing was stopped"
        out.append(GrantOutcome(
            guideline=guideline, gate=gate, reason=reasons.get(guideline),
            spent=stopped_on is not None and decided, passed=stopped_on, why=why,
        ))
    return tuple(out)


def guideline_override_refusal(ctx: RuleContext, guideline_id: str) -> str | None:
    """Why ``invariant override <guideline_id>`` may not be granted, or ``None``.

    ADR-012 ruling 4 and D12. The id must name a guideline in the ruleset of
    the tree in hand, read as the guideline gates read it, and the guideline
    must be one its family gate can be stopped by: a pattern, boundary or
    cochange guideline at blocker (``guidelines.mechanical_clean``), or a
    judgement guideline whose phases owe a verdict, at either severity
    (``guidelines.verdicts_complete``, A4.5). A grant nothing could ever
    spend is refused rather than held: a control handed back that silently
    does nothing is worse than one never offered (ADR-001 D25).
    """
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        detail = f" ({failure.detail})" if failure.detail else ""
        return (
            f"no rule {guideline_id!r}, and no guideline can be named: "
            f"{failure.message}{detail}"
        )
    if ruleset is None:
        return (
            f"no rule {guideline_id!r}, and no guidelines ruleset is configured, so "
            f"it names no guideline either; 'invariant rules list' shows the rules"
        )
    guideline = ruleset.by_id(guideline_id)
    if guideline is None:
        ids = [r.id for r in ruleset.rules]
        near = get_close_matches(guideline_id, ids, n=1, cutoff=0.6)
        hint = f"; did you mean {near[0]!r}?" if near else ""
        return (
            f"no rule or guideline {guideline_id!r}{hint} 'invariant rules list' "
            f"shows the rules, and 'invariant guidelines show' the guidelines"
        )
    gate = guideline_family_gate(guideline)
    if gate is None:
        return (
            f"guideline {guideline_id!r} is enforced by the rule "
            f"{guideline.check.rule}, so override that: "
            f"invariant override {guideline.check.rule} --reason '<why>'"
        )
    if gate == VERDICTS_GATE:
        # A4.5, DN-9: a judgement guideline's missing or stale verdict fails
        # the gate at any severity, so a warn one can be stopped too. One
        # whose phases owe no verdict is never asked about at all.
        if not set(guideline.phases) & verdicts_reader.OWED_PHASES:
            return (
                f"guideline {guideline_id!r} is never owed a verdict: its phases "
                f"({', '.join(guideline.phases)}) name neither review, landing "
                f"nor always, so {VERDICTS_GATE} never asks about it and a grant "
                f"for it could never be spent"
            )
        return None
    if guideline.severity != "blocker":
        # A registry class's outcome never exceeds its rule's severity, so a
        # warn guideline's findings never block, whatever its kind.
        return (
            f"guideline {guideline_id!r} is a warn guideline whose findings never "
            f"block, so there is nothing for a grant to pass"
        )
    return None


def _finding_reads(tree: Path, adapters: list[Adapter]) -> dict[str, Any]:
    """The reads ``core/findings`` is handed, from the tree in hand: one
    answer for the gate, ``--record`` and ``--prune``, so a record holds
    exactly what the gate then finds."""
    return {
        "read": lambda rel: _read_bytes(tree / rel),
        "mask": lambda rel, text, keep: _lexical_mask(adapters, Path(rel), text, keep),
        "references": lambda rel: _references(adapters, tree / rel, Path(rel), tree),
    }


def _changed_in_tree(
    ctx: RuleContext, changed: _ChangedSet, tree: Path
) -> tuple[list[str], list[str]]:
    """The changed paths, project-relative: those that are files in the tree,
    and those that are not.

    A deleted file has nothing to match and imports nothing, so only a
    cochange reads the second list: removing a file is a change. The
    spelling is the one every declared glob is compared in, and a declared
    absolute path is spelled against the project's checkouts.
    """
    files: set[str] = set()
    gone: set[str] = set()
    for path in changed.paths:
        rel = _tree_relative(ctx, tree, path)
        if rel is None:
            continue
        (files if (tree / rel).is_file() else gone).add(rel)
    return sorted(files), sorted(gone)


def _tree_relative(ctx: RuleContext, tree: Path, path: Path | str) -> str | None:
    bases = [tree, ctx.manifest.root, git.toplevel(ctx.cwd), git.main_checkout(ctx.cwd)]
    rel = project_relative(Path(path).as_posix(), bases)
    return None if rel in (None, ".") else rel


def _change_history(
    ctx: RuleContext, tree: Path, base: str, adapters: list[Adapter]
) -> findings_reader.History:
    """What git knows about the change, read from the tree in hand.

    Measured from where the branch left its base, as ``_changed_set``'s
    commit leg is. With no base recorded every answer is ``None``, so a
    cochange rule that needs one is unanswerable rather than passed. The
    merge base is asked once, and the added lines once, and only when a rule
    needs them.
    """
    asked: dict[str, Any] = {}

    def merge_base() -> str | None:
        if "base" not in asked:
            asked["base"] = git.merge_base(tree, base) if base else None
        return asked["base"]

    def existed(rel: str) -> bool | None:
        point = merge_base()
        return None if point is None else git.exists_at(tree, point, rel)

    def before(rel: str) -> bytes | None:
        point = merge_base()
        return None if point is None else git.blob_at(tree, point, rel)

    def lines() -> dict[str, Any] | None:
        answer = git.changed_lines(tree, base) if base else None
        if answer is None:
            return None
        spelled: dict[str, Any] = {}
        for path, added in answer.items():
            rel = _tree_relative(ctx, tree, path)
            if rel is not None:
                spelled[rel] = added
        return spelled

    return findings_reader.History(
        existed=existed,
        before=before,
        lines=lines,
        executable=lambda rel, text: _executable_text(adapters, Path(rel), text),
    )


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _mechanical_detail(
    evaluation: findings_reader.Evaluation,
    warned: list[findings_reader.Finding],
    excepted: list[findings_reader.Finding],
    baselined: Iterable[findings_reader.Finding] = (),
    stale: Iterable[findings_reader.Stale] = (),
    *,
    overridden: Iterable[findings_reader.Finding] = (),
    grants: Mapping[str, str] | None = None,
) -> str:
    parts: list[str] = []
    overridden = list(overridden)
    grants = dict(grants or {})
    for guideline, reason in grants.items():
        mine = [f for f in overridden if f.rule == guideline]
        if mine:
            parts.append(
                f"{len(mine)} passed by override: "
                + "; ".join(f.describe() for f in mine)
                + f", granted with `invariant override {guideline}` (reason: {reason}); "
                "the pipeline advance this lets through spends the grant"
            )
        else:
            parts.append(
                f"override held for {guideline} (reason: {reason}), unused here: "
                f"{guideline} has no new blocker finding in this change, so "
                f"nothing spends it; withdraw it with "
                f"invariant override {guideline} --clear"
            )
    baselined = list(baselined)
    if baselined:
        parts.append(
            f"{len(baselined)} baselined: " + "; ".join(f.describe() for f in baselined)
        )
    stale = list(stale)
    if stale:
        noun = "entry" if len(stale) == 1 else "entries"
        parts.append(
            f"{len(stale)} stale baseline {noun}, which no finding holds any more: "
            + "; ".join(s.describe() for s in stale)
            + "; prune them in this change: invariant guidelines baseline --prune"
        )
    if warned:
        parts.append(
            f"{len(warned)} warn finding(s): " + "; ".join(f.describe() for f in warned)
        )
    if excepted:
        parts.append(
            f"{len(excepted)} excepted: "
            + "; ".join(f"{f.describe()} ({f.note})" for f in excepted)
        )
    if evaluation.not_text:
        parts.append(
            "not UTF-8 text, so no pattern applies: " + ", ".join(evaluation.not_text)
        )
    return ". ".join(parts)


def _mechanical_fixes(blocking: list[findings_reader.Finding]) -> tuple[str, ...]:
    fixes = []
    if any(f.status == "new" and f.kind != "cochange" for f in blocking):
        fixes.append(
            "change the code so the guideline holds, or give the finding a reasoned "
            "exception in the ruleset (its path, and its fingerprint from "
            "`invariant check guidelines.mechanical_clean --json`)"
        )
    passable = sorted({f.rule for f in blocking if f.status == "new"})
    for guideline in passable:
        fixes.append(
            f"or pass {guideline}'s new findings for one advance, recorded: "
            f"invariant override {guideline} --reason '<why>'"
        )
    if any(f.status == "unanswerable" for f in blocking):
        fixes.append(
            "make the file answerable: declare the ecosystem whose adapter reads it, "
            "or fix what stops it being read; an unanswered file never passes"
        )
    for finding in blocking:
        if finding.fix and finding.fix not in fixes:
            fixes.append(finding.fix)
    return tuple(fixes)


# -- the edit-time advisory (ADR-012 ruling 9, D7, DN-8) -----------------------

#: The kinds advised at an edit: a finding about one file's lines. A cochange
#: describes the whole change, so it is judged only at the landing.
ADVISED_KINDS = ("pattern", "boundary")

#: How much of a matched line an advised finding quotes.
ADVISED_LINE_CHARS = 160


def edit_advisory(
    manifest: Manifest, target: Path, context: Callable[[], RuleContext]
) -> guidelines_reader.EditAdvisory | None:
    """What the edit that just landed on ``target`` adds at the landing gate.

    ADR-012 ruling 9, D7 and DN-8. A projection of ``guidelines.mechanical_clean``
    over one file: its ``pattern`` and ``boundary`` guidelines, with the
    baseline ratchet applied, so only a finding the landing would count as new
    is named. Never a deny, a rule event or a verdict, so no level applies.
    ``cochange`` is never evaluated here: it describes the whole change, and
    its second file may come later.

    ``None`` is silence: no ruleset (decided from the manifest alone, so no
    file is read, no git is asked and ``context`` is never called), an edit
    outside the tree the gate reads, a file no advised guideline covers, or
    no new finding. An unanswerable file, and a ruleset or baseline that does
    not load, is one short ``problem`` line. Any exception inside is caught
    and becomes that line too, so a hook that asks this can never fail on it.
    """
    if not manifest.get("guidelines.ruleset"):
        return None
    try:
        return _edit_advisory(context(), target)
    except Exception as exc:  # noqa: BLE001 - an advisory may never fail its hook
        return guidelines_reader.EditAdvisory(
            path=target.name,
            problem=(
                f"the guidelines advisory could not be computed for this edit "
                f"({type(exc).__name__}); guidelines.mechanical_clean still "
                f"evaluates it at landing"
            ),
        )


def _edit_advisory(ctx: RuleContext, target: Path) -> guidelines_reader.EditAdvisory | None:
    tree = guidelines_tree(ctx)
    rel = _inside_tree(tree, target if target.is_absolute() else ctx.cwd / target)
    if rel is None:
        return None
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        return guidelines_reader.EditAdvisory(path=rel, problem=_unloaded_at_edit(rel, failure))
    advised = [
        rule for rule in ruleset.rules
        if rule.check.kind in ADVISED_KINDS
        and any(findings_reader.matches(rel, glob) for glob in rule.applies_to)
    ]
    if not advised or not (tree / rel).is_file():
        return None
    baseline, broken = load_baseline(ctx, ruleset)
    if broken is not None:
        return guidelines_reader.EditAdvisory(
            path=rel,
            problem=f"the guidelines baseline does not load, so {rel} was not checked: "
            f"{broken.message}",
        )
    raw = _read_bytes(tree / rel)
    reads = _finding_reads(tree, _ecosystem_adapters(ctx))
    reads["read"] = lambda _rel: raw
    evaluation = findings_reader.evaluate(advised, [rel], **reads)
    ratcheted = findings_reader.ratchet(evaluation.findings, baseline or (), touched={rel})
    lines = (raw or b"").decode("utf-8", errors="replace").split("\n")
    new = tuple(
        _advised(f, lines) for f in ratcheted.findings if f.status == "new"
    )
    unanswerable = [f for f in ratcheted.findings if f.status == "unanswerable"]
    problem = None
    if unanswerable:
        ids = ", ".join(dict.fromkeys(f.rule for f in unanswerable))
        notes = "; ".join(dict.fromkeys(f.note for f in unanswerable))
        problem = f"{rel} could not be checked against {ids}: {notes}"
    if not new and problem is None:
        return None
    return guidelines_reader.EditAdvisory(path=rel, findings=new, problem=problem)


def _inside_tree(tree: Path, target: Path) -> str | None:
    """``target`` relative to ``tree`` in POSIX spelling, or ``None`` outside it.

    Resolved on both sides, so a symlinked prefix does not move a file out of
    the tree. The spelling is taken as git and the gate's changed set give
    it, without trimming, so a name with whitespace at an end is read as the
    file it is.
    """
    try:
        relative = target.resolve().relative_to(tree.resolve())
    except (OSError, ValueError):
        return None
    rel = relative.as_posix()
    return None if rel in ("", ".") else rel


def _advised(
    finding: findings_reader.Finding, lines: list[str]
) -> guidelines_reader.AdvisedFinding:
    line = finding.line or 0
    text = findings_reader.collapse(lines[line - 1]) if 0 < line <= len(lines) else ""
    if len(text) > ADVISED_LINE_CHARS:
        text = text[: ADVISED_LINE_CHARS - 3] + "..."
    where = f"path {finding.path}, fingerprint {finding.fingerprint}"
    if finding.target is not None:
        fix = (
            f"remove the reference to {finding.target}, or give it a reasoned "
            f"exception in the ruleset ({where})"
        )
    else:
        fix = (
            f"change line {line} so {finding.rule} holds, or give it a reasoned "
            f"exception in the ruleset ({where})"
        )
    return guidelines_reader.AdvisedFinding(
        rule=finding.rule, severity=finding.severity, path=finding.path,
        line=finding.line, text=text, target=finding.target, fix=fix,
    )


def _unloaded_at_edit(rel: str, failure: Verdict) -> str:
    """The one line an edit carries when the ruleset does not load.

    Both halves of what an edit carries say it in these words, so the
    renderer says it once.
    """
    return (
        f"the guidelines ruleset does not load, so {rel} was not checked: "
        f"{failure.message}; see invariant check guidelines.ruleset_valid"
    )


# -- the edit layer (ADR-012 ruling 8, D7, the A5 rulings D4 to D9) -------------------


def edit_guidelines(
    manifest: Manifest, target: Path, context: Callable[[], RuleContext]
) -> guidelines_reader.EditGuidelines | None:
    """The judgement rules whose statements the edit on ``target`` carries.

    The A5 rulings D4 and D5: only judgement rules, and every matching edit
    carries them, since nothing here remembers an earlier one. A rule is
    carried when ``core/guidelines.select`` picks it for the current state's
    roles over the one edited file: its phases meet the state's (D1, D2), its
    ``applies_to`` covers the file and no path exception sets the file aside.
    With no pipeline only the always rules are carried, as the turn index
    lists them alone.

    ``None`` is silence: no ruleset or ``guidelines.injection.on_edit`` false
    (both decided from the manifest alone, so no file is read, no git is
    asked and ``context`` is never called), an edit outside the tree the
    gates read, a path that is no file, a landing or abandon state, and no
    judgement rule for the file. A ruleset that does not load is one
    ``problem`` line, worded as :func:`edit_advisory` words it. Any exception
    inside is one line too, so a hook that asks this can never fail on it.
    """
    if not manifest.get("guidelines.ruleset"):
        return None
    if manifest.get("guidelines.injection.on_edit") is False:
        return None
    try:
        return _edit_guidelines(context(), target)
    except Exception as exc:  # noqa: BLE001 - a projection may never fail its hook
        return guidelines_reader.EditGuidelines(
            path=target.name,
            problem=(
                f"the guideline statements could not be computed for this edit "
                f"({type(exc).__name__}); the ruleset is printed by: invariant guidelines show"
            ),
        )


def _edit_guidelines(
    ctx: RuleContext, target: Path
) -> guidelines_reader.EditGuidelines | None:
    tree = guidelines_tree(ctx)
    rel = _inside_tree(tree, target if target.is_absolute() else ctx.cwd / target)
    if rel is None or not (tree / rel).is_file():
        return None
    pipeline = ctx.pipeline
    roles = _state_roles(pipeline, ctx.manifest) if pipeline else ()
    phases = guidelines_reader.phases_for(roles) if pipeline else ("always",)
    if not phases:
        return None
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        return guidelines_reader.EditGuidelines(path=rel, problem=_unloaded_at_edit(rel, failure))
    carried = tuple(
        rule for rule in guidelines_reader.select(ruleset, roles, (rel,))
        if rule.check.kind == "judgement" and set(phases).intersection(rule.phases)
    )
    if not carried:
        return None
    return guidelines_reader.EditGuidelines(
        path=rel,
        phases=phases,
        rules=carried,
        show=f"invariant guidelines show --phase {_show_phase(roles)} --files {shlex.quote(rel)}",
    )


# -- the baseline (ADR-012 D6) ------------------------------------------------
#
# The ratchet's record of pre-existing pattern and boundary findings, named by
# `guidelines.baseline` (D13). Read in full or not at all by every gate that
# reads it (`load_baseline`), and written only by `guidelines baseline`, the
# generated-file write class (ruling 13): `plan_baseline` decides, checks and
# scans everything before `domains/guidelines` writes, the shape of
# `plan_render`. The engine never writes it at the landing; gates only ask.


class BaselineRefused(RenderRefused):
    """A ``guidelines baseline`` that would write somewhere it may not, write
    text it may not, or write a record it could not compute in full.

    Raised while the write is planned, before anything is written. A
    :class:`RenderRefused`, because the baseline is a file of the same write
    class as the checklist, and it refuses what render refuses.
    """


def load_baseline(
    ctx: RuleContext, ruleset: guidelines_reader.Ruleset
) -> tuple[tuple[guidelines_reader.BaselineEntry, ...] | None, Verdict | None]:
    """The recorded baseline, the failure that stands in for it, or neither.

    ``(None, None)`` with ``guidelines.baseline`` null: no baseline, and every
    pattern or boundary finding is new (DN-1). Anything short of a baseline
    that reads in full against ``ruleset`` is ``(None, verdict)``, and the
    caller FAILS on it: a path naming nothing inside the project, a path
    isolation exempts (a generated file never is, ruling 13), a configured
    file that does not exist, and a file with any line that does not parse or
    names a rule it may not. Never a smaller record. It is read from the tree
    in hand, as the ruleset is.
    """
    configured = ctx.manifest.get("guidelines.baseline")
    if not configured:
        return None, None
    rel = guidelines_relpath(ctx, configured)
    if rel is None:
        return None, Verdict.fail(
            f"guidelines.baseline {configured!r} names nothing inside the project",
            detail="The baseline is reviewed with the change like the ruleset is; a "
            "path outside every checkout of this repository is not this project's.",
            fixes=(
                (
                    "set guidelines.baseline to a project-relative path, such as "
                    + guidelines_example(ctx, "baseline.txt")
                ),
            ),
        )
    tree = guidelines_tree(ctx)
    path = tree / rel
    if is_exempt_path(ctx.manifest, tree, path):
        return None, Verdict.fail(
            f"the guidelines baseline {rel} is exempt from session isolation, and a "
            f"generated file never is (ADR-012 ruling 13)",
            detail="An exempt baseline could gain entries on the base branch with no "
            "review, which is a relaxation of every pattern and boundary rule.",
            fixes=(
                (
                    "move it outside session.exempt_paths and the declared stores, and "
                    "point guidelines.baseline at it"
                ),
            ),
        )
    if not path.is_file():
        return None, Verdict.fail(
            f"the configured guidelines baseline {rel} does not exist",
            detail="An absent file is not an empty baseline: it cannot be told apart "
            "from one that was never recorded, so the gates that read it fail (DN-1).",
            fixes=("invariant guidelines baseline --record", "or clear guidelines.baseline"),
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, Verdict.fail(
            f"the guidelines baseline {rel} could not be read",
            detail=f"{rel}: {exc}.",
            fixes=(f"check {rel} is readable UTF-8 text",),
        )
    try:
        entries = guidelines_reader.parse_baseline(text)
        problems = guidelines_reader.baseline_rule_problems(entries, ruleset)
    except guidelines_reader.BaselineError as exc:
        problems = list(exc.problems)
    if problems:
        return None, Verdict.fail(
            f"the guidelines baseline {rel} does not parse ({len(problems)} problem(s))",
            detail="; ".join(problems) + ". The gate will not answer from a record it "
            "read only part of: a dropped entry would stop being honoured, and with "
            "nothing in any verdict to say so.",
            fixes=(f"fix {rel}, or prune it: invariant guidelines baseline --prune",
                   "or record it again: invariant guidelines baseline --record"),
        )
    return entries, None


def baseline_summary(ctx: RuleContext) -> tuple[bool, str]:
    """``doctor``'s ``guidelines baseline`` row: ``(ok, message)``.

    ``ok`` false is a WARNING, never a failed diagnosis: an unreadable or
    malformed baseline is the gates' failure at the landing, and a ruleset
    that does not load is the ``guidelines ruleset`` row's.
    """
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        return False, f"cannot be checked: {failure.message}"
    if ruleset is None:
        return True, "no guidelines ruleset configured"
    configured = ctx.manifest.get("guidelines.baseline")
    if not configured:
        return True, "no baseline configured: every pattern or boundary finding is new"
    entries, broken = load_baseline(ctx, ruleset)
    if broken is not None:
        detail = f": {broken.detail}" if broken.detail else ""
        return False, broken.message + detail + "".join(
            f"; try: {fix}" for fix in broken.fixes
        )
    noun = "entry" if len(entries) == 1 else "entries"
    total = sum(entry.count for entry in entries)
    return True, (
        f"{len(entries)} {noun} holding {total} finding(s) in "
        f"{len({e.path for e in entries})} file(s) under "
        f"{len({e.rule for e in entries})} guideline(s), "
        f"{guidelines_relpath(ctx, configured)}"
    )


@dataclass(frozen=True)
class BaselineChange:
    """An entry ``guidelines baseline`` lowered, dropped or moved, and why."""

    rule: str
    path: str
    fingerprint: str
    before: int
    #: 0 when the entry is dropped.
    after: int
    reason: str
    #: The path the entry moved to, when the change renamed its file (DN-7).
    moved_to: str | None = None

    def describe(self) -> str:
        if self.moved_to is not None:
            return (
                f"moved {self.rule} {self.path} {self.fingerprint} to {self.moved_to}: "
                f"{self.reason}"
            )
        what = "dropped" if self.after == 0 else f"lowered {self.before} -> {self.after}"
        return f"{what} {self.rule} {self.path} {self.fingerprint}: {self.reason}"


@dataclass(frozen=True)
class BaselinePlan:
    """Everything one ``guidelines baseline`` would write, decided first."""

    #: ``record`` or ``prune``.
    mode: str
    #: ``new`` (no file yet), ``current`` (the text is unchanged) or ``changed``.
    status: str
    path: Path
    relpath: str
    #: The ruleset digest the findings were evaluated under.
    ruleset: str
    text: str
    entries: tuple[guidelines_reader.BaselineEntry, ...] = ()
    changes: tuple[BaselineChange, ...] = ()


BASELINE_MODES = ("record", "prune")


def plan_baseline(
    ctx: RuleContext, *, mode: str, rules: tuple[str, ...] = ()
) -> BaselinePlan | None:
    """What ``guidelines baseline`` would write, with nothing written.

    ``None`` with no ruleset configured. ``record`` scans every tracked file
    under each pattern and boundary rule's ``applies_to`` with the evaluator
    ``guidelines.mechanical_clean`` uses, and records what it finds; with
    ``rules`` it replaces only those rules' entries. ``prune`` recomputes the
    files the change touched: it lowers a count to what the file still holds,
    and drops an entry whose count reaches zero, whose file is deleted, whose
    rule is gone, or whose rule no longer applies to its file. It never adds
    an entry, and never raises a count.

    Every refusal is a :class:`BaselineRefused` raised before anything could
    be written: no ``guidelines.baseline`` (DN-1); a target outside the
    project, exempt from isolation, or another guidelines file; a rule that
    cannot be baselined; a record or a prune resting on a file nothing could
    answer for; an existing file a ``--rule`` record or a prune cannot read in
    full; and a privacy finding in the text.
    """
    if mode not in BASELINE_MODES:
        raise ValueError(f"mode must be one of {BASELINE_MODES}, got {mode!r}")
    if rules and mode != "record":
        raise BaselineRefused(
            "--rule names the rules a --record replaces; --prune takes none",
            "drop --rule, or record: invariant guidelines baseline --record --rule <id>",
        )
    ruleset = ruleset_or_refuse(ctx)
    if ruleset is None:
        return None
    tree = guidelines_tree(ctx)
    rel = _baseline_target(ctx, ruleset, tree)
    path = tree / rel
    try:
        existing = path.read_text(encoding="utf-8") if path.is_file() else None
    except (OSError, UnicodeDecodeError) as exc:
        raise BaselineRefused(
            f"the guidelines baseline {rel} could not be read ({exc})",
            f"check {rel} is readable UTF-8 text",
        ) from exc
    if mode == "record":
        entries, changes = _record_baseline(ctx, tree, ruleset, rules, existing, rel)
    else:
        entries, changes = _prune_baseline(ctx, tree, ruleset, existing, rel)
    try:
        text = guidelines_reader.render_baseline(entries)
    except guidelines_reader.BaselineError as exc:
        raise BaselineRefused(
            f"{'; '.join(exc.problems)}; nothing was written",
            "rename the file so its path has no line break and no space at either end",
        ) from exc
    found = PrivacyScanner.from_manifest(ctx.manifest).scan_text(text, path=Path(rel))
    if found:
        named = ", ".join(f"{rel}:{f.line} {f.pattern} {f.redacted}" for f in found)
        raise BaselineRefused(
            f"the baseline text fails the privacy scan ({named}); nothing was written",
            "rename the file the finding names, or record without its rule",
        )
    status = "new" if existing is None else ("current" if existing == text else "changed")
    return BaselinePlan(
        mode=mode, status=status, path=path, relpath=rel, ruleset=ruleset.digest,
        text=text, entries=tuple(entries), changes=tuple(changes),
    )


def _baseline_target(
    ctx: RuleContext, ruleset: guidelines_reader.Ruleset, tree: Path
) -> str:
    """Where the baseline is written, checked; a refusal otherwise.

    Never another file of the guidelines family: the ruleset, the checklist,
    an instruction file or a registry the ruleset names. Never exempt from
    isolation (ruling 13).
    """
    configured = ctx.manifest.get("guidelines.baseline")
    if not configured:
        raise BaselineRefused(
            "guidelines.baseline is not set, so there is no baseline to record or "
            "prune; with it null every pattern or boundary finding is new (DN-1)",
            "set guidelines.baseline to a project-relative path, such as "
            f"{guidelines_example(ctx, 'baseline.txt')}, then: invariant guidelines "
            "baseline --record",
        )
    rel = guidelines_relpath(ctx, configured)
    if rel is None:
        raise BaselineRefused(
            f"guidelines.baseline {configured!r} names nothing inside the project",
            "set guidelines.baseline to a project-relative path",
        )
    source = guidelines_relpath(ctx, ctx.manifest.get("guidelines.ruleset"))
    others = {source: "the guidelines ruleset"}
    checklist = _checklist_relpath(ctx, source)
    if checklist is not None:
        others.setdefault(checklist, "the guidelines checklist")
    named = ctx.manifest.get("guidelines.instruction_files")
    for entry in ctx.host_instruction_files if named is None else named:
        spelled = guidelines_relpath(ctx, entry)
        if spelled is not None:
            others.setdefault(spelled, "a guidelines instruction file")
    for registry in ruleset.registries:
        others.setdefault(registry.path, "a registry the ruleset names")
    if rel in others:
        raise BaselineRefused(
            f"guidelines.baseline {rel} is {others[rel]}; recording would overwrite it",
            "point guidelines.baseline at a file of its own",
        )
    if is_exempt_path(ctx.manifest, tree, tree / rel):
        raise BaselineRefused(
            f"the baseline {rel} is exempt from session isolation, and a generated "
            f"file never is (ADR-012 ruling 13)",
            "set guidelines.baseline to a path outside session.exempt_paths and the "
            "declared stores",
        )
    return rel


def _checklist_relpath(ctx: RuleContext, source: str | None) -> str | None:
    """The checklist's path as render would write it, or ``None`` when render
    could not say (a ``docs.format`` with no renderer, or no ruleset path)."""
    configured = ctx.manifest.get("guidelines.checklist")
    if configured is not None:
        return guidelines_relpath(ctx, configured)
    suffix = guidelines_reader.CHECKLIST_SUFFIXES.get(ctx.manifest.get("docs.format"))
    if suffix is None or source is None:
        return None
    return _default_checklist(ctx, source, suffix, guidelines_tree(ctx))


def _baselined_rules(
    ruleset: guidelines_reader.Ruleset, names: tuple[str, ...]
) -> list[guidelines_reader.Guideline]:
    """The rules a record scans: every pattern and boundary rule, or those named."""
    if not names:
        return [r for r in ruleset.rules if r.check.kind in guidelines_reader.BASELINE_KINDS]
    chosen: list[guidelines_reader.Guideline] = []
    for name in dict.fromkeys(names):
        rule = ruleset.by_id(name)
        if rule is None:
            raise BaselineRefused(
                f"the ruleset has no guideline {name!r}",
                "name a pattern or boundary rule of the ruleset in --rule",
            )
        if rule.check.kind not in guidelines_reader.BASELINE_KINDS:
            reason = (
                "a cochange finding has no fingerprint, so no baseline can hold it (DN-4)"
                if rule.check.kind == "cochange"
                else f"a {rule.check.kind} rule has no finding of its own to record"
            )
            raise BaselineRefused(
                f"{name} is a {rule.check.kind} rule, and only pattern and boundary "
                f"rules are baselined: {reason}",
                "name a pattern or boundary rule in --rule",
            )
        chosen.append(rule)
    return chosen


def _parsed_or_refuse(
    text: str, rel: str
) -> tuple[guidelines_reader.BaselineEntry, ...]:
    try:
        return guidelines_reader.parse_baseline(text)
    except guidelines_reader.BaselineError as exc:
        raise BaselineRefused(
            f"the guidelines baseline {rel} does not parse ({len(exc.problems)} "
            f"problem(s)): {'; '.join(exc.problems)}; nothing was written",
            f"fix {rel} by hand, or record every rule again: "
            "invariant guidelines baseline --record",
        ) from exc


def _gone(
    entry: guidelines_reader.BaselineEntry, rule: guidelines_reader.Guideline | None
) -> BaselineChange | None:
    """The drop of an entry whose rule can no longer hold it, else ``None``."""
    if rule is None:
        reason = "its rule is gone from the ruleset"
    elif rule.check.kind not in guidelines_reader.BASELINE_KINDS:
        reason = f"its rule is now a {rule.check.kind} rule, which is never baselined"
    else:
        return None
    return BaselineChange(entry.rule, entry.path, entry.fingerprint, entry.count, 0, reason)


def _refuse_unanswerable(found: Iterable[findings_reader.Finding], what: str) -> None:
    unanswerable = [f for f in found if f.status == "unanswerable"]
    if unanswerable:
        raise BaselineRefused(
            f"{len(unanswerable)} unanswerable finding(s), so {what}: "
            + "; ".join(f.describe() for f in unanswerable),
            "make each file answerable (declare the ecosystem whose adapter reads it, "
            "or fix what stops it being read), then run it again",
        )


def _record_baseline(
    ctx: RuleContext,
    tree: Path,
    ruleset: guidelines_reader.Ruleset,
    names: tuple[str, ...],
    existing: str | None,
    rel: str,
) -> tuple[list[guidelines_reader.BaselineEntry], list[BaselineChange]]:
    """Every tracked file scanned under the chosen rules; the entries to write.

    With ``names``, every other rule's entries are kept as they are, so the
    existing file must read in full; an entry whose rule can no longer hold
    it is dropped and named, because the file would not load with it.
    Without, the existing file is replaced whatever it holds.
    """
    selected = _baselined_rules(ruleset, names)
    kept: list[guidelines_reader.BaselineEntry] = []
    changes: list[BaselineChange] = []
    if names and existing is not None:
        replaced = {rule.id for rule in selected}
        for entry in _parsed_or_refuse(existing, rel):
            if entry.rule in replaced:
                continue
            gone = _gone(entry, ruleset.by_id(entry.rule))
            if gone is not None:
                changes.append(gone)
            else:
                kept.append(entry)
    tracked = git.tracked_files(tree)
    if tracked is None:
        raise BaselineRefused(
            "git could not list the tracked files, so nothing can be recorded",
            "run it inside the project's repository",
        )
    top = git.toplevel(tree) or tree
    files = sorted({
        spelled
        for name in tracked
        if (spelled := _tree_relative(ctx, tree, top / name)) is not None
        and (tree / spelled).is_file()
    })
    adapters = _ecosystem_adapters(ctx)
    evaluation = findings_reader.evaluate(selected, files, **_finding_reads(tree, adapters))
    _refuse_unanswerable(evaluation.findings, "a record of part of the tree is no record")
    counts = findings_reader.counted(evaluation.findings)
    kept.extend(
        guidelines_reader.BaselineEntry(rule, path, stamp, count)
        for (rule, path, stamp), count in counts.items()
    )
    return kept, changes


def _prune_baseline(
    ctx: RuleContext,
    tree: Path,
    ruleset: guidelines_reader.Ruleset,
    existing: str | None,
    rel: str,
) -> tuple[list[guidelines_reader.BaselineEntry], list[BaselineChange]]:
    """The baseline shrunk to what the touched files still hold. Never grown.

    An entry whose file the change renamed moves to the new path first
    (DN-7), and is then lowered or dropped there like any other.
    """
    if existing is None:
        raise BaselineRefused(
            f"the configured guidelines baseline {rel} does not exist; --prune only "
            f"shrinks a baseline that exists (DN-1)",
            "invariant guidelines baseline --record",
        )
    entries = _parsed_or_refuse(existing, rel)
    changed = _changed_set(ctx)
    if not changed.answerable:
        raise BaselineRefused(
            "the changed set could not be established, so prune cannot tell which "
            "files the change touched",
            "declare the session's files or record its base, then prune again",
        )
    files, gone = _changed_in_tree(ctx, changed, tree)
    touched = set(files) | set(gone)
    entries, changes = _follow_renames(ctx, tree, changed.base, entries)
    rules = [r for r in ruleset.rules if r.check.kind in guidelines_reader.BASELINE_KINDS]
    evaluation = findings_reader.evaluate(
        rules, files, **_finding_reads(tree, _ecosystem_adapters(ctx))
    )
    recorded = {(e.rule, e.path) for e in entries if e.path in touched}
    _refuse_unanswerable(
        [f for f in evaluation.findings if (f.rule, f.path) in recorded],
        "prune cannot tell what those files still hold",
    )
    counts = findings_reader.counted(evaluation.findings)
    kept: list[guidelines_reader.BaselineEntry] = []
    for entry in entries:
        rule = ruleset.by_id(entry.rule)
        drop = _gone(entry, rule)
        if drop is None and not (tree / entry.path).is_file():
            drop = BaselineChange(entry.rule, entry.path, entry.fingerprint, entry.count, 0,
                                  "its file was deleted")
        if drop is None and not any(
            findings_reader.matches(entry.path, glob) for glob in rule.applies_to
        ):
            drop = BaselineChange(entry.rule, entry.path, entry.fingerprint, entry.count, 0,
                                  "its rule no longer applies to the file")
        if drop is None and entry.path in touched:
            still = counts.get(entry.key, 0)
            if still == 0:
                drop = BaselineChange(entry.rule, entry.path, entry.fingerprint,
                                      entry.count, 0, "the change fixed every finding it held")
            elif still < entry.count:
                changes.append(BaselineChange(
                    entry.rule, entry.path, entry.fingerprint, entry.count, still,
                    "the change fixed some of the findings it held",
                ))
                entry = guidelines_reader.BaselineEntry(
                    entry.rule, entry.path, entry.fingerprint, still
                )
        if drop is not None:
            changes.append(drop)
        else:
            kept.append(entry)
    return kept, changes


def _spelled_renames(
    ctx: RuleContext, tree: Path, answer: Mapping[str, str]
) -> dict[str, str]:
    """``git.renamed_files``' answer in the spelling every declared path is
    compared in, as ``_changed_in_tree`` spells the changed paths."""
    return {
        old: new
        for raw_old, raw_new in answer.items()
        if (old := _tree_relative(ctx, tree, raw_old)) is not None
        and (new := _tree_relative(ctx, tree, raw_new)) is not None
    }


def _follow_renames(
    ctx: RuleContext,
    tree: Path,
    base: str,
    entries: Iterable[guidelines_reader.BaselineEntry],
) -> tuple[list[guidelines_reader.BaselineEntry], list[BaselineChange]]:
    """Each entry whose file the change renamed, moved to the new path (DN-7).

    Git is asked only when an entry's file is gone from the tree and a base
    is recorded; with no base there is nothing to measure a rename from, and
    a gone file is a deleted one, as before. An answer git cannot give
    refuses the prune rather than dropping a moved entry as deleted. A moved
    entry that meets one already recorded at the new path is merged with it,
    since one (rule, path, fingerprint) is one entry.
    """
    entries = list(entries)
    if not base or all((tree / e.path).is_file() for e in entries):
        return entries, []
    answer = git.renamed_files(tree, base)
    if answer is None:
        raise BaselineRefused(
            "git could not say which files this change renamed, so prune cannot tell a "
            "moved file from a deleted one",
            "check the session's base branch is reachable, then prune again",
        )
    renames = _spelled_renames(ctx, tree, answer)
    merged: dict[tuple[str, str, str], guidelines_reader.BaselineEntry] = {}
    changes: list[BaselineChange] = []
    for entry in entries:
        moved_to = renames.get(entry.path)
        if moved_to is not None and not (tree / entry.path).is_file():
            changes.append(BaselineChange(
                entry.rule, entry.path, entry.fingerprint, entry.count, entry.count,
                f"its file was renamed to {moved_to}", moved_to=moved_to,
            ))
            entry = guidelines_reader.BaselineEntry(
                entry.rule, moved_to, entry.fingerprint, entry.count
            )
        held = merged.get(entry.key)
        merged[entry.key] = entry if held is None else guidelines_reader.BaselineEntry(
            entry.rule, entry.path, entry.fingerprint, held.count + entry.count
        )
    return list(merged.values()), changes


@rule(
    "guidelines.baseline_only_shrinks",
    summary="The guidelines baseline only shrinks against the base branch's copy, "
    "and no stale entry is left in a touched file.",
    events=(),
)
def _guidelines_baseline_only_shrinks(ctx: RuleContext) -> Verdict:
    """ADR-012 D6 ("Only shrinks"), ruling 7, and the A3 rulings DN-5 to DN-7.

    The baseline in the change is compared with the base branch's committed
    copy, read through git at the merge base, at the path the BASE's own
    manifest names, so pointing ``guidelines.baseline`` at a new file is no
    amnesty. An added entry or a raised count fails, unless DN-6 admits it:
    the base configured no baseline, or the entry's rule is absent from the
    base's ruleset, or has a different ``applies_to``, ``check`` or
    ``exceptions`` there (``definition_of``, compared structurally). An entry
    follows a rename git detects (DN-7), so a move is accepted while rule,
    fingerprint and count do not grow. Deleting entries is shrinking.

    A stale entry fails it too (DN-5): the ratchet's arithmetic over the
    touched files, as ``guidelines.mechanical_clean`` reports it in its
    detail, with ``invariant guidelines baseline --prune`` as the fix. A file
    the change renamed away counts as touched, so an entry left at the old
    path is stale whatever ``diff.renames`` says.

    N/A with no ruleset or no baseline. A ruleset or baseline that does not
    load, a changed set that cannot be established, no base, a merge base, a
    rename listing or a base copy git cannot give, and a base copy that does
    not parse each FAIL it: none of them is an answer, and admitting
    everything would be.
    """
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        return _cannot_compare(failure)
    if ruleset is None:
        return Verdict.na("no guidelines ruleset configured")
    if not ctx.manifest.get("guidelines.baseline"):
        return Verdict.na("no baseline configured: every pattern or boundary finding is new")
    entries, broken = load_baseline(ctx, ruleset)
    if broken is not None:
        return _cannot_compare(broken)
    changed = _changed_set(ctx)
    if not changed.answerable:
        return _unestablished_changed_set(changed)
    tree = guidelines_tree(ctx)
    base = _base_point(tree, changed.base)
    if isinstance(base, Verdict):
        return base
    point, renames = base[0], _spelled_renames(ctx, tree, base[1])
    copy = _base_baseline(ctx, tree, point)
    if isinstance(copy, Verdict):
        return copy

    grown = findings_reader.growth(copy.entries, entries, renames)
    admitted: list[str] = []
    refused: list[findings_reader.Growth] = []
    if grown and copy.path is None:
        admitted = [g.describe() for g in grown]
    elif grown:
        base_rules = _base_rules(ctx, tree, point, copy.ruleset)
        if isinstance(base_rules, Verdict):
            return base_rules
        for item in grown:
            why = _admitted(base_rules.get(item.rule), ruleset.by_id(item.rule))
            if why is None:
                refused.append(item)
            else:
                admitted.append(f"{item.describe()}: {why}")

    files, gone = _changed_in_tree(ctx, changed, tree)
    rules = [r for r in ruleset.rules if r.check.kind in guidelines_reader.BASELINE_KINDS]
    evaluation = findings_reader.evaluate(
        rules, files, **_finding_reads(tree, _ecosystem_adapters(ctx))
    )
    stale = findings_reader.ratchet(
        evaluation.findings, entries, touched=set(files) | set(gone) | set(renames)
    ).stale

    detail = _shrink_detail(copy, admitted, renames)
    if refused or stale:
        parts, fixes = [], []
        if refused:
            parts.append(
                f"{len(refused)} baseline {_entries_noun(len(refused))} grew against the base "
                f"branch's copy: " + "; ".join(g.describe() for g in refused)
            )
            fixes.append(
                "the baseline only shrinks: remove the added entries and lower the raised "
                "counts, and fix the findings they would hold; a rule the change introduces "
                "or redefines records its entries in the same change (DN-6)"
            )
        if stale:
            parts.append(
                f"{len(stale)} stale baseline {_entries_noun(len(stale))}, which no finding "
                f"holds any more; prune each in this change: "
                + "; ".join(s.describe() for s in stale)
            )
            fixes.append(PRUNE_FIX)
        return Verdict.fail(". ".join(parts), detail=detail, fixes=tuple(fixes))
    return Verdict.clean(
        f"the baseline only shrank: {len(entries)} {_entries_noun(len(entries))} against "
        f"{len(copy.entries)} on the base branch, none stale",
        detail=detail,
    )


#: The fix every stale entry names (DN-5).
PRUNE_FIX = "invariant guidelines baseline --prune"


@dataclass(frozen=True)
class _BaseCopy:
    """The base branch's baseline as the merge base holds it.

    ``path`` is ``None`` when the base configured no baseline, and every
    entry of the change is then admitted (DN-6).
    """

    path: str | None
    entries: tuple[guidelines_reader.BaselineEntry, ...] = ()
    #: The base's own ``guidelines.ruleset``, as its manifest spells it.
    ruleset: str | None = None


def _entries_noun(count: int) -> str:
    return "entry" if count == 1 else "entries"


def _cannot_compare(failure: Verdict) -> Verdict:
    return Verdict.fail(
        f"the baseline cannot be compared: {failure.message}",
        detail=failure.detail,
        fixes=failure.fixes,
    )


def _base_fail(message: str, detail: str, *fixes: str) -> Verdict:
    return Verdict.fail(
        message,
        detail=detail + " The rule will not answer without the base branch's copy: "
        "admitting every entry would let the baseline grow unseen.",
        fixes=fixes or ("check the session's base branch is reachable",),
    )


def _base_point(tree: Path, base: str) -> tuple[str, dict[str, str]] | Verdict:
    """The merge base the base copy is read at, and the change's renames."""
    if not base:
        return _base_fail(
            "no base recorded, so the base branch's baseline cannot be read",
            "The baseline is compared with the base branch's committed copy, read at "
            "the merge base.",
            "start the session with its base branch named: "
            "invariant session start <name> --base <branch>",
        )
    point = git.merge_base(tree, base)
    if point is None:
        return _base_fail(
            f"git could not find the merge base of {base!r} and this change",
            f"The base branch's copy is read where the branch left {base!r}.",
        )
    renames = git.renamed_files(tree, base)
    if renames is None:
        return _base_fail(
            "git could not list this change's renames, so a moved entry cannot be "
            "told from an added one",
            "An entry follows a rename git detects from the merge base (DN-7).",
        )
    return point, renames


def _at_base(tree: Path, point: str, rel: str, what: str) -> bytes | None | Verdict:
    """``rel``'s bytes at the merge base, ``None`` when it was no file there,
    or the failure when git could not say."""
    exists = git.exists_at(tree, point, rel)
    if exists is None:
        return _base_fail(
            f"git could not read the base branch's {what} {rel} at the merge base", ""
        )
    if not exists:
        return None
    data = git.blob_at(tree, point, rel)
    if data is None:
        return _base_fail(
            f"git could not read the base branch's {what} {rel} at the merge base", ""
        )
    return data


def _base_baseline(ctx: RuleContext, tree: Path, point: str) -> _BaseCopy | Verdict:
    """The base branch's baseline, at the path the base's own manifest names.

    The base's manifest is read for its two ``guidelines`` paths and nothing
    else. No manifest there, or one with no ``guidelines.baseline``, is a base
    with no baseline configured. Anything else short of a baseline that
    parses in full is a failure, never a smaller copy: a missing file, one
    git cannot read, one that is not UTF-8 and one with a line that does not
    parse. The base copy is not checked against any ruleset; the entries are
    only counted.
    """
    manifest_rel = MANIFEST_RELPATH.as_posix()
    data = _at_base(tree, point, manifest_rel, "manifest")
    if isinstance(data, Verdict):
        return data
    if data is None:
        return _BaseCopy(None)
    try:
        raw = yaml.safe_load(data.decode("utf-8")) or {}
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        return _base_manifest_fail(f"does not parse ({exc})")
    if not isinstance(raw, Mapping):
        return _base_manifest_fail("is not a mapping")
    section = raw.get("guidelines") or {}
    if not isinstance(section, Mapping):
        return _base_manifest_fail("gives guidelines as something other than a mapping")
    paths: dict[str, str | None] = {}
    for key in ("baseline", "ruleset"):
        value = section.get(key)
        if value is not None and not (isinstance(value, str) and value.strip()):
            return _base_manifest_fail(f"gives guidelines.{key} {value!r}, which is not a path")
        paths[key] = value
    if paths["baseline"] is None:
        return _BaseCopy(None)
    rel = guidelines_relpath(ctx, paths["baseline"])
    if rel is None:
        return _base_manifest_fail(
            f"names guidelines.baseline {paths['baseline']!r}, which is nothing inside "
            f"the project"
        )
    data = _at_base(tree, point, rel, "baseline")
    if isinstance(data, Verdict):
        return data
    if data is None:
        return _base_fail(
            f"the base branch's baseline {rel} does not exist at the merge base",
            "The base branch's manifest names a baseline its tree does not hold, and a "
            "configured file that does not exist is an error, never an empty baseline.",
            f"restore {rel} on the base branch, or pass this landing deliberately, "
            f"recorded: {_FORCED_LANDING}",
        )
    try:
        entries = guidelines_reader.parse_baseline(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        return _base_fail(f"the base branch's baseline {rel} is not UTF-8 text", f"{exc}.")
    except guidelines_reader.BaselineError as exc:
        return _base_fail(
            f"the base branch's baseline {rel} does not parse "
            f"({len(exc.problems)} problem(s))",
            "; ".join(exc.problems) + ".",
            f"repair {rel} on the base branch, or pass this landing deliberately, "
            f"recorded: {_FORCED_LANDING}",
        )
    return _BaseCopy(rel, entries, paths["ruleset"])


def _base_manifest_fail(why: str) -> Verdict:
    return _base_fail(
        f"the base branch's manifest {MANIFEST_RELPATH.as_posix()} {why}",
        "The base's own manifest says where its baseline is.",
        "repair the manifest on the base branch, or pass this landing deliberately, "
        f"recorded: {_FORCED_LANDING}",
    )


#: The route past ``guidelines.baseline_only_shrinks``. It gates a pipeline
#: transition and fires at no hook, so no ``invariant override`` grant is ever
#: spent on it; and its failures concern the baseline, not a guideline, so no
#: grant naming a guideline reaches it either (A3.7).
_FORCED_LANDING = "invariant pipeline advance <state> --force --reason '<why>'"


def _base_rules(
    ctx: RuleContext, tree: Path, point: str, configured: str | None
) -> dict[str, guidelines_reader.Guideline] | Verdict:
    """The base branch's rules by id, read from its ruleset at the merge base.

    No ruleset configured there is no rule there. The ruleset is parsed as the
    base held it, its registries read at the merge base too, for the schema
    question alone: the engines a base's adapters offered do not change what
    a rule is.
    """
    if configured is None:
        return {}
    rel = guidelines_relpath(ctx, configured)
    if rel is None:
        return _base_fail(
            f"the base branch's ruleset {configured!r} names nothing inside the project", ""
        )
    data = _at_base(tree, point, rel, "ruleset")
    if isinstance(data, Verdict):
        return data
    if data is None:
        return _base_fail(f"the base branch's ruleset {rel} does not exist at the merge base", "")
    try:
        parsed = guidelines_reader.parse(
            data.decode("utf-8"),
            registered=[spec.id for spec in all_rules()],
            read=lambda name: git.blob_at(tree, point, name),
        )
    except UnicodeDecodeError as exc:
        return _base_fail(f"the base branch's ruleset {rel} is not UTF-8 text", f"{exc}.")
    except guidelines_reader.RulesetError as exc:
        return _base_fail(
            f"the base branch's ruleset {rel} does not parse, so no added entry can be "
            f"admitted ({len(exc.problems)} problem(s))",
            "; ".join(exc.problems) + ".",
        )
    return {rule.id: rule for rule in parsed.rules}


def _admitted(
    before: guidelines_reader.Guideline | None, now: guidelines_reader.Guideline | None
) -> str | None:
    """Why DN-6 admits growth of this rule's entries, or ``None`` when it does not."""
    if before is None:
        return "its rule is absent from the base branch's ruleset"
    if now is not None and guidelines_reader.definition_of(
        before
    ) != guidelines_reader.definition_of(now):
        return "its rule has a different applies_to, check or exceptions on the base branch"
    return None


def _shrink_detail(copy: _BaseCopy, admitted: list[str], renames: Mapping[str, str]) -> str:
    parts = [
        f"compared with the base branch's {copy.path}, read at the merge base"
        if copy.path is not None
        else "the base branch has no baseline configured, so every entry is admitted (DN-6)"
    ]
    if admitted:
        parts.append(f"{len(admitted)} admitted (DN-6): " + "; ".join(admitted))
    if renames:
        parts.append(
            "renames followed (DN-7): "
            + ", ".join(f"{old} -> {new}" for old, new in sorted(renames.items()))
        )
    return ". ".join(parts)


# -- verdicts (ADR-012 D5, A4 increment 1) ---------------------------------------


#
# The verdict plan: a submission parsed and every D5 predicate checked, with
# nothing recorded. `domains/guidelines` records what the plan accepted, and
# the `guidelines.verdicts_complete` gate (A4.3) asks the same plan, so a
# verdict the command accepts is one the gate reads as valid. It lives here,
# beside `plan_render` and `plan_baseline`, because a rule will ask it and
# `core/` may not import a domain. `core/verdicts` checks; this does every
# read: the ruleset and its pin, the machine's roles for the state, the
# changed set, the cited files' bytes, the lines git says the change added,
# the pipeline's test runs, the privacy scan, and what the mechanical N/A
# conditions read (the masks and the change's history, A4.2).


class VerdictRefused(InvariantError):
    """A verdict submission refused before anything was recorded (exit 1).

    Each line names the entry, the predicate that failed and its fix (DN-11).
    ``refusals`` carries the same lines with those names apart, which the
    CLI prints under ``--json`` (A4 increment 7); ``problems`` is the lines.
    """

    exit_code = 1

    def __init__(self, refusals: Iterable[verdicts_reader.Refusal]) -> None:
        self.refusals = tuple(refusals)
        self.problems = tuple(r.line for r in self.refusals)
        super().__init__(
            "the verdict submission is refused; nothing was recorded:\n  "
            + "\n  ".join(self.problems)
        )

    @classmethod
    def precondition(cls, line: str) -> VerdictRefused:
        """A refusal of the whole: nothing to check a verdict against (no
        session, no pipeline, a pipeline that moved)."""
        return cls([verdicts_reader.Refusal(verdicts_reader.PRECONDITION, line)])

    @classmethod
    def unreadable(cls, line: str) -> VerdictRefused:
        """A refusal of the whole: the submitted file cannot be read, or is
        not JSON."""
        return cls([verdicts_reader.Refusal(verdicts_reader.SUBMISSION, line)])


@dataclass(frozen=True)
class VerdictPlan:
    """A submission checked: each entry's answer, or why it cannot be had."""

    checked: tuple[verdicts_reader.Checked, ...] = ()
    #: Problems of the whole: no pipeline, no ruleset, a shape the
    #: submission as a whole does not have. Each entry's are on ``checked``.
    whole: tuple[verdicts_reader.Refusal, ...] = ()
    #: The digest the accepted verdicts record, and the state they were
    #: submitted in; ``None`` when the plan could not get that far.
    ruleset: str | None = None
    state: str | None = None

    @property
    def refusals(self) -> tuple[verdicts_reader.Refusal, ...]:
        """Every problem named, the whole's first, then each entry's in order."""
        return self.whole + tuple(
            refusal for checked in self.checked for refusal in checked.refusals()
        )

    @property
    def problems(self) -> tuple[str, ...]:
        """Each refusal's line, in the same order."""
        return tuple(r.line for r in self.refusals)

    @property
    def ok(self) -> bool:
        """Accepted only whole: one invalid entry refuses every entry (DN-11)."""
        return bool(self.checked) and not self.problems

    @property
    def records(self) -> tuple[verdicts_reader.VerdictRecord, ...]:
        if not self.ok:
            return ()
        return tuple(c.record for c in self.checked if c.record is not None)

    def refusal(self) -> VerdictRefused:
        return VerdictRefused(self.refusals)


#: The predicate a refusal of the whole submission is named by.
_PRECONDITION = verdicts_reader.PRECONDITION


def _whole(line: str) -> verdicts_reader.Refusal:
    return verdicts_reader.Refusal(_PRECONDITION, f"{_PRECONDITION}: {line}")


def plan_verdicts(ctx: RuleContext, submission: Any) -> VerdictPlan:
    """Check a verdict submission against D5, recording nothing (A4.1, DN-1).

    ``submission`` is the decoded JSON: a list of D5 objects. The ruleset is
    read from the tree in hand, as every guidelines gate reads it, and the
    pipeline is ``ctx.pipeline``. Every refusal is named, per entry; a plan
    with any problem accepts nothing (DN-11).
    """
    pipeline = ctx.pipeline
    if not pipeline:
        return VerdictPlan(whole=(_whole(
            "no pipeline is running for this session, so a verdict "
            "has no evidence to enter; fix: invariant pipeline start --task "
            "\"<what you are doing>\""
        ),))
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        detail = f" ({failure.detail})" if failure.detail else ""
        fixes = "; ".join(failure.fixes) or "fix the ruleset"
        return VerdictPlan(whole=(_whole(
            f"no guideline can receive a verdict: {failure.message}{detail}; fix: {fixes}"
        ),))
    if ruleset is None:
        return VerdictPlan(whole=(_whole(
            "no guidelines ruleset is configured, so no guideline "
            "can receive a verdict; fix: set guidelines.ruleset"
        ),))
    parsed = verdicts_reader.parse_submission(submission)
    whole = tuple(
        verdicts_reader.Refusal(p.predicate, f"{verdicts_reader.SUBMISSION}: {p.describe()}")
        for p in parsed.problems
    )
    facts = _verdict_facts(ctx, ruleset, pipeline)
    checked = verdicts_reader.check_all(parsed, ruleset, facts)
    return VerdictPlan(
        checked=checked, whole=whole,
        ruleset=facts.pin or facts.tree_digest, state=facts.state,
    )


def _verdict_facts(
    ctx: RuleContext,
    ruleset: guidelines_reader.Ruleset,
    pipeline: Mapping[str, Any],
    changed: _ChangedSet | None = None,
) -> verdicts_reader.Facts:
    """Every read the checks need, from the tree in hand (DN-1).

    The submission and ``guidelines.verdicts_complete`` both ask this, so a
    verdict is checked at the gate against exactly what submission read.
    ``changed`` is the changed set when the caller has read it already.
    """
    pinned = pipeline.get("ruleset")
    pin = pinned.get("digest") if isinstance(pinned, Mapping) else None
    state = str(pipeline.get("state") or "")
    roles: tuple[str, ...] = ()
    roles_problem = None
    try:
        machine = load_pipeline_machine(ctx.manifest, pipeline.get("methodology") or None)
        roles = tuple(machine.state(state).roles)
    except InvariantError as exc:
        roles_problem = str(exc)

    tree = guidelines_tree(ctx)
    if changed is None:
        changed = _changed_set(ctx)
    touched: tuple[str, ...] | None = None
    if changed.answerable:
        # A changed path gone from the tree is no file to meet applies_to
        # through, nor one to cite (DN-3, DN-4).
        files, _gone = _changed_in_tree(ctx, changed, tree)
        touched = tuple(files)
    base = changed.base
    asked: dict[str, Any] = {}

    def added() -> Mapping[str, Any] | None:
        if "lines" not in asked:
            asked["lines"] = _added_lines(ctx, tree, base)
        return asked["lines"]

    # The mechanical N/A conditions read the tree, the masks and the change's
    # history exactly as guidelines.mechanical_clean does (A4.2).
    adapters = _ecosystem_adapters(ctx)
    reads = _finding_reads(tree, adapters)
    history = _change_history(ctx, tree, base, adapters)

    def na(rule, condition, files):
        return findings_reader.na_answer(
            rule, condition, files, read=reads["read"], mask=reads["mask"], history=history
        )

    runs = (ctx.evidence("test_runs") or ()) if ctx.pipeline else ()
    scanner = PrivacyScanner.from_manifest(ctx.manifest)
    return verdicts_reader.Facts(
        tree_digest=ruleset.digest,
        pin=pin,
        state=state,
        roles=roles,
        touched=touched,
        read=lambda rel: _read_bytes(tree / rel),
        added=added,
        test_runs=tuple(runs) if isinstance(runs, list) else (),
        note_max_chars=int(ctx.manifest.get("guidelines.verdicts.note_max_chars")),
        scan=lambda note: _note_findings(scanner, note),
        roles_problem=roles_problem,
        na=na,
    )


def _added_lines(ctx: RuleContext, tree: Path, base: str) -> dict[str, Any] | None:
    """The lines the change added, per file, spelled as the touched set is.

    ``None`` with no base recorded, or when git cannot answer from the merge
    base: a ``scope: diff`` verdict is then unanswerable, never accepted.
    """
    if not base or git.merge_base(tree, base) is None:
        return None
    answer = git.changed_lines(tree, base)
    if answer is None:
        return None
    spelled: dict[str, Any] = {}
    for path, lines in answer.items():
        rel = _tree_relative(ctx, tree, path)
        if rel is not None:
            spelled[rel] = lines
    return spelled


def _note_findings(scanner: PrivacyScanner, note: str) -> list[str]:
    """The privacy findings in a note, scanned whatever ``privacy.scope`` says.

    The note is about to be written into evidence, so it is scanned as render
    scans what it writes. The exemption marker is a finding here: a note
    cannot exempt itself, since nothing it says is read by any gate.
    """
    found = [
        f"line {f.line} {f.pattern} {f.redacted}" for f in scanner.scan_text(note)
    ]
    if EXEMPTION_MARKER in note:
        found.append(f"the privacy exemption marker {EXEMPTION_MARKER!r}")
    return found


@rule(
    "guidelines.verdicts_complete",
    summary="Every judgement guideline the change owes a verdict has one that still holds.",
    events=(),
)
def _guidelines_verdicts_complete(ctx: RuleContext) -> Verdict:
    """ADR-012 D5 ("The gate", "Staleness") and the A4 rulings (A4.3).

    Which judgement guidelines the change owes a verdict is
    ``core/verdicts.owed``: phases naming review, landing or always (DN-2),
    met by a changed file present in the tree outside the path exceptions
    (DN-3). A mechanical N/A condition the engine finds true answers a rule
    itself and no verdict is owed (D5 item 5); a pass or fail recorded on it
    is set aside, since the gate is over applicable rules, and named. Every
    other owed rule is answered by its latest recorded verdict, checked again
    through the reads ``plan_verdicts`` makes and the checks submission ran,
    the review-role predicate aside: a cited file whose blob changed, a
    ruleset that is not the pin (DN-6), a test whose latest run is not green
    (DN-8) or a mechanical N/A no longer true makes it stale, and stale is
    missing. It fails on a missing or stale verdict, and on a current
    ``fail`` at blocker severity; a ``fail`` at warn is reported inside a
    pass. It never reads which agent submitted a verdict (D1 item 2, DN-12).

    A live grant naming a judgement guideline (A4.5, ruling 4, DN-9) passes
    every failure of that guideline in this evaluation, a warn one's
    included: no verdict, a stale one, a current fail. The verdict names it
    with the grant's reason, counts it as passed by override, and carries it
    in ``Verdict.overridden`` when it passed a failing rule, which is what
    the CLI spends at the advance it lets through. A grant passing nothing
    (a current pass, the engine's N/A, a rule not owed) is named as unused.
    Every other guideline stays in force. The grant is read here, never
    spent.

    The reads are :func:`read_standings`', and the verdict is
    :func:`standings_verdict` over them: ``guidelines verdicts show`` prints
    that same read (A4 increment 4), so the command and the gate cannot
    disagree.
    """
    return standings_verdict(read_standings(ctx))


class StandingsRefused(InvariantError):
    """``guidelines verdicts show`` cannot say how the verdicts stand (exit 1).

    Raised where the gate fails without standings to show (a ruleset that
    does not load, a changed set that cannot be established), carrying the
    gate's own message, detail and fixes, and with no session to read.
    """

    exit_code = 1

    def __init__(self, message: str, detail: str = "", fixes: Iterable[str] = ()) -> None:
        self.message = message
        self.detail = detail
        self.fixes = tuple(fixes)
        lines = [f"no verdict standing can be shown: {message}"]
        if detail:
            lines.append(f"  {detail}")
        lines.extend(f"  try: {fix}" for fix in self.fixes)
        super().__init__("\n".join(lines))


@dataclass(frozen=True)
class StandingsRead:
    """What ``guidelines.verdicts_complete`` sees, read once (A4 increment 4).

    The gate's verdict is :func:`standings_verdict` over this read, and
    ``guidelines verdicts show`` prints the same read, so the two cannot
    disagree about how a verdict stands.
    """

    #: ``standings``; ``no-ruleset`` or ``no-pipeline`` (the gate is N/A);
    #: or ``unanswerable`` (the gate fails, and ``failure`` says why).
    status: str
    #: Each owed judgement rule's standing, in ruleset order.
    standings: tuple[verdicts_reader.Standing, ...] = ()
    #: Recorded verdicts on rules the change does not owe one.
    unowed: tuple[verdicts_reader.Unowed, ...] = ()
    #: The digest verdicts are judged against: the pin, or the tree's.
    ruleset: str | None = None
    state: str | None = None
    failure: Verdict | None = None
    #: The live grants naming a judgement guideline, each with its reason
    #: (A4.5), and those of them that pass nothing in this read.
    grants: Mapping[str, str] = field(default_factory=dict)
    unused: tuple[verdicts_reader.Unused, ...] = ()


def read_standings(ctx: RuleContext) -> StandingsRead:
    """Every read the gate makes, and ``core/verdicts``' answers over them.

    The ruleset through ``load_ruleset``, the changed set through
    ``_changed_set``, every other fact through ``_verdict_facts`` (the reads
    ``plan_verdicts`` makes), ``evidence["verdicts"]``, and the live grants
    naming a judgement guideline (A4.5). Nothing recorded, nothing spent.
    """
    ruleset, failure = load_ruleset(ctx)
    if failure is not None:
        return StandingsRead(status="unanswerable", failure=Verdict.fail(
            f"no verdict can be judged: {failure.message}",
            detail=failure.detail,
            fixes=failure.fixes,
        ))
    if ruleset is None:
        return StandingsRead(status="no-ruleset")
    pipeline = ctx.pipeline
    if not pipeline:
        return StandingsRead(status="no-pipeline", ruleset=ruleset.digest)
    changed = _changed_set(ctx)
    if not changed.answerable:
        return StandingsRead(
            status="unanswerable", failure=_unestablished_changed_set(changed),
            ruleset=ruleset.digest, state=str(pipeline.get("state") or ""),
        )
    facts = _verdict_facts(ctx, ruleset, pipeline, changed)
    recorded = ctx.evidence("verdicts") or ()
    records = tuple(recorded) if isinstance(recorded, list) else ()
    grants = guideline_grants(ctx, ruleset, judgement=True)
    found = verdicts_reader.standings(ruleset, records, facts, grants)
    return StandingsRead(
        status="standings",
        standings=found,
        unowed=verdicts_reader.unowed(ruleset, records, facts),
        ruleset=facts.pin or facts.tree_digest,
        state=facts.state,
        grants=grants,
        unused=verdicts_reader.unused_grants(found, grants),
    )


def standings_verdict(read: StandingsRead) -> Verdict:
    """``guidelines.verdicts_complete``'s verdict over one read of the standings."""
    if read.failure is not None:
        return read.failure
    if read.status == "no-ruleset":
        return Verdict.na("no guidelines ruleset configured")
    if read.status == "no-pipeline":
        return Verdict.na("no pipeline is running, so no verdict is owed")
    found = read.standings
    digest = read.ruleset
    unused = "; ".join(u.describe() for u in read.unused)
    # What a landing keeps of this verdict (A4.6, DN-7): the standings it was
    # decided on, the digest, and every grant read, whatever the outcome.
    kept = {
        "standings": tuple(s.record() for s in found),
        "ruleset": digest,
        "grants": tuple(read.grants.items()),
    }
    if not found:
        return Verdict(
            ok=True,
            message=f"no judgement guideline is owed a verdict by this change, "
            f"ruleset {digest}",
            detail=unused,
            **kept,
        )
    blocking = [s for s in found if s.blocks]
    passed = [s for s in found if s.overridden]
    parts = [_passed_detail(s) for s in passed]
    parts += [
        s.describe() for s in found if not s.blocks and not s.overridden
        and (s.warns or s.standing == verdicts_reader.ENGINE)
    ]
    if unused:
        parts.append(unused)
    detail = "; ".join(parts)
    tally = f"{len(passed)} passed by override" if passed else ""
    # Only a grant that passed a failing rule decided anything, so only it is
    # one the advance spends: a warn fail never blocked (A3.7's property).
    overridden = tuple((s.rule, s.unpassed()) for s in passed if s.failing)
    if blocking:
        return Verdict(
            ok=False,
            message=f"{len(blocking)} of {len(found)} owed judgement guideline(s) lack "
            f"a verdict that holds, or fail at blocker" + (f" ({tally})" if tally else "")
            + ": "
            + "; ".join(s.describe() for s in blocking),
            detail=detail,
            fixes=_verdict_fixes(blocking),
            overridden=overridden,
            **kept,
        )
    return Verdict(
        ok=True,
        message=f"every one of {len(found)} judgement guideline(s) owed a verdict has one "
        f"that holds, ruleset {digest}" + (f"; {tally}" if tally else ""),
        detail=detail,
        overridden=overridden,
        **kept,
    )


def _passed_detail(s: verdicts_reader.Standing) -> str:
    """A rule a grant passes, named with the grant, and whether it decided."""
    told = s.describe()
    if s.failing:
        return told + "; the pipeline advance this lets through spends the grant"
    return told + "; a fail at warn never blocks, so nothing spends the grant"


def _verdict_fixes(blocking: Iterable[verdicts_reader.Standing]) -> tuple[str, ...]:
    """One fix per blocking rule: a fresh verdict, or the code a fail cites;
    then, per rule, the recorded grant that passes it for one advance."""
    fixes: list[str] = []
    rules: list[str] = []
    for s in blocking:
        rules.append(s.rule)
        if s.standing == verdicts_reader.CURRENT:
            fixes.append(
                f"fix what the fail on {s.rule} cites, then answer it again: "
                f"invariant guidelines verdict {s.rule} --status pass"
            )
        else:
            fixes.append(
                f"invariant guidelines verdict {s.rule} --status pass  # or fail, "
                f"or na; with the evidence the rule declares: --cite <path> <first> "
                f"<last>, --test <selector>, or --na-condition <id>"
            )
    for rule_id in rules:
        fixes.append(
            f"or pass {rule_id}'s verdict for one advance, recorded: "
            f"invariant override {rule_id} --reason '<why>'"
        )
    return tuple(fixes)


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
                f"invariant pipeline test --phase {phase}  # records the failing run",
            ),
        )
    failing = [r for r in red if not r.get("passed", True)]
    if not failing:
        detail = (
            "A test that passes before the change cannot be evidence for it. "
            "Either it does not reach the defect, or the defect is not there."
        )
        # A run that exited clean with expected failures on it carries the
        # recorder's reason for not reading them as red (BUG_066). Without it
        # this verdict tells a reproducer its failing test passed.
        why = [
            str(r.get("note"))
            for r in red
            if r.get("expected_failures") and r.get("note")
        ]
        if why:
            detail += " The recorder's reading: " + "; ".join(why)
        return Verdict.fail(
            f"the run recorded in the {phase} phase passed",
            detail=detail,
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
    # On a sweep cadence (ADR-010 D1) the logic below is unchanged and only
    # the claim narrows: a red scoped run is a real red, not an under-reading,
    # so it still fails here. What moves to the sweep is "the WHOLE suite is
    # green", and a change touching a held path still owes that claim.
    scheduled = _scheduled(ctx, "test.all_pass")
    runs = ctx.evidence("test_runs", []) or []
    if not runs:
        return Verdict.fail(
            "no test run recorded",
            detail="Running the suite outside the pipeline leaves no record, "
            "and this gate can only answer from the record."
            + (
                " The whole suite is on the sweep cadence, so record the "
                "suites this change affects."
                if scheduled
                else ""
            ),
            fixes=(
                ("invariant pipeline test --selector <test>",)
                if scheduled
                else ("invariant pipeline test",)
            ),
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
    if scheduled:
        return _scheduled_suite_verdict(ctx, last)
    return Verdict.clean("suite is green and the pass is trustworthy")


def _scheduled_suite_verdict(ctx: RuleContext, last: Mapping[str, Any]) -> Verdict:
    """A green last run, judged as a scheduled `test.all_pass` judges it.

    A full run says what it always said. A scoped one passes, naming the
    cadence that now holds the whole suite — unless the change touches a
    `held_paths` glob, which owes a whole-suite run before merge (ADR-010 D8).
    """
    full = _run_scope(last) == "full"
    held, unanswerable = _held_paths_touched(ctx)
    if unanswerable is not None:
        return unanswerable
    if held and not full:
        return Verdict.fail(
            f"{len(held)} touched path(s) under {_HELD_PATHS_KEY} owe a "
            "whole-suite run, and the last run was not one",
            detail=", ".join(held[:6])
            + ". The project holds these paths back from the sweep cadence: a "
            "change touching one is merged only on a full run.",
            fixes=("invariant pipeline test  # the whole suite, which held_paths owes",),
        )
    if full:
        return Verdict.clean("suite is green and the pass is trustworthy")
    return Verdict.clean(
        "the affected suites are green; the whole suite is on the sweep cadence "
        f"({_cadence_phrase(ctx)})"
    )


@rule(
    "coverage.meets_floor",
    summary="Touched files meet the configured coverage floor.",
    events=(),
)
def _coverage_meets_floor(ctx: RuleContext) -> Verdict:
    # The floor's verdict is reached for the HELD files alone; advisory files
    # are split off on the way in and reported on the way out, so not one
    # branch of the floor below needed to learn that they exist. Every return
    # in it — the no-row block, the trust checks, the ratchet — therefore
    # applies to held files exactly as it did before the key existed.
    advisory: list[tuple[str, str]] = []
    held = _held_files_floor(ctx, advisory)
    return _with_advisory_report(ctx, held, advisory)


def _held_files_floor(ctx: RuleContext, advisory: list[tuple[str, str]]) -> Verdict:
    """The floor over every file not under `coverage_advisory_paths`.

    ``advisory`` is filled with ``(path, pattern)`` for each file split off,
    for :func:`_with_advisory_report` to name. When the split leaves nothing
    held, this returns a skip, which that function turns into the pass the
    exception grants — saying so.
    """
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
        source_changed, declared_non_source = _excluded_as_declared_non_source(
            ctx, source_changed
        )
        excluded += declared_non_source

        # A declared adapter whose coverage is not applicable (ADR-015 D1)
        # answers for the files it claims, its documents included, so a
        # change made only of them is N/A naming the adapter and its reason
        # rather than "no source file was touched". Stacked with an
        # ecosystem that has coverage, the rest is held as before.
        not_applicable = _coverage_not_applicable(ctx, changed, roots)
        if not_applicable:
            claimed = {rel for rel, _adapter in not_applicable}
            excluded += [
                f"{rel} (coverage not applicable: {adapter.id})"
                for rel, adapter in not_applicable
                if rel in source_changed
            ]
            source_changed -= claimed
            if not source_changed:
                return _not_applicable_verdict(not_applicable)

        # A POLICY about where the floor binds, asked after the claims about
        # what a path IS (above) and before any question about what was
        # measured, because an advisory file is owed no measurement at all.
        # An advisory-only change therefore never reaches the recording guard.
        source_changed, split_off = _split_advisory(ctx, source_changed)
        advisory.extend(split_off)
        if split_off and not source_changed:
            return Verdict.na("nothing held to the floor was touched")
        # On a sweep cadence a change with nothing held owes no coverage run
        # at all, source or not: the per-change floor demands nothing the
        # sweep does not hold (ADR-010 D1). The one exception is a held-path
        # list over a change nobody could establish, which keeps the
        # refusal below rather than reading "unknown" as "none touched".
        if (
            not source_changed
            and _scheduled(ctx, "coverage.meets_floor")
            and (touched_set.paths or touched_set.answerable or not _held_path_globs(ctx))
        ):
            return Verdict.na(
                "no touched source file is held to the floor; coverage.meets_floor "
                "is on the sweep cadence"
            )

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

    # Under a sweep cadence the only files still held are under `held_paths`,
    # and they owe a FULL run: a scoped figure is the lower bound the cadence
    # exists to stop holding to the floor (ADR-010 D8).
    if scope == "touched" and source_changed and _scheduled(ctx, "coverage.meets_floor"):
        run = ctx.evidence("coverage_run", {}) or {}
        if _run_scope(run) != "full":
            held = sorted(source_changed)
            return Verdict.fail(
                f"{len(held)} touched path(s) under {_HELD_PATHS_KEY} owe a full "
                "coverage run, and the coverage on record is not one",
                detail=", ".join(held[:6])
                + ". A scoped run measures a lower bound, which is why a "
                "scheduled floor is advisory; these paths are held back from the "
                "cadence, so their figure must come from a whole-scheme run.",
                fixes=("invariant pipeline coverage  # the full run held_paths owes",),
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
        # Extended, not rebound. This used to assign over `excluded`, which
        # was harmless only because nothing had been added to it yet — and
        # stopped being harmless the moment a declared non-source path was
        # (see above). A set-aside that vanishes from the verdict is the
        # vacuous green this rule spends most of its length preventing.
        newly_excluded, unmatched = _explain_absent_rows(ctx, unmatched, roots)
        excluded.extend(newly_excluded)
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
    else:
        # The wider scopes hold every measured row, so the split is over
        # rows: an advisory row is reported, never held.
        coverage, split_off = _split_advisory(ctx, coverage)
        advisory.extend(split_off)
        if split_off and not coverage:
            return Verdict.na("no measured file is held to the floor")

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


# -- the sweep cadence -------------------------------------------------


@rule(
    "sweep.no_full_run",
    summary="Under a sweep cadence, a change does not run the whole suite or "
    "whole-scheme coverage.",
    events=("pre-tool-use",),
)
def _sweep_no_full_run(ctx: RuleContext) -> Verdict:
    """A full run on a change, where the project moved full runs to a sweep.

    Two surfaces, one rule (ADR-010 D5). The CLI names its own run in
    ``ctx.extra["full_run"]`` — ``pipeline test`` or ``pipeline coverage``
    with no ``--selector`` is the configured whole-suite or whole-scheme
    command by construction, so that surface is exact for every host and for
    none. The hook reads a shell call, and fires only where the project's own
    template leads the call and the ecosystem adapter answers ``"full"`` for
    what follows; any doubt is no verdict, because a false "full" refuses a
    legitimate scoped run and a missed one only costs time.
    """
    scheduled = ctx.manifest.scheduled_gates()
    if not scheduled:
        return Verdict.na("no sweep cadence is configured")
    kind = ctx.extra.get("full_run")
    if kind is None:
        command = ctx.tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return Verdict.na("not screened: this call is not a shell command")
        kind = _full_run_in(ctx, command)
        if kind is None:
            return Verdict.na(
                "not a full run of the configured suite, or not one this "
                "ecosystem's adapter can tell is full"
            )
    # A whole-scheme coverage run runs the whole suite too, so it is caught
    # when either gate is on the cadence.
    if kind == "test" and "test.all_pass" not in scheduled:
        return Verdict.na("test.all_pass is not on the sweep cadence")

    held, _unanswerable = _held_paths_touched(ctx) if ctx.session else ([], None)
    if held:
        return Verdict.clean(
            f"a full run is owed: this change touches {len(held)} path(s) under "
            f"{_HELD_PATHS_KEY}",
            detail=", ".join(held[:6]),
        )
    what = "whole-scheme coverage" if kind == "coverage" else "whole-suite"
    scoped = (
        "invariant pipeline coverage --selector <test>"
        if kind == "coverage"
        else "invariant pipeline test --selector <test>"
    )
    return Verdict.fail(
        f"a {what} run on a change: {', '.join(sorted(scheduled))} "
        f"{'is' if len(scheduled) == 1 else 'are'} on the sweep cadence "
        f"({_cadence_phrase(ctx)})",
        detail="This project moved whole-suite and whole-scheme evidence to a "
        "scheduled sweep; a change records the suites it affects, and the "
        "sweep runs everything and files what broke.",
        fixes=(
            scoped,
            "invariant override sweep.no_full_run --reason '<why>'  # one deliberate full run",
        ),
    )


@rule(
    "sweep.fresh",
    summary="Under a sweep cadence, the last committed sweep is no older than "
    "the cadence plus its grace.",
    events=(),
)
def _sweep_fresh(ctx: RuleContext) -> Verdict:
    """How old the evidence holding the scheduled gates is (ADR-010 D6).

    Injected beside every scheduled gate (``core/machines``) and evaluated in
    ``invariant check``. It warns, and only warns: the manifest refuses a
    level that would stop work (D3 item 5), because a sweep that ran late
    leaves a change exactly as safe as one that ran on time, and a machine off
    over a weekend must not block every session. The record is read from the
    base branch's commits, never the working tree, so the answer is the same
    in every worktree and on every clone that holds the branch (D9).
    """
    cadence, grace = sweep_reader.cadence_of(ctx.manifest)
    if cadence is None:
        return Verdict.na("no sweep cadence is configured")
    last = _last_sweep(ctx)
    limit = f"the {cadence}-day cadence plus {grace} day(s) grace"
    fixes = (
        (
            f"invariant sweep run  # from the main checkout, on "
            f"{last.branch or 'the base branch'}; or schedule it (cron, a LaunchAgent, CI)"
        ),
    )
    advisory = (
        "sweep.fresh only warns. The scheduled gates are held by the sweep, so "
        "until one runs nothing has checked the whole suite since the last."
    )
    detail = _unreadable_records(last)
    if not last.readable:
        where = repr(last.branch) if last.branch else "a detached HEAD with no session base"
        return Verdict.fail(
            f"no sweep can be read: git could not read {where}",
            detail=" ".join(p for p in (advisory, detail) if p),
            fixes=fixes,
        )
    record = last.record
    if record is None:
        return Verdict.fail(
            f"no sweep recorded on {last.branch}",
            detail=" ".join(p for p in (advisory, detail) if p),
            fixes=fixes,
        )
    age = sweep_reader.age_days(record.ran_on, _today(ctx))
    which = f"sweep {record.seq} at {record.head[:12]}, {record.ran_on}"
    if age is None:
        return Verdict.fail(
            f"the last sweep on {last.branch} has a date that cannot be read "
            f"({record.ran_on!r}; {which}), so it is not fresh",
            detail=" ".join(p for p in (advisory, detail) if p),
            fixes=fixes,
        )
    if sweep_reader.overdue(age, cadence_days=cadence, grace_days=grace):
        return Verdict.fail(
            f"the last sweep on {last.branch} is {age} day(s) old ({which}), "
            f"past {limit}",
            detail=" ".join(p for p in (advisory, detail) if p),
            fixes=fixes,
        )
    return Verdict.clean(
        f"the last sweep on {last.branch} is {age} day(s) old ({which}), within {limit}",
        detail=detail,
    )


def _today(ctx: RuleContext):
    """The date ages are read against: ``extra["today"]`` when a caller pins it."""
    return ctx.extra.get("today") or sweep_reader.utc_today()


def _sweep_branch(ctx: RuleContext) -> str | None:
    """The branch whose sweeps hold this change: the session's base, else HEAD's.

    A session is judged against its BASE, where the sweep commits its records,
    not its own branch, which no sweep ever runs on. With no session (a
    scheduler, ``invariant check`` in the main checkout) the checked-out
    branch is the answer.
    """
    base = str((ctx.session or {}).get("base") or "")
    return base or git.current_branch(Path(ctx.manifest.root))


def _last_sweep(ctx: RuleContext) -> sweep_reader.LastSweep:
    return sweep_reader.last_on(
        Path(ctx.manifest.root), _sweep_branch(ctx), sweep_reader.directory_of(ctx.manifest)
    )


def _unreadable_records(last: sweep_reader.LastSweep) -> str:
    if not last.unreadable:
        return ""
    return (
        f"{len(last.unreadable)} committed record(s) could not be read and were "
        f"skipped: {', '.join(last.unreadable[:6])}."
    )


def sweep_cadence_summary(ctx: RuleContext) -> str:
    """``doctor``'s informational row: the cadence, its gates, and the last sweep.

    One wording for the reader that asks, in the spirit of
    :func:`coverage_advisory_exception`. Informational, never failing: a
    missing or stale sweep is ``sweep.fresh``'s warning, and a ``doctor`` red
    for it would be red on every project the week it adopts a cadence.
    """
    cadence, grace = sweep_reader.cadence_of(ctx.manifest)
    if cadence is None:
        return "none configured"
    gates = ", ".join(sorted(ctx.manifest.scheduled_gates()))
    held = _held_path_globs(ctx)
    parts = [f"every {cadence} day(s) + {grace} grace; scheduled: {gates}"]
    if held:
        parts.append(f"held_paths: {', '.join(held)}")
    verdict = _sweep_fresh(ctx)
    parts.append(("" if verdict.ok else "STALE: ") + verdict.message)
    return "; ".join(parts)


#: A leading ``NAME=value`` on a shell command: an environment assignment for
#: the command after it, never the command itself.
_ENV_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


def _full_run_in(ctx: RuleContext, command: str) -> str | None:
    """``"coverage"`` or ``"test"`` when the shell command runs that template in full.

    Each segment between shell separators is tried, leading ``VAR=value``
    assignments dropped. A template is matched as a token prefix, a template
    token carrying a ``{placeholder}`` matching any one token; the coverage
    template is tried first because the test template is usually a prefix of
    it. What follows the template is the adapter's to judge — the adapter of
    the ecosystem whose template it is, each declared ecosystem being asked
    about its own (ADR-013 D5).
    """
    tokens = _shell_words(command)
    if not tokens:
        return None
    ecosystems = [(eco, get_adapter(eco)) for eco in ctx.manifest.declared_ecosystems]
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token not in _SHELL_SEPARATORS:
            segment.append(token)
            continue
        while segment and _ENV_ASSIGNMENT.match(segment[0]):
            segment = segment[1:]
        for eco, adapter in ecosystems:
            for key in ("coverage", "test"):
                template = _shell_words(ctx.manifest.command_template(key, eco) or "")
                extra = _after_template(template, segment)
                if extra is not None and adapter.test_scope_of(extra) == "full":
                    return key
        segment = []
    return None


def _shell_words(command: str) -> list[str]:
    """A shell command as words, with ``;``, ``|`` and ``&&`` split off.

    `_shell_tokens` keeps ``start;`` as one word, which is right for the
    commit readers and wrong here, where the separators are what divide one
    command from the next. Nothing when the command cannot be read as words.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: nothing here is reliable
        return []


def _after_template(template: list[str], tokens: list[str]) -> list[str] | None:
    """What follows ``template`` in ``tokens``, or ``None`` if it does not lead them."""
    if not template or len(tokens) < len(template):
        return None
    for want, got in zip(template, tokens):
        if "{" in want and "}" in want:
            continue
        if want != got:
            return None
    return tokens[len(template):]


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
    # Something the gate deliberately did NOT read, on a call where it read
    # something else. Carried onto the verdict rather than dropped, because a
    # PASS that names one route and stays silent about the route it abstained
    # on is the shape of defect this rule keeps producing (BUG_001, BUG_020).
    notes: list[str] = []

    if ctx.is_edit:
        target = ctx.target_path
        content = _edit_content(ctx.tool_input)
        if target is not None and content and _in_privacy_scope(ctx, target, scope):
            scanned.append(str(target))
            findings.extend(scanner.scan_text(content, path=target))

    command = str(ctx.tool_input.get("command", "")) if ctx.tool_name == "Bash" else ""
    committing = _is_git_commit(command)

    # WHICH repository the commit is being made in. `-C` and its relatives
    # move it, and until BUG_048 nothing downstream of `_is_git_commit` asked
    # — so the gate read `ctx.cwd`'s index and vouched for a commit landing
    # somewhere else. Resolved before either half runs, because both halves
    # spell a path against it: the index the staged blobs come from, and the
    # directory a relative `-F` message file is read from.
    commit_cwd, redirection = (
        _git_command_cwd(command, ctx.cwd) if committing else (ctx.cwd, None)
    )
    if redirection:
        return Verdict.fail(
            "the repository this commit is made in could not be resolved",
            detail=redirection
            + " A verdict naming the option it will not follow is the honest "
            "floor here; a clean scan of the wrong tree is not.",
            fixes=(
                "commit from that repository's own directory",
                (
                    "or record the exception: invariant override privacy.clean "
                    "--reason '<why>'"
                ),
            ),
        )
    # A tree this project does not own. Privacy scope is spelled
    # project-relative (`_in_privacy_scope`), so it describes no path in
    # there: scanning would report a scan of nothing. This abstains rather
    # than refusing for the reason `_is_outside_project` exists at all — a
    # rule that blocks work in an unrelated checkout is one a project turns
    # off, and turning it off turns it off for the project it was meant to
    # govern. The abstention is stated on the verdict, so it is not a silent
    # pass either.
    foreign = committing and _is_outside_project(ctx, commit_cwd)

    # A transition hands this rule an EMPTY payload: no tool name, no edit, no
    # command to be a commit. `privacy.clean` is named as an exit gate by six
    # machines and every one of its routes needed a payload it does not have
    # there, so as a review-boundary gate it screened nothing at all
    # (BUG_047). What the boundary is actually about is the work this session
    # would put into its base branch.
    if not ctx.payload:
        reviewable, refusal = _review_boundary_content_in_scope(ctx, scope)
        if refusal is not None:
            return refusal
        for path, text in reviewable:
            scanned.append(path)
            findings.extend(scanner.scan_text(text, path=Path(path)))

    if "commit_messages" in scope:
        message = ctx.extra.get("commit_message")
        problem = None
        fixes: tuple[str, ...] = (_INLINE_MESSAGE_FIX,)
        if not message and ctx.tool_name == "Bash":
            message, problem, fixes = _extract_commit_message(
                command, cwd=commit_cwd, shell_cwd=ctx.cwd
            )
        if problem:
            # Fail closed, per the do-not-relax convention every other
            # unanswerable question here follows (AUD-07/08): a message this
            # gate is configured to scan and cannot read is not a clean one.
            return Verdict.fail(
                "the commit message could not be scanned",
                detail=problem,
                fixes=fixes,
            )
        if message:
            scanned.append("commit message")
            findings.extend(scanner.scan_text(message))

    if foreign:
        notes.append(
            f"The staged content was not screened: this commit is made in "
            f"{commit_cwd}, a repository outside this project, and privacy "
            f"scope names paths of this one."
        )
    elif committing or ctx.extra.get("committing"):
        staged, problem = _staged_content_in_scope(ctx, scope, commit_cwd)
        if problem:
            return Verdict.fail(
                "the staged content could not be scanned",
                detail=problem,
                fixes=(
                    (
                        "run `invariant privacy scan <path>` over what is "
                        "staged, then commit"
                    ),
                ),
            )
        for path, text in staged:
            scanned.append(path)
            findings.extend(scanner.scan_text(text, path=Path(path)))

    aside = " ".join(notes)
    if not scanned:
        return Verdict.na(
            _privacy_screened_nothing(ctx, committing=committing, foreign=foreign),
            detail=aside,
        )
    if not findings:
        return Verdict.clean(
            f"scanned {', '.join(scanned)}: no matches", detail=aside
        )

    shown = "; ".join(f.describe() for f in findings[:6])
    return Verdict.fail(
        f"{len(findings)} personal-data match(es) in content being written down",
        detail=shown
        + ". Matches are shown redacted on purpose: a report that quotes what "
        "it found is a second copy of it."
        + (f" {aside}" if aside else ""),
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


#: The legs a session's changed set is read from (ADR-014 D5, D9). The
#: repository leg is the record's own worktree, branch and base; the store
#: leg is the commits inside the session's docs-lock spans.
REPO_LEG = "repo"
STORE_LEG = "store"


def _changed_set(ctx: RuleContext, leg: str = REPO_LEG) -> _ChangedSet:
    """Every path this change touched on one leg (ADR-014 D9).

    The repository leg is the default and every gate's reading before
    stores existed; the store leg is :func:`_store_leg`'s, its paths
    absolute in the store's main checkout. Which gate reads which leg is D9:
    the docs gates read the doc leg, privacy reads every leg, and coverage,
    tests and guideline selection read the repository leg.
    """
    if leg == STORE_LEG:
        store = _store_leg(ctx)
        paths = {change.path for change in store.changes}
        return _ChangedSet(
            paths=tuple(sorted(paths, key=str)), answerable=store.answerable, base=""
        )
    if leg != REPO_LEG:
        raise ValueError(f"no leg named {leg!r}; the legs are {REPO_LEG} and {STORE_LEG}")
    return _repo_changed_set(ctx)


@dataclass(frozen=True)
class _StoreChange:
    """One store path a span of the session's docs lock covers (ADR-014 D9).

    ``committed`` is whether it changed in the store's commits across the
    span; an uncommitted change is a held unit's dirty path. ``start`` is the
    span's ``acquired_at_sha``, against which a file new in the span is told.
    """

    path: Path
    rel: str
    unit: str
    lease: str
    start: str
    committed: bool


@dataclass(frozen=True)
class _StoreLeg:
    """The session's store leg: what its docs-lock spans changed, or why that
    cannot be said."""

    root: Path | None
    changes: tuple[_StoreChange, ...] = ()
    answerable: bool = True
    problem: str | None = None


def _store_spans(session: Mapping | None) -> list[Mapping]:
    """Every docs-lock span on the session's store leg, open or closed."""
    spans: list[Mapping] = []
    for leg in (session or {}).get("legs") or []:
        if isinstance(leg, Mapping) and leg.get("kind") == STORE_LEG:
            spans.extend(s for s in leg.get("lock_spans") or [] if isinstance(s, Mapping))
    return spans


def _store_leg(ctx: RuleContext) -> _StoreLeg:
    """The commits inside the session's lock spans, and what a held unit carries.

    D9: for each span, the store's diff from its ``acquired_at_sha`` to its
    ``released_at_sha`` (the store's HEAD while it is still open), kept to the
    span's unit; plus, for a unit still held, its uncommitted paths. Units
    never overlap and only the holder writes one, so the commits a span
    covers are the session's (DEC_ADR_014_I4A). An in-repo project,
    and a session with no span, has an empty leg, which is an answer. A span
    git cannot read makes the leg unanswerable, never empty.
    """
    store = getattr(ctx.manifest, "store", None)
    if store is None:
        return _StoreLeg(None)
    root = Path(store.root)
    spans = _store_spans(ctx.session)
    if not spans:
        return _StoreLeg(root)
    try:
        config = doclock.read_config(root)
    except ManifestError as exc:
        return _StoreLeg(root, answerable=False, problem=str(exc))
    head = git.resolve_commit(root, "HEAD")
    dirty: list[str] | None = None
    changes: list[_StoreChange] = []
    for span in spans:
        unit, lease = str(span.get("unit") or ""), str(span.get("lease") or "")
        start = str(span.get("acquired_at_sha") or "")
        held = not span.get("released_at")
        end = head if held else str(span.get("released_at_sha") or "")
        if not start or not end:
            return _StoreLeg(
                root, answerable=False,
                problem=f"the span of {unit} names no store commit to read from",
            )
        names = [] if start == end else git.diff_names(root, start, end)
        if names is None:
            return _StoreLeg(
                root, answerable=False,
                problem=f"the store's commits across the span of {unit} "
                f"({start[:12]}..{end[:12]}) cannot be read",
            )
        for rel in names:
            if doclock.in_unit(rel, unit, config):
                changes.append(_StoreChange(root / rel, rel, unit, lease, start, True))
        if held:
            if dirty is None:
                dirty = git.dirty_paths(root)
                if dirty is None:
                    return _StoreLeg(
                        root, answerable=False,
                        problem="the store's uncommitted changes cannot be read",
                    )
            for rel in dirty:
                if doclock.in_unit(rel, unit, config):
                    changes.append(_StoreChange(root / rel, rel, unit, lease, start, False))
    return _StoreLeg(root, tuple(changes))


def _repo_changed_set(ctx: RuleContext) -> _ChangedSet:
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
    # Which tip carries the work is not always HEAD. `session merge` runs from
    # the MAIN checkout, where HEAD is the base itself, so `base...HEAD` is
    # empty and — being a list rather than None — reads as the answer "nothing
    # changed" rather than as a question that could not be asked. Every gate
    # sharing this union then screens nothing at exactly the boundary that
    # publishes the work. Name the branch when we are not standing on it.
    branch = str((ctx.session or {}).get("branch") or "")
    tip = branch if branch and git.current_branch(ctx.cwd) != branch else "HEAD"
    committed = git.branch_files(ctx.cwd, base, tip)
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
    """The docs directory inside the working tree the rule is running against.

    The one this run's messages name, and the one `docs.updated` resolves its
    changed paths against — both are questions about the tree in hand. Asking
    whether a RECORDED path is a doc is a different question, because that
    path was spelled by whichever tree the reader was in; use
    :func:`docs_roots` for it.
    """
    configured = ctx.manifest.path_for("docs")
    relative = _docs_relative(ctx.manifest, ctx.cwd)
    if relative is None:
        return configured  # an absolute path outside the project: use as given
    return (_repo_root(ctx) / relative).resolve()


def _docs_relative(manifest: Manifest, cwd: Path) -> Path | None:
    """``paths.docs`` spelled project-relative, or ``None`` outside the project.

    Through the shared normaliser (`core.manifest.project_relative`) rather
    than by relativising the resolved path against the manifest root alone. An
    absolute value naming the main checkout is a spelling of the same
    directory, but from inside a linked worktree the manifest root is the
    worktree, so the old test answered "outside the project" and the gate
    accepted reads of the main checkout's copy only (BUG_071, the BUG_046
    shape).
    """
    raw = manifest.get("paths.docs")
    bases: list[Path | None] = [manifest.root]
    if PurePosixPath(str(raw).strip()).is_absolute():
        bases += [git.toplevel(cwd), git.main_checkout(cwd)]
    relative = project_relative(raw, bases)
    return None if relative is None else Path(relative)


def docs_roots(manifest: Manifest, cwd: Path) -> list[Path]:
    """The docs directory in every working tree of this repository.

    One document, as many absolute spellings as the repository has trees. Which
    spelling gets RECORDED is decided by the attribution route — a read inside
    a worktree names that worktree — and which one gets ACCEPTED used to be
    decided by where the CLI happened to be standing. Those two answers are
    disjoint in the ordinary isolated-session case, which is how
    `docs.read_architecture` became a blocking gate with no legal move
    (BUG_046). Reducing both ends to the same set is the fix; widening what
    counts as evidence is not, so the set is the trees of THIS repository and
    stops there. A neighbouring checkout's `docs/architecture` is not this
    project's, however alike the two paths look.

    Takes the manifest and a directory rather than a :class:`RuleContext`, so
    the recording side in ``hosts/`` can ask the same question the reading side
    asks. Two definitions of "is this an architecture doc" is exactly the
    disjointness this fixes.
    """
    configured = manifest.path_for("docs")
    if _docs_in_store(manifest):
        # The store's main checkout holds the one copy: a store has no
        # worktree per session (ADR-014 D5), so a session reads it there.
        return [configured]
    relative = _docs_relative(manifest, cwd)
    if relative is None:
        # An absolute path outside the project: there is one copy by
        # construction, so there is nothing to spell two ways.
        return [configured]

    bases: list[Path | None] = [git.toplevel(cwd), manifest.root]
    bases += [w.path for w in git.worktrees(cwd)]
    roots: list[Path] = []
    for base in bases:
        if base is None:
            continue
        try:
            root = (Path(base) / relative).resolve()
        except OSError:  # pragma: no cover - a resolve that cannot answer
            continue
        if root not in roots:
            roots.append(root)
    return roots


def _docs_in_store(manifest: Manifest) -> bool:
    """Are the architecture docs kept in the project's store (ADR-014 D1)?"""
    records = manifest.get("records") or {}
    return getattr(manifest, "store", None) is not None and records.get("docs") == "store"


def _read_path(row: Any) -> str | None:
    """The path a ``docs_read`` row names: the row itself, or its ``path``."""
    if isinstance(row, str):
        return row
    if isinstance(row, Mapping) and isinstance(row.get("path"), str):
        return row["path"]
    return None


def _session_leases(session: Mapping | None) -> set[str]:
    """Every docs-lock lease the session held a unit under: its spans' leases."""
    leases: set[str] = set()
    for leg in (session or {}).get("legs") or []:
        if not isinstance(leg, Mapping) or leg.get("kind") != "store":
            continue
        for span in leg.get("lock_spans") or []:
            if isinstance(span, Mapping) and span.get("lease"):
                leases.add(str(span["lease"]))
    return leases


def is_architecture_doc(manifest: Manifest, cwd: Path, target: Path | str) -> bool:
    """Would a read of ``target`` be evidence for ``docs.read_architecture``?

    The recording side's copy of the reading side's question, asked through the
    same helper so the two cannot drift apart.
    """
    return any(_is_within(Path(target), root) for root in docs_roots(manifest, cwd))


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


def is_exempt_path(manifest: Manifest, cwd: Path, target: Path | str) -> bool:
    """Is ``target`` a path the project declared exempt from session isolation?

    The isolation rules' own question, asked through the same helper by a
    caller outside a rule: `domains/session` will not count a write to the
    work-item store or to `session.exempt_paths` as a session's first edit,
    because the exemption exists precisely because such a write is not work on
    the branch (BUG_062). Two definitions of "exempt" would drift.
    """
    return _is_exempt(RuleContext(manifest=manifest, cwd=Path(cwd)), Path(target))


def _is_exempt(ctx: RuleContext, target: Path | None) -> bool:
    """Paths a project has declared exempt from session isolation.

    Configuration and knowledge files are the motivating case: they are data
    rather than code, they are edited by every session, and routing them
    through a branch produces conflicts with no review value.
    """
    if target is None:
        return False
    patterns = [
        canonical
        for _written, canonical in _declared_in_project(
            ctx, ctx.manifest.get("session.exempt_paths") or []
        )
    ]

    # The work-item store is exempt for the same stated reason as config and
    # memory: it is data every session touches, and routing it through a branch
    # produces conflicts with no review value. It is not in the default list
    # because its path is configurable — the library knows where it put it, so
    # it adds it here rather than asking every project to repeat it.
    #
    # A project that wants backlog edits reviewed can say so with
    # items.exempt: false.
    if ctx.manifest.get("items.exempt", True):
        patterns.extend(_store_patterns(ctx, ctx.manifest.get("items.path")))

    # The report store, for the same reason and by the same mechanism (ADR-002
    # D4). The write scope is "the state root and the stores the manifest
    # declares", so a declared store is writable by construction rather than by
    # being added to a list of exceptions — the alternative turns a crisp
    # guarantee into an enumeration that grows on every feature.
    #
    # The report is a GENERATED artifact besides: a session that regenerates it
    # is running a command, not authoring code, and blocking that would make
    # the isolation rule refuse the one edit it has no review value over.
    patterns.extend(_store_patterns(ctx, ctx.manifest.get("paths.reports")))

    # The sweep records (ADR-010 D9): a declared store the engine commits to
    # from the main checkout, by the same mechanism and for the same reason.
    patterns.extend(_store_patterns(ctx, ctx.manifest.get("paths.sweeps")))

    if not patterns:
        return False

    for rel in _project_relative_forms(ctx, target):
        if any(fnmatch.fnmatch(rel, pattern) for pattern in patterns):
            return True
    return False


def _store_patterns(ctx: RuleContext, declared: Any) -> list[str]:
    """The exempt patterns a declared store contributes: itself and its tree."""
    return [
        pattern
        for _written, canonical in _declared_in_project(ctx, [declared])
        for pattern in (canonical, f"{canonical}/**")
    ]


def _declared_in_project(
    ctx: RuleContext, values: Iterable[Any]
) -> list[tuple[str, str]]:
    """``(as written, canonical)`` for every declared path inside the project.

    The one place a rule turns a manifest path into something it can compare
    with a target's project-relative spelling, through the one normaliser,
    `core.manifest.project_relative`. A declaration compared as written
    matched nothing whenever it was spelled ``./x``, ``x//y`` or with a
    climbing ``..`` — the exemption it declared was simply off (BUG_071).

    The written form rides along so a verdict can name the pattern the way
    the project wrote it. Entries naming nothing inside the project — an
    absolute path under no checkout, a ``..`` that climbs out, the root itself
    — are dropped: they cannot exempt or scope a path outside the project, and
    the root would otherwise widen to every file in it. The checkouts an
    absolute value is spelled against are the bases `_project_relative_forms`
    spells targets against, asked only when an absolute value is present.
    """
    bases: list[Path | None] | None = None
    declared: list[tuple[str, str]] = []
    for value in values:
        if value is None:
            continue
        written = str(value)
        if bases is None and PurePosixPath(written.strip()).is_absolute():
            bases = [ctx.manifest.root, git.toplevel(ctx.cwd), git.main_checkout(ctx.cwd)]
        canonical = project_relative(written, bases or ())
        if canonical is None or canonical == ".":
            continue
        declared.append((written, canonical))
    return declared


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


def _coverage_not_applicable(
    ctx: RuleContext, changed: set[str], roots: list[Path]
) -> list[tuple[str, Adapter]]:
    """Each changed path a declared adapter without coverage claims, with it.

    Sorted by path. A path is claimed when the adapter claims it in any root
    the change can be read in: a claim is positive evidence, and the roots
    are where the change's tree is.
    """
    adapters = [a for a in _ecosystem_adapters(ctx) if not a.coverage_applicable]
    if not adapters:
        return []
    bases = [Path(root) for root in roots]
    found: list[tuple[str, Adapter]] = []
    for rel in sorted(changed):
        for adapter in adapters:
            if any(adapter.claims(Path(rel), base) for base in bases):
                found.append((rel, adapter))
                break
    return found


def _not_applicable_verdict(not_applicable: list[tuple[str, Adapter]]) -> Verdict:
    """N/A naming each adapter that declared coverage not applicable, its
    reason, and the files."""
    adapters = list(dict.fromkeys(adapter for _rel, adapter in not_applicable))
    reasons = "; ".join(
        f"the {adapter.id} adapter declares coverage not applicable, because "
        f"{adapter.coverage_reason}"
        for adapter in adapters
    )
    return Verdict.na(reasons, detail=", ".join(rel for rel, _adapter in not_applicable))


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

    # Absence has to be judged in the tree the CHANGE produced, and that is not
    # any of `roots`. The MAIN checkout still carries a file the session branch
    # deleted right up until the merge lands, so `_first_existing` below finds
    # that stale copy, `located` is not None, and the deletion branch is never
    # reached — a change whose whole point is REMOVING a file could not clear
    # this gate, which is exactly the "gate no test can clear" the caller's own
    # comment warns about. Judging absence in the session worktree fixes it
    # while keeping the soundness `git.deleted_files` asks of every caller: its
    # commit leg also reports a path deleted and later RESTORED, and such a path
    # is present here, so it stays unexplained rather than being set aside.
    change_root = _session_worktree(ctx)

    excluded: list[str] = []
    unexplained: list[str] = []
    for rel in missing:
        # Asked FIRST, because the question it settles is not "where is this
        # file" but "did this change remove it", and only the second can be
        # answered once another root still holds a copy.
        if (
            change_root is not None
            and not (change_root / rel).exists()
            and any(_same_file(rel, form) for form in deleted_forms)
        ):
            excluded.append(f"{rel} (deleted by this change)")
            continue
        # A DIRECTORY is positive evidence, not an absence. `session files add`
        # accepts a path ending in `/`, so a declared bundle reaches the
        # changed set as a bare directory path — and `_first_existing` asks
        # `is_file()`, which made it indistinguishable from a misspelt file:
        # absent, undeletable, unexplainable, blocking forever. No coverage
        # reporter names a directory in a row, and the files inside one reach
        # this gate on their own through git, so setting it aside loses no
        # coverage of anything.
        directory = _first_existing_dir(rel, roots)
        if directory is not None:
            excluded.append(f"{rel} (a directory; a coverage row names a file)")
            continue
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
        # Both spellings, because the question has two halves that need
        # different ones: the Swift reader OPENS `found`, while the
        # directory convention is a statement about the project's own
        # layout and must be read from `rel`. Handing over only the
        # absolute path let an ancestor above the repository root decide
        # (BUG_042) — the same hazard the comment above guards
        # `_is_test_file` against, one question over.
        elif _carries_executable_code(adapters, found, Path(rel)) is False:
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


def _excluded_as_test_code(
    ctx: RuleContext, source_changed: set[str]
) -> tuple[set[str], list[str]]:
    """Hold out the paths a declared ecosystem's adapter claims as test code.

    Asked of the project-relative spelling, for the reason the coverage gate
    gives: the directory convention is a fact about the project's layout, and
    an absolute path can pick up a stray ``tests`` component from above the
    root. Only ``True`` holds a path out — an adapter with no opinion leaves
    it source, so a project declaring no ecosystem loses nothing.
    """
    adapters = _ecosystem_adapters(ctx)
    if not adapters:
        return source_changed, []
    kept: set[str] = set()
    excluded: list[str] = []
    for rel in sorted(source_changed):
        if _is_test_file(adapters, Path(rel)) is True:
            excluded.append(
                f"{rel} (a test file; the suite, not the architecture it measures)"
            )
        else:
            kept.add(rel)
    return kept, excluded


def _excluded_as_declared_non_source(
    ctx: RuleContext, source_changed: set[str]
) -> tuple[set[str], list[str]]:
    """Hold out paths the project has declared are not source code at all.

    A different question from `coverage_exclude`, and the two are not
    interchangeable — which is the whole reason this key exists.
    `coverage_exclude` means "measured, but not held to the floor", and it is
    applied AFTER the missing-row check deliberately, so that suppressing a
    path from the reporter cannot buy a pass through that door. Nothing in the
    manifest could say the earlier thing: "this path is not code, and no run
    in this project could ever produce a row for it". A consuming project
    versioning design bundles beside the code they specify had a blocking gate
    with no local fix and no work that could clear it, which is the family this
    library treats as an engine defect rather than as a configuration problem.

    Held out HERE, where the built-in documentation suffixes are held out, and
    not among the exclusions further down. The distinction is the same one
    `_explain_absent_rows` draws: this is a claim about what the path IS, which
    belongs before the gate starts asking what was measured about it.

    Every held-out path is NAMED in the verdict, with the pattern that spent
    it. An exclusion nobody can see is indistinguishable from a gate that did
    not run, and this one is a project's own policy rather than a fact the
    engine established — so the reader is owed both halves.
    """
    patterns = _declared_in_project(
        ctx, ctx.manifest.get("methodology.gates.non_source") or []
    )
    if not patterns:
        return source_changed, []

    kept: set[str] = set()
    excluded: list[str] = []
    for rel in sorted(source_changed):
        matched = _first_matching_glob(rel, patterns)
        if matched is None:
            kept.add(rel)
        else:
            excluded.append(f"{rel} (non_source {matched!r})")
    return kept, excluded


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
    patterns = _declared_in_project(
        ctx, ctx.manifest.get("methodology.gates.coverage_exclude") or []
    )
    if not patterns:
        return touched, []

    kept: dict[str, float] = {}
    excluded: list[str] = []
    for rel, pct in touched.items():
        matched = _first_matching_glob(rel, patterns)
        if matched is None:
            kept[rel] = pct
        else:
            excluded.append(f"{rel} {float(pct):g}% (coverage_exclude {matched!r})")
    return kept, excluded


_ADVISORY_KEY = "methodology.gates.coverage_advisory_paths"
_SCHEDULE_KEY = "methodology.gates.schedule"
_HELD_PATHS_KEY = "methodology.gates.schedule.held_paths"

#: The reason an advisory entry carries when a sweep cadence, not a glob,
#: made it advisory.
_SCHEDULED = "scheduled"


def _split_advisory(ctx: RuleContext, paths):
    """Split ``paths`` into the ones held to the floor and the advisory ones.

    The ONE place a touched file is classed HELD or ADVISORY, armed two ways
    (ADR-010 D2). A sweep cadence scheduling the floor makes every file
    advisory except those under ``schedule.held_paths``; with no cadence,
    ``coverage_advisory_paths`` makes the files under its globs advisory. The
    manifest refuses both at once, so a file never has two reasons.

    ``paths`` is a set of project-relative paths or a ``{path: pct}`` mapping;
    the held part comes back in the same shape, the advisory part as
    ``(path, reason)`` — the pattern as written, or :data:`_SCHEDULED`.
    Matched by the same normaliser and glob reader as `coverage_exclude` and
    `non_source`, so the lists cannot come to disagree about what a pattern
    names.
    """
    scheduled = _scheduled(ctx, "coverage.meets_floor")
    if scheduled:
        patterns = _declared_in_project(ctx, _held_path_globs(ctx))
    else:
        patterns = _declared_in_project(ctx, ctx.manifest.get(_ADVISORY_KEY) or [])
        if not patterns:
            return paths, []
    split_off: list[tuple[str, str]] = []
    kept = {} if isinstance(paths, dict) else set()
    for rel in sorted(paths):
        matched = _first_matching_glob(rel, patterns)
        advisory = matched is None if scheduled else matched is not None
        if advisory:
            split_off.append((rel, _SCHEDULED if scheduled else str(matched)))
        elif isinstance(kept, dict):
            kept[rel] = paths[rel]
        else:
            kept.add(rel)
    return kept, split_off


def _scheduled(ctx: RuleContext, gate: str) -> bool:
    """Has a sweep cadence moved this gate off the per-change path?"""
    return gate in ctx.manifest.scheduled_gates()


def _held_path_globs(ctx: RuleContext) -> list[str]:
    return [str(g) for g in ctx.manifest.get(_HELD_PATHS_KEY) or ()]


def _cadence_phrase(ctx: RuleContext) -> str:
    days = ctx.manifest.get(f"{_SCHEDULE_KEY}.cadence_days")
    return f"every {days} day(s), {_SCHEDULE_KEY}"


def _run_scope(run: Mapping[str, Any]) -> str | None:
    """``"full"``, ``"scoped"``, or ``None`` for a record that does not say.

    A record written before runs carried a scope says nothing, and nothing is
    not proof of a full run: the held-path checks read ``None`` as "not
    full", the do-not-relax direction.
    """
    scope = run.get("scope")
    if scope in ("full", "scoped"):
        return str(scope)
    return "scoped" if run.get("selectors") else None


def _held_paths_touched(ctx: RuleContext) -> tuple[list[str], Verdict | None]:
    """The touched paths under ``schedule.held_paths``, or the refusal to say.

    Asked only where held paths are configured, so a project without them
    never pays for a changed-set read. An unestablished changed set is
    refused rather than read as "no held path touched" — the BUG_012 reading
    of an unanswered question — and the second element carries that verdict.
    """
    globs = _held_path_globs(ctx)
    if not globs:
        return [], None
    changed = _changed_set(ctx)
    if not changed.paths and not changed.answerable:
        return [], _unestablished_changed_set(changed)
    patterns = _declared_in_project(ctx, globs)
    roots = _coverage_roots(ctx)
    held = sorted(
        {
            rel
            for rel in (_relative_to_any(path, roots) for path in changed.paths)
            if _first_matching_glob(rel, patterns) is not None
        }
    )
    return held, None


def _with_advisory_report(
    ctx: RuleContext, held: Verdict, advisory: list[tuple[str, str]]
) -> Verdict:
    """The held files' verdict, with every advisory file named in it.

    An advisory file never fails the gate, and is never silent in it either:
    its figure when a run measured one, flagged when it is under the floor,
    and "not measured" when nothing did — never an invented number. When the
    held set came to nothing, the pass is GRANTED by the exception and the
    message says so in full, because a pass that hides the reason it passed
    is the silent green this gate exists to prevent.
    """
    if not advisory:
        return held
    floor = float(ctx.manifest.get("methodology.gates.coverage_min", 0) or 0)
    report = _advisory_detail(ctx, advisory, floor)
    scheduled = all(reason == _SCHEDULED for _rel, reason in advisory)

    if held.skipped:
        if scheduled:
            message = (
                f"{len(advisory)} touched source file(s) advisory: "
                f"coverage.meets_floor is on the sweep cadence "
                f"({_cadence_phrase(ctx)}), so the sweep holds the {floor:g}% "
                "floor and nothing held to it needs a coverage run"
            )
        else:
            message = (
                f"{len(advisory)} touched source file(s) under {_ADVISORY_KEY}, "
                f"where the {floor:g}% floor is advisory, and nothing held to the "
                "floor needs a coverage run; a coverage run is still owed before "
                "that list is removed"
            )
        reason = f"Held files: {held.message}." if held.message else ""
        detail = " ".join(part for part in (report, reason, held.detail) if part)
        return Verdict.clean(message, detail=detail)

    detail = f"{held.detail}  {report}" if held.detail else report
    if held.ok:
        where = "on the sweep cadence" if scheduled else "under coverage_advisory_paths"
        return Verdict.clean(
            f"{held.message}; {len(advisory)} more advisory {where}, not held",
            detail=detail,
        )
    return Verdict.fail(held.message, detail=detail, fixes=held.fixes)


def _advisory_detail(
    ctx: RuleContext, advisory: list[tuple[str, str]], floor: float
) -> str:
    recorded = ctx.evidence("coverage", {}) or {}
    roots = _coverage_roots(ctx)
    coverage = {_relative_to_any(path, roots): pct for path, pct in recorded.items()}
    scheduled = all(reason == _SCHEDULED for _rel, reason in advisory)
    # Under a cadence the floor is held by the sweep, so the figure that
    # stands behind each advisory file is the one the last sweep measured.
    last = _last_sweep(ctx) if scheduled else None
    sweep_figure = _last_sweep_figure(last)
    entries: list[str] = []
    for rel, reason in advisory:
        # A glob is named as the project wrote it; the cadence is named once,
        # in the header, since it is the reason for every entry at once.
        tag = "" if reason == _SCHEDULED else f" ({reason!r})"
        tag += sweep_figure(rel)
        if not recorded:
            entries.append(f"{rel} not measured, no coverage recorded{tag}")
            continue
        hits = [pct for f, pct in coverage.items() if _same_file(rel, f)]
        if not hits:
            entries.append(f"{rel} not measured, no coverage row{tag}")
            continue
        pct = float(hits[0])
        below = f", below the {floor:g}% floor" if pct < floor else ""
        entries.append(f"{rel} {pct:g}%{below}{tag}")
    if scheduled:
        header = (
            f"Advisory on the sweep cadence ({_SCHEDULE_KEY}), so never held to "
            "the floor per change; a scoped figure is a lower bound"
        )
    else:
        header = f"Advisory under {_ADVISORY_KEY}, so never held to the floor"
    text = f"{header}: " + "; ".join(entries) + "."
    if last is not None:
        text += _last_sweep_note(last)
    run = ctx.evidence("coverage_run", {}) or {}
    overstating = run.get("overstating_hazards") or []
    if recorded and overstating:
        text += (
            " These figures may read HIGH; live on the measuring run: "
            + ", ".join(overstating)
            + "."
        )
    return text


def _last_sweep_figure(last: sweep_reader.LastSweep | None) -> Callable[[str], str]:
    """Per advisory file, what the last sweep measured for it, and at which commit.

    Empty where there is no coverage sweep to quote; :func:`_last_sweep_note`
    says why once rather than once per file.
    """
    record = last.record if last is not None else None
    if record is None or record.command != "coverage":
        return lambda _rel: ""
    head = record.head[:12]

    def figure(rel: str) -> str:
        hits = [pct for f, pct in record.files.items() if _same_file(rel, f)]
        if not hits:
            return f" (not in the last sweep at {head})"
        return f" (last sweep {float(hits[0]):g}% at {head})"

    return figure


def _last_sweep_note(last: sweep_reader.LastSweep) -> str:
    record = last.record
    if record is None:
        where = last.branch or "the base branch"
        return f" No sweep is recorded on {where} yet, so no whole-scheme figure stands behind these."
    if record.command != "coverage":
        return (
            f" The last sweep ({record.head[:12]}, {record.ran_on}) ran "
            f"commands.{record.command} and measured no coverage."
        )
    return f" Last sweep: {record.seq} at {record.head[:12]}, {record.ran_on}."


def coverage_advisory_exception(manifest: Any) -> str | None:
    """The standing exception `coverage_advisory_paths` grants, as one line.

    ``None`` when the list is empty. A configured list is a phase state the
    project chose deliberately, so the line is informational rather than a
    failure, and it is shown where an operator looks when they suspect a
    forgotten setting: the list is easy to leave behind once the phase that
    needed it is over.
    """
    globs = [str(g) for g in (manifest.get(_ADVISORY_KEY) or [])]
    if not globs:
        return None
    return (
        f"ACTIVE EXCEPTION: the coverage floor is advisory for "
        f"{', '.join(globs)} ({_ADVISORY_KEY}); a coverage run over those "
        "paths is owed before the list is removed"
    )


def _first_matching_glob(rel: str, patterns: list[tuple[str, str]]) -> str | None:
    """The first declared glob ``rel`` falls under, named as it was written.

    A glob matches the path itself or anything beneath it; the
    comparison is against the canonical spelling (`_declared_in_project`).
    """
    return next(
        (written for written, canonical in patterns if glob_matches(rel, canonical)),
        None,
    )


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
        reduced_before = _executable_text(adapters, Path(rel), before)
        reduced_after = _executable_text(adapters, Path(rel), after)
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

    roots = _coverage_roots(ctx)
    located = _first_existing(str(configured), roots)
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
        written, recorded = fields
        # Keyed by the canonical spelling, so `src/a.py` and `./src/a.py` are
        # one file recorded twice and refused as such. Keyed as written, both
        # were accepted and the lookup's `_same_file` took whichever came
        # first — a silent pick of one record over the other, which is what
        # the duplicate refusal exists to prevent (BUG_071).
        rel = project_relative(written, roots) or written
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


def sweep_below_floor(
    manifest: Any, files: Mapping[str, float]
) -> tuple[dict[str, float] | None, str | None]:
    """The HELD files a sweep's coverage leaves under the floor (ADR-010 D4).

    The floor's own policy, read through the floor's own helpers, so a sweep
    and a change cannot disagree about which path a glob names or what a
    baseline entry forgives: ``non_source`` and ``coverage_exclude`` paths are
    never held, and a file at or above its ``coverage_baseline`` entry is a
    pre-existing shortfall, not a finding. A file BELOW its entry is a
    regression and stays. Returns ``(below, None)``, or ``(None, why)`` when
    the baseline cannot be read: a record read in part would turn dropped
    entries into findings, or hide real ones.
    """
    ctx = RuleContext(manifest=manifest, cwd=Path(manifest.root))
    baseline, problem = _coverage_baseline(ctx)
    if problem is not None:
        return None, f"{problem.message}: {problem.detail}".rstrip(": ")
    floor = float(manifest.get("methodology.gates.coverage_min", 0) or 0)
    never_held = _declared_in_project(
        ctx,
        [
            *(manifest.get("methodology.gates.non_source") or []),
            *(manifest.get("methodology.gates.coverage_exclude") or []),
        ],
    )
    below = {
        rel: float(pct)
        for rel, pct in files.items()
        if float(pct) < floor and _first_matching_glob(rel, never_held) is None
    }
    _still, ratcheted, _regressed = _ratcheted_against_baseline(below, baseline, floor)
    forgiven = {rel for rel in below for entry in ratcheted if entry.startswith(f"{rel} ")}
    return {rel: pct for rel, pct in below.items() if rel not in forgiven}, None


def _ratchet_detail(ratcheted: list[str]) -> str:
    if not ratcheted:
        return ""
    return (
        "Not held to the floor, because a recorded baseline says the shortfall "
        "predates this change: " + "; ".join(ratcheted) + "."
    )


def _executable_text(
    adapters: list[Adapter], relative: Path, source: str
) -> str | None:
    """The first adapter with an opinion wins; no opinion answers ``None``.

    The path is what makes the first opinion the right one: an adapter
    answers only for its own ecosystem's files, so a file reaches the adapter
    whose language it is written in or relaxes nothing (BUG_103).
    """
    for adapter in adapters:
        reduced = adapter.executable_text(relative, source)
        if reduced is not None:
            return reduced
    return None


def _lexical_mask(
    adapters: list[Adapter], relative: Path, text: str, keep: str
) -> str | None:
    """The stacking rule again: the first adapter with a mask answers.

    An adapter masks only a file of its own ecosystem, so the first answer is
    the owning adapter's. ``None`` when no declared adapter owns the file,
    which leaves a rule needing a mask unanswerable for it rather than
    matching its raw text (ADR-012 D4).
    """
    for adapter in adapters:
        mask = adapter.lexical_mask(relative, text, keep)
        if mask is not None:
            return mask
    return None


def _references(
    adapters: list[Adapter], path: Path, relative: Path, root: Path
) -> list[Reference] | None:
    """The stacking rule for the ``imports`` engine: the first answer wins.

    An adapter answers only for its own ecosystem's files, so the first
    answer is the owning adapter's. ``None`` when no declared adapter can
    answer, which leaves a boundary rule unanswerable for the file rather
    than passing it (ADR-012 D4).
    """
    for adapter in adapters:
        answer = adapter.references(path, relative, root)
        if answer is not None:
            return answer
    return None


def _offered_engines(adapters: list[Adapter]) -> frozenset[str]:
    """Every guideline engine some declared adapter offers (ADR-012 D4)."""
    return frozenset().union(*(adapter.guideline_engines for adapter in adapters))


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


def _first_existing_dir(rel: str, roots: list[Path]) -> Path | None:
    """The first root under which ``rel`` names a directory, if any."""
    for root in roots:
        candidate = Path(root) / rel
        try:
            if candidate.is_dir():
                return candidate
        except OSError:  # pragma: no cover - an unreadable mount
            continue
    return None


def _ecosystem_adapters(ctx: RuleContext) -> list[Adapter]:
    return [get_adapter(eco) for eco in ctx.manifest.ecosystems]


def _carries_executable_code(
    adapters: list[Adapter], path: Path, relative: Path | None = None
) -> bool | None:
    """The first adapter with an opinion answers; ``None`` if none has one.

    Adapters stack, and one that does not know a language returns ``None`` for
    it rather than a wrong answer — so asking them in the manifest's order and
    taking the first real answer is what makes a mixed-ecosystem project work.
    """
    for adapter in adapters:
        answer = adapter.carries_executable_code(path, relative)
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
        cleaned = cleaned.removeprefix(remote_prefix)
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
#:
#: Ecosystem-independent by construction, and that is the admission price. Each
#: entry is a kind no coverage reporter produces a row for in ANY ecosystem —
#: prose, configuration, markup, stylesheets, vector art. A kind that is real
#: source somewhere does not belong here however clearly it is an asset in the
#: project in front of you: `.jsx` is production code with real rows under a
#: Node reporter, and listing it would silently exempt it there. That case is
#: the project's to declare, through `methodology.gates.non_source`.
#:
#: `.html`, `.css` and `.svg` were added for a Swift project that versions
#: design bundles next to the code they specify. The report proposed putting
#: them in the Xcode adapter's resource allowlist instead; they are here
#: because that allowlist is about kinds an Xcode *resource build step*
#: consumes, and the adapter already declines to hold documentation suffixes
#: for the reason that applies unchanged — the same judgement written down in
#: two layers is the duplication that drifts.
_UNMEASURED_SUFFIXES = (
    ".md",
    ".rst",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".html",
    ".htm",
    ".css",
    ".svg",
)


#: Repository metadata that carries no suffix for `_UNMEASURED_SUFFIXES` to
#: see, matched on the whole file name. The same admission price applies: no
#: reporter in any ecosystem has a row for one. All but `CODEOWNERS` are prose;
#: that one is configuration, ownership rather than structure. `LICENSE-MIT`
#: and its kin are the Rust convention for a dual licence, which is why the
#: licence names also match as a prefix.
_UNMEASURED_NAMES = frozenset(
    {"LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "CONTRIBUTORS", "CODEOWNERS"}
)
_UNMEASURED_NAME_PREFIXES = ("LICENSE-", "LICENCE-", "COPYING-")

#: The subset of `_UNMEASURED_SUFFIXES` no diagram can depend on — what
#: `docs.updated` holds out. Configuration, markup and stylesheets are absent
#: deliberately: unmeasurable is not the same as unstructural (BUG_064).
_PROSE_SUFFIXES = (".md", ".rst", ".txt")


def _is_metadata_name(path: str) -> bool:
    name = Path(str(path)).name.upper()
    return name in _UNMEASURED_NAMES or name.startswith(_UNMEASURED_NAME_PREFIXES)


def _is_prose(path: str) -> bool:
    return str(path).lower().endswith(_PROSE_SUFFIXES) or _is_metadata_name(path)


def _looks_like_docs(path: str) -> bool:
    return str(path).lower().endswith(_UNMEASURED_SUFFIXES) or _is_metadata_name(path)


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

    declared: list[Any] = []
    for key in scope:
        for manifest_key in _PRIVACY_SCOPE_PATHS.get(key, ()):
            declared.append(ctx.manifest.get(manifest_key) or None)
        if key not in _PRIVACY_SCOPE_KEYWORDS:
            # A literal project-relative prefix. This is how a project widens
            # scope beyond what paths.* names — e.g. all of ``docs/`` when
            # paths.docs points at one subtree of it (AUD-15).
            declared.append(key)
    # Through the shared normaliser, like every other declared path a rule
    # compares: `Path()` alone left a climbing `..` and an absolute in-project
    # path spelled so that no target could ever fall under them (BUG_071).
    scoped_prefixes = [canonical for _written, canonical in _declared_in_project(ctx, declared)]

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


def _staged_content_in_scope(
    ctx: RuleContext, scope: set[str], repo: Path | None = None
) -> tuple[list[tuple[str, str]], str | None]:
    """What this commit would put into history, for the paths scope covers.

    ``repo`` is the repository the commit is being made IN, which is not
    always the one the call is standing in: `git -C <other> commit` names a
    different index, and asking `ctx.cwd` for it screened one tree while
    vouching for a commit into another (BUG_048). The caller resolves it,
    because deciding it needs the whole command, not just the paths.

    Screening at the COMMIT is what makes the gate independent of the route
    that wrote the file. ``_edit_content`` can only read a payload an edit
    tool announced itself with, so a heredoc, a ``sed -i``, a ``cp`` or a
    generator emitting a document all landed unscreened — and the rule
    reported "nothing in privacy scope was written here" about every one of
    them (BUG_027). Nothing in a staged blob records which tool produced it,
    which is exactly why nothing here has to enumerate them: the next route
    will not be a tool name either.

    The WHOLE staged blob, not the added lines of a diff. An exemption marker
    reaches a block (BUG_045), so a marker sitting on an unchanged line above
    an added one is invisible to a scan of the addition alone — and the gate
    would then block on a finding the project had already exempted, in a spot
    the author cannot see from the diff.

    Returns ``(pairs, problem)``. ``problem`` is set when the commit's content
    exists and could not be read, and the caller fails closed on it: this is
    the one gate here whose subject cannot be recovered afterwards, because
    nothing un-writes an identifier out of a published history.
    """
    repo = Path(repo) if repo is not None else ctx.cwd
    listed = git.staged_files(repo, exclude_deleted=True)
    if listed is None:
        return [], (
            f"git could not list what is staged in {repo}, so the content "
            "this commit would put into history could not be read."
        )

    root = git.toplevel(repo) or ctx.manifest.root
    found: list[tuple[str, str]] = []
    for rel in listed:
        # Staged paths are spelled against the repository toplevel, so they
        # are resolved against it and not against ``ctx.cwd`` — a commit typed
        # in a subdirectory names the same blobs.
        if not _in_privacy_scope(ctx, Path(root) / rel, scope):
            continue
        blob = git.staged_blob(repo, str(rel))
        if blob is None:
            return [], (
                f"{rel} is staged and inside privacy scope, and its staged "
                "content could not be read."
            )
        try:
            found.append((str(rel), blob.decode("utf-8")))
        except UnicodeDecodeError:
            # Bytes that do not decode carry nothing that can leak in TEXT
            # form — the same answer ``PrivacyScanner.scan_file`` gives a
            # binary file, and the same one ``invariant privacy scan`` gives.
            # Counted as looked at rather than dropped, so a commit of a
            # diagram is not a gate no test can clear.
            found.append((str(rel), ""))
    return found, None


def _review_boundary_content_in_scope(
    ctx: RuleContext, scope: set[str]
) -> tuple[list[tuple[str, str]], Verdict | None]:
    """What this session would put into its base branch, for the scoped paths.

    The review boundary asks a different question from the commit boundary,
    and that is why it needs its own reader. A commit is one act with one
    index; a review is a claim about a whole branch, and by the time it is
    made the work is committed by construction — so the index, the one thing
    ``_staged_content_in_scope`` can see, is normally empty there. That is how
    this gate came to be named by six machines and screen nothing in any of
    them (BUG_047).

    The subject is therefore the union three other end-of-phase gates already
    read: the branch's own commits against its base, plus the index and the
    working tree for what is not committed yet, plus whatever the session
    declared. That union is ``_changed_set``, and reading it through the same
    helper is the point — BUG_012 was one insight implemented in one rule and
    not propagated to the three beside it, and a fifth spelling of the same
    query is how that recurs.

    Content is read from whichever tree the merge would actually publish, and
    those are two different trees at two different boundaries. Inside the
    session's own worktree — the review proper — that is the working tree,
    which is why uncommitted work is screened here at all. But `session merge`
    must run from the MAIN checkout (merging inside the branch's own worktree
    merges it into itself and exits zero), and there the working tree still
    holds the PRE-merge text. Reading it would re-read the copy the merge is
    about to REPLACE.

    For most sessions the two agree. It bites exactly the session that REMOVES
    a violation from a tracked file: the gate finds the very text the branch
    fixes and refuses, so the remediation this gate exists to encourage is the
    one thing it could never pass. When the checkout is not on the session's
    branch, the branch tip is therefore the subject.

    A path the branch ADDED and later REMOVED still sits in the branch's
    history and is not read here; it was screened at the commit that added it,
    which is the boundary that could still refuse it.

    Returns ``(pairs, refusal)``. ``refusal`` is a verdict rather than a
    string because the two ways this question goes unanswered are different
    facts with different fixes, and flattening them would cost the reader the
    one thing that tells them what to do.
    """
    changed = _changed_set(ctx)
    if not changed.answerable:
        # Never a skip: a gate reporting an unanswerable query as "nothing to
        # check" is BUG_012 reintroduced, and here it would be reintroduced on
        # the one gate whose subject cannot be recovered afterwards.
        return [], _unestablished_changed_set(changed)

    root = _repo_root(ctx)
    # The branch tip is the subject only when this checkout is NOT on it —
    # i.e. the merge case. In the session's own worktree the working tree is
    # what the merge publishes, uncommitted work included, and reading the tip
    # there would stop screening exactly what the review boundary is for.
    branch = str((ctx.session or {}).get("branch") or "")
    publishes = branch if branch and git.current_branch(ctx.cwd) != branch else ""
    found: list[tuple[str, str]] = []
    for path in changed.paths:
        absolute = path if Path(path).is_absolute() else Path(root) / path
        if not _in_privacy_scope(ctx, absolute, scope):
            continue
        if publishes:
            text = git.file_at(ctx.cwd, publishes, _rel(absolute, Path(root)))
            # `None` is "not readable at that revision", which for a path in
            # the changed set means the branch does not carry it — deleted, or
            # never there. Nothing of it lands in the merged tree, so it is
            # skipped for the same reason a missing file is below.
            if text is not None:
                found.append((_rel(absolute, Path(root)), text))
            continue
        try:
            found.append((_rel(absolute, Path(root)), absolute.read_text("utf-8")))
        except (FileNotFoundError, IsADirectoryError):
            # Removed by this change, so nothing of it lands in the merged
            # tree. Failing closed here would refuse every review that deleted
            # a document — the same reason `staged_files` grows an
            # `exclude_deleted`. A DIRECTORY joins it: a declared file set may
            # name one, and a directory holds no text of its own to leak.
            continue
        except UnicodeDecodeError:
            # Bytes that do not decode carry nothing that can leak in TEXT
            # form, the answer `PrivacyScanner.scan_file` already gives a
            # binary file. Counted as looked at, so committing a diagram is
            # not a gate no review can clear.
            found.append((_rel(absolute, Path(root)), ""))
        except OSError as exc:
            return [], Verdict.fail(
                "content this session would merge could not be scanned",
                detail=(
                    f"{_rel(absolute, Path(root))} is inside privacy scope and "
                    f"could not be read ({exc.strerror or exc}). An unanswered "
                    "question is not a pass, and nothing un-writes an "
                    "identifier out of a published history."
                ),
                fixes=(
                    "invariant privacy scan <path>",
                    (
                        "or record the exception: invariant override "
                        "privacy.clean --reason '<why>'"
                    ),
                ),
            )
    if getattr(ctx.manifest, "store", None) is not None:
        store_found, refusal = _store_leg_content_in_scope(ctx, scope)
        if refusal is not None:
            return [], refusal
        found.extend(store_found)
    return found, None


def _store_leg_content_in_scope(
    ctx: RuleContext, scope: set[str]
) -> tuple[list[tuple[str, str]], Verdict | None]:
    """The store leg's part of the review boundary (ADR-014 D9: privacy reads
    every leg).

    The store's docs land on its main line at release, before the review, so
    the content is read from the store's main checkout, where it already is.
    Scope is spelled store-relative: ``all``, and each keyword whose path
    resolves into the store. A literal prefix is spelled against the
    repository and reaches no store path; the store's own ``privacy`` block
    is D8's, increment 6.
    """
    leg = _store_leg(ctx)
    if not leg.answerable:
        return [], Verdict.fail(
            "the store leg this session would land could not be scanned",
            detail=f"{leg.problem}. An unanswered question is not a pass.",
            fixes=("invariant store status", "invariant session status"),
        )
    found: list[tuple[str, str]] = []
    for rel in sorted({change.rel for change in leg.changes}):
        if not _in_store_privacy_scope(ctx, leg.root, rel, scope):
            continue
        path = leg.root / rel
        try:
            found.append((str(path), path.read_text("utf-8")))
        except (FileNotFoundError, IsADirectoryError):
            continue  # removed in the span: nothing of it is on the main line
        except UnicodeDecodeError:
            found.append((str(path), ""))
        except OSError as exc:
            return [], Verdict.fail(
                "content this session landed in its store could not be scanned",
                detail=f"{path} is inside privacy scope and could not be read "
                f"({exc.strerror or exc}).",
                fixes=("invariant privacy scan <path>",),
            )
    return found, None


def _in_store_privacy_scope(ctx: RuleContext, root: Path, rel: str, scope: set[str]) -> bool:
    """Is the store-relative ``rel`` inside privacy scope, spelled store-relative?"""
    if "all" in scope:
        return True
    store = root.resolve()
    for key in scope:
        for manifest_key in _PRIVACY_SCOPE_PATHS.get(key, ()):
            value = ctx.manifest.get(manifest_key)
            if not isinstance(value, str) or not Path(value).is_absolute():
                continue
            try:
                prefix = Path(value).resolve().relative_to(store).as_posix()
            except ValueError:
                continue
            if rel == prefix or rel.startswith(prefix.rstrip("/") + "/"):
                return True
    return False


def _privacy_screened_nothing(
    ctx: RuleContext, *, committing: bool, foreign: bool = False
) -> str:
    """Why this call was not screened, in a sentence that is true OF IT.

    One sentence — "nothing in privacy scope was written here" — used to be
    returned on every route that reached the end, including the
    shell-mediated write where it was affirmatively false. That is what let
    BUG_027 survive a long session in which every edit went through a heredoc:
    the gate answered, the answer read like a pass, and nobody re-checked. A
    skip and a pass are different facts (BUG_020), and a skip that misstates
    its own reason is the harder of the two to notice.
    """
    if ctx.is_edit:
        return "nothing in privacy scope was written here"
    if foreign:
        return "not screened: this commit is made in a repository outside the project"
    if committing:
        return "no staged path in this commit is inside privacy scope"
    if not ctx.payload:
        base = str((ctx.session or {}).get("base") or "the base branch")
        return (
            f"no path this session would put into {base} is inside privacy scope"
        )
    if ctx.tool_name == "Bash":
        return (
            "not screened: a shell command is not read as it runs — what it "
            "writes is screened when it is committed"
        )
    if ctx.tool_name:
        return f"not screened: {ctx.tool_name} is not an Edit, a Write or a commit"
    return "not screened: this turn made no Edit, Write or commit"


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


def _shell_tokens(command: str) -> list[str]:
    """A shell command as words, or nothing when it cannot be read as words."""
    try:
        return shlex.split(command)
    except ValueError:  # unbalanced quotes: nothing here is reliable
        return []


def _is_git_commit(command: str) -> bool:
    """Whether this shell command runs ``git commit``.

    Token-aware rather than a substring test, in both directions: ``git config
    commit.gpgsign false`` is not a commit, and ``git add -A && git commit -m
    …`` — the chained spelling agents actually emit — is. A path-qualified
    ``/usr/bin/git`` counts, and a ``git commit`` inside a quoted string is one
    word and so does not.

    Both halves of ``privacy.clean`` ask through here. A rule whose message
    half and content half disagreed about what a commit is would screen one
    and not the other on the very same call, which is the shape of the defect
    this function was added for (BUG_027).
    """
    saw_git = False
    for token in _shell_tokens(command):
        if saw_git and token == "commit":
            return True
        if PurePosixPath(token).name == "git":
            saw_git = True
    return False


#: git options that take their value as the NEXT token and are not `-C`.
#: They have to be listed only so the scan steps over their value: a bare
#: word is otherwise read as the subcommand, and `git -c commit.gpgsign=false
#: -C <other> commit` would stop at the config pair and never see the `-C`.
_GIT_VALUE_OPTIONS = frozenset({"-c", "--namespace", "--exec-path", "--super-prefix"})

#: Redirections of the same class as `-C` that this rule will NOT follow.
#: Resolving them means reproducing git's own gitdir/worktree pairing, and a
#: half-right resolution here reports a scan of a tree nobody asked about —
#: the exact failure being fixed. Naming the option and failing closed is the
#: honest floor (AUD-09).
_GIT_LAYOUT_OPTIONS = frozenset({"--git-dir", "--work-tree"})


def _git_command_cwd(command: str, cwd: Path) -> tuple[Path, str | None]:
    """The directory the ``git commit`` in this command actually runs in.

    Returns ``(directory, problem)``. ``problem`` is set when the commit
    redirects git somewhere this will not follow, and the caller fails closed
    on it.

    Read per INVOCATION rather than per command, because the command is
    routinely more than one: ``git add -A && git -C <other> commit -m …`` is
    the chained spelling agents actually emit, and a reader that stopped at
    the first subcommand would resolve the ``add``'s directory and hand it to
    the commit. Only the invocation that commits decides, which is also what
    keeps ``--git-dir`` on a neighbouring ``rev-parse`` from failing a commit
    it has nothing to do with.

    ``-C`` is REPEATABLE and cumulative within one invocation, each path
    resolved against the one before — ``git -C a -C b`` runs in ``a/b``. A
    reader that took the first and stopped would land one directory up, which
    is usually a real directory with a real index, so the mistake produces a
    confident scan of the wrong tree rather than an error anyone notices.

    Only git's OWN options are read, which is why each scan stops at the
    subcommand: ``commit -C <commit>`` reuses another commit's message and has
    nothing to do with directories, and a reader that kept going would take it
    for one.
    """
    tokens = _shell_tokens(command)
    index = 0
    while index < len(tokens):
        if PurePosixPath(tokens[index]).name != "git":
            index += 1
            continue
        index += 1
        resolved = Path(cwd)
        refusal: str | None = None
        while index < len(tokens):
            token = tokens[index]
            head = token.split("=", 1)[0]
            if head in _GIT_LAYOUT_OPTIONS:
                # Held, not returned yet: it only matters if this invocation
                # turns out to be the commit.
                refusal = refusal or (
                    f"{head} points git at a repository this scan does not "
                    "follow, so the content this commit would put into "
                    "history could not be read."
                )
                index += 2 if "=" not in token else 1
                continue
            if token == "-C":
                following = tokens[index + 1] if index + 1 < len(tokens) else ""
                if not following:
                    # Nothing to resolve against. Reachable only at the very
                    # end of the command — where no subcommand follows, so
                    # this invocation is not the commit — and for the literal
                    # `-C ""`, which git itself reads as no change of
                    # directory. Both mean: leave the directory where it is.
                    break
                candidate = Path(following)
                resolved = (
                    candidate if candidate.is_absolute() else resolved / candidate
                )
                index += 2
                continue
            if token in _GIT_VALUE_OPTIONS:
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            break  # the subcommand: what follows belongs to it, not to git
        if index >= len(tokens) or tokens[index] != "commit":
            continue  # some other invocation in the chain; keep looking
        if refusal:
            return resolved, refusal
        if not resolved.is_dir():
            return resolved, (
                f"-C names {str(resolved)!r}, which is not a directory at scan "
                "time, so the content this commit would put into history could "
                "not be read."
            )
        return resolved, None
    return Path(cwd), None


_INLINE_MESSAGE_FIX = "write the message inline with -m '<message>'"

#: The remedy when the ``-F`` file is written by the very command that commits.
#: A retry of the same call cannot help: a denied call never ran, so the file
#: is still missing the next time the hook looks (BUG_067).
_SEPARATE_CALL_FIXES = (
    "write the file in a separate call first, then commit with -F",
    "or " + _INLINE_MESSAGE_FIX,
)

#: Shell words that end one command of a chain, so a ``tee`` stops reading
#: its file arguments there.
_SHELL_SEPARATORS = frozenset({"|", "||", "&&", ";", "&"})

#: An output redirection: ``>``, ``>>`` or ``>|``, with or without its target
#: attached to the operator in the same word.
_OUTPUT_REDIRECT = re.compile(r"(>>|>\|?)(.*)", re.DOTALL)


def _shell_write_targets(tokens: list[str]) -> list[str]:
    """The files a shell command writes, as spelled in its words.

    Deliberately narrow: an output redirection (``>``, ``>>``, ``>|`` — which
    is also how a heredoc-fed ``cat > f <<EOF`` writes) and a ``tee [-a]``
    file argument. Anything else is not recognised, and the caller keeps its
    generic wording for it: a cause named wrongly is worse than one left
    general.
    """
    targets: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        redirect = _OUTPUT_REDIRECT.fullmatch(token)
        if redirect:
            attached = redirect.group(2)
            if attached:
                targets.append(attached)
            elif index + 1 < len(tokens):
                targets.append(tokens[index + 1])
                index += 1
        elif PurePosixPath(token).name == "tee":
            index += 1
            while index < len(tokens) and tokens[index] not in _SHELL_SEPARATORS:
                word = tokens[index]
                if word.startswith((">", "<")):
                    break  # a redirection of tee itself: read it as one
                if not word.startswith("-"):
                    targets.append(word)
                index += 1
            continue
        index += 1
    return targets


def _joined(name: str, base: Path | None) -> Path:
    """``name`` as the shell or git would spell it from ``base``."""
    path = Path(name)
    if not path.is_absolute():
        path = Path(base or Path.cwd()) / path
    return path


def _extract_commit_message(
    command: str, cwd: Path | None = None, shell_cwd: Path | None = None
) -> tuple[str, str | None, tuple[str, ...]]:
    """The message a ``git commit`` invocation carries, or why it is unreadable.

    Returns ``(message, problem, fixes)``. ``problem`` is set when the command
    names a message this scan cannot read — a ``-F`` file that is absent at
    hook time, or ``-F -`` (stdin, which a hook never sees). The caller fails
    closed on it: the scan's whole subject exists and could not be looked at,
    and an unanswered question is not a pass (AUD-09). ``fixes`` is the
    remedy for that cause.

    ``cwd`` is where git reads a relative ``-F`` path (``-C`` applied);
    ``shell_cwd`` is where the shell resolves a relative redirection target.
    """
    if not _is_git_commit(command):
        return "", None, (_INLINE_MESSAGE_FIX,)
    tokens = _shell_tokens(command)
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token == "--message" or _COMMIT_MESSAGE_CLUSTER.fullmatch(token):
            return following, None, (_INLINE_MESSAGE_FIX,)
        if token.startswith("--message="):
            return token.split("=", 1)[1], None, (_INLINE_MESSAGE_FIX,)
        name = None
        if token == "--file" or _COMMIT_FILE_CLUSTER.fullmatch(token):
            name = following
        elif token.startswith("--file="):
            name = token.split("=", 1)[1]
        if name is None:
            continue
        message, problem = _read_commit_message_file(name, cwd)
        if problem and name not in ("", "-") and _written_by(
            tokens, _joined(name, cwd), shell_cwd or cwd
        ):
            return "", (
                f"-F names {name!r}, which this same command writes. A hook "
                "runs before the command, so the file cannot exist at scan "
                "time, and the message would land in history unscanned."
            ), _SEPARATE_CALL_FIXES
        return message, problem, (_INLINE_MESSAGE_FIX,)
    return "", None, (_INLINE_MESSAGE_FIX,)


def _written_by(tokens: list[str], path: Path, shell_cwd: Path | None) -> bool:
    """Whether one of the command's own writes lands on ``path``."""
    wanted = os.path.realpath(path)
    return any(
        os.path.realpath(_joined(target, shell_cwd)) == wanted
        for target in _shell_write_targets(tokens)
    )


def _read_commit_message_file(name: str, cwd: Path | None) -> tuple[str, str | None]:
    """A ``git commit -F`` message, read the way git itself would."""
    if not name:
        return "", "-F names no file"
    if name == "-":
        return "", (
            "-F - reads the message from stdin, which a hook cannot see. "
            "The message would land in history unscanned."
        )
    try:
        return _joined(name, cwd).read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError):
        return "", (
            f"-F names {name!r}, which could not be read at scan time. The "
            "message would land in history unscanned."
        )
