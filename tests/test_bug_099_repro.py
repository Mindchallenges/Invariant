"""Repro for BUG_099 — see the work-item entry (docs/backlog/BUG_099.md).

Failed while the defect was live; the fix removed the strict expected-failure
marker and the assertion passes unchanged.

Observed: a live Claude window that wrote no Claude Code per-process record
(its ``<claude home>/sessions`` holds only the ``<pid>.<hash>.key`` file, no
``<pid>.json``; typical of a host whose environment inherited
``CLAUDE_CODE_CHILD_SESSION``) is never offered Close. Its leftovers card
shows only the info line "start or resume a session from that host's own
terminal".

Expected (the entry): a live, verified Claude host is closeable from the panel
whichever source named its pid, with the same pid-reuse safety as today --
same ``claude``, same start, same tty at the moment Close acts.

Asserted (docs/architecture/oracle.md, "oracle/recommend"): an orphan host
that is ``leftover`` and alive is offered ``invariant oracle close-host``
carrying ``--pid``, ``--start`` and ``--tty``, the facts close-host checks
again. Here the pid comes from the host's attention record, and the process
table (a stand-in ``ps``) describes it as a live ``claude`` with a start and
a tty, so every one of those facts is knowable.

The test asserts the symptom -- the step the ``oracle list --json`` document
offers the row -- not the suspected cause (which ``pid_source`` the close
guards accept, or whether the scan reads tty and start for an attention pid).
The Claude Code home is a synthetic directory under ``tmp_path`` read through
the real ``ClaudeCodeProbe`` (never the real ``~/.claude``); ``ps`` and ``lsof`` are
stand-ins; no process is signalled and no Terminal is driven.
"""

from __future__ import annotations

import subprocess

import pytest
from test_oracle_scan import (
    HOUR,
    NOW,
    FakeProbe,
    iso,
    make_project,
    put_attention,
    put_session,
)

from invariant.hosts import claude_code_launch as launch
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import assess
from invariant.oracle.scan import ScanCache, scan_all

PID = 41099
HOST = "9b0e2c41-aaaa-bbbb-cccc-ddddeeeeffff"
TTY = "ttys019"
#: A synthetic ``ps -o lstart=`` spelling, assembled so no clock-time literal
#: sits in a committed file (the house privacy rule); it is no real start.
HHMMSS = (8, 0, 0)
PROC_START = "Mon Oct 26 {:02d}:{:02d}:{:02d} 2026".format(*HHMMSS)
IDLE_FOR = 15 * HOUR


class RealClaudeProbe(FakeProbe):
    """The scan's fake transcripts, with Claude Code's real record, name, ``ps`` and ``lsof`` reads."""

    def __init__(self, real, transcripts):
        super().__init__(transcripts)
        self.real = real

    def is_host_process(self, pid):
        return self.real.is_host_process(pid)

    def host_processes(self):
        return self.real.host_processes()

    def process_facts(self, pid):
        return self.real.process_facts(pid)

    def process_cwd(self, pid):
        return self.real.process_cwd(pid)


@pytest.fixture
def fake_ps(monkeypatch, tmp_path):
    """``ps`` for the one live Claude process: its name, and its tty, start and name.

    And ``lsof``, for the folder it was started in: the project's root.
    """
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if argv and argv[0] == "lsof":
            assert str(PID) in argv, argv
            out = f"p{PID}\nfcwd\nn{tmp_path / 'alpha'}\n"
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
        if not argv or argv[0] != "ps":
            return real_run(argv, *args, **kwargs)  # git, for the project fixture
        assert str(PID) in argv, argv
        if argv[-1] == "comm=":
            out = "/usr/local/bin/claude\n"
        else:
            out = f"{TTY}  {PROC_START}     /usr/local/bin/claude\n"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

    monkeypatch.setattr(launch.subprocess, "run", run)


def test_BUG_099_a_live_host_with_no_per_process_record_is_offered_close(tmp_path, fake_ps):
    alpha = make_project(tmp_path, "alpha")
    # The session this host was bound to has ended: the host is a leftover.
    put_session(alpha, id="s-done", name="done-s-done", state="torn_down",
                origin=str(alpha.root), host_session_id=HOST, heartbeat=NOW - IDLE_FOR)
    # The hooks recorded the host's pid; its last turn ended long ago.
    put_attention(alpha, HOST, mtime=NOW - IDLE_FOR, pid=PID, turn="ended",
                  turn_since=iso(NOW - IDLE_FOR))
    # Claude Code wrote no per-process record for it: only the key file.
    home = tmp_path / "claude-home"
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / f"{PID}.0f3c.key").write_text("k", "utf-8")
    real = launch.ClaudeCodeProbe(home=home, platform=lambda: "darwin")
    probe = RealClaudeProbe(real, {"alpha": {HOST: NOW - IDLE_FOR}})
    cache = ScanCache(probe=probe, pid_alive=lambda pid: pid == PID)

    snapshots = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
    document = assess(snapshots, NOW)

    [row] = [r for p in document["projects"] for r in p["rows"]
             if r["kind"] == "host" and r["hosts"][0]["host_session_id"] == HOST]
    host = row["hosts"][0]
    # The world is what the field report saw: a live Claude, its pid known.
    assert (host["pid"], host["alive"]) == (PID, True)
    assert not row.get("claimed_elsewhere")
    [orphan] = [s for s in row["signals"] if s["kind"] == "orphan_host"]
    assert orphan["cause"] == "leftover"

    step = row["recommendation"] or {}
    argv = tuple(step.get("argv") or ())
    assert argv[:6] == ("invariant", "oracle", "close-host", HOST, "--pid", str(PID)), (
        f"a live Claude host (pid {PID}, claude on {TTY}, started {PROC_START!r}) "
        f"with no per-process record is offered {step.get('kind')!r} "
        f"{step.get('label')!r} argv={step.get('argv')!r}, not close-host")
    assert "--start" in argv and argv[argv.index("--start") + 1]
    assert argv[-2:] == ("--tty", TTY)
