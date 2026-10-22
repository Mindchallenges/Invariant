"""The gate cache: what a session's pipeline owes, for the recommendation only.

A row the scan reads says a gate refused only when a refusal was actually met
(``blocked`` in the attention sidecar, ADR-007 D8). What it cannot say is
WHICH gates are owed now and what each one wants, and that is the detail that
turns "run ``pipeline gates``" into a recommendation a person can act on from
the list. :class:`GateCache` answers it by running
``PipelineDomain.evaluate_gates_by_destination`` — the evaluation ``pipeline
gates`` prints — and keeps the answer in memory.

Four rules keep it from costing a project anything:

* **Never a write.** The domain is built on an
  :class:`~invariant.core.store.InertStore` over the state root, so whatever a
  rule path touches is discarded, and the manifest is the session worktree's
  own, the one ``pipeline gates`` run there would load.
* **Never while a turn runs.** The rules shell out to git in the session's
  worktree; a host mid-turn is using that worktree, and its own git commands
  must not meet ours. A session is evaluated when its turn has ended, or, with
  no turn known (or its host gone), after two quiet minutes.
* **Only when something moved.** The fingerprint is the session's state and
  heartbeat, the pipeline's state and transition count, and the record's write
  time. An idle session is evaluated once per fingerprint, not once per tick.
* **Bounded.** Each evaluation runs in a daemon thread, at most
  ``max_concurrent`` at a time — a worker that timed out still holds its slot
  until it really ends — and a result later than ``timeout`` is dropped. A
  timeout or an error keeps the previous result, marked as such, and that
  fingerprint is not tried again.

The answer feeds :func:`~invariant.oracle.recommend.recommend` and nothing
else. A gate evaluated here never raises an alert: an alert is a refusal
somebody met, and an evaluation is only a forecast of one (ADR-007 D8, D16).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core.manifest import MANIFEST_RELPATH, Manifest
from ..core.rules import Evaluated
from ..core.store import InertStore
from ..domains.pipeline import PipelineDomain
from ..domains.session import SessionDomain
from .scan import ROW_SESSION, ProjectSnapshot, Row

#: Seconds one evaluation may take before its result is given up on.
GATE_TIMEOUT_SECONDS = 10.0

#: Evaluations running at once, across every project.
MAX_CONCURRENT = 2

#: With no turn known, a session this quiet is idle.
IDLE_SECONDS = 120.0

_TURN_RUNNING = "running"
_TURN_ENDED = "ended"

GateKey = tuple[str, str]
Fingerprint = tuple[Any, ...]
Evaluator = Callable[[Row], Mapping[str, list[Evaluated]]]


@dataclass(frozen=True)
class OwedGate:
    """One gate the pipeline owes, and every edge out of its state that owes it."""

    rule_id: str
    level: str
    #: The verdict's first line: what is owed, in the rule's own words.
    message: str
    #: Work stops at it (``block`` or ``ask``); otherwise it is advisory.
    blocks: bool
    to: tuple[str, ...] = ()
    fix: str = ""

    def to_dict(self) -> dict[str, Any]:
        body = asdict(self)
        body["to"] = list(self.to)
        return body


@dataclass(frozen=True)
class GateReport:
    """What one session's pipeline owed when it was last evaluated."""

    pipeline_state: str
    owed: tuple[OwedGate, ...] = ()
    #: The session or pipeline has moved since; this is the earlier answer.
    stale: bool = False
    #: The evaluation of the current fingerprint ran out of time.
    timed_out: bool = False
    #: The evaluation of the current fingerprint failed, and why.
    error: str | None = None

    @property
    def blocking(self) -> tuple[OwedGate, ...]:
        return tuple(g for g in self.owed if g.blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pipeline_state": self.pipeline_state,
            "owed": [g.to_dict() for g in self.owed],
            "stale": self.stale,
            "timed_out": self.timed_out,
            "error": self.error,
        }


def gate_key(row: Row) -> GateKey:
    return (row.project, row.session_id or "")


def fingerprint(row: Row) -> Fingerprint:
    """The facts whose change means the gates may answer differently."""
    pipeline = row.pipeline
    return (
        row.state,
        row.heartbeat,
        pipeline.state if pipeline else None,
        pipeline.transitions if pipeline else None,
        pipeline.written_ns if pipeline else None,
    )


