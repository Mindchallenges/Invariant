"""Repro for BUG_086 — see the work-item entry (docs/backlog/BUG_086.md).

Claude Code writes a per-process record's ``procStart`` in UTC, while
``ps -o lstart=`` prints the start in the zone its ``TZ`` names, else the
machine's local zone. Observed on a Mac at UTC+2, every running pid's record
read exactly two hours earlier than a plain ``ps`` did, to the second -- the
same instant in two zones. The fix runs ``ps`` with ``TZ=UTC``.

The tests run in a fixed non-UTC zone (``Etc/GMT-2``, which is UTC+2 all
year), write a synthetic record under ``tmp_path`` (never the real
``~/.claude``), and play ``ps`` with a stand-in for ``subprocess.run`` that
prints one instant as the real ``ps`` would: in UTC when its environment's
``TZ`` is ``UTC``, else in local time. The record and ``ps`` go through the
real ``ClaudeCodeProbe`` (``read_host_processes``, ``process_facts``,
``process_start_token``); nothing else of the host is real. No process is
signalled: close host's ``terminate`` is a fake.

Asserted (docs/architecture/oracle.md):

1. "Host liveness": a running host whose record and process table describe
   the same process reads ``alive: true``, its pid verified -- not
   ``host_gone``.
2. "Close host": the check "``ps`` describes it, as ``claude``, started at
   ``--start``" passes for that same process, so close-host does not refuse
   with "started at another time".
3. The guard still bites: a process on the same pid that started at another
   instant reads not alive, and close-host refuses it, in the same zone.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import time

import pytest
from test_oracle_closehost import FakeHost
from test_oracle_scan import HOUR, NOW, FakeProbe, make_project, put_session, rows_of

from invariant.core.errors import InvariantError
from invariant.hosts import claude_code_launch as launch
from invariant.oracle.closehost import CloseRequest, close_host
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.scan import ROW_HOST, ScanCache, scan_all

PID = 8601
HOST = "27885f4e-1111-2222-3333-444455556666"
TTY = "ttys004"
#: The same instant, as each side spells it on a UTC+2 machine.
RECORD_PROC_START_UTC = "Tue Oct 20 07:00:00 2026"
PS_LSTART_LOCAL = "Tue Oct 20 09:00:00 2026"
#: A different instant on the same pid (a reused pid), an hour later.
NEWER_START_UTC = "Tue Oct 20 08:00:00 2026"
NEWER_START_LOCAL = "Tue Oct 20 10:00:00 2026"


@pytest.fixture
def utc_plus_two():
    """The machine's local zone is UTC+2 for the test, and restored after."""
    before = os.environ.get("TZ")
    os.environ["TZ"] = "Etc/GMT-2"
    time.tzset()
    try:
        assert time.strftime("%z", time.localtime(0)) == "+0200"
        yield
    finally:
        if before is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = before
        time.tzset()


@pytest.fixture
def claude_home(tmp_path):
    """A synthetic Claude Code home with one record, as Claude Code writes it."""
    home = tmp_path / "claude-home"
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / f"{PID}.json").write_text(json.dumps({
        "pid": PID, "sessionId": HOST, "name": "quiet-one", "cwd": "/w/alpha",
        "startedAt": 1_792_887_738_000, "procStart": RECORD_PROC_START_UTC,
        "pidDomain": "darwin", "kind": "interactive", "status": "idle",
    }), "utf-8")
    return home


def play_ps(monkeypatch, utc_spelling, local_spelling):
    """Stand in for ``ps -o tty=,lstart=,comm= -p PID`` as the real one behaves.

    The real ``ps`` prints ``lstart`` in the zone its environment's ``TZ``
    names, else the machine's local zone (here UTC+2): one instant, given
    here in both spellings.
    """
    asked = []
    real_run = subprocess.run

    def fake_run(argv, *args, **kwargs):
        if not argv or argv[0] != "ps":
            return real_run(argv, *args, **kwargs)  # git, for the project fixture
        asked.append(list(argv))
        assert argv[:3] == ["ps", "-o", "tty=,lstart=,comm="], argv
        assert argv[-1] == str(PID), argv
        env = kwargs.get("env")
        zone = (os.environ if env is None else env).get("TZ")
        start = utc_spelling if zone == "UTC" else local_spelling
        return subprocess.CompletedProcess(
            argv, 0, stdout=f"{TTY}  {start}     /usr/local/bin/claude\n", stderr="")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)
    return asked


