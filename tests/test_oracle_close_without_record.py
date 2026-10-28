"""Close a live Claude window that Claude Code keeps no per-process record for (BUG_099).

A host started with an inherited ``CLAUDE_CODE_CHILD_SESSION`` writes no
``<claude home>/sessions/<pid>.json``, so only its attention record names its
pid. Every close guard still has to hold, each by evidence and none relaxed:

* ``ps``: the pid is ``claude``, with a start and its terminal;
* it started before the attention record was last written, so it is the
  process that wrote the pid and not a later one on a reused pid;
* Claude Code's records were read, and none names the pid for another host;
* ``lsof``: its working directory, which must be in the project.

What cannot be verified is named on the card instead of the generic line.
Every fact here comes from a fake: no real process is asked anything, no
``lsof`` or ``ps`` runs, the real ``~/.claude`` is never read, and nothing is
signalled.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

import pytest
from test_oracle_scan import (
    HOUR,
    NOW,
    FakeProbe,
    make_project,
    put_attention,
    put_session,
)

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import InvariantError
from invariant.hosts import claude_code_launch as launch
from invariant.oracle import closehost, panel
from invariant.oracle.closehost import (
    CloseRequest,
    HostSeams,
    attention_refusal,
    close_host,
)
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import (
    CLOSE_CLAIMED,
    CLOSE_CWD,
    CLOSE_NEWER,
    CLOSE_NOT_ROOTED,
    CLOSE_OTHER,
    CLOSE_PS,
    CLOSE_RECORDS,
    CLOSE_START,
    assess,
    close_blocker,
)
from invariant.oracle.scan import (
    PID_ATTENTION,
    PID_ATTENTION_PS,
    PID_RECORD,
    ROW_HOST,
    HostView,
    Row,
    ScanCache,
    scan_all,
)
from invariant.oracle.signals import ORPHAN_ACTIVE, ORPHAN_LEFTOVER, ORPHAN_UNMANAGED

HOST = "5c2e7a10-aaaa-bbbb-cccc-ddddeeeeffff"
PID = 42099
OTHER = 42100
TTY = "ttys021"
IDLE = 15 * HOUR
#: The process started before the host last wrote its attention record.
STARTED = NOW - 20 * HOUR


def token(epoch: float) -> str:
    """A start token, spelled as the probe spells one, built from an epoch."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch))


START = token(STARTED)


@dataclass
class Facts:
    is_host: bool = True
    start: str | None = START
    tty: str | None = TTY
    started: float | None = STARTED


@dataclass
class Rec:
    session_id: str
    pid: int
    name: str | None = None
    cwd: str | None = None
    started: float | None = None
    start: str | None = START
    status: str | None = None


class AttentionProbe(FakeProbe):
    """No per-process record for the host; ``ps`` and ``lsof`` answers per pid."""

    def __init__(self, facts=None, cwd=None, records=(), records_fail=None,
                 facts_fail=None, cwd_fail=None, transcripts=None):
        super().__init__(transcripts)
        self.facts = facts if facts is not None else {}
        self.cwd = cwd if cwd is not None else {}
        self.records = list(records)
        self.records_fail = records_fail
        self.facts_fail = facts_fail
        self.cwd_fail = cwd_fail
        self.cwd_asked: list[int] = []
        self.facts_asked: list[int] = []

    def is_host_process(self, pid: int) -> bool:
        self.asked.append(pid)
        return pid in (PID, OTHER)

    def host_processes(self):
        if self.records_fail is not None:
            raise self.records_fail
        return list(self.records)

    def process_facts(self, pid: int):
        self.facts_asked.append(pid)
        if self.facts_fail is not None:
            raise self.facts_fail
        return self.facts.get(pid)

    def process_cwd(self, pid: int):
        self.cwd_asked.append(pid)
        if self.cwd_fail is not None:
            raise self.cwd_fail
        return self.cwd.get(pid)


class NoCwdProbe(AttentionProbe):
    """A probe from before BUG_099: it cannot read a working directory."""

    process_cwd = None


class NoFactsProbe(FakeProbe):
    """A probe that answers the name check and nothing about the process table."""

    def is_host_process(self, pid: int) -> bool:
        return pid == PID

    def host_processes(self):
        return []


def alive(pid) -> bool:
    return pid in (PID, OTHER)


