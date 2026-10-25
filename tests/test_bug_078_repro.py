"""Repro for BUG_078 — see the work-item entry (docs/backlog/BUG_078.md).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom asserted: a host whose last conversational turn (the newest ``user``
or ``assistant`` line in its transcript) is ten days old, but whose transcript
file was touched five minutes ago, is reported by the oracle's scan with a
last activity of five minutes ago. The row should say ten days. The entry's
"Expected": last activity "reflects real conversational activity: the
timestamp of the newest user or assistant entry in the host's transcript".

The host here is an orphan whose pid is alive, so its row is listed whatever
its age and the value can be read. That is the shape of the 2026-10-22
observation (orphan rows reading under 1 h while their transcripts said days).

Explicitly NOT asserted: the suspected cause (that ``ClaudeCodeTranscripts
.activity`` returns ``st_mtime``). The test goes through the real
``ClaudeCodeProbe`` and ``scan_project`` and reads only the row's
``last_activity``, the value ``oracle list`` prints.

Nothing here reads the real ``~/.claude``: the probe is pointed at a
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
DAY = 24 * 3600.0
#: The last user/assistant turn: ten days ago.
LAST_TURN = NOW - 10 * DAY
#: The file's mtime: five minutes ago, moved by a line that is not a turn.
TOUCHED = NOW - 300.0
HOST_ID = "h-idle-bug-078"


def _iso(epoch: float) -> str:
    stamp = datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="milliseconds")
    return stamp.replace("+00:00", "Z")


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


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
    # was last written as long ago as its last turn.
    store = JsonStore(manifest.state_root)
    store.write(
        f"attention/{HOST_ID}.json",
        {"host_session_id": HOST_ID, "ended": False, "pid": os.getpid(), "turn": "ended"},
    )
    attention = store.root / "attention" / f"{HOST_ID}.json"
    os.utime(attention, (LAST_TURN, LAST_TURN))

    # Its transcript, in a synthetic Claude Code home: the conversation ended
    # ten days ago; untimestamped bookkeeping lines were appended since, which
    # is what moves the mtime while the host does nothing.
    home = tmp_path / "claude"
    project_dir = home / "projects" / str(manifest.root).replace("/", "-")
    project_dir.mkdir(parents=True)
    lines = [
        {"type": "user", "timestamp": _iso(LAST_TURN - 60), "sessionId": HOST_ID,
         "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "timestamp": _iso(LAST_TURN), "sessionId": HOST_ID,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "hi"}]}},
        {"type": "last-prompt", "sessionId": HOST_ID, "lastPrompt": "hello"},
        {"type": "cost-state", "sessionId": HOST_ID, "totalCostUSD": 0.0},
    ]
    transcript = project_dir / f"{HOST_ID}.jsonl"
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines), "utf-8")
    os.utime(transcript, (TOUCHED, TOUCHED))

    probe = ClaudeCodeProbe(home=home, name_of=lambda pid: "claude")
    return entry, probe


def test_BUG_078_last_activity_is_the_last_turn_not_the_transcript_mtime(world):
    entry, probe = world
    snapshot = scan_project(entry, clock=lambda: NOW, cache=ScanCache(probe=probe))
    hosts = {
        r.hosts[0].host_session_id: r for r in snapshot.rows if r.kind == ROW_HOST
    }
    assert HOST_ID in hosts, [r.kind for r in snapshot.rows]
    idle_hours = (NOW - hosts[HOST_ID].last_activity) / 3600.0
    assert hosts[HOST_ID].last_activity == pytest.approx(LAST_TURN, abs=1.0), (
        f"host reads idle for {idle_hours:.2f} h; its last user/assistant "
        f"turn was {(NOW - LAST_TURN) / 3600.0:.0f} h ago"
    )
