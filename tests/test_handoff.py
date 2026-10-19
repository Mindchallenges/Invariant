"""`invariant session handoff` — one command that hands the work on.

Four layers are exercised, each where it lives: the name derived from the
note and the note's vetting (`domains/session`), the record it leaves
(`domains/session`), the launch plan and its quoting (`hosts/claude_code_launch`)
and the command that joins them (`cli/main`), plus the greeting that surfaces a
recorded note when nobody is bound.

**No test here opens a window.** The CLI resolves its launcher through one
function, `_handoff_launcher`, and an autouse fixture replaces it with a
recorder for every test in this file — so a test that forgets to inject one
records a call instead of driving Terminal. The real launcher is covered by
replacing the one `subprocess.run` it makes.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from invariant.cli.main import main
from invariant.core.errors import GateFailure, InvariantError, UsageError
from invariant.core.manifest import Manifest
from invariant.core.store import JsonStore
from invariant.domains.session import (
    HANDOFF_NAME_CHARS,
    HANDOFF_NOTE_MAX,
    SessionDomain,
    handoff_name,
)
from invariant.hosts import claude_code_launch as launch

#: A note carrying every character class the quoting has to survive: spaces,
#: colons, a middot, an arrow, an apostrophe, double quotes, a backslash, a
#: dollar sign, a backtick and a glob. Synthetic — names no real item.
NASTY = (
    "done: P1.10 · next: P1.11 BUG_EXAMPLE_KEY_LOST critical -> bugprep · "
    "carry: it's \"quoted\" back\\slash $HOME `tick` *glob* ; && |"
)


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text

    def isatty(self) -> bool:
        return False


class RecordingLauncher:
    def __init__(self) -> None:
        self.plans: list[launch.LaunchPlan] = []

    def launch(self, plan: launch.LaunchPlan) -> None:
        self.plans.append(plan)


@pytest.fixture(autouse=True)
def launcher(monkeypatch):
    """Every test in this file gets a recorder in place of the real launcher."""
    recorder = RecordingLauncher()
    monkeypatch.setattr("invariant.cli.main._handoff_launcher", lambda: recorder)
    return recorder


@pytest.fixture
def macos_with_claude(monkeypatch, tmp_path):
    """A host that can launch: macOS, and a `claude` on PATH."""
    monkeypatch.setattr(launch, "host_platform", lambda: "darwin")
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(launch, "resolve_claude", lambda: str(fake))
    return fake


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "master"],
        ["git", "config", "user.email", "t@example.invalid"],
        ["git", "config", "user.name", "T"],
    ):
        subprocess.run(argv, cwd=root, check=True)
    (root / "docs" / "architecture").mkdir(parents=True)
    (root / "docs" / "architecture" / "overview.md").write_text(
        "# Overview\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    assert main(["init", "--name", "demo"]) == 0
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "manifest"], cwd=root, check=True)
    return root


def run(*argv) -> int:
    return main(list(argv))


def last_json(capsys) -> dict:
    text = capsys.readouterr().out
    lines = text.splitlines()
    for start in range(len(lines) - 1, -1, -1):
        if lines[start].lstrip().startswith("{"):
            try:
                return json.loads("\n".join(lines[start:]))
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"no JSON document in output:\n{text}")


def domain(root: Path, clock=None) -> SessionDomain:
    manifest = Manifest.load(root)
    kwargs = {"clock": clock} if clock else {}
    return SessionDomain(manifest, JsonStore(manifest.state_root), **kwargs)


def started(project, capsys, name="add-widget") -> dict:
    run("session", "start", name, "--json")
    return last_json(capsys)["session"]


# -- the name ----------------------------------------------------------


class TestTheNameComesFromTheNextItem:
    def test_the_documented_example(self):
        note = (
            "next: P1.11 BUG_PERLIB_MINTED_KEY_CLOBBERED_AFTER_BINDING "
            "critical -> bugprep"
        )
        assert handoff_name(note) == "p1-11-perlib-minted-key-clobbered"

    def test_the_cli_doc_example(self):
        note = "next: P1.11 BUG_SOME_ITEM_NAME critical -> bugprep"
        assert handoff_name(note) == "p1-11-some-item-name-critical"

    def test_a_given_name_is_normalised_not_derived(self):
        assert handoff_name("done: all", name="Next Thing") == "next-thing"
        with pytest.raises(UsageError):
            handoff_name("next: x", name="---")

    def test_it_is_deterministic(self):
        assert handoff_name(NASTY) == handoff_name(NASTY) == "p1-11-example-key-lost-critical"

    def test_the_item_ends_at_a_separator(self):
        assert handoff_name("next: wire toolbar · carry: soak") == "wire-toolbar"
        assert handoff_name("next: wire toolbar | then: x") == "wire-toolbar"
        assert handoff_name("next: wire toolbar; later") == "wire-toolbar"
        assert handoff_name("next: wire toolbar → review") == "wire-toolbar"
        assert handoff_name("next: wire toolbar carry: soak") == "wire-toolbar"

    def test_an_idea_prefix_is_dropped_too(self):
        assert handoff_name("next: IDEA_SHARE_SHEET") == "share-sheet"

    def test_the_label_is_case_insensitive_and_found_mid_note(self):
        assert handoff_name("done: a · NEXT: Ship It") == "ship-it"

    def test_the_length_is_bounded_at_a_word(self):
        words = " ".join(["abcdefghij"] * 5)
        name = handoff_name(f"next: {words}")
        assert len(name) <= HANDOFF_NAME_CHARS
        assert not name.endswith("-")
        assert name == "abcdefghij-abcdefghij-abcdefghij-abcdefghij"

    def test_a_single_overlong_word_is_cut_rather_than_lost(self):
        name = handoff_name("next: " + "x" * 80)
        assert name == "x" * HANDOFF_NAME_CHARS

    def test_no_next_item_names_nothing(self):
        assert handoff_name("done: everything") is None
        assert handoff_name("next: · · ·") is None
        assert handoff_name("context: nothing") is None


# -- vetting and recording --------------------------------------------


class TestTheNoteIsVetted:
    def test_a_privacy_finding_is_refused_and_redacted(self, project):
        with pytest.raises(GateFailure) as caught:
            domain(project).vet_handoff_note("next: x · ping someone@example.org")
        message = str(caught.value)
        assert "emails" in message
        assert "someone@example.org" not in message

    def test_a_clock_time_is_refused(self, project):
        with pytest.raises(GateFailure):
            domain(project).vet_handoff_note("next: x · resumed at 14:05")

    def test_the_exemption_marker_cannot_ride_in_a_note(self, project):
        with pytest.raises(GateFailure) as caught:
            domain(project).vet_handoff_note(
                "next: x invariant: allow-privacy someone@example.org"
            )
        assert "allow-privacy" in str(caught.value)

    def test_an_empty_note_is_refused(self, project):
        with pytest.raises(UsageError):
            domain(project).vet_handoff_note("   ")

    def test_a_multi_line_note_is_refused(self, project):
        with pytest.raises(UsageError) as caught:
            domain(project).vet_handoff_note("next: a\nnext: b")
        assert "one line" in str(caught.value)

    def test_an_overlong_note_is_refused(self, project):
        with pytest.raises(UsageError):
            domain(project).vet_handoff_note("next: " + "a" * HANDOFF_NOTE_MAX)

    def test_a_clean_note_comes_back_stripped(self, project):
        assert domain(project).vet_handoff_note("  next: x  ") == "next: x"


class TestTheHandoffIsRecorded:
    def test_it_appends_to_the_record_and_writes_a_standstill_row(
        self, project, capsys
    ):
        session = started(project, capsys)
        sessions = domain(project)
        updated = sessions.record_handoff(session["id"], "next: ship it", "ship-it")

        assert updated.state == "registered"
        assert updated.handoffs[-1]["note"] == "next: ship it"
        assert updated.handoffs[-1]["name"] == "ship-it"
        assert updated.handoffs[-1]["state"] == "registered"
        row = updated.history[-1]
        assert row["from"] == row["to"] == "registered"
        assert row["handoff"] == "ship-it"
        # Persisted, not only returned.
        assert sessions.get(session["id"]).handoffs[-1]["name"] == "ship-it"

    def test_a_torn_down_session_can_hand_off_and_stays_deregistered(
        self, project, capsys
    ):
        session = started(project, capsys)
        sessions = domain(project)
        sessions.transition(session["id"], "merged", force=True, reason="fixture")
        sessions.teardown(session["id"])
        sessions.record_handoff(session["id"], "next: ship it", "ship-it")

        registry = sessions.store.read("registry.json", {"sessions": {}})
        assert session["id"] not in registry["sessions"]
        assert sessions.get(session["id"]).state == "torn_down"
        assert sessions.get(session["id"]).handoffs[-1]["state"] == "torn_down"

    def test_a_live_session_stays_registered(self, project, capsys):
        session = started(project, capsys)
        sessions = domain(project)
        sessions.record_handoff(session["id"], "next: ship it", "ship-it")
        registry = sessions.store.read("registry.json", {"sessions": {}})
        assert session["id"] in registry["sessions"]

    def test_the_record_refuses_an_unvetted_note(self, project, capsys):
        session = started(project, capsys)
        with pytest.raises(GateFailure):
            domain(project).record_handoff(
                session["id"], "next: x someone@example.org", "x"
            )
        assert domain(project).get(session["id"]).handoffs == []


class TestWhichSessionHandsOff:
    def test_the_most_recently_moved_session_here_any_state(self, project, capsys):
        ticks = iter(range(100, 200))
        sessions = domain(project, clock=lambda: float(next(ticks)))
        older = sessions.start("older-task")
        newer = sessions.start("newer-task")
        sessions.transition(newer.id, "abandoned", reason="fixture")

        picked = sessions.handoff_candidate(project)
        assert picked.id == newer.id
        assert older.id != newer.id

    def test_a_session_bound_to_this_host_wins(self, project):
        ticks = iter(range(100, 200))
        sessions = domain(project, clock=lambda: float(next(ticks)))
        mine = sessions.start("mine", host_session_id="host-aaaa-0000")
        sessions.start("theirs", host_session_id="host-bbbb-1111")

        assert sessions.handoff_candidate(project, "host-aaaa-0000").id == mine.id

    def test_an_earlier_binding_counts_as_this_host(self, project):
        ticks = iter(range(100, 200))
        sessions = domain(project, clock=lambda: float(next(ticks)))
        mine = sessions.start("mine", host_session_id="host-aaaa-0000")
        mine.hosts.append("host-cccc-2222")
        sessions._save(mine)
        sessions.start("theirs", host_session_id="host-bbbb-1111")

        assert sessions.handoff_candidate(project, "host-cccc-2222").id == mine.id

    def test_nothing_registered_here_is_none(self, tmp_path, project):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        assert domain(project).handoff_candidate(elsewhere) is None


class TestTheLatestHandoff:
    def test_the_newest_entry_across_sessions(self, project):
        ticks = iter(range(100, 200))
        sessions = domain(project, clock=lambda: float(next(ticks)))
        first = sessions.start("first")
        second = sessions.start("second")
        sessions.record_handoff(first.id, "next: one", "one")
        sessions.record_handoff(second.id, "next: two", "two")

        session, entry = sessions.latest_handoff(project)
        assert session.id == second.id
        assert entry["name"] == "two"

    def test_a_cold_handoff_is_not_offered(self, project):
        now = [100.0]
        sessions = domain(project, clock=lambda: now[0])
        first = sessions.start("first")
        sessions.record_handoff(first.id, "next: one", "one")
        now[0] += 8 * 86400  # past the default seven days

        assert sessions.latest_handoff(project) is None

    def test_nothing_recorded_is_none(self, project):
        assert domain(project).latest_handoff(project) is None

    def test_a_handoff_registered_elsewhere_is_not_offered_here(
        self, tmp_path, project
    ):
        sessions = domain(project)
        first = sessions.start("first")
        sessions.record_handoff(first.id, "next: one", "one")
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        assert sessions.latest_handoff(elsewhere) is None

    def test_the_newest_wins_whatever_order_the_record_holds(self, project):
        sessions = domain(project, clock=lambda: 1000.0)
        first = sessions.start("first")
        first.handoffs = [
            {"note": "next: newer", "name": "newer", "at": 900.0, "state": "registered"},
            {"note": "next: older", "name": "older", "at": 800.0, "state": "registered"},
            {"note": "next: junk", "name": "junk", "at": "not-a-time", "state": "x"},
        ]
        sessions._save(first)

        _, entry = sessions.latest_handoff(project)
        assert entry["name"] == "newer"

    def test_an_unreadable_staleness_setting_falls_back_to_seven_days(
        self, project, monkeypatch
    ):
        now = [100.0]
        sessions = domain(project, clock=lambda: now[0])
        first = sessions.start("first")
        sessions.record_handoff(first.id, "next: one", "one")
        real_get = sessions.manifest.get
        monkeypatch.setattr(
            sessions.manifest,
            "get",
            lambda key, default=None: (
                "a week" if key == "session.plan_stale_days" else real_get(key, default)
            ),
        )

        now[0] += 6 * 86400
        assert sessions.latest_handoff(project) is not None
        now[0] += 2 * 86400
        assert sessions.latest_handoff(project) is None


# -- the launch plan ---------------------------------------------------


class TestTheLaunchPlan:
    def plan(self, note=NASTY, cwd="/work/a dir/it's here"):
        return launch.plan_launch("/opt/bin/claude", "p1-11-example", note, Path(cwd))

    def test_the_command_is_the_documented_flags(self):
        plan = self.plan()
        assert plan.argv == (
            "/opt/bin/claude",
            "--name",
            "p1-11-example",
            "--remote-control",
            "p1-11-example",
            NASTY,
        )

    def test_the_shell_line_round_trips_through_shlex(self):
        plan = self.plan()
        tokens = shlex.split(plan.shell_line)
        assert tokens[:3] == ["cd", "/work/a dir/it's here", "&&"]
        assert tuple(tokens[3:]) == plan.argv

    def test_the_applescript_string_round_trips(self):
        plan = self.plan()
        literal = plan.applescript.split("do script ", 1)[1].split("\n", 1)[0]
        assert literal.startswith('"') and literal.endswith('"')
        body = literal[1:-1]
        unescaped = body.replace('\\"', '"').replace("\\\\", "\\")
        assert unescaped == plan.shell_line
        assert 'tell application "Terminal"' in plan.applescript

    def test_a_real_shell_hands_claude_the_note_byte_for_byte(self, tmp_path):
        """The proof that matters: a shell, not a tokeniser, parsing the line."""
        captured = tmp_path / "argv.json"
        fake = tmp_path / "claude"
        fake.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            f"json.dump({{'argv': sys.argv[1:], 'cwd': os.getcwd()}}, "
            f"open({str(captured)!r}, 'w'))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        workdir = tmp_path / "a dir" / "it's here"
        workdir.mkdir(parents=True)

        plan = launch.plan_launch(str(fake), "p1-11-example", NASTY, workdir)
        subprocess.run(["/bin/sh", "-c", plan.shell_line], check=True)

        got = json.loads(captured.read_text(encoding="utf-8"))
        assert got["argv"] == list(plan.argv[1:])
        assert Path(got["cwd"]).resolve() == workdir.resolve()

    @pytest.mark.skipif(
        sys.platform != "darwin" or shutil.which("osascript") is None,
        reason="needs osascript; evaluates a string literal and opens nothing",
    )
    def test_osascript_reads_the_literal_back_unchanged(self):
        plan = self.plan()
        literal = launch.applescript_string(plan.shell_line)
        proc = subprocess.run(
            ["osascript", "-e", f"return {literal}"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert proc.stdout.rstrip("\n") == plan.shell_line


class TestTheTerminalLauncher:
    def test_it_runs_osascript_with_the_script_as_one_argument(self, monkeypatch):
        calls = []

        def fake_run(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(launch.subprocess, "run", fake_run)
        plan = launch.plan_launch("/opt/bin/claude", "x", "next: x", Path("/w"))
        launch.TerminalLauncher().launch(plan)

        (argv, kwargs), = calls
        assert argv == ["osascript", "-e", plan.applescript]
        assert kwargs.get("shell") in (None, False)

    def test_a_failed_osascript_is_reported(self, monkeypatch):
        monkeypatch.setattr(
            launch.subprocess,
            "run",
            lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "not allowed"),
        )
        plan = launch.plan_launch("/opt/bin/claude", "x", "next: x", Path("/w"))
        with pytest.raises(InvariantError) as caught:
            launch.TerminalLauncher().launch(plan)
        assert "not allowed" in str(caught.value)
        assert plan.shell_line in str(caught.value)

    def test_resolve_claude_reads_path(self, monkeypatch):
        monkeypatch.setattr(launch.shutil, "which", lambda name: f"/found/{name}")
        assert launch.resolve_claude() == "/found/claude"

    def test_host_platform_is_sys_platform(self):
        assert launch.host_platform() == sys.platform


# -- the command -------------------------------------------------------


class TestTheCommand:
    def test_dry_run_prints_the_command_and_records_nothing(
        self, project, capsys, launcher, macos_with_claude
    ):
        session = started(project, capsys)
        assert run("session", "handoff", "--note", NASTY, "--dry-run") == 0
        text = capsys.readouterr().out

        assert "p1-11-example-key-lost-critical" in text
        assert "do script" in text
        assert "--remote-control" in text
        assert launcher.plans == []
        assert domain(project).get(session["id"]).handoffs == []

    def test_dry_run_json_carries_the_plan(
        self, project, capsys, macos_with_claude
    ):
        started(project, capsys)
        run("session", "handoff", "--note", "next: ship it", "--dry-run", "--json")
        doc = last_json(capsys)
        assert doc["name"] == "ship-it"
        assert doc["dry_run"] is True
        assert doc["argv"][-1] == "next: ship it"
        assert Path(doc["cwd"]).resolve() == project.resolve()

    def test_dry_run_works_where_nothing_could_launch(
        self, project, capsys, monkeypatch
    ):
        started(project, capsys)
        monkeypatch.setattr(launch, "host_platform", lambda: "linux")
        monkeypatch.setattr(launch, "resolve_claude", lambda: None)
        assert run("session", "handoff", "--note", "next: ship it", "--dry-run") == 0
        text = capsys.readouterr().out
        assert "claude --name ship-it" in text
        assert "not on PATH" in text

    def test_a_launch_records_then_opens_the_window(
        self, project, capsys, launcher, macos_with_claude
    ):
        session = started(project, capsys)
        assert run("session", "handoff", "--note", NASTY) == 0
        text = capsys.readouterr().out

        (plan,) = launcher.plans
        assert plan.argv[0] == str(macos_with_claude)
        assert plan.argv[-1] == NASTY
        assert Path(plan.cwd).resolve() == project.resolve()
        recorded = domain(project).get(session["id"]).handoffs
        assert recorded[-1]["name"] == "p1-11-example-key-lost-critical"
        assert session["name"] in text

    def test_the_launch_cds_into_the_main_checkout_not_the_worktree(
        self, project, capsys, launcher, macos_with_claude, monkeypatch
    ):
        session = started(project, capsys)
        run("session", "isolate", "--execute", "--id", session["id"])
        record = domain(project).get(session["id"])
        monkeypatch.chdir(record.worktree)

        assert run("session", "handoff", "--note", "next: ship it") == 0
        (plan,) = launcher.plans
        assert Path(plan.cwd).resolve() == project.resolve()

    def test_name_overrides_the_derived_one(
        self, project, capsys, launcher, macos_with_claude
    ):
        started(project, capsys)
        assert run(
            "session", "handoff", "--note", "done: all", "--name", "Next Thing"
        ) == 0
        (plan,) = launcher.plans
        assert plan.argv[2] == "next-thing"

    def test_no_next_item_and_no_name_is_refused(
        self, project, capsys, launcher, macos_with_claude
    ):
        started(project, capsys)
        assert run("session", "handoff", "--note", "done: all") == 2
        err = capsys.readouterr().err
        assert "next:" in err and "--name" in err
        assert launcher.plans == []

    def test_a_privacy_finding_refuses_before_anything_happens(
        self, project, capsys, launcher, macos_with_claude
    ):
        session = started(project, capsys)
        assert run(
            "session", "handoff", "--note", "next: x · mail someone@example.org"
        ) == 1
        assert launcher.plans == []
        assert domain(project).get(session["id"]).handoffs == []

    def test_non_macos_is_refused_with_the_command_to_run_by_hand(
        self, project, capsys, launcher, monkeypatch
    ):
        started(project, capsys)
        monkeypatch.setattr(launch, "host_platform", lambda: "linux")
        monkeypatch.setattr(launch, "resolve_claude", lambda: "/opt/bin/claude")
        assert run("session", "handoff", "--note", "next: ship it") == 2
        err = capsys.readouterr().err
        assert "macOS" in err
        assert "/opt/bin/claude --name ship-it --remote-control ship-it" in err
        assert launcher.plans == []

    def test_a_missing_claude_is_refused(
        self, project, capsys, launcher, monkeypatch
    ):
        session = started(project, capsys)
        monkeypatch.setattr(launch, "host_platform", lambda: "darwin")
        monkeypatch.setattr(launch, "resolve_claude", lambda: None)
        assert run("session", "handoff", "--note", "next: ship it") == 2
        assert "PATH" in capsys.readouterr().err
        assert launcher.plans == []
        assert domain(project).get(session["id"]).handoffs == []

    def test_a_torn_down_session_hands_off_by_explicit_id(
        self, project, capsys, launcher, macos_with_claude, monkeypatch
    ):
        session = started(project, capsys)
        domain(project).transition(session["id"], "abandoned", reason="fixture")
        monkeypatch.setenv("INVARIANT_SESSION_ID", session["id"])

        assert run("session", "handoff", "--note", "next: ship it") == 0
        assert domain(project).get(session["id"]).handoffs[-1]["name"] == "ship-it"

    def test_an_explicit_id_that_names_nothing_is_refused(
        self, project, capsys, macos_with_claude, monkeypatch
    ):
        started(project, capsys)
        monkeypatch.setenv("INVARIANT_SESSION_ID", "deadbeef")
        assert run("session", "handoff", "--note", "next: ship it") == 2

    def test_with_no_session_at_all_it_is_refused(
        self, tmp_path, capsys, macos_with_claude, monkeypatch
    ):
        root = tmp_path / "bare"
        root.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
        monkeypatch.chdir(root)
        assert run("init", "--name", "bare") == 0
        capsys.readouterr()
        assert run("session", "handoff", "--note", "next: ship it") == 2
        assert "no session" in capsys.readouterr().err

    def test_another_host_kind_is_refused(
        self, project, capsys, macos_with_claude
    ):
        started(project, capsys)
        manifest = project / ".claude" / "invariant.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "host:\n  kind: other\n",
            encoding="utf-8",
        )
        assert run("session", "handoff", "--note", "next: ship it") == 2
        assert "claude_code" in capsys.readouterr().err

    def test_a_failed_launch_keeps_the_note(
        self, project, capsys, macos_with_claude, monkeypatch
    ):
        session = started(project, capsys)

        class Broken:
            def launch(self, plan):
                raise InvariantError("osascript refused")

        monkeypatch.setattr("invariant.cli.main._handoff_launcher", lambda: Broken())
        assert run("session", "handoff", "--note", "next: ship it") == 1
        assert domain(project).get(session["id"]).handoffs[-1]["name"] == "ship-it"

    def test_the_real_launcher_is_the_terminal_one(self):
        from invariant.cli import main as cli

        # The autouse fixture replaced the attribute; the module's own
        # function is still reachable on the class of the original.
        assert isinstance(cli._real_handoff_launcher(), launch.TerminalLauncher)


# -- surfacing ---------------------------------------------------------


class TestTheNoteIsSurfacedWhenNobodyIsBound:
    def record(self, project, capsys):
        session = started(project, capsys)
        domain(project).record_handoff(session["id"], "next: ship it · carry: soak", "ship-it")
        domain(project).transition(session["id"], "abandoned", reason="fixture")
        capsys.readouterr()
        return session

    def test_context_shows_it(self, project, capsys):
        session = self.record(project, capsys)
        assert run("context") == 0
        text = capsys.readouterr().out
        assert "next: ship it · carry: soak" in text
        assert session["name"] in text

    def test_the_greeting_shows_it(self, project, capsys, monkeypatch):
        self.record(project, capsys)
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-new-9999")
        monkeypatch.setattr(
            "sys.stdin", _Stdin(json.dumps({"session_id": "host-new-9999"}))
        )
        assert run("hook", "session-start") == 0
        context = last_json(capsys)["hookSpecificOutput"]["additionalContext"]
        assert "next: ship it · carry: soak" in context

    def test_the_greeting_without_a_host_shows_it(self, project, capsys, monkeypatch):
        self.record(project, capsys)
        monkeypatch.setattr("sys.stdin", _Stdin(json.dumps({})))
        assert run("hook", "session-start") == 0
        context = last_json(capsys)["hookSpecificOutput"]["additionalContext"]
        assert "next: ship it · carry: soak" in context
        assert "no session is active" in context

    def test_nothing_recorded_changes_nothing(self, project, capsys):
        assert run("context") == 0
        assert capsys.readouterr().out.strip() == ""

    def test_a_bound_session_is_not_shown_the_note(self, project, capsys):
        session = started(project, capsys)
        domain(project).record_handoff(session["id"], "next: ship it", "ship-it")
        capsys.readouterr()
        assert run("context", "--id", session["id"]) == 0
        assert "next: ship it" not in capsys.readouterr().out


def test_no_test_here_can_reach_a_real_window():
    """A guard on the guard: the autouse fixture is what keeps Terminal shut."""
    from invariant.cli import main as cli

    assert isinstance(cli._handoff_launcher(), RecordingLauncher)
    assert os.environ.get("INVARIANT_SESSION_ID") is None
