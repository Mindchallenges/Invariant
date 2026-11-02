"""Every read-modify-write of a pipeline record holds the store's lock (BUG_107).

tests/test_bug_107_repro.py proves the two losses it was filed on: a second
writer's evidence, and an advance undone. This file holds the rest of the
contract. Every writer, enumerated, holds the lock across its load and its save,
and nothing slow runs under it. The two fail-open cases cannot return. An advance
never passes on a verdict about a record that has since moved. And a lock that
cannot be had costs each kind of caller what that caller says it costs.
"""

from __future__ import annotations

import fcntl
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_audit_record import (
    MODEL_A,
    RUN,
    TOKEN,
    VERDICT_CMD,
    VERDICT_OUT,
    acting,
    post,
)
from test_bug_107_repro import BOUND_SECONDS, ParkingStore
from test_cli import run
from test_domains import git_project
from test_host_claude_code import FakeCtx

from invariant.cli.main import Context
from invariant.core.errors import (
    GateFailure,
    LockTimeout,
    StateError,
    TransitionError,
    UsageError,
)
from invariant.core.rules import VerdictRefused
from invariant.core.store import LOCK_TIMEOUT_SECONDS, JsonStore
from invariant.domains import pipeline as pipeline_module
from invariant.domains.pipeline import ADVANCE_ATTEMPTS, PipelineDomain
from invariant.domains.session import Resolution, SessionDomain
from invariant.hosts import claude_code
from invariant.hosts.claude_code import HOOK_LOCK_SECONDS, HOOK_TIMEOUT_SECONDS

SID = "s1"
DOC = "docs/architecture/overview.md"
#: Far longer than a thread's write takes, so a writer the lock did not stop has
#: finished by the time the parked writer is released.
BLOCKED_SECONDS = 0.4
#: A held lock gives up this fast, so a timeout test costs no real wait.
SHORT = 0.05


def lock_is_free(root: Path, sid: str = SID) -> bool:
    """Whether the record's lock could be taken now, by a handle of its own."""
    path = Path(root) / "locks" / f"{sid}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return True


@contextmanager
def wedged(root: Path, sid: str = SID):
    """Hold the record's lock, as a live process that never lets go would."""
    path = Path(root) / "locks" / f"{sid}.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class WatchingStore(JsonStore):
    """Notes, for each write of the record, whether its load and it were locked.

    The load is the read before the last one. The last is `_save`'s own read of
    the archive list, which is always beside the write, so judging by it alone
    passes a writer that loaded its copy before it took the lock: the defect.
    """

    def __init__(self, root: Path, rel: str) -> None:
        super().__init__(root)
        self.rel = rel
        self.reads: list[bool] = []
        self.writes: list[tuple[bool | None, bool]] = []

    def read(self, rel, default=None):
        if rel == self.rel:
            self.reads.append(not lock_is_free(self.root))
        return super().read(rel, default)

    def write(self, rel, data):
        if rel == self.rel:
            load = self.reads[-2] if len(self.reads) > 1 else None
            self.writes.append((load, not lock_is_free(self.root)))
        super().write(rel, data)


def verdict(status: str, state: str = "designing", token: str = TOKEN) -> dict:
    return {"rule": "a.rule", "status": status, "state": state, "token": token}


#: Every writer of the record, and a call that makes it write. Adding a writer
#: means adding it here: the source scan below fails until it is.
WRITERS = {
    "start": lambda p, m: p.start("s2-unused", task="t") and p.start(SID, task="again"),
    "acknowledge": lambda p, m: p.acknowledge(SID, "test.new_test_fails", "because"),
    "advance": lambda p, m: p.advance(SID, "abandoned", cwd=m.root),
    "record_docs_read": lambda p, m: p.record_docs_read(SID, "new.md"),
    "record_agent_run": lambda p, m: p.record_agent_run(SID, "agent"),
    "record_verdicts": lambda p, m: p.record_verdicts(SID, [verdict("pass")]),
    "stamp_verdict_agent": lambda p, m: (
        p.record_verdicts(SID, [verdict("pass")]),
        p.store.writes.clear(),
        p.stamp_verdict_agent(SID, TOKEN, "agent", model=MODEL_A),
    ),
    "record_audit": lambda p, m: p.record_audit(SID, host="h", agent="a", run=RUN),
    "record_coverage": lambda p, m: p.record_coverage(SID, {"a.py": 90.0}),
    "run_tests": lambda p, m: p.run_tests(SID, phase="red", cwd=m.root),
    "run_coverage": lambda p, m: p.run_coverage(SID, cwd=m.root),
}


