"""Repro for BUG_087 — see the work-item entry (docs/backlog/BUG_087.md).

Fails while the defect is live. When the fix lands, delete the strict
expected-failure marker; the assertion must then pass unchanged.

Observed: live Claude Code hosts idle for 14 to 44 hours, alive by Claude
Code's per-process record (``pid_source: both``), rooted in the project and
claimed by no other project, were classed ``orphan_host`` with cause
``active``, so Close was never offered. Each one's attention record still
said ``turn: running`` (the Stop hook never wrote ``turn-ended``), while
``<claude home>/sessions/<pid>.json`` for the same pid said ``status: idle``.

Asserted (docs/architecture/oracle.md, "oracle/recommend"): an orphan host
that is ``leftover`` and passes every close guard is offered ``invariant
oracle close-host``; "A gone, active or unknown orphan is never offered
close." The entry's expected behaviour: a host idle for longer than
``orphan_idle_minutes`` whose per-process record says it is not busy is
``leftover`` (session ended) or ``unmanaged`` (never bound), not ``active``.

The test asserts the symptom -- the class the ``oracle list --json``
document gives the row, and the step it offers -- not the suspected cause
(how ``orphan_class`` reads ``turn``). The record is a synthetic file under
``tmp_path`` read through the real ``ClaudeCodeProbe`` (never the real
``~/.claude``); ``ps`` is a stand-in; no process is signalled.
"""

from __future__ import annotations

import json
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

PID = 41011
HOST = "5a1e0f87-aaaa-bbbb-cccc-ddddeeeeffff"
TTY = "ttys011"
PROC_START = "Fri Oct 23 08:00:00 2026"
IDLE_FOR = 15 * HOUR


class RealRecordsProbe(FakeProbe):
    """The scan's fake transcripts, with Claude Code's real record and ``ps`` reads."""

    def __init__(self, real, transcripts):
        super().__init__(transcripts)
        self.real = real

    def host_processes(self):
        return self.real.host_processes()

    def process_facts(self, pid):
        return self.real.process_facts(pid)


@pytest.fixture
def fake_ps(monkeypatch):
    """``ps -o tty=,lstart=,comm= -p PID`` for the one live Claude process."""
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if not argv or argv[0] != "ps":
            return real_run(argv, *args, **kwargs)  # git, for the project fixture
        assert argv[-1] == str(PID), argv
        return subprocess.CompletedProcess(
            argv, 0, stdout=f"{TTY}  {PROC_START}     /usr/local/bin/claude\n", stderr="")

    monkeypatch.setattr(launch.subprocess, "run", run)


@pytest.mark.xfail(strict=True, reason="BUG_087 is live — delete this line with the fix")
def test_BUG_087_a_long_idle_host_with_a_stale_running_turn_is_offered_close(tmp_path, fake_ps):
    alpha = make_project(tmp_path, "alpha")
    # The session this host was bound to has ended: the host is a leftover.
    put_session(alpha, id="s-done", name="done-s-done", state="torn_down",
                origin=str(alpha.root), host_session_id=HOST, heartbeat=NOW - IDLE_FOR)
    # The hooks left the turn running; nothing has written since.
    put_attention(alpha, HOST, mtime=NOW - IDLE_FOR, pid=PID, turn="running",
                  turn_since=iso(NOW - IDLE_FOR - 60))
    # Claude Code's own record for the same pid says it is idle at the prompt.
    home = tmp_path / "claude-home"
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / f"{PID}.json").write_text(json.dumps({
        "pid": PID, "sessionId": HOST, "name": "quiet-one", "cwd": str(alpha.root),
        "startedAt": int((NOW - 2 * 24 * HOUR) * 1000), "procStart": PROC_START,
        "pidDomain": "darwin", "kind": "interactive", "status": "idle",
    }), "utf-8")
    real = launch.ClaudeCodeProbe(home=home, platform=lambda: "darwin")
    probe = RealRecordsProbe(real, {"alpha": {HOST: NOW - IDLE_FOR}})
    cache = ScanCache(probe=probe, pid_alive=lambda pid: pid == PID)

    snapshots = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=cache)
    document = assess(snapshots, NOW)

    [row] = [r for p in document["projects"] for r in p["rows"]
             if r["kind"] == "host" and r["hosts"][0]["host_session_id"] == HOST]
    host = row["hosts"][0]
    # The close guards hold: the scan saw what the field report saw.
    assert (host["pid"], host["pid_source"], host["alive"]) == (PID, "both", True)
    assert host["rooted_here"] is True and not host.get("pid_conflict")
    assert not row.get("claimed_elsewhere")

    [orphan] = [s for s in row["signals"] if s["kind"] == "orphan_host"]
    step = row["recommendation"] or {}
    assert (orphan["cause"], tuple(step.get("argv", ()))[:4]) == (
        "leftover", ("invariant", "oracle", "close-host", HOST)), (
        f"a host idle {IDLE_FOR / HOUR:.0f} h whose record says status idle reads "
        f"orphan_host cause={orphan['cause']!r}, offered {step.get('label')!r} "
        f"argv={step.get('argv')!r}")
