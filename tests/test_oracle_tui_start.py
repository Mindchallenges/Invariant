"""IDEA_030 increment 3: the dashboard's "New session" key (ADR-009 D16).

``n`` in ``invariant oracle tui`` opens a modal over the same two steps the
command and the phone run, a :class:`~invariant.oracle.startsession.Starter`
the CLI builds from ``_start_prepare`` and ``_start_launch``. Nothing is
launched until Start, and Start prepares again from a fresh read and refuses
a name that moved, as the page does.

Most tests hand the app a recording Starter. The ones that run the CLI's own
Starter go through the suite's launcher and successor seams
(``tests/conftest.py``), so no window opens, no process table is read and the
real ``~/.claude`` and ``~/.invariant`` are never touched.
"""

from __future__ import annotations

import dataclasses
import time

import pytest

pytest.importorskip("textual")

import test_oracle_scan
import test_oracle_start_session
from test_oracle_tui import _actions, drive, make_app, select, settle

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import InvariantError
from invariant.oracle import dashboard, startsession, tui
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import assess
from invariant.oracle.scan import ScanCache, scan_all

machine_world = test_oracle_scan.machine_world
project = test_oracle_start_session.project
macos_with_claude = test_oracle_start_session.macos_with_claude
successor = test_oracle_start_session.successor
_scan_without_a_probe = test_oracle_start_session._scan_without_a_probe


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    """Ask again at once on every edit, rather than after the typing pause."""
    monkeypatch.setattr(tui, "START_DEBOUNCE", 0.01)


class FakeStarter:
    """A Starter that records what it was asked, and launches nothing."""

    def __init__(self) -> None:
        self.prepared: list[tuple] = []
        self.launched: list[dict] = []
        self.taken: set[str] = set()
        self.refuse: str | None = None
        self.fail: BaseException | None = None

    def prepare(self, key, label, prompt, project):
        self.prepared.append((key, label, prompt, project))
        if self.refuse:
            raise InvariantError(self.refuse)
        name = (project.get("project") or {}).get("name") or key
        label = label if label is not None else "1026-0930"
        full = startsession.session_name(name, label, self.taken)
        argv = ["claude", "--name", full, "--remote-control", full, *([prompt] if prompt else [])]
        return {
            "project": {"key": key, "name": name, "root": f"/w/{name}"},
            "name": full, "label": label, "prompt": prompt, "cwd": f"/w/{name}",
            "argv": argv, "by_hand": f"cd /w/{name} && " + " ".join(argv),
            "facts": startsession.start_facts(project, 3),
        }

    def launch(self, prepared):
        self.launched.append(prepared)
        if self.fail is not None:
            raise self.fail
        return f"  claude is running as {prepared['name']} (pid 4242)"

    @property
    def starter(self) -> startsession.Starter:
        return startsession.Starter(prepare=self.prepare, launch=self.launch)


def start_app(world, fake=None, **kwargs):
    app, _, loads = make_app(world)
    fake = fake or FakeStarter()
    app.starter = fake.starter
    for name, value in kwargs.items():
        setattr(app, name, value)
    return app, fake, loads


def text_of(screen, selector) -> str:
    return str(screen.query_one(selector).content)


async def open_start(app, pilot):
    await pilot.press("n")
    await settle(app, pilot)
    assert isinstance(app.screen, tui.StartScreen)
    return app.screen


async def type_into(app, pilot, selector, text):
    field = app.screen.query_one(selector)
    field.value = text
    await settle(app, pilot)