class TestEveryWriterHoldsTheLock:
    @pytest.fixture
    def m(self, tmp_path):
        return git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"name": "p", "ecosystems": ["python"]},
                "commands": {
                    "test": "echo '1 passed in 0.1s'",
                    "coverage": "echo '1 passed in 0.1s'; echo 'a.py 10 1 90%'",
                },
            },
        )

    @pytest.mark.parametrize("writer", sorted(WRITERS))
    def test_across_its_load_and_its_save(self, m, writer):
        plain = PipelineDomain(m)
        plain.start(SID, task="t")
        if writer == "start":
            plain.advance(SID, "abandoned", cwd=m.root)
        store = WatchingStore(m.state_root, plain.relpath(SID))
        pipelines = PipelineDomain(m, store=store)

        WRITERS[writer](pipelines, m)

        assert store.writes, f"{writer} wrote nothing, so this proved nothing"
        assert store.writes == [(True, True)] * len(store.writes), (
            f"{writer}: (load locked, save locked) per write: {store.writes}"
        )

    def test_the_table_names_every_writer_in_the_domain(self):
        """A method that saves and is not in the table is a writer nobody checked."""
        import ast

        tree = ast.parse(Path(pipeline_module.__file__).read_text(encoding="utf-8"))
        domain = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PipelineDomain"
        )
        saving = set()
        for method in domain.body:
            if not isinstance(method, ast.FunctionDef):
                continue
            calls = {
                node.func.attr
                for node in ast.walk(method)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            if calls & {"_save", "_change", "_record_test_run", "record_coverage", "write"}:
                saving.add(method.name)
        # The helpers themselves, and the private route `run_tests` reaches it by.
        assert saving - {"_save", "_change", "_record_test_run"} == set(WRITERS)

    def test_nothing_outside_the_domain_writes_a_pipeline_record(self):
        """The domain's table says nothing about a writer in another module."""
        import ast

        domain = Path(pipeline_module.__file__)
        writes = {"write", "delete", "locked", "lock", "_save", "_change"}
        found = []
        for path in domain.parents[1].rglob("*.py"):
            if path == domain:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                if node.func.attr not in writes:
                    continue
                named = " ".join(ast.unparse(part) for part in (node.func.value, *node.args))
                if "pipeline" in named.lower():
                    found.append(f"{path.name}:{node.lineno}")
        assert found == []

    def test_a_write_that_changes_nothing_takes_no_lock(self, m):
        """The usual answer on every call a subagent makes, so it cannot wait."""
        pipelines = PipelineDomain(m)
        pipelines.start(SID, task="t")
        pipelines.record_docs_read(SID, DOC)
        pipelines.record_agent_run(SID, "agent")
        pipelines.record_audit(SID, host="h", agent="a", run=RUN)
        with wedged(m.state_root):
            pipelines.lock_timeout = BOUND_SECONDS
            before = time.monotonic()
            pipelines.record_docs_read(SID, DOC)
            assert pipelines.record_agent_run(SID, "agent") == pipeline_module.ALREADY
            assert pipelines.record_audit(SID, host="h", agent="a", run=RUN) is False
            assert pipelines.stamp_verdict_agent(SID, "no-such-token", "agent") == 0
            assert time.monotonic() - before < BOUND_SECONDS

    def test_no_pipeline_takes_no_lock_and_makes_no_lock_file(self, m):
        pipelines = PipelineDomain(m)
        assert pipelines.record_agent_run(SID, "agent") == pipeline_module.NO_PIPELINE
        pipelines.record_docs_read(SID, DOC)
        assert pipelines.record_audit(SID, host="h", agent="a") is False
        assert pipelines.stamp_verdict_agent(SID, TOKEN, "agent") == 0
        assert not (Path(m.state_root) / "locks" / f"{SID}.lock").exists()

    def test_a_record_that_went_away_under_the_lock_records_nothing(self, m):
        """The plain read saw a pipeline and the locked one does not."""
        pipelines = PipelineDomain(m)
        pipelines.start(SID, task="t")
        record = pipelines.require(SID)
        reads = iter([record])
        pipelines.current = lambda _sid: next(reads, None)
        assert pipelines.record_agent_run(SID, "agent") == pipeline_module.NO_PIPELINE

        record = PipelineDomain(m).require(SID)
        record.evidence["verdicts"] = [verdict("pass")]
        reads = iter([record])
        assert pipelines.stamp_verdict_agent(SID, TOKEN, "agent") == 0

    def test_a_write_decided_otherwise_under_the_lock_records_nothing(self, m):
        """Another writer got there between the plain read and the lock."""
        pipelines = PipelineDomain(m)
        pipelines.start(SID, task="t")
        stale = pipelines.require(SID)
        pipelines.record_docs_read(SID, DOC)
        fresh = pipelines.require(SID)
        reads = iter([stale, fresh])
        store_writes = []
        original = pipelines.store.write
        pipelines.store.write = lambda *a: (store_writes.append(a), original(*a))
        pipelines.current = lambda _sid: next(reads)
        pipelines.record_docs_read(SID, DOC)
        assert store_writes == []


class TestNothingSlowRunsUnderTheLock:
    @pytest.fixture
    def m(self, tmp_path):
        return git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"name": "p", "ecosystems": ["python"]},
                "commands": {
                    "test": "echo '1 passed in 0.1s'",
                    "coverage": "echo '1 passed in 0.1s'; echo 'a.py 10 1 90%'",
                },
            },
        )

    def test_advance_evaluates_its_gates_and_reads_usage_outside_it(self, m, monkeypatch):
        seen = []

        class Watermarks:
            def watermark(self, _host):
                seen.append(("watermark", lock_is_free(m.state_root)))

        pipelines = PipelineDomain(m, watermarks=Watermarks())
        pipelines.start(SID, task="t")
        original = pipelines.evaluate_gates

        def evaluate(*args, **kwargs):
            seen.append(("gates", lock_is_free(m.state_root)))
            return original(*args, **kwargs)

        monkeypatch.setattr(pipelines, "evaluate_gates", evaluate)
        pipelines.advance(
            SID, "abandoned", session={"host_session_id": "h"}, cwd=m.root
        )
        assert seen == [("gates", True), ("watermark", True)]

    def test_a_test_run_and_a_coverage_run_hold_no_lock_while_they_run(self, m, monkeypatch):
        pipelines = PipelineDomain(m)
        pipelines.start(SID, task="t")
        free = []
        original = pipeline_module.run_command

        def spawn(command, root):
            free.append(lock_is_free(m.state_root))
            return original(command, root)

        monkeypatch.setattr(pipeline_module, "run_command", spawn)
        pipelines.run_tests(SID, phase="red", cwd=m.root)
        pipelines.run_coverage(SID, cwd=m.root)
        assert free == [True, True]

    def test_start_reads_the_ruleset_pin_outside_it(self, m, monkeypatch):
        pipelines = PipelineDomain(m)
        free = []
        original = pipeline_module.ruleset_pin

        def pin(ctx):
            free.append(lock_is_free(m.state_root))
            return original(ctx)

        monkeypatch.setattr(pipeline_module, "ruleset_pin", pin)
        pipelines.start(SID, task="t")
        assert free == [True]


