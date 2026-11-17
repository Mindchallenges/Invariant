"""Repro for BUG_125 — see the work-item entry (`invariant item show BUG_125`).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom: a ``compliance-reviewer`` dispatched into a running pipeline of an
isolated session reads ``agents.review_agent_ran`` as unmet ("observed none")
while it reviews, because none of its own calls wrote an ``actor`` row.

The calls modelled here are the shape the reviewer's run had on Dang (session
a8958b87, first pipeline), read from that run's transcript: every call was a
shell call spelled ``cd <session worktree> && ...``, the host's working
directory stayed the main checkout, and no call named a file path. The parent
host is bound to the session, the session has a worktree, and the payloads
carry ``agent_type: compliance-reviewer``. The same session's second reviewer,
whose one Read named a file inside the worktree, was recorded as ``actor``.

Asserted (SYMPTOM): ``agents.review_agent_ran`` passes on the pipeline after
the reviewer's own calls, before any dispatch row is written.
NOT asserted: the suspected cause (``_actor_session`` placing a pathless call
by the host's working directory) or how a fix should place the call.

Contract: docs/architecture/state_model.rst, BUG_125's Expected line: "Since
BUG_028 a subagent is recorded as ``actor`` on its first call placed in the
pipeline's session, so the reviewer satisfies ``agents.review_agent_ran``
while it runs".
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.hosts.claude_code import ClaudeCodeHost

HOST_ID = "host-parent"
REVIEWER = "compliance-reviewer"


@pytest.fixture
def world(tmp_path, monkeypatch):
    from invariant.cli.main import Context
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
                "agents": {"roles": {"review": [REVIEWER]}},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "docs" / "architecture").mkdir(parents=True)

    def git(*args):
        subprocess.run(["git", *args], cwd=str(tmp_path), check=True, capture_output=True)

    git("init", "-q", "-b", "master")
    git("config", "user.email", "dev@example.invalid")
    git("config", "user.name", "Dev")
    git("add", "-A")
    git("commit", "-qm", "init")
    return tmp_path, Context(cwd=tmp_path, host_session_id=HOST_ID)


def post(root: Path, payload: dict) -> dict:
    """The reviewer's PostToolUse, as the hook sees it: standing in the main
    checkout, carrying the parent's host session id."""
    from invariant.cli.main import Context

    return ClaudeCodeHost(root)._post_tool_use(
        payload, Context(cwd=root, host_session_id=HOST_ID)
    )


def review_gate(root: Path, ctx, session_id: str):
    from invariant.core.manifest import Manifest
    from invariant.core.rules import RuleContext, evaluate

    results = evaluate(
        ["agents.review_agent_ran"],
        RuleContext(
            manifest=Manifest.locate(root),
            cwd=root,
            session={"id": session_id},
            pipeline=ctx.pipelines.current(session_id).to_dict(),
            payload={},
            extra={},
        ),
    )
    assert len(results) == 1, "the gate under test did not evaluate"
    return results[0].verdict


def test_BUG_125_reviewer_shell_calls_into_worktree_satisfy_review_gate(world):
    root, ctx = world
    session = ctx.sessions.start("bug-fix")
    ctx.sessions.adopt_host_session(session.id, HOST_ID)
    ctx.sessions.isolate(session.id, execute=True)
    ctx.pipelines.start(session.id, task="the fix under review")
    worktree = ctx.sessions.get(session.id).worktree
    assert worktree, "precondition: the session is isolated"
    assert not review_gate(root, ctx, session.id).ok, (
        "precondition: the review gate was already satisfied"
    )

    for command in (
        "invariant session status; invariant pipeline gates --state committed",
        "git diff HEAD -- docs/",
        "invariant check",
    ):
        post(
            root,
            {
                "tool_name": "Bash",
                "agent_type": REVIEWER,
                "agent_id": "a-reviewer-run",
                "tool_input": {"command": f"cd {worktree} && {command}"},
                "tool_response": "",
            },
        )

    verdict = review_gate(root, ctx, session.id)
    evidence = ctx.pipelines.current(session.id).evidence
    assert verdict.ok, (
        f"agents.review_agent_ran unmet while the reviewer worked in the "
        f"session's worktree: {verdict.message!r}; "
        f"agents_run_how={evidence.get('agents_run_how')!r}"
    )
