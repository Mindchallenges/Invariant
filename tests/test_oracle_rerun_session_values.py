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

import json
import os
import shlex
import sys
import time
from pathlib import Path

import pytest
import yaml
from test_oracle_scan import add_worktree, git, put_pipeline, put_session

from invariant.cli.main import main
from invariant.core.errors import StateError
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.privacy import PrivacyScanner
from invariant.oracle.actions import Actions, confirm_lines, step_for
from invariant.oracle.devices import Device
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.panel import view
from invariant.oracle.recommend import assess
from invariant.oracle.scan import (
    ROW_SESSION,
    discovery_env,
    mask_value,
    privacy_scanner,
    replay_values,
    row_key,
    scan_project,
    shown_env,
)
from invariant.oracle.webactions import AuditLog, RemoteActions

SESSION_VALUE = "SIM-OWN-A"
SELECTOR = "tests/test_widget.py::test_one"
DISCOVER = (
    'test -n "$INVARIANT_DESTINATION_ID" || exit 1; '
    'printf %s "$INVARIANT_DESTINATION_ID"'
)


def _project(base: Path, seen: Path, privacy: dict | None = None) -> Manifest:
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
        **({"privacy": privacy} if privacy else {}),
    }), "utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    manifest = Manifest.locate(root.resolve())
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


def _own_failing_run(tmp_path, monkeypatch, selectors=(SELECTOR,), value=SESSION_VALUE,
                     privacy=None):
    """The repro's project and session, after the session's own shell ran its tests."""
    base = tmp_path / "w"
    base.mkdir()
    seen = tmp_path / "destination-seen"
    project = _project(base, seen, privacy)
    worktree = add_worktree(project, "own")
    put_session(project, id="s-own", name="own-s-own", state="isolated",
                worktree=str(worktree), branch="session/own", origin=str(project.root),
                host_session_id="h-own", host_ended=True, heartbeat=time.time() - 600)
    put_pipeline(project, "s-own", methodology="tdd", state="green")
    _own_run(monkeypatch, worktree, value, selectors)
    seen.unlink()
    return project, worktree, seen


def _own_run(monkeypatch, worktree, value=SESSION_VALUE, selectors=(SELECTOR,)):
    """One more run from the session's own shell, its own value exported."""
    own_shell = dict(os.environ, INVARIANT_SESSION_ID="s-own",
                     INVARIANT_DESTINATION_ID=value)
    argv = ("invariant", "pipeline", "test")
    for selector in selectors:
        argv += ("--selector", selector)
    _in_process(monkeypatch)(argv, str(worktree), own_shell)


def _phone_row(name: str = "heapish") -> dict:
    document = assess([scan_project(ProjectIndex(oracle_home()).match(name))], time.time())
    [row] = [r for p in document["projects"] for r in p["rows"]
             if r["kind"] == ROW_SESSION and r["session_id"] == "s-own"]
    return row


def _phone_runs(project: Manifest) -> list[dict]:
    record = yaml.safe_load((project.state_root / "pipelines" / "s-own.json").read_text())
    return record["current"]["evidence"]["test_runs"]


def test_BUG_093_phone_rerun_replays_the_last_scoped_selector(tmp_path, monkeypatch):
    project, _, _ = _own_failing_run(tmp_path, monkeypatch)

    step = step_for(_phone_row())
    assert step is not None
    assert step.argv == ("invariant", "pipeline", "test", "--selector", SELECTOR)
    oracle_env = {k: v for k, v in os.environ.items() if k != "INVARIANT_DESTINATION_ID"}
    Actions(_in_process(monkeypatch), lambda: oracle_env, lambda env: env).perform(step)

    phone_run = _phone_runs(project)[-1]
    assert phone_run["selectors"] == [SELECTOR]
    assert phone_run["scope"] == "scoped"


# -- the user's ruling: use it, but mask it ------------------------------------------

