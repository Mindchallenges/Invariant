"""Launched windows end with Claude, and hosts in a polluted environment are named (IDEA_035).

Two halves:

* **The typed line is ``exec``-ed.** The window's own shell becomes the launch
  shell, so when Claude exits nothing is left at a prompt: the window ends,
  or shows itself finished, by the user's Terminal profile. The launch file
  runs ``claude`` as its child rather than ``exec``-ing it, so a Claude that
  fails (a non-zero exit, not a signal) pauses with a short message until
  Return, and a start-up error stays readable. Every path then exits 0.
* **A host whose own environment inherited ``CLAUDE_CODE_CHILD_SESSION`` or
  ``COLORTERM=truecolor`` from Terminal.app is named**: a ``doctor`` warn row
  and an oracle info signal, with the remedy. The check reads the ``claude``
  process's environment, never the caller's, and reports only the two names.

Every fact comes from a fake: no Terminal.app, no ``osascript``, no window, no
real handoff, no real process environment, no signal to a real process. The
launch file is run by a real ``/bin/sh`` against a fake ``claude``, which is
how the suite already proves the typed line.
"""

from __future__ import annotations

import json
import os
import pty
import select
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from test_oracle_close_without_record import (
    HOST,
    IDLE,
    PID,
    TTY,
    Facts,
    Rec,
    probe_for,
    the_row,
)
from test_oracle_scan import NOW, make_project, put_attention, put_session

from invariant.cli import main as cli_main
from invariant.cli.main import _doctor_host_environments as real_doctor_seam
from invariant.hosts import claude_code_launch as launch
from invariant.hosts.claude_code_launch import (
    process_environment_markers as real_environment_markers,
)
from invariant.oracle import closehost, panel
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import assess
from invariant.oracle.scan import (
    PID_ATTENTION,
    ROW_SESSION,
    HostView,
    Row,
    ScanCache,
    scan_all,
)
from invariant.oracle.signals import INFO, INHERITED_ENV, classify

CHILD = "CLAUDE_CODE_CHILD_SESSION"
COLOUR = "COLORTERM"


# -- (a) the typed line is exec-ed --------------------------------------------------


def plan(note="hello there", cwd="/work/a dir", token=None, claude="/opt/bin/claude"):
    return launch.plan_launch(claude, "p1-idea", note, Path(cwd), token)


