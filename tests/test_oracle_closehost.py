"""Close a leftover Claude window from the oracle (IDEA_027 increment 6, ADR-009 D15).

``invariant oracle close-host`` checks everything again at the moment it runs,
asks Claude to end with SIGTERM, waits for it, and only then closes the
Terminal tab the host ran in, when only its shell is left there. Every host
fact is a seam, and every seam here is a fake: no test signals a process it
did not spawn itself, runs ``osascript``, reads the real ``~/.claude`` or
binds anything.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field

import pytest
from test_oracle_scan import FakeProbe, make_project, put_session

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import InvariantError
from invariant.oracle import closehost
from invariant.oracle.closehost import (
    CloseRequest,
    HostSeams,
    binding_refusal,
    close_host,
)
from invariant.oracle.scan import ScanCache

HOST = "27885f4e-1111-2222-3333-444455556666"
PID = 41001
START = "2026-10-20T09:00:00"
TTY = "ttys004"


@dataclass
class Rec:
    session_id: str
    pid: int
    start: str | None = START


@dataclass
class Facts:
    is_host: bool = True
    start: str | None = START
    tty: str | None = TTY


@dataclass
class FakeHost:
    """One Claude process, as every seam would describe it."""

    records: list = field(default_factory=lambda: [Rec(HOST, PID)])
    facts: Facts | None = field(default_factory=Facts)
    alive: bool = True
    #: How many polls after SIGTERM the process stays alive; None never exits.
    exits_after: int | None = 2
    in_terminal: bool | None = True
    tab: tuple[str, str] = ("closed_tab", "")
    terminate_raises: BaseException | None = None
    records_raise: BaseException | None = None
    signalled: list = field(default_factory=list)
    tabs_asked: list = field(default_factory=list)
    slept: list = field(default_factory=list)
    clock: float = 0.0

    def seams(self) -> HostSeams:
        return HostSeams(
            records=self.read_records,
            facts=lambda pid: self.facts,
            alive=self.is_alive,
            terminate=self.terminate,
            in_terminal=lambda pid: self.in_terminal,
            close_tab=self.close_tab,
            sleep=self.sleep,
            monotonic=lambda: self.clock,
        )

    def read_records(self):
        if self.records_raise is not None:
            raise self.records_raise
        return list(self.records)

    def is_alive(self, pid: int) -> bool:
        if not self.signalled:
            return self.alive
        if self.exits_after is None:
            return True
        self.exits_after -= 1
        return self.exits_after >= 0

    def terminate(self, pid: int) -> None:
        self.signalled.append(pid)
        if self.terminate_raises is not None:
            raise self.terminate_raises

    def close_tab(self, tty: str) -> tuple[str, str]:
        self.tabs_asked.append(tty)
        return self.tab

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.clock += seconds


def request(**changes) -> CloseRequest:
    fields = {"host_id": HOST, "pid": PID, "start": START, "tty": TTY}
    fields.update(changes)
    return CloseRequest(**fields)


def unbound() -> str | None:
    return None


def run(fake: FakeHost, bound=unbound, **changes) -> list[str]:
    return close_host(request(**changes), fake.seams(), bound)


def refused(fake: FakeHost, bound=unbound, **changes) -> str:
    with pytest.raises(InvariantError) as caught:
        run(fake, bound, **changes)
    return str(caught.value)


# -- the checks at the moment of confirm -------------------------------------------------


class TestItChecksEverythingAgain:
    @pytest.mark.parametrize("pid", [0, -1])
    def test_a_pid_that_names_a_process_group_is_refused(self, pid):
        fake = FakeHost()
        assert "positive" in refused(fake, pid=pid)
        assert fake.signalled == []

    def test_a_host_id_that_is_not_a_plain_name_is_refused(self):
        fake = FakeHost()
        assert "host id" in refused(fake, host_id="../x")
        assert fake.signalled == []

    def test_records_that_cannot_be_read(self):
        fake = FakeHost(records_raise=FileNotFoundError("no sessions directory"))
        assert "cannot be read" in refused(fake)
        assert fake.signalled == []

    def test_no_record_of_the_pid_any_more(self):
        fake = FakeHost(records=[Rec(HOST, PID + 1)])
        assert "no record of process 41001" in refused(fake)
        assert fake.signalled == []

    def test_the_record_names_another_claude_session(self):
        fake = FakeHost(records=[Rec("another-session", PID)])
        assert "another Claude session" in refused(fake)
        assert fake.signalled == []

    def test_the_record_is_a_newer_process(self):
        fake = FakeHost(records=[Rec(HOST, PID, start="2026-10-25T08:00:00")])
        assert "newer process" in refused(fake)
        assert fake.signalled == []

    def test_the_pid_has_exited(self):
        fake = FakeHost(alive=False)
        assert "already exited" in refused(fake)
        assert fake.signalled == []

    def test_ps_cannot_describe_it(self):
        fake = FakeHost(facts=None)
        assert "cannot describe" in refused(fake)
        assert fake.signalled == []

    def test_it_is_not_claude_any_more(self):
        fake = FakeHost(facts=Facts(is_host=False))
        assert "not Claude" in refused(fake)
        assert fake.signalled == []

    @pytest.mark.parametrize("start", ["2026-10-25T08:00:00", None])
    def test_it_started_at_another_time(self, start):
        fake = FakeHost(facts=Facts(start=start))
        assert "started at another time" in refused(fake)
        assert fake.signalled == []

    @pytest.mark.parametrize(("ps_tty", "asked"), [("ttys009", TTY), (None, TTY), (TTY, None)])
    def test_it_runs_on_another_terminal(self, ps_tty, asked):
        fake = FakeHost(facts=Facts(tty=ps_tty))
        assert "another terminal" in refused(fake, tty=asked)
        assert fake.signalled == []

    def test_a_live_session_binds_it_now(self):
        fake = FakeHost()
        said = refused(fake, bound=lambda: "a live invariant session uses it (Heap, s1)")
        assert said.startswith("nothing was done: a live invariant session uses it")
        assert fake.signalled == []

    def test_every_refusal_says_nothing_was_done(self):
        assert refused(FakeHost(alive=False)).startswith("nothing was done: ")


# -- ending Claude ---------------------------------------------------------------------


class TestEndingClaude:
    def test_one_sigterm_then_it_waits_for_the_exit(self):
        fake = FakeHost(exits_after=2)
        lines = run(fake)
        assert fake.signalled == [PID]
        assert fake.slept and all(s == closehost.POLL_SECONDS for s in fake.slept)
        assert any("asked Claude (process 41001) to end" in line for line in lines)

    def test_a_claude_that_does_not_exit_is_never_forced(self):
        fake = FakeHost(exits_after=None)
        said = refused(fake)
        assert said.startswith("Claude did not exit")
        assert "nothing else was done" in said
        assert fake.signalled == [PID]
        assert fake.tabs_asked == []
        assert sum(fake.slept) >= closehost.GRACE_SECONDS

    def test_a_process_that_left_before_the_signal_counts_as_ended(self):
        fake = FakeHost(terminate_raises=ProcessLookupError())
        lines = run(fake)
        assert lines[-1] == closehost.OUTCOMES["closed_tab"]

    def test_a_signal_this_user_may_not_send_is_refused(self):
        fake = FakeHost(terminate_raises=PermissionError())
        assert "may not signal" in refused(fake)
        assert fake.tabs_asked == []


# -- closing the tab -------------------------------------------------------------------


class TestClosingTheTab:
    @pytest.mark.parametrize("code", ["closed_tab", "closed_window"])
    def test_a_tab_with_only_its_shell_left_is_closed(self, code):
        fake = FakeHost(tab=(code, ""))
        lines = run(fake)
        assert fake.tabs_asked == [TTY]
        assert lines[-1] == closehost.OUTCOMES[code]

    def test_no_terminal_leaves_the_window(self):
        fake = FakeHost(facts=Facts(tty=None))
        lines = run(fake, tty=None)
        assert fake.tabs_asked == []
        assert lines[-1].startswith("Claude ended; the window was left open because")
        assert "no terminal" in lines[-1]

    @pytest.mark.parametrize(("app", "words"), [(False, "does not run in Terminal"),
                                                (None, "could not tell")])
    def test_a_host_outside_terminal_app_leaves_the_window(self, app, words):
        fake = FakeHost(in_terminal=app)
        lines = run(fake)
        assert fake.tabs_asked == []
        assert words in lines[-1]

    @pytest.mark.parametrize(
        ("code", "detail", "words"),
        [
            ("busy", "login,-zsh,vim", "something else is running in its tab (login,-zsh,vim)"),
            ("no_tab", "", "no Terminal tab is on ttys004"),
            ("not_running", "", "no Terminal tab is on ttys004"),
            ("tab_not_closable", "it would not", "would not close one tab of several"),
            ("failed", "boom", "Terminal could not be asked (boom)"),
            ("something new", "", "Terminal could not be asked"),
        ],
    )
    def test_anything_else_in_the_tab_leaves_it_and_says_why(self, code, detail, words):
        fake = FakeHost(tab=(code, detail))
        lines = run(fake)
        assert lines[-1].startswith("Claude ended; the window was left open because")
        assert words in lines[-1]

    def test_macos_not_allowing_it_says_how_to_grant_it(self):
        fake = FakeHost(tab=("not_allowed", "-1743"))
        last = run(fake)[-1]
        assert last.startswith("Claude ended; macOS has not allowed the oracle to control "
                               "Terminal")
        assert "System Settings > Privacy & Security > Automation" in last


# -- the binding check -----------------------------------------------------------------


def document(*rows) -> dict:
    return {"projects": [{"project": {"name": "Heap"}, "rows": list(rows)}]}


class TestBindingRefusal:
    def test_a_host_on_no_live_session_is_free(self):
        orphan = {"kind": "host", "hosts": [{"host_session_id": HOST}]}
        assert binding_refusal(document(orphan), HOST) is None

    @pytest.mark.parametrize("current", [True, False])
    def test_a_host_any_live_session_lists_is_refused(self, current):
        row = {"kind": "session", "project_name": "Heap", "session_id": "4b7b00c1-aaaa",
               "hosts": [{"host_session_id": HOST, "current": current}]}
        said = binding_refusal(document(row), HOST)
        assert said == "a live invariant session uses this Claude window now (Heap, session 4b7b00c1)"


# -- the real seams --------------------------------------------------------------------


class TestTheRealSignal:
    def test_sigterm_reaches_a_child_this_test_spawned(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            closehost.send_sigterm(child.pid)
            assert child.wait(timeout=10) == -signal.SIGTERM
        finally:
            if child.poll() is None:
                child.kill()

    @pytest.mark.parametrize("pid", [0, -1, True])
    def test_it_never_signals_a_group(self, pid):
        with pytest.raises(ValueError):
            closehost.send_sigterm(pid)


class TestTheGuard:
    """No test may signal a process it did not spawn (conftest's kill guard)."""

    def test_signalling_a_process_this_test_did_not_spawn_is_refused(self):
        from conftest import RefusedProgram

        with pytest.raises(RefusedProgram, match="kill"):
            os.kill(4_194_305, signal.SIGTERM)

    def test_asking_whether_a_pid_exists_is_allowed(self):
        os.kill(os.getpid(), 0)


# -- the command -----------------------------------------------------------------------


class TestTheCommand:
    @pytest.fixture
    def fake(self, monkeypatch):
        fake = FakeHost()
        monkeypatch.setattr(cli_main, "_oracle_host_seams", fake.seams)
        monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache(probe=FakeProbe()))
        return fake

    def argv(self, *extra) -> list[str]:
        return ["oracle", "close-host", HOST, "--pid", str(PID), "--start", START, *extra]

    def test_it_closes_and_prints_the_outcome_last(self, fake, capsys):
        assert main(self.argv("--tty", TTY)) == 0
        out = capsys.readouterr().out.strip().splitlines()
        assert out[-1] == closehost.OUTCOMES["closed_tab"]
        assert fake.signalled == [PID]

    def test_json_carries_the_lines(self, fake, capsys):
        assert main([*self.argv("--tty", TTY), "--json"]) == 0
        import json

        body = json.loads(capsys.readouterr().out)
        assert body["host_id"] == HOST
        assert body["lines"][-1] == closehost.OUTCOMES["closed_tab"]

    def test_a_refusal_is_invariants_own_line_and_exit_1(self, fake, capsys):
        fake.alive = False
        assert main(self.argv("--tty", TTY)) == 1
        err = capsys.readouterr().err
        assert err.startswith("invariant: nothing was done: process 41001 has already exited")
        assert fake.signalled == []

    def test_a_live_session_anywhere_on_the_machine_refuses_it(self, fake, tmp_path, capsys):
        alpha = make_project(tmp_path, "alpha")
        put_session(alpha, id="s1", name="one-s1", state="working", origin=str(alpha.root),
                    host_session_id=HOST)
        assert main(self.argv("--tty", TTY)) == 1
        assert "a live invariant session uses this Claude window now (alpha, session s1)" in (
            capsys.readouterr().err)
        assert fake.signalled == []

    def test_the_suite_never_reaches_the_real_seams(self, capsys):
        assert main(self.argv("--tty", TTY)) == 1
        assert "nothing was done" in capsys.readouterr().err
