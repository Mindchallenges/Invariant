"""The oracle's dashboard, ``invariant oracle tui`` (IDEA_012 increment 7).

Three layers, each tested where it lives:

* ``oracle/dashboard`` — pure: where the document comes from (the watcher's
  snapshot while it is fresh, else an in-process scan behind a banner) and how
  each row becomes a line of the table, with its tone;
* ``oracle/actions`` — pure apart from the one subprocess it can start: the
  step a row recommends, what the confirm dialog shows, the refusals, and the
  dispatch to an injected runner or terminal launcher;
* ``oracle/tui`` — the Textual app, driven through ``App.run_test()``.

**Nothing here runs a recommended command or opens a window.** The CLI reaches
the runner through ``_oracle_runner`` and the terminal through the existing
``_handoff_launcher``; ``conftest.py`` replaces both for every test. Every
confirm test reads the recorder those fixtures return.
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest
import test_oracle_scan

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import InvariantError, UsageError
from invariant.hosts import claude_code_launch as launch
from invariant.oracle import actions as actions_module
from invariant.oracle import dashboard, tui
from invariant.oracle.actions import (
    Actions,
    Outcome,
    RecordingRunner,
    Step,
    child_env,
    confirm_lines,
    refusal,
    run_subprocess,
    step_for,
    terminal_request,
)
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import INFO, RUN, TERMINAL, assess
from invariant.oracle.scan import ScanCache, scan_all
from invariant.oracle.watcher import SNAPSHOT_FILE, Watcher

# -- fixtures --------------------------------------------------------------

#: Three projects, five live sessions and everything around them, shared with
#: the scan's tests.
machine_world = test_oracle_scan.machine_world


def _tick(world) -> dict:
    """One watcher tick over the machine: a fresh snapshot, and its document."""
    watcher = Watcher(
        ProjectIndex(oracle_home()), None, cache=ScanCache(probe=world["probe"])
    )
    return watcher.tick().document


def _scan(world):
    """An in-process scan of the machine, counted."""
    calls = []

    def scan() -> dict:
        calls.append(1)
        snapshots = scan_all(ProjectIndex(oracle_home()), cache=ScanCache(probe=world["probe"]))
        return assess(snapshots, time.time())

    return scan, calls


def _age_snapshot(seconds: float) -> None:
    path = oracle_home() / SNAPSHOT_FILE
    old = time.time() - seconds
    os.utime(path, (old, old))


def _row(document: dict, **match) -> dict:
    for project in document["projects"]:
        for row in project["rows"]:
            if all(row.get(k) == v for k, v in match.items()):
                return row
    raise AssertionError(f"no row matches {match}")


def _actions(runner=None, terminal=None, preview=None) -> Actions:
    return Actions(
        runner=runner or RecordingRunner(),
        environ=lambda: {"PATH": "/usr/bin", "CLAUDECODE": "1", "INVARIANT_SESSION_ID": "mine"},
        strip=lambda env: {k: v for k, v in env.items() if k != "CLAUDECODE"},
        terminal=terminal,
        preview=preview,
    )


def run_step(**overrides) -> Step:
    fields = {
        "label": "merge it, from the main checkout",
        "kind": RUN,
        "argv": ("invariant", "session", "merge", "--id", "s-1"),
        "cwd": str(Path.cwd()),
        "env": {},
        "detail": "",
        "subject": "one-s-1  (reviewed)",
        "session_id": "s-1",
        "session_name": "one-s-1",
    }
    fields.update(overrides)
    return Step(**fields)


def drive(app, script, size=(200, 50)):
    """Run ``script(pilot)`` against ``app`` headless, and return the app."""

    async def go():
        async with app.run_test(size=size) as pilot:
            await settle(app, pilot)
            await script(pilot)

    asyncio.run(go())
    return app


async def settle(app, pilot):
    for _ in range(3):
        await app.workers.wait_for_complete()
        await pilot.pause()


def row_key_for(app, **match) -> str:
    for key, line in app.lines.items():
        if not line.nested and all(line.row.get(k) == v for k, v in match.items()):
            return key
    raise AssertionError(f"no line for {match}")


async def select(app, pilot, **match):
    table = app.query_one("#rows")
    table.move_cursor(row=table.get_row_index(row_key_for(app, **match)))
    await pilot.pause()


# -- dashboard: where the document comes from --------------------------------


class TestLoad:
    def test_a_fresh_snapshot_is_read_and_nothing_is_scanned(self, machine_world):
        document = _tick(machine_world)
        scan, calls = _scan(machine_world)
        loaded = dashboard.load(oracle_home(), time.time(), scan)
        assert loaded.source == dashboard.SOURCE_SNAPSHOT
        assert loaded.banner == ""
        assert calls == []
        assert loaded.document["projects"] == json.loads(
            json.dumps(document["projects"], default=str)
        )
        assert loaded.interval == document["interval"]

    def test_a_stale_snapshot_scans_in_process_behind_a_banner(self, machine_world):
        _tick(machine_world)
        _age_snapshot(60)
        scan, calls = _scan(machine_world)
        loaded = dashboard.load(oracle_home(), time.time(), scan)
        assert calls == [1]
        assert loaded.source == dashboard.SOURCE_SCAN
        assert loaded.banner.startswith("watcher not running")
        assert "old" in loaded.banner and "invariant oracle install" in loaded.banner
        assert {p["project"]["name"] for p in loaded.document["projects"]} == {
            "alpha", "beta", "gamma"
        }

    def test_no_snapshot_at_all_scans_too(self, tmp_path):
        loaded = dashboard.load(tmp_path, time.time(), lambda: {"projects": []})
        assert loaded.source == dashboard.SOURCE_SCAN
        assert "no snapshot has been written" in loaded.banner
        assert loaded.interval == dashboard.DEFAULT_INTERVAL

    @pytest.mark.parametrize("body", ["{not json", "[1, 2]", '{"projects": 3}'])
    def test_a_fresh_snapshot_that_cannot_be_read_scans(self, tmp_path, body):
        path = tmp_path / SNAPSHOT_FILE
        path.parent.mkdir(parents=True)
        path.write_text(body, "utf-8")
        loaded = dashboard.load(tmp_path, time.time(), lambda: {"projects": []})
        assert loaded.source == dashboard.SOURCE_SCAN
        assert "cannot be read" in loaded.banner


# -- dashboard: rows into lines ----------------------------------------------


class TestLines:
    def test_sessions_carry_their_hosts_then_orphans_worktrees_and_errors(self, machine_world):
        document = _tick(machine_world)
        document["projects"][1]["rows"].insert(
            0, {"kind": "error", "project": "x", "error": "boom", "session_id": None,
                "signals": [], "recommendation": None, "hosts": []},
        )
        lines = dashboard.lines(document)
        assert len({line.key for line in lines}) == len(lines)
        beta = [line for line in lines if not line.nested and line.project == "beta"]
        kinds = [line.row["kind"] for line in beta]
        assert kinds.index("error") > max(i for i, k in enumerate(kinds) if k == "worktree")
        firsts = [kinds.index(k) for k in ("session", "host", "worktree", "error")]
        assert firsts == sorted(firsts)

        live = next(
            i for i, line in enumerate(lines)
            if not line.nested and line.row.get("session_id") == "a-live"
        )
        nested = lines[live + 1 : live + 3]
        assert [line.nested for line in nested] == [True, True]
        assert nested[0].subject.endswith("host h-live")
        assert nested[0].stage == "current host"
        assert nested[1].stage == "earlier host"
        assert all(line.row["session_id"] == "a-live" for line in nested)

    def test_the_waiting_row_is_red(self, machine_world):
        lines = dashboard.lines(_tick(machine_world))
        line = next(x for x in lines if not x.nested and x.row.get("session_id") == "a-live")
        assert line.tone == dashboard.RED
        assert line.status == "ALERT"
        assert line.signals[0][1] == dashboard.RED
        assert "waiting" in line.signals[0][0]

    def test_a_session_row_names_its_stage_and_its_lane(self, machine_world):
        lines = dashboard.lines(_tick(machine_world))
        line = next(x for x in lines if not x.nested and x.row.get("session_id") == "a-live")
        assert line.stage == "working · tdd:green"
        rev = next(x for x in lines if not x.nested and x.row.get("session_id") == "g-rev")
        assert rev.next == "invariant session merge --id g-rev"

    def test_tones(self):
        def signal(alert, severity="info"):
            return {"kind": "k", "detail": "d", "alert": alert, "severity": severity}

        assert dashboard.row_tone({"signals": []}) == dashboard.PLAIN
        assert dashboard.row_tone({"signals": [signal(False)]}) == dashboard.DIM
        assert dashboard.row_tone({"signals": [signal(True, "low")]}) == dashboard.AMBER
        assert dashboard.row_tone({"signals": [signal(True, "medium")]}) == dashboard.AMBER
        assert dashboard.row_tone(
            {"signals": [signal(False), signal(True, "high")]}
        ) == dashboard.RED
        assert dashboard.status_flag({"signals": []}) == "ok"
        assert dashboard.status_flag({"signals": [signal(False)]}) == "note"
        assert dashboard.status_flag({"signals": [signal(True)]}) == "ALERT"

    def test_the_next_column(self):
        assert dashboard.next_text(None) == ""
        assert dashboard.next_text({"label": "your turn", "argv": []}) == "your turn"
        assert dashboard.next_text(
            {"label": "x", "argv": ["invariant", "doctor", "a b"]}
        ) == "invariant doctor 'a b'"

    def test_a_nested_hosts_status(self):
        assert dashboard.host_status({"alive": True, "attention": None}) == "alive"
        assert dashboard.host_status({"alive": False, "attention": {}}) == "gone"
        assert dashboard.host_status(
            {"alive": None, "attention": {"waiting": {"kind": "question"}}}
        ) == "pid unknown, waiting: question"

    def test_stages_of_the_other_kinds(self):
        assert dashboard.stage_text({"kind": "host"}) == "orphan host"
        assert dashboard.stage_text({"kind": "worktree"}) == "leaked worktree"
        assert dashboard.stage_text({"kind": "error"}) == "unreadable"
        assert dashboard.stage_text({"kind": "session", "stage": "early", "pipeline": None}) == "early"


# -- actions: what a row would run ------------------------------------------


class TestSteps:
    def test_a_row_with_no_recommendation_has_no_step(self):
        assert step_for({"recommendation": None}) is None

    def test_a_step_is_the_recommendation_exactly(self, machine_world):
        row = _row(_tick(machine_world), session_id="g-rev")
        step = step_for(row)
        assert step.kind == RUN
        assert step.argv == ("invariant", "session", "merge", "--id", "g-rev")
        assert step.cwd == row["recommendation"]["cwd"]
        assert step.session_name == "rev-g-rev"
        assert step.subject == "rev-g-rev  (reviewed)"

    def test_the_confirm_lines_show_argv_cwd_and_env(self):
        step = run_step(env={"INVARIANT_SESSION_ID": "s-1"})
        lines = confirm_lines(step, None)
        assert f"run   {shlex.join(step.argv)}" in lines
        assert f"in    {step.cwd}" in lines
        assert "env   INVARIANT_SESSION_ID=s-1" in lines
        assert any("host markers" in line for line in lines)

    def test_a_terminal_steps_lines_show_the_launch(self):
        step = run_step(kind=TERMINAL, argv=("invariant", "session", "resume", "--id", "s-1"))
        lines = confirm_lines(step, "cd /x && env claude --name s")
        assert "env   (nothing added)" in lines
        assert "opens a new Terminal window running:" in lines
        assert "  cd /x && env claude --name s" in lines

    def test_refusals(self, tmp_path):
        waiting = {"signals": [{"kind": "waiting", "alert": True}]}
        assert "never answers" in refusal(run_step(), waiting)
        assert refusal(run_step(kind=INFO, argv=()), waiting) is None
        assert "only invariant commands" in refusal(run_step(argv=("rm", "-rf", "/")), {})
        assert "only invariant commands" in refusal(run_step(argv=()), {})
        assert "does not exist" in refusal(run_step(cwd=str(tmp_path / "gone")), {})
        assert "does not exist" in refusal(run_step(cwd=None), {})
        assert "unknown kind" in refusal(run_step(kind="launch-missiles"), {})
        assert refusal(run_step(), {"signals": []}) is None

    def test_the_child_environment(self):
        env = child_env(
            {"PATH": "/bin", "CLAUDECODE": "1", "INVARIANT_SESSION_ID": "mine"},
            {"INVARIANT_SESSION_ID": "theirs"},
            strip=lambda e: {k: v for k, v in e.items() if k != "CLAUDECODE"},
        )
        assert env == {"PATH": "/bin", "INVARIANT_SESSION_ID": "theirs"}
        assert "INVARIANT_SESSION_ID" not in child_env(
            {"INVARIANT_SESSION_ID": "mine"}, {}, strip=dict
        )

    def test_a_terminal_request_names_the_session_and_carries_the_command(self):
        name, note = terminal_request(
            run_step(kind=TERMINAL, argv=("invariant", "session", "resume", "--id", "s-1"),
                     session_name="the one/with spaces")
        )
        assert name == "the-one-with-spaces"
        assert "invariant session resume --id s-1" in note
        name, _ = terminal_request(run_step(session_name=None, session_id="s-9"))
        assert name == "s-9"
        name, _ = terminal_request(run_step(session_name="///", session_id=None))
        assert name == "invariant-session"


class TestActions:
    def test_run_calls_the_runner_once_with_the_exact_argv(self):
        runner = RecordingRunner(output="merged")
        step = run_step(env={"INVARIANT_SESSION_ID": "s-1"})
        outcome = _actions(runner).perform(step)
        assert outcome == Outcome(ok=True, text="merged", status=0)
        (argv, cwd, env), = runner.calls
        assert argv == step.argv and cwd == step.cwd
        assert env == {"PATH": "/usr/bin", "INVARIANT_SESSION_ID": "s-1"}

    def test_a_failed_run_is_not_ok(self):
        outcome = _actions(RecordingRunner(status=1, output="refused")).perform(run_step())
        assert not outcome.ok and outcome.status == 1 and outcome.text == "refused"

    def test_a_runner_that_cannot_start_is_an_outcome_not_a_crash(self):
        def runner(argv, cwd, env):
            raise FileNotFoundError("no invariant on PATH")

        outcome = _actions(runner).perform(run_step())
        assert not outcome.ok and "no invariant on PATH" in outcome.text

    def test_terminal_goes_to_the_terminal_seam(self):
        asked = []
        actions = _actions(terminal=lambda s: asked.append(s) or "opened", preview=lambda s: "line")
        step = run_step(kind=TERMINAL)
        assert actions.perform(step) == Outcome(ok=True, text="opened")
        assert asked == [step]
        assert "  line" in actions.describe(step)

    def test_a_terminal_that_fails_is_an_outcome(self):
        def broken(step):
            raise InvariantError("no window")

        assert _actions(terminal=broken).perform(run_step(kind=TERMINAL)) == Outcome(
            ok=False, text="no window"
        )

    def test_a_preview_that_fails_says_why(self):
        def broken(step):
            raise InvariantError("too long")

        lines = _actions(terminal=lambda s: "", preview=broken).describe(run_step(kind=TERMINAL))
        assert "  (cannot preview the launch: too long)" in lines

    def test_without_a_terminal_seam_a_terminal_step_is_refused(self):
        assert _actions().perform(run_step(kind=TERMINAL)) == Outcome(
            ok=False, text="no terminal launcher is available here"
        )

    def test_info_has_no_action(self):
        with pytest.raises(ValueError):
            _actions().perform(run_step(kind=INFO, argv=()))

    def test_the_real_runner_runs_without_a_shell_and_without_stdin(self, tmp_path):
        code, output = run_subprocess(
            (sys.executable, "-c", "import sys; print('hi'); print(repr(sys.stdin.read()))"),
            str(tmp_path),
            dict(os.environ),
        )
        assert code == 0
        assert output.split() == ["hi", "''"]

    def test_the_real_runner_reports_stderr_too(self, tmp_path):
        code, output = run_subprocess(
            (sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(3)"),
            str(tmp_path),
            dict(os.environ),
        )
        assert code == 3 and "bad" in output

    def test_a_runner_that_times_out_is_an_outcome(self, monkeypatch):
        def slow(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], 1)

        monkeypatch.setattr(actions_module.subprocess, "run", slow)
        outcome = _actions(run_subprocess).perform(run_step())
        assert not outcome.ok and "timed out" in outcome.text


# -- the app ------------------------------------------------------------------


def make_app(world, fresh=True, actions=None):
    if fresh:
        _tick(world)
    else:
        _tick(world)
        _age_snapshot(60)
    scan, calls = _scan(world)
    loads = []

    def load():
        loads.append(1)
        return dashboard.load(oracle_home(), time.time(), scan)

    app = tui.OracleApp(load, actions or _actions(cli_main._oracle_runner()))
    return app, calls, loads


class TestTheApp:
    def test_rows_render_from_the_snapshot(self, machine_world):
        app, calls, _ = make_app(machine_world)

        async def script(pilot):
            table = app.query_one("#rows")
            assert table.row_count == len(app.lines) > 0
            assert calls == []
            banner = app.query_one("#banner")
            assert not banner.display
            key = row_key_for(app, session_id="a-live")
            cells = table.get_row(key)
            assert "live-a-live" in str(cells[1])
            assert str(cells[3]) == "ALERT"
            assert cells[3].style == tui.TONE_STYLES[dashboard.RED]
            assert cells[1].style == tui.TONE_STYLES[dashboard.RED]
            assert "snapshot" in str(app.query_one("#source").content)

        drive(app, script)

    def test_a_stale_snapshot_shows_the_banner_and_scans(self, machine_world):
        app, calls, _ = make_app(machine_world, fresh=False)

        async def script(pilot):
            assert calls == [1]
            banner = app.query_one("#banner")
            assert banner.display
            assert "watcher not running" in str(banner.content)
            assert app.query_one("#rows").row_count == len(app.lines) > 0
            assert "scanned in-process" in str(app.query_one("#source").content)

        drive(app, script)

    def test_three_projects_and_five_sessions_show_within_five_seconds(self, machine_world):
        started = time.perf_counter()
        app, _, _ = make_app(machine_world, fresh=False)
        shown = {}

        async def script(pilot):
            shown["elapsed"] = time.perf_counter() - started
            sessions = {
                line.row["session_id"] for line in app.lines.values()
                if line.row["kind"] == "session"
            }
            shown["sessions"] = sessions
            shown["projects"] = {
                line.project for line in app.lines.values() if not line.nested
            }

        drive(app, script)
        print(f"\noracle tui: first render {shown['elapsed']:.2f}s")
        assert shown["projects"] == {"alpha", "beta", "gamma"}
        assert len(shown["sessions"]) == 5
        assert shown["elapsed"] < 5.0

    def test_the_action_key_opens_the_confirm_modal_with_argv_and_cwd(self, machine_world):
        app, _, _ = make_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, tui.ConfirmScreen)
            text = "\n".join(screen.lines)
            assert "run   invariant session merge --id g-rev" in text
            assert f"in    {machine_world['gamma'].root}" in text
            assert "invariant session merge --id g-rev" in str(
                screen.query_one("#command").content
            )

        drive(app, script)

    @pytest.mark.parametrize("key", ["n", "escape"])
    def test_cancel_runs_nothing(self, machine_world, _no_real_oracle_runner, key):
        app, _, _ = make_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, tui.ConfirmScreen)
            await pilot.press(key)
            await settle(app, pilot)
            assert not isinstance(app.screen, tui.ConfirmScreen)

        drive(app, script)
        assert _no_real_oracle_runner.calls == []

    def test_the_cancel_button_runs_nothing(self, machine_world, _no_real_oracle_runner):
        app, _, _ = make_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.click("#cancel")
            await settle(app, pilot)

        drive(app, script)
        assert _no_real_oracle_runner.calls == []

    def test_confirm_runs_the_argv_exactly_once_and_shows_the_output(
        self, machine_world, _no_real_oracle_runner
    ):
        _no_real_oracle_runner.output = "merged rev-g-rev"
        app, _, loads = make_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.press("y")
            await settle(app, pilot)
            screen = app.screen
            assert isinstance(screen, tui.OutputScreen)
            assert "merged rev-g-rev" in str(screen.query_one("#output").content)
            assert "exit 0" in str(screen.query_one("#status").content)
            await pilot.press("escape")
            await settle(app, pilot)
            assert not isinstance(app.screen, tui.OutputScreen)

        drive(app, script)
        (argv, cwd, env), = _no_real_oracle_runner.calls
        assert argv == ("invariant", "session", "merge", "--id", "g-rev")
        assert cwd == str(machine_world["gamma"].root)
        assert "CLAUDECODE" not in env
        assert len(loads) >= 2, "a finished action refreshes the table"

    def test_the_confirm_button_runs_it(self, machine_world, _no_real_oracle_runner):
        app, _, _ = make_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            await pilot.press("enter")
            await pilot.pause()
            await pilot.click("#confirm")
            await settle(app, pilot)

        drive(app, script)
        assert len(_no_real_oracle_runner.calls) == 1

    def test_a_terminal_step_goes_to_the_launcher_seam(
        self, machine_world, _no_real_oracle_runner, _no_real_terminal_launch,
        _no_real_successor_watch, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(launch, "launch_dir", lambda: tmp_path / "launch")
        monkeypatch.setattr(launch, "host_platform", lambda: "darwin")
        monkeypatch.setattr(launch, "resolve_claude", lambda: "/opt/bin/claude")
        _no_real_successor_watch.pid = 4242
        app, _, _ = make_app(machine_world, actions=cli_main._oracle_actions())

        async def script(pilot):
            await select(app, pilot, session_id="a-parked")
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, tui.ConfirmScreen)
            text = "\n".join(screen.lines)
            assert "run   invariant session resume --id a-parked" in text
            assert "opens a new Terminal window running:" in text
            assert "--remote-control parked-a-parked" in text
            await pilot.press("y")
            await settle(app, pilot)
            assert "pid 4242" in str(app.screen.query_one("#output").content)

        drive(app, script)
        assert _no_real_oracle_runner.calls == []
        (plan,) = _no_real_terminal_launch.plans
        assert plan.cwd == str(machine_world["alpha"].root)
        assert plan.name == "parked-a-parked"
        assert "invariant session resume --id a-parked" in plan.argv[-1]

    def test_info_shows_text_and_has_no_action(self, machine_world, _no_real_oracle_runner):
        app, _, _ = make_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="a-live")
            await pilot.press("enter")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, tui.InfoScreen)
            assert "never does" in str(screen.query_one("#text").content)
            assert not screen.query("#confirm")
            await pilot.press("y")
            await pilot.press("escape")
            await settle(app, pilot)
            assert not isinstance(app.screen, tui.InfoScreen)

        drive(app, script)
        assert _no_real_oracle_runner.calls == []

    def test_a_refused_step_is_shown_not_run(self, machine_world, _no_real_oracle_runner):
        _tick(machine_world)
        path = oracle_home() / SNAPSHOT_FILE
        document = json.loads(path.read_text("utf-8"))
        row = _row(document, session_id="g-rev")
        row["recommendation"]["argv"] = ["rm", "-rf", "/"]
        path.write_text(json.dumps(document), "utf-8")
        scan, _ = _scan(machine_world)
        app = tui.OracleApp(
            lambda: dashboard.load(oracle_home(), time.time(), scan),
            _actions(cli_main._oracle_runner()),
        )

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, tui.InfoScreen)
            assert "only invariant commands" in str(app.screen.query_one("#text").content)

        drive(app, script)
        assert _no_real_oracle_runner.calls == []

    def test_a_row_with_nothing_recommended_opens_nothing(self, machine_world):
        app, _, _ = make_app(machine_world)

        async def script(pilot):
            target = next(
                key for key, line in app.lines.items()
                if line.row.get("recommendation") is None
            )
            table = app.query_one("#rows")
            table.move_cursor(row=table.get_row_index(target))
            await pilot.press("a")
            await pilot.pause()
            assert app.screen is app.screen_stack[0]

        document = _tick(machine_world)
        row = _row(document, session_id="b-dead")
        row["recommendation"] = None
        path = oracle_home() / SNAPSHOT_FILE
        path.write_text(json.dumps(document), "utf-8")
        drive(app, script)

    def test_a_nested_host_line_acts_for_its_session(self, machine_world):
        app, _, _ = make_app(machine_world)

        async def script(pilot):
            target = next(key for key, line in app.lines.items() if line.nested)
            table = app.query_one("#rows")
            table.move_cursor(row=table.get_row_index(target))
            await pilot.press("a")
            await pilot.pause()
            assert isinstance(app.screen, (tui.InfoScreen, tui.ConfirmScreen))

        drive(app, script)

    def test_refresh_reloads_and_keeps_the_cursor(self, machine_world):
        app, _, loads = make_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            before = app.current_key()
            await pilot.press("r")
            await settle(app, pilot)
            assert len(loads) == 2
            assert app.current_key() == before

        drive(app, script)

    def test_an_empty_machine_has_no_current_row(self, tmp_path):
        app = tui.OracleApp(
            lambda: dashboard.load(tmp_path, time.time(), lambda: {"projects": []}),
            _actions(),
        )

        async def script(pilot):
            assert app.current_key() is None
            await pilot.press("a")
            await pilot.pause()
            assert app.screen is app.screen_stack[0]

        drive(app, script)

    def test_a_load_that_fails_is_shown_and_the_app_lives(self):
        def load():
            raise InvariantError("the index is broken")

        app = tui.OracleApp(load, _actions())

        async def script(pilot):
            banner = app.query_one("#banner")
            assert banner.display and "the index is broken" in str(banner.content)

        drive(app, script)

    def test_the_refresh_follows_the_snapshots_interval(self, machine_world):
        app, _, _ = make_app(machine_world)

        async def script(pilot):
            assert app.interval == 5.0
            assert app.refresh_timer is not None

        drive(app, script)

    def test_quit(self, machine_world):
        app, _, _ = make_app(machine_world)

        async def script(pilot):
            await pilot.press("q")

        drive(app, script)
        assert app.return_code in (0, None)


# -- the CLI -------------------------------------------------------------------


class TestTheCommand:
    def test_oracle_tui_runs_the_app_with_the_cli_seams(self, machine_world, monkeypatch):
        ran = []
        monkeypatch.setattr(tui.OracleApp, "run", lambda self: ran.append(self))
        monkeypatch.setattr(
            cli_main, "_oracle_cache", lambda: ScanCache(probe=machine_world["probe"])
        )
        assert main(["oracle", "tui"]) == 0
        (app,) = ran
        loaded = app.loader()
        assert loaded.source == dashboard.SOURCE_SCAN
        assert {p["project"]["name"] for p in loaded.document["projects"]} == {
            "alpha", "beta", "gamma"
        }
        assert loaded.document["home"] == str(oracle_home())
        assert isinstance(app.actions, Actions)

    def test_a_missing_textual_is_a_clean_usage_error(self, monkeypatch, capsys):
        monkeypatch.setitem(sys.modules, "textual", None)
        monkeypatch.delitem(sys.modules, "invariant.oracle.tui", raising=False)
        monkeypatch.delattr("invariant.oracle.tui", raising=False)
        assert main(["oracle", "tui"]) == UsageError.exit_code
        err = capsys.readouterr().err
        assert "pip install 'invariant[oracle]'" in err
        assert "Traceback" not in err

    def test_another_import_failure_is_not_disguised(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "invariant.oracle.tui", None)
        monkeypatch.delattr("invariant.oracle.tui", raising=False)
        with pytest.raises(ImportError, match="invariant.oracle.tui"):
            main(["oracle", "tui"])

    def test_the_suite_can_reach_no_real_runner_or_window(self):
        assert isinstance(cli_main._oracle_runner(), RecordingRunner)
        assert not isinstance(cli_main._handoff_launcher(), launch.TerminalLauncher)

    def test_the_real_seams(self):
        assert cli_main._real_oracle_runner() is run_subprocess


class TestTheTerminalSeam:
    STEP = run_step(
        kind=TERMINAL, argv=("invariant", "session", "resume", "--id", "s-1"),
        session_name="one-s-1",
    )

    @pytest.fixture(autouse=True)
    def _here(self, monkeypatch, tmp_path):
        monkeypatch.setattr(launch, "launch_dir", lambda: tmp_path / "launch")
        monkeypatch.setattr(launch, "resolve_claude", lambda: "/opt/bin/claude")
        monkeypatch.setattr(launch, "host_platform", lambda: "darwin")
        return tmp_path / "launch"

    def test_a_launch_that_starts_a_successor(
        self, _no_real_terminal_launch, _no_real_successor_watch, _here
    ):
        _no_real_successor_watch.pid = 77
        said = cli_main._oracle_terminal(self.STEP)
        assert "pid 77" in said and "one-s-1" in said
        (plan,) = _no_real_terminal_launch.plans
        assert plan.argv[:5] == ("/opt/bin/claude", "--name", "one-s-1", "--remote-control", "one-s-1")
        assert plan.staged

    def test_a_launch_with_no_successor_fails_and_leaves_no_file(
        self, _no_real_terminal_launch, _here
    ):
        with pytest.raises(InvariantError, match="did not start a successor"):
            cli_main._oracle_terminal(self.STEP)
        assert not list(_here.glob("handoff-*.sh"))

    def test_not_on_macos_it_says_how_to_run_it_by_hand(self, monkeypatch, _no_real_terminal_launch):
        monkeypatch.setattr(launch, "host_platform", lambda: "linux")
        with pytest.raises(InvariantError, match="by hand") as caught:
            cli_main._oracle_terminal(self.STEP)
        assert "--remote-control one-s-1" in str(caught.value)
        assert _no_real_terminal_launch.plans == []

    def test_no_claude_on_path(self, monkeypatch, _no_real_terminal_launch):
        monkeypatch.setattr(launch, "resolve_claude", lambda: None)
        with pytest.raises(InvariantError, match="no claude"):
            cli_main._oracle_terminal(self.STEP)
        assert _no_real_terminal_launch.plans == []

    def test_the_preview_writes_nothing(self, _here):
        line = cli_main._oracle_actions().describe(self.STEP)
        assert any("--remote-control one-s-1" in x for x in line)
        assert not _here.exists() or not list(_here.iterdir())


def test_only_the_dashboard_module_imports_textual():
    """The core install stays PyYAML-only: Textual is reached from one module."""
    import ast

    package = Path(cli_main.__file__).resolve().parents[1]
    importers = set()
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text("utf-8"))
        for node in ast.walk(tree):
            names = (
                [a.name for a in node.names] if isinstance(node, ast.Import)
                else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0
                else []
            )
            if any(n.split(".")[0] in ("textual", "rich") for n in names):
                importers.add(path.relative_to(package).as_posix())
    assert importers == {"oracle/tui.py"}