class TestTheTypedLineIsExeced:
    def test_the_window_shell_is_replaced_by_the_launch_shell(self):
        tokens = shlex.split(plan().shell_line)
        assert tokens[:5] == ["cd", "/work/a dir", "&&", "exec", "env"]

    def test_the_by_hand_line_is_not_execed(self):
        """Pasted into a shell that is already up, ``exec`` would end that shell."""
        tokens = shlex.split(plan().by_hand)
        assert "exec" not in tokens
        assert tokens[:4] == ["cd", "/work/a dir", "&&", "env"]

    def test_the_length_check_counts_the_exec(self, monkeypatch, tmp_path):
        typed = len((plan(cwd=tmp_path).shell_line + "\n").encode())
        monkeypatch.setattr(launch, "MAX_CANON", typed - 1)
        with pytest.raises(launch.InvariantError, match="would be"):
            plan(cwd=tmp_path)

    def test_a_cd_that_fails_execs_nothing_and_leaves_the_prompt(self, tmp_path):
        """``cd x && exec …``: a missing checkout stops before the shell is replaced."""
        line = plan(cwd=tmp_path / "gone").shell_line
        proc = subprocess.run(["/bin/sh", "-c", line + "; echo still-here"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              check=False)
        assert "still-here" in proc.stdout


class TestTheLaunchFile:
    def test_claude_runs_as_a_child_and_every_path_exits_zero(self):
        lines = plan().script.splitlines()
        assert lines[0] == "#!/bin/sh"
        assert shlex.split(lines[1])[:3] == ["rm", "-f", "--"]
        assert tuple(shlex.split(lines[2])) == plan().argv
        assert not any(line.lstrip().startswith("exec") for line in lines)
        assert lines[-1] == "exit 0"

    def test_it_pauses_only_on_a_failure_and_only_at_a_terminal(self):
        script = plan().script
        assert '[ "$status" -ne 0 ] && [ "$status" -le 128 ] && [ -t 0 ]' in script
        assert "read -r _" in script
        assert launch.LAUNCH_PAUSE in script


def fake_claude(tmp_path: Path, body: str) -> Path:
    fake = tmp_path / "claude"
    fake.write_text(f"#!{sys.executable}\nimport os, signal, sys\n{body}\n", encoding="utf-8")
    fake.chmod(0o755)
    return fake


def run_line(tmp_path, body, stdin=subprocess.DEVNULL):
    fake = fake_claude(tmp_path, body)
    staged = launch.plan_launch(str(fake), "p1-idea", "a note", tmp_path)
    proc = subprocess.run(["/bin/sh", "-c", staged.shell_line], capture_output=True,
                          text=True, stdin=stdin, timeout=30, check=False)
    return staged, proc


class TestARealShellRunsTheLaunch:
    def test_a_clean_exit_ends_the_launch_with_zero(self, tmp_path):
        staged, proc = run_line(tmp_path, "sys.exit(0)")
        assert proc.returncode == 0
        assert not Path(staged.launch_file).exists()

    def test_a_failure_away_from_a_terminal_does_not_wait(self, tmp_path):
        _, proc = run_line(tmp_path, "print('boom', file=sys.stderr); sys.exit(3)")
        assert proc.returncode == 0
        assert "boom" in proc.stderr
        assert "Press Return" not in proc.stdout

    def test_the_environment_still_reaches_claude(self, tmp_path):
        captured = tmp_path / "env.json"
        fake = fake_claude(tmp_path, "import json\n"
                           f"json.dump(dict(os.environ), open({str(captured)!r}, 'w'))")
        staged = launch.plan_launch(str(fake), "x", None, tmp_path, token="s:t")
        polluted = {name: "1" for name in launch.HOST_MARKERS}
        polluted["PATH"] = os.environ["PATH"]
        subprocess.run(["/bin/sh", "-c", staged.shell_line], check=True,
                       env=polluted, stdin=subprocess.DEVNULL)
        got = json.loads(captured.read_text(encoding="utf-8"))
        assert not set(launch.HOST_MARKERS) & set(got)
        assert got[launch.HANDOFF_ENV] == "s:t"


def run_at_a_terminal(tmp_path, body, answer: bytes | None):
    """Run the typed line with a pseudo-terminal as its stdin; what it printed."""
    fake = fake_claude(tmp_path, body)
    staged = launch.plan_launch(str(fake), "p1-idea", None, tmp_path)
    leader, follower = pty.openpty()
    try:
        proc = subprocess.Popen(["/bin/sh", "-c", staged.shell_line], stdin=follower,
                                stdout=follower, stderr=follower)
        os.close(follower)
        said = b""
        while proc.poll() is None:
            ready, _, _ = select.select([leader], [], [], 10)
            if not ready:
                break
            try:
                chunk = os.read(leader, 1024)
            except OSError:
                break
            said += chunk
            if answer is not None and b"Press Return" in said:
                os.write(leader, answer)
                answer = None
        proc.wait(timeout=10)
        return proc.returncode, said.decode(errors="replace")
    finally:
        os.close(leader)


class TestAtATerminal:
    def test_a_failed_start_pauses_until_return_then_ends(self, tmp_path):
        code, said = run_at_a_terminal(
            tmp_path, "print('cannot start'); sys.exit(2)", answer=b"\n")
        assert "cannot start" in said
        assert "claude exited with status 2. Press Return to close this window." in said
        assert code == 0

    def test_a_clean_exit_does_not_pause(self, tmp_path):
        code, said = run_at_a_terminal(tmp_path, "sys.exit(0)", answer=None)
        assert "Press Return" not in said
        assert code == 0

    def test_claude_ended_by_a_signal_does_not_pause(self, tmp_path):
        """The closer's SIGTERM (BUG_100, Close host) must leave nothing waiting in the tab."""
        code, said = run_at_a_terminal(
            tmp_path, "os.kill(os.getpid(), signal.SIGTERM)", answer=None)
        assert "Press Return" not in said
        assert code == 0


# -- (b) the environment a host inherited -------------------------------------------


ARGV = "/opt/bin/claude --name p1 --remote-control p1 COLORTERM=truecolor in a note"


class TestReadingTheMarkers:
    def test_only_the_two_names_are_reported(self):
        full = f"{ARGV} PATH=/bin HOME=/h {CHILD}=1 SECRET=hunter2 {COLOUR}=truecolor"
        assert launch.environment_markers(ARGV, full) == frozenset({CHILD, COLOUR})

    def test_the_argv_is_never_read_as_environment(self):
        """The note on the command line says ``COLORTERM=truecolor``; the environment does not."""
        full = f"{ARGV} PATH=/bin HOME=/h"
        assert launch.environment_markers(ARGV, full) == frozenset()

    def test_colorterm_counts_only_as_truecolor(self):
        full = f"{ARGV} PATH=/bin {COLOUR}=256"
        assert launch.environment_markers(ARGV, full) == frozenset()

    def test_an_empty_child_session_does_not_count(self):
        assert launch.environment_markers(ARGV, f"{ARGV} {CHILD}=") == frozenset()

    @pytest.mark.parametrize("full", [
        ARGV,                       # no environment shown: another user's process
        f"{ARGV}   ",
        "something else entirely",  # the argv changed between the two reads
        f"{ARGV}X=1",               # no separator after the argv
    ])
    def test_what_cannot_be_read_is_unknown(self, full):
        assert launch.environment_markers(ARGV, full) is None

    def test_an_empty_argv_is_unknown(self):
        assert launch.environment_markers("", f" {CHILD}=1") is None


class FakePs:
    def __init__(self, argv=ARGV, env=f" PATH=/bin {CHILD}=1", code=0, fail=None):
        self.argv, self.env, self.code, self.fail = argv, env, code, fail
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.fail is not None:
            raise self.fail
        out = self.argv + (self.env if "-E" in argv else "")
        return subprocess.CompletedProcess(argv, self.code, out + "\n", "")


class TestTheProcessTableRead:
    def test_two_ps_reads_of_the_pid_alone(self):
        ps = FakePs()
        assert real_environment_markers(4242, run=ps) == frozenset({CHILD})
        assert ps.calls == [["ps", "-ww", "-o", "command=", "-p", "4242"],
                            ["ps", "-E", "-ww", "-o", "command=", "-p", "4242"]]

    def test_a_ps_that_fails_is_unknown(self):
        assert real_environment_markers(4242, run=FakePs(code=1)) is None

    def test_a_ps_that_cannot_run_is_unknown(self):
        assert real_environment_markers(4242, run=FakePs(fail=OSError("no ps"))) is None

    def test_an_empty_answer_is_unknown(self):
        assert real_environment_markers(4242, run=FakePs(argv="", env="")) is None


#: pid -> (ppid, comm): claude 40 under sh 30 under login 20 under Terminal 10.
IN_TERMINAL = {40: (30, "/opt/bin/claude"), 30: (20, "sh"), 20: (10, "login"),
               10: (1, "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal"),
               1: (0, "/sbin/launchd")}


class TestUnderTerminalApp:
    def test_a_top_level_host_in_a_terminal_tab(self):
        assert launch.top_level_in_terminal_app(40, IN_TERMINAL.get) is True

    def test_a_claude_started_by_another_claude_is_not_the_terminal_s(self):
        chain = {**IN_TERMINAL, 40: (35, "claude"), 35: (30, "/opt/bin/claude")}
        assert launch.top_level_in_terminal_app(40, chain.get) is False

    def test_another_app_is_not_terminal(self):
        chain = {40: (30, "claude"), 30: (20, "zsh"), 20: (1, "iTerm2"), 1: (0, "launchd")}
        assert launch.top_level_in_terminal_app(40, chain.get) is False

    def test_a_chain_that_cannot_be_read_is_unknown(self):
        assert launch.top_level_in_terminal_app(40, {40: (30, "claude")}.get) is None
        assert launch.top_level_in_terminal_app(40, {}.get) is None

    def test_a_process_that_is_its_own_parent_ends_the_chain(self):
        assert launch.top_level_in_terminal_app(40, {40: (30, "claude"),
                                                     30: (30, "zsh")}.get) is False

    def test_a_chain_longer_than_the_bound_is_unknown(self):
        chain = {n: (n - 1, "sh") for n in range(2, 40)}
        chain[40] = (39, "claude")
        assert launch.top_level_in_terminal_app(40, chain.get, max_hops=3) is None


class TestInheritedMarkers:
    def test_both_markers_under_terminal_are_named_sorted(self):
        got = launch.inherited_markers(40, lambda pid: frozenset({COLOUR, CHILD}),
                                       IN_TERMINAL.get)
        assert got == (CHILD, COLOUR)

    def test_a_clean_host_is_empty_without_walking(self):
        walked = []
        got = launch.inherited_markers(40, lambda pid: frozenset(),
                                       lambda pid: walked.append(pid))
        assert got == () and walked == []

    def test_an_unreadable_environment_is_unknown(self):
        assert launch.inherited_markers(40, lambda pid: None, IN_TERMINAL.get) is None

    def test_markers_outside_terminal_are_not_reported(self):
        chain = {40: (30, "claude"), 30: (1, "Code Helper"), 1: (0, "launchd")}
        assert launch.inherited_markers(40, lambda pid: frozenset({COLOUR}), chain.get) == ()

    def test_markers_with_a_chain_that_cannot_be_read_are_unknown(self):
        assert launch.inherited_markers(40, lambda pid: frozenset({CHILD}), {}.get) is None

    def test_the_default_reader_is_the_suite_guarded_one(self):
        """The conftest guard stands in for the real read: no test reads a real environment."""
        assert launch.inherited_markers(os.getpid()) is None

    def test_the_probe_asks_its_reader(self):
        probe = launch.ClaudeCodeProbe(env_of=lambda pid: frozenset())
        assert probe.inherited_markers(40) == ()


class TestRunningClaudes:
    def test_the_table_names_every_claude_and_its_terminal(self):
        table = ("  40 ttys004  /opt/bin/claude\n  41 ??       claude\n"
                 "  42 ttys005  /bin/zsh\n  bad line\n")
        assert launch.claude_processes(table) == [(40, "ttys004"), (41, None)]

    def test_the_real_read_is_one_ps(self):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "  40 ttys004 claude\n", "")

        assert launch.running_claude_processes(run=run) == [(40, "ttys004")]
        assert calls == [["ps", "-axo", "pid=,tty=,comm="]]

    def test_a_table_that_cannot_be_read_is_none(self):
        def failed(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, "", "")

        def broken(argv, **kwargs):
            raise OSError("no ps")

        assert launch.running_claude_processes(run=failed) is None
        assert launch.running_claude_processes(run=broken) is None


