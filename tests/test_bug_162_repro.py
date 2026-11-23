"""Repro for BUG_162 — see the work-item entry (docs/backlog.rst).

Fails while the defect is live. When the fix lands, delete the
expected-failure wrapper; the assertion must then pass unchanged.

The symptom: a chained session bound to a queue entry, abandoned while it is
still only registered (no worktree, no plan, no files), is counted by the chain
as a failed run. The entry is marked failed, a triage is queued at the head and
a retry behind it, and the entry's one retry is spent. Expected: a work-free
yield is not a failure.

The test drives the real pieces end to end: the chain step over the real
``chain.session_end`` reading a real session record, and hands whose queue
writes are the real ``QueueDomain`` (the same calls the CLI's
``_real_chain_hands`` makes). Only the window launch is a recorder.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.manifest import Manifest
from invariant.core.queue import Queue
from invariant.core.store import JsonStore
from invariant.domains.queue import QueueDomain
from invariant.oracle import chain
from invariant.oracle.chain import ChainSettings
from invariant.oracle.chainstep import ChainHands, ChainStep
from invariant.oracle.notify import RecordingNotifier

NOON = time.mktime((2026, 11, 23, 12, 0, 0, 0, 0, -1))
KEY = "/state/demo"


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    assert main(["init", "--home", "repo", "--name", "demo"]) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "manifest")
    return root


class _Inputs:
    def __init__(self, queue, settings=None):
        self.queue = queue
        self.ready = []
        self.settings = settings or ChainSettings()


@pytest.mark.xfail(strict=True, reason="BUG_162 is live — delete this line with the fix")
def test_BUG_162_work_free_abandon_of_a_chained_session_is_not_a_failure(project, tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    def queue() -> QueueDomain:
        manifest = Manifest.locate(project)
        return QueueDomain(manifest, JsonStore(manifest.state_root))

    entry = queue().add("increment eight").id

    def start(key, label, prompt):
        return {"name": f"demo-{label}", "pid": 4242}

    hands = ChainHands(
        start=start,
        begin=lambda key, entry, ref: queue().begin(entry, ref),
        mark=lambda key, entry, state, session=None, reason=None:
            queue().mark(entry, state, session, reason),
        fail=lambda key, entry, reason, template, retry:
            queue().fail(entry, reason, template, retry),
    )
    holder = {"inputs": _Inputs(queue().list())}
    step = ChainStep(home, hands, RecordingNotifier(), reader=lambda root: holder["inputs"],
                     session_end=chain.session_end, pid_alive=lambda pid: True,
                     log=lambda message: None)

    def doc(rows=()):
        return {"projects": [{"project": {"key": KEY, "name": "demo", "root": str(project)},
                              "rows": list(rows)}]}

    # Tick 1: the chain starts the queued entry.
    started = step.step(doc(), NOON)
    assert [a["action"] for a in started] == ["started"], started
    holder["inputs"] = _Inputs(Queue())  # nothing further to start

    # The window registers a session; it never isolates, plans or touches a file.
    assert main(["session", "start", "work"]) == 0
    sid = cli_main.Context().sessions.list()[0].id
    session = cli_main.Context().sessions.get(sid)
    assert session.state == "registered"
    assert not session.worktree and not session.plan and not session.files

    # Tick 2: the chain binds the window's session to the entry.
    row = {"kind": "session", "session_id": sid, "name": "work", "state": "registered",
           "hosts": [{"host_session_id": "h1", "launched_name": "demo-increment eight",
                      "pid": None, "alive": True}]}
    bound = step.step(doc([row]), NOON + 30)
    assert [a["action"] for a in bound] == ["bound"], bound

    # The user sets the work-free session aside: registered -> abandoned.
    assert main(["session", "transition", "--id", sid, "abandoned"]) == 0
    assert chain.session_end(str(project), sid) == chain.END_ABANDONED

    # Tick 3: the chain follows the ended session.
    actions = step.step(doc(), NOON + 60)

    # SYMPTOM: the work-free yield must not be counted as a failure — no
    # failed mark, no triage queued at the head, no retry spent.
    entries = queue().list().entries
    observed = {
        "actions": [(a["action"], a["detail"]) for a in actions],
        "entries": [(e.id, e.ref, e.state, e.triage_of, e.retry_of) for e in entries],
    }
    original = next(e for e in entries if e.id == entry)
    assert original.state != "failed", observed
    assert not any(e.triage_of == entry for e in entries), observed
    assert not any(e.retry_of == entry for e in entries), observed
    assert not any(a["action"] == "failed" for a in actions), observed
