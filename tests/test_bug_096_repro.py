"""Repro for BUG_096 — see the work-item entry (docs/backlog/BUG_096.md).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom asserted: a host whose main transcript's last turn is two hours old,
while its subagent's transcript (Claude Code writes it to
``<host transcript dir>/subagents/agent-*.jsonl``) has a turn from one minute
ago, is reported by the oracle's scan with a last activity of two hours ago.
docs/architecture/oracle.md, ``oracle/scan``, "Last activity": "Last activity
is the latest of the host transcripts' last turns ... A subagent's
(`isSidechain`) entry counts too." The row should say one minute.

The host here is an orphan whose pid is alive, so its row is listed whatever
its age and the value can be read (the same shape as BUG_078's repro).

Explicitly NOT asserted: the suspected cause (that
``ClaudeCodeTranscripts.paths`` globs ``*.jsonl`` one level deep only). The
test goes through the real ``ClaudeCodeProbe`` and ``scan_project`` and reads
only the row's ``last_activity``, the value ``oracle list`` prints.

Nothing here reads the real Claude Code home: the probe is pointed at a
synthetic home under ``tmp_path``. The oracle home is the suite's isolated
``INVARIANT_HOME``.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.store import JsonStore
from invariant.hosts.claude_code_launch import ClaudeCodeProbe
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.scan import ROW_HOST, ScanCache, scan_project

NOW = time.time()
#: The main transcript's last user/assistant turn: two hours ago.
MAIN_TURN = NOW - 2 * 3600.0
#: The subagent transcript's last turn: one minute ago.
SUBAGENT_TURN = NOW - 60.0
HOST_ID = "h-busy-subagent-bug-096"
AGENT_ID = "a0096subagent"


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds")
    return stamp.replace("+00:00", "Z")


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


def _write_jsonl(path: Path, lines: list[dict], mtime: float) -> None:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), "utf-8")
    os.utime(path, (mtime, mtime))


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    (root / MANIFEST_RELPATH).parent.mkdir(parents=True)
    (root / MANIFEST_RELPATH).write_text(
        yaml.safe_dump({"version": 1, "project": {"name": "proj"}}), "utf-8"
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "init")
    manifest = Manifest.load(root.resolve())
    entry, _ = ProjectIndex(oracle_home()).register(manifest, via="manual")

    # An orphan host whose pid is alive and is "claude"; its attention record
    # was last written when the main conversation last had a turn (the hooks
    # are quiet while the subagent works).
    store = JsonStore(manifest.state_root)
    store.write(
        f"attention/{HOST_ID}.json",
        {"host_session_id": HOST_ID, "ended": False, "pid": os.getpid(), "turn": "running"},
    )
    attention = store.root / "attention" / f"{HOST_ID}.json"
    os.utime(attention, (MAIN_TURN, MAIN_TURN))

    # The synthetic Claude Code home, laid out as Claude Code writes it: the
    # main transcript at <project dir>/<host id>.jsonl, and the subagent's at
    # <project dir>/<host id>/subagents/agent-<agent id>.jsonl.
    home = tmp_path / "claude"
    project_dir = home / "projects" / str(manifest.root).replace("/", "-")
    project_dir.mkdir(parents=True)
    _write_jsonl(
        project_dir / f"{HOST_ID}.jsonl",
        [
            {"type": "user", "timestamp": _iso(MAIN_TURN - 60), "sessionId": HOST_ID,
             "message": {"role": "user", "content": "run the subagent"}},
            {"type": "assistant", "timestamp": _iso(MAIN_TURN), "sessionId": HOST_ID,
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "dispatching"}]}},
        ],
        MAIN_TURN,
    )
    subagents = project_dir / HOST_ID / "subagents"
    subagents.mkdir(parents=True)
    _write_jsonl(
        subagents / f"agent-{AGENT_ID}.jsonl",
        [
            {"type": "user", "timestamp": _iso(MAIN_TURN), "sessionId": HOST_ID,
             "isSidechain": True, "agentId": AGENT_ID,
             "message": {"role": "user", "content": "do the work"}},
            {"type": "assistant", "timestamp": _iso(SUBAGENT_TURN), "sessionId": HOST_ID,
             "isSidechain": True, "agentId": AGENT_ID,
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "still working"}]}},
        ],
        SUBAGENT_TURN,
    )

    probe = ClaudeCodeProbe(home=home, name_of=lambda pid: "claude")
    return entry, probe


@pytest.mark.xfail(
    strict=True,
    reason="BUG_096 is live — delete this marker with the fix",
)
def test_BUG_096_subagent_turn_moves_the_hosts_last_activity(world):
    entry, probe = world
    snapshot = scan_project(entry, clock=lambda: NOW, cache=ScanCache(probe=probe))
    hosts = {
        r.hosts[0].host_session_id: r for r in snapshot.rows if r.kind == ROW_HOST
    }
    assert HOST_ID in hosts, [r.kind for r in snapshot.rows]
    idle_minutes = (NOW - hosts[HOST_ID].last_activity) / 60.0
    assert hosts[HOST_ID].last_activity == pytest.approx(SUBAGENT_TURN, abs=1.0), (
        f"host reads idle for {idle_minutes:.0f} min; its subagent's last turn "
        f"was {(NOW - SUBAGENT_TURN) / 60.0:.0f} min ago"
    )