# -- the oracle: scan, signal, panel ------------------------------------------------


class MarkerProbe:
    """Wraps a BUG_099 probe with an environment reader, counting its reads."""

    def __init__(self, inner, markers=(CHILD,), fail=None):
        self.inner, self.markers, self.fail = inner, markers, fail
        self.read: list[int] = []

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def inherited_markers(self, pid):
        self.read.append(pid)
        if self.fail is not None:
            raise self.fail
        return self.markers


def alive(pid) -> bool:
    return pid == PID


@pytest.fixture
def alpha(tmp_path):
    """BUG_099's leftover: its session ended, its pid only in the attention record."""
    project = make_project(tmp_path, "alpha")
    put_session(project, id="s-done", name="done-s-done", state="torn_down",
                origin=str(project.root), host_session_id=HOST, heartbeat=NOW - IDLE)
    put_attention(project, HOST, mtime=NOW - IDLE, pid=PID, turn="ended")
    return project


class TestTheScanCarriesTheFinding:
    def test_a_verified_orphan_carries_the_names(self, alpha):
        probe = MarkerProbe(probe_for(alpha), markers=(COLOUR, CHILD))
        row = the_row(probe)
        assert row["hosts"][0]["inherited_env"] == [CHILD, COLOUR]
        kinds = {s["kind"]: s for s in row["signals"]}
        assert kinds[INHERITED_ENV]["severity"] == INFO
        assert kinds[INHERITED_ENV]["alert"] is False
        assert "quit Terminal.app fully and relaunch it from the Dock" in \
            kinds[INHERITED_ENV]["detail"]

    def test_the_read_is_kept_for_the_cache_s_time(self, alpha):
        probe = MarkerProbe(probe_for(alpha))
        cache = ScanCache(probe=probe, pid_alive=alive)
        the_row(probe, cache)
        the_row(probe, cache)
        assert probe.read == [PID]

    def test_a_dead_pid_drops_the_kept_answer(self, alpha):
        probe = MarkerProbe(probe_for(alpha))
        state = {"alive": True}
        cache = ScanCache(probe=probe, pid_alive=lambda pid: state["alive"] and pid == PID)
        cache.inherited(PID)
        state["alive"] = False
        assert cache.process_alive(PID, None) == (False, None)
        assert cache.host_alive(PID) is False
        state["alive"] = True
        cache.inherited(PID)
        assert probe.read == [PID, PID]

    def test_a_clean_host_raises_nothing(self, alpha):
        row = the_row(MarkerProbe(probe_for(alpha), markers=()))
        assert row["hosts"][0]["inherited_env"] == []
        assert INHERITED_ENV not in {s["kind"] for s in row["signals"]}

    def test_a_reader_that_fails_is_unknown_and_quiet(self, alpha):
        row = the_row(MarkerProbe(probe_for(alpha), fail=OSError("ps went away")))
        assert row["hosts"][0]["inherited_env"] is None
        assert INHERITED_ENV not in {s["kind"] for s in row["signals"]}

    def test_a_probe_without_the_reader_reads_nothing(self, alpha):
        row = the_row(probe_for(alpha))
        assert row["hosts"][0]["inherited_env"] is None

    def test_an_attention_pid_that_started_after_the_last_write_is_not_read(self, alpha):
        """A reused pid may be another claude: its environment is not this host's."""
        later = Facts(started=NOW, start="2099-01-01T00:00:00")
        probe = MarkerProbe(probe_for(alpha, facts={PID: later}))
        row = the_row(probe)
        assert row["hosts"][0]["pid_source"] == PID_ATTENTION
        assert row["hosts"][0]["inherited_env"] is None
        assert probe.read == []

    def test_an_attention_pid_ps_cannot_describe_is_not_read(self, alpha):
        probe = MarkerProbe(probe_for(alpha, facts={PID: None}))
        assert the_row(probe)["hosts"][0]["inherited_env"] is None
        assert probe.read == []

    def test_an_attention_pid_with_no_start_is_not_read(self, alpha):
        probe = MarkerProbe(probe_for(alpha, facts={PID: Facts(started=None)}))
        assert the_row(probe)["hosts"][0]["inherited_env"] is None
        assert probe.read == []


