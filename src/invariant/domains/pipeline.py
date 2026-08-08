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
from ..core.rules import Evaluated, RuleContext, evaluate
from ..core.store import JsonStore


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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Pipeline":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in raw.items() if k in known})


class PipelineDomain:
    def __init__(self, manifest, store: JsonStore | None = None) -> None:
        self.manifest = manifest
        self.store = store or JsonStore(manifest.state_root)
        self.machine = load_pipeline_machine(manifest)

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
        self, session_id: str, methodology: str | None = None, task: str = ""
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

        pipeline = Pipeline(
            session_id=session_id,
            methodology=self.machine.id,
            task=task,
            state=self.machine.initial,
            started=time.time(),
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
        return evaluate(gate_ids, ctx)

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

        pipeline.history.append(
            {"from": pipeline.state, "to": target, "reason": reason, "forced": bool(force)}
        )
        pipeline.state = target
        if target in self.machine.terminal:
            pipeline.finished = time.time()
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
        command = self.manifest.command_for(
            command_key, **({"selector": selector} if selector else {})
        )
        adapter = get_adapter(self.manifest.ecosystems[0])

        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd or self.manifest.root),
            capture_output=True,
            text=True,
        )
        result = adapter.parse_test_output(proc.stdout, proc.stderr, proc.returncode)

        record = {
            "phase": phase or pipeline.state,
            "command": command,
            "passed": result.passed,
            "exit_code": result.exit_code,
            "executed": result.total,
            "failed": result.failed,
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