def _parked(m, second_write) -> None:
    """A loads and parks in its save; ``second_write`` runs; then A saves."""
    plain = PipelineDomain(m)
    store = ParkingStore(m.state_root, plain.relpath(SID))
    first = PipelineDomain(m, store=store)
    failures: list[BaseException] = []

    def guarded(work):
        def body():
            try:
                work()
            except BaseException as exc:  # noqa: BLE001 - reported on the main thread
                failures.append(exc)

        return threading.Thread(target=body, daemon=True)

    writer_a = guarded(lambda: first.record_docs_read(SID, "from-a.md"))
    writer_b = guarded(lambda: second_write(PipelineDomain(m)))
    try:
        writer_a.start()
        assert store.parked.wait(BOUND_SECONDS), f"A never reached its save: {failures}"
        writer_b.start()
        writer_b.join(BLOCKED_SECONDS)
        assert writer_b.is_alive(), "B finished while A held the lock"
    finally:
        store.release.set()
    writer_a.join(BOUND_SECONDS)
    writer_b.join(BOUND_SECONDS)
    assert not failures, failures
    assert not writer_a.is_alive() and not writer_b.is_alive()


class Run:
    """A test result as ``_record_test_run`` reads one."""

    def __init__(self, passed: bool) -> None:
        self.passed = passed
        self.exit_code = 0 if passed else 1
        self.total = 1
        self.failed = 0 if passed else 1
        self.failed_tests = 0 if passed else 1
        self.source = "console"
        self.device = None
        self.skipped = 0
        self.expected_failures = 0
        self.hazards = ()
        self.note = ""