@pytest.fixture
def working(tmp_path):
    """A live session whose current host is verified by its per-process record."""
    beta = make_project(tmp_path, "beta")
    put_session(beta, id="s-live", name="live-s-live", state="working",
                origin=str(beta.root), host_session_id=HOST, heartbeat=NOW - 60)
    put_attention(beta, HOST, mtime=NOW - 60, pid=PID, turn="ended",
                  waiting={"kind": "idle", "since": "2026-01-01T00:00:00+00:00"})
    return beta


def session_row(probe):
    cache = ScanCache(probe=probe, pid_alive=alive)
    document = assess(scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW,
                               cache=cache), NOW)
    [row] = [r for p in document["projects"] for r in p["rows"] if r["kind"] == ROW_SESSION]
    return row


class TestASessionsHost:
    def test_a_host_its_record_verifies_is_read(self, working):
        inner = probe_for(working, records=[Rec(HOST, PID, start=None)],
                          transcripts={"beta": {HOST: NOW - 60}})
        probe = MarkerProbe(inner)
        row = session_row(probe)
        assert row["hosts"][0]["inherited_env"] == [CHILD]
        assert INHERITED_ENV in {s["kind"] for s in row["signals"]}
        assert probe.read == [PID]

    def test_an_attention_only_host_started_before_its_last_write_is_read(self, working):
        """The polluted host writes no record: its attention pid, ordered by ``ps``."""
        inner = probe_for(working, transcripts={"beta": {HOST: NOW - 60}})
        probe = MarkerProbe(inner)
        row = session_row(probe)
        assert row["hosts"][0]["pid_source"] == PID_ATTENTION
        assert row["hosts"][0]["inherited_env"] == [CHILD]

    def test_a_host_that_is_gone_is_not_read(self, working):
        inner = probe_for(working, transcripts={"beta": {HOST: NOW - 60}})
        probe = MarkerProbe(inner)
        cache = ScanCache(probe=probe, pid_alive=lambda pid: False)
        scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
        assert probe.read == []


