"""Repro for BUG_062 — see the work-item entry (docs/backlog/BUG_062.md).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom asserted: an isolated session whose worktree receives an edit, through
the same hook pair the host fires around every Edit tool call
(``pre-tool-use`` then ``post-tool-use``), is in ``working`` afterwards — the
documented edge ``isolated --> working: first edit``
(docs/architecture/state_model.md, "Domain: session").

Explicitly NOT asserted: the suspected cause (that ``_advance`` has no call
site for ``working``, or that a particular hook method lacks a write). The test
reads only the session's recorded state after the edit landed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.hosts.claude_code import ClaudeCodeHost

HOST_ID = "host-bug-062"


@pytest.fixture
def project(tmp_path, monkeypatch):
    from invariant.core.manifest import MANIFEST_RELPATH

    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)

    (tmp_path / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / MANIFEST_RELPATH).write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "demo"},
                "session": {"worktree_dir": ".claude/worktrees"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "thing.py").write_text("x = 1\n", encoding="utf-8")

    def git(*args):
        subprocess.run(
            ["git", *args], cwd=str(tmp_path), check=True, capture_output=True
        )

    git("init", "-q", "-b", "master")
    git("config", "user.email", "dev@example.invalid")
    git("config", "user.name", "Dev")
    git("add", "-A")
    git("commit", "-qm", "init")
    return tmp_path


def _ctx(root: Path):
    from invariant.cli.main import Context

    return Context(cwd=root, host_session_id=HOST_ID)


@pytest.mark.xfail(
    strict=True,
    reason="BUG_062 is live — delete this marker with the fix",
)
def test_BUG_062_first_edit_in_isolated_worktree_leaves_session_working(project):
    root = project
    ctx = _ctx(root)
    session = ctx.sessions.start("first-edit")
    ctx.sessions.adopt_host_session(session.id, HOST_ID)
    ctx.sessions.isolate(session.id, execute=True)
    worktree = Path(ctx.sessions.get(session.id).worktree)
    assert ctx.sessions.get(session.id).state == "isolated"  # precondition

    target = worktree / "src" / "thing.py"
    payload = {
        "session_id": HOST_ID,
        "tool_name": "Edit",
        "tool_input": {
            "file_path": str(target),
            "old_string": "x = 1",
            "new_string": "x = 2",
        },
    }
    host = ClaudeCodeHost(root)

    # The host asks first. A denial here would mean the edit never happened,
    # which is not the reported symptom — guard it so a red run cannot be a
    # strawman.
    pre = host.handle("pre-tool-use", json.dumps(payload), _ctx(root))
    decision = (pre.get("hookSpecificOutput") or {}).get("permissionDecision")
    assert decision not in ("deny", "ask"), f"setup: edit was refused: {pre}"

    # The tool runs: the edit really lands in the worktree.
    target.write_text("x = 2\n", encoding="utf-8")

    # The host reports the completed tool call.
    host.handle(
        "post-tool-use",
        json.dumps({**payload, "tool_response": {"filePath": str(target)}}),
        _ctx(root),
    )

    state = _ctx(root).sessions.get(session.id).state
    assert state == "working", (
        f"session {session.id} made an edit in its worktree and is still "
        f"{state!r}; the state model draws `isolated --> working: first edit`"
    )