def _workdir(row: Row) -> Path | None:
    """Where ``pipeline gates`` would run for the session; ``None`` if it is gone."""
    if row.stranded:
        return None
    path = Path(row.worktree or row.root)
    return path if path.is_dir() else None


def eligible(row: Row, now: float, idle_seconds: float = IDLE_SECONDS) -> bool:
    """Whether ``row`` may be evaluated now: a running pipeline, idle, on disk."""
    if row.kind != ROW_SESSION or row.pipeline is None or row.pipeline.terminal:
        return False
    if _workdir(row) is None:
        return False
    host = row.current_host
    record = host.attention if host and isinstance(host.attention, dict) else {}
    turn = record.get("turn")
    if turn == _TURN_RUNNING and host.alive is not False:
        return False
    if turn == _TURN_ENDED:
        return True
    return row.last_activity is None or now - row.last_activity >= idle_seconds


def summarise(by_destination: Mapping[str, list[Evaluated]], state: str) -> GateReport:
    """Every gate not met, once, with the edges that owe it; blocking ones first."""
    owed: dict[str, OwedGate] = {}
    for target, verdicts in by_destination.items():
        for evaluated in verdicts:
            if evaluated.verdict.ok or evaluated.verdict.skipped:
                continue
            known = owed.get(evaluated.rule_id)
            if known is not None:
                owed[evaluated.rule_id] = replace(known, to=(*known.to, target))
                continue
            message = (evaluated.verdict.message or "").strip().splitlines()
            owed[evaluated.rule_id] = OwedGate(
                rule_id=evaluated.rule_id,
                level=evaluated.level.value,
                message=message[0] if message else "",
                blocks=evaluated.blocks,
                to=(target,),
                fix=evaluated.verdict.fixes[0] if evaluated.verdict.fixes else "",
            )
    ordered = sorted(owed.values(), key=lambda g: not g.blocks)
    return GateReport(pipeline_state=state, owed=tuple(ordered))


def evaluate_row(row: Row) -> Mapping[str, list[Evaluated]]:
    """``pipeline gates`` for the session ``row`` names, as its worktree sees it.

    Over an inert store on the project's state root, so nothing is written, and
    with the worktree's own manifest, because the rules resolve the paths they
    compare against the manifest's root.
    """
    workdir = _workdir(row)
    if workdir is None:
        raise FileNotFoundError(f"the session's worktree is gone: {row.worktree}")
    manifest_root = workdir if (workdir / MANIFEST_RELPATH).is_file() else Path(row.root)
    manifest = Manifest.load(manifest_root)
    store = InertStore(Path(row.project))
    session = SessionDomain(manifest, store=store).get(row.session_id)
    return PipelineDomain(manifest, store=store).evaluate_gates_by_destination(
        row.session_id,
        session=session.to_dict() if session is not None else None,
        cwd=workdir,
    )


@dataclass
class _Job:
    key: GateKey
    fingerprint: Fingerprint
    state: str
    started: float
    done: threading.Event = field(default_factory=threading.Event)
    report: GateReport | None = None
    error: str | None = None


@dataclass(frozen=True)
class _Failure:
    """A fingerprint given up on: it ran out of time, or its evaluation raised."""

    fingerprint: Fingerprint
    timed_out: bool = False
    error: str | None = None


@dataclass
class _Settled:
    """The last answer for a key, and the fingerprint it answered."""

    fingerprint: Fingerprint
    report: GateReport