@pytest.fixture
def alpha(tmp_path):
    """A project with a leftover host: its session ended, its pid only in attention."""
    alpha = make_project(tmp_path, "alpha")
    put_session(alpha, id="s-done", name="done-s-done", state="torn_down",
                origin=str(alpha.root), host_session_id=HOST, heartbeat=NOW - IDLE)
    put_attention(alpha, HOST, mtime=NOW - IDLE, pid=PID, turn="ended")
    return alpha


def probe_for(alpha, **changes) -> AttentionProbe:
    fields = {"facts": {PID: Facts()}, "cwd": {PID: str(alpha.root)},
              "transcripts": {"alpha": {HOST: NOW - IDLE}}}
    fields.update(changes)
    return AttentionProbe(**fields)


def read(probe, cache=None):
    cache = cache or ScanCache(probe=probe, pid_alive=alive)
    return scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)


def the_row(probe, cache=None) -> dict:
    document = assess(read(probe, cache), NOW)
    [row] = [r for p in document["projects"] for r in p["rows"]
             if r["kind"] == ROW_HOST and r["hosts"][0]["host_session_id"] == HOST]
    return row


def offered(probe) -> tuple[dict, dict]:
    row = the_row(probe)
    return row["hosts"][0], row["recommendation"] or {}


# -- the scan vouches for the pid -------------------------------------------------------


class TestAVerifiedAttentionPid:
    def test_every_fact_is_read_and_close_is_offered(self, alpha):
        host, step = offered(probe_for(alpha))
        assert host["pid_source"] == PID_ATTENTION_PS
        assert (host["pid"], host["alive"], host["unverified"]) == (PID, True, None)
        assert (host["process_start"], host["tty"]) == (START, TTY)
        assert host["cwd"] == str(alpha.root)
        assert host["rooted_here"] is True
        assert step["argv"] == ["invariant", "oracle", "close-host", HOST, "--pid", str(PID),
                                "--start", START, "--tty", TTY]
        assert step["kind"] == "run"

    def test_a_folder_under_the_root_is_rooted_here(self, alpha):
        host, step = offered(probe_for(alpha, cwd={PID: str(alpha.root / "sub")}))
        assert host["rooted_here"] is True
        assert step["argv"][2] == "close-host"

    def test_no_tty_closes_without_one(self, alpha):
        _, step = offered(probe_for(alpha, facts={PID: Facts(tty=None)}))
        assert step["argv"][-2:] == ["--start", START]

    def test_other_hosts_records_do_not_stop_it(self, alpha):
        records = [Rec("someone-else", OTHER)]
        host, step = offered(probe_for(alpha, records=records))
        assert host["pid_source"] == PID_ATTENTION_PS
        assert step["argv"][2] == "close-host"


class TestWhatCannotBeVerified:
    """Each guard path: the pid stays ``attention``, and the card says why."""

    def assert_not_offered(self, alpha, reason, words, **changes):
        host, step = offered(probe_for(alpha, **changes))
        assert host["pid_source"] == PID_ATTENTION
        assert not step.get("argv")
        assert step["kind"] == "info"
        assert step["reason"] == reason
        assert words in step["label"]
        return host

    def test_ps_unreadable(self, alpha):
        host = self.assert_not_offered(alpha, CLOSE_PS, "could not describe", facts={PID: None})
        assert host["unverified"] == "ps"

    def test_ps_that_raises(self, alpha):
        self.assert_not_offered(alpha, CLOSE_PS, "could not describe",
                                facts_fail=OSError("ps went away"))

    def test_a_probe_with_no_process_table(self, alpha):
        probe = NoFactsProbe({"alpha": {HOST: NOW - IDLE}})
        host, step = offered(probe)
        assert (host["pid_source"], host["unverified"]) == (PID_ATTENTION, "ps")
        assert step["reason"] == CLOSE_PS

    def test_wrong_command_is_a_reused_pid(self, alpha):
        host, step = offered(probe_for(alpha, facts={PID: Facts(is_host=False)}))
        assert host["alive"] is False
        assert host["pid_source"] == PID_ATTENTION
        assert not step.get("argv")
        assert not step.get("reason")

    @pytest.mark.parametrize("facts", [Facts(start=None), Facts(started=None)])
    def test_no_start(self, alpha, facts):
        self.assert_not_offered(alpha, CLOSE_START, "start could not be read",
                                facts={PID: facts})

    def test_a_start_after_the_last_write_may_be_another_process(self, alpha):
        later = NOW - HOUR
        host = self.assert_not_offered(
            alpha, CLOSE_NEWER, "started after the window last reported",
            facts={PID: Facts(start=token(later), started=later)})
        assert host["unverified"] == "newer"

    def test_records_unreadable(self, alpha):
        self.assert_not_offered(alpha, CLOSE_RECORDS, "process records could not be read",
                                records_fail=FileNotFoundError("no sessions directory"))

    def test_another_hosts_record_names_the_pid(self, alpha):
        self.assert_not_offered(alpha, CLOSE_OTHER, "another Claude conversation",
                                records=[Rec("another-conversation", PID)])

    def test_cwd_unreadable(self, alpha):
        host = self.assert_not_offered(alpha, CLOSE_CWD, "working folder could not be read",
                                       cwd={})
        assert host["rooted_here"] is None
        assert host["cwd"] is None

    def test_cwd_that_raises(self, alpha):
        self.assert_not_offered(alpha, CLOSE_CWD, "working folder could not be read",
                                cwd_fail=OSError("lsof went away"))

    def test_a_probe_that_cannot_read_a_folder(self, alpha):
        probe = NoCwdProbe(facts={PID: Facts()}, transcripts={"alpha": {HOST: NOW - IDLE}})
        _, step = offered(probe)
        assert step["reason"] == CLOSE_CWD

    def test_cwd_outside_the_project(self, alpha, tmp_path):
        host, step = offered(probe_for(alpha, cwd={PID: str(tmp_path / "elsewhere")}))
        assert host["pid_source"] == PID_ATTENTION_PS
        assert host["rooted_here"] is False
        assert step["reason"] == CLOSE_NOT_ROOTED
        assert "outside this project" in step["label"]