class TestTheFailOpenCases:
    """The two losses the entry says fail open: a later fail read as a pass."""

    @pytest.fixture
    def m(self, tmp_path):
        return git_project(tmp_path / "proj")

    def test_a_red_run_is_not_lost_to_a_stale_save(self, m):
        """Lost, it leaves the older green run as the latest, and the gate passes."""
        plain = PipelineDomain(m)
        plain.start(SID, methodology="tdd", task="t")
        plain.advance(SID, "red", force=True, reason="the docs are not the point", cwd=m.root)
        plain.advance(SID, "green", force=True, reason="the red is not the point", cwd=m.root)
        plain._record_test_run(SID, "green", "suite", Run(passed=True))

        _parked(m, lambda pipelines: pipelines._record_test_run(
            SID, "green", "suite", Run(passed=False)
        ))

        runs = plain.require(SID).evidence["test_runs"]
        assert [run_["passed"] for run_ in runs] == [True, False]
        assert "from-a.md" in plain.require(SID).evidence["docs_read"]
        with pytest.raises(GateFailure) as refused:
            plain.advance(SID, "refactor", cwd=m.root)
        assert "test.all_pass" in [v.rule_id for v in refused.value.verdicts if v.blocks]

    def test_a_fail_verdict_is_not_lost_to_a_stale_save(self, m):
        """Lost, it leaves the older pass answering the rule."""
        plain = PipelineDomain(m)
        plain.start(SID, methodology="tdd", task="t")
        plain.record_verdicts(SID, [verdict("pass")])

        _parked(m, lambda pipelines: pipelines.record_verdicts(SID, [verdict("fail")]))

        recorded = plain.require(SID).evidence
        assert [v["status"] for v in recorded["verdicts"]] == ["pass", "fail"]
        assert "from-a.md" in recorded["docs_read"]

    def test_verdicts_checked_in_a_state_the_record_left_are_refused_under_the_lock(self, m):
        """The check and the append are one step, so an advance cannot land between."""
        plain = PipelineDomain(m)
        plain.start(SID, methodology="tdd", task="t")
        plain.advance(SID, "abandoned", cwd=m.root)
        with pytest.raises(VerdictRefused):
            plain.record_verdicts(SID, [verdict("fail", state="designing")])
        assert "verdicts" not in plain.require(SID).evidence