#: The project's own privacy patterns flag the session's value.
FLAGGED = {"extra": [r"SIM-[A-Z]+-[A-Z]"]}
MASK = "SI" + "█" * 7
PHONE = Device(id="d1phone", name="phone", paired="2026-10-22")


def _oracle_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "INVARIANT_DESTINATION_ID"}


def _fresh() -> dict:
    """A fresh read, as the page's server takes one: the JSON the snapshot is."""
    document = assess([scan_project(ProjectIndex(oracle_home()).match("heapish"))],
                      time.time())
    return json.loads(json.dumps(document))


def _remote(monkeypatch, tmp_path) -> RemoteActions:
    actions = Actions(_in_process(monkeypatch), _oracle_env, lambda env: env)
    return RemoteActions(actions, _fresh, AuditLog(tmp_path / "home"),
                         spawn=lambda work: work())


def test_a_flagged_value_is_masked_in_the_snapshot_step_and_copy_line(tmp_path, monkeypatch):
    _own_failing_run(tmp_path, monkeypatch, privacy=FLAGGED)

    document = _fresh()
    assert SESSION_VALUE not in json.dumps(document)
    row = _phone_row()
    step = step_for(row)
    assert step.env["INVARIANT_DESTINATION_ID"] == MASK
    assert step.masked == ("INVARIANT_DESTINATION_ID",)
    assert row["pipeline"]["last_test"]["discovered"] == {"destination_id": MASK}
    assert MASK in row["pipeline"]["last_test"]["command"]

    detail = view(document, acting=True)["details"][row_key(row)]
    assert detail["step"]["copy"].startswith(
        "# Not runnable as shown, INVARIANT_DESTINATION_ID masked: ")
    assert f"INVARIANT_DESTINATION_ID='{MASK}'" in detail["step"]["copy"]
    assert all(SESSION_VALUE not in line for line in confirm_lines(step, None))


def test_the_confirm_sheet_masks_and_the_child_gets_the_real_value(tmp_path, monkeypatch):
    _, _, seen = _own_failing_run(tmp_path, monkeypatch, privacy=FLAGGED)
    remote = _remote(monkeypatch, tmp_path)
    key = row_key(_phone_row())

    answer = remote.confirm(PHONE, key).body
    words = answer["words"]
    assert SESSION_VALUE not in json.dumps(answer)
    assert f"INVARIANT_DESTINATION_ID={MASK}  (masked; the session's recorded value is " \
           "used when it runs)" in words["env"]
    assert words["command"].startswith("# Not runnable as shown")

    job = remote.act(PHONE, key, answer["nonce"]).body["job"]

    assert seen.read_text() == SESSION_VALUE
    assert job["state"] == "done"
    log = (tmp_path / "home" / "web" / "actions.log").read_text()
    assert SESSION_VALUE not in log and "INVARIANT_DESTINATION_ID" not in log


def test_another_run_that_masks_alike_is_not_what_was_confirmed(tmp_path, monkeypatch):
    _, worktree, seen = _own_failing_run(tmp_path, monkeypatch, privacy=FLAGGED)
    remote = _remote(monkeypatch, tmp_path)
    key = row_key(_phone_row())
    nonce = remote.confirm(PHONE, key).body["nonce"]

    _own_run(monkeypatch, worktree, value="SIM-OWN-B")  # same mask, same selector
    seen.unlink()
    answer = remote.act(PHONE, key, nonce)

    assert answer.status == 409
    assert "changed since it was confirmed" in answer.body["error"]
    assert not seen.exists()


def test_a_record_that_moved_under_a_confirmed_step_runs_nothing(tmp_path, monkeypatch):
    project, _, seen = _own_failing_run(tmp_path, monkeypatch, privacy=FLAGGED)
    step = step_for(_phone_row())
    record = project.state_root / "pipelines" / "s-own.json"
    body = json.loads(record.read_text())
    body["current"]["evidence"]["test_runs"][-1]["discovered"]["destination_id"] = "XY-OTHER"
    record.write_text(json.dumps(body))

    outcome = Actions(_in_process(monkeypatch), _oracle_env, lambda env: env).perform(step)

    assert outcome.ok is False and outcome.status is None
    assert "no longer matches what was confirmed; open the step again; nothing ran" \
        in outcome.text
    assert not seen.exists()