class TestTheSignal:
    def row(self, inherited, alive_=True):
        host = HostView(host_session_id=HOST, pid=PID, alive=alive_, inherited_env=inherited)
        return Row(kind=ROW_SESSION, project="p", session_id="s", hosts=[host])

    def test_the_key_names_the_markers(self):
        [signal] = [s for s in classify(self.row([CHILD, COLOUR]), NOW)
                    if s.kind == INHERITED_ENV]
        assert signal.key == f"p|s|{INHERITED_ENV}|{CHILD},{COLOUR}"
        assert CHILD in signal.detail and COLOUR in signal.detail

    def test_a_gone_host_raises_nothing(self):
        assert INHERITED_ENV not in {s.kind for s in classify(self.row([CHILD], False), NOW)}

    def test_unknown_raises_nothing(self):
        assert INHERITED_ENV not in {s.kind for s in classify(self.row(None), NOW)}


class TestThePanel:
    def test_the_card_says_what_and_the_remedy(self):
        row = {"kind": ROW_SESSION, "signals": [{"kind": INHERITED_ENV, "cause": "",
                                                 "alert": False, "severity": INFO}]}
        chips = panel._chips(row, NOW)
        assert {"text": "Terminal.app needs a restart", "tone": panel.TONE_IDLE} in chips
        [sentence] = panel._sentences(row, NOW)
        assert "Quit Terminal.app fully and relaunch it from the Dock." in sentence

    def test_a_leftover_row_names_it_after_the_window(self, alpha):
        row = the_row(MarkerProbe(probe_for(alpha)))
        line = panel._host_leftover(row, "alpha", NOW, acting=True)["line"]
        assert line.endswith(" · Terminal.app needs a restart: quit it fully and relaunch "
                             "it from the Dock")

    def test_a_clean_leftover_row_is_unchanged(self, alpha):
        row = the_row(MarkerProbe(probe_for(alpha), markers=()))
        line = panel._host_leftover(row, "alpha", NOW, acting=True)["line"]
        assert "Terminal.app" not in line