class TestAdvanceAndARecordThatMoves:
    @pytest.fixture
    def m(self, tmp_path):
        return git_project(tmp_path / "proj")

    def _during_evaluation(self, pipelines, monkeypatch, *writes):
        """Each evaluation returns its answer, then the next write lands."""
        original = pipelines.evaluate_gates
        pending = list(writes)
        calls = []

        def evaluate(*args, **kwargs):
            calls.append(args)
            answer = original(*args, **kwargs)
            if pending:
                pending.pop(0)()
            return answer

        monkeypatch.setattr(pipelines, "evaluate_gates", evaluate)
        return calls

    def _in_green_with_a_green_run(self, m):
        """One gate from `refactor`, whose entry reads the latest run."""
        pipelines = PipelineDomain(m)
        pipelines.start(SID, methodology="tdd", task="t")
        pipelines.advance(SID, "red", force=True, reason="the docs are not the point", cwd=m.root)
        pipelines.advance(SID, "green", force=True, reason="the red is not the point", cwd=m.root)
        pipelines._record_test_run(SID, "green", "suite", Run(passed=True))
        return pipelines

    def test_a_clean_advance_evaluates_once(self, m, monkeypatch):
        pipelines = self._in_green_with_a_green_run(m)
        calls = self._during_evaluation(pipelines, monkeypatch)
        pipeline, _ = pipelines.advance(SID, "refactor", cwd=m.root)
        assert pipeline.state == "refactor"
        assert len(calls) == 1

    def test_a_red_run_landing_during_the_evaluation_blocks_it(self, m, monkeypatch):
        """A green verdict reached before a red run landed may not pass over it."""
        pipelines = self._in_green_with_a_green_run(m)
        other = PipelineDomain(m)
        self._during_evaluation(pipelines, monkeypatch, lambda: other._record_test_run(
            SID, "green", "suite", Run(passed=False)
        ))
        with pytest.raises(GateFailure):
            pipelines.advance(SID, "refactor", cwd=m.root)
        assert pipelines.require(SID).state == "green"

    def test_evidence_landing_during_the_evaluation_survives_it(self, m, monkeypatch):
        pipelines = self._in_green_with_a_green_run(m)
        other = PipelineDomain(m)
        calls = self._during_evaluation(
            pipelines, monkeypatch, lambda: other.record_docs_read(SID, "landed.md")
        )
        pipeline, _ = pipelines.advance(SID, "refactor", cwd=m.root)
        assert pipeline.state == "refactor"
        assert "landed.md" in pipeline.evidence["docs_read"]
        assert len(calls) == 2

    def test_an_audit_write_during_the_evaluation_costs_nothing_and_survives(self, m, monkeypatch):
        """No gate can read it, so the verdicts still answer about the record."""
        pipelines = self._in_green_with_a_green_run(m)
        other = PipelineDomain(m)
        calls = self._during_evaluation(
            pipelines, monkeypatch, lambda: other.record_audit(SID, host="h", agent="a", run=RUN)
        )
        pipeline, _ = pipelines.advance(SID, "refactor", cwd=m.root)
        assert pipeline.state == "refactor"
        assert len(calls) == 1
        assert PipelineDomain(m).require(SID).evidence["audit"]["agents"]["a"]["runs"] == [RUN]

    def test_a_red_run_beside_an_audit_write_still_blocks_it(self, m, monkeypatch):
        """Only the audit record is left out of the comparison."""
        pipelines = self._in_green_with_a_green_run(m)
        other = PipelineDomain(m)

        def both():
            other.record_audit(SID, host="h", agent="a", run=RUN)
            other._record_test_run(SID, "green", "suite", Run(passed=False))

        self._during_evaluation(pipelines, monkeypatch, both)
        with pytest.raises(GateFailure):
            pipelines.advance(SID, "refactor", cwd=m.root)

    def test_the_state_moving_during_the_evaluation_asks_about_the_new_state(self, m, monkeypatch):
        """The edge is decided again, so nothing carries the state backwards."""
        pipelines = self._in_green_with_a_green_run(m)
        other = PipelineDomain(m)
        self._during_evaluation(
            pipelines, monkeypatch, lambda: other.advance(SID, "abandoned", cwd=m.root)
        )
        with pytest.raises(TransitionError):
            pipelines.advance(SID, "refactor", cwd=m.root)
        record = pipelines.require(SID)
        assert record.state == "abandoned"
        assert [row["to"] for row in record.history] == ["red", "green", "abandoned"]

    def test_a_record_that_never_holds_still_is_refused_with_nothing_written(self, m, monkeypatch):
        pipelines = self._in_green_with_a_green_run(m)
        other = PipelineDomain(m)
        writes = [
            lambda n=n: other.record_docs_read(SID, f"moving-{n}.md")
            for n in range(ADVANCE_ATTEMPTS)
        ]
        calls = self._during_evaluation(pipelines, monkeypatch, *writes)
        with pytest.raises(StateError, match="run the advance again"):
            pipelines.advance(SID, "refactor", cwd=m.root)
        assert len(calls) == ADVANCE_ATTEMPTS
        assert pipelines.require(SID).state == "green"

    def test_an_override_is_recorded_once_across_the_retries(self, m, monkeypatch):
        pipelines = PipelineDomain(m)
        pipelines.start(SID, methodology="tdd", task="t")
        other = PipelineDomain(m)
        self._during_evaluation(
            pipelines, monkeypatch, lambda: other.record_agent_run(SID, "agent")
        )
        pipeline, _ = pipelines.advance(SID, "red", force=True, reason="why", cwd=m.root)
        assert len(pipeline.overrides) == 1
        assert pipeline.evidence["agents_run"] == ["agent"]

    def test_two_starts_cannot_both_take_the_record(self, m, monkeypatch):
        """The second was past its plain check when the first landed."""
        pipelines = PipelineDomain(m)
        other = PipelineDomain(m)
        original = pipeline_module.ruleset_pin

        def pin(ctx):
            monkeypatch.setattr(pipeline_module, "ruleset_pin", original)
            other.start(SID, task="the winner")
            return original(ctx)

        monkeypatch.setattr(pipeline_module, "ruleset_pin", pin)
        with pytest.raises(UsageError, match="already running"):
            pipelines.start(SID, task="the loser")
        assert PipelineDomain(m).require(SID).task == "the winner"