class TestTheKey:
    def test_n_is_bound_and_named_in_the_footer(self):
        (binding,) = [b for b in tui.OracleApp.BINDINGS if b.key == "n"]
        assert binding.action == "new_session"
        assert binding.description == "new session"
        assert "``n``" in tui.__doc__

    def test_it_opens_the_modal_and_nothing_launches(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            screen = await open_start(app, pilot)
            names = [label for label, _ in screen.choices]
            assert names == ["alpha", "beta", "gamma"]
            assert screen.query_one("#start").disabled is False

        drive(app, script)
        assert fake.launched == []
        assert fake.prepared, "the modal asks the Starter what would start"

    def test_it_preselects_the_highlighted_rows_project(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            screen = await open_start(app, pilot)
            gamma = next(key for label, key in screen.choices if label == "gamma")
            assert screen.query_one("#project").value == gamma
            assert fake.prepared[-1][0] == gamma
            assert fake.prepared[-1][3]["project"]["name"] == "gamma"

        drive(app, script)

    def test_a_nested_host_line_preselects_its_project(self, machine_world):
        app, _, _ = start_app(machine_world)

        async def script(pilot):
            target = next(key for key, line in app.lines.items() if line.nested)
            table = app.query_one("#rows")
            table.move_cursor(row=table.get_row_index(target))
            await pilot.pause()
            screen = await open_start(app, pilot)
            assert screen.query_one("#project").value == app.lines[target].project_key

        drive(app, script)

    def test_with_no_row_the_first_project_is_offered(self):
        document = {"projects": [{"project": {"key": "k1", "name": "one"}, "rows": []},
                                 {"project": {"key": "k2", "name": "two"}, "rows": []}]}
        fake = FakeStarter()
        app = tui.OracleApp(lambda: dashboard.Loaded(document, dashboard.SOURCE_SCAN, 5.0),
                            _actions(), starter=fake.starter)

        async def script(pilot):
            screen = await open_start(app, pilot)
            assert screen.query_one("#project").value == "k1"

        drive(app, script)

    def test_no_registered_project_opens_nothing(self):
        app = tui.OracleApp(lambda: dashboard.Loaded({"projects": []}, dashboard.SOURCE_SCAN, 5.0),
                            _actions(), starter=FakeStarter().starter)
        said = []

        async def script(pilot):
            app.notify = lambda message, **_: said.append(message)
            await pilot.press("n")
            await settle(app, pilot)
            assert not isinstance(app.screen, tui.StartScreen)

        drive(app, script)
        assert said == ["no registered project to start a session in"]

    def test_an_app_without_a_starter_says_so(self, machine_world):
        app, _, _ = make_app(machine_world)
        said = []

        async def script(pilot):
            app.notify = lambda message, **_: said.append(message)
            await pilot.press("n")
            await settle(app, pilot)
            assert not isinstance(app.screen, tui.StartScreen)

        drive(app, script)
        assert said == ["starting a session is not available here"]


class TestTheModal:
    def test_it_shows_the_name_facts_command_and_first_message(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            await open_start(app, pilot)
            await type_into(app, pilot, "#prompt", "look at the flaky test")
            await type_into(app, pilot, "#label", "Flaky")
            screen = app.screen
            assert text_of(screen, "#name") == "name: gamma-flaky"
            facts = text_of(screen, "#facts")
            assert "already open in this project" in facts or "no Claude window" in facts
            assert "the main checkout has 3 uncommitted changes" in facts
            command = text_of(screen, "#command")
            assert "claude --name gamma-flaky --remote-control gamma-flaky" in command
            assert "look at the flaky test" in text_of(screen, "#message")
            assert "Claude's default permission mode" in text_of(screen, "#mode")

        drive(app, script)
        assert fake.prepared[-1][1:3] == ("Flaky", "look at the flaky test")
        assert fake.launched == []

    def test_a_blank_label_and_message_are_the_time_and_none(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            screen = await open_start(app, pilot)
            await type_into(app, pilot, "#label", "   ")
            assert text_of(screen, "#name").endswith("-1026-0930")
            assert text_of(screen, "#message") == "first message: none"

        drive(app, script)
        assert fake.prepared[-1][1:3] == (None, None)

    def test_choosing_another_project_asks_again(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            screen = await open_start(app, pilot)
            beta = next(key for label, key in screen.choices if label == "beta")
            screen.query_one("#project").value = beta
            await settle(app, pilot)
            assert text_of(screen, "#name").startswith("name: beta-")

        drive(app, script)
        assert fake.prepared[-1][3]["project"]["name"] == "beta"

    def test_a_refusal_is_shown_and_start_is_disabled(self, machine_world):
        fake = FakeStarter()
        fake.refuse = "a label needs a letter or digit (alphanumeric)"
        app, _, _ = start_app(machine_world, fake)

        async def script(pilot):
            screen = await open_start(app, pilot)
            assert "alphanumeric" in text_of(screen, "#refused")
            assert screen.query_one("#start").disabled
            screen.action_start()
            await settle(app, pilot)
            assert isinstance(app.screen, tui.StartScreen)

        drive(app, script)
        assert fake.launched == []

    def test_an_unexpected_error_while_asking_is_shown(self, machine_world):
        fake = FakeStarter()
        app, _, _ = start_app(machine_world, fake)

        def broken(*_):
            raise OSError("git is gone")

        app.starter = startsession.Starter(prepare=broken, launch=fake.launch)

        async def script(pilot):
            screen = await open_start(app, pilot)
            assert text_of(screen, "#refused") == "will not start: OSError: git is gone"
            assert text_of(screen, "#name") == ""

        drive(app, script)

    def test_the_dashboards_keys_type_into_the_fields(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            await open_start(app, pilot)
            await pilot.press("q", "n", "a", "r", "y")
            await settle(app, pilot)
            assert isinstance(app.screen, tui.StartScreen)
            assert app.screen.query_one("#prompt").value == "qnary"

        drive(app, script)
        assert fake.launched == []
        assert fake.prepared[-1][2] == "qnary"

    @pytest.mark.parametrize("how", ["escape", "button"])
    def test_cancel_starts_nothing(self, machine_world, how):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            await open_start(app, pilot)
            if how == "escape":
                await pilot.press("escape")
            else:
                await pilot.click("#cancel")
            await settle(app, pilot)
            assert not isinstance(app.screen, tui.StartScreen)

        drive(app, script)
        assert fake.launched == []

    def test_the_answer_of_an_older_edit_is_dropped(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            screen = await open_start(app, pilot)
            stale = dict(fake.prepare(screen.selected_key(), "old", None, {}), name="stale")
            screen.show_answer(("elsewhere", "old", ""), stale, None)
            assert text_of(screen, "#name") != "name: stale"

        drive(app, script)

    def test_the_dashboard_keeps_refreshing_under_the_modal(self, machine_world):
        app, _, loads = start_app(machine_world)
        load = app.loader
        app.loader = lambda: dataclasses.replace(load(), interval=0.2)

        async def script(pilot):
            await open_start(app, pilot)
            before = len(loads)
            for _ in range(50):  # up to 5 s under a loaded machine; 0.2 s when idle
                await pilot.pause(0.1)
                if len(loads) > before + 1:
                    break
            assert len(loads) > before + 1, "the timer kept reading under the modal"
            assert isinstance(app.screen, tui.StartScreen)

        drive(app, script)


class TestStart:
    def test_start_prepares_again_launches_once_and_shows_the_name(self, machine_world):
        app, fake, loads = start_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            await open_start(app, pilot)
            await type_into(app, pilot, "#label", "x")
            asked = len(fake.prepared)
            before = len(loads)
            await pilot.click("#start")
            await settle(app, pilot)
            screen = app.screen
            assert isinstance(screen, tui.OutputScreen)
            assert "started as gamma-x" in text_of(screen, "#output")
            assert "pid 4242" in text_of(screen, "#output")
            assert "done" in text_of(screen, "#status")
            assert len(fake.prepared) == asked + 1, "prepared again at Start"
            assert len(loads) >= before + 2, "a fresh read, then the table refreshes"

        drive(app, script)
        (launched,) = fake.launched
        assert launched["name"] == "gamma-x" and launched["label"] == "x"

    def test_the_label_the_modal_resolved_is_the_one_started(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            await open_start(app, pilot)
            await pilot.click("#start")
            await settle(app, pilot)

        drive(app, script)
        assert fake.prepared[-1][1] == "1026-0930"
        assert len(fake.launched) == 1

    def test_a_name_taken_since_the_confirm_starts_nothing(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            await select(app, pilot, session_id="g-rev")
            await open_start(app, pilot)
            await type_into(app, pilot, "#label", "x")
            fake.taken.add("gamma-x")
            await pilot.click("#start")
            await settle(app, pilot)
            screen = app.screen
            assert isinstance(screen, tui.OutputScreen)
            assert "failed" in text_of(screen, "#status")
            output = text_of(screen, "#output")
            assert output.startswith("did not start: the window would now be named gamma-x-2")
            assert "nothing ran" in output

        drive(app, script)
        assert fake.launched == []

    def test_a_refusal_at_start_starts_nothing(self, machine_world):
        app, fake, _ = start_app(machine_world)

        async def script(pilot):
            await open_start(app, pilot)
            fake.refuse = "demo is registered, but its root no longer carries a manifest"
            app.screen.action_start()
            await settle(app, pilot)
            assert "no longer carries a manifest; nothing ran" in text_of(app.screen, "#output")

        drive(app, script)
        assert fake.launched == []

    def test_a_window_that_did_not_open_says_why(self, machine_world):
        fake = FakeStarter()
        fake.fail = InvariantError("the new window did not start a successor; run it by hand")
        app, _, _ = start_app(machine_world, fake)

        async def script(pilot):
            await open_start(app, pilot)
            await pilot.click("#start")
            await settle(app, pilot)
            output = text_of(app.screen, "#output")
            assert output == "did not start: the new window did not start a successor; run it by hand"
            assert "failed" in text_of(app.screen, "#status")

        drive(app, script)
        assert len(fake.launched) == 1

    def test_an_unexpected_failure_is_shown_and_the_dashboard_goes_on(self, machine_world):
        fake = FakeStarter()
        fake.fail = RuntimeError("boom")
        app, _, loads = start_app(machine_world, fake)

        async def script(pilot):
            await open_start(app, pilot)
            await pilot.click("#start")
            await settle(app, pilot)
            assert text_of(app.screen, "#output") == "did not start: RuntimeError: boom"
            before = len(loads)
            await pilot.press("escape")
            await pilot.press("r")
            await settle(app, pilot)
            assert len(loads) > before

        drive(app, script)

    def test_a_read_that_fails_at_start_starts_nothing(self, machine_world):
        app, fake, _ = start_app(machine_world)

        def broken():
            raise OSError("disk gone")

        async def script(pilot):
            await open_start(app, pilot)
            app.loader = broken
            await pilot.click("#start")
            await settle(app, pilot)
            assert text_of(app.screen, "#output") == (
                "did not start: could not read the machine: OSError: disk gone; nothing ran")

        drive(app, script)
        assert fake.launched == []


# -- the CLI's own Starter, behind the suite's seams --------------------------


def _demo_loader():
    def scan():
        return assess(scan_all(ProjectIndex(oracle_home()), cache=ScanCache()), time.time())

    return lambda: dashboard.load(oracle_home(), time.time(), scan)


class TestTheCommandsOwnPath:
    def test_oracle_tui_hands_the_app_the_clis_starter(self, project, monkeypatch):
        ran = []
        monkeypatch.setattr(tui.OracleApp, "run", lambda self: ran.append(self))
        assert main(["oracle", "tui"]) == 0
        (app,) = ran
        assert isinstance(app.starter, startsession.Starter)
        prepared = app.starter.prepare("demo", "x", None, {})
        assert prepared["name"] == "demo-x"

    def test_start_opens_one_window_through_the_launcher_seam(
        self, project, macos_with_claude, successor, _no_real_terminal_launch,
    ):
        app = tui.OracleApp(_demo_loader(), _actions(),
                            starter=cli_main._oracle_starter(cli_main._project_index()))

        async def script(pilot):
            screen = await open_start(app, pilot)
            assert [label for label, _ in screen.choices] == ["demo"]
            await type_into(app, pilot, "#label", "Flaky")
            await type_into(app, pilot, "#prompt", "look at it")
            assert text_of(screen, "#name") == "name: demo-flaky"
            assert "--remote-control demo-flaky" in text_of(screen, "#command")
            assert _no_real_terminal_launch.plans == []
            await pilot.click("#start")
            await settle(app, pilot)
            assert "started as demo-flaky" in text_of(app.screen, "#output")

        drive(app, script)
        (plan,) = _no_real_terminal_launch.plans
        assert plan.name == "demo-flaky"
        assert plan.argv[1:5] == ("--name", "demo-flaky", "--remote-control", "demo-flaky")
        assert plan.argv[-1] == "look at it"
        assert not any("permission" in part for part in plan.argv)

    @pytest.mark.parametrize("field, value, said", [
        ("#label", "!!!", "alphanumeric"),
        ("#prompt", "--dangerously-skip-permissions", "flag"),
    ])
    def test_the_commands_refusals_are_shown(self, project, macos_with_claude, field, value,
                                             said, _no_real_terminal_launch):
        app = tui.OracleApp(_demo_loader(), _actions(),
                            starter=cli_main._oracle_starter(cli_main._project_index()))

        async def script(pilot):
            screen = await open_start(app, pilot)
            await type_into(app, pilot, field, value)
            assert said in text_of(screen, "#refused")
            assert screen.query_one("#start").disabled

        drive(app, script)
        assert _no_real_terminal_launch.plans == []