# -- the closer sees the window that ended with Claude --------------------------------


class TestNoTabLeft:
    @pytest.mark.parametrize("code", ["no_tab", "not_running"])
    def test_the_line_says_nothing_was_left_not_that_it_was_left_open(self, code):
        line = closehost._tab_outcome(TTY, True, ClosedSeams((code, "")).seams)
        assert line == (f"Claude ended, and no Terminal tab is left on {TTY}: its window "
                        "closed with it, or was already closed.")
        assert "left open" not in line
        assert closehost.outcome_word(line) == "no_tab"

    def test_the_other_outcomes_keep_their_words(self):
        line = closehost._tab_outcome(TTY, True, ClosedSeams(("closed_window", "")).seams)
        assert line == "Claude ended, and its Terminal window closed."
        assert closehost.outcome_word(line) == "closed_window"


class ClosedSeams:
    def __init__(self, answer):
        self.seams = closehost.HostSeams(
            records=list, facts=lambda pid: None, alive=lambda pid: False,
            terminate=lambda pid: None, in_terminal=lambda pid: True,
            close_tab=lambda tty: answer, sleep=lambda s: None)


# -- doctor ------------------------------------------------------------------------


REMEDY = "quit Terminal.app fully and relaunch it from the Dock"


class TestTheDoctorRow:
    def test_no_claude_running_passes(self):
        label, ok, detail, warn = cli_main._host_environment_check([])
        assert (label, ok, warn) == ("claude window environment", True, False)
        assert detail == "no live claude window"

    def test_clean_hosts_pass(self):
        found = [{"pid": 40, "tty": "ttys004", "markers": ()},
                 {"pid": 41, "tty": None, "markers": ()}]
        _, ok, detail, warn = cli_main._host_environment_check(found)
        assert ok and not warn
        assert detail == (f"2 live claude windows; none inherited {CHILD} or "
                          f"{COLOUR}=truecolor from Terminal.app")

    def test_a_polluted_host_warns_naming_it_and_the_remedy(self):
        found = [{"pid": 40, "tty": "ttys004", "markers": (CHILD, COLOUR)},
                 {"pid": 41, "tty": None, "markers": (COLOUR,)},
                 {"pid": 42, "tty": "ttys006", "markers": ()}]
        _, ok, detail, warn = cli_main._host_environment_check(found)
        assert ok and warn
        assert detail == (f"claude 40 on ttys004 inherited {CHILD}, {COLOUR}=truecolor; "
                          f"claude 41 inherited {COLOUR}=truecolor from Terminal.app: "
                          f"{REMEDY}")

    def test_what_could_not_be_read_is_said(self):
        found = [{"pid": 40, "tty": None, "markers": ()},
                 {"pid": 41, "tty": None, "markers": None}]
        _, ok, detail, warn = cli_main._host_environment_check(found)
        assert ok and not warn
        assert detail.endswith("; 1 could not be read")

    def test_a_table_that_could_not_be_read_passes_saying_so(self):
        _, ok, detail, warn = cli_main._host_environment_check(None)
        assert ok and not warn
        assert detail == "the process table could not be read"

    def test_the_real_read_joins_the_table_and_each_environment(self, monkeypatch):
        monkeypatch.setattr(launch, "running_claude_processes",
                            lambda: [(40, "ttys004"), (41, None)])
        monkeypatch.setattr(launch, "inherited_markers",
                            lambda pid: (CHILD,) if pid == 40 else None)
        assert cli_main._real_host_environments() == [
            {"pid": 40, "tty": "ttys004", "markers": (CHILD,)},
            {"pid": 41, "tty": None, "markers": None}]

    def test_the_real_read_with_no_table(self, monkeypatch):
        monkeypatch.setattr(launch, "running_claude_processes", lambda: None)
        assert cli_main._real_host_environments() is None

    def test_the_suite_never_reads_the_real_table(self):
        assert cli_main._doctor_host_environments() == []

    def test_the_seam_reads_through_the_real_join(self, monkeypatch):
        monkeypatch.setattr(cli_main, "_real_host_environments", lambda: None)
        assert real_doctor_seam() is None

    def test_doctor_warns_and_keeps_its_exit_code(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        clean = cli_main.main(["doctor", "--json"])
        capsys.readouterr()
        monkeypatch.setattr(cli_main, "_doctor_host_environments", lambda: [
            {"pid": 40, "tty": "ttys004", "markers": (CHILD,)}])
        polluted = cli_main.main(["doctor", "--json"])
        rows = {r["check"]: r for r in json.loads(capsys.readouterr().out)["checks"]}
        row = rows["claude window environment"]
        assert (row["ok"], row["warn"]) == (True, True)
        assert "claude 40 on ttys004" in row["detail"] and REMEDY in row["detail"]
        assert polluted == clean

    def test_doctor_judges_the_host_never_its_own_environment(self, tmp_path, monkeypatch,
                                                              capsys):
        """Claude sets markers on its tool shells: doctor's own environment is not evidence."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(CHILD, "1")
        monkeypatch.setenv(COLOUR, "truecolor")
        cli_main.main(["doctor", "--json"])
        rows = {r["check"]: r for r in json.loads(capsys.readouterr().out)["checks"]}
        row = rows["claude window environment"]
        assert (row["ok"], row["warn"]) == (True, False)
