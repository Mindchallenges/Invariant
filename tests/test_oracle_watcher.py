"""The oracle's watcher, its de-dup ledger and its notifier (IDEA_012 increment 4).

A tick scans every registered project, assesses it, writes the snapshot, and
alerts once per rising edge. The ledger is what makes "once" true: a key seen
before is not news, a key that disappears is dropped so the condition coming
back is news again, and a new discriminator is a new key.

Nothing here reaches a real ``osascript``: the notifier is a recorder, the CLI
seam is replaced suite-wide in ``conftest.py``, and a guard test proves the
guard is in place.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from conftest import RefusedProgram
from test_oracle_scan import (
    THE_HOST_PID,
    FakeProbe,
    iso,
    make_project,
    put_attention,
    put_pipeline,
    put_session,
    store_of,
)

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import InvariantError
from invariant.hosts.claude_code_launch import applescript_string
from invariant.oracle import notify as notify_module
from invariant.oracle import watcher as watcher_module
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.notify import (
    Notification,
    NotifyError,
    OsascriptNotifier,
    RecordingNotifier,
)
from invariant.oracle.recommend import row_label
from invariant.oracle.scan import ScanCache
from invariant.oracle.signals import Thresholds
from invariant.oracle.watcher import (
    DEFAULT_INTERVAL,
    LEDGER_FILE,
    SNAPSHOT_FILE,
    SNAPSHOT_VERSION,
    STALE_INTERVALS,
    Watcher,
    snapshot_status,
)

#: A wait that began ten minutes before the run, on the watcher's default clock.
SINCE = iso(time.time() - 600)


def waiting(kind: str = "permission", since: str = SINCE) -> dict:
    return {"kind": kind, "message": "Allow Bash?", "since": since}


@pytest.fixture
def one_project(tmp_path):
    """One project, one live session whose host is the one live ``claude``."""
    base = tmp_path / "w"
    base.mkdir()
    alpha = make_project(base, "alpha")
    put_session(alpha, id="a-1", name="one-a-1", state="working",
                origin=str(alpha.root), host_session_id="h-1")
    put_attention(alpha, "h-1", pid=THE_HOST_PID, turn="running")
    return alpha


def make_watcher(notifier=None, clock=time.time, **kwargs) -> Watcher:
    return Watcher(
        ProjectIndex(oracle_home()),
        notifier,
        clock=clock,
        cache=ScanCache(probe=FakeProbe()),
        **kwargs,
    )


def ledger() -> dict:
    path = oracle_home() / LEDGER_FILE
    return json.loads(path.read_text("utf-8")) if path.exists() else {}


def alert_keys() -> set[str]:
    return set(ledger().get("alerts", {}))


# -- the notifier ------------------------------------------------------------


class TestOsascriptNotifier:
    def run_recorder(self, returncode=0, stderr=""):
        calls = []

        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, returncode, "", stderr)

        return calls, run

    def test_it_runs_one_display_notification_with_the_script_as_one_argument(self):
        calls, run = self.run_recorder()
        OsascriptNotifier(quote=applescript_string, run=run).notify(
            Notification(title='a "t"', message="m\\x", subtitle="s", sound="Glass")
        )
        (argv, kwargs), = calls
        assert argv[:2] == ["osascript", "-e"] and len(argv) == 3
        assert argv[2] == (
            'display notification "m\\\\x" with title "a \\"t\\"" '
            'subtitle "s" sound name "Glass"'
        )
        assert kwargs.get("shell") in (None, False)
        assert kwargs.get("check") is False

    def test_subtitle_and_sound_are_optional(self):
        calls, run = self.run_recorder()
        OsascriptNotifier(quote=applescript_string, run=run).notify(
            Notification(title="t", message="m")
        )
        assert calls[0][0][2] == 'display notification "m" with title "t"'

    def test_the_quoting_is_the_injected_one(self):
        calls, run = self.run_recorder()
        OsascriptNotifier(quote=lambda text: f"<{text}>", run=run).notify(
            Notification(title="t", message="m")
        )
        assert calls[0][0][2] == "display notification <m> with title <t>"

    def test_a_refusal_is_an_error_carrying_the_reason(self):
        _, run = self.run_recorder(returncode=1, stderr="not allowed")
        with pytest.raises(NotifyError, match="not allowed"):
            OsascriptNotifier(quote=applescript_string, run=run).notify(
                Notification(title="t", message="m")
            )

    def test_a_missing_osascript_is_an_error(self):
        def run(argv, **kwargs):
            raise FileNotFoundError("osascript")

        with pytest.raises(NotifyError, match="osascript"):
            OsascriptNotifier(quote=applescript_string, run=run).notify(
                Notification(title="t", message="m")
            )

    def test_the_default_runner_is_subprocess_run(self):
        assert OsascriptNotifier(quote=applescript_string).run is subprocess.run


class TestRecordingNotifier:
    def test_it_records_and_can_fail(self):
        recorder = RecordingNotifier()
        recorder.notify(Notification(title="t", message="m"))
        assert recorder.sent == [Notification(title="t", message="m")]
        recorder.fail = True
        with pytest.raises(NotifyError):
            recorder.notify(Notification(title="t", message="again"))
        assert len(recorder.sent) == 1


class TestNoTestReachesARealOsascript:
    def test_the_cli_seam_is_a_recorder_suite_wide(self, _no_real_notifier):
        assert cli_main._oracle_notifier() is _no_real_notifier
        assert isinstance(_no_real_notifier, RecordingNotifier)

    def test_running_osascript_is_refused(self):
        with pytest.raises(RefusedProgram, match="osascript"):
            subprocess.run(["osascript", "-e", 'return "x"'], check=False)
        with pytest.raises(RefusedProgram, match="osascript"):
            subprocess.run("/usr/bin/osascript -e 1", shell=True, check=False)

    def test_the_watchers_broad_handler_cannot_swallow_the_refusal(self, one_project):
        """The notify path catches ``Exception``; the refusal is not one."""
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        real = OsascriptNotifier(quote=applescript_string)
        with pytest.raises(RefusedProgram):
            make_watcher(notifier=real).tick()

    def test_other_programs_still_run(self):
        assert subprocess.run(["true"], check=False).returncode == 0

    def test_the_real_seam_is_osascript_with_the_hosts_quoting(self):
        notifier = cli_main._real_oracle_notifier()
        assert isinstance(notifier, OsascriptNotifier)
        assert notifier.quote is applescript_string


# -- the tick ----------------------------------------------------------------


class TestTheTick:
    def test_it_writes_the_snapshot_as_the_list_document_plus_metadata(
        self, one_project
    ):
        result = make_watcher(RecordingNotifier(), interval=7).tick()
        path = oracle_home() / SNAPSHOT_FILE
        assert result.snapshot_path == path
        body = json.loads(path.read_text("utf-8"))
        assert body["version"] == SNAPSHOT_VERSION
        assert body["interval"] == 7
        assert body["watcher_pid"] > 0
        assert body["home"] == str(oracle_home())
        assert body["generated"].endswith("+00:00")
        assert [p["project"]["name"] for p in body["projects"]] == ["alpha"]
        [row] = body["projects"][0]["rows"]
        assert row["session_id"] == "a-1"
        assert "signals" in row and "recommendation" in row
        assert result.elapsed >= 0
        assert result.rows == 1

    def test_the_snapshot_is_replaced_atomically(self, one_project, monkeypatch):
        replaced = []
        real = os.replace
        monkeypatch.setattr(os, "replace", lambda a, b: replaced.append(Path(b).name)
                            or real(a, b))
        make_watcher(RecordingNotifier()).tick()
        assert "snapshot.json" in replaced
        leftovers = [p.name for p in (oracle_home() / "oracle").iterdir()
                     if p.name.endswith(".tmp")]
        assert leftovers == []

    def test_a_rising_edge_notifies_once(self, one_project):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        recorder = RecordingNotifier()
        watcher = make_watcher(recorder)
        first = watcher.tick()
        assert [a["kind"] for a in first.alerts] == ["waiting"]
        assert first.alerts[0]["notified"] is True
        [sent] = recorder.sent
        assert sent.title == "invariant: alpha"
        assert sent.subtitle == "one-a-1  (working)"
        assert "waiting on a permission" in sent.message
        assert sent.sound == watcher_module.ALERT_SOUND
        assert watcher.tick().alerts == []
        assert len(recorder.sent) == 1
        [key] = alert_keys()
        assert key.endswith(f"|waiting|permission@{SINCE}")
        assert ledger()["alerts"][key]["notified"] is True
        assert ledger()["alerts"][key]["since"].endswith("+00:00")
        assert ledger()["version"] == 1

    def test_a_cleared_condition_is_dropped_and_alerts_again(self, one_project):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        recorder = RecordingNotifier()
        watcher = make_watcher(recorder)
        watcher.tick()
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running")
        assert watcher.tick().alerts == []
        assert alert_keys() == set()
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        assert len(watcher.tick().alerts) == 1
        assert len(recorder.sent) == 2

    def test_a_new_discriminator_alerts_again(self, one_project):
        put_pipeline(one_project, "a-1", methodology="tdd", state="green", evidence={
            "test_runs": [{"phase": "green", "passed": False, "failed": 1}]})
        recorder = RecordingNotifier()
        watcher = make_watcher(recorder)
        assert [a["kind"] for a in watcher.tick().alerts] == ["error"]
        put_pipeline(one_project, "a-1", methodology="tdd", state="green", evidence={
            "test_runs": [{"phase": "green", "passed": False, "failed": 1},
                          {"phase": "green", "passed": False, "failed": 3}]})
        second = watcher.tick()
        assert [a["key"].rsplit("|", 1)[-1] for a in second.alerts] == ["tests:2"]
        assert len(recorder.sent) == 2
        assert [k.rsplit("|", 1)[-1] for k in alert_keys()] == ["tests:2"]

    def test_markers_never_notify(self, one_project):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting("idle"))
        recorder = RecordingNotifier()
        result = make_watcher(recorder).tick()
        assert result.alerts == [] and recorder.sent == []
        assert alert_keys() == set()

    def test_a_low_severity_alert_is_silent(self, tmp_path):
        base = tmp_path / "w"
        base.mkdir()
        gamma = make_project(base, "gamma")
        put_session(gamma, id="g-1", name="rev-g-1", state="reviewed",
                    origin=str(gamma.root))
        recorder = RecordingNotifier()
        make_watcher(recorder).tick()
        [sent] = recorder.sent
        assert sent.sound is None
        assert "awaiting merge" in sent.message

    def test_without_a_notifier_the_edge_is_owed_not_spent(self, one_project):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        quiet = make_watcher(None).tick()
        assert [a["notified"] for a in quiet.alerts] == [False]
        [key] = alert_keys()
        assert ledger()["alerts"][key]["notified"] is False
        recorder = RecordingNotifier()
        owed = make_watcher(recorder).tick()
        assert [a["notified"] for a in owed.alerts] == [True]
        assert len(recorder.sent) == 1
        assert make_watcher(recorder).tick().alerts == []
        assert len(recorder.sent) == 1

    def test_an_owed_edge_is_not_reported_again_while_nothing_can_deliver_it(
        self, one_project
    ):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        quiet = make_watcher(None)
        assert len(quiet.tick().alerts) == 1
        assert quiet.tick().alerts == []
        [entry] = ledger()["alerts"].values()
        assert entry["notified"] is False

    def test_a_failed_notification_is_retried_on_the_next_tick(self, one_project):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        logged = []
        recorder = RecordingNotifier()
        recorder.fail = True
        watcher = make_watcher(recorder, log=logged.append)
        result = watcher.tick()
        assert [a["notified"] for a in result.alerts] == [False]
        assert any("notify" in line for line in logged)
        recorder.fail = False
        assert [a["notified"] for a in watcher.tick().alerts] == [True]
        assert len(recorder.sent) == 1

    def test_an_unreadable_project_keeps_its_ledger_entries(self, one_project):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        recorder = RecordingNotifier()
        watcher = make_watcher(recorder)
        watcher.tick()
        manifest = one_project.root / ".claude" / "invariant.yaml"
        body = manifest.read_text("utf-8")
        manifest.write_text("version: [unclosed\n", "utf-8")
        broken = watcher.tick()
        assert [a["kind"] for a in broken.alerts] == ["error"]
        assert any(k.endswith("|waiting|permission@" + SINCE) for k in alert_keys())
        manifest.write_text(body, "utf-8")
        healed = watcher.tick()
        assert healed.alerts == []
        assert len(recorder.sent) == 2
        assert not any(k.endswith("|scan") for k in alert_keys())

    def test_a_corrupt_ledger_is_replaced(self, one_project):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        path = oracle_home() / LEDGER_FILE
        path.parent.mkdir(parents=True)
        path.write_text("{not json", "utf-8")
        recorder = RecordingNotifier()
        assert len(make_watcher(recorder).tick().alerts) == 1
        assert len(alert_keys()) == 1

    @pytest.mark.parametrize("document", [
        [], {"version": 99, "alerts": {"x": {}}}, {"version": 1, "alerts": []},
        {"version": 1, "alerts": {"k": "not a dict"}},
    ])
    def test_a_ledger_it_cannot_read_starts_empty(self, one_project, document):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        store_path = oracle_home() / LEDGER_FILE
        store_path.parent.mkdir(parents=True)
        store_path.write_text(json.dumps(document), "utf-8")
        assert len(make_watcher(RecordingNotifier()).tick().alerts) == 1
        assert ledger()["version"] == 1

    def test_the_stuck_threshold_is_the_watchers(self, one_project):
        store = store_of(one_project)
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      turn_since="t1")
        path = store.root / "attention" / "h-1.json"
        old = time.time() - 30 * 60
        os.utime(path, (old, old))
        assert make_watcher(RecordingNotifier(),
                            thresholds=Thresholds(stuck_minutes=60)).tick().alerts == []
        assert [a["kind"] for a in make_watcher(
            RecordingNotifier(), thresholds=Thresholds(stuck_minutes=20)).tick().alerts
        ] == ["stuck"]

    def test_the_ledger_and_snapshot_are_safe_to_delete(self, one_project):
        put_attention(one_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        recorder = RecordingNotifier()
        watcher = make_watcher(recorder)
        watcher.tick()
        (oracle_home() / LEDGER_FILE).unlink()
        (oracle_home() / SNAPSHOT_FILE).unlink()
        assert len(watcher.tick().alerts) == 1
        assert (oracle_home() / SNAPSHOT_FILE).exists()

    def test_the_home_holds_only_the_oracle_subdirectory_and_locks(self, one_project):
        make_watcher(RecordingNotifier()).tick()
        assert sorted(p.name for p in oracle_home().iterdir()) == [
            "locks", "oracle", "projects.json"
        ]
        assert sorted(p.name for p in (oracle_home() / "oracle").iterdir()) == [
            "alerts.json", "snapshot.json"
        ]

    def test_nothing_under_a_state_root_is_written(self, one_project):
        from test_oracle_scan import tree_fingerprint

        before = tree_fingerprint(one_project.state_root)
        make_watcher(RecordingNotifier()).tick()
        assert tree_fingerprint(one_project.state_root) == before


# -- the loop ----------------------------------------------------------------


class TestTheLoop:
    def test_once_runs_one_tick_and_never_sleeps(self, one_project):
        slept = []
        ticks = []
        watcher = make_watcher(RecordingNotifier(), sleep=slept.append)
        watcher.run(once=True, on_tick=ticks.append)
        assert len(ticks) == 1 and slept == []

    def test_it_keeps_the_cadence(self, one_project):
        slept = []
        watcher = make_watcher(RecordingNotifier(), interval=5, sleep=slept.append)
        watcher.run(max_ticks=3)
        assert len(slept) == 2
        assert all(0 <= s <= 5 for s in slept)

    def test_a_tick_that_raises_is_logged_and_the_loop_goes_on(
        self, one_project, monkeypatch
    ):
        logged = []
        ticks = []
        watcher = make_watcher(RecordingNotifier(), sleep=lambda s: None,
                               log=logged.append)
        real = watcher.tick
        calls = iter([RuntimeError("boom"), None, None])

        def flaky():
            failure = next(calls)
            if failure:
                raise failure
            return real()

        monkeypatch.setattr(watcher, "tick", flaky)
        watcher.run(max_ticks=3, on_tick=ticks.append)
        assert len(ticks) == 2
        assert any("boom" in line for line in logged)

    def test_an_interval_must_be_positive(self):
        with pytest.raises(ValueError):
            make_watcher(None, interval=0)

    def test_the_default_log_writes_stderr(self, capsys):
        watcher_module._stderr("hello")
        assert "hello" in capsys.readouterr().err


# -- the budget --------------------------------------------------------------


class TestTheBudget:
    def test_three_projects_and_five_sessions_tick_well_inside_five_seconds(
        self, tmp_path
    ):
        base = tmp_path / "w"
        base.mkdir()
        for p in range(3):
            manifest = make_project(base, f"p{p}")
            for s in range(5):
                put_session(manifest, id=f"s{p}{s}", name=f"n-s{p}{s}",
                            state="working", origin=str(manifest.root),
                            host_session_id=f"h{p}{s}")
                put_attention(manifest, f"h{p}{s}", pid=THE_HOST_PID, turn="running",
                              waiting=waiting(since=f"2026-10-22T10:0{s}:00+00:00"))
        recorder = RecordingNotifier()
        watcher = make_watcher(recorder)
        cold = watcher.tick()
        warm = watcher.tick()
        assert cold.rows == 15
        assert len(recorder.sent) == 15
        assert cold.elapsed < 2.0, cold.elapsed
        assert warm.elapsed < 2.0, warm.elapsed
        print(f"tick: cold {cold.elapsed:.3f}s warm {warm.elapsed:.3f}s")


class TestSnapshotStatus:
    """How old the last tick is, for ``oracle status`` (increment 6)."""

    def write(self, body, age: float) -> Path:
        path = oracle_home() / SNAPSHOT_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body if isinstance(body, str) else json.dumps(body), "utf-8")
        then = time.time() - age
        os.utime(path, (then, then))
        return path

    def test_no_snapshot_is_stale_with_no_age(self):
        status = snapshot_status(oracle_home(), time.time())
        assert status == {
            "path": str(oracle_home() / SNAPSHOT_FILE), "exists": False, "age": None,
            "interval": None, "stale": True, "watcher_pid": None,
        }

    def test_a_snapshot_within_three_intervals_is_fresh(self):
        self.write({"interval": 5.0, "watcher_pid": 77}, age=14)
        status = snapshot_status(oracle_home(), time.time())
        assert status["exists"] and not status["stale"]
        assert status["interval"] == 5.0 and status["watcher_pid"] == 77
        assert 13 <= status["age"] <= 16

    def test_beyond_three_intervals_it_is_stale(self):
        assert STALE_INTERVALS == 3
        self.write({"interval": 2.0}, age=7)
        assert snapshot_status(oracle_home(), time.time())["stale"] is True

    def test_the_real_tick_writes_a_fresh_one(self, one_project):
        make_watcher().tick()
        status = snapshot_status(oracle_home(), time.time())
        assert status["stale"] is False
        assert status["watcher_pid"] == os.getpid()

    @pytest.mark.parametrize("body", ["not json", {"interval": "x"}, {"interval": -1}, []])
    def test_an_unreadable_interval_falls_back_to_the_default(self, body):
        self.write(body, age=12)
        status = snapshot_status(oracle_home(), time.time())
        assert status["interval"] == DEFAULT_INTERVAL
        assert status["stale"] is False


# -- the CLI -----------------------------------------------------------------


@pytest.fixture
def cli_project(one_project, monkeypatch):
    monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache(probe=FakeProbe()))
    monkeypatch.chdir(one_project.root.parent)
    return one_project


class TestWatchOnce:
    """The acceptance cases: one alert, no repeat, re-armed after a clear."""

    def test_a_permission_prompt_pings_once_and_rearms(
        self, cli_project, _no_real_notifier, capsys
    ):
        put_attention(cli_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        assert main(["oracle", "watch", "--once", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert [a["kind"] for a in body["alerts"]] == ["waiting"]
        assert body["snapshot"] == str(oracle_home() / SNAPSHOT_FILE)
        assert body["ledger"] == str(oracle_home() / LEDGER_FILE)
        assert body["rows"] == 1
        assert len(_no_real_notifier.sent) == 1

        assert main(["oracle", "watch", "--once"]) == 0
        assert len(_no_real_notifier.sent) == 1

        put_attention(cli_project, "h-1", pid=THE_HOST_PID, turn="running")
        assert main(["oracle", "watch", "--once"]) == 0
        put_attention(cli_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        assert main(["oracle", "watch", "--once"]) == 0
        assert len(_no_real_notifier.sent) == 2

    def test_the_text_names_each_new_alert(self, cli_project, capsys):
        put_attention(cli_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        assert main(["oracle", "watch", "--once"]) == 0
        out = capsys.readouterr().out
        assert "ALERT" in out and "one-a-1" in out and "waiting" in out
        assert "1 row" in out and "1 new alert" in out

    def test_no_notify_sends_nothing(self, cli_project, _no_real_notifier, capsys):
        put_attention(cli_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        assert main(["oracle", "watch", "--once", "--no-notify", "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert [a["notified"] for a in body["alerts"]] == [False]
        assert _no_real_notifier.sent == []

    def test_stuck_minutes_reaches_the_watcher(self, cli_project, monkeypatch):
        seen = {}
        original = watcher_module.Watcher.__init__

        def spy(self, *args, **kwargs):
            seen.update(kwargs)
            original(self, *args, **kwargs)

        monkeypatch.setattr(watcher_module.Watcher, "__init__", spy)
        assert main(["oracle", "watch", "--once", "--stuck-minutes", "45",
                     "--interval", "9"]) == 0
        assert seen["thresholds"].stuck_minutes == 45
        assert seen["interval"] == 9

    @pytest.mark.parametrize("flag", ["--interval", "--stuck-minutes"])
    def test_a_non_positive_number_is_a_usage_error(self, cli_project, flag, capsys):
        assert main(["oracle", "watch", "--once", flag, "0"]) == 2

    def test_a_broken_home_is_a_state_error(self, cli_project, capsys):
        home = oracle_home()
        (home / "oracle").write_text("not a directory", "utf-8")
        assert main(["oracle", "watch", "--once"]) == 3


def interrupt(seconds):
    raise KeyboardInterrupt


class TestWatchLoop:
    def test_it_prints_each_tick_and_stops_on_interrupt(
        self, cli_project, monkeypatch, capsys
    ):
        put_attention(cli_project, "h-1", pid=THE_HOST_PID, turn="running",
                      waiting=waiting())
        monkeypatch.setattr(watcher_module.time, "sleep", interrupt)
        assert main(["oracle", "watch"]) == 0
        out = capsys.readouterr().out
        assert "1 new alert" in out

    def test_json_is_one_line_per_tick(self, cli_project, monkeypatch, capsys):
        monkeypatch.setattr(watcher_module.time, "sleep", interrupt)
        assert main(["oracle", "watch", "--json"]) == 0
        lines = [line for line in capsys.readouterr().out.splitlines() if line]
        assert len(lines) == 1
        assert json.loads(lines[0])["rows"] == 1


class TestNotifyTest:
    def test_it_sends_one_notification_through_the_seam(self, _no_real_notifier, capsys):
        assert main(["oracle", "notify-test", "--json"]) == 0
        assert json.loads(capsys.readouterr().out) == {"sent": True}
        [sent] = _no_real_notifier.sent
        assert sent.title == "invariant oracle"
        assert "notify-test" in sent.message

    def test_text(self, _no_real_notifier, capsys):
        assert main(["oracle", "notify-test"]) == 0
        assert "sent" in capsys.readouterr().out

    def test_a_failure_is_reported(self, _no_real_notifier, capsys):
        _no_real_notifier.fail = True
        assert main(["oracle", "notify-test"]) == 1
        assert "notification" in capsys.readouterr().err

    def test_notify_error_is_an_invariant_error(self):
        assert issubclass(NotifyError, InvariantError)


class TestRowLabel:
    @pytest.mark.parametrize(("row", "label"), [
        ({"kind": "session", "name": "n", "state": "working"}, "n  (working)"),
        ({"kind": "host", "hosts": [{"host_session_id": "h"}]}, "host h"),
        ({"kind": "host", "hosts": []}, "host"),
        ({"kind": "worktree", "path": "/p"}, "worktree /p"),
        ({"kind": "error", "session_id": "s"}, "error s"),
        ({"kind": "error", "session_id": None}, "error"),
    ])
    def test_each_kind(self, row, label):
        assert row_label(row) == label


class TestLayering:
    def test_the_watcher_and_notifier_do_not_import_hosts(self):
        import ast

        for module in (watcher_module, notify_module):
            tree = ast.parse(Path(module.__file__).read_text("utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    assert "hosts" not in (node.module or "")
