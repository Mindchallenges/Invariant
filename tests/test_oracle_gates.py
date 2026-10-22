"""The gate cache: recommendation detail, never an alert (IDEA_012 increment 5).

Gates are evaluated off the tick, in worker threads, and only for a session
that is idle and whose fingerprint moved since the last evaluation. Most of
these tests replace the evaluator with a fake that counts and, where a test
needs it, blocks, so the cadence is asserted without running a rule. The rest
run the real evaluator over a real project and check that it wrote nothing.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from test_oracle_scan import (
    THE_HOST_PID,
    FakeProbe,
    add_worktree,
    make_project,
    put_attention,
    put_pipeline,
    put_session,
    tree_fingerprint,
)
from test_oracle_signals import NOW, PROJECT, attention, host, session_row

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.manifest import Level
from invariant.core.rules import Evaluated, Verdict
from invariant.oracle import gates as gates_module
from invariant.oracle.gates import (
    GateCache,
    GateReport,
    OwedGate,
    eligible,
    evaluate_row,
    fingerprint,
    gate_key,
    summarise,
)
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectEntry, ProjectIndex
from invariant.oracle.recommend import assess
from invariant.oracle.scan import (
    ROW_HOST,
    PipelineView,
    ProjectSnapshot,
    Row,
    ScanCache,
    scan_all,
)
from invariant.oracle.watcher import Watcher

ROOT = "/repo/alpha"


def pipeline(state: str = "green", transitions: int = 2, written_ns: int = 1) -> PipelineView:
    return PipelineView(
        methodology="tdd", state=state, transitions=transitions, written_ns=written_ns
    )


def idle_row(tmp_path: Path, sid: str = "s1", **fields) -> Row:
    """A session whose turn ended, with a worktree that exists."""
    base = {
        "session_id": sid,
        "worktree": str(tmp_path),
        "heartbeat": NOW - 600,
        "pipeline": pipeline(),
        "hosts": [host(attention(turn="ended"))],
        "last_activity": NOW - 30,
    }
    base.update(fields)
    return session_row(**base)


def snap(*rows: Row) -> list[ProjectSnapshot]:
    entry = ProjectEntry(key=PROJECT, root=ROOT, name="alpha", via="manual")
    return [ProjectSnapshot(project=entry, rows=list(rows), scanned_at=NOW)]


def verdict(rule_id, ok=False, level=Level.BLOCK, message="owed", skipped=False, fixes=()):
    return Evaluated(rule_id, level, Verdict(ok=ok, message=message, skipped=skipped,
                                             fixes=tuple(fixes)))


class Counting:
    """A stand-in for ``evaluate_gates_by_destination`` that counts its calls."""

    def __init__(self, answer=None, gate: threading.Event | None = None):
        self.answer = answer if answer is not None else {
            "refactor": [verdict("test.all_pass", message="2 failed")],
            "abandoned": [],
        }
        self.gate = gate
        self.calls: list[str] = []
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, row: Row):
        with self._lock:
            self.calls.append(row.session_id)
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.gate is not None:
                self.gate.wait(5)
            return self.answer
        finally:
            with self._lock:
                self.active -= 1


# -- the fingerprint ----------------------------------------------------------


class TestFingerprint:
    def test_it_is_the_session_and_pipeline_facts(self, tmp_path):
        row = idle_row(tmp_path, state="working", heartbeat=5.0,
                       pipeline=pipeline("red", transitions=3, written_ns=77))
        assert fingerprint(row) == ("working", 5.0, "red", 3, 77)

    @pytest.mark.parametrize("change", [
        lambda: {"state": "reviewed"},
        lambda: {"heartbeat": NOW},
        lambda: {"pipeline": pipeline("refactor")},
        lambda: {"pipeline": pipeline(transitions=9)},
        lambda: {"pipeline": pipeline(written_ns=2)},
    ], ids=["state", "heartbeat", "pipeline-state", "transitions", "written"])
    def test_each_fact_moves_it(self, tmp_path, change):
        assert fingerprint(idle_row(tmp_path)) != fingerprint(idle_row(tmp_path, **change()))


class TestEligibility:
    def test_an_ended_turn_is_idle_at_once(self, tmp_path):
        assert eligible(idle_row(tmp_path, last_activity=NOW), NOW)

    def test_a_running_turn_is_never_evaluated(self, tmp_path):
        row = idle_row(tmp_path, hosts=[host(attention(turn="running"))],
                       last_activity=NOW - 3600)
        assert not eligible(row, NOW)

    def test_a_running_turn_whose_host_is_gone_waits_for_quiet(self, tmp_path):
        gone = [host(attention(turn="running"), alive=False)]
        assert not eligible(idle_row(tmp_path, hosts=gone, last_activity=NOW - 30), NOW)
        assert eligible(idle_row(tmp_path, hosts=gone, last_activity=NOW - 300), NOW)

    def test_no_turn_known_needs_two_quiet_minutes(self, tmp_path):
        assert not eligible(idle_row(tmp_path, hosts=[], last_activity=NOW - 60), NOW)
        assert eligible(idle_row(tmp_path, hosts=[], last_activity=NOW - 121), NOW)
        assert eligible(idle_row(tmp_path, hosts=[], last_activity=None), NOW)

    def test_a_gone_worktree_is_skipped(self, tmp_path):
        assert not eligible(idle_row(tmp_path, worktree=str(tmp_path / "gone")), NOW)
        assert not eligible(idle_row(tmp_path, stranded=True), NOW)

    def test_no_worktree_recorded_runs_from_an_existing_root(self, tmp_path):
        assert eligible(idle_row(tmp_path, worktree=None, root=str(tmp_path)), NOW)
        assert not eligible(idle_row(tmp_path, worktree=None, root=str(tmp_path / "x")), NOW)

    def test_no_pipeline_or_a_finished_one_owes_nothing(self, tmp_path):
        assert not eligible(idle_row(tmp_path, pipeline=None), NOW)
        done = PipelineView(methodology="tdd", state="committed", terminal=True)
        assert not eligible(idle_row(tmp_path, pipeline=done), NOW)

    def test_only_session_rows(self, tmp_path):
        row = Row(kind=ROW_HOST, project=PROJECT, root=str(tmp_path), hosts=[host()])
        assert not eligible(row, NOW)


# -- the summary ---------------------------------------------------------------


class TestSummarise:
    def test_owed_gates_are_named_once_with_every_edge_that_owes_them(self):
        report = summarise({
            "refactor": [verdict("test.all_pass", message="2 failed\nmore")],
            "reviewing": [
                verdict("test.all_pass", message="2 failed\nmore"),
                verdict("coverage.meets_floor", level=Level.WARN, message="80%",
                        fixes=("write tests",)),
                verdict("docs.updated", ok=True),
                verdict("privacy.clean", ok=False, skipped=True),
            ],
            "abandoned": [],
        }, "green")
        assert report.pipeline_state == "green"
        first, second = report.owed
        assert first == OwedGate("test.all_pass", "block", "2 failed", True,
                                 ("refactor", "reviewing"))
        assert second.rule_id == "coverage.meets_floor"
        assert second.blocks is False and second.fix == "write tests"
        assert [g.rule_id for g in report.blocking] == ["test.all_pass"]

    def test_blocking_gates_come_first(self):
        report = summarise({"x": [verdict("a", level=Level.WARN), verdict("b")]}, "s")
        assert [g.rule_id for g in report.owed] == ["b", "a"]

    def test_it_serialises(self):
        body = summarise({"x": [verdict("b")]}, "s").to_dict()
        assert body["owed"][0]["to"] == ["x"]
        assert body["stale"] is False and body["timed_out"] is False
        assert body["error"] is None


# -- the cache ---------------------------------------------------------------


class TestCadence:
    def test_the_fingerprint_gates_re_evaluation(self, tmp_path):
        fake = Counting()
        cache = GateCache(evaluate=fake)
        row = idle_row(tmp_path)
        first = cache.refresh(snap(row), NOW, wait=2)
        assert fake.calls == ["s1"]
        assert first[gate_key(row)].blocking[0].rule_id == "test.all_pass"

        cache.refresh(snap(row), NOW, wait=2)
        cache.refresh(snap(idle_row(tmp_path)), NOW + 60, wait=2)
        assert fake.calls == ["s1"]

        moved = idle_row(tmp_path, pipeline=pipeline(written_ns=2))
        report = cache.refresh(snap(moved), NOW, wait=2)[gate_key(moved)]
        assert fake.calls == ["s1", "s1"]
        assert report.stale is False

    def test_no_evaluation_while_the_turn_is_running(self, tmp_path):
        fake = Counting()
        cache = GateCache(evaluate=fake)
        cache.refresh(snap(idle_row(tmp_path)), NOW, wait=2)
        running = idle_row(tmp_path, hosts=[host(attention(turn="running"))],
                           heartbeat=NOW, last_activity=NOW - 3600)
        reports = cache.refresh(snap(running), NOW, wait=2)
        assert fake.calls == ["s1"]
        report = reports[gate_key(running)]
        assert report.stale is True
        assert report.blocking[0].rule_id == "test.all_pass"

    def test_a_running_turn_with_nothing_cached_has_no_report(self, tmp_path):
        fake = Counting()
        running = idle_row(tmp_path, hosts=[host(attention(turn="running"))])
        assert GateCache(evaluate=fake).refresh(snap(running), NOW, wait=1) == {}
        assert fake.calls == []

    def test_a_timeout_keeps_the_previous_result(self, tmp_path):
        release = threading.Event()
        fake = Counting()
        cache = GateCache(evaluate=fake, timeout=0.5)
        row = idle_row(tmp_path)
        cache.refresh(snap(row), NOW, wait=2)

        fake.gate = release
        fake.answer = {"refactor": [verdict("other.gate")]}
        moved = idle_row(tmp_path, heartbeat=NOW)
        report = cache.refresh(snap(moved), NOW, wait=1)[gate_key(moved)]
        assert report.timed_out is True and report.stale is True
        assert [g.rule_id for g in report.owed] == ["test.all_pass"]

        release.set()
        _wait_for(lambda: not _workers())
        again = cache.refresh(snap(moved), NOW, wait=0.2)[gate_key(moved)]
        assert len(fake.calls) == 2, "a timed-out fingerprint is not retried"
        assert [g.rule_id for g in again.owed] == ["test.all_pass"]

    def test_a_timeout_with_nothing_before_reports_it(self, tmp_path):
        release = threading.Event()
        cache = GateCache(evaluate=Counting(gate=release), timeout=0.05)
        row = idle_row(tmp_path)
        report = cache.refresh(snap(row), NOW, wait=1)[gate_key(row)]
        release.set()
        assert report.timed_out is True and report.owed == ()

    def test_concurrency_is_capped(self, tmp_path):
        release = threading.Event()
        fake = Counting(gate=release)
        cache = GateCache(evaluate=fake, max_concurrent=2)
        rows = [idle_row(tmp_path, sid=f"s{n}") for n in range(5)]
        assert cache.refresh(snap(*rows), NOW, wait=0) == {}
        _wait_for(lambda: fake.active == 2)
        time.sleep(0.05)
        assert len(fake.calls) == 2

        release.set()
        reports = cache.refresh(snap(*rows), NOW, wait=3)
        assert len(fake.calls) == 5
        assert fake.peak <= 2
        assert set(reports) == {gate_key(r) for r in rows}

    def test_a_timed_out_worker_still_holds_its_slot(self, tmp_path):
        release = threading.Event()
        fake = Counting(gate=release)
        cache = GateCache(evaluate=fake, timeout=0.05, max_concurrent=1)
        first, second = idle_row(tmp_path, sid="s1"), idle_row(tmp_path, sid="s2")
        cache.refresh(snap(first, second), NOW, wait=0.3)
        assert fake.calls == ["s1"], "the hung worker is still running"
        release.set()
        _wait_for(lambda: not _workers())
        cache.refresh(snap(first, second), NOW, wait=2)
        assert fake.calls == ["s1", "s2"]

    def test_wait_zero_returns_at_once_and_the_next_refresh_collects(self, tmp_path):
        fake = Counting()
        cache = GateCache(evaluate=fake)
        row = idle_row(tmp_path)
        cache.refresh(snap(row), NOW, wait=0)
        _wait_for(lambda: len(fake.calls) == 1 and not _workers())
        assert gate_key(row) in cache.refresh(snap(row), NOW, wait=0)

    def test_an_evaluator_that_raises_is_recorded_and_not_retried(self, tmp_path):
        calls = []

        def broken(row):
            calls.append(row.session_id)
            raise RuntimeError("no manifest")

        cache = GateCache(evaluate=broken)
        row = idle_row(tmp_path)
        report = cache.refresh(snap(row), NOW, wait=2)[gate_key(row)]
        assert report.error == "no manifest" and report.owed == ()
        cache.refresh(snap(row), NOW, wait=2)
        assert calls == ["s1"]

    def test_an_error_keeps_the_previous_result(self, tmp_path):
        answers = iter([{"x": [verdict("a")]}])

        def flaky(row):
            return next(answers)

        cache = GateCache(evaluate=flaky)
        cache.refresh(snap(idle_row(tmp_path)), NOW, wait=2)
        moved = idle_row(tmp_path, heartbeat=NOW)
        report = cache.refresh(snap(moved), NOW, wait=2)[gate_key(moved)]
        assert report.error and report.stale
        assert [g.rule_id for g in report.owed] == ["a"]

    def test_a_session_that_is_gone_is_forgotten(self, tmp_path):
        fake = Counting()
        cache = GateCache(evaluate=fake)
        cache.refresh(snap(idle_row(tmp_path)), NOW, wait=2)
        assert cache.refresh(snap(), NOW, wait=0) == {}
        cache.refresh(snap(idle_row(tmp_path)), NOW, wait=2)
        assert fake.calls == ["s1", "s1"]

    def test_the_settings_must_be_sane(self):
        with pytest.raises(ValueError):
            GateCache(timeout=0)
        with pytest.raises(ValueError):
            GateCache(max_concurrent=0)


def _workers() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name.startswith("oracle-gates-")]


def _wait_for(condition, limit: float = 3.0) -> None:
    deadline = time.monotonic() + limit
    while not condition():
        assert time.monotonic() < deadline, "condition never held"
        time.sleep(0.005)


# -- the real evaluator ------------------------------------------------------


@pytest.fixture
def gate_world(tmp_path):
    """One project, one idle session in a worktree with a pipeline in green."""
    base = tmp_path / "w"
    base.mkdir()
    alpha = make_project(base, "alpha")
    wt = add_worktree(alpha, "idle")
    put_session(alpha, id="a-idle", name="idle-a-idle", state="working",
                worktree=str(wt), branch="session/idle", origin=str(alpha.root),
                host_session_id="h-idle", heartbeat=NOW - 600)
    put_attention(alpha, "h-idle", pid=THE_HOST_PID, turn="ended")
    put_pipeline(alpha, "a-idle", methodology="tdd", state="green")
    return {"alpha": alpha, "base": base, "worktree": wt,
            "index": ProjectIndex(oracle_home())}


class TestTheRealEvaluator:
    def scan(self, world):
        return scan_all(world["index"], cache=ScanCache(probe=FakeProbe()))

    def test_it_reports_what_pipeline_gates_would(self, gate_world):
        [snapshot] = self.scan(gate_world)
        [row] = [r for r in snapshot.rows if r.session_id == "a-idle"]
        by_destination = evaluate_row(row)
        assert "test.all_pass" in {v.rule_id for v in by_destination["refactor"]}

    def test_nothing_is_written_under_any_state_root(self, gate_world):
        alpha = gate_world["alpha"]
        before = tree_fingerprint(alpha.state_root, oracle_home())
        snapshots = self.scan(gate_world)
        reports = GateCache().refresh(snapshots, time.time(), wait=10)
        assert any(r.blocking for r in reports.values())
        assert tree_fingerprint(alpha.state_root, oracle_home()) == before

    def test_the_owed_gates_reach_the_recommendation(self, gate_world):
        put_attention(gate_world["alpha"], "h-idle", pid=THE_HOST_PID, turn="ended",
                      blocked={"rules": ["test.all_pass"], "surface": "cli"})
        snapshots = self.scan(gate_world)
        now = time.time()
        reports = GateCache().refresh(snapshots, now, wait=10)
        document = assess(snapshots, now, gates=reports)
        [row] = [r for r in document["projects"][0]["rows"] if r["session_id"] == "a-idle"]
        advice = row["recommendation"]
        assert advice["argv"] == ["invariant", "pipeline", "gates"]
        assert "test.all_pass" in advice["detail"]
        assert row["gates"]["owed"][0]["rule_id"] == "test.all_pass"
        assert [s["kind"] for s in row["signals"] if s["alert"]] == ["gate_blocked"]

    def test_a_gone_worktree_is_refused_not_guessed(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="worktree is gone"):
            evaluate_row(idle_row(tmp_path, worktree=str(tmp_path / "gone")))

    def test_the_project_worktree_manifest_is_the_one_read(self, gate_world, monkeypatch):
        seen = []
        real = gates_module.Manifest.load

        def spy(start=None, *args, **kwargs):
            seen.append(Path(start))
            return real(start, *args, **kwargs)

        [snapshot] = self.scan(gate_world)
        [row] = [r for r in snapshot.rows if r.session_id == "a-idle"]
        monkeypatch.setattr(gates_module.Manifest, "load", spy)
        evaluate_row(row)
        assert seen == [gate_world["worktree"]]


class TestTheEvaluationTakesNoOptionalLock:
    """The rules' git runs with ``GIT_OPTIONAL_LOCKS=0`` (ADR-007 D16).

    A host is working in the worktree being evaluated, and a plain ``git
    diff`` there may refresh the index stat cache and take ``index.lock``
    under it. The oracle must be strictly read-only, so every git subprocess
    the evaluation starts carries the variable — and the oracle's other git,
    and the rest of the process, do not.
    """

    @pytest.fixture
    def git_envs(self, monkeypatch):
        from invariant.core import git as git_module

        seen: list = []
        real = git_module.subprocess.run

        def record(argv, *args, **kwargs):
            if argv and argv[0] == "git":
                seen.append(kwargs.get("env"))
            return real(argv, *args, **kwargs)

        monkeypatch.setattr(git_module.subprocess, "run", record)
        return seen

    def row(self, world):
        # From reviewing, the edge to committed owes docs.updated and the
        # coverage floor, which ask git what changed: plain `git diff`.
        put_pipeline(world["alpha"], "a-idle", methodology="tdd", state="reviewing")
        [snapshot] = scan_all(world["index"], cache=ScanCache(probe=FakeProbe()))
        [row] = [r for r in snapshot.rows if r.session_id == "a-idle"]
        return snapshot, row

    def test_every_git_the_evaluation_runs_carries_it(self, gate_world, git_envs):
        _, row = self.row(gate_world)
        git_envs.clear()
        evaluate_row(row)
        assert git_envs, "the evaluation asked git nothing; the test proves nothing"
        assert len(git_envs) > 5
        assert all(env is not None and env["GIT_OPTIONAL_LOCKS"] == "0" for env in git_envs)

    def test_it_reaches_the_worker_thread(self, gate_world, git_envs):
        snapshot, _ = self.row(gate_world)
        git_envs.clear()
        GateCache().refresh([snapshot], time.time(), wait=10)
        assert git_envs
        assert all(env is not None and env["GIT_OPTIONAL_LOCKS"] == "0" for env in git_envs)

    def test_the_scan_and_the_process_are_untouched(self, gate_world, git_envs, monkeypatch):
        import os

        monkeypatch.delenv("GIT_OPTIONAL_LOCKS", raising=False)
        _, row = self.row(gate_world)
        assert git_envs and all(env is None for env in git_envs)
        evaluate_row(row)
        assert "GIT_OPTIONAL_LOCKS" not in os.environ


# -- the watcher and the CLI ---------------------------------------------------


class TestTheWatcher:
    def test_gates_feed_the_snapshot_and_never_an_alert(self, gate_world):
        fake = Counting()
        watcher = Watcher(gate_world["index"], None, cache=ScanCache(probe=FakeProbe()),
                          gates=GateCache(evaluate=fake), gate_wait=5)
        result = watcher.tick()
        [row] = [r for r in result.document["projects"][0]["rows"]
                 if r["session_id"] == "a-idle"]
        assert row["gates"]["owed"][0]["rule_id"] == "test.all_pass"
        assert "next owed" in row["recommendation"]["label"]
        assert result.alerts == []
        watcher.tick()
        assert fake.calls == ["a-idle"], "the cache lives across ticks"

    def test_a_watcher_without_gates_evaluates_nothing(self, gate_world):
        watcher = Watcher(gate_world["index"], None, cache=ScanCache(probe=FakeProbe()))
        [row] = [r for r in watcher.tick().document["projects"][0]["rows"]
                 if r["session_id"] == "a-idle"]
        assert row["gates"] is None

    def test_the_tick_does_not_wait_for_a_slow_evaluation(self, gate_world):
        release = threading.Event()
        watcher = Watcher(gate_world["index"], None, cache=ScanCache(probe=FakeProbe()),
                          gates=GateCache(evaluate=Counting(gate=release)))
        started = time.perf_counter()
        watcher.tick()
        elapsed = time.perf_counter() - started
        release.set()
        assert elapsed < 2.0

    def test_the_gate_wait_must_not_be_negative(self, gate_world):
        with pytest.raises(ValueError):
            Watcher(gate_world["index"], None, gate_wait=-1)


class TestTheCli:
    @pytest.fixture
    def cli(self, gate_world, monkeypatch):
        monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache(probe=FakeProbe()))
        monkeypatch.chdir(gate_world["base"])
        return gate_world

    def test_list_evaluates_the_idle_session_once(self, cli, capsys, monkeypatch):
        fake = Counting()
        monkeypatch.setattr(cli_main, "_oracle_gates", lambda: GateCache(evaluate=fake))
        assert main(["oracle", "list"]) == 0
        out = capsys.readouterr().out
        assert "next owed: test.all_pass" in out
        assert fake.calls == ["a-idle"]

    def test_list_json_carries_the_real_gates(self, cli, capsys):
        assert main(["oracle", "list", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        [row] = [r for r in body["projects"][0]["rows"] if r["session_id"] == "a-idle"]
        assert row["gates"]["owed"][0]["rule_id"] == "test.all_pass"

    def test_the_detail_line_is_printed(self, cli, capsys):
        put_attention(cli["alpha"], "h-idle", pid=THE_HOST_PID, turn="ended",
                      blocked={"rules": ["test.all_pass"], "surface": "cli"})
        assert main(["oracle", "list"]) == 0
        out = capsys.readouterr().out
        assert "invariant pipeline gates" in out
        assert "owed: test.all_pass" in out

    def test_watch_once_waits_for_the_gates(self, cli, capsys):
        assert main(["oracle", "watch", "--once", "--no-notify", "--json"]) == 0
        capsys.readouterr()
        snapshot = json.loads((oracle_home() / "oracle" / "snapshot.json").read_text())
        [row] = [r for r in snapshot["projects"][0]["rows"] if r["session_id"] == "a-idle"]
        assert row["gates"]["owed"][0]["rule_id"] == "test.all_pass"

    def test_the_real_gate_seam_is_a_cache(self):
        assert isinstance(cli_main._oracle_gates(), GateCache)


def test_a_report_is_plain_data():
    report = GateReport(pipeline_state="red")
    assert report.blocking == () and report.to_dict()["owed"] == []