class TestKeptCheap:
    def test_a_host_a_live_session_holds_is_not_read_further(self, alpha):
        put_session(alpha, id="s-live", name="live-s-live", state="working",
                    origin=str(alpha.root), host_session_id=HOST, heartbeat=NOW - 60)
        probe = probe_for(alpha)
        read(probe)
        assert probe.cwd_asked == []
        assert probe.facts_asked == []

    def test_a_gone_host_is_not_read_further(self, alpha):
        put_attention(alpha, HOST, mtime=NOW - IDLE, pid=OTHER + 1, turn="ended")
        probe = probe_for(alpha)
        read(probe)
        assert probe.cwd_asked == []

    def test_the_folder_is_read_once_per_ttl_and_dropped_with_the_pid(self, alpha):
        probe = probe_for(alpha)
        living = {PID}
        cache = ScanCache(probe=probe, pid_alive=lambda pid: pid in living)
        read(probe, cache)
        read(probe, cache)
        assert probe.cwd_asked == [PID]
        living.clear()
        assert cache.process_alive(PID, START) == (False, None)
        living.add(PID)
        read(probe, cache)
        assert probe.cwd_asked == [PID, PID]

    def test_an_empty_folder_answer_is_unread(self, alpha):
        _, step = offered(probe_for(alpha, cwd={PID: ""}))
        assert step["reason"] == CLOSE_CWD


# -- the guards, as reasons -------------------------------------------------------------


def a_row(**host_fields) -> tuple[Row, HostView]:
    fields = {"host_session_id": HOST, "pid": PID, "alive": True, "pid_source": PID_RECORD,
              "process_start": START, "rooted_here": True}
    fields.update(host_fields)
    host = HostView(**fields)
    return Row(kind=ROW_HOST, project="K", hosts=[host]), host


class TestCloseBlocker:
    def test_every_guard_holding_blocks_nothing(self):
        row, host = a_row()
        assert close_blocker(row, host, ORPHAN_LEFTOVER) is None

    @pytest.mark.parametrize(("fields", "reason"), [
        ({"process_start": None}, CLOSE_START),
        ({"rooted_here": None}, CLOSE_CWD),
        ({"rooted_here": False}, CLOSE_NOT_ROOTED),
        ({"pid_source": PID_ATTENTION}, CLOSE_PS),
        ({"pid_source": PID_ATTENTION, "unverified": "records"}, CLOSE_RECORDS),
    ])
    def test_the_first_guard_that_fails(self, fields, reason):
        row, host = a_row(**fields)
        assert close_blocker(row, host, ORPHAN_UNMANAGED) == reason

    def test_a_host_another_project_lists(self):
        row, host = a_row()
        row.claimed_elsewhere = "beta"
        assert close_blocker(row, host, ORPHAN_LEFTOVER) == CLOSE_CLAIMED

    @pytest.mark.parametrize(("fields", "cause"), [
        ({}, ORPHAN_ACTIVE),
        ({"alive": None}, ORPHAN_LEFTOVER),
        ({"alive": False}, ORPHAN_LEFTOVER),
        ({"pid": None}, ORPHAN_LEFTOVER),
        ({"pid_conflict": [{"source": "record"}]}, ORPHAN_LEFTOVER),
    ])
    def test_hosts_the_guards_do_not_apply_to(self, fields, cause):
        row, host = a_row(**fields)
        assert close_blocker(row, host, cause) is None

    def test_no_host(self):
        row, _ = a_row()
        assert close_blocker(row, None, ORPHAN_LEFTOVER) is None