class TestALockThatCannotBeHad:
    """What each kind of writer loses, and that nothing is half written."""

    @pytest.fixture
    def m(self, tmp_path):
        return git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"name": "p", "ecosystems": ["python"]},
                "commands": {"test": "echo '1 passed in 0.1s'"},
            },
        )

    @pytest.fixture
    def pipelines(self, m):
        pipelines = PipelineDomain(m, lock_timeout=SHORT)
        pipelines.start(SID, task="t")
        return pipelines

    @pytest.mark.parametrize(
        "write",
        [
            lambda p, root: p.advance(SID, "abandoned", cwd=root),
            lambda p, root: p.acknowledge(SID, "test.new_test_fails", "because"),
            lambda p, root: p.record_verdicts(SID, [verdict("pass")]),
            lambda p, root: p.record_coverage(SID, {"a.py": 90.0}),
            lambda p, root: p.run_tests(SID, phase="red", cwd=root),
        ],
        ids=["advance", "acknowledge", "verdicts", "coverage", "test-run"],
    )
    def test_a_command_is_refused_out_loud_with_the_record_untouched(self, m, pipelines, write):
        before = pipelines.store.read(pipelines.relpath(SID))
        with wedged(m.state_root), pytest.raises(LockTimeout, match="could not lock"):
            write(pipelines, m.root)
        assert pipelines.store.read(pipelines.relpath(SID)) == before

    def test_a_start_is_refused_out_loud_with_the_record_untouched(self, m, pipelines):
        pipelines.advance(SID, "abandoned", cwd=m.root)
        before = pipelines.store.read(pipelines.relpath(SID))
        with wedged(m.state_root), pytest.raises(LockTimeout, match="could not lock"):
            pipelines.start(SID, task="again")
        assert pipelines.store.read(pipelines.relpath(SID)) == before

    def test_the_command_line_reports_it_as_a_state_error(self, m, monkeypatch, capsys):
        """Exit 3, nothing recorded, and the run is simply repeated."""
        monkeypatch.chdir(m.root)
        session = SessionDomain(m).start("wedged")
        monkeypatch.setenv("INVARIANT_SESSION_ID", session.id)
        PipelineDomain(m).start(session.id, methodology="tdd", task="t")
        monkeypatch.setattr(
            "invariant.cli.main.Context.pipelines",
            property(lambda self: PipelineDomain(self.manifest, self.store, lock_timeout=SHORT)),
        )
        with wedged(m.state_root, session.id):
            assert run("pipeline", "advance", "abandoned") == 3
        assert "could not lock" in capsys.readouterr().err
        assert PipelineDomain(m).require(session.id).state == "designing"