def test_a_run_from_before_the_record_offers_no_rerun_and_says_why(tmp_path, monkeypatch):
    project, _, _ = _own_failing_run(tmp_path, monkeypatch)
    record = project.state_root / "pipelines" / "s-own.json"
    body = json.loads(record.read_text())
    del body["current"]["evidence"]["test_runs"][-1]["discovered"]
    record.write_text(json.dumps(body))

    document = _fresh()
    row = _phone_row()
    assert step_for(row).kind == "info"
    cards = [c for g in view(document, acting=True)["groups"] for c in g["cards"]
             if c["row"] == row_key(row)]
    assert cards and cards[0]["button"] is None
    going_on = view(document, acting=True)["details"][row_key(row)]["going_on"]
    assert any("Run its tests once from the session's own window." in s for s in going_on)


# -- the pieces --------------------------------------------------------------------


class _Config:
    def __init__(self, privacy):
        self.privacy = privacy

    def get(self, key):
        return self.privacy if key == "privacy" else None


def test_a_scanner_that_cannot_be_built_masks_every_value_whole():
    scanner = privacy_scanner(_Config({"patterns": ["no_such_pattern"]}))
    assert mask_value(scanner, "plain") == "pl███"


def test_mask_value_redacts_each_flagged_span_and_keeps_the_rest():
    scanner = PrivacyScanner(patterns=(), extra=(r"SIM-[A-Z]+", r"SIM-[A-Z]+-[A-Z]", r"Q\d"))
    assert mask_value(scanner, "nothing here") is None
    assert mask_value(scanner, "on SIM-OWN-A and Q7\nthen SIM-TWO") == (
        "on SI███████ and ██\nthen SI█████")


def test_only_a_variable_name_other_than_the_session_pin_is_exported():
    assert discovery_env("destination_id") == "INVARIANT_DESTINATION_ID"
    assert discovery_env("not a name") is None
    assert discovery_env("session_id") is None
    assert shown_env({"device": 7, "bad key": "x", "scheme": "App"},
                     PrivacyScanner(patterns=())) == ({"INVARIANT_SCHEME": "App"}, [])


def _reference(project, **override):
    record = json.loads((project.state_root / "pipelines" / "s-own.json").read_text())
    reference = {"project": str(project.state_root), "root": str(project.root),
                 "session": "s-own", "started": record["current"]["started"],
                 "run": len(record["current"]["evidence"]["test_runs"]) - 1}
    return {**reference, **override}


def test_replay_values_reads_the_one_confirmed_run(tmp_path, monkeypatch):
    project, _, _ = _own_failing_run(tmp_path, monkeypatch, privacy=FLAGGED)
    names, shown = ("INVARIANT_DESTINATION_ID",), {"INVARIANT_DESTINATION_ID": MASK}

    assert replay_values(_reference(project), names, shown) == {
        "INVARIANT_DESTINATION_ID": SESSION_VALUE}
    for reference, message in [
        ({"run": 3}, "values are not on record"),
        ({"started": -1.0}, "not the one whose run was confirmed"),
        ({"session": "s-gone"}, "not the one whose run was confirmed"),
        ({"root": str(tmp_path)}, "no manifest at"),
    ]:
        with pytest.raises(StateError, match=message):
            replay_values(_reference(project, **reference), names, shown)
    with pytest.raises(StateError, match="names no recorded run"):
        replay_values({}, names, shown)
    with pytest.raises(StateError, match="no longer matches"):
        replay_values(_reference(project), ("INVARIANT_OTHER",), shown)
