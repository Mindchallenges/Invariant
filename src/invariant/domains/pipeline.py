"""The pipeline domain — one task's progress through the configured methodology.

The evidence dictionary is the point of this module. Gates do not re-derive
whether the suite passed or whether the docs were read; they read what was
recorded at the moment those things actually happened. That is the difference
between a gate that verifies and a gate that can be satisfied by accident.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..adapters import get_adapter
from ..core.errors import GateFailure, InvariantError, TransitionError, UsageError
from ..core.fsm import Machine
from ..core.machines import ABANDON_ROLE, load_pipeline_machine
from ..core.manifest import Level
from ..core.rules import (
    Evaluated,
    RuleContext,
    apply_acknowledgements,
    evaluate,
    get_rule,
)
from ..core.stats import Usage, WatermarkSource
from ..core.store import JsonStore, PreservesUnknownKeys


def _refuse_chained_coverage(coverage: str, report: str, bundle: str) -> None:
    """Stop a manifest that still runs both legs as one command.

    Projects wrote ``xcodebuild ... ; xcrun xccov ...`` into ``commands.coverage``
    because that is what this engine used to ship. Such a manifest cannot simply
    be tolerated once the report leg exists on its own: the report would run
    twice, and — the part that matters — the first command's exit status would
    STILL be the reporter's, so the split would appear to be adopted while the
    defect it exists to remove carried on unchanged. A migration that silently
    does nothing is worse than one that asks.

    Detection is by containment rather than by looking for a ``;``, so a project
    that legitimately chains setup into its coverage command is not caught: the
    resolved report command, minus the per-run bundle path, must literally
    appear inside the coverage command.
    """
    probe = " ".join(report.replace(bundle, " ").split())
    if not probe or probe not in " ".join(coverage.split()):
        return
    raise UsageError(
        "commands.coverage still runs the reporting tool itself.\n"
        f"  coverage:        {coverage}\n"
        f"  coverage_report: {report}\n"
        "  A shell reports the LAST command's status, so chained this way a "
        "red suite followed by a clean read exits 0 and a green suite "
        "followed by a failed read exits non-zero — the suite's own result "
        "is unreadable in both directions.\n"
        "  Split them: leave ONLY the command that runs the suite in "
        "commands.coverage, and put the reporting command in "
        "commands.coverage_report. Anything the first leg needs in its "
        "environment the second leg needs too — a `VAR=value cmd` prefix "
        "applies to that one command alone and never crossed the `;`."
    )


#: Variables stripped from the environment a spawned suite inherits.
#:
#: ``INVARIANT_SESSION_ID`` is how a caller names the session a command should
#: record against, and the test command is a CHILD PROGRAM, not a caller. In
#: the one repository whose own suite exercises session creation — this one —
#: inheriting it made every test that starts a session collide with the real,
#: live session and die on "session already exists": 25 false failures written
#: into durable pipeline evidence, against a bare run of the same suite that
#: was fully green (BUG_035). The documented way to name a run for recording
#: purposes must not change what the suite it records measures.
#:
#: An inflated failure count on a phase that later reads green is the harder
#: of the two mistakes to notice: a false green hides behind nothing, a false
#: red hides behind a plausible regression a reader has to actively disprove.
CHILD_ENV_STRIPPED = ("INVARIANT_SESSION_ID",)


def _child_env() -> dict[str, str]:
    """The environment for a command this domain spawns."""
    env = dict(os.environ)
    for name in CHILD_ENV_STRIPPED:
        env.pop(name, None)
    return env


#: How an agent came to be in ``agents_run``. Kept beside the list rather than
#: folded into it, because the gate reads names and an auditor reads evidence:
#: changing the list's shape would break every stored record and every gate for
#: the sake of a question the gate does not ask.
#:
#: ``dispatch`` — a parent invoked this agent and the invocation resolved to
#: this session. ``actor`` — the host itself named this agent as the one making
#: a call that landed in this pipeline, which is the stronger reading: it is
#: the agent's own work being observed rather than a request for it.
VIA_DISPATCH = "dispatch"
VIA_ACTOR = "actor"

#: What :meth:`PipelineDomain.record_agent_run` did. Returned rather than
#: logged, so the hook that called it can surface a drop to the session that
#: would otherwise meet it at a blocking gate (BUG_028).
RECORDED = "recorded"
ALREADY = "already"
NO_PIPELINE = "no-pipeline"


@dataclass
class Pipeline(PreservesUnknownKeys):
    """One task's progress through a methodology.

    Shares the session record's store and, until BUG_061, its defect: an older
    build loading and saving this document deleted every field it did not
    declare. No field of this record was at risk when that was found, only
    because none had been added alongside the session's four — a fact about the
    calendar rather than about the code. So it takes the same round-trip
    contract, from the same place, rather than a second copy of the reasoning.
    """

    session_id: str
    methodology: str
    task: str = ""
    state: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    overrides: list[dict] = field(default_factory=list)
    started: float = 0.0
    finished: float | None = None
    #: The usage watermark at the moment this pipeline started (ADR-002 D3).
    #: Per-state spend is the difference between consecutive watermarks, so the
    #: FIRST state needs an earlier reading to subtract; without this it would
    #: have a duration and no spend. ``None`` where the watermark could not be
    #: observed, which is a fact the report renders rather than a zero.
    usage_at_start: dict | None = None
    #: The host session that baseline was counted against. A cumulative count
    #: is only commensurable with another taken from the same transcript, and
    #: the binding can move under a forced rebind — so the counter's identity
    #: is recorded with the reading rather than assumed from the session's
    #: binding at the time the report is rendered.
    usage_at_start_host: str | None = None
    #: Keys a document carried that this build does not declare. See
    #: `core.store.PreservesUnknownKeys`; never written under this name.
    _extra: dict[str, Any] = field(default_factory=dict)

    def state_entered(self) -> float | None:
        """When the current state began, derived rather than stored.

        ``started`` plus every elapsed since. Deriving it is what keeps a
        second clock time out of the record: the anchor is reconstructed from
        the durations that are already there.

        ``None`` once any entry lacks ``elapsed`` — a pipeline that ran before
        D3 cannot have its anchor reconstructed, and guessing would attribute
        every earlier state's time to whichever one happens to be current.
        """
        if not self.started:
            return None
        total = 0.0
        for entry in self.history:
            elapsed = entry.get("elapsed")
            if elapsed is None:
                return None
            total += float(elapsed)
        return self.started + total


class PipelineDomain:
    def __init__(
        self,
        manifest,
        store: JsonStore | None = None,
        watermarks: WatermarkSource | None = None,
        clock=time.time,
    ) -> None:
        self.manifest = manifest
        self.store = store or JsonStore(manifest.state_root)
        #: The manifest-default machine — the one a NEW pipeline starts under.
        #: A running pipeline is judged by :meth:`machine_for` instead: the
        #: methodology is pinned to the record at ``start``, so neither a
        #: ``--methodology`` override (AUD-02) nor a mid-pipeline manifest edit
        #: (AUD-03) can swap the gate set out from under a pipeline in flight.
        self.machine = load_pipeline_machine(manifest)
        #: Injected by the CLI (ADR-002 D2). ``None`` here is normal and
        #: harmless: transitions record ``usage: None`` and the report says so.
        self.watermarks = watermarks
        self.clock = clock

    def _reading(self, host_session_id: str | None) -> tuple[dict | None, str | None]:
        """Read the watermark and the counter it came from, or give up quietly.

        Telemetry may never be a reason a transition refuses. ADR-002 rejects
        gating on the report for the same reason it rejects gating teardown:
        the step that records what happened must not acquire new ways to fail.

        The reading and the host session travel together because a cumulative
        count means nothing without the transcript it was counted against. A
        forced rebind moves the binding mid-pipeline, and without the second
        half of this pair the difference between the readings either side is
        well-formed, meaningless, and indistinguishable from a measurement
        (BUG_014).
        """
        if self.watermarks is None or not host_session_id:
            return None, None
        try:
            usage = self.watermarks.watermark(host_session_id)
        except Exception:  # noqa: BLE001 - see the docstring
            return None, None
        if not isinstance(usage, Usage):
            return None, None
        return usage.to_dict(), host_session_id

    def _path(self, session_id: str) -> str:
        return f"pipelines/{session_id}.json"

    def machine_for(self, pipeline: Pipeline) -> Machine:
        """The machine this pipeline runs under — read from the RECORD.

        ``pipeline start --methodology`` used to swap the machine in memory
        only, so the very next invocation loaded the manifest default and asked
        it about a state it does not have. That wedged ``status``, ``gates``
        and ``advance``, and — through the hook's fail-open path — turned all
        enforcement off (AUD-02). Reading the persisted methodology also means
        editing ``methodology.pipeline`` mid-pipeline cannot silently swap a
        running pipeline's gate set (AUD-03); the change applies from the next
        ``start``.
        """
        if not pipeline.methodology or pipeline.methodology == self.machine.id:
            return self.machine
        return load_pipeline_machine(self.manifest, pipeline.methodology)

    # -- lookup --------------------------------------------------------

    def current(self, session_id: str) -> Pipeline | None:
        raw = self.store.read(self._path(session_id), {})
        current = raw.get("current") if isinstance(raw, dict) else None
        return Pipeline.from_dict(current) if current else None

    def require(self, session_id: str) -> Pipeline:
        pipeline = self.current(session_id)
        if pipeline is None:
            raise UsageError(
                "no pipeline is running for this session; start one with "
                "'invariant pipeline start --task \"<what you are doing>\"'"
            )
        return pipeline

    def history(self, session_id: str) -> list[dict]:
        raw = self.store.read(self._path(session_id), {})
        return list(raw.get("completed", [])) if isinstance(raw, dict) else []

    # -- lifecycle -----------------------------------------------------

    def start(
        self,
        session_id: str,
        methodology: str | None = None,
        task: str = "",
        host_session_id: str | None = None,
    ) -> Pipeline:
        existing = self.current(session_id)
        if existing:
            try:
                terminal = self.machine_for(existing).terminal
            except InvariantError:
                # The recorded machine no longer loads — a deleted project
                # machine file, say. Every other operation on that pipeline
                # honestly refuses, so starting a fresh one, which ARCHIVES
                # the old record rather than discarding it, is the one route
                # out that loses nothing.
                terminal = None
            if terminal is not None and existing.state not in terminal:
                raise UsageError(
                    f"a pipeline is already running in state {existing.state!r}; "
                    f"finish or abandon it first"
                )

        # An explicit override for this one task, without editing the manifest
        # — a chore in a TDD project is the motivating case. Resolved here and
        # PERSISTED on the record; nothing is swapped in memory, because the
        # in-memory swap died with this call and wedged every later one
        # (AUD-02), and mutating the shared manifest object was itself an
        # unobserved gate-set change (AUD-03).
        machine = (
            load_pipeline_machine(self.manifest, methodology)
            if methodology
            else self.machine
        )

        baseline, baseline_host = self._reading(host_session_id)
        pipeline = Pipeline(
            session_id=session_id,
            methodology=machine.id,
            task=task,
            state=machine.initial,
            started=self.clock(),
            usage_at_start=baseline,
            usage_at_start_host=baseline_host,
        )
        self._save(session_id, pipeline, archive=existing)
        return pipeline

    # -- gates ---------------------------------------------------------

    def gates_for(
        self, source: str, target: str | None, machine: Machine | None = None
    ) -> list[str]:
        """What ONE edge owes: the source's exit gates plus the target's entry gates.

        ``machine`` is the machine the states belong to — for a running
        pipeline, :meth:`machine_for`'s answer, not the manifest default.

        With no ``target`` this answers about *leaving* the state and nothing
        else, which since BUG_037 is a much smaller set than it used to be:
        the gates that permit an outcome now hang off that outcome, so a review
        state's own exit set is usually empty. Callers asking "what is owed
        here" want :meth:`gates_by_destination`, not this with ``None``.
        """
        machine = machine or self.machine
        if target and ABANDON_ROLE in machine.state(target).roles:
            # Giving up owes nothing that landing owes. Every exit gate on the
            # state being left exists to protect an OUTCOME, and abandonment is
            # the outcome where there is none to protect — the work is being
            # dropped. Holding it to them means the only supported way to stop
            # is to finish first, and what people actually do from there is
            # `--force`, which is the outcome this library treats as its own
            # defect (BUG_037). The destination's own entry gates still apply:
            # if a machine wants to demand something before abandoning, that is
            # where it says so.
            return list(machine.state(target).entry_gates)
        gates = list(machine.state(source).exit_gates) if source else []
        if target:
            gates += [g for g in machine.state(target).entry_gates if g not in gates]
        return gates

    def gates_by_destination(
        self, source: str, machine: Machine | None = None
    ) -> dict[str, list[str]]:
        """Every legal edge out of ``source``, and what each of them owes.

        The question `invariant context` and `invariant pipeline gates` are
        really asking. They used to ask :meth:`gates_for` with no target, which
        was a fair paraphrase while every gate hung off the state being left —
        one set applied to every edge, so naming the edge added nothing. That
        stopped being true when the gates moved to the outcomes they protect
        (BUG_037), and it was never quite true anyway: `reviewing` advertised
        four successors and refused two of them, and nothing in "gates owed
        here" said which two.

        Ordered as the machine declares its successors, so the forward edge
        reads first.
        """
        machine = machine or self.machine
        return {
            target: self.gates_for(source, target, machine)
            for target in machine.next_states(source)
        }

    def evaluate_gates(
        self,
        session_id: str,
        target: str | None = None,
        session: dict | None = None,
        cwd: Path | None = None,
    ) -> list[Evaluated]:
        """Verdicts for one edge, or for everything reachable from here.

        With a ``target`` this is the edge's own obligation, which is what a
        transition evaluates. Without one it is the UNION over every legal
        destination — because the alternative, after BUG_037 moved the gates
        onto the outcomes they protect, is that `pipeline gates` in a review
        state reports nothing owed right up until the advance refuses. A gate
        that only appears at the moment it blocks is the shape this library
        exists to remove. :meth:`evaluate_gates_by_destination` says which edge
        each verdict belongs to.
        """
        pipeline = self.require(session_id)
        machine = self.machine_for(pipeline)
        if target is None:
            gate_ids: list[str] = []
            for edge_gates in self.gates_by_destination(pipeline.state, machine).values():
                gate_ids += [g for g in edge_gates if g not in gate_ids]
        else:
            gate_ids = self.gates_for(pipeline.state, target, machine)
        ctx = RuleContext(
            manifest=self.manifest,
            cwd=Path(cwd or Path.cwd()),
            session=session,
            pipeline=pipeline.to_dict(),
        )
        return apply_acknowledgements(pipeline.to_dict(), evaluate(gate_ids, ctx))

    def evaluate_gates_by_destination(
        self,
        session_id: str,
        session: dict | None = None,
        cwd: Path | None = None,
    ) -> dict[str, list[Evaluated]]:
        """The same verdicts as :meth:`evaluate_gates`, attributed to their edges.

        Evaluated ONCE over the union and then distributed, rather than per
        destination: several edges share a gate, and a rule that shells out to
        git would otherwise run once per edge — and, worse, could answer
        differently between two of them and produce a report that contradicts
        itself.
        """
        pipeline = self.require(session_id)
        machine = self.machine_for(pipeline)
        verdicts = {
            v.rule_id: v
            for v in self.evaluate_gates(session_id, session=session, cwd=cwd)
        }
        return {
            target: [verdicts[g] for g in gates if g in verdicts]
            for target, gates in self.gates_by_destination(
                pipeline.state, machine
            ).items()
        }

    def acknowledge(self, session_id: str, rule_id: str, reason: str) -> Pipeline:
        """Record a deliberate decision to proceed against an advisory gate.

        Refused for a gate that stops work. Allowing it there would make
        ``block`` and ``warn`` the same level with different spelling, and the
        whole design rests on those being different: an enforced gate is dealt
        with, or overridden where the override is recorded.
        """
        if not reason or not reason.strip():
            raise UsageError(
                "acknowledging a gate requires --reason. An acknowledgement "
                "with no stated reason is a silencer, and a silencer is the "
                "thing this is designed not to be."
            )

        spec = get_rule(rule_id)
        if spec is None:
            raise UsageError(
                f"no rule {rule_id!r}; 'invariant rules list' shows them all"
            )

        level = self.manifest.rule_level(rule_id)
        if level in (Level.BLOCK, Level.ASK):
            try:
                machine = self.machine_for(self.require(session_id))
            except InvariantError:
                machine = self.machine
            raise UsageError(
                f"{rule_id} {level.value}s in this project and cannot be "
                f"acknowledged.\n"
                + _override_routes(rule_id, spec.events, machine)
            )
        if level is Level.OFF:
            raise UsageError(f"{rule_id} is off in this project; nothing to acknowledge")

        pipeline = self.require(session_id)
        pipeline.evidence.setdefault("acknowledged", {})[rule_id] = reason.strip()
        self._save(session_id, pipeline)
        return pipeline

    def advance(
        self,
        session_id: str,
        target: str,
        force: bool = False,
        reason: str | None = None,
        session: dict | None = None,
        cwd: Path | None = None,
    ) -> tuple[Pipeline, list[Evaluated]]:
        pipeline = self.require(session_id)
        machine = self.machine_for(pipeline)

        structural_override = False
        if pipeline.state != target:
            try:
                machine.require_edge(pipeline.state, target)
            except TransitionError:
                if not force:
                    raise
                structural_override = True

        verdicts = self.evaluate_gates(session_id, target, session=session, cwd=cwd)
        blocking = [v for v in verdicts if v.blocks]

        if blocking and not force:
            raise GateFailure(
                f"{len(blocking)} gate(s) block {pipeline.state!r} -> {target!r}",
                verdicts=verdicts,
            )

        if force and (blocking or structural_override):
            if not reason:
                raise UsageError(
                    "--force requires --reason: an override that records no "
                    "rationale is indistinguishable from the gate never having "
                    "run"
                )
            pipeline.overrides.append(
                {
                    "from": pipeline.state,
                    "to": target,
                    "reason": reason,
                    "structural": structural_override,
                    "gates": [v.rule_id for v in blocking],
                }
            )

        # ADR-002 D3. `elapsed` is derived from an anchor reconstructed out of
        # the durations already recorded, and the watermark is a pair of
        # counters — so per-state attribution costs no stored clock time, and
        # spend has no boundary problem: it is a subtraction of two counts
        # rather than an arbitrary assignment of a response to one side of a
        # transition.
        now = self.clock()
        entered = pipeline.state_entered()
        host_session_id = (session or {}).get("host_session_id")
        usage, usage_host = self._reading(host_session_id)
        pipeline.history.append(
            {
                "from": pipeline.state,
                "to": target,
                "reason": reason,
                "forced": bool(force),
                "elapsed": None if entered is None else max(0.0, now - entered),
                "usage": usage,
                "usage_host": usage_host,
            }
        )
        pipeline.state = target
        if target in machine.terminal:
            pipeline.finished = now
        self._save(session_id, pipeline)
        return pipeline, verdicts

    # -- evidence ------------------------------------------------------

    def record_docs_read(self, session_id: str, path: str) -> None:
        pipeline = self.current(session_id)
        if pipeline is None:
            return  # reading docs outside a pipeline is fine, just unrecorded
        read = pipeline.evidence.setdefault("docs_read", [])
        if path not in read:
            read.append(path)
            self._save(session_id, pipeline)

    def record_agent_run(
        self, session_id: str, agent_type: str, *, how: str = VIA_DISPATCH
    ) -> str:
        """Note that a subagent ran in this pipeline, and say what happened.

        Returns one of :data:`RECORDED`, :data:`ALREADY` or
        :data:`NO_PIPELINE`. The return value is the point. This used to be a
        bare ``return`` when there was no pipeline, defended as "dispatching an
        agent before starting one is allowed, it just cannot be evidence for a
        gate that does not exist yet". The permission is right and is kept —
        nothing here raises, and no dispatch is refused. What was wrong is the
        silence: ``agents.*_ran`` is block-level, so the caller learns its
        evidence went nowhere at the gate, at the end of the phase, with no way
        left to satisfy it (BUG_028). A caller that is told can say so.

        ``how`` records WHY this agent is here, because the two routes are not
        equally strong and a reader of the record cannot otherwise tell them
        apart. :data:`VIA_DISPATCH` means a parent asked for this agent and the
        request resolved to this session. :data:`VIA_ACTOR` means the host
        named this agent as the one making a call that landed in this
        pipeline — the agent's own work, not somebody's account of it. An actor
        reading is never downgraded to a dispatch reading.
        """
        pipeline = self.current(session_id)
        if pipeline is None:
            return NO_PIPELINE

        ran = pipeline.evidence.setdefault("agents_run", [])
        via = pipeline.evidence.setdefault("agents_run_how", {})
        changed = False
        if agent_type not in ran:
            ran.append(agent_type)
            changed = True
        if via.get(agent_type) not in (VIA_ACTOR, how):
            via[agent_type] = how
            changed = True
        if not changed:
            return ALREADY
        self._save(session_id, pipeline)
        return RECORDED

    def _fresh_bundle(self, session_id: str, kind: str) -> Path:
        """A per-run result-bundle path, cleared before use.

        Per SESSION and per KIND, never shared: two concurrent sessions writing
        one bundle would overwrite each other's measurements while both
        reported success. Cleared because xcodebuild refuses an existing path,
        and because a stale bundle read as a fresh one would republish the
        previous run's numbers.
        """
        bundle = Path(self.store.root) / "coverage" / f"{session_id}.{kind}.xcresult"
        bundle.parent.mkdir(parents=True, exist_ok=True)
        if bundle.exists():
            import shutil

            shutil.rmtree(bundle, ignore_errors=True)
        return bundle

    def run_coverage(self, session_id: str, cwd: Path | None = None) -> dict:
        """Run the configured coverage command and record what it measured.

        This is the missing half of ``coverage.meets_floor``. The rule, the
        manifest slot and every adapter's parser all existed; nothing ever
        called them, so the gate blocked on evidence no code path could
        produce and advised running "the configured coverage command" — which
        no command ever ran. A blocking gate that cannot be satisfied does not
        enforce a standard, it teaches people to reach for --force.
        """
        pipeline = self.require(session_id)
        root = Path(cwd or self.manifest.root)

        # A per-run path, supplied here rather than configured. Writing a
        # result-bundle path into a manifest would make it shared state between
        # concurrent sessions, and two runs would overwrite each other's
        # measurements while both reported success.
        bundle = self._fresh_bundle(session_id, "coverage")
        command = self.manifest.command_for("coverage", result_bundle=str(bundle))
        adapter = get_adapter(self.manifest.ecosystems[0])

        # The measurement and the reading of it are separate programs in some
        # ecosystems and one program in others. Where they are separate, they
        # are run separately — see _refuse_chained_coverage for what joining
        # them with a `;` cost, and why it cannot simply be tolerated.
        report_command: str | None = None
        if self.manifest.get("commands.coverage_report"):
            report_command = self.manifest.command_for(
                "coverage_report", result_bundle=str(bundle)
            )
            _refuse_chained_coverage(command, report_command, str(bundle))

        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(root),
            capture_output=True,
            text=True,
            env=_child_env(),
        )
        # The suite's own status, read from the command that RAN the suite.
        # When a report leg follows, this is the whole point of the split: the
        # exit code here is the compiler's and the test runner's, and nothing
        # a reporting tool does afterwards can overwrite it.
        outcome = adapter.read_run(
            proc.stdout, proc.stderr, proc.returncode, bundle=bundle
        )

        if report_command is None:
            measured = adapter.parse_coverage(f"{proc.stdout}\n{proc.stderr}")
            report_exit = proc.returncode
        else:
            report = subprocess.run(
                report_command,
                shell=True,
                cwd=str(root),
                capture_output=True,
                text=True,
                env=_child_env(),
            )
            measured = adapter.parse_coverage(f"{report.stdout}\n{report.stderr}")
            report_exit = report.returncode

        # A suite that never ran cannot have measured anything, and a report
        # produced from a build that fell over describes the previous state of
        # the world at best. `total is None` means this adapter does not count
        # tests, which is not the same as counting zero.
        if outcome.total == 0:
            raise UsageError(
                "the coverage run executed no tests, so its measurement "
                "describes nothing.\n"
                f"  command: {command}\n"
                f"  exit: {proc.returncode}\n"
                "  Usually a build or launch failure rather than a coverage "
                "problem — read the build output first."
            )

        if not measured:
            # Naming the leg matters. While the two were chained, this message
            # printed the whole chain and its LAST exit status, so a reader
            # diagnosing "no measurements" was shown a command whose test half
            # had in fact succeeded — and the failure was twice attributed to
            # the suite, and once to an imagined race, before anyone looked at
            # the reporting tool.
            raise UsageError(
                "the coverage run produced no per-file measurements.\n"
                f"  command: {report_command or command}\n"
                f"  exit: {report_exit}\n"
                + (
                    f"  The suite itself ran: executed={outcome.total} "
                    f"failed={outcome.failed}, exit {proc.returncode}. Only "
                    "the reporting leg came back empty.\n"
                    if report_command
                    else ""
                )
                + "  Recording an empty result would make the gate pass on a "
                "measurement that never happened, so nothing was recorded."
            )

        self.record_coverage(session_id, measured)

        # A coverage run IS a test run — it runs the suite. Not recording it
        # left `test.all_pass` reading the last `pipeline test` entry, which
        # could be an older, narrower, greener run, and answering about that
        # one as though it were current. A whole-suite run with 625 failures
        # sat beside a rule saying "the suite is green and the pass is
        # trustworthy", because the rule was never shown this run at all.
        self._record_test_run(session_id, "coverage", command, outcome)

        # The suite's state at the moment of measurement, kept with it. A file
        # at 100% measured on a run where twelve tests failed is a different
        # fact from the same number measured on a green run, and the record
        # should not flatten them into one.
        pipeline = self.require(session_id)
        pipeline.evidence["coverage_run"] = {
            # The resolved command, as test_runs already records. Without it,
            # a reader of this evidence cannot tell WHICH run produced the
            # numbers — scoped or whole-suite, which simulator, which flags —
            # and that ambiguity is not recoverable later. A peer hit exactly
            # that: unable to tell whether an annotation was missing because
            # nothing was recorded or because nothing needed annotating.
            "command": command,
            "executed": outcome.total,
            "failed": outcome.failed,
            "exit_code": proc.returncode,
            # Kept apart on purpose. One number used to stand for both, and
            # which one it stood for depended on which command the shell
            # happened to finish with — so the evidence could not answer "did
            # the suite fail, or did the reader fail?" at all.
            "report_command": report_command,
            "report_exit_code": report_exit,
            "suite_passed": outcome.passed,
            # A hazard on the measuring run travels with the measurement. Exit
            # 65 can mean whole suites never ran, which makes coverage
            # UNDERSTATED — and an understated number is still a number
            # somebody will act on. Annotating beats silently trusting, for the
            # same reason TestResult.trustworthy is narrower than passed.
            "hazards": [h.id for h in outcome.hazards],
            # Split at record time, because the reader of the evidence cannot
            # re-derive it: the adapter knows which way each hazard bends a
            # measurement, the rule does not.
            "overstating_hazards": [
                h.id for h in outcome.hazards if h.may_overstate
            ],
        }
        self._save(session_id, pipeline)

        return {
            "command": command,
            "exit_code": proc.returncode,
            "report_command": report_command,
            "report_exit_code": report_exit,
            "files": len(measured),
            "coverage": measured,
            "executed": outcome.total,
            "failed": outcome.failed,
            # These were missing, and their absence made describe_run silent
            # about the very thing it was added to announce: a console fallback
            # rendered identically to a bundle read, so a scraped count that
            # was wrong by 400x printed with no caveat at all.
            "failed_tests": outcome.failed_tests,
            "skipped": outcome.skipped,
            "source": outcome.source,
            "device": outcome.device,
            "note": outcome.note,
            "suite_passed": outcome.passed,
        }

    def record_coverage(self, session_id: str, coverage: dict[str, float]) -> None:
        pipeline = self.require(session_id)
        pipeline.evidence["coverage"] = dict(coverage)
        self._save(session_id, pipeline)

    def run_tests(
        self,
        session_id: str,
        phase: str | None = None,
        selectors: Sequence[str] | None = None,
        cwd: Path | None = None,
    ) -> dict:
        """Run the configured suite and record what actually happened.

        ``selectors`` is a list because a caller may legitimately ask for
        several. Whether several fit in one command is the adapter's answer;
        what to do when they do not is this domain's, and the answer is to
        refuse. A refusal costs the caller one retry, and the alternative —
        running a subset — costs them the belief that the scope was covered
        (BUG_031).
        """
        pipeline = self.require(session_id)
        selectors = [s for s in (selectors or ()) if s]
        adapter = get_adapter(self.manifest.ecosystems[0])
        command_key = "test_one" if selectors else "test"
        bundle = self._fresh_bundle(session_id, "test")
        extra = {"result_bundle": str(bundle)}
        if selectors:
            joined = adapter.join_selectors(selectors)
            if joined is None:
                raise UsageError(
                    f"the {adapter.id!r} adapter takes one selector at a time, "
                    f"and {len(selectors)} were given.\n"
                    + "".join(f"  - {s}\n" for s in selectors)
                    + "  Run them one per invocation. Nothing was run and "
                    "nothing was recorded: a run covering part of the requested "
                    "scope would be read by test.all_pass as the state of the "
                    "whole suite."
                )
            extra["selector"] = joined
        command = self.manifest.command_for(command_key, **extra)

        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd or self.manifest.root),
            capture_output=True,
            text=True,
            env=_child_env(),
        )
        result = adapter.read_run(
            proc.stdout, proc.stderr, proc.returncode, bundle=bundle
        )
        phase = phase or pipeline.state
        result, read_as_red = self._read_red_phase_run(
            pipeline, phase, adapter, result, selectors, cwd or self.manifest.root
        )

        return self._record_test_run(
            session_id, phase, command, result, read_as_red=read_as_red
        )

    def _read_red_phase_run(
        self, pipeline, phase, adapter, result, selectors, root
    ) -> tuple[Any, bool]:
        """In the red phase, a strict expected failure that fails is red.

        The committed form of a repro is wrapped in a strict expected-failure
        marker, and such a run exits clean: read on exit status alone it was a
        PASS, so ``test.new_test_fails`` could not be satisfied until the fixer
        removed the wrapper (BUG_066). Here, and only in the phase the machine
        names as its proof of red, a clean run whose only outcome is expected
        failures the adapter confirms are strict is recorded as failing, those
        failures counted — ``_why_not_proof`` asks for a parsed, non-zero
        failure count, and flipping ``passed`` alone would not supply one.

        Everywhere else the exit status stands. In a green run a strict
        expected failure still failing is the tripwire holding, not a failure,
        and reading it as one would block ``test.all_pass`` on every suite
        carrying a committed repro. The exit code is recorded unchanged either
        way, and ``read_as_red`` says which reading was taken.

        A selector is required: a whole-suite run cannot say WHICH test
        xfailed, and every other committed repro in the suite is a strict
        expected failure too.
        """
        expected = result.expected_failures
        if not expected or not result.passed or result.failed:
            return result, False
        try:
            red_phase = self.machine_for(pipeline).red_phase or "red"
        except InvariantError:
            return result, False
        if phase != red_phase:
            return result, False

        strict = (
            adapter.expected_failures_are_strict(Path(root), selectors)
            if selectors
            else None
        )
        if strict is not True:
            why = (
                "the run named no selector, so which test failed cannot be told"
                if not selectors
                else "the adapter could not confirm its marker is strict"
            )
            note = (
                f"{expected} expected failure(s) ran, but {why}, so the run is "
                "recorded as it exited — mark the repro strict "
                "(pytest: xfail(strict=True)) and name it with --selector, or "
                "record the red run with the wrapper off"
            )
            return replace(result, note=_joined(result.note, note)), False

        note = (
            f"{expected} strict expected failure(s) read as red: the wrapped "
            "test failed as intended, and the run exited 0 because of the marker"
        )
        return (
            replace(result, passed=False, failed=expected, note=_joined(result.note, note)),
            True,
        )

    def _record_test_run(
        self, session_id: str, phase: str, command: str, result, read_as_red=False
    ):
        """Append one run to the evidence every test rule reads.

        Shared so that every path which runs the suite lands here. A path that
        ran the suite and recorded it somewhere else left the test rules
        answering about a different run than the one that just happened.
        """
        pipeline = self.require(session_id)
        record = {
            "phase": phase,
            # The RESOLVED command, placeholders substituted. A discovered
            # value that picked the wrong thing — another session's simulator,
            # say — is otherwise invisible: nothing in the output names what
            # was chosen, so a run against the wrong device is indistinguishable
            # from a run against the right one.
            "command": command,
            "passed": result.passed,
            "exit_code": result.exit_code,
            "executed": result.total,
            "failed": result.failed,
            "failed_tests": result.failed_tests,
            # Where the counts came from, and what they ran on. Both were
            # unanswerable from the record until now, and both mattered: one
            # session's numbers came from a peer's simulator, and another's
            # came from a console layout that under-reported by 400x.
            "source": result.source,
            "device": result.device,
            "skipped": result.skipped,
            # BUG_066. `read_as_red` marks the one case where `passed` is not
            # the exit status: a strict expected failure counted as red in the
            # machine's red phase. `exit_code` above stays the process's own.
            "expected_failures": result.expected_failures,
            "read_as_red": read_as_red,
            "hazards": [h.id for h in result.hazards],
            "note": result.note,
        }
        pipeline.evidence.setdefault("test_runs", []).append(record)
        self._save(session_id, pipeline)
        return record

    # -- internals -----------------------------------------------------

    def _save(
        self, session_id: str, pipeline: Pipeline, archive: Pipeline | None = None
    ) -> None:
        self.store.ensure_root()
        raw = self.store.read(self._path(session_id), {}) or {}
        completed = list(raw.get("completed", []))
        if archive is not None:
            completed.append(archive.to_dict())
        self.store.write(
            self._path(session_id),
            {"current": pipeline.to_dict(), "completed": completed},
        )


def _override_routes(rule_id: str, events: tuple[str, ...], machine: Machine) -> str:
    """The commands that can actually pass THIS rule, and only those.

    This message is the one place in the library where overriding a gate that
    stops work is discussed, and it used to name `pipeline advance --force`
    unconditionally. For a rule that fires on a TOOL CALL there is no
    transition to force — the block happened at `pre-tool-use` or at `stop` —
    so the single piece of advice on offer pointed at a command that could not
    help, which is worse than saying there is no override (AUTH-01).

    Both halves are checked against what is actually true here rather than
    assumed. A rule can be both — `privacy.clean` fires on an edit AND guards
    this machine's `reviewing` exit — and a rule can be neither: `docs.updated`
    guards a state of `spec-first` and of no other shipped methodology, so in a
    `tdd` project the advice to force a transition names a transition that does
    not exist. Where neither route is real the message says so, which is the
    one answer the old text could never give.
    """
    routes = []
    if events:
        routes.append(
            f"    invariant override {rule_id} --reason '<why>'\n"
            f"      (it fires on {', '.join(events)}; one call, then it is spent)"
        )
    if rule_id in machine.gate_ids():
        routes.append(
            "    invariant pipeline advance <state> --force --reason '<why>'\n"
            f"      (it gates a state of the {machine.id!r} pipeline)"
        )
    if not routes:
        return (
            f"  Nothing can override it here: it fires at no host event and "
            f"gates no state of\n  the {machine.id!r} pipeline. Satisfy it, or "
            f"change its level in the manifest."
        )
    return (
        "  Satisfy it, or record a deliberate override — where it is RECORDED:\n"
        + "\n".join(routes)
    )


def _joined(note: str, extra: str) -> str:
    return f"{note}; {extra}" if note else extra


def describe_run(record: dict) -> str:
    """One line naming each number's UNIT.

    `executed=14 failed=6` invites the reader to look for six failing tests
    among fourteen; there were two, and the six were assertions. Two numbers in
    one line counted in different things is the same species of defect as a
    number that lies outright — it just makes the reader do the lying.
    """
    executed = record.get("executed")
    parts = [f"{executed if executed is not None else '?'} test(s) executed"]

    failed_tests = record.get("failed_tests")
    failed = record.get("failed")
    if failed is None:
        parts.append("failure count unknown")
    elif failed_tests is not None and failed_tests != failed:
        parts.append(f"{failed} assertion(s) failed in {failed_tests} test(s)")
    elif failed:
        parts.append(f"{failed} failure(s)")
    else:
        parts.append("no failures")

    if record.get("read_as_red"):
        parts.append(
            f"{record.get('expected_failures')} strict expected failure(s) read as red"
        )
    elif record.get("expected_failures"):
        parts.append(f"{record['expected_failures']} expected failure(s)")
    if record.get("skipped"):
        parts.append(f"{record['skipped']} skipped")
    # Where the number came from, because a console fallback and a bundle read
    # produce the same-looking line and are not equally trustworthy.
    if record.get("source") == "console":
        parts.append("counts scraped from console output")
    if record.get("device"):
        parts.append(f"on {record['device']}")
    return ", ".join(parts)


def summarise(verdicts: list[Evaluated]) -> str:
    """A short, honest rendering: what blocked, what warned, what was inert."""
    if not verdicts:
        return "no gates on this transition"
    blocked = [v for v in verdicts if v.blocks]
    warned = [v for v in verdicts if v.warns]
    skipped = [v for v in verdicts if v.verdict.skipped]
    passed = [v for v in verdicts if v.verdict.ok and not v.verdict.skipped]
    parts = [f"{len(passed)} passed"]
    if blocked:
        parts.append(f"{len(blocked)} blocking")
    if warned:
        parts.append(f"{len(warned)} warning")
    if skipped:
        # Reported separately from passes on purpose: a gate that did not
        # apply has not vouched for anything. The blocking ones are named
        # within that count because "an advisory gate did not apply" and "a
        # gate this project enforces did not apply" are not the same news.
        inert_blocking = [v for v in skipped if v.skipped_blocking]
        note = f"{len(skipped)} not applicable"
        if inert_blocking:
            note += f" ({len(inert_blocking)} blocking)"
        parts.append(note)
    return ", ".join(parts)
