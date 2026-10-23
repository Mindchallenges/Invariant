"""Confirm-first actions from the oracle's web page (IDEA_023 increment 2, ADR-008).

The page acts through ``oracle/actions`` — the TUI's own ``step_for``,
``refusal``, ``Actions.describe`` and ``Actions.perform`` — and adds only a
transport (``oracle/webactions``):

* a confirm view that shows exactly what ``confirm_lines`` shows, with a
  single-use nonce bound to the device and the row, short-lived;
* an action ``POST`` that carries only the row key and that nonce, needs a
  paired token and a same-origin ``Origin``, and re-derives the step from a
  fresh assessment, so nothing the client sends reaches the runner;
* one action at a time per row, its result polled, and an audit line per
  action under the oracle home, owner-only from creation.

Plus the listener hardening from the increment 1 review: a socket timeout, a
header cap, a connection cap, and owner-only files created that way.

**Nothing here runs a real command or opens a Terminal.** ``RemoteActions`` is
built on a ``RecordingRunner`` and a recording terminal; the CLI tests go
through the suite-wide ``_oracle_runner`` and ``_handoff_launcher`` seams. The
one server binds loopback on an ephemeral port.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import threading
import time
import urllib.parse
from pathlib import Path

import pytest
import test_oracle_scan
from test_oracle_web import ENGINE, TS4, Clock, _loaded, _script

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.store import JsonStore
from invariant.oracle import home as oracle_home_module
from invariant.oracle import web, webactions
from invariant.oracle.actions import Actions, RecordingRunner, child_env, step_for
from invariant.oracle.devices import COOKIE_NAME, DEVICES_FILE, Device, DeviceStore
from invariant.oracle.home import oracle_home
from invariant.oracle.scan import ScanCache, row_key
from invariant.oracle.web import LOOPBACK, Request, WebApp, WebServer
from invariant.oracle.webactions import (
    AUDIT_FILE,
    CONFIRM_TTL,
    MAX_OUTPUT,
    AuditLog,
    Confirms,
    RemoteActions,
    find_row,
)

machine_world = test_oracle_scan.machine_world

PHONE = Device(id="d1phone", name="phone", paired="2026-10-22T10:00:00+00:00")
TABLET = Device(id="d2tblet", name="tablet", paired="2026-10-22T10:00:00+00:00")
MERGE = ("invariant", "session", "merge", "--id", "s1")
ORIGIN = f"http://{TS4}:7341"


# -- a hand-built assessed document ------------------------------------------


def _row(session_id="s1", kind="run", argv=MERGE, cwd=None, env=None, waiting=False,
         project="K"):
    signals = []
    if waiting:
        signals.append({"kind": "waiting", "severity": "high", "alert": True,
                        "detail": "permission: Allow Bash?", "key": "k|s|waiting|x"})
    return {
        "kind": "session", "project": project, "project_name": "p", "root": "/r",
        "session_id": session_id, "name": f"name-{session_id}", "state": "reviewed",
        "stage": "reviewed", "hosts": [], "path": None, "pipeline": None,
        "signals": signals,
        "recommendation": {"label": "merge it", "kind": kind, "argv": list(argv),
                           "cwd": cwd, "env": dict(env or {}), "detail": ""},
    }


def _doc(*rows, key="K"):
    return {"projects": [{"project": {"key": key, "name": "p", "root": "/r"},
                          "elapsed": 0.0, "rows": list(rows)}]}


class World:
    """A mutable fresh assessment, a recording runner and terminal, a clock."""

    def __init__(self, tmp_path: Path) -> None:
        self.cwd = tmp_path / "proj"
        self.cwd.mkdir()
        self.home = tmp_path / "home"
        self.clock = Clock()
        self.runner = RecordingRunner(status=0, output="merged\n")
        self.launched: list = []
        self.environ = {"PATH": "/bin", "INVARIANT_SESSION_ID": "ambient", "CLAUDECODE": "1"}
        self.actions = Actions(
            runner=self.runner,
            environ=lambda: self.environ,
            strip=lambda env: {k: v for k, v in env.items() if k != "CLAUDECODE"},
            terminal=self._terminal,
            preview=lambda step: f"cd {step.cwd} && claude --name x --remote-control x",
        )
        self.document = _doc(_row(cwd=str(self.cwd)))
        self.reads = 0
        self.deferred: list = []
        self.spawn = lambda work: work()
        self.tokens = iter(f"tok{n:04d}" for n in range(10_000))
        self.remote = RemoteActions(
            self.actions, self._fresh, AuditLog(self.home, clock=self.clock),
            clock=self.clock, timer=self.clock, spawn=lambda work: self.spawn(work),
            token=lambda: next(self.tokens),
        )

    def _fresh(self):
        self.reads += 1
        return json.loads(json.dumps(self.document))

    def _terminal(self, step):
        self.launched.append(step)
        return "opened a Terminal window: claude (pid 4242) is running x"

    def defer(self) -> None:
        self.spawn = self.deferred.append

    def key(self, n: int = 0) -> str:
        return row_key(self.document["projects"][0]["rows"][n])

    def audit(self) -> list[dict]:
        path = self.home / AUDIT_FILE
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


# -- the row key ------------------------------------------------------------------


class TestRowKey:
    def test_a_row_is_keyed_by_its_project_and_subject_not_its_position(self):
        a, b = _row("s1"), _row("s2")
        assert row_key(a) == "K|s1"
        assert find_row(_doc(b, a), "K|s1") is a

    def test_hosts_and_worktrees_have_their_own_keys(self):
        host = {"kind": "host", "project": "K", "session_id": None,
                "hosts": [{"host_session_id": "h1"}], "path": None}
        tree = {"kind": "worktree", "project": "K", "session_id": None, "hosts": [],
                "path": "/w/x"}
        error = {"kind": "error", "project": "K", "session_id": None, "hosts": [],
                 "path": None}
        assert row_key(host) == "K|host:h1"
        assert row_key(tree) == "K|worktree:/w/x"
        assert row_key(error) == "K|project"

    def test_the_scan_row_names_the_same_subject(self, machine_world):
        from invariant.oracle.scan import scan_all

        snapshots = scan_all(machine_world["index"],
                             cache=ScanCache(probe=machine_world["probe"]))
        for snapshot in snapshots:
            for row in snapshot.rows:
                assert row_key(row.to_dict()) == f"{row.project}|{row.subject}"

    def test_a_missing_or_ambiguous_row_is_not_found(self):
        assert find_row(_doc(_row("s1")), "K|nope") is None
        assert find_row(_doc(_row("s1"), _row("s1")), "K|s1") is None


# -- the confirm view ------------------------------------------------------------


class TestConfirm:
    def test_it_shows_exactly_what_the_tui_shows_and_runs_nothing(self, world):
        answer = world.remote.confirm(PHONE, world.key())
        row = world.document["projects"][0]["rows"][0]
        assert answer.status == 200
        body = answer.body
        assert body["lines"] == world.actions.describe(step_for(row))
        assert body["lines"][0] == "run   invariant session merge --id s1"
        assert body["lines"][1] == f"in    {world.cwd}"
        assert body["nonce"] == "tok0000"
        assert body["kind"] == "run" and body["refused"] is None
        assert body["expires_in"] == CONFIRM_TTL
        assert world.runner.calls == [] and world.audit() == []

    def test_the_env_it_adds_is_shown(self, world):
        world.document = _doc(_row(cwd=str(world.cwd), env={"INVARIANT_SESSION_ID": "s1"}))
        lines = world.remote.confirm(PHONE, world.key()).body["lines"]
        assert "env   INVARIANT_SESSION_ID=s1" in lines

    def test_an_info_step_has_no_action(self, world):
        world.document = _doc(_row(kind="info", argv=(), cwd=None))
        body = world.remote.confirm(PHONE, world.key()).body
        assert body["kind"] == "info" and body["nonce"] is None
        assert body["lines"] == ["merge it"]

    def test_a_row_with_no_step_has_no_action(self, world):
        row = _row(cwd=str(world.cwd))
        row["recommendation"] = None
        world.document = _doc(row)
        body = world.remote.confirm(PHONE, world.key()).body
        assert body["nonce"] is None and "nothing is recommended" in body["refused"]

    def test_a_gone_row_is_404(self, world):
        answer = world.remote.confirm(PHONE, "K|nope")
        assert answer.status == 404 and answer.body["nonce"] is None

    @pytest.mark.parametrize("case", ["waiting", "program", "cwd", "kind"])
    def test_each_refusal_gets_no_nonce(self, world, case):
        world.document = _doc(_refused(case, world.cwd))
        body = world.remote.confirm(PHONE, world.key()).body
        assert body["nonce"] is None
        assert body["refused"] == REFUSALS[case]

    def test_a_terminal_step_says_where_it_opens_and_how_to_reach_it(self, world):
        world.document = _doc(_row(kind="terminal", cwd=str(world.cwd),
                                   argv=("invariant", "session", "resume", "--id", "s1")))
        body = world.remote.confirm(PHONE, world.key()).body
        assert body["nonce"]
        assert any("claude --name x --remote-control x" in line for line in body["lines"])
        assert any("Remote Control" in line and "name-s1" in line for line in body["lines"])
        assert world.launched == []

    def test_a_failed_read_is_503(self, world):
        world.remote.fresh = _raises
        assert world.remote.confirm(PHONE, world.key()).status == 503

    def test_it_carries_the_sheets_words_from_the_panel(self, world):
        world.document = _doc(_row(cwd=str(world.cwd), env={"INVARIANT_SESSION_ID": "s1"}))
        words = world.remote.confirm(PHONE, world.key()).body["words"]
        assert words["title"] == "Merge this session?"
        assert words["button"] == "Merge"
        assert words["command"] == "INVARIANT_SESSION_ID=s1 invariant session merge --id s1"
        assert words["place"] == "In p"
        assert words["cwd"] == str(world.cwd)
        assert words["env"] == ["INVARIANT_SESSION_ID=s1"]
        assert words["launch"] is None

    def test_a_terminal_steps_words_carry_its_launch_line_previewed_once(self, world):
        previews = []
        world.actions.preview = lambda step: previews.append(step) or "cd x && claude"
        world.document = _doc(_row(kind="terminal", cwd=str(world.cwd),
                                   argv=("invariant", "session", "resume", "--id", "s1")))
        body = world.remote.confirm(PHONE, world.key()).body
        assert body["words"]["launch"] == "cd x && claude"
        assert body["words"]["button"] == "Open window"
        assert "  cd x && claude" in body["lines"]
        assert len(previews) == 1

    def test_a_refused_or_info_row_carries_no_words(self, world):
        world.document = _doc(_refused("waiting", world.cwd))
        assert world.remote.confirm(PHONE, world.key()).body["words"] is None
        world.document = _doc(_row(kind="info", argv=(), cwd=None))
        assert world.remote.confirm(PHONE, world.key()).body["words"] is None


def _raises():
    raise RuntimeError("scan broke")


REFUSALS = {
    "waiting": ("its host is waiting on a prompt; answer it in its own terminal. "
                "The oracle never answers one"),
    "program": "only invariant commands run from here, not 'rm'",
    "cwd": "its directory /no/such/dir does not exist",
    "kind": "unknown kind of step 'shell'; nothing runs",
}


def _refused(case: str, cwd: Path) -> dict:
    if case == "waiting":
        return _row(cwd=str(cwd), waiting=True)
    if case == "program":
        return _row(cwd=str(cwd), argv=("rm", "-rf", "/"))
    if case == "cwd":
        return _row(cwd="/no/such/dir")
    return _row(cwd=str(cwd), kind="shell")


# -- the action ---------------------------------------------------------------------


class TestAct:
    def _confirm(self, world, device=PHONE, n=0):
        return world.remote.confirm(device, world.key(n)).body["nonce"]

    def test_a_confirmed_step_runs_once_with_the_server_derived_command(self, world):
        world.document = _doc(_row(cwd=str(world.cwd), env={"INVARIANT_SESSION_ID": "s1"}))
        nonce = self._confirm(world)
        answer = world.remote.act(PHONE, world.key(), nonce)
        assert answer.status == 202
        expected_env = child_env(world.environ, {"INVARIANT_SESSION_ID": "s1"},
                                 world.actions.strip)
        assert world.runner.calls == [(MERGE, str(world.cwd), expected_env)]
        assert "CLAUDECODE" not in world.runner.calls[0][2]
        job = answer.body["job"]
        assert job["state"] == "done" and job["ok"] is True
        assert job["status"] == 0 and job["output"] == "merged\n"
        assert job["argv"] == list(MERGE) and job["cwd"] == str(world.cwd)
        assert job["elapsed"] == 0.0

    def test_the_nonce_is_single_use(self, world):
        nonce = self._confirm(world)
        assert world.remote.act(PHONE, world.key(), nonce).status == 202
        again = world.remote.act(PHONE, world.key(), nonce)
        assert again.status == 403 and "already used" in again.body["error"]
        assert len(world.runner.calls) == 1

    def test_the_nonce_expires(self, world):
        nonce = self._confirm(world)
        world.clock.now += CONFIRM_TTL + 1
        answer = world.remote.act(PHONE, world.key(), nonce)
        assert answer.status == 403 and "expired" in answer.body["error"]
        assert world.runner.calls == []

    def test_the_nonce_is_bound_to_the_device_and_spent_by_any_use(self, world):
        nonce = self._confirm(world)
        answer = world.remote.act(TABLET, world.key(), nonce)
        assert answer.status == 403 and "another device" in answer.body["error"]
        assert world.remote.act(PHONE, world.key(), nonce).status == 403
        assert world.runner.calls == []

    def test_the_nonce_is_bound_to_the_row(self, world):
        world.document = _doc(_row("s1", cwd=str(world.cwd)), _row("s2", cwd=str(world.cwd)))
        nonce = self._confirm(world, n=0)
        answer = world.remote.act(PHONE, world.key(1), nonce)
        assert answer.status == 403 and "another row" in answer.body["error"]
        assert world.runner.calls == []

    def test_an_unknown_nonce_runs_nothing(self, world):
        assert world.remote.act(PHONE, world.key(), "forged").status == 403
        assert world.remote.act(PHONE, world.key(), "").status == 403
        assert world.runner.calls == []

    @pytest.mark.parametrize("case", ["waiting", "program", "cwd", "kind"])
    def test_each_refusal_holds_at_act_time_too(self, world, case):
        nonce = self._confirm(world)
        world.document = _doc(_refused(case, world.cwd))
        answer = world.remote.act(PHONE, world.key(), nonce)
        assert answer.status == 409 and answer.body["error"] == REFUSALS[case]
        assert world.runner.calls == [] and world.audit() == []

    def test_an_info_step_is_never_acted_on(self, world):
        nonce = self._confirm(world)
        world.document = _doc(_row(kind="info", argv=(), cwd=None))
        answer = world.remote.act(PHONE, world.key(), nonce)
        assert answer.status == 409 and "no action" in answer.body["error"]

    def test_a_row_with_no_step_left_is_not_acted_on(self, world):
        nonce = self._confirm(world)
        world.document["projects"][0]["rows"][0]["recommendation"] = None
        answer = world.remote.act(PHONE, world.key(), nonce)
        assert answer.status == 409 and "nothing is recommended" in answer.body["error"]

    def test_a_step_that_changed_since_the_confirm_does_not_run(self, world):
        nonce = self._confirm(world)
        world.document = _doc(_row(cwd=str(world.cwd),
                                   argv=("invariant", "session", "teardown", "--id", "s1")))
        answer = world.remote.act(PHONE, world.key(), nonce)
        assert answer.status == 409 and "changed" in answer.body["error"]
        assert world.runner.calls == []

    def test_a_changed_label_or_detail_is_not_a_changed_step(self, world):
        nonce = self._confirm(world)
        world.document["projects"][0]["rows"][0]["recommendation"]["detail"] = "next owed: x"
        assert world.remote.act(PHONE, world.key(), nonce).status == 202

    def test_a_row_that_went_away_does_not_run(self, world):
        nonce = self._confirm(world)
        world.document = _doc()
        answer = world.remote.act(PHONE, "K|s1", nonce)
        assert answer.status == 409 and "not there" in answer.body["error"]

    def test_a_failed_read_is_503_and_runs_nothing(self, world):
        nonce = self._confirm(world)
        world.remote.fresh = _raises
        assert world.remote.act(PHONE, world.key(), nonce).status == 503
        assert world.runner.calls == []

    def test_one_action_at_a_time_per_row(self, world):
        world.defer()
        first = world.remote.act(PHONE, world.key(), self._confirm(world))
        assert first.status == 202 and first.body["job"]["state"] == "running"
        second = world.remote.act(PHONE, world.key(), self._confirm(world))
        assert second.status == 409 and "already running" in second.body["error"]
        (work,) = world.deferred
        work()
        assert len(world.runner.calls) == 1
        assert world.remote.act(PHONE, world.key(), self._confirm(world)).status == 202

    def test_a_double_submit_runs_once(self, world):
        nonce = self._confirm(world)
        answers = []
        threads = [threading.Thread(target=lambda: answers.append(
            world.remote.act(PHONE, world.key(), nonce).status)) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(answers) == [202, 403, 403, 403]
        assert len(world.runner.calls) == 1

    def test_the_running_cap_holds_across_rows(self, world):
        world.defer()
        rows = [_row(f"s{n}", cwd=str(world.cwd)) for n in range(webactions.MAX_RUNNING + 1)]
        world.document = _doc(*rows)
        statuses = [world.remote.act(PHONE, world.key(n), self._confirm(world, n=n)).status
                    for n in range(len(rows))]
        assert statuses == [202] * webactions.MAX_RUNNING + [409]

    def test_a_failed_run_is_shown_with_its_status(self, world):
        world.runner.status, world.runner.output = 3, "refused: owed review\n"
        answer = world.remote.act(PHONE, world.key(), self._confirm(world))
        job = answer.body["job"]
        assert job["ok"] is False and job["status"] == 3
        assert job["output"] == "refused: owed review\n"

    def test_a_runner_that_raises_is_a_failed_job_not_a_dead_server(self, world):
        def boom(argv, cwd, env):
            raise RuntimeError("kaboom")

        world.actions.runner = boom
        job = world.remote.act(PHONE, world.key(), self._confirm(world)).body["job"]
        assert job["state"] == "done" and job["ok"] is False
        assert "kaboom" in job["output"]

    def test_long_output_keeps_its_tail(self, world):
        world.runner.output = "x" * (MAX_OUTPUT + 10) + "END"
        job = world.remote.act(PHONE, world.key(), self._confirm(world)).body["job"]
        assert job["output"].endswith("END")
        assert len(job["output"]) <= MAX_OUTPUT + 200
        assert "truncated" in job["output"]

    def test_a_job_is_polled_only_by_its_device(self, world):
        world.defer()
        job = world.remote.act(PHONE, world.key(), self._confirm(world)).body["job"]
        assert world.remote.job(PHONE, job["id"])["state"] == "running"
        assert world.remote.job(TABLET, job["id"]) is None
        assert world.remote.job(PHONE, "nope") is None
        world.clock.now += 2.5
        world.deferred[0]()
        polled = world.remote.job(PHONE, job["id"])
        assert polled["state"] == "done" and polled["elapsed"] == 2.5
        assert [j["id"] for j in world.remote.recent(PHONE)] == [job["id"]]
        assert world.remote.recent(TABLET) == []

    def test_old_finished_jobs_are_forgotten(self, world):
        for _ in range(webactions.MAX_KEPT + 3):
            world.remote.act(PHONE, world.key(), self._confirm(world))
        assert len(world.remote.recent(PHONE)) == webactions.MAX_KEPT

    def test_a_terminal_step_launches_once_and_reports_the_watch(self, world):
        world.document = _doc(_row(kind="terminal", cwd=str(world.cwd),
                                   argv=("invariant", "session", "resume", "--id", "s1")))
        answer = world.remote.act(PHONE, world.key(), self._confirm(world))
        job = answer.body["job"]
        assert len(world.launched) == 1 and world.runner.calls == []
        assert job["ok"] is True and job["status"] is None
        assert "pid 4242" in job["output"]
        assert job["remote_control"] == "name-s1"
        assert world.audit()[-1]["exit"] is None


def test_the_real_spawn_runs_the_work_in_a_thread():
    done = threading.Event()
    webactions.start_thread(done.set)
    assert done.wait(5)


class TestConfirms:
    def test_the_book_is_capped_oldest_first(self):
        clock = Clock()
        tokens = iter(range(1000))
        book = Confirms(clock, token=lambda: f"n{next(tokens)}", cap=3)
        step = step_for(_row(cwd="/tmp"))
        for _ in range(4):
            book.issue(PHONE.id, "K|s1", step)
        assert book.spend("n0", PHONE.id, "K|s1")[1] is not None
        assert book.spend("n3", PHONE.id, "K|s1")[1] is None

    def test_expired_entries_are_dropped_when_another_is_issued(self):
        clock = Clock()
        tokens = iter(range(1000))
        book = Confirms(clock, token=lambda: f"n{next(tokens)}")
        step = step_for(_row(cwd="/tmp"))
        book.issue(PHONE.id, "K|s1", step)
        clock.now += CONFIRM_TTL + 1
        book.issue(PHONE.id, "K|s1", step)
        assert len(book) == 1


# -- the audit line -----------------------------------------------------------------


class TestAudit:
    def test_each_action_writes_a_start_and_a_done_line_without_secrets(self, world):
        world.document = _doc(_row(cwd=str(world.cwd), env={"INVARIANT_SESSION_ID": "s1"}))
        nonce = world.remote.confirm(PHONE, world.key()).body["nonce"]
        job = world.remote.act(PHONE, world.key(), nonce).body["job"]
        start, done = world.audit()
        assert start["event"] == "start" and done["event"] == "done"
        for line in (start, done):
            assert line["device"] == PHONE.id and line["device_name"] == "phone"
            assert line["row"] == "K|s1" and line["argv"] == list(MERGE)
            assert line["job"] == job["id"] and line["kind"] == "run"
            assert line["at"].endswith("+00:00")
        assert done["exit"] == 0 and done["ok"] is True and done["elapsed"] == 0.0
        text = (world.home / AUDIT_FILE).read_text()
        assert nonce not in text and "env" not in text and "merged" not in text

    def test_the_log_is_owner_only_from_creation(self, world, monkeypatch):
        _no_chmod(monkeypatch)
        old = os.umask(0)
        try:
            world.remote.act(PHONE, world.key(), world.remote.confirm(PHONE, world.key())
                             .body["nonce"])
        finally:
            os.umask(old)
        assert stat.S_IMODE((world.home / AUDIT_FILE).stat().st_mode) == 0o600
        assert stat.S_IMODE((world.home / "web").stat().st_mode) == 0o700

    def test_an_action_that_cannot_be_recorded_does_not_run(self, world):
        (world.home / AUDIT_FILE).mkdir(parents=True)
        nonce = world.remote.confirm(PHONE, world.key()).body["nonce"]
        answer = world.remote.act(PHONE, world.key(), nonce)
        assert answer.status == 503 and "nothing ran" in answer.body["error"]
        assert world.runner.calls == []
        # The row is free again: the refused start held nothing.
        (world.home / AUDIT_FILE).rmdir()
        nonce = world.remote.confirm(PHONE, world.key()).body["nonce"]
        assert world.remote.act(PHONE, world.key(), nonce).status == 202

    def test_a_done_line_that_cannot_be_written_is_said_on_the_job(self, world, monkeypatch):
        world.defer()
        job = world.remote.act(PHONE, world.key(), world.remote.confirm(PHONE, world.key())
                               .body["nonce"]).body["job"]
        monkeypatch.setattr(world.remote.audit, "record", _raises_state)
        world.deferred[0]()
        assert "audit" in world.remote.job(PHONE, job["id"])["audit_error"]


def _raises_state(entry):
    from invariant.core.errors import StateError

    raise StateError("disk full")


def _no_chmod(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a private file is created private, not chmodded after")

    monkeypatch.setattr(os, "chmod", refuse)


# -- owner-only files from creation (hardening b) -------------------------------------


class TestOwnerOnlyFromCreation:
    def test_devices_json_is_0600_from_its_temporary_file_on(self, tmp_path, monkeypatch):
        seen = {}
        real_replace = os.replace

        def replace(src, dst):
            seen[str(dst)] = stat.S_IMODE(os.stat(src).st_mode)
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", replace)
        _no_chmod(monkeypatch)
        store = DeviceStore(tmp_path / "home", clock=Clock())
        old = os.umask(0)
        try:
            store.redeem(store.issue("phone").code)
        finally:
            os.umask(old)
        path = tmp_path / "home" / DEVICES_FILE
        assert seen[str(path)] == 0o600
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    def test_a_stale_temporary_file_is_not_reused_with_its_mode(self, tmp_path):
        store = JsonStore(tmp_path, mode=0o600)
        stale = tmp_path / f".x.json.{os.getpid()}.tmp"
        stale.write_text("old")
        stale.chmod(0o644)
        store.write("x.json", {"a": 1})
        assert stat.S_IMODE((tmp_path / "x.json").stat().st_mode) == 0o600
        assert json.loads((tmp_path / "x.json").read_text()) == {"a": 1}

    def test_a_store_without_a_mode_writes_as_before(self, tmp_path):
        old = os.umask(0o022)
        try:
            JsonStore(tmp_path).write("y.json", {})
        finally:
            os.umask(old)
        assert stat.S_IMODE((tmp_path / "y.json").stat().st_mode) == 0o644

    def test_a_private_dir_made_wider_by_an_older_build_is_tightened(self, tmp_path):
        path = tmp_path / "web"
        path.mkdir(mode=0o755)
        path.chmod(0o755)
        oracle_home_module.private_dir(path)
        assert stat.S_IMODE(path.stat().st_mode) == 0o700

    def test_a_private_dir_that_cannot_be_made_is_a_state_error(self, tmp_path):
        from invariant.core.errors import StateError

        (tmp_path / "file").write_text("")
        with pytest.raises(StateError):
            oracle_home_module.private_dir(tmp_path / "file" / "web")

    def test_the_audit_log_refuses_a_symlink(self, tmp_path):
        from invariant.core.errors import StateError

        (tmp_path / "web").mkdir(mode=0o700)
        (tmp_path / "elsewhere").write_text("")
        (tmp_path / AUDIT_FILE).symlink_to(tmp_path / "elsewhere")
        with pytest.raises(StateError):
            AuditLog(tmp_path).record({"event": "start"})
        assert (tmp_path / "elsewhere").read_text() == ""


# -- over HTTP, without a socket ---------------------------------------------------------


def _app(world, store, remote=True):
    return WebApp(store, lambda: _loaded(world.document), [TS4], clock=Clock(),
                  nonce=lambda: "N0NCE", remote=world.remote if remote else None)


def _paired(store):
    return store.redeem(store.issue("phone", ttl=300).code)


def _get(app, path, token=None):
    headers = {"host": f"{TS4}:7341"}
    if token:
        headers["cookie"] = f"{COOKIE_NAME}={token}"
    return app.handle(Request("GET", path, headers, b""))


def _act(app, token, body, origin=ORIGIN, content_type="application/json", extra=None):
    headers = {"host": f"{TS4}:7341", "content-type": content_type}
    if token:
        headers["cookie"] = f"{COOKIE_NAME}={token}"
    if origin is not None:
        headers["origin"] = origin
    headers.update(extra or {})
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return app.handle(Request("POST", "/api/act", headers, raw))


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / "devices-home", clock=Clock())


class TestOverHttp:
    def _nonce(self, app, token, key):
        response = _get(app, "/api/confirm?row=" + urllib.parse.quote(key), token)
        assert response.status == 200
        return json.loads(response.body)["nonce"]

    def test_the_confirm_needs_a_token_and_a_row(self, world, store):
        _, token = _paired(store)
        app = _app(world, store)
        assert _get(app, "/api/confirm?row=K%7Cs1").status == 401
        assert _get(app, "/api/confirm", token).status == 400
        body = json.loads(_get(app, "/api/confirm?row=K%7Cs1", token).body)
        assert body["lines"][0].startswith("run   invariant session merge")
        assert world.runner.calls == []

    def test_the_action_runs_with_the_paired_token_and_same_origin(self, world, store):
        device, token = _paired(store)
        app = _app(world, store)
        nonce = self._nonce(app, token, "K|s1")
        response = _act(app, token, {"row": "K|s1", "nonce": nonce})
        assert response.status == 202
        job = json.loads(response.body)["job"]
        assert world.runner.calls[0][0] == MERGE
        assert world.audit()[0]["device"] == device.id
        polled = _get(app, f"/api/job?id={job['id']}", token)
        assert polled.status == 200 and json.loads(polled.body)["status"] == 0

    def test_client_supplied_argv_cwd_and_env_are_ignored(self, world, store):
        _, token = _paired(store)
        app = _app(world, store)
        nonce = self._nonce(app, token, "K|s1")
        _act(app, token, {"row": "K|s1", "nonce": nonce, "argv": ["rm", "-rf", "/"],
                          "cwd": "/", "env": {"EVIL": "1"}, "kind": "terminal"})
        ((argv, cwd, env),) = world.runner.calls
        assert argv == MERGE and cwd == str(world.cwd) and "EVIL" not in env

    def test_a_missing_origin_is_refused(self, world, store):
        _, token = _paired(store)
        app = _app(world, store)
        nonce = self._nonce(app, token, "K|s1")
        response = _act(app, token, {"row": "K|s1", "nonce": nonce}, origin=None)
        assert response.status == 403
        assert world.runner.calls == []

    @pytest.mark.parametrize("origin", ["http://evil.example", "null",
                                        "http://100.101.102.103:9999",
                                        "https://100.101.102.103:7341"])
    def test_a_foreign_origin_is_refused(self, world, store, origin):
        _, token = _paired(store)
        app = _app(world, store)
        nonce = self._nonce(app, token, "K|s1")
        assert _act(app, token, {"row": "K|s1", "nonce": nonce}, origin=origin).status == 403
        assert world.runner.calls == []

    def test_a_cross_site_fetch_is_refused_even_with_a_matching_origin(self, world, store):
        _, token = _paired(store)
        app = _app(world, store)
        nonce = self._nonce(app, token, "K|s1")
        response = _act(app, token, {"row": "K|s1", "nonce": nonce},
                        extra={"sec-fetch-site": "cross-site"})
        assert response.status == 403 and world.runner.calls == []

    def test_an_unpaired_action_is_refused_before_anything(self, world, store):
        _, token = _paired(store)
        app = _app(world, store)
        nonce = self._nonce(app, token, "K|s1")
        reads = world.reads
        response = _act(app, None, {"row": "K|s1", "nonce": nonce})
        assert response.status == 401 and b"not paired" in response.body
        assert _act(app, "wrong", {"row": "K|s1", "nonce": nonce}).status == 401
        assert world.reads == reads and world.runner.calls == []

    def test_a_form_body_works_too(self, world, store):
        _, token = _paired(store)
        app = _app(world, store)
        nonce = self._nonce(app, token, "K|s1")
        body = urllib.parse.urlencode({"row": "K|s1", "nonce": nonce}).encode()
        response = _act(app, token, body, content_type="application/x-www-form-urlencoded")
        assert response.status == 202

    @pytest.mark.parametrize("body,content_type,status", [
        (b"{}", "text/plain", 415),
        (b"not json", "application/json", 400),
        (b"[1, 2]", "application/json", 400),
        (b'{"row": "K|s1"}', "application/json", 400),
        (b'{"row": 1, "nonce": 2}', "application/json", 400),
        (b"x" * (web.MAX_BODY + 1), "application/json", 413),
    ])
    def test_a_malformed_action_is_refused(self, world, store, body, content_type, status):
        _, token = _paired(store)
        app = _app(world, store)
        assert _act(app, token, body, content_type=content_type).status == status
        assert world.runner.calls == []

    def test_a_double_submit_over_http_runs_once(self, world, store):
        _, token = _paired(store)
        app = _app(world, store)
        nonce = self._nonce(app, token, "K|s1")
        first = _act(app, token, {"row": "K|s1", "nonce": nonce})
        second = _act(app, token, {"row": "K|s1", "nonce": nonce})
        assert (first.status, second.status) == (202, 403)
        assert len(world.runner.calls) == 1

    def test_a_job_is_404_for_another_device(self, world, store):
        _, token = _paired(store)
        _, other = _paired(store)
        app = _app(world, store)
        job = json.loads(_act(app, token, {"row": "K|s1",
                                           "nonce": self._nonce(app, token, "K|s1")}).body)
        assert _get(app, f"/api/job?id={job['job']['id']}", other).status == 404
        assert _get(app, "/api/job", token).status == 404

    def test_the_state_carries_the_row_keys_the_actions_and_the_jobs(self, world, store):
        _, token = _paired(store)
        app = _app(world, store)
        _act(app, token, {"row": "K|s1", "nonce": self._nonce(app, token, "K|s1")})
        body = json.loads(_get(app, "/api/state", token).body)
        detail = body["view"]["details"]["K|s1"]
        assert detail["step"]["button"] == {"text": "Merge…"}
        assert [j["row"] for j in body["jobs"]] == ["K|s1"]
        assert body["jobs"][0]["words"] == {"headline": "Done in 1 s", "line": "Merge",
                                            "tone": "ok"}
        assert detail["recent"] == [{"text": "Merge: Done in 1 s",
                                     "at": body["jobs"][0]["started"]}]

    def test_an_info_step_gets_no_action_and_a_hosted_row_one_card(self, world, store):
        _, token = _paired(store)
        info = _row("s2", kind="info", argv=(), cwd=None)
        hosted = _row("s3", cwd=str(world.cwd))
        hosted["hosts"] = [{"host_session_id": "h3", "current": True, "alive": True}]
        world.document = _doc(info, hosted)
        body = json.loads(_get(_app(world, store), "/api/state", token).body)
        details = body["view"]["details"]
        assert details["K|s2"]["step"] is None
        assert details["K|s3"]["step"]["button"] == {"text": "Merge…"}
        cards = [c["row"] for g in body["view"]["groups"] for c in g["cards"]]
        assert sorted(cards) == ["K|s2", "K|s3"]

    def test_a_read_only_app_offers_no_action(self, world, store):
        _, token = _paired(store)
        app = _app(world, store, remote=False)
        body = json.loads(_get(app, "/api/state", token).body)
        assert body["view"]["details"]["K|s1"]["step"]["button"] is None
        assert body["jobs"] == []
        assert _get(app, "/api/confirm?row=K%7Cs1", token).status == 404
        assert _act(app, token, {"row": "K|s1", "nonce": "x"}).status == 405

    def test_any_other_post_is_not_allowed(self, world, store):
        _, token = _paired(store)
        app = _app(world, store)
        response = app.handle(Request("POST", "/api/state", {
            "host": f"{TS4}:7341", "cookie": f"{COOKIE_NAME}={token}", "origin": ORIGIN}, b""))
        assert response.status == 405
        assert _get(app, "/api/act", token).status == 405


# -- a real socket, on loopback -------------------------------------------------------


def _http(url: str, method: str = "GET", body: bytes | None = None,
          headers: dict | None = None) -> tuple[int, bytes, dict]:
    """One request with ``http.client``, keeping the query string."""
    import http.client

    target = urllib.parse.urlsplit(url)
    path = (target.path or "/") + (f"?{target.query}" if target.query else "")
    connection = http.client.HTTPConnection(target.hostname, target.port, timeout=5)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        answer = connection.getresponse()
        return answer.status, answer.read(), dict(answer.getheaders())
    finally:
        connection.close()


def _loopback_world(world, store, **server_options):
    app = WebApp(store, lambda: _loaded(world.document), [LOOPBACK], remote=world.remote)
    server = WebServer(app, [LOOPBACK], 0, **server_options)
    url = server.start()[0]
    server.serve_in_background()
    return server, url


class TestOverTheSocket:
    def test_the_confirm_flow_end_to_end(self, world, store):
        world.spawn = lambda work: threading.Thread(target=work, daemon=True).start()
        _, token = _paired(store)
        server, url = _loopback_world(world, store)
        try:
            cookie = {"Cookie": f"{COOKIE_NAME}={token}"}
            origin = url.rstrip("/")
            status, body, _ = _http(url + "api/confirm?row=K%7Cs1", headers=cookie)
            assert status == 200
            shown = json.loads(body)
            assert shown["lines"][:2] == ["run   invariant session merge --id s1",
                                          f"in    {world.cwd}"]
            assert world.runner.calls == []
            act = json.dumps({"row": "K|s1", "nonce": shown["nonce"]}).encode()
            json_type = {"Content-Type": "application/json"}
            status, _, _ = _http(url + "api/act", "POST", act, {**cookie, **json_type})
            assert status == 403 and world.runner.calls == []
            status, body, _ = _http(url + "api/act", "POST", act,
                                    {**cookie, **json_type, "Origin": origin})
            assert status == 202
            job = json.loads(body)["job"]
            deadline = time.monotonic() + 20
            while True:
                status, body, _ = _http(url + f"api/job?id={job['id']}", headers=cookie)
                polled = json.loads(body)
                if polled["state"] == "done" or time.monotonic() > deadline:
                    break
                time.sleep(0.02)
            assert polled["state"] == "done" and polled["status"] == 0
            assert polled["output"] == "merged\n"
            status, _, _ = _http(url + "api/act", "POST", act,
                                 {**cookie, **json_type, "Origin": origin})
            assert status == 403
            assert world.runner.calls == [(MERGE, str(world.cwd), world.runner.calls[0][2])]
            assert [line["event"] for line in world.audit()] == ["start", "done"]
        finally:
            server.close()

    def test_a_slow_client_is_cut_off(self, world, store):
        server, url = _loopback_world(world, store, request_timeout=0.3)
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        try:
            with socket.create_connection((LOOPBACK, port), timeout=30) as sock:
                sock.sendall(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n")
                started = time.monotonic()
                assert sock.recv(1024) == b""
                # Cut off by the 0.3-second timeout, well before the default 10.
                assert time.monotonic() - started < 8
        finally:
            server.close()

    def test_too_many_headers_is_431(self, world, store):
        server, url = _loopback_world(world, store)
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        try:
            with socket.create_connection((LOOPBACK, port), timeout=5) as sock:
                headers = "".join(f"X-H{n}: v\r\n" for n in range(web.MAX_HEADERS + 1))
                sock.sendall(f"GET / HTTP/1.1\r\nHost: {LOOPBACK}\r\n{headers}\r\n".encode())
                answer = sock.recv(1024)
            assert answer.startswith((b"HTTP/1.0 431", b"HTTP/1.1 431"))
        finally:
            server.close()

    def test_a_bad_content_length_is_400(self, world, store):
        server, url = _loopback_world(world, store)
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        try:
            for length in (b"abc", b"-5"):
                with socket.create_connection((LOOPBACK, port), timeout=5) as sock:
                    sock.sendall(b"POST /pair HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                                 b"Content-Length: " + length + b"\r\n\r\n")
                    assert b" 400 " in sock.recv(1024).split(b"\r\n", 1)[0]
        finally:
            server.close()

    def test_a_malformed_request_line_is_400(self, world, store):
        server, url = _loopback_world(world, store)
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        try:
            with socket.create_connection((LOOPBACK, port), timeout=5) as sock:
                sock.sendall(b"NONSENSE\r\n\r\n")
                assert b" 400 " in sock.recv(1024).split(b"\r\n", 1)[0]
        finally:
            server.close()

    def test_connections_past_the_cap_are_closed_at_once(self, world, store):
        server, url = _loopback_world(world, store, request_timeout=20, max_connections=1)
        port = int(url.rsplit(":", 1)[1].rstrip("/"))
        slots = server.servers[0].slots
        try:
            with socket.create_connection((LOOPBACK, port), timeout=30) as held:
                held.sendall(b"GET / HTTP/1.1\r\n")
                deadline = time.monotonic() + 10
                while slots._value and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert slots._value == 0
                with socket.create_connection((LOOPBACK, port), timeout=30) as extra:
                    started = time.monotonic()
                    assert extra.recv(1024) == b""
                    # Closed at once, not after the 20-second read timeout.
                    assert time.monotonic() - started < 10
        finally:
            server.close()


# -- the page's script ---------------------------------------------------------------------


class TestThePageScript:
    def test_the_action_sends_only_the_row_and_the_nonce(self):
        script = _script()
        assert "/api/confirm?row=" in script and "/api/act" in script
        assert "innerHTML" not in script
        assert "ORACLE.actionBody(" in script

    @pytest.mark.skipif(ENGINE is None, reason="no JavaScript engine runs here")
    def test_the_helpers(self):
        import subprocess

        harness = _script() + r"""
