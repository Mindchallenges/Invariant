"""The pipeline domain — one task's progress through the configured methodology.

The evidence dictionary is the point of this module. Gates do not re-derive
whether the suite passed or whether the docs were read; they read what was
recorded at the moment those things actually happened. That is the difference
between a gate that verifies and a gate that can be satisfied by accident.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..adapters import get_adapter
from ..core.errors import GateFailure, TransitionError, UsageError
from ..core.machines import load_pipeline_machine
from ..core.manifest import Level
from ..core.rules import Evaluated, RuleContext, Verdict, evaluate, get_rule
from ..core.stats import Usage, WatermarkSource
from ..core.store import JsonStore


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


@dataclass
class Pipeline:
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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Pipeline":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in known})

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
        if existing and existing.state not in self.machine.terminal:
            raise UsageError(
                f"a pipeline is already running in state {existing.state!r}; "
                f"finish or abandon it first"
            )
        if methodology and methodology != self.manifest.get("methodology.pipeline"):
            # An explicit override for this one task, without editing the
            # manifest — a chore in a TDD project is the motivating case.
            from ..core.machines import load_pipeline_machine as _load

            self.manifest.data["methodology"]["pipeline"] = methodology
            self.machine = _load(self.manifest)

        baseline, baseline_host = self._reading(host_session_id)
        pipeline = Pipeline(
            session_id=session_id,
            methodology=self.machine.id,
            task=task,
            state=self.machine.initial,
            started=self.clock(),
            usage_at_start=baseline,
            usage_at_start_host=baseline_host,
        )
        self._save(session_id, pipeline, archive=existing)
        return pipeline

    # -- gates ---------------------------------------------------------

    def gates_for(self, source: str, target: str | None) -> list[str]:
        """Exit gates of the state being left, plus entry gates of the one entered."""
        gates = list(self.machine.state(source).exit_gates) if source else []
        if target:
            gates += [g for g in self.machine.state(target).entry_gates if g not in gates]
        return gates

    def evaluate_gates(
        self,
        session_id: str,
        target: str | None = None,
        session: dict | None = None,
        cwd: Path | None = None,
    ) -> list[Evaluated]:
        pipeline = self.require(session_id)
        gate_ids = self.gates_for(pipeline.state, target)
        ctx = RuleContext(
            manifest=self.manifest,
            cwd=Path(cwd or Path.cwd()),
            session=session,
            pipeline=pipeline.to_dict(),
        )
        return self._apply_acknowledgements(pipeline, evaluate(gate_ids, ctx))

    def _apply_acknowledgements(
        self, pipeline: Pipeline, verdicts: list[Evaluated]
    ) -> list[Evaluated]:
        """Turn an acknowledged advisory failure into a recorded skip.

        Not a silencer. The reason travels with the verdict and shows in
        ``pipeline status``, so "considered and declined" stops being
        indistinguishable from "never read" — which is the real defect a
        repeating warning has, more than the repetition is.
        """
        acknowledged = pipeline.evidence.get("acknowledged") or {}
        if not acknowledged:
            return verdicts

        out: list[Evaluated] = []
        for evaluated in verdicts:
            reason = acknowledged.get(evaluated.rule_id)
            if (
                reason
                and not evaluated.verdict.ok
                and evaluated.level is not Level.BLOCK
            ):
                out.append(
                    Evaluated(
                        evaluated.rule_id,
                        evaluated.level,
                        Verdict.na(f"acknowledged — {reason}"),
                    )
                )
            else:
                out.append(evaluated)
        return out

    def acknowledge(self, session_id: str, rule_id: str, reason: str) -> Pipeline:
        """Record a deliberate decision to proceed against an advisory gate.

        Refused for blocking gates. Allowing it there would make ``block`` and
        ``warn`` the same level with different spelling, and the whole design
        rests on those being different: a blocking gate is dealt with, or
        overridden on the transition itself where the override is recorded
        against that transition rather than standing for the rest of the run.
        """
        if not reason or not reason.strip():
            raise UsageError(
                "acknowledging a gate requires --reason. An acknowledgement "
                "with no stated reason is a silencer, and a silencer is the "
                "thing this is designed not to be."
            )

        if get_rule(rule_id) is None:
            raise UsageError(
                f"no rule {rule_id!r}; 'invariant rules list' shows them all"
            )

        level = self.manifest.rule_level(rule_id)
        if level is Level.BLOCK:
            raise UsageError(
                f"{rule_id} blocks in this project and cannot be acknowledged.\n"
                f"  Satisfy it, or record a deliberate override on the "
                f"transition itself:\n"
                f"    invariant pipeline advance <state> --force --reason '<why>'"
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

        structural_override = False
        if pipeline.state != target:
            try:
                self.machine.require_edge(pipeline.state, target)
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
        if target in self.machine.terminal:
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

    def record_agent_run(self, session_id: str, agent_type: str) -> None:
        """Note that a subagent was dispatched in this pipeline.

        Unrecorded outside a pipeline, like a docs read: dispatching an agent
        before starting one is allowed, it just cannot be evidence for a gate
        that does not exist yet.
        """
        pipeline = self.current(session_id)
        if pipeline is None:
            return
        ran = pipeline.evidence.setdefault("agents_run", [])
        if agent_type not in ran:
            ran.append(agent_type)
            self._save(session_id, pipeline)

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
            command, shell=True, cwd=str(root), capture_output=True, text=True
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
        selector: str | None = None,
        cwd: Path | None = None,
    ) -> dict:
        """Run the configured suite and record what actually happened."""
        pipeline = self.require(session_id)
        command_key = "test_one" if selector else "test"
        bundle = self._fresh_bundle(session_id, "test")
        extra = {"result_bundle": str(bundle)}
        if selector:
            extra["selector"] = selector
        command = self.manifest.command_for(command_key, **extra)
        adapter = get_adapter(self.manifest.ecosystems[0])

        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd or self.manifest.root),
            capture_output=True,
            text=True,
        )
        result = adapter.read_run(
            proc.stdout, proc.stderr, proc.returncode, bundle=bundle
        )

        return self._record_test_run(
            session_id, phase or pipeline.state, command, result
        )

    def _record_test_run(self, session_id: str, phase: str, command: str, result):
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
        # apply has not vouched for anything.
        parts.append(f"{len(skipped)} not applicable")
    return ", ".join(parts)