class TestAHookOverAWedgedLock:
    """The hook returns its ordinary answer. The write is what is lost."""

    @pytest.fixture
    def wired(self, tmp_path):
        m = git_project(tmp_path / "proj")
        plain = PipelineDomain(m)
        plain.start(SID, task="t")
        plain.record_verdicts(SID, [dict(verdict("pass", state=plain.require(SID).state))])
        ctx = FakeCtx()
        ctx.manifest = m
        ctx.cwd = m.root
        ctx.pipelines = PipelineDomain(m, lock_timeout=SHORT)
        session = type("S", (), {"id": SID, "worktree": None})()
        ctx.resolution_for_payload = lambda _p: Resolution(session, "target")
        return ctx, m, plain

    @pytest.mark.parametrize(
        "call",
        [
            lambda tmp: {"tool_name": "Read", "tool_input": {"file_path": DOC}},
            lambda tmp: {
                "tool_name": "Agent",
                "tool_input": {"subagent_type": "reviewer", "prompt": "p", "model": MODEL_A},
            },
            lambda tmp: acting(tmp),
            lambda tmp: acting(tmp, tool_input={"command": VERDICT_CMD}, tool_response=VERDICT_OUT),
        ],
        ids=["docs-read", "dispatch", "first-act", "verdict-stamp"],
    )
    def test_each_hook_path_answers_and_loses_only_the_write(self, wired, tmp_path, call):
        ctx, m, plain = wired
        before = plain.store.read(plain.relpath(SID))
        with wedged(m.state_root):
            assert post(call(tmp_path), ctx) == {}
        assert plain.store.read(plain.relpath(SID)) == before

    def test_the_lost_write_lands_on_the_next_call(self, wired, tmp_path):
        """The cure the entry promises: an agent's next call records it."""
        ctx, m, plain = wired
        call = acting(tmp_path)
        with wedged(m.state_root):
            post(call, ctx)
        post(call, ctx)
        evidence = plain.require(SID).evidence
        assert evidence["agents_run"] == ["reviewer"]
        assert evidence["audit"]["agents"]["reviewer"]["runs"] == [RUN]

    def test_every_write_a_hook_can_reach_fits_inside_the_hosts_budget(self):
        """Four writes, each waiting out the lock, with time left to answer."""
        assert HOOK_LOCK_SECONDS * 4 < HOOK_TIMEOUT_SECONDS
        assert HOOK_LOCK_SECONDS < LOCK_TIMEOUT_SECONDS

    def test_the_installed_hooks_are_given_that_budget(self, tmp_path):
        host = claude_code.ClaudeCodeHost(tmp_path)
        host._wire_settings()
        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text("utf-8"))
        timeouts = {
            hook["timeout"]
            for entries in settings["hooks"].values()
            for entry in entries
            for hook in entry["hooks"]
        }
        assert timeouts == {HOOK_TIMEOUT_SECONDS}


class TestWhoGetsTheShortWait:
    def test_a_command_waits_the_stores_time(self, tmp_path):
        m = git_project(tmp_path / "proj")
        assert PipelineDomain(m).lock_timeout == LOCK_TIMEOUT_SECONDS
        assert Context(cwd=m.root).pipelines.lock_timeout == LOCK_TIMEOUT_SECONDS

    def test_a_context_hands_its_wait_to_its_pipelines(self, tmp_path):
        m = git_project(tmp_path / "proj")
        ctx = Context(cwd=m.root, pipeline_lock_timeout=SHORT)
        assert ctx.pipelines.lock_timeout == SHORT

    def test_the_hook_command_builds_its_context_with_the_short_wait(
        self, tmp_path, monkeypatch
    ):
        m = git_project(tmp_path / "proj")
        monkeypatch.chdir(m.root)
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        built = []
        real = Context

        class Spy(real):
            def __init__(self, *args, **kwargs):
                built.append(kwargs.get("pipeline_lock_timeout"))
                super().__init__(*args, **kwargs)

        monkeypatch.setattr("invariant.cli.main.Context", Spy)

        class Stdin:
            def isatty(self):
                return False

            def read(self):
                return json.dumps({"tool_name": "Bash", "tool_input": {"command": "true"}})

        monkeypatch.setattr("sys.stdin", Stdin())
        assert run("hook", "post-tool-use") == 0
        assert built[0] == HOOK_LOCK_SECONDS
