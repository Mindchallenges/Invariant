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
import re
import shlex
import shutil
import subprocess
import sys
import time
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
        unset = [arg for name in launch.HOST_MARKERS for arg in ("-u", name)]
        assert tokens[:4] == ["cd", "/work/a dir/it's here", "&&", "env"]
        assert tokens[4 : 4 + len(unset)] == unset
        assert tuple(tokens[4 + len(unset) :]) == plan.argv

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

    def test_a_polluted_terminal_still_starts_a_clean_claude(self, tmp_path):
        """The typed line alone cleans the successor: Terminal is already dirty."""
        captured = tmp_path / "env.json"
        fake = tmp_path / "claude"
        fake.write_text(
            f"#!{sys.executable}\nimport json, os\n"
            f"json.dump(dict(os.environ), open({str(captured)!r}, 'w'))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        polluted = {name: "1" for name in launch.HOST_MARKERS}
        polluted.update(
            PATH=os.environ["PATH"], FOO="bar", CLAUDE_CODE_USE_BEDROCK="1"
        )

        plan = launch.plan_launch(str(fake), "x", NASTY, tmp_path, token="s:t")
        subprocess.run(["/bin/sh", "-c", plan.shell_line], check=True, env=polluted)

        got = json.loads(captured.read_text(encoding="utf-8"))
        assert not set(launch.HOST_MARKERS) & set(got)
        assert got["FOO"] == "bar"
        assert got["CLAUDE_CODE_USE_BEDROCK"] == "1"
        assert got[launch.HANDOFF_ENV] == "s:t"

    def test_without_host_markers_removes_only_the_markers(self):
        environ = {name: "1" for name in launch.HOST_MARKERS}
        environ.update(FOO="bar", CLAUDE_CODE_USE_BEDROCK="1")
        assert launch.without_host_markers(environ) == {
            "FOO": "bar",
            "CLAUDE_CODE_USE_BEDROCK": "1",
        }

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

    def test_osascript_runs_without_the_host_markers(self, monkeypatch):
        """A Terminal.app the call starts keeps this environment for good."""
        for name in launch.HOST_MARKERS:
            monkeypatch.setenv(name, "1")
        monkeypatch.setenv("CLAUDE_CODE_USE_BEDROCK", "1")
        monkeypatch.setenv("FOO", "bar")
        calls = []
        monkeypatch.setattr(
            launch.subprocess,
            "run",
            lambda argv, **kw: calls.append(kw)
            or subprocess.CompletedProcess(argv, 0, "", ""),
        )
        plan = launch.plan_launch("/opt/bin/claude", "x", "next: x", Path("/w"))
        launch.TerminalLauncher().launch(plan)

        (kwargs,) = calls
        assert not set(launch.HOST_MARKERS) & set(kwargs["env"])
        assert kwargs["env"]["FOO"] == "bar"
        assert kwargs["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"

    def test_resolve_claude_reads_path(self, monkeypatch):
        monkeypatch.setattr(launch.shutil, "which", lambda name: f"/found/{name}")
        assert launch.resolve_claude() == "/found/claude"

    def test_host_platform_is_sys_platform(self):
        assert launch.host_platform() == sys.platform


# -- BUG_068: the successor must not inherit the launcher's host markers ----


class TestBUG068SuccessorEnvironment:
    """Repro for BUG_068 — see the work-item entry.

    A launch is run from INSIDE a Claude Code session, so the caller's
    environment carries that session's host markers. The successor ``claude``
    must start as a fresh, top-level host session: none of those markers, while
    the user's own variables and ``INVARIANT_HANDOFF`` still arrive.

    The harness follows the environment along the real chain, without opening
    a window: ``TerminalLauncher`` runs ``osascript`` (recorded, not run), the
    Terminal it drives inherits the environment ``osascript`` was given — the
    ``env=`` it was passed, or this process's own when none was — and that
    Terminal's shell runs ``plan.shell_line``. Here a real ``/bin/sh`` runs the
    line in that environment, and a fake ``claude`` dumps what it received. A
    fix that strips the markers in the shell line, or one that hands
    ``osascript`` a clean environment, both turn it green.

    The repro is a strict xfail: it fails while the defect is live. When the
    fix lands, delete the xfail marker; the assertions must then pass
    unchanged. The guard is deliberately NOT xfail: it pins that the user's
    own environment and the claim token reach ``claude`` today, so a fix that
    wipes the whole environment cannot pass as one.
    """

    MARKERS = (
        ("CLAUDECODE", "1"),
        ("CLAUDE_CODE_CHILD_SESSION", "1"),
        ("CLAUDE_CODE_SESSION_ID", "x"),
        ("COLORTERM", "truecolor"),
    )
    TOKEN = "s-bug068:2026-10-20"

    def successor_env(self, tmp_path, monkeypatch):
        """The environment the successor ``claude`` starts with, end to end."""
        for key, value in self.MARKERS:
            monkeypatch.setenv(key, value)
        monkeypatch.setenv("FOO", "bar")

        captured = tmp_path / "env.json"
        fake = tmp_path / "claude"
        fake.write_text(
            f"#!{sys.executable}\nimport json, os\n"
            f"json.dump(dict(os.environ), open({str(captured)!r}, 'w'))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        workdir = tmp_path / "main"
        workdir.mkdir()

        real_run = subprocess.run
        calls = []

        def record(argv, **kwargs):
            calls.append((list(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(launch.subprocess, "run", record)
        plan = launch.plan_launch(
            str(fake), "bug-068", "next: x", workdir, token=self.TOKEN
        )
        launch.TerminalLauncher().launch(plan)
        monkeypatch.setattr(launch.subprocess, "run", real_run)

        osascript = [kw for argv, kw in calls if argv and argv[0] == "osascript"]
        assert len(osascript) == 1, calls
        terminal_env = osascript[0].get("env")
        if terminal_env is None:
            terminal_env = dict(os.environ)

        real_run(["/bin/sh", "-c", plan.shell_line], check=True, env=terminal_env)
        return json.loads(captured.read_text(encoding="utf-8"))

    def test_BUG_068_guard_user_env_and_token_reach_claude(
        self, tmp_path, monkeypatch
    ):
        got = self.successor_env(tmp_path, monkeypatch)
        assert got.get("FOO") == "bar"
        assert got.get(launch.HANDOFF_ENV) == self.TOKEN

    def test_BUG_068_successor_claude_starts_without_the_launchers_host_markers(
        self, tmp_path, monkeypatch
    ):
        got = self.successor_env(tmp_path, monkeypatch)
        leaked = {key: got[key] for key, _ in self.MARKERS if key in got}
        assert leaked == {}, f"successor claude inherited host markers: {leaked}"
        assert got.get("FOO") == "bar"
        assert got.get(launch.HANDOFF_ENV) == self.TOKEN


# -- BUG_069: two Claude Code exports the BUG_068 list missed ----------


class TestBUG069SuccessorEnvironment:
    """Repro for BUG_069 — see the work-item entry.

    Inside a Claude Code session the environment also carries
    ``CLAUDE_EFFORT=medium`` and ``GIT_EDITOR=true``, which the launching host
    sets and the user does not. The successor must not inherit them: the typed
    line must unset both, ``without_host_markers`` must drop both, and the
    successor ``claude`` — followed along the real chain by the BUG_068
    harness — must start without them, while the user's own variables and the
    claim token still arrive.

    """

    MARKERS = (("CLAUDE_EFFORT", "medium"), ("GIT_EDITOR", "true"))

    def test_BUG_069_successor_starts_without_claude_effort_and_git_editor(
        self, tmp_path, monkeypatch
    ):
        names = [key for key, _ in self.MARKERS]

        kept = launch.without_host_markers({"FOO": "bar", **dict(self.MARKERS)})
        assert kept == {"FOO": "bar"}, (
            f"without_host_markers kept launcher exports: {kept}"
        )

        plan = launch.plan_launch(
            "/bin/claude", "bug-069", "next: x", tmp_path, token="t"
        )
        words = shlex.split(plan.shell_line)
        unset = {words[i + 1] for i, word in enumerate(words) if word == "-u"}
        missing = [name for name in names if name not in unset]
        assert missing == [], f"typed line does not unset {missing}"

        harness = TestBUG068SuccessorEnvironment()
        harness.MARKERS = self.MARKERS
        got = harness.successor_env(tmp_path, monkeypatch)
        leaked = {name: got[name] for name in names if name in got}
        assert leaked == {}, f"successor claude inherited: {leaked}"
        assert got.get("FOO") == "bar"
        assert got.get(launch.HANDOFF_ENV) == harness.TOKEN


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


# -- ending the old session (IDEA_009) ----------------------------------
#
# After a successful launch the launching Claude Code process — CLAUDE_PID —
# is sent SIGTERM a bounded delay later, unless --keep-open says otherwise.
# Never on a dry run, a refusal or a failed launch. No test signals anything:
# the closer is replaced suite-wide (conftest), and the real one is proved by
# running its script in a shell whose `kill` is a function that only records.


@pytest.fixture
def closer(_no_real_host_close, monkeypatch):
    """The suite's inert closer, with a launching host that answers `claude`."""
    monkeypatch.setenv("CLAUDE_PID", "4242")
    return _no_real_host_close


class TestTheOldSessionEnds:
    def test_a_launch_schedules_sigterm_to_the_launching_claude(
        self, project, capsys, launcher, closer, macos_with_claude
    ):
        started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it") == 0
        text = capsys.readouterr().out

        assert len(launcher.plans) == 1
        assert [pid for pid, _ in closer.closed] == [4242]
        assert closer.asked == [4242]
        assert "4242" in text and "--keep-open" in text

    def test_the_output_is_printed_before_anything_is_closed(
        self, project, capsys, launcher, closer, macos_with_claude
    ):
        started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it") == 0
        text = capsys.readouterr().out
        assert text.index("handed off as ship-it") < text.index("4242")

    def test_the_delay_outlasts_the_hook_that_records_the_handoff(
        self, project, capsys, closer, macos_with_claude, tmp_path
    ):
        from invariant.hosts.claude_code import ClaudeCodeHost

        host_root = tmp_path / "host"
        host_root.mkdir()
        ClaudeCodeHost(host_root).install_hooks()
        settings = json.loads(
            (host_root / ".claude" / "settings.json").read_text(encoding="utf-8")
        )
        (entry,) = settings["hooks"]["PostToolUse"]
        hook_budget = entry["hooks"][0]["timeout"]

        started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it") == 0
        ((_, delay),) = closer.closed
        assert hook_budget < delay <= 30

    def test_keep_open_launches_and_closes_nothing(
        self, project, capsys, launcher, closer, macos_with_claude
    ):
        started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it", "--keep-open") == 0
        text = capsys.readouterr().out
        assert len(launcher.plans) == 1
        assert closer.closed == []
        assert "--keep-open" in text and "open" in text

    def test_a_dry_run_closes_nothing_and_says_what_a_real_run_would(
        self, project, capsys, launcher, closer, macos_with_claude
    ):
        started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it", "--dry-run") == 0
        text = capsys.readouterr().out
        assert closer.closed == []
        assert "SIGTERM" in text and "--keep-open" in text

    def test_a_refusal_closes_nothing(
        self, project, capsys, launcher, closer, monkeypatch
    ):
        started(project, capsys)
        monkeypatch.setattr(launch, "host_platform", lambda: "linux")
        monkeypatch.setattr(launch, "resolve_claude", lambda: "/opt/bin/claude")
        assert run("session", "handoff", "--note", "next: ship it") == 2
        assert closer.closed == []

    def test_a_failed_launch_closes_nothing(
        self, project, capsys, closer, macos_with_claude, monkeypatch
    ):
        started(project, capsys)

        class Broken:
            def launch(self, plan):
                raise InvariantError("osascript refused")

        monkeypatch.setattr("invariant.cli.main._handoff_launcher", lambda: Broken())
        assert run("session", "handoff", "--note", "next: ship it") == 1
        assert closer.closed == []

    def test_no_claude_pid_leaves_the_session_open_and_says_why(
        self, project, capsys, launcher, closer, macos_with_claude, monkeypatch
    ):
        monkeypatch.delenv("CLAUDE_PID")
        started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it") == 0
        text = capsys.readouterr().out
        assert len(launcher.plans) == 1
        assert closer.closed == []
        assert "CLAUDE_PID" in text and "leaving this session open" in text

    @pytest.mark.parametrize("value", ["abc", "0", "-7", ""])
    def test_an_unusable_claude_pid_leaves_it_open(
        self, project, capsys, closer, macos_with_claude, monkeypatch, value
    ):
        monkeypatch.setenv("CLAUDE_PID", value)
        started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it") == 0
        text = capsys.readouterr().out
        assert closer.closed == [] and closer.asked == []
        assert "leaving this session open" in text

    @pytest.mark.parametrize(
        ("name", "said"), [("python3", "python3"), (None, "no running process")]
    )
    def test_a_pid_that_is_not_claude_leaves_it_open(
        self, project, capsys, closer, macos_with_claude, name, said
    ):
        closer.name = name
        started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it") == 0
        text = capsys.readouterr().out
        assert closer.closed == []
        assert said in text and "leaving this session open" in text

    def test_json_says_what_was_scheduled(
        self, project, capsys, closer, macos_with_claude
    ):
        started(project, capsys)
        run("session", "handoff", "--note", "next: ship it", "--json")
        close = last_json(capsys)["close"]
        assert close["scheduled"] is True
        assert close["pid"] == 4242
        assert close["delay"] == closer.closed[0][1]

    def test_json_on_keep_open_and_dry_run_schedules_nothing(
        self, project, capsys, closer, macos_with_claude
    ):
        started(project, capsys)
        run("session", "handoff", "--note", "next: ship it", "--keep-open", "--json")
        assert last_json(capsys)["close"]["scheduled"] is False
        run("session", "handoff", "--note", "next: ship it", "--dry-run", "--json")
        assert last_json(capsys)["close"]["scheduled"] is False

    def test_dismiss_takes_no_keep_open(self, project, capsys, closer):
        started(project, capsys)
        assert run("session", "handoff", "--dismiss", "--keep-open") == 2
        assert "--keep-open" in capsys.readouterr().err

    def test_the_real_closer_is_the_detached_one(self):
        from invariant.cli import main as cli

        assert isinstance(cli._real_handoff_closer(), launch.DetachedCloser)


def _run_close_script(script: str, comm: str | None, tmp_path) -> list[str]:
    """Run ``script`` in a real shell whose ``kill`` only records its arguments.

    ``kill`` is a regular built-in, so a function of the same name shadows it;
    ``ps`` is replaced the same way when ``comm`` is given, and is the real
    ``ps`` otherwise. Nothing is signalled either way.
    """
    log = tmp_path / "kill.log"
    stubs = [f'kill() {{ echo "$*" >> {shlex.quote(str(log))}; }}']
    if comm is not None:
        stubs.append(f"ps() {{ printf '%s\\n' {shlex.quote(comm)}; }}")
    proc = subprocess.run(
        ["/bin/sh", "-c", "\n".join([*stubs, script])],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


class TestTheHostCloser:
    @pytest.mark.parametrize(
        ("environ", "pid"),
        [
            ({"CLAUDE_PID": "123"}, 123),
            ({"CLAUDE_PID": " 77 "}, 77),
            ({}, None),
            ({"CLAUDE_PID": "abc"}, None),
            ({"CLAUDE_PID": "0"}, None),
            ({"CLAUDE_PID": "-3"}, None),
        ],
    )
    def test_host_pid_reads_claude_pid(self, environ, pid):
        assert launch.host_pid(environ) == pid

    @pytest.mark.parametrize(
        ("name", "verdict"),
        [
            ("claude", True),
            ("/usr/local/bin/claude", True),
            ("python3", False),
            ("claude-helper", False),
            ("", False),
            (None, False),
        ],
    )
    def test_only_claude_is_a_claude_process(self, name, verdict):
        assert launch.is_claude_process_name(name) is verdict

    def test_the_script_is_a_bounded_sleep_then_a_checked_term(self):
        script = launch.close_script(4242, 20)
        assert script.startswith("sleep 20;")
        assert "ps -p 4242 -o comm=" in script
        assert "kill -TERM 4242" in script

    @pytest.mark.parametrize("comm", ["claude", "/opt/homebrew/bin/claude"])
    def test_the_script_signals_a_claude_process(self, comm, tmp_path):
        assert _run_close_script(launch.close_script(4242, 0), comm, tmp_path) == [
            "-TERM 4242"
        ]

    @pytest.mark.parametrize("comm", ["python3", "claude-helper", ""])
    def test_the_script_spares_anything_else(self, comm, tmp_path):
        assert _run_close_script(launch.close_script(4242, 0), comm, tmp_path) == []

    def test_a_reused_pid_is_spared_at_kill_time_by_the_real_ps(self, tmp_path):
        # This process is python, not claude: the check the script makes
        # immediately before the signal must refuse it.
        script = launch.close_script(os.getpid(), 0)
        assert _run_close_script(script, None, tmp_path) == []

    def test_process_name_reads_ps(self):
        name = launch.DetachedCloser().process_name(os.getpid())
        assert name
        assert not launch.is_claude_process_name(name)

    def test_process_name_is_none_without_a_process(self, monkeypatch):
        def fake_run(argv, **kwargs):
            assert argv == ["ps", "-p", "999999", "-o", "comm="]
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")

        monkeypatch.setattr(launch.subprocess, "run", fake_run)
        assert launch.DetachedCloser().process_name(999999) is None

    def test_process_name_is_none_without_ps(self, monkeypatch):
        def fake_run(argv, **kwargs):
            raise FileNotFoundError(argv[0])

        monkeypatch.setattr(launch.subprocess, "run", fake_run)
        assert launch.DetachedCloser().process_name(42) is None

    def test_close_spawns_a_detached_sleeper_holding_no_pipe(self, monkeypatch):
        calls = []

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))

        monkeypatch.setattr(launch.subprocess, "Popen", fake_popen)
        launch.DetachedCloser().close(4242, 20)

        ((argv, kwargs),) = calls
        assert argv == ["/bin/sh", "-c", launch.close_script(4242, 20)]
        assert kwargs["start_new_session"] is True
        for stream in ("stdin", "stdout", "stderr"):
            assert kwargs[stream] is subprocess.DEVNULL


def test_no_test_here_can_end_the_host_session():
    """A guard on the guard: conftest's inert closer is what keeps the host up."""
    from invariant.cli import main as cli

    assert cli._handoff_closer().closed == []
    assert os.environ.get("CLAUDE_PID") is None


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


# -- consuming a note (IDEA_008) ----------------------------------------
#
# A note stops being offered once a session acts on it. Two exits, and only
# two: the launched host's first `session start` / `session resume` claims
# EXACTLY the note its launcher token names, and `session handoff --dismiss`
# retires the latest fresh one by hand. Freshness stays the third, derived
# expiry, untouched.

#: The variable the launcher sets in front of `claude`. Spelled out here rather
#: than imported so that a rename is a failing test, not a silent agreement.
TOKEN_ENV = "INVARIANT_HANDOFF"


@pytest.fixture(autouse=True)
def _no_ambient_handoff_token(monkeypatch):
    """A suite run from a handed-off host inherits that host's token."""
    monkeypatch.delenv(TOKEN_ENV, raising=False)


def hook(event: str, body: dict, monkeypatch) -> int:
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(body)))
    return run("hook", event)


def token_for(session_id: str, entry: dict) -> str:
    return f"{session_id}:{entry['at']!r}"


def started_id(out: str) -> str:
    match = re.search(r"session\s+([0-9a-z]+)\s+\(", out)
    assert match, out
    return match.group(1)


def claim_payload(command: str, stdout: str, host: str = "host-next-4242") -> dict:
    return {
        "session_id": host,
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"stdout": stdout},
    }


class TestAConsumedHandoffIsNotOffered:
    def test_a_consumed_entry_is_skipped(self, project):
        sessions = domain(project, clock=lambda: 1000.0)
        first = sessions.start("first")
        first.handoffs = [
            {
                "note": "next: done already",
                "name": "done-already",
                "at": 900.0,
                "state": "registered",
                "consumed": {"at": 950.0, "how": "claimed", "session": "x", "host": None},
            }
        ]
        sessions._save(first)

        assert sessions.latest_handoff(project) is None

    def test_an_older_open_note_is_offered_when_the_newest_is_consumed(self, project):
        sessions = domain(project, clock=lambda: 1000.0)
        first = sessions.start("first")
        first.handoffs = [
            {"note": "next: older", "name": "older", "at": 800.0, "state": "registered"},
            {
                "note": "next: newer",
                "name": "newer",
                "at": 900.0,
                "state": "registered",
                "consumed": {"at": 950.0, "how": "dismissed", "session": None, "host": None},
            },
        ]
        sessions._save(first)

        _, entry = sessions.latest_handoff(project)
        assert entry["name"] == "older"

    def test_an_entry_without_the_key_reads_as_unconsumed(self, project):
        sessions = domain(project, clock=lambda: 1000.0)
        first = sessions.start("first")
        first.handoffs = [
            {"note": "next: open", "name": "open", "at": 900.0, "state": "registered"}
        ]
        sessions._save(first)

        _, entry = sessions.latest_handoff(project)
        assert entry["name"] == "open"


class TestTheLaunchCarriesTheClaimToken:
    def test_the_typed_line_names_the_recorded_note(
        self, project, capsys, launcher, macos_with_claude
    ):
        session = started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it") == 0

        (plan,) = launcher.plans
        entry = domain(project).get(session["id"]).handoffs[-1]
        token = token_for(session["id"], entry)
        assert f"{TOKEN_ENV}={token}" in shlex.split(plan.shell_line)
        assert plan.argv[-1] == "next: ship it"

    def test_a_real_shell_hands_claude_the_token(
        self, project, capsys, launcher, monkeypatch, tmp_path
    ):
        captured = tmp_path / "env.json"
        fake = tmp_path / "fakebin" / "claude"
        fake.parent.mkdir()
        fake.write_text(
            f"#!{sys.executable}\nimport json, os, sys\n"
            f"json.dump({{'token': os.environ.get({TOKEN_ENV!r}), "
            f"'argv': sys.argv[1:]}}, open({str(captured)!r}, 'w'))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        monkeypatch.setattr(launch, "host_platform", lambda: "darwin")
        monkeypatch.setattr(launch, "resolve_claude", lambda: str(fake))
        session = started(project, capsys)
        assert run("session", "handoff", "--note", NASTY) == 0

        (plan,) = launcher.plans
        subprocess.run(["/bin/sh", "-c", plan.shell_line], check=True)
        got = json.loads(captured.read_text(encoding="utf-8"))
        entry = domain(project).get(session["id"]).handoffs[-1]
        assert got["token"] == token_for(session["id"], entry)
        assert got["argv"][-1] == NASTY

    def test_a_dry_run_carries_no_token_because_nothing_was_recorded(
        self, project, capsys, macos_with_claude
    ):
        started(project, capsys)
        assert run("session", "handoff", "--note", "next: ship it", "--dry-run") == 0
        text = capsys.readouterr().out
        assert f"{TOKEN_ENV}=" not in text.split("command:", 1)[1].splitlines()[0]

    def test_the_by_hand_line_on_another_platform_carries_no_token(
        self, project, capsys, launcher, monkeypatch
    ):
        started(project, capsys)
        monkeypatch.setattr(launch, "host_platform", lambda: "linux")
        monkeypatch.setattr(launch, "resolve_claude", lambda: "/opt/bin/claude")
        assert run("session", "handoff", "--note", "next: ship it") == 2
        assert f"{TOKEN_ENV}=" not in capsys.readouterr().err


class TestTheLaunchedSessionClaimsItsNote:
    """The claim rides the PostToolUse binding path, never SessionStart."""

    def handed_off(self, project, capsys, note="next: ship it", name="ship-it"):
        session = started(project, capsys, name="old-task")
        sessions = domain(project)
        sessions.record_handoff(session["id"], note, name)
        sessions.transition(session["id"], "abandoned", reason="fixture")
        entry = sessions.get(session["id"]).handoffs[-1]
        capsys.readouterr()
        return session, entry

    def start_next(self, capsys, name="ship-it"):
        assert run("session", "start", name) == 0
        out = capsys.readouterr().out
        return out

    def test_the_first_start_claims_the_named_note(
        self, project, capsys, monkeypatch
    ):
        old, entry = self.handed_off(project, capsys)
        monkeypatch.setenv(TOKEN_ENV, token_for(old["id"], entry))
        out = self.start_next(capsys)
        new_id = started_id(out)

        assert hook(
            "post-tool-use",
            claim_payload("invariant session start ship-it", out),
            monkeypatch,
        ) == 0
        capsys.readouterr()

        consumed = domain(project).get(old["id"]).handoffs[-1].get("consumed")
        assert consumed is not None
        assert consumed["how"] == "claimed"
        assert consumed["session"] == new_id
        assert consumed["host"] == "host-next-4242"
        assert isinstance(consumed["at"], float)

        assert domain(project).latest_handoff(project) is None

    def test_context_no_longer_shows_a_claimed_note(
        self, project, capsys, monkeypatch
    ):
        old, entry = self.handed_off(project, capsys)
        monkeypatch.setenv(TOKEN_ENV, token_for(old["id"], entry))
        out = self.start_next(capsys)
        hook(
            "post-tool-use",
            claim_payload("invariant session start ship-it", out),
            monkeypatch,
        )
        # The new session exists now; take it out of the way so `context` is
        # asked the unbound question the greeting asks.
        new_id = started_id(out)
        domain(project).transition(new_id, "abandoned", reason="fixture")
        capsys.readouterr()

        assert run("context") == 0
        assert "next: ship it" not in capsys.readouterr().out

    def test_a_resume_claims_too(self, project, capsys, monkeypatch):
        old, entry = self.handed_off(project, capsys)
        parked = domain(project).start("parked-task", host_session_id="host-gone-1")
        domain(project).end_host_session("host-gone-1")
        monkeypatch.setenv(TOKEN_ENV, token_for(old["id"], entry))
        assert run("session", "resume", parked.name) == 0
        out = capsys.readouterr().out

        hook(
            "post-tool-use",
            claim_payload(f"invariant session resume {parked.name}", out),
            monkeypatch,
        )
        consumed = domain(project).get(old["id"]).handoffs[-1].get("consumed")
        assert consumed is not None and consumed["session"] == parked.id

    def test_session_start_the_greeting_writes_nothing(
        self, project, capsys, monkeypatch
    ):
        old, entry = self.handed_off(project, capsys)
        monkeypatch.setenv(TOKEN_ENV, token_for(old["id"], entry))
        hook("session-start", {"session_id": "host-next-4242"}, monkeypatch)
        capsys.readouterr()

        assert "consumed" not in domain(project).get(old["id"]).handoffs[-1]

    def test_without_a_token_a_start_consumes_nothing(
        self, project, capsys, monkeypatch
    ):
        old, _ = self.handed_off(project, capsys)
        out = self.start_next(capsys)
        hook(
            "post-tool-use",
            claim_payload("invariant session start ship-it", out),
            monkeypatch,
        )
        assert "consumed" not in domain(project).get(old["id"]).handoffs[-1]

    def test_a_read_only_command_claims_nothing(self, project, capsys, monkeypatch):
        old, entry = self.handed_off(project, capsys)
        monkeypatch.setenv(TOKEN_ENV, token_for(old["id"], entry))
        hook(
            "post-tool-use",
            claim_payload(
                "invariant session status",
                f"invariant — session {old['id']} (old-task) is abandoned",
            ),
            monkeypatch,
        )
        assert "consumed" not in domain(project).get(old["id"]).handoffs[-1]

    def test_only_the_named_note_is_consumed(self, project, capsys, monkeypatch):
        ticks = iter(range(1000, 2000))
        sessions = domain(project, clock=lambda: float(next(ticks)))
        a = sessions.start("task-a")
        b = sessions.start("task-b")
        sessions.record_handoff(a.id, "next: one", "one")
        sessions.record_handoff(b.id, "next: two", "two")
        named = sessions.get(a.id).handoffs[-1]
        monkeypatch.setenv(TOKEN_ENV, token_for(a.id, named))

        out = self.start_next(capsys, name="one")
        hook(
            "post-tool-use",
            claim_payload("invariant session start one", out),
            monkeypatch,
        )
        assert "consumed" in domain(project).get(a.id).handoffs[-1]
        assert "consumed" not in domain(project).get(b.id).handoffs[-1]

    def test_a_note_recorded_after_the_claim_is_still_offered(
        self, project, capsys, monkeypatch
    ):
        old, entry = self.handed_off(project, capsys)
        monkeypatch.setenv(TOKEN_ENV, token_for(old["id"], entry))
        out = self.start_next(capsys)
        hook(
            "post-tool-use",
            claim_payload("invariant session start ship-it", out),
            monkeypatch,
        )
        domain(project).record_handoff(old["id"], "next: later work", "later-work")

        _, latest = domain(project).latest_handoff(project)
        assert latest["name"] == "later-work"

    def test_a_second_start_in_the_same_host_does_not_reclaim(
        self, project, capsys, monkeypatch
    ):
        old, entry = self.handed_off(project, capsys)
        monkeypatch.setenv(TOKEN_ENV, token_for(old["id"], entry))
        first = self.start_next(capsys)
        hook(
            "post-tool-use",
            claim_payload("invariant session start ship-it", first),
            monkeypatch,
        )
        before = domain(project).get(old["id"]).handoffs[-1]["consumed"]

        second = self.start_next(capsys, name="other-thing")
        hook(
            "post-tool-use",
            claim_payload("invariant session start other-thing", second, host="host-2"),
            monkeypatch,
        )
        assert domain(project).get(old["id"]).handoffs[-1]["consumed"] == before

    def test_the_old_record_gets_a_standstill_row_and_stays_deregistered(
        self, project, capsys, monkeypatch
    ):
        session = started(project, capsys, name="old-task")
        sessions = domain(project)
        sessions.transition(session["id"], "merged", force=True, reason="fixture")
        sessions.teardown(session["id"])
        sessions.record_handoff(session["id"], "next: ship it", "ship-it")
        entry = sessions.get(session["id"]).handoffs[-1]
        capsys.readouterr()
        monkeypatch.setenv(TOKEN_ENV, token_for(session["id"], entry))

        out = self.start_next(capsys)
        hook(
            "post-tool-use",
            claim_payload("invariant session start ship-it", out),
            monkeypatch,
        )
        record = domain(project).get(session["id"])
        assert record.state == "torn_down"
        row = record.history[-1]
        assert row["from"] == row["to"] == "torn_down"
        assert row.get("handoff_consumed") == "ship-it"
        assert row.get("how") == "claimed"
        registry = sessions.store.read("registry.json", {"sessions": {}})
        assert session["id"] not in registry["sessions"]


class TestTheClaimIsForgiving:
    """A hook must never fail a completed command over a bad token."""

    def test_a_record_that_cannot_be_read_does_not_fail_the_hook(
        self, project, capsys, monkeypatch
    ):
        session = started(project, capsys, name="old-task")
        domain(project).record_handoff(session["id"], "next: ship it", "ship-it")
        entry = domain(project).get(session["id"]).handoffs[-1]
        capsys.readouterr()
        monkeypatch.setenv(TOKEN_ENV, token_for(session["id"], entry))

        def broken(self, *args, **kwargs):
            raise InvariantError("state store unreadable")

        monkeypatch.setattr(SessionDomain, "claim_handoff", broken)
        assert run("session", "start", "ship-it") == 0
        out = capsys.readouterr().out
        assert hook(
            "post-tool-use",
            claim_payload("invariant session start ship-it", out),
            monkeypatch,
        ) == 0

    @pytest.mark.parametrize(
        "token",
        ["", "no-colon", "deadbeef:1.0", "{sid}:not-a-number", "{sid}:12345.0"],
    )
    def test_a_token_that_names_nothing_changes_nothing(
        self, project, capsys, monkeypatch, token
    ):
        session = started(project, capsys, name="old-task")
        domain(project).record_handoff(session["id"], "next: ship it", "ship-it")
        capsys.readouterr()
        monkeypatch.setenv(TOKEN_ENV, token.format(sid=session["id"]))

        assert run("session", "start", "ship-it") == 0
        out = capsys.readouterr().out
        assert hook(
            "post-tool-use",
            claim_payload("invariant session start ship-it", out),
            monkeypatch,
        ) == 0
        assert "consumed" not in domain(project).get(session["id"]).handoffs[-1]

    def test_the_domain_answers_none_for_a_token_it_cannot_honour(self, project):
        sessions = domain(project)
        first = sessions.start("first")
        sessions.record_handoff(first.id, "next: one", "one")
        entry = sessions.get(first.id).handoffs[-1]

        assert sessions.claim_handoff("garbage", "other", None) is None
        assert sessions.claim_handoff(token_for(first.id, entry), "other", "h") is not None
        assert sessions.claim_handoff(token_for(first.id, entry), "again", "h") is None


class TestDismiss:
    def recorded(self, project, capsys):
        session = started(project, capsys, name="old-task")
        domain(project).record_handoff(session["id"], "next: ship it", "ship-it")
        domain(project).transition(session["id"], "abandoned", reason="fixture")
        capsys.readouterr()
        return session

    def test_dismiss_retires_the_latest_fresh_note(self, project, capsys, launcher):
        session = self.recorded(project, capsys)

        assert run("session", "handoff", "--dismiss") == 0
        text = capsys.readouterr().out
        assert "ship-it" in text
        assert f"session {session['id']} (" not in text
        consumed = domain(project).get(session["id"]).handoffs[-1]["consumed"]
        assert consumed["how"] == "dismissed"
        assert launcher.plans == []

        assert run("context") == 0
        assert "next: ship it" not in capsys.readouterr().out

    def test_dismiss_records_a_standstill_row(self, project, capsys):
        session = self.recorded(project, capsys)
        run("session", "handoff", "--dismiss")
        row = domain(project).get(session["id"]).history[-1]
        assert row["from"] == row["to"] == "abandoned"
        assert row.get("handoff_consumed") == "ship-it"
        assert row.get("how") == "dismissed"

    def test_dismiss_json(self, project, capsys):
        session = self.recorded(project, capsys)
        assert run("session", "handoff", "--dismiss", "--json") == 0
        doc = last_json(capsys)
        assert doc["dismissed"] is True
        assert doc["session"] == session["id"]
        assert doc["name"] == "ship-it"

    def test_dismiss_dry_run_writes_nothing(self, project, capsys):
        session = self.recorded(project, capsys)
        assert run("session", "handoff", "--dismiss", "--dry-run") == 0
        assert "ship-it" in capsys.readouterr().out
        assert "consumed" not in domain(project).get(session["id"]).handoffs[-1]

    def test_dismiss_with_nothing_fresh_is_refused(self, project, capsys):
        started(project, capsys)
        assert run("session", "handoff", "--dismiss") == 2
        assert "no fresh handoff" in capsys.readouterr().err

    def test_a_dry_run_with_nothing_fresh_is_refused_the_same_way(
        self, project, capsys
    ):
        started(project, capsys)
        assert run("session", "handoff", "--dismiss", "--dry-run") == 2
        assert "no fresh handoff" in capsys.readouterr().err

    def test_dismiss_names_the_proven_session_and_host_as_the_dismisser(
        self, project, capsys, monkeypatch
    ):
        old = self.recorded(project, capsys)
        mine = domain(project).start("mine", host_session_id="host-here-7777")
        monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-here-7777")

        assert run("session", "handoff", "--dismiss") == 0
        consumed = domain(project).get(old["id"]).handoffs[-1]["consumed"]
        assert consumed["session"] == mine.id
        assert consumed["host"] == "host-here-7777"

    def test_dismiss_retires_one_note_at_a_time(self, project, capsys):
        # Distinct, increasing and FRESH: the command reads the real clock.
        base = time.time() - 60.0
        ticks = iter(range(100))
        sessions = domain(project, clock=lambda: base + next(ticks))
        a = sessions.start("task-a")
        sessions.record_handoff(a.id, "next: one", "one")
        sessions.record_handoff(a.id, "next: two", "two")

        assert run("session", "handoff", "--dismiss") == 0
        capsys.readouterr()
        _, left = domain(project).latest_handoff(project)
        assert left["name"] == "one"

    @pytest.mark.parametrize(
        "extra",
        [["--note", "next: x"], ["--name", "x"], ["--id", "deadbeef"]],
    )
    def test_dismiss_takes_no_note_name_or_id(self, project, capsys, extra):
        self.recorded(project, capsys)
        assert run("session", "handoff", "--dismiss", *extra) == 2
        assert "--dismiss" in capsys.readouterr().err

    def test_neither_note_nor_dismiss_is_refused(self, project, capsys):
        started(project, capsys)
        assert run("session", "handoff") == 2
        err = capsys.readouterr().err
        assert "--note" in err and "--dismiss" in err

    def test_the_greeting_names_the_way_to_dismiss(self, project, capsys, monkeypatch):
        self.recorded(project, capsys)
        assert run("context") == 0
        assert "invariant session handoff --dismiss" in capsys.readouterr().out


# -- BUG_075: a long note is cut by the tty, and nothing checks the successor --


#: A note well over the line budget, still one a real handoff accepts: one
#: line, a ``next:`` item, clean, and within ``HANDOFF_NOTE_MAX`` characters.
#: The middots are two bytes each, so it is over 2000 BYTES while staying under
#: 2000 characters. Synthetic — names no real item.
BUG_075_NOTE = (
    "done: P1.10 · next: P1.11 BUG_EXAMPLE_LONG_NOTE critical -> bugprep · carry: "
    + "the successor reads this carry line in full · " * 41
).strip()

#: macOS canonical-input limit (MAX_CANON). ``do script`` TYPES the line into
#: the new window's tty, and canonical mode holds at most this many bytes of
#: one line, newline included; the reported tab received exactly this many.
BUG_075_MAX_CANON = 1024


class TestBUG075LongNoteHandoff:
    """Repro for BUG_075 — see the work-item entry.

    Three assertions, one per mechanism, so a fix can be proven against each:
    the typed line fits the tty; the bytes a tty delivers still start
    ``claude``; and a handoff that sees no live successor neither reports
    success nor ends the launching session.

    Each is a strict xfail: it fails while the defect is live. When the fix
    lands, delete the xfail markers; the assertions must then pass unchanged.
    The third needs whatever successor check the fix adds to find NO successor
    here — the stub launcher starts nothing.
    """

    SLUG = "p1-11-example-long-note-critical"

    def test_BUG_075_guard_the_note_is_long_and_acceptable(self, project):
        """Not xfail: pins that the note is a valid handoff note, over 2000 bytes."""
        assert len(BUG_075_NOTE.encode("utf-8")) > 2000
        assert len(BUG_075_NOTE) <= HANDOFF_NOTE_MAX
        assert domain(project).vet_handoff_note(BUG_075_NOTE) == BUG_075_NOTE
        assert handoff_name(BUG_075_NOTE) == self.SLUG

    @pytest.mark.xfail(
        strict=True, reason="BUG_075 is live — delete this line with the fix"
    )
    def test_BUG_075_typed_line_fits_the_tty_for_a_2000_byte_note(self):
        plan = launch.plan_launch(
            "/opt/homebrew/bin/claude",
            self.SLUG,
            BUG_075_NOTE,
            Path("/work/projects/example-project"),
            token="5678abcd:2026-10-20",
        )
        typed = (plan.shell_line + "\n").encode("utf-8")
        assert len(typed) <= BUG_075_MAX_CANON, (
            f"typed line is {len(typed)} bytes; the tty keeps {BUG_075_MAX_CANON}"
        )

    @pytest.mark.xfail(
        strict=True, reason="BUG_075 is live — delete this line with the fix"
    )
    def test_BUG_075_the_line_a_tty_delivers_still_starts_claude(self, tmp_path):
        """The tab's shell gets at most MAX_CANON bytes; claude must still run."""
        captured = tmp_path / "argv.json"
        fake = tmp_path / "claude"
        fake.write_text(
            f"#!{sys.executable}\nimport json, sys\n"
            f"json.dump(sys.argv[1:], open({str(captured)!r}, 'w'))\n",
            encoding="utf-8",
        )
        fake.chmod(0o755)
        workdir = tmp_path / "main"
        workdir.mkdir()

        plan = launch.plan_launch(
            str(fake), self.SLUG, BUG_075_NOTE, workdir, token="5678abcd:2026-10-20"
        )
        delivered = plan.shell_line.encode("utf-8")[:BUG_075_MAX_CANON]
        subprocess.run(
            [b"/bin/sh", b"-c", delivered], capture_output=True, check=False
        )

        assert captured.exists(), "no claude started from the line the tty delivered"
        argv = json.loads(captured.read_text(encoding="utf-8"))
        assert argv[:4] == ["--name", self.SLUG, "--remote-control", self.SLUG]

    @pytest.mark.xfail(
        strict=True, reason="BUG_075 is live — delete this line with the fix"
    )
    def test_BUG_075_no_live_successor_means_no_success_and_no_sigterm(
        self, project, capsys, launcher, closer, macos_with_claude
    ):
        started(project, capsys)
        code = run("session", "handoff", "--note", BUG_075_NOTE)
        capsys.readouterr()

        assert len(launcher.plans) == 1, "the handoff never reached the launch"
        assert closer.closed == [], (
            f"SIGTERM scheduled for the launching session with no successor: "
            f"{closer.closed}"
        )
        assert code != 0, "handoff reported success with no successor alive"
