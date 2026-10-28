"""The phone's "Re-run tests…" for a session whose values it resolved itself.

Repro for BUG_093 — see docs/backlog/BUG_093.md. A project discovers
``destination_id`` from ``INVARIANT_DESTINATION_ID`` and refuses to guess when
it is unset (two or more simulators booted). Each session exports its own in
its own shell. The session's own ``pipeline test`` resolves it; the oracle's
re-run of that session's failing tests, confirmed from the panel, must reach
the same tests against the same destination, not fail discovery with exit 2.

Everything is in-process: a real project in a git repository, a real session
and pipeline record, the real scan, ``assess`` and ``step_for``, and
``Actions.perform`` with a runner that hands the step's argv to the CLI's
``main`` in the step's folder and environment. The "test suite" is a script
that records the destination it was given and fails. No simulator, no real
suite, no ``invariant`` subprocess.
"""

from __future__ import annotations

import os
import shlex
import sys
import time
from pathlib import Path

import pytest
import yaml
from test_oracle_scan import add_worktree, git, put_pipeline, put_session

from invariant.cli.main import main
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.oracle.actions import Actions, step_for
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import assess
from invariant.oracle.scan import ROW_SESSION, scan_project

SESSION_VALUE = "SIM-OWN-A"
SELECTOR = "tests/test_widget.py::test_one"
DISCOVER = (
    'test -n "$INVARIANT_DESTINATION_ID" || exit 1; '
    'printf %s "$INVARIANT_DESTINATION_ID"'
)


def _project(base: Path, seen: Path) -> Manifest:
    """A project whose tests record the destination they were handed, and fail."""
    suite = base / "fake_suite.py"
    suite.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(seen)!r}).write_text(sys.argv[1])\n"
        "sys.exit(1)\n",
        "utf-8",
    )
    runner = f"{sys.executable} {suite} {{destination_id}}"
    root = base / "heapish"
    root.mkdir()
    git(root, "init", "-q", "-b", "master")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "version": 1,
        "project": {"name": "heapish"},
        "commands": {"test": runner, "test_one": runner + " {selector}"},
        "discover": {"destination_id": DISCOVER},
    }), "utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    manifest = Manifest.load(root.resolve())
    ProjectIndex(oracle_home()).register(manifest, via="manual")
    return manifest


def _in_process(monkeypatch):
    """A runner that runs an ``invariant`` argv through ``main``, here."""

    def run(argv, cwd, env):
        with monkeypatch.context() as m:
            for name in list(os.environ):
                if name not in env:
                    m.delenv(name)
            for name, value in env.items():
                m.setenv(name, value)
            m.chdir(cwd)
            return main(list(argv[1:])), ""

    return run


@pytest.mark.xfail(
    strict=True, reason="BUG_093 is live — delete this marker with the fix"
)
def test_BUG_093_phone_rerun_reaches_the_sessions_own_destination(tmp_path, monkeypatch):
    # Repro for BUG_093 — see the work-item entry.
    # Fails while the defect is live. When the fix lands, delete the
    # expected-failure wrapper; the assertion must then pass unchanged.
    base = tmp_path / "w"
    base.mkdir()
    seen = tmp_path / "destination-seen"
    project = _project(base, seen)
    worktree = add_worktree(project, "own")
    put_session(project, id="s-own", name="own-s-own", state="isolated",
                worktree=str(worktree), branch="session/own", origin=str(project.root),
                host_session_id="h-own", host_ended=True, heartbeat=time.time() - 600)
    put_pipeline(project, "s-own", methodology="tdd", state="green")

    # The session's own shell: its simulator exported, a scoped run, failing.
    own_shell = dict(os.environ, INVARIANT_SESSION_ID="s-own",
                     INVARIANT_DESTINATION_ID=SESSION_VALUE)
    _in_process(monkeypatch)(
        ("invariant", "pipeline", "test", "--selector", SELECTOR), str(worktree), own_shell
    )
    assert seen.read_text() == SESSION_VALUE  # the session's own run resolved it
    seen.unlink()

    # The phone: a fresh scan, the Problems card's step, confirmed and run by
    # the oracle, whose own environment has no destination exported.
    document = assess([scan_project(ProjectIndex(oracle_home()).match("heapish"))],
                      time.time())
    [row] = [r for p in document["projects"] for r in p["rows"]
             if r["kind"] == ROW_SESSION and r["session_id"] == "s-own"]
    step = step_for(row)
    assert step is not None and step.argv[:3] == ("invariant", "pipeline", "test")
    oracle_env = {k: v for k, v in os.environ.items() if k != "INVARIANT_DESTINATION_ID"}
    actions = Actions(_in_process(monkeypatch), lambda: oracle_env, lambda env: env)

    outcome = actions.perform(step)

    reached = seen.read_text() if seen.exists() else None
    assert reached == SESSION_VALUE, (
        f"the phone's re-run of {shlex.join(step.argv)} did not reach the "
        f"session's tests on its own destination {SESSION_VALUE!r} "
        f"(exit {outcome.status}; reached {reached!r})"
    )