# -- the panel says why -----------------------------------------------------------------


class TestThePanelSaysWhy:
    def shown(self, alpha, **changes):
        document = assess(read(probe_for(alpha, **changes)), NOW)
        return panel.view(document, acting=True)

    def test_the_card_names_what_could_not_be_verified(self, alpha):
        shown = self.shown(alpha, cwd={})
        [card] = shown["leftovers"]["sections"][0]["rows"]
        assert card["line"] == (f"Still open on the Mac · window {HOST[:8]} · "
                                "cannot close from here: invariant could not read its folder")
        assert card["button"] is None

    def test_a_closeable_card_is_unchanged(self, alpha):
        [card] = self.shown(alpha)["leftovers"]["sections"][0]["rows"]
        assert card["line"] == f"Still open on the Mac · window {HOST[:8]}"
        assert card["button"] == {"text": "Close…"}

    def test_every_reason_has_words(self):
        reasons = (CLOSE_PS, CLOSE_START, CLOSE_NEWER, CLOSE_RECORDS, CLOSE_OTHER, CLOSE_CWD,
                   CLOSE_NOT_ROOTED, CLOSE_CLAIMED)
        assert set(panel._NO_CLOSE) == set(reasons)


# -- close-host checks it again, without a record ---------------------------------------


@dataclass
class Host:
    facts: Facts | None = None
    records: tuple = ()
    signalled: list | None = None

    def seams(self) -> HostSeams:
        self.signalled = []
        exits = iter([True, False])
        return HostSeams(
            records=lambda: list(self.records),
            facts=lambda pid: self.facts,
            alive=lambda pid: next(exits, False) if self.signalled else True,
            terminate=self.signalled.append,
            in_terminal=lambda pid: True,
            close_tab=lambda tty: ("closed_tab", ""),
            sleep=lambda seconds: None,
            monotonic=lambda: 0.0,
        )


def request(**changes) -> CloseRequest:
    fields = {"host_id": HOST, "pid": PID, "start": START, "tty": TTY}
    fields.update(changes)
    return CloseRequest(**fields)


class TestCloseHostWithoutARecord:
    def test_a_fresh_scan_that_vouches_lets_it_close(self):
        host = Host(facts=Facts())
        lines = close_host(request(), host.seams(), lambda: None, lambda: None)
        assert lines[-1] == closehost.OUTCOMES["closed_tab"]
        assert host.signalled == [PID]

    def test_no_voucher_is_the_old_refusal(self):
        host = Host(facts=Facts())
        with pytest.raises(InvariantError, match=f"no record of process {PID} any more"):
            close_host(request(), host.seams(), lambda: None)
        assert host.signalled == []

    def test_a_fresh_scan_that_does_not_vouch_refuses(self):
        host = Host(facts=Facts())
        with pytest.raises(InvariantError, match="nothing was done: not this one"):
            close_host(request(), host.seams(), lambda: None, lambda: "not this one")
        assert host.signalled == []

    @pytest.mark.parametrize(("facts", "words"), [
        (None, "cannot describe"),
        (Facts(is_host=False), "not Claude any more"),
        (Facts(start=token(NOW - HOUR)), "started at another time"),
        (Facts(tty="ttys099"), "another terminal"),
    ])
    def test_every_process_check_still_applies(self, facts, words):
        host = Host(facts=facts)
        with pytest.raises(InvariantError, match=words):
            close_host(request(), host.seams(), lambda: None, lambda: None)
        assert host.signalled == []

    def test_a_record_for_the_pid_is_checked_as_before(self):
        host = Host(facts=Facts(), records=(Rec("another-conversation", PID),))
        with pytest.raises(InvariantError, match="another Claude session"):
            close_host(request(), host.seams(), lambda: None, lambda: None)


def document(**host_fields) -> dict:
    fields = {"host_session_id": HOST, "pid": PID, "pid_source": PID_ATTENTION_PS,
              "process_start": START}
    fields.update(host_fields)
    rows = [{"kind": "session", "hosts": [{"host_session_id": "other"}]},
            {"kind": ROW_HOST, "hosts": [{"host_session_id": "another"}]},
            {"kind": ROW_HOST, "hosts": [fields]}]
    return {"projects": [{"rows": rows}]}


