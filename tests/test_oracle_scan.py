"""The oracle's read-only scan (IDEA_012 increment 3).

Three synthetic projects in real git repositories, five live sessions between
them — one with a host waiting on a permission prompt, one parked, one
stranded, one whose host died without a SessionEnd, one reviewed — plus orphan
hosts, a leaked worktree, a reused pid and an ended session. The scan must see
all of it, must write nothing under any state root, and must finish well
inside the dashboard's five-second budget.

Host facts come through a fake probe, the seam the CLI fills with Claude Code's
transcripts and process names, so nothing here reads ``~/.claude`` or asks
``ps`` about a real ``claude``.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.fsm import Machine
from invariant.core.machines import load_session_machine
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.store import JsonStore
from invariant.domains.pipeline import Pipeline
from invariant.domains.session import Session
from invariant.hosts import claude_code_launch as launch
from invariant.hosts.claude_code_telemetry import ClaudeCodeTranscripts
from invariant.oracle import scan as scan_module
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectEntry, ProjectIndex
from invariant.oracle.scan import (
    ROW_ERROR,
    ROW_HOST,
    ROW_SESSION,
    ROW_WORKTREE,
    STAGE_EARLY,
    STAGE_ENDED,
    STAGE_LANDED,
    STAGE_REVIEWED,
    STAGE_WORKING,
    ScanCache,
    pid_exists,
    scan_all,
    scan_project,
    session_stages,
)

NOW = time.time()
HOUR = 3600.0
THE_HOST_PID = os.getpid()
NOT_A_HOST_PID = os.getppid()


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", ""])
    proc.wait()
    return proc.pid


DEAD_PID = dead_pid()


# -- fixtures ------------------------------------------------------------


def git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


def make_project(base: Path, name: str) -> Manifest:
    root = base / name
    root.mkdir()
    git(root, "init", "-q", "-b", "master")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"version": 1, "project": {"name": name}}), "utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    manifest = Manifest.load(root.resolve())
    ProjectIndex(oracle_home()).register(manifest, via="manual")
    return manifest


def add_worktree(manifest: Manifest, name: str) -> Path:
    path = manifest.root.parent / f"{manifest.root.name}-{name}"
    git(manifest.root, "worktree", "add", "-q", "-b", f"session/{name}", str(path))
    return path.resolve()


def store_of(manifest: Manifest) -> JsonStore:
    return JsonStore(manifest.state_root)


def put_session(manifest: Manifest, **fields) -> Session:
    session = Session(**fields)
    store_of(manifest).write(f"sessions/{session.id}.json", session.to_dict())
    return session


def put_pipeline(manifest: Manifest, session_id: str, **fields) -> None:
    pipeline = Pipeline(session_id=session_id, **fields)
    store_of(manifest).write(
        f"pipelines/{session_id}.json", {"current": pipeline.to_dict(), "completed": []}
    )


def put_attention(manifest: Manifest, host_id: str, mtime: float | None = None, **fields):
    record = {"host_session_id": host_id, "ended": False, **fields}
    store = store_of(manifest)
    store.write(f"attention/{host_id}.json", record)
    if mtime is not None:
        path = store.root / "attention" / f"{host_id}.json"
        os.utime(path, (mtime, mtime))


class FakeTranscripts:
    def __init__(self, activity: dict[str, float]) -> None:
        self._activity = activity

    def activity(self) -> dict[str, float]:
        return dict(self._activity)


class FakeProbe:
    """Transcripts per project root, and the one pid that is a host."""

    def __init__(self, transcripts: dict[str, dict[str, float]] | None = None) -> None:
        self.by_root = transcripts or {}
        self.built: list[Path] = []
        self.asked: list[int] = []

    def transcripts(self, root: Path) -> FakeTranscripts:
        self.built.append(root)
        return FakeTranscripts(self.by_root.get(Path(root).name, {}))

    def is_host_process(self, pid: int) -> bool:
        self.asked.append(pid)
        return pid == THE_HOST_PID


@pytest.fixture
def machine_world(tmp_path):
    """Three projects, five live sessions, and everything around them."""
    base = tmp_path / "w"
    base.mkdir()
    alpha = make_project(base, "alpha")
    beta = make_project(base, "beta")
    gamma = make_project(base, "gamma")

    # alpha: a live session waiting on a permission prompt, and a parked one.
    live_wt = add_worktree(alpha, "live")
    put_session(alpha, id="a-live", name="live-a-live", state="working",
                worktree=str(live_wt), branch="session/live", origin=str(alpha.root),
                host_session_id="h-live", hosts=["h-older"], heartbeat=NOW - 600,
                plan=[{"n": 1, "title": "x", "done": False, "done_at": None}])
    put_attention(alpha, "h-live", pid=THE_HOST_PID, turn="running",
                  waiting={"kind": "permission", "message": "Allow Bash?",
                           "since": "2026-10-22T10:00:00+00:00"})
    put_pipeline(alpha, "a-live", methodology="tdd", state="green", evidence={
        "test_runs": [{"phase": "red", "passed": False, "failed": 1},
                      {"phase": "green", "passed": False, "failed": 2,
                       "read_as_red": False}]})
    parked_wt = add_worktree(alpha, "parked")
    put_session(alpha, id="a-parked", name="parked-a-parked", state="isolated",
                worktree=str(parked_wt), branch="session/parked", origin=str(alpha.root),
                host_session_id="h-parked-old", host_ended=True, heartbeat=NOW - 5 * HOUR)

    # beta: a stranded session, one whose host died, an orphan host, a leaked
    # worktree, and a host whose pid now belongs to something else.
    put_session(beta, id="b-strand", name="strand-b-strand", state="working",
                worktree=str(base / "gone"), branch="session/gone",
                origin=str(beta.root), host_ended=True)
    dead_wt = add_worktree(beta, "dead")
    put_session(beta, id="b-dead", name="dead-b-dead", state="working",
                worktree=str(dead_wt), branch="session/dead", origin=str(beta.root),
                host_session_id="h-dead", heartbeat=NOW - HOUR)
    put_attention(beta, "h-dead", pid=DEAD_PID, turn="running")
    put_attention(beta, "h-orphan", pid=THE_HOST_PID, turn="ended")
    put_attention(beta, "h-reused", pid=NOT_A_HOST_PID, turn="running")
    leaked = add_worktree(beta, "leaked")

    # gamma: a reviewed session, a torn-down one whose host lives on, and two
    # transcripts nobody claims — one recent, one a day and more old.
    put_session(gamma, id="g-rev", name="rev-g-rev", state="reviewed",
                origin=str(gamma.root), plan_touched=NOW - 120)
    put_pipeline(gamma, "g-rev", methodology="tdd", state="committed")
    put_session(gamma, id="g-done", name="done-g-done", state="torn_down",
                host_session_id="h-after")
    put_attention(gamma, "h-after", pid=THE_HOST_PID, turn="ended")
    put_attention(gamma, "h-stale", mtime=NOW - 3 * 24 * HOUR, turn="ended")

    probe = FakeProbe({
        "alpha": {"h-live": NOW - 30},
        "gamma": {"h-recent": NOW - HOUR, "h-old": NOW - 2 * 24 * HOUR},
    })
    return {
        "alpha": alpha, "beta": beta, "gamma": gamma, "probe": probe,
        "leaked": leaked, "index": ProjectIndex(oracle_home()), "base": base,
    }


def snapshot_by_name(snapshots) -> dict:
    return {s.project.name: s for s in snapshots}


def rows_of(snapshot, kind: str) -> dict:
    return {
        (r.session_id or r.path or (r.hosts[0].host_session_id if r.hosts else "")): r
        for r in snapshot.rows
        if r.kind == kind
    }


def tree_fingerprint(*roots: Path) -> str:
    """Every file under ``roots``: its path, size and mtime, hashed."""
    digest = hashlib.sha256()
    for root in roots:
        for path in sorted(Path(root).rglob("*")):
            stat = path.stat()
            digest.update(f"{path}|{path.is_dir()}|{stat.st_size}|{stat.st_mtime_ns}\n".encode())
    return digest.hexdigest()


# -- the whole machine -----------------------------------------------------


class TestTheMachine:
    def scan(self, world):
        cache = ScanCache(probe=world["probe"])
        return snapshot_by_name(scan_all(world["index"], clock=lambda: NOW, cache=cache))

    def test_every_project_and_every_live_session_is_listed(self, machine_world):
        found = self.scan(machine_world)
        assert sorted(found) == ["alpha", "beta", "gamma"]
        sessions = {
            sid for snap in found.values() for sid in rows_of(snap, ROW_SESSION)
        }
        assert sessions == {"a-live", "a-parked", "b-strand", "b-dead", "g-rev"}

    def test_a_waiting_session_carries_its_hosts_current_first(self, machine_world):
        row = rows_of(self.scan(machine_world)["alpha"], ROW_SESSION)["a-live"]
        assert [h.host_session_id for h in row.hosts] == ["h-live", "h-older"]
        current, older = row.hosts
        assert current.current and not older.current
        assert current.alive is True
        assert current.pid == THE_HOST_PID
        assert current.attention["waiting"]["kind"] == "permission"
        assert current.transcript_written == NOW - 30
        assert older.attention is None and older.alive is None
        assert row.stage == STAGE_WORKING
        assert row.plan_total == 1 and row.plan_complete is False
        assert row.pipeline.state == "green"
        assert row.pipeline.test_runs == 2
        assert row.pipeline.last_test["failed"] == 2
        assert row.pipeline.red_phase == "red"
        assert row.pipeline.landed is False

    def test_last_activity_is_the_latest_sign_of_life(self, machine_world):
        row = rows_of(self.scan(machine_world)["alpha"], ROW_SESSION)["a-live"]
        current = row.hosts[0]
        assert row.last_activity == max(
            current.attention_written, current.transcript_written, NOW - 600
        )

    def test_a_parked_session(self, machine_world):
        row = rows_of(self.scan(machine_world)["alpha"], ROW_SESSION)["a-parked"]
        assert row.host_ended is True
        assert row.stranded is False
        assert row.stage == STAGE_EARLY
        assert row.last_activity == NOW - 5 * HOUR

    def test_a_stranded_session(self, machine_world):
        row = rows_of(self.scan(machine_world)["beta"], ROW_SESSION)["b-strand"]
        assert row.stranded is True
        assert row.hosts == []
        assert row.last_activity is None

    def test_a_dead_pid_is_a_gone_host(self, machine_world):
        row = rows_of(self.scan(machine_world)["beta"], ROW_SESSION)["b-dead"]
        assert row.hosts[0].pid == DEAD_PID
        assert row.hosts[0].alive is False

    def test_orphan_hosts(self, machine_world):
        beta = rows_of(self.scan(machine_world)["beta"], ROW_HOST)
        assert set(beta) == {"h-orphan", "h-reused"}
        assert beta["h-orphan"].hosts[0].alive is True
        assert beta["h-reused"].hosts[0].alive is False, "a reused pid is not the host"
        assert beta["h-orphan"].adoptable == []

    def test_a_host_outliving_its_session_and_a_recent_transcript(self, machine_world):
        gamma = rows_of(self.scan(machine_world)["gamma"], ROW_HOST)
        assert set(gamma) == {"h-after", "h-recent"}
        assert gamma["h-after"].previous_session == "g-done"
        assert gamma["h-recent"].hosts[0].transcript_written == NOW - HOUR
        assert gamma["h-recent"].hosts[0].attention is None
        assert gamma["h-recent"].last_activity == NOW - HOUR
        assert gamma["h-recent"].adoptable == ["g-rev"]

    def test_a_reviewed_session_with_a_landed_pipeline(self, machine_world):
        row = rows_of(self.scan(machine_world)["gamma"], ROW_SESSION)["g-rev"]
        assert row.stage == STAGE_REVIEWED
        assert row.pipeline.terminal is True
        assert row.pipeline.landed is True
        assert row.last_activity == NOW - 120

    def test_ended_sessions_are_not_rows(self, machine_world):
        gamma = self.scan(machine_world)["gamma"]
        assert "g-done" not in rows_of(gamma, ROW_SESSION)

    def test_a_leaked_worktree(self, machine_world):
        beta = self.scan(machine_world)["beta"]
        assert set(rows_of(beta, ROW_WORKTREE)) == {str(machine_world["leaked"])}
        alpha = self.scan(machine_world)["alpha"]
        assert rows_of(alpha, ROW_WORKTREE) == {}

    def test_the_scan_writes_nothing_anywhere(self, machine_world):
        roots = [
            machine_world[name].state_root for name in ("alpha", "beta", "gamma")
        ]
        home = oracle_home()
        before = tree_fingerprint(*roots, home)
        listing = sorted(str(p) for root in roots for p in root.rglob("*"))
        self.scan(machine_world)
        self.scan(machine_world)
        assert tree_fingerprint(*roots, home) == before
        assert sorted(str(p) for root in roots for p in root.rglob("*")) == listing

    def test_three_projects_and_five_sessions_inside_the_budget(self, machine_world):
        cache = ScanCache(probe=machine_world["probe"])
        started = time.perf_counter()
        snapshots = scan_all(machine_world["index"], clock=lambda: NOW, cache=cache)
        cold = time.perf_counter() - started
        started = time.perf_counter()
        scan_all(machine_world["index"], clock=lambda: NOW, cache=cache)
        warm = time.perf_counter() - started
        print(f"\noracle scan: cold {cold:.3f}s, warm {warm:.3f}s")
        assert len(snapshots) == 3
        assert cold < 5.0
        assert warm < 5.0
        assert all(s.elapsed >= 0 for s in snapshots)

    def test_select_narrows_to_one_project(self, machine_world):
        cache = ScanCache(probe=machine_world["probe"])
        only = scan_all(machine_world["index"], clock=lambda: NOW, cache=cache,
                        select="beta")
        assert [s.project.name for s in only] == ["beta"]

    def test_a_snapshot_serialises(self, machine_world):
        alpha = self.scan(machine_world)["alpha"]
        body = json.loads(json.dumps(alpha.to_dict()))
        assert body["project"]["name"] == "alpha"
        assert body["scanned_at"] == NOW
        assert {r["kind"] for r in body["rows"]} == {ROW_SESSION}


# -- failure isolation ---------------------------------------------------------


class TestBrokenProjects:
    def test_a_broken_manifest_is_one_error_row_and_hides_nothing(self, machine_world):
        manifest_path = machine_world["beta"].root / MANIFEST_RELPATH
        manifest_path.write_text("version: [unclosed\n", encoding="utf-8")
        found = snapshot_by_name(
            scan_all(machine_world["index"], clock=lambda: NOW,
                     cache=ScanCache(probe=machine_world["probe"]))
        )
        [error] = found["beta"].rows
        assert error.kind == ROW_ERROR
        assert "YAML" in error.error
        assert "a-live" in rows_of(found["alpha"], ROW_SESSION)

    def test_a_missing_root_is_an_error_row(self, tmp_path):
        entry = ProjectEntry(key=str(tmp_path / "state"), root=str(tmp_path / "nowhere"),
                             name="ghost", via="manual")
        [row] = scan_project(entry, clock=lambda: NOW).rows
        assert row.kind == ROW_ERROR
        assert row.project_name == "ghost"

    def test_one_corrupt_session_record_is_its_own_error_row(self, machine_world):
        alpha = machine_world["alpha"]
        (alpha.state_root / "sessions" / "a-bad.json").write_text("{nope", "utf-8")
        snapshot = scan_project(
            ProjectIndex(oracle_home()).get(str(alpha.state_root)),
            clock=lambda: NOW, cache=ScanCache(probe=machine_world["probe"]),
        )
        errors = rows_of(snapshot, ROW_ERROR)
        assert set(errors) == {"a-bad"}
        assert "a-live" in rows_of(snapshot, ROW_SESSION)

    def test_a_corrupt_attention_record_does_not_hide_its_session(self, machine_world):
        alpha = machine_world["alpha"]
        (alpha.state_root / "attention" / "h-live.json").write_text("{", "utf-8")
        snapshot = scan_project(
            ProjectIndex(oracle_home()).get(str(alpha.state_root)),
            clock=lambda: NOW, cache=ScanCache(probe=machine_world["probe"]),
        )
        row = rows_of(snapshot, ROW_SESSION)["a-live"]
        assert row.hosts[0].attention is None
        assert row.hosts[0].alive is None

    def test_a_broken_pipeline_record_leaves_the_session_without_one(self, machine_world):
        alpha = machine_world["alpha"]
        put_pipeline(alpha, "a-parked", methodology="no-such-machine", state="x")
        snapshot = scan_project(
            ProjectIndex(oracle_home()).get(str(alpha.state_root)),
            clock=lambda: NOW, cache=ScanCache(probe=machine_world["probe"]),
        )
        row = rows_of(snapshot, ROW_SESSION)["a-parked"]
        assert row.pipeline.methodology == "no-such-machine"
        assert row.pipeline.terminal is False


# -- the stages, from the machine's structure -----------------------------------


class TestStages:
    def test_the_shipped_session_machine(self):
        stages = session_stages(load_session_machine())
        assert stages == {
            "registered": STAGE_EARLY,
            "isolated": STAGE_EARLY,
            "working": STAGE_WORKING,
            "reviewed": STAGE_REVIEWED,
            "merged": STAGE_LANDED,
            "pushed": STAGE_LANDED,
            "torn_down": STAGE_ENDED,
            "abandoned": STAGE_ENDED,
        }

    def test_a_project_machine_with_its_own_names(self):
        machine = Machine.from_dict({
            "id": "session", "kind": "session", "initial": "new",
            "terminal": ["gone", "dropped"],
            "states": {
                "new": {"next": ["doing", "dropped"]},
                "doing": {"next": ["checked", "dropped"]},
                "checked": {"next": ["landed", "doing"],
                            "entry_gates": ["pipeline.complete"]},
                "landed": {"next": ["gone"], "entry_gates": ["session.ff_merge_target"]},
                "gone": {},
                "dropped": {"roles": ["abandon"]},
            },
        })
        assert session_stages(machine) == {
            "new": STAGE_EARLY, "doing": STAGE_WORKING, "checked": STAGE_REVIEWED,
            "landed": STAGE_LANDED, "gone": STAGE_ENDED, "dropped": STAGE_ENDED,
        }

    def test_a_machine_that_never_merges_is_all_early(self):
        machine = Machine.from_dict({
            "id": "session", "kind": "session", "initial": "a", "terminal": ["b"],
            "states": {"a": {"next": ["b"]}, "b": {}},
        })
        assert session_stages(machine) == {"a": STAGE_EARLY, "b": STAGE_ENDED}


# -- the cache -----------------------------------------------------------------


class TestCache:
    def test_git_is_asked_once_per_ttl(self, machine_world, monkeypatch):
        calls = []
        real = scan_module.git.worktrees

        def counting(root):
            calls.append(root)
            return real(root)

        monkeypatch.setattr(scan_module.git, "worktrees", counting)
        ticks = [0.0]
        cache = ScanCache(probe=machine_world["probe"], ttl=60, monotonic=lambda: ticks[0])
        for _ in range(3):
            scan_all(machine_world["index"], clock=lambda: NOW, cache=cache)
        assert len(calls) == 3
        assert len(machine_world["probe"].built) == 3
        ticks[0] = 61.0
        scan_all(machine_world["index"], clock=lambda: NOW, cache=cache)
        assert len(calls) == 6

    def test_the_process_name_is_asked_once_per_pid(self):
        probe = FakeProbe()
        cache = ScanCache(probe=probe, pid_alive=lambda pid: True)
        assert cache.host_alive(THE_HOST_PID) is True
        assert cache.host_alive(THE_HOST_PID) is True
        assert probe.asked == [THE_HOST_PID]

    def test_a_dead_pid_is_not_asked_its_name_and_not_remembered(self):
        probe = FakeProbe()
        alive = {"now": False}
        cache = ScanCache(probe=probe, pid_alive=lambda pid: alive["now"])
        assert cache.host_alive(THE_HOST_PID) is False
        assert probe.asked == []
        alive["now"] = True
        assert cache.host_alive(THE_HOST_PID) is True

    def test_no_pid_is_unknown(self):
        assert ScanCache().host_alive(None) is None

    def test_without_a_probe_a_live_pid_counts_and_there_are_no_transcripts(self):
        cache = ScanCache()
        assert cache.host_alive(os.getpid()) is True
        assert cache.transcript_activity(Path("/")) == {}

    def test_a_probe_that_raises_costs_only_its_answer(self):
        class Broken:
            def transcripts(self, root):
                raise OSError("no")

            def is_host_process(self, pid):
                raise OSError("no")

        cache = ScanCache(probe=Broken(), pid_alive=lambda pid: True)
        assert cache.transcript_activity(Path("/")) == {}
        assert cache.host_alive(7) is None


class TestPidExists:
    def test_this_process_exists(self):
        assert pid_exists(os.getpid()) is True

    def test_a_reaped_child_does_not(self):
        assert pid_exists(DEAD_PID) is False

    @pytest.mark.parametrize("pid", [None, 0, -1, "12"])
    def test_nothing_that_is_not_a_positive_pid_exists(self, pid):
        assert pid_exists(pid) is False

    def test_a_process_we_may_not_signal_exists(self, monkeypatch):
        def denied(pid, sig):
            raise PermissionError

        monkeypatch.setattr(scan_module.os, "kill", denied)
        assert pid_exists(1) is True

    def test_any_other_error_reads_as_absent(self, monkeypatch):
        def broken(pid, sig):
            raise OSError("odd")

        monkeypatch.setattr(scan_module.os, "kill", broken)
        assert pid_exists(1) is False


# -- the Claude Code side of the probe ------------------------------------------


class TestClaudeCodeProbe:
    def test_transcript_activity_is_each_transcripts_mtime(self, tmp_path):
        home = tmp_path / "claude"
        origin = tmp_path / "proj"
        origin.mkdir()
        project = home / "projects" / str(origin.resolve()).replace("/", "-")
        project.mkdir(parents=True)
        (project / "h1.jsonl").write_text("{}\n", "utf-8")
        os.utime(project / "h1.jsonl", (1000.0, 1000.0))
        activity = ClaudeCodeTranscripts(origin=origin.resolve(), home=home).activity()
        assert activity == {"h1": 1000.0}

    def test_a_transcript_that_vanishes_is_skipped(self, tmp_path, monkeypatch):
        ghost = tmp_path / "ghost.jsonl"
        monkeypatch.setattr(ClaudeCodeTranscripts, "paths", lambda self: [ghost])
        assert ClaudeCodeTranscripts(origin=tmp_path).activity() == {}

    def test_the_probe_builds_transcripts_for_a_root(self, tmp_path):
        probe = launch.ClaudeCodeProbe(home=tmp_path)
        source = probe.transcripts(tmp_path)
        assert isinstance(source, ClaudeCodeTranscripts)
        assert source.origin == tmp_path
        assert source.home == tmp_path

    @pytest.mark.parametrize(
        ("name", "expected"),
        [("claude", True), ("/usr/local/bin/claude", True), ("zsh", False), (None, False)],
    )
    def test_the_host_is_a_process_named_claude(self, name, expected):
        probe = launch.ClaudeCodeProbe(name_of=lambda pid: name)
        assert probe.is_host_process(42) is expected

    def test_process_name_reads_ps(self):
        assert launch.process_name(os.getpid())
        assert launch.process_name(DEAD_PID) is None

    def test_process_name_without_ps(self, monkeypatch):
        def missing(*args, **kwargs):
            raise OSError("no ps")

        monkeypatch.setattr(launch.subprocess, "run", missing)
        assert launch.process_name(1) is None

    def test_the_closer_asks_the_same_question(self, monkeypatch):
        monkeypatch.setattr(launch, "process_name", lambda pid: f"name-{pid}")
        assert launch.DetachedCloser().process_name(7) == "name-7"


# -- the CLI ---------------------------------------------------------------------


@pytest.fixture
def cli_world(machine_world, monkeypatch):
    monkeypatch.setattr(
        cli_main, "_oracle_cache", lambda: ScanCache(probe=machine_world["probe"])
    )
    monkeypatch.chdir(machine_world["base"])
    return machine_world


def last_json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestOracleList:
    def test_json_lists_every_project_with_signals_and_advice(self, cli_world, capsys):
        assert main(["oracle", "list", "--json"]) == 0
        body = last_json(capsys)
        assert body["home"] == str(oracle_home())
        names = [p["project"]["name"] for p in body["projects"]]
        assert names == ["alpha", "beta", "gamma"]
        alpha_rows = {r["session_id"]: r for r in body["projects"][0]["rows"]}
        live = alpha_rows["a-live"]
        assert {s["kind"] for s in live["signals"]} >= {"waiting", "error"}
        assert live["recommendation"]["kind"] == "info"
        assert live["recommendation"]["argv"] == []

    def test_json_narrows_to_one_project(self, cli_world, capsys):
        assert main(["oracle", "list", "--project", "gamma", "--json"]) == 0
        body = last_json(capsys)
        assert [p["project"]["name"] for p in body["projects"]] == ["gamma"]
        [rev] = [r for r in body["projects"][0]["rows"] if r["session_id"] == "g-rev"]
        assert rev["recommendation"]["argv"] == [
            "invariant", "session", "merge", "--id", "g-rev"
        ]

    def test_an_unknown_project_is_a_usage_error(self, cli_world, capsys):
        assert main(["oracle", "list", "--project", "nope"]) == 2
        assert "nope" in capsys.readouterr().err

    def test_text_is_a_status_table(self, cli_world, capsys):
        assert main(["oracle", "list"]) == 0
        out = capsys.readouterr().out
        assert "alpha" in out and "beta" in out and "gamma" in out
        assert "ALERT" in out
        assert "live-a-live" in out
        assert "waiting" in out
        assert "invariant session merge --id g-rev" in out
        assert "leaked_worktree" in out
        assert "host h-orphan" in out

    def test_text_with_no_projects(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache())
        monkeypatch.chdir(tmp_path)
        assert main(["oracle", "list"]) == 0
        assert "no projects registered" in capsys.readouterr().out

    def test_a_project_with_nothing_live(self, monkeypatch, tmp_path, capsys):
        make_project(tmp_path, "quiet")
        monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache())
        monkeypatch.chdir(tmp_path)
        assert main(["oracle", "list"]) == 0
        assert "nothing live" in capsys.readouterr().out

    def test_the_real_cache_seam_carries_the_claude_code_probe(self):
        cache = cli_main._oracle_cache()
        assert isinstance(cache, ScanCache)
        assert isinstance(cache.probe, launch.ClaudeCodeProbe)


class TestLayering:
    def test_the_oracle_does_not_import_hosts(self):
        """ADR-007 D11: host facts reach the oracle through the probe the CLI injects."""
        import ast

        src = Path(scan_module.__file__).resolve().parent
        offenders = []
        for path in src.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and "hosts" in (node.module or ""):
                    offenders.append(path.name)
                if isinstance(node, ast.Import) and any("hosts" in a.name for a in node.names):
                    offenders.append(path.name)
        assert offenders == []