@pytest.fixture
def ps_in_local_time(monkeypatch):
    """``ps`` for the process the record describes, started at its ``procStart``."""
    return play_ps(monkeypatch, RECORD_PROC_START_UTC, PS_LSTART_LOCAL)


@pytest.fixture
def ps_of_a_newer_process(monkeypatch):
    """``ps`` for a process on the same pid that started an hour after the record's."""
    return play_ps(monkeypatch, NEWER_START_UTC, NEWER_START_LOCAL)


@pytest.fixture
def real_probe(claude_home):
    return launch.ClaudeCodeProbe(home=claude_home, platform=lambda: "darwin")


class RealRecordsProbe(FakeProbe):
    """The scan's fake transcripts, with Claude Code's real record and ``ps`` reads."""

    def __init__(self, real, transcripts):
        super().__init__(transcripts)
        self.real = real

    def host_processes(self):
        return self.real.host_processes()

    def process_facts(self, pid):
        return self.real.process_facts(pid)


def test_BUG_086_a_live_host_reads_alive_when_procStart_is_UTC_and_ps_is_local(
        tmp_path, utc_plus_two, real_probe, ps_in_local_time):
    alpha = make_project(tmp_path, "alpha")
    put_session(alpha, id="s1", name="one-s1", state="working", origin=str(alpha.root),
                host_session_id="h-bound", heartbeat=NOW - 60)
    probe = RealRecordsProbe(real_probe, {"alpha": {HOST: NOW - 3 * HOUR}})
    cache = ScanCache(probe=probe, pid_alive=lambda pid: pid == PID)

    [snapshot] = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)

    host = rows_of(snapshot, ROW_HOST)[HOST].hosts[0]
    assert ps_in_local_time, "ps was never asked: the start check did not run"
    assert (host.pid, host.pid_source) == (PID, "record")
    assert host.alive is True, (
        f"a running Claude host reads alive={host.alive!r} (host_gone): record procStart "
        f"{RECORD_PROC_START_UTC!r} (UTC) vs ps lstart {PS_LSTART_LOCAL!r} (UTC+2), "
        f"tokens {host.process_start!r} vs {real_probe.process_facts(PID).start!r}")
    assert host.tty == TTY


def test_BUG_086_close_host_start_recheck_accepts_the_same_process(
        utc_plus_two, real_probe, ps_in_local_time):
    [record] = real_probe.host_processes()
    fake = FakeHost()
    seams = dataclasses.replace(fake.seams(), records=real_probe.host_processes,
                                facts=real_probe.process_facts)
    # The scan hands the record's start token to the confirm, as process_start.
    request = CloseRequest(host_id=HOST, pid=PID, start=record.start, tty=TTY)

    lines = close_host(request, seams, lambda: None)

    assert fake.signalled == [PID]
    assert lines[0].startswith(f"checked just now: process {PID} is Claude")


# -- the guard still bites: a genuinely different start, in the same non-UTC zone ------


def test_BUG_086_a_reused_pid_with_another_start_still_reads_not_alive(
        tmp_path, utc_plus_two, real_probe, ps_of_a_newer_process):
    alpha = make_project(tmp_path, "alpha")
    put_session(alpha, id="s1", name="one-s1", state="working", origin=str(alpha.root),
                host_session_id="h-bound", heartbeat=NOW - 60)
    probe = RealRecordsProbe(real_probe, {"alpha": {HOST: NOW - 3 * HOUR}})
    cache = ScanCache(probe=probe, pid_alive=lambda pid: pid == PID)

    [snapshot] = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)

    host = rows_of(snapshot, ROW_HOST)[HOST].hosts[0]
    assert ps_of_a_newer_process, "ps was never asked: the start check did not run"
    assert (host.pid, host.pid_source) == (PID, "record")
    assert host.alive is False


def test_BUG_086_close_host_refuses_a_process_that_started_at_another_time(
        utc_plus_two, real_probe, ps_of_a_newer_process):
    [record] = real_probe.host_processes()
    fake = FakeHost()
    seams = dataclasses.replace(fake.seams(), records=real_probe.host_processes,
                                facts=real_probe.process_facts)
    request = CloseRequest(host_id=HOST, pid=PID, start=record.start, tty=TTY)

    with pytest.raises(InvariantError, match="started at another time"):
        close_host(request, seams, lambda: None)

    assert ps_of_a_newer_process
    assert fake.signalled == []
