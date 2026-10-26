"""Repro for BUG_040 — see the work-item entry (docs/backlog/BUG_040.md).

Fails while the defect is live. When the fix lands, delete the strict
expected-failure marker; the assertion must then pass unchanged.

Symptom: an agent dispatched for a live isolated session in project B, from a
host session whose project root is project A, is recorded nowhere. B's
pipeline evidence carries no ``agents_run`` key at all, so B's block-level
``agents.design_agent_ran`` / ``agents.review_agent_ran`` gates cannot be
satisfied by any work B's track can do.

Asserted (docs/backlog/BUG_040.md, "Expected"): "An agent dispatched for a
track's work is recorded against that track's pipeline, whatever repository
the host session driving it is rooted in." The test reads only B's pipeline
evidence after the real ``invariant hook post-tool-use`` entry point ran with
``CLAUDE_PROJECT_DIR`` naming A.

Explicitly NOT asserted: the suspected cause (that ``cmd_hook`` roots the
manifest and store at A, so ``by_worktree_containing`` searches A's registry).
Nor is the entry's widening to ``Read``/``Edit``/``Write`` asserted here; that
is the same lookup and belongs to the fix's own tests.

The control the entry names -- the same payload with ``CLAUDE_PROJECT_DIR``
naming B records the agent -- is deliberately kept out of this strict-xfail
test: a failing control inside an expected-failure test would read as the
expected failure.

SAFETY: both projects live under ``tmp_path``; ``INVARIANT_STATE_ROOT`` and
the session variables are removed so each project's state root is its own git
common directory; the host-session id is synthetic.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

HOST_ID = "host-bug040-rooted-in-a"


class _Stdin:
    def __init__(self, text: str) -> None:
        self._text = text

    def read(self) -> str:
        return self._text

    def isatty(self) -> bool:
        return False


def _project(root: Path, name: str) -> Path:
    """A minimal invariant project: a manifest at its root, one commit."""
    root.mkdir(parents=True)
    (root / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_RELPATH).write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": name},
                "session": {"worktree_dir": ".claude/worktrees"},
            }
        ),
        encoding="utf-8",
    )

    def git(*args):
        subprocess.run(
            ["git", *args], cwd=str(root), check=True, capture_output=True, text=True
        )

    git("init", "-q", "-b", "master")
    # Not address-shaped: this repository's privacy gate blocks anything that
    # parses as an email, synthetic or not.
    git("config", "user.email", "tester")
    git("config", "user.name", "Test")
    git("add", "-A")
    git("commit", "-qm", "init")
    return root


@pytest.mark.xfail(
    strict=True,
    reason="BUG_040 is live — delete this marker with the fix",
)
def test_BUG_040_a_dispatch_from_a_host_rooted_elsewhere_reaches_the_named_pipeline(
    tmp_path, monkeypatch, capsys
):
    for var in ("INVARIANT_STATE_ROOT", "INVARIANT_SESSION_ID",
                "INVARIANT_HOST_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)

    a = _project(tmp_path / "project-a", "host-project")
    b = _project(tmp_path / "project-b", "track-project")

    # A live, isolated session in B with a pipeline running.
    b_manifest = Manifest.load(b)
    sessions = SessionDomain(b_manifest)
    session = sessions.start("track")
    sessions.isolate(session.id, execute=True)
    PipelineDomain(b_manifest).start(session.id, task="the change B is designing")
    worktree = Path(sessions.get(session.id).worktree)
    assert worktree.is_dir(), "B's session worktree was not created"
    assert not (
        PipelineDomain(Manifest.load(b)).current(session.id).evidence.get("agents_run")
    ), "an agent was already recorded; nothing to prove"

    # The host session is rooted in A and standing there; the brief names B's
    # worktree by absolute path, which is the only way the agent can work there.
    monkeypatch.chdir(a)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(a))
    dispatch = {
        "session_id": HOST_ID,
        "hook_event_name": "PostToolUse",
        "tool_name": "Task",
        "tool_input": {
            "subagent_type": "feature-architect",
            "description": "design the change",
            "prompt": f"Work inside {worktree} and design the fix.",
        },
        "tool_response": {"content": "done"},
    }
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(dispatch)))
    assert main(["hook", "post-tool-use"]) == 0
    capsys.readouterr()

    evidence = PipelineDomain(Manifest.load(b)).current(session.id).evidence
    assert evidence.get("agents_run") == ["feature-architect"], (
        "B's pipeline recorded no agent for a dispatch whose brief named B's "
        f"worktree, made from a host rooted in A; evidence keys: {sorted(evidence)}"
    )