class GateCache:
    """Gate verdicts per session, evaluated off the tick and kept across ticks."""

    def __init__(
        self,
        evaluate: Evaluator = evaluate_row,
        timeout: float = GATE_TIMEOUT_SECONDS,
        max_concurrent: int = MAX_CONCURRENT,
        idle_seconds: float = IDLE_SECONDS,
    ) -> None:
        if not timeout > 0:
            raise ValueError(f"the timeout must be positive, not {timeout!r}")
        if max_concurrent < 1:
            raise ValueError(f"at least one evaluation must be allowed, not {max_concurrent!r}")
        self.evaluate = evaluate
        self.timeout = timeout
        self.max_concurrent = max_concurrent
        self.idle_seconds = idle_seconds
        self._cond = threading.Condition()
        self._settled: dict[GateKey, _Settled] = {}
        #: The fingerprint whose evaluation timed out or failed, and how.
        self._failed: dict[GateKey, _Failure] = {}
        self._running: dict[GateKey, _Job] = {}
        #: Timed-out workers still alive: each holds its slot until it ends.
        self._abandoned: list[_Job] = []

    def refresh(
        self, snapshots: list[ProjectSnapshot], now: float, wait: float = 0.0
    ) -> dict[GateKey, GateReport]:
        """Start what is owed an evaluation, wait up to ``wait`` seconds, report.

        ``wait=0`` is the watcher's tick: whatever finishes after it is
        collected by the next one. ``oracle list`` waits for the timeout.
        """
        rows = {
            gate_key(row): row
            for snapshot in snapshots
            for row in snapshot.rows
            if row.kind == ROW_SESSION and row.session_id
        }
        deadline = time.monotonic() + max(0.0, wait)
        with self._cond:
            self._forget(rows, {s.project.key for s in snapshots})
            self._reap()
            queue = [
                (key, row, fingerprint(row))
                for key, row in rows.items()
                if eligible(row, now, self.idle_seconds) and not self._known(key, fingerprint(row))
            ]
            while True:
                self._reap()
                queue = self._start(queue)
                waiting = queue or any(key in rows for key in self._running)
                remaining = deadline - time.monotonic()
                if not waiting or remaining <= 0:
                    break
                self._cond.wait(min(remaining, self._next_expiry()))
            self._reap()
            return self._reports(rows)

    # -- under the lock -------------------------------------------------

    def _known(self, key: GateKey, fp: Fingerprint) -> bool:
        """Answered, given up on, or being evaluated: nothing to start."""
        settled = self._settled.get(key)
        failed = self._failed.get(key)
        return (
            (settled is not None and settled.fingerprint == fp)
            or (failed is not None and failed.fingerprint == fp)
            or key in self._running
        )

    def _forget(self, rows: Mapping[GateKey, Row], projects: set[str]) -> None:
        """Drop the answers for sessions no longer live in a project just scanned."""
        for table in (self._settled, self._failed):
            for key in [k for k in table if k[0] in projects and k not in rows]:
                del table[key]

    def _slots(self) -> int:
        self._abandoned = [job for job in self._abandoned if not job.done.is_set()]
        return self.max_concurrent - len(self._running) - len(self._abandoned)

    def _start(self, queue: list[tuple[GateKey, Row, Fingerprint]]) -> list:
        while queue and self._slots() > 0:
            key, row, fp = queue.pop(0)
            job = _Job(key, fp, row.pipeline.state, time.monotonic())
            self._running[key] = job
            threading.Thread(
                target=self._work, args=(job, row), name=f"oracle-gates-{key[1]}", daemon=True
            ).start()
        return queue

    def _work(self, job: _Job, row: Row) -> None:
        try:
            job.report = summarise(self.evaluate(row), job.state)
        except Exception as exc:  # noqa: BLE001 - one session's rules must not stop the rest
            job.error = str(exc) or type(exc).__name__
        finally:
            with self._cond:
                job.done.set()
                self._cond.notify_all()

    def _reap(self) -> None:
        now = time.monotonic()
        for key, job in list(self._running.items()):
            if job.done.is_set():
                del self._running[key]
                if job.report is not None:
                    self._settled[key] = _Settled(job.fingerprint, job.report)
                    self._failed.pop(key, None)
                else:
                    self._failed[key] = _Failure(job.fingerprint, error=job.error or "failed")
            elif now - job.started >= self.timeout:
                del self._running[key]
                self._failed[key] = _Failure(job.fingerprint, timed_out=True)
                self._abandoned.append(job)

    def _next_expiry(self) -> float:
        if not self._running:
            return self.timeout
        first = min(job.started for job in self._running.values())
        return max(0.001, first + self.timeout - time.monotonic())

    def _reports(self, rows: Mapping[GateKey, Row]) -> dict[GateKey, GateReport]:
        reports: dict[GateKey, GateReport] = {}
        for key, row in rows.items():
            fp = fingerprint(row)
            settled = self._settled.get(key)
            failed = self._failed.get(key)
            failure = failed if failed is not None and failed.fingerprint == fp else None
            if settled is None and failure is None:
                continue
            base = settled.report if settled is not None else GateReport(
                pipeline_state=row.pipeline.state if row.pipeline else ""
            )
            reports[key] = replace(
                base,
                stale=settled is not None and settled.fingerprint != fp,
                timed_out=failure is not None and failure.timed_out,
                error=failure.error if failure is not None else None,
            )
        return reports