(typeof print === "function" ? print : console.log)(JSON.stringify({
  body: JSON.parse(ORACLE.actionBody("K|s1", "n0")),
  full: ORACLE.countdown(120),
  later: ORACLE.countdown(65.2),
  gone: ORACLE.countdown(0),
  filled: ORACLE.fill("{a} and {b}", {a: 1}),
}));
"""
        out = subprocess.run([*ENGINE, harness], capture_output=True, text=True, timeout=30,
                             check=True)
        result = json.loads(out.stdout)
        assert result["body"] == {"row": "K|s1", "nonce": "n0"}
        assert result["full"] == {"text": "This confirm expires in 2:00", "expired": False}
        assert result["later"] == {"text": "This confirm expires in 1:06", "expired": False}
        assert result["gone"] == {"text": "This confirm expired.", "expired": True}
        assert result["filled"] == "1 and {b}"


# -- the command ------------------------------------------------------------------------------


class TestTheCommand:
    def _app(self, capsys, oracle_web, machine_world, monkeypatch):
        monkeypatch.setattr(cli_main, "_oracle_cache",
                            lambda: ScanCache(probe=machine_world["probe"]))
        oracle_web.addresses = [TS4]
        assert main(["oracle", "web"]) == 0
        capsys.readouterr()
        app = oracle_web.servers[0].app
        app.remote.spawn = lambda work: work()
        _, token = _paired(DeviceStore(oracle_home()))
        return app, token

    def _key(self, app, token, session_id):
        body = json.loads(_get(app, "/api/state", token).body)
        for key, detail in body["view"]["details"].items():
            if key.endswith("|" + session_id):
                return key, (detail["step"] or {}).get("button")
        raise AssertionError(session_id)

    def test_it_acts_through_the_runner_seam(self, capsys, oracle_web, machine_world,
                                             monkeypatch, _no_real_oracle_runner):
        app, token = self._app(capsys, oracle_web, machine_world, monkeypatch)
        key, action = self._key(app, token, "g-rev")
        assert action == {"text": "Merge…"}
        nonce = json.loads(_get(app, "/api/confirm?row=" + urllib.parse.quote(key),
                                token).body)["nonce"]
        response = _act(app, token, {"row": key, "nonce": nonce})
        assert response.status == 202, response.body
        ((argv, cwd, _),) = _no_real_oracle_runner.calls
        assert argv == ("invariant", "session", "merge", "--id", "g-rev")
        assert cwd == str(machine_world["gamma"].root)
        lines = (oracle_home() / AUDIT_FILE).read_text().splitlines()
        assert [json.loads(line)["event"] for line in lines] == ["start", "done"]

    def test_a_waiting_row_is_refused_through_the_command(self, capsys, oracle_web,
                                                          machine_world, monkeypatch,
                                                          _no_real_oracle_runner):
        app, token = self._app(capsys, oracle_web, machine_world, monkeypatch)
        key, action = self._key(app, token, "a-live")
        assert action is None
        body = json.loads(_get(app, "/api/confirm?row=" + urllib.parse.quote(key), token).body)
        assert body["nonce"] is None
        assert _no_real_oracle_runner.calls == []

    def test_a_terminal_step_shows_the_launch_watch(self, capsys, oracle_web, machine_world,
                                                    monkeypatch, _no_real_terminal_launch):
        from invariant.hosts import claude_code_launch as launch

        monkeypatch.setattr(launch, "host_platform", lambda: "darwin")
        monkeypatch.setattr(launch, "resolve_claude", lambda: "/usr/local/bin/claude")
        app, token = self._app(capsys, oracle_web, machine_world, monkeypatch)
        key, action = self._key(app, token, "a-parked")
        assert action == {"text": "Resume…"}
        shown = json.loads(_get(app, "/api/confirm?row=" + urllib.parse.quote(key),
                                token).body)
        assert any("--remote-control" in line for line in shown["lines"])
        job = json.loads(_act(app, token, {"row": key, "nonce": shown["nonce"]}).body)["job"]
        assert len(_no_real_terminal_launch.plans) == 1
        # The suite's watch finds no successor, so the page says so, with the
        # command to run by hand.
        assert job["ok"] is False and "did not start a successor" in job["output"]


def test_the_new_module_imports_no_host_and_no_toolkit():
    import re

    source = (Path(web.__file__).parent / "webactions.py").read_text()
    imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", source, re.MULTILINE)
    assert not [i for i in imports if "hosts" in i or "textual" in i or "tui" in i]



# -- BUG_079: the Origin a browser derives from the page's own headers -------------------


def _browser_origin(referrer_policy: str, mode: str, page_origin: str, target_origin: str) -> str:
    """The ``Origin`` a browser sends on a non-``GET`` request the page makes.

    The Fetch standard, "append a request `Origin` header", for a request whose
    response tainting is not "cors" (any same-origin request): if the request's
    mode is not "cors", the document's referrer policy decides, and
    ``no-referrer`` serializes the origin as ``null``. A form submission is a
    navigation (mode "navigate"); ``fetch()`` without a ``mode`` is "cors".
    """
    if mode == "cors":
        return page_origin
    policy = referrer_policy.split(",")[-1].strip().lower()
    if policy == "no-referrer":
        return "null"
    if policy == "same-origin" and page_origin != target_origin:
        return "null"
    if policy in ("no-referrer-when-downgrade", "strict-origin",
                  "strict-origin-when-cross-origin") and (
            page_origin.startswith("https:") and not target_origin.startswith("https:")):
        return "null"
    return page_origin


def _fetch_mode(page_html: str, url: str) -> str:
    """The ``mode`` the page's script passes to ``fetch(url, ...)``; "cors" without one."""
    import re

    call = re.search(r'fetch\("' + re.escape(url) + r'",\s*\{(.*?)\}\);', page_html, re.DOTALL)
    assert call, f"the page no longer fetches {url}"
    mode = re.search(r'\bmode:\s*"([a-z-]+)"', call.group(1))
    return mode.group(1) if mode else "cors"