class TestAttentionRefusal:
    def test_the_same_verified_pid_at_the_same_start(self):
        assert attention_refusal(document(), HOST, PID, START) is None

    @pytest.mark.parametrize("fields", [
        {"pid": OTHER}, {"pid_source": PID_ATTENTION}, {"pid_source": PID_RECORD},
        {"process_start": token(NOW - HOUR)},
    ])
    def test_anything_else_is_refused(self, fields):
        said = attention_refusal(document(**fields), HOST, PID, START)
        assert said == (f"Claude Code has no record of process {PID}, and the window's own "
                        "record no longer verifies it as this Claude")

    def test_no_row_for_the_host(self):
        said = attention_refusal({"projects": [{"rows": []}]}, HOST, PID, START)
        assert said == f"Claude Code has no record of process {PID}, and nothing else names it any more"


class TestTheCommand:
    @pytest.fixture
    def host(self, monkeypatch, alpha):
        host = Host(facts=Facts())
        monkeypatch.setattr(cli_main, "_oracle_host_seams", host.seams)
        return host

    def argv(self):
        return ["oracle", "close-host", HOST, "--pid", str(PID), "--start", START,
                "--tty", TTY]

    def test_a_verified_window_closes(self, host, alpha, monkeypatch, capsys):
        monkeypatch.setattr(cli_main, "_oracle_cache",
                            lambda: ScanCache(probe=probe_for(alpha), pid_alive=alive))
        assert main(self.argv()) == 0
        assert capsys.readouterr().out.strip().splitlines()[-1] == (
            closehost.OUTCOMES["closed_tab"])
        assert host.signalled == [PID]

    def test_a_folder_that_cannot_be_read_now_is_refused(self, host, alpha, monkeypatch,
                                                          capsys):
        monkeypatch.setattr(cli_main, "_oracle_cache",
                            lambda: ScanCache(probe=probe_for(alpha, cwd={}),
                                              pid_alive=alive))
        assert main(self.argv()) == 1
        assert "no longer verifies it as this Claude" in capsys.readouterr().err
        assert host.signalled == []

    def test_a_pid_the_scan_reads_as_another_process_is_refused(self, host, alpha,
                                                                 monkeypatch, capsys):
        later = NOW - HOUR
        probe = probe_for(alpha, facts={PID: Facts(start=token(later), started=later)})
        monkeypatch.setattr(cli_main, "_oracle_cache",
                            lambda: ScanCache(probe=probe, pid_alive=alive))
        assert main(self.argv()) == 1
        assert "no longer verifies it as this Claude" in capsys.readouterr().err
        assert host.signalled == []


# -- the probe reads the folder ---------------------------------------------------------


class TestProcessCwd:
    def canned(self, monkeypatch, stdout="", returncode=0, raises=None):
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            if raises:
                raise raises
            return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

        monkeypatch.setattr(launch.subprocess, "run", run)
        return calls

    def test_one_lsof_names_the_folder(self, monkeypatch, tmp_path):
        calls = self.canned(monkeypatch, f"p52001\nfcwd\nn{tmp_path}\n")
        assert launch.process_cwd(52001) == str(tmp_path)
        assert calls == [["lsof", "-a", "-p", "52001", "-d", "cwd", "-Fn"]]

    @pytest.mark.parametrize("kwargs", [{"returncode": 1},
                                        {"stdout": "p1\nfcwd\n"},
                                        {"stdout": "p1\nfcwd\nnrelative\n"},
                                        {"raises": OSError("no lsof")},
                                        {"raises": subprocess.TimeoutExpired("lsof", 5)}])
    def test_no_answer_is_none(self, monkeypatch, kwargs):
        self.canned(monkeypatch, **kwargs)
        assert launch.process_cwd(1) is None

    def test_the_probe_asks_through_its_seam(self):
        probe = launch.ClaudeCodeProbe(cwd_of=lambda pid: f"cwd-{pid}")
        assert probe.process_cwd(9) == "cwd-9"

    def test_the_start_is_also_epoch_seconds(self, monkeypatch):
        lstart = time.strftime("%a %b %d %H:%M:%S %Y", time.gmtime(STARTED))
        self.canned(monkeypatch, f"ttys004  {lstart}     /opt/bin/claude\n")
        facts = launch.process_facts(52001)
        assert (facts.start, facts.started) == (START, float(int(STARTED)))
