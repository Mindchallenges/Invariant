"""Repro for BUG_071 — see the work-item entry (docs/backlog/BUG_071.md).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

Symptom asserted: in a project whose manifest declares the work-item store as
``items.path: ./docs/backlog`` (``items.exempt`` at its default), a Write to a
store file in the main checkout, put through the same ``pre-tool-use`` hook
the host fires before every tool call, is NOT refused. The contract is
docs/architecture/manifest.md, "Declared stores are writable stores":
declaring ``items.path`` "is what exempts it from session isolation".

Explicitly NOT asserted: the suspected cause (that ``_is_exempt`` builds its
glob patterns from the raw, un-normalised manifest string). The test reads only
the hook's permission decision.

The canonical-spelling control (``docs/backlog``, which is allowed today) is
deliberately kept out of this strict-xfail test: a failing control inside an
expected-failure test would read as the expected failure.
"""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml

from invariant.hosts.claude_code import ClaudeCodeHost

HOST_ID = "host-bug-071"


@pytest.fixture
def project(tmp_path, monkeypatch):
    from invariant.core.manifest import MANIFEST_RELPATH

    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    monkeypatch.delenv("INVARIANT_DISABLE", raising=False)

    (tmp_path / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / MANIFEST_RELPATH).write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "demo"},
                "items": {"backend": "markdown", "path": "./docs/backlog"},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "docs" / "backlog").mkdir(parents=True)
    (tmp_path / "docs" / "backlog" / "BUG_001.md").write_text(
        "---\nid: BUG_001\n---\n", encoding="utf-8"
    )

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


@pytest.mark.xfail(
    strict=True,
    reason="BUG_071 is live — delete this marker with the fix",
)
def test_BUG_071_dot_slash_items_path_store_write_is_not_denied(project):
    from invariant.cli.main import Context

    root = project
    target = root / "docs" / "backlog" / "BUG_001.md"
    payload = {
        "session_id": HOST_ID,
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(target),
            "content": "---\nid: BUG_001\n---\nupdated\n",
        },
    }
    out = ClaudeCodeHost(root).handle(
        "pre-tool-use", json.dumps(payload), Context(cwd=root, host_session_id=HOST_ID)
    )
    hook = out.get("hookSpecificOutput") or {}
    decision = hook.get("permissionDecision")
    assert decision not in ("deny", "ask"), (
        "a Write to the declared work-item store (items.path: ./docs/backlog) "
        f"in the main checkout was refused: {decision!r} — "
        f"{hook.get('permissionDecisionReason')!r}"
    )