def _shown_text(body: bytes) -> str:
    """What a person reads on an HTML answer: the body, without style, script or tags."""
    import re

    text = re.sub(r"<(style|script)\b.*?</\1>", " ", body.decode("utf-8", "replace"), flags=re.DOTALL)
    return " ".join(re.sub(r"<[^>]+>", " ", text).split())


# BUG_079: under Referrer-Policy no-referrer the pairing form's own POST carried
# Origin: null and was refused. Per the Fetch standard a fetch() POST in the
# default "cors" mode keeps its real Origin under any referrer policy, so the
# "act" case guards the action route.
@pytest.mark.parametrize("surface", ["pair", "act"])
def test_BUG_079_the_pages_own_post_is_accepted_with_the_origin_a_browser_derives(
        world, store, surface):
    app = _app(world, store)
    if surface == "pair":
        # The page that holds the form, as the pairing link opens it.
        page = _get(app, "/pair?code=ignored")
        assert page.status == 200 and '<form method="post" action="/pair">' in page.body.decode()
        mode = "navigate"
    else:
        _, token = _paired(store)
        page = _get(app, "/", token)
        assert page.status == 200
        mode = _fetch_mode(page.body.decode(), "/api/act")
    policy = page.header("referrer-policy") or "strict-origin-when-cross-origin"
    origin = _browser_origin(policy, mode, ORIGIN, ORIGIN)
    headers = {"host": f"{TS4}:7341", "origin": origin, "sec-fetch-site": "same-origin"}

    if surface == "pair":
        code = store.issue("phone", ttl=300).code
        response = app.handle(Request("POST", "/pair", {
            **headers, "content-type": "application/x-www-form-urlencoded",
            "sec-fetch-mode": "navigate"}, f"code={code}".encode()))
        assert response.status == 303, (
            f"the pairing form posted from its own page, Referrer-Policy {policy!r} -> "
            f"Origin {origin!r}, was refused: HTTP {response.status} "
            f"{_shown_text(response.body)!r}")
        assert store.verify(response.header("set-cookie").split(";")[0].split("=", 1)[1])
    else:
        shown = json.loads(_get(app, "/api/confirm?row=K%7Cs1", token).body)
        response = app.handle(Request("POST", "/api/act", {
            **headers, "content-type": "application/json", "sec-fetch-mode": mode,
            "cookie": f"{COOKIE_NAME}={token}"},
            json.dumps({"row": "K|s1", "nonce": shown["nonce"]}).encode()))
        assert response.status == 202, (
            f"the page's own action, Referrer-Policy {policy!r}, fetch mode {mode!r} -> "
            f"Origin {origin!r}, was refused: HTTP {response.status} {response.body!r}")
        assert world.runner.calls[0][0] == MERGE
