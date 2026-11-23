"""ADR-027 increment 3: the chain on the phone panel (rulings 14 to 16, D31 to D37).

The phone shows each project's chain and acts on it through the CLI's own verbs:

* **the read** (``oracle/chainview.ChainReader``): one read of the chain ledger,
  each project's queue and the plan's usage hold, with the actions each
  project offers now. The panel words it and the server derives the steps
  from it, so what is offered and what may run come from one place;
* **the steps**: every chain action is an ``invariant`` argv, derived by the
  server from a fresh read, confirmed with a nonce and run through the
  page's runner like any step (ruling 14). A move or a remove names the entry
  and the position the confirm saw, and the queue refuses it under its lock
  when the entry has moved since (``--if-queued-at``);
* **the words** (``oracle/panel``): the Needs-you cards, the Projects row's
  chain line and the project Chain screen (ruling 15), every string from the
  panel's table;
* **the page** (``oracle/webpage``): the chain screen drawn inside the one page.

Nothing here runs a real command or opens a window: the runner is a
``RecordingRunner``, the CLI tests run behind the suite's inert launcher.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.parse
from pathlib import Path

import pytest
from test_oracle_web import ENGINE, TS4, Clock, _loaded
from test_oracle_web_actions import ORIGIN, PHONE, TABLET
from test_oracle_webpage import _run

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core import queue as queue_record
from invariant.core.errors import UsageError
from invariant.core.manifest import Manifest
from invariant.core.queue import Queue
from invariant.core.store import JsonStore
from invariant.domains.queue import QueueDomain
from invariant.oracle import chainstep, chainview, panel, webactions
from invariant.oracle.actions import Actions, RecordingRunner
from invariant.oracle.devices import COOKIE_NAME, DeviceStore
from invariant.oracle.index import HomeStore
from invariant.oracle.scan import ScanCache
from invariant.oracle.usage import PlanUsage
from invariant.oracle.web import Request, WebApp
from invariant.oracle.webactions import AUDIT_FILE, AuditLog, RemoteActions

NOW = 1_792_640_000.0
KEY = "K"


# -- the queue's own guard: --if-queued-at (D33) --------------------------------------


def _queue(*refs: str) -> Queue:
    q = Queue()
    for ref in refs:
        q.add(ref, added_by="user")
    return q


class TestTheQueueGuard:
    def test_an_entry_still_queued_where_the_confirm_saw_it_passes(self):
        q = _queue("a", "b", "c")
        assert q.expect("Q2", 2).id == "Q2"

    def test_an_entry_that_moved_is_refused_naming_where_it_is(self):
        q = _queue("a", "b", "c")
        q.move("Q2", 3)
        with pytest.raises(UsageError, match="position 3"):
            q.expect("Q2", 2)

    def test_an_entry_no_longer_queued_is_refused(self):
        q = _queue("a", "b")
        q.mark("Q1", "started")
        with pytest.raises(UsageError, match="started"):
            q.expect("Q1", 1)

    def test_an_entry_that_is_gone_is_refused(self):
        with pytest.raises(UsageError, match="no entry"):
            _queue("a").expect("Q9", 1)


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _scan_without_a_probe(monkeypatch):
    monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache())


@pytest.fixture(autouse=True)
def _fresh_usage(monkeypatch):
    monkeypatch.setattr(cli_main, "_oracle_plan_usage", lambda index: lambda: PlanUsage(
        weekly=10.0, weekly_resets_at=None, five_hour=1.0, five_hour_resets_at=None,
        read_at=time.time()))


@pytest.fixture
def project(tmp_path, monkeypatch):
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


def _domain(root: Path) -> QueueDomain:
    manifest = Manifest.locate(root)
    return QueueDomain(manifest, JsonStore(manifest.state_root))


class TestTheGuardedEdits:
    def test_move_and_remove_check_the_position_under_the_lock(self, project):
        domain = _domain(project)
        for ref in ("a", "b", "c"):
            domain.add(ref)
        domain.move("Q3", 2, expect_at=3)
        assert [e.id for e in domain.list().entries] == ["Q1", "Q3", "Q2"]
        with pytest.raises(UsageError, match="position 2"):
            domain.move("Q3", 1, expect_at=3)
        with pytest.raises(UsageError, match="position 3"):
            domain.remove("Q2", expect_at=1)
        assert [e.id for e in domain.list().entries] == ["Q1", "Q3", "Q2"]
        assert domain.remove("Q2", expect_at=3).ref == "b"

    def test_the_commands_take_the_guard(self, project, capsys):
        for ref in ("a", "b"):
            assert main(["queue", "add", ref]) == 0
        capsys.readouterr()
        assert main(["queue", "move", "Q2", "--to", "1", "--if-queued-at", "2"]) == 0
        assert main(["queue", "move", "Q2", "--to", "2", "--if-queued-at", "2"]) == 2
        assert "nothing was moved" in capsys.readouterr().err
        assert main(["queue", "remove", "Q1", "--if-queued-at", "1"]) == 2
        assert "nothing was removed" in capsys.readouterr().err
        assert main(["queue", "remove", "Q1", "--if-queued-at", "2", "--json"]) == 0
        assert json.loads(capsys.readouterr().out)["removed"]["id"] == "Q1"
        assert [e.id for e in _domain(project).list().entries] == ["Q2"]


# -- oracle chain start --item (D34) ------------------------------------------------------


def _file(root: Path, title: str) -> str:
    assert main(["item", "new", "idea", "--title", title, "--json"]) == 0
    return _domain(root).items.list()[-1].id


class TestStartABacklogItem:
    def test_it_starts_that_ready_item_and_records_it(self, project, capsys, monkeypatch):
        first = _file(project, "First thing")
        second = _file(project, "Second thing")
        capsys.readouterr()
        launched = []
        monkeypatch.setattr(cli_main, "_start_launch", lambda r: (
            launched.append(r) or ({**r, "successor": {"pid": 9}}, ["opened"])))
        assert main(["oracle", "chain", "start", "demo", "--item", second, "--json"]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["started"]["ref"] == second
        assert [e.ref for e in _domain(project).list().entries] == [second]
        assert first not in launched[0]["name"]

    def test_an_item_not_ready_or_already_queued_is_refused(self, project, capsys):
        item = _file(project, "Something")
        assert main(["queue", "add", item]) == 0
        capsys.readouterr()
        assert main(["oracle", "chain", "start", "demo", "--item", item]) == 2
        assert "not a ready backlog item" in capsys.readouterr().err
        assert main(["oracle", "chain", "start", "demo", "--item", "IDEA_404"]) == 2

    def test_entry_and_item_together_are_refused(self, project, capsys):
        with pytest.raises(SystemExit):
            main(["oracle", "chain", "start", "demo", "--entry", "Q1", "--item", "IDEA_1"])


# -- the ledger, read for the phone ---------------------------------------------------------


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "oracle-home"
    path.mkdir()
    return path


def _ledger(home: Path, projects: dict, usage: dict | None = None) -> None:
    HomeStore(home).write(chainstep.LEDGER_FILE, {
        "version": 1, "projects": projects, "notices": [],
        **({"usage": usage} if usage is not None else {})})


class TestReadLedger:
    def test_an_empty_home_reads_as_an_empty_ledger(self, home):
        ledger = chainstep.read_ledger(home)
        assert ledger["projects"] == {} and ledger["usage"]["greenlight"] is None

    def test_each_project_is_filled_in(self, home):
        _ledger(home, {KEY: {"paused": {"reason": "x", "by": "user", "since": "t"}}})
        state = chainstep.read_ledger(home)["projects"][KEY]
        assert state["paused"]["reason"] == "x" and state["proposed"] == {}

    def test_an_unreadable_ledger_raises(self, home):
        (home / "oracle").mkdir(exist_ok=True)
        (home / chainstep.LEDGER_FILE).write_text("[]")
        with pytest.raises(chainstep.LedgerError):
            chainstep.read_ledger(home)


# -- the chain view: the read and its offers (D31) -----------------------------------------


def _usage(weekly=10.0, read_at=NOW - 60):
    return PlanUsage(weekly=weekly, weekly_resets_at=None, five_hour=1.0,
                     five_hour_resets_at=None, read_at=read_at)


def _entries(*rows) -> Queue:
    q = Queue()
    for ref, extra in rows:
        entry = q.add(ref, added_by=extra.get("by", "user"), mode=extra.get("mode"),
                      brief=extra.get("brief"), triage_of=extra.get("triage_of"),
                      retry_of=extra.get("retry_of"))
        if extra.get("state"):
            q.mark(entry.id, extra["state"], reason=extra.get("reason"))
    return q


def _doc(rows=(), key=KEY, name="Heap", root="/r", more=()):
    return {"now": NOW, "projects": [
        {"project": {"key": key, "name": name, "root": root}, "rows": list(rows)}, *more]}


def _stalled_row(sid="s9", pid=4100, last=NOW - 3 * 3600):
    return {"kind": "session", "project": KEY, "session_id": sid, "name": f"work-{sid}",
            "state": "isolated", "last_activity": last,
            "hosts": [{"host_session_id": "27885f4e-aaaa-bbbb-cccc-ddddeeeeffff",
                       "current": True, "pid": pid, "alive": True, "tty": "ttys004",
                       "process_start": "Mon Nov 18 09:00:00 2026"}],
            "signals": [{"kind": "stalled", "cause": "limit", "alert": True,
                         "severity": "medium"}],
            "recommendation": None}


def _current(entry="Q1", ref="IDEA_7", state="running", stall=None, session="s9"):
    return {"id": "C1", "state": state, "source": "queue", "ref": ref, "entry": entry,
            "mode": "auto", "name": "heap-idea-7", "pid": 4100, "session": session,
            "at": "2026-11-18T09:00:00+00:00", "at_epoch": NOW - 4 * 3600, "stall": stall}


def _reader(home, queue=None, reading=None, titles=None, cap=95):
    queues = {"/r": queue if queue is not None else Queue()}
    return chainview.ChainReader(
        home, usage=lambda: reading if reading is not None else _usage(), cap=cap,
        clock=lambda: NOW, queues=lambda root: queues[root],
        titles=titles or (lambda root, ref: f"Title of {ref}"))


def _offers(read, key=KEY):
    return [(o["action"], o["arg"]) for o in read["projects"][key]["offered"]]


class TestTheKeys:
    def test_a_key_round_trips(self):
        key = chainview.action_key("/a|b", chainview.MOVE_UP, "Q4")
        assert key == "/a|b|chain:move_up:Q4"
        assert chainview.parse_key(key) == ("/a|b", "move_up", "Q4")
        assert chainview.parse_key("K|chain:pause") == ("K", "pause", None)

    @pytest.mark.parametrize("key", ["K|s1", "K|chain:", "K|chain:dance", "nochain",
                                     "K|chain:move_up", "K|chain:pause:Q1"])
    def test_anything_else_is_not_a_chain_key(self, key):
        assert chainview.parse_key(key) is None

    def test_the_project_key_is_the_chains_job_row(self):
        assert chainview.job_row("K|chain:remove:Q1") == "K|chain"


class TestTheRead:
    def test_an_idle_project_offers_only_a_pause(self, home):
        read = _reader(home).read(_doc())
        state = read["projects"][KEY]
        assert state["status"] == chainview.IDLE and state["queued"] == []
        assert _offers(read) == [("pause", None)]
        assert read["usage"]["hold"] is None and read["offered"] == []

    def test_the_queue_offers_up_down_and_remove_by_position(self, home):
        queue = _entries(("a", {"state": "done"}), ("b", {}), ("c", {"mode": "propose"}),
                         ("d", {}))
        read = _reader(home, queue).read(_doc())
        state = read["projects"][KEY]
        assert state["status"] == chainview.WAITING
        assert state["queued"] == ["Q2", "Q3", "Q4"]
        assert [e["position"] for e in state["entries"]] == [1, 2, 3, 4]
        offers = _offers(read)
        assert ("move_up", "Q2") not in offers and ("move_down", "Q4") not in offers
        assert {("move_down", "Q2"), ("move_up", "Q3"), ("move_down", "Q3"), ("move_up", "Q4"),
                ("remove", "Q2"), ("remove", "Q3"), ("remove", "Q4")} <= set(offers)
        # A queued entry of its own propose mode can always be started by hand.
        assert ("start", "Q3") in offers and ("start", "Q2") not in offers

    def test_a_paused_chain_offers_resume_not_pause(self, home):
        chainstep.pause(home, KEY, "lunch", NOW - 600)
        read = _reader(home).read(_doc())
        assert read["projects"][KEY]["status"] == chainview.PAUSED
        assert read["projects"][KEY]["paused"]["reason"] == "lunch"
        assert ("resume", None) in _offers(read) and ("pause", None) not in _offers(read)

    def test_a_running_start_offers_no_start_and_no_stall_action(self, home):
        queue = _entries(("IDEA_7", {"state": "started"}), ("x", {"mode": "propose"}))
        _ledger(home, {KEY: {"current": _current()}})
        read = _reader(home, queue).read(_doc())
        state = read["projects"][KEY]
        assert state["status"] == chainview.RUNNING
        assert state["current"]["entry"] == "Q1" and state["current"]["stall"] is None
        assert not {a for a, _ in _offers(read)} & {"start", "continue", "retry", "close"}

    def test_a_launching_start_is_starting(self, home):
        _ledger(home, {KEY: {"current": _current(state="launching", session=None)}})
        assert _reader(home).read(_doc())["projects"][KEY]["status"] == chainview.STARTING

    def test_a_recorded_stall_offers_continue_retry_and_close(self, home):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": "typed",
                 "nudged_epoch": NOW - 2 * 3600, "still": False, "still_said": True}
        _ledger(home, {KEY: {"current": _current(stall=stall)}})
        read = _reader(home).read(_doc([_stalled_row()]))
        assert {("continue", None), ("retry", None), ("close", None)} <= set(_offers(read))
        assert read["projects"][KEY]["current"]["stall"]["still_said"] is True

    def test_the_steps_proposals_stay_while_still_open(self, home):
        queue = _entries(("a", {"mode": "propose"}), ("b", {"state": "started"}),
                         ("c", {}))
        _ledger(home, {KEY: {"proposed": {
            "entry:Q1": {"since": "2026-11-18T08:00:00+00:00"},
            "entry:Q2": {"since": "2026-11-18T08:00:00+00:00"},
            "queue:Q3": {"since": "2026-11-18T08:30:00+00:00"},
            "backlog:IDEA_9": {"since": "2026-11-18T08:40:00+00:00"},
            "backlog:c": {"since": "2026-11-18T08:40:00+00:00"},
        }}})
        read = _reader(home, queue).read(_doc())
        proposals = read["projects"][KEY]["proposals"]
        assert [(p["source"], p["entry"], p["ref"]) for p in proposals] == [
            ("queue", "Q1", "a"), ("queue", "Q3", "c"), ("backlog", None, "IDEA_9")]
        assert proposals[2]["title"] == "Title of IDEA_9"
        assert ("start_item", "IDEA_9") in _offers(read)
        assert ("start", "Q3") in _offers(read)

    def test_an_unknown_usage_hold_offers_the_greenlight_and_holds_queued_work(self, home):
        _ledger(home, {}, usage={"held": {"kind": "unknown", "since": "t"},
                                 "greenlight": None})
        read = _reader(home, _entries(("a", {})), reading=_usage(read_at=NOW - 3600)) \
            .read(_doc())
        assert read["usage"]["hold"] == "unknown" and read["usage"]["held"] is True
        assert read["usage"]["why"] == "stale" and read["usage"]["age"] == 3600
        assert read["projects"][KEY]["status"] == chainview.HELD
        assert [(o["action"], o["key"]) for o in read["offered"]] == [
            ("greenlight", "oracle|chain:greenlight")]

    @pytest.mark.parametrize("reading, why", [
        (None, "missing"),
        (PlanUsage(None, None, 2.0, None, NOW - 10), "no_weekly"),
    ])
    def test_why_the_reading_is_unknown(self, home, reading, why):
        reader = _reader(home)
        reader.usage = lambda: reading
        assert reader.read(_doc())["usage"]["why"] == why

    def test_an_over_hold_offers_no_greenlight(self, home):
        read = _reader(home, reading=_usage(weekly=97.0)).read(_doc())
        assert read["usage"]["hold"] == "over" and read["offered"] == []
        assert read["usage"]["weekly"] == 97.0 and read["usage"]["cap"] == 95

    def test_a_greenlight_given_lifts_the_hold_until_a_fresh_reading(self, home):
        _ledger(home, {}, usage={"held": None,
                                 "greenlight": {"at": "t", "at_epoch": NOW - 30}})
        read = _reader(home, reading=_usage(read_at=NOW - 7200)).read(_doc())
        assert read["usage"]["hold"] is None and read["usage"]["greenlight"] == "t"

    def test_an_unreadable_ledger_offers_nothing_anywhere(self, home):
        (home / "oracle").mkdir(exist_ok=True)
        (home / chainstep.LEDGER_FILE).write_text("[]")
        read = _reader(home, _entries(("a", {}))).read(_doc())
        assert "cannot be read" in read["error"]
        assert read["projects"][KEY]["status"] == chainview.UNREAD
        assert _offers(read) == [] and read["offered"] == []

    def test_an_unreadable_queue_is_that_projects_error(self, home):
        def broken(root):
            raise queue_record.StateError("the queue at /r is not valid JSON")
        reader = _reader(home)
        reader.queues = broken
        read = reader.read(_doc())
        assert read["projects"][KEY]["error"] == "the queue at /r is not valid JSON"
        assert read["projects"][KEY]["status"] == chainview.UNREAD
        assert _offers(read) == []

    def test_only_reads_one_project_and_skips_unread_ones(self, home):
        other = {"project": {"key": "L", "name": "Lark", "root": "/l"}, "rows": []}
        unread = {"project": {"key": "M", "name": "Moss", "root": "/m"}, "rows": [],
                  "unread": "older oracle"}
        reader = _reader(home)
        assert set(reader.read(_doc(more=[other, unread]), only=KEY)["projects"]) == {KEY}

    def test_the_queue_path_is_read_once_a_minute(self, project, home):
        _domain(project).add("a")
        reader = chainview.ChainReader(home, usage=lambda: _usage(), clock=lambda: NOW)
        document = _doc(root=str(project))
        assert reader.read(document)["projects"][KEY]["queued"] == ["Q1"]
        _domain(project).add("b")
        assert reader.read(document)["projects"][KEY]["queued"] == ["Q1", "Q2"]
        assert len(reader._paths) == 1

    def test_a_backlog_title_is_read_from_the_project(self, project):
        item = _file(project, "A thing worth doing")
        assert chainview.item_title(str(project), item) == "A thing worth doing"
        assert chainview.item_title(str(project), "IDEA_404") is None


# -- the steps the server derives (D32 to D34) -------------------------------------------


def _derive(home, key, queue=None, rows=(), ledger=None, reading=None):
    if ledger is not None:
        _ledger(home, ledger)
    document = _doc(rows)
    read = _reader(home, queue, reading=reading).read(document)
    return chainview.derive(read, document, key, home)


class TestTheDerivation:
    def test_move_up_names_the_neighbour_and_the_confirmed_position(self, home):
        queue = _entries(("a", {}), ("x", {"state": "done"}), ("b", {}))
        derived = _derive(home, "K|chain:move_up:Q3", queue)
        assert derived.argv == ("invariant", "queue", "move", "Q3", "--to", "1",
                                "--if-queued-at", "3")
        assert derived.cwd == "/r" and derived.name == "Heap"
        assert derived.facts["entry"] == "Q3" and derived.facts["other"] == "Q1"
        assert derived.facts["ref"] == "b" and derived.facts["other_ref"] == "a"

    def test_move_down_and_remove(self, home):
        queue = _entries(("a", {}), ("x", {"state": "done"}), ("b", {}))
        assert _derive(home, "K|chain:move_down:Q1", queue).argv == (
            "invariant", "queue", "move", "Q1", "--to", "3", "--if-queued-at", "1")
        assert _derive(home, "K|chain:remove:Q3", queue).argv == (
            "invariant", "queue", "remove", "Q3", "--if-queued-at", "3")

    def test_the_chain_verbs(self, home):
        assert _derive(home, "K|chain:pause").argv == ("invariant", "oracle", "chain",
                                                         "pause", KEY)
        chainstep.pause(home, KEY, "x", NOW)
        assert _derive(home, "K|chain:resume").argv[-2:] == ("resume", KEY)
        queue = _entries(("a", {"mode": "propose"}))
        assert _derive(home, "K|chain:start:Q1", queue).argv == (
            "invariant", "oracle", "chain", "start", KEY, "--entry", "Q1")

    def test_a_backlog_proposal_starts_its_item(self, home):
        derived = _derive(home, "K|chain:start_item:IDEA_9",
                          ledger={KEY: {"proposed": {"backlog:IDEA_9": {"since": "t"}}}})
        assert derived.argv[-2:] == ("--item", "IDEA_9")
        assert derived.facts["ref"] == "IDEA_9" and derived.facts["title"] == "Title of IDEA_9"

    def test_the_greenlight_runs_in_the_oracle_home(self, home):
        _ledger(home, {}, usage={"held": {"kind": "unknown"}, "greenlight": None})
        derived = _derive(home, "oracle|chain:greenlight", reading=_usage(read_at=NOW - 7200))
        assert derived.argv == ("invariant", "oracle", "chain", "greenlight")
        assert derived.cwd == str(home) and derived.name == ""

    def test_retry_and_close_carry_the_stalled_window(self, home):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": "no_tab",
                 "still": False, "still_said": False}
        derived = _derive(home, "K|chain:retry", rows=[_stalled_row()],
                          ledger={KEY: {"current": _current(stall=stall)}})
        assert derived.argv == ("invariant", "oracle", "chain", "retry", KEY)
        assert derived.facts["window"] == "27885f4e" and derived.facts["pid"] == 4100
        assert derived.facts["last_activity"] == NOW - 3 * 3600
        assert derived.facts["entry"] == "Q1" and derived.facts["ref"] == "IDEA_7"

    def test_a_stall_whose_window_is_gone_has_no_window(self, home):
        stall = {"since_epoch": NOW, "cause": "error", "nudge": "no_tab"}
        derived = _derive(home, "K|chain:close", rows=[],
                          ledger={KEY: {"current": _current(stall=stall)}})
        assert derived.facts["window"] is None

    @pytest.mark.parametrize("key, needle", [
        ("K|chain:move_up:Q1", "first"),
        ("K|chain:move_down:Q2", "last"),
        ("K|chain:remove:Q7", "not queued"),
        ("K|chain:resume", "not paused"),
        ("K|chain:start:Q1", "not offered"),
        ("K|chain:start_item:IDEA_1", "not offered"),
        ("K|chain:continue", "no stall"),
        ("oracle|chain:greenlight", "unknown"),
        ("Z|chain:pause", "not registered"),
    ])
    def test_what_is_not_offered_now_is_refused_with_why(self, home, key, needle):
        why = _derive(home, key, _entries(("a", {}), ("b", {})))
        assert isinstance(why, str) and needle in why

    def test_a_start_while_the_chain_follows_one_is_refused(self, home):
        queue = _entries(("IDEA_7", {"state": "started"}), ("b", {"mode": "propose"}))
        why = _derive(home, "K|chain:start:Q2", queue, ledger={KEY: {"current": _current()}})
        assert "already follows Q1" in why

    def test_an_unreadable_chain_is_refused(self, home):
        (home / "oracle").mkdir(exist_ok=True)
        (home / chainstep.LEDGER_FILE).write_text("[]")
        assert "cannot be read" in _derive(home, "K|chain:pause")

    def test_a_key_that_is_not_the_chains_is_refused(self, home):
        assert "not a chain action" in _derive(home, "K|s1")


# -- the words (ruling 15, ruling 16) ---------------------------------------------------


def _view(home, queue=None, rows=(), ledger=None, reading=None, acting=True, jobs=(),
          usage_ledger=None):
    if ledger is not None or usage_ledger is not None:
        _ledger(home, ledger or {}, usage=usage_ledger)
    document = _doc(rows)
    chains = _reader(home, queue, reading=reading).read(document)
    return panel.view(document, acting=acting, jobs=list(jobs), starting=True,
                      chains=chains)


def _needs(view):
    group = next((g for g in view["groups"] if g["id"] == "needs"), None)
    return group["cards"] if group else []


ENGINE_SHAPED = ("/r", "chain.json", "launching", "auto", "invariant oracle")


class TestTheProjectsRow:
    def test_each_project_carries_its_chain_line(self, home):
        queue = _entries(("IDEA_7", {"state": "started"}), ("b", {}), ("c", {}),
                         ("d", {"mode": "propose"}))
        _ledger(home, {KEY: {"current": _current()}})
        view = _view(home, queue)
        assert view["projects"] == [{"id": KEY, "name": "Heap", "line": "No open session",
                                     "chain": "Chain: running Q1 · 3 queued"}]

    @pytest.mark.parametrize("ledger, queue, line", [
        ({}, (), "Chain: idle"),
        ({}, (("a", {}),), "Chain: waiting · 1 queued"),
        ({KEY: {"paused": {"reason": "x", "by": "user", "since": "t"}}}, (("a", {}),),
         "Chain: paused · 1 queued"),
        ({KEY: {"current": _current(state="launching", session=None)}}, (),
         "Chain: starting Q1"),
    ])
    def test_the_states(self, home, ledger, queue, line):
        assert _view(home, _entries(*queue), ledger=ledger)["projects"][0]["chain"] == line

    def test_a_held_chain_and_its_proposals(self, home):
        queue = _entries(("a", {}), ("b", {"mode": "propose"}))
        _ledger(home, {KEY: {"proposed": {"entry:Q2": {"since": "t"}}}},
                usage={"held": {"kind": "unknown"}, "greenlight": None})
        view = _view(home, queue, reading=_usage(read_at=NOW - 7200))
        assert view["projects"][0]["chain"] == "Chain: held · 2 queued · 1 proposed"

    def test_without_a_chain_read_there_is_no_line_and_no_chain(self, home):
        view = panel.view(_doc(), acting=True, starting=True)
        assert view["projects"][0]["chain"] is None and view["chain"] is None


class TestTheNeedsYouCards:
    def test_a_proposal_is_a_card_with_start(self, home):
        queue = _entries(("fix it", {"mode": "propose"}))
        view = _view(home, queue, ledger={KEY: {"proposed": {
            "entry:Q1": {"since": "2026-11-18T08:00:00+00:00"}}}})
        card = _needs(view)[0]
        assert card["project"] == "Heap" and card["title"] == "fix it"
        assert card["chips"] == [{"text": "Proposed", "tone": "needs"}]
        assert card["button"] == {"text": "Start…", "row": "K|chain:start:Q1"}
        assert card["chain"] == KEY and card["actions"] == []
        assert view["summary"]["needs"] == 1 and view["doc_title"] == "(1) Sessions"

    def test_a_backlog_proposal_is_worded_by_its_title(self, home):
        view = _view(home, ledger={KEY: {"proposed": {"backlog:IDEA_9": {"since": "t"}}}})
        card = _needs(view)[0]
        assert card["title"] == "Title of IDEA_9"
        assert card["button"]["row"] == "K|chain:start_item:IDEA_9"
        assert "backlog" in card["note"]

    def test_a_stall_the_nudge_did_not_clear_has_its_three_actions(self, home):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": "typed",
                 "nudged_epoch": NOW - 2 * 3600, "still": True, "still_said": True}
        view = _view(home, rows=[_stalled_row()], ledger={KEY: {"current": _current(stall=stall)}})
        card = next(c for c in _needs(view) if c["row"].endswith("chain:card:stall"))
        assert card["chips"] == [{"text": "Still stalled", "tone": "needs"}]
        assert [a["text"] for a in card["actions"]] == ["Continue…", "Retry…", "Close…"]
        assert [a["row"] for a in card["actions"]] == [
            "K|chain:continue", "K|chain:retry", "K|chain:close"]
        assert card["button"] is None and card["age"] == "3 h"

    def test_a_stall_that_could_not_be_nudged_says_so(self, home):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "error", "nudge": "no_tab"}
        view = _view(home, rows=[_stalled_row()], ledger={KEY: {"current": _current(stall=stall)}})
        card = next(c for c in _needs(view) if c["row"].endswith("chain:card:stall"))
        assert card["chips"][0]["text"] == "Not nudged"
        assert "Terminal tab" in card["note"]

    def test_a_stall_still_waiting_for_its_nudge_is_no_card(self, home):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": None,
                 "still": False, "still_said": False}
        view = _view(home, rows=[_stalled_row()], ledger={KEY: {"current": _current(stall=stall)}})
        assert not any(c["row"].endswith("chain:card:stall") for c in _needs(view))

    def test_a_paused_chain_is_a_card_with_resume(self, home):
        view = _view(home, ledger={KEY: {"paused": {
            "reason": "session work reported needs-user: look", "by": "report",
            "since": "2026-11-18T08:00:00+00:00"}}})
        card = _needs(view)[0]
        assert card["title"] == "The chain is paused"
        assert card["button"] == {"text": "Resume chain…", "row": "K|chain:resume"}
        assert card["note"] == "A session reported that it is blocked or needs you."
        assert "needs-user" not in json.dumps(card)

    def test_the_unknown_usage_hold_is_one_card_with_go_ahead(self, home):
        view = _view(home, _entries(("a", {})), reading=_usage(read_at=NOW - 7200),
                     usage_ledger={"held": {"kind": "unknown"}, "greenlight": None})
        card = next(c for c in _needs(view) if c["row"] == "oracle|chain:card:usage")
        assert card["project"] == "Every project"
        assert card["title"] == "The weekly usage cannot be read"
        assert card["button"] == {"text": "Go ahead…", "row": "oracle|chain:greenlight"}
        assert "2 h old" in card["note"]
        detail = view["details"]["oracle|chain:card:usage"]
        assert detail["going_on"] and detail["technical"]["lines"]

    def test_the_over_hold_is_shown_without_a_greenlight(self, home):
        view = _view(home, _entries(("a", {})), reading=_usage(weekly=97.0),
                     usage_ledger={"held": {"kind": "over"}, "greenlight": None})
        card = next(c for c in _needs(view) if c["row"] == "oracle|chain:card:usage")
        assert card["title"] == "The weekly usage is over the cap"
        assert card["button"] is None and "97%" in card["note"] and "95%" in card["note"]

    def test_a_hold_the_step_never_said_is_no_card(self, home):
        view = _view(home, reading=_usage(read_at=NOW - 7200))
        assert not _needs(view)

    def test_a_read_only_view_draws_the_cards_without_buttons(self, home):
        view = _view(home, ledger={KEY: {"paused": {"reason": "x", "by": "user",
                                                    "since": "t"}}}, acting=False)
        assert _needs(view)[0]["button"] is None
        assert view["chain"]["projects"][KEY]["control"] is None

    def test_no_card_holds_an_engine_name(self, home):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": "typed",
                 "still": True, "still_said": True}
        queue = _entries(("IDEA_7", {"state": "started"}), ("b", {"mode": "propose"}))
        view = _view(home, queue, rows=[_stalled_row()], ledger={KEY: {
            "current": _current(stall=stall), "proposed": {"entry:Q2": {"since": "t"}},
            "paused": {"reason": "the triage Q3 failed: launch", "by": "failure",
                       "since": "t"}}})
        shown = json.dumps(_needs(view)) + json.dumps(view["projects"])
        for word in ENGINE_SHAPED:
            assert word not in shown


class TestTheChainScreen:
    def test_the_screen_of_a_waiting_queue(self, home):
        queue = _entries(("a", {"state": "done"}), ("IDEA_3", {"brief": "do the thing"}),
                         ("b", {"mode": "propose", "by": "s1"}),
                         ("triage Q1", {"triage_of": "Q1", "by": "oracle"}))
        screen = _view(home, queue)["chain"]["projects"][KEY]
        assert screen["title"] == "Heap" and screen["kicker"] == "Chain"
        assert screen["chips"] == [{"text": "Waiting", "tone": "idle"}]
        assert screen["going_on"] == [
            "3 entries are queued. The next one starts when nothing else runs in Heap."]
        assert screen["control"] == {"text": "Pause chain…", "row": "K|chain:pause",
                                     "primary": False}
        assert screen["now"] is None
        rows = screen["queue"]
        assert [r["title"] for r in rows] == ["Q2 · IDEA_3", "Q3 · b", "Q4 · triage Q1"]
        assert rows[0]["line"] == "Added by you"
        assert rows[0]["brief"] == "do the thing"
        assert rows[1]["line"] == "Offered, never started on its own · Added by a session"
        assert rows[2]["line"] == "Triage of Q1 · Added by the oracle"
        assert rows[0]["up"] is None
        assert rows[0]["down"] == {"label": "Move Q2 down…", "row": "K|chain:move_down:Q2"}
        assert rows[2]["down"] is None
        assert rows[1]["remove"] == {"text": "Remove…", "row": "K|chain:remove:Q3"}
        assert [p["title"] for p in screen["proposed"]] == ["b"]
        assert screen["proposed"][0]["button"] == {"text": "Start…", "row": "K|chain:start:Q3"}
        assert screen["empty"] is None

    def test_an_empty_queue_says_so(self, home):
        screen = _view(home)["chain"]["projects"][KEY]
        assert screen["queue"] == [] and screen["empty"] == "Nothing is queued."
        assert screen["going_on"] == ["Nothing is queued in Heap."]

    def test_a_paused_screen_resumes(self, home):
        chainstep.pause(home, KEY, "lunch", NOW)
        screen = _view(home)["chain"]["projects"][KEY]
        assert screen["chips"] == [{"text": "Paused", "tone": "needs"}]
        assert screen["going_on"][0].startswith("The chain is paused. It was paused by hand.")
        assert screen["control"] == {"text": "Resume chain…", "row": "K|chain:resume",
                                     "primary": True}
        assert any("lunch" in line for line in screen["technical"]["lines"])

    def test_now_shows_the_running_start_and_its_stall_actions(self, home):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": "typed",
                 "nudged_epoch": NOW - 1800, "still": False, "still_said": False}
        queue = _entries(("IDEA_7", {"state": "started"}))
        screen = _view(home, queue, rows=[_stalled_row()],
                       ledger={KEY: {"current": _current(stall=stall)}})["chain"]["projects"][KEY]
        assert screen["chips"] == [{"text": "Running Q1", "tone": "working"}]
        now = screen["now"]
        assert now["title"] == "Q1 · IDEA_7"
        assert now["line"] == "Stalled at a usage limit · nudged 30 min ago"
        assert [a["text"] for a in now["actions"]] == ["Continue…", "Retry…", "Close…"]

    def test_now_without_a_stall(self, home):
        screen = _view(home, ledger={KEY: {"current": _current()}})["chain"]["projects"][KEY]
        assert screen["now"]["line"] == "Running in a window on the Mac"
        assert screen["now"]["actions"] == []
        launching = _view(home, ledger={KEY: {"current": _current(
            state="launching", session=None)}})["chain"]["projects"][KEY]
        assert launching["now"]["line"] == "Opening its window on the Mac"

    @pytest.mark.parametrize("stall, line", [
        ({"cause": "error", "nudge": None}, "Stalled on an error"),
        ({"cause": "unknown", "nudge": "no_tab"}, "Stalled · not nudged"),
        ({"cause": "limit", "nudge": None, "waiting_said": True},
         "Stalled at a usage limit · nudged once it resets"),
        ({"cause": "unknown", "nudge": "typed", "still_said": True},
         "Stalled · still stalled after one nudge"),
    ])
    def test_the_stall_lines(self, home, stall, line):
        stall = {"since_epoch": NOW - 3 * 3600, **stall}
        screen = _view(home, rows=[_stalled_row()], ledger={KEY: {
            "current": _current(stall=stall)}})["chain"]["projects"][KEY]
        assert screen["now"]["line"] == line

    def test_recent_lists_this_devices_chain_actions(self, home):
        job = {"id": "j1", "row": "K|chain", "kind": "run", "started": NOW - 60,
               "argv": ["invariant", "queue", "remove", "Q2", "--if-queued-at", "2"],
               "state": "done", "ok": True, "elapsed": 1.0}
        other = {**job, "id": "j2", "row": "K|s1"}
        screen = _view(home, jobs=[job, other])["chain"]["projects"][KEY]
        assert screen["recent"] == [{"text": "Remove from queue: Done in 1 s", "at": NOW - 60}]

    def test_an_unreadable_queue_is_said_and_offers_nothing(self, home):
        def broken(root):
            raise queue_record.StateError("bad queue")
        document = _doc()
        reader = _reader(home)
        reader.queues = broken
        view = panel.view(document, acting=True, starting=True, chains=reader.read(document))
        screen = view["chain"]["projects"][KEY]
        assert screen["chips"] == [{"text": "Cannot be read", "tone": "problem"}]
        assert screen["control"] is None
        assert "Queue error: bad queue" in screen["technical"]["lines"]
        assert view["projects"][0]["chain"] == "Chain: cannot be read"

    def test_failed_entries_are_under_technical_details(self, home):
        queue = _entries(("a", {"state": "failed", "reason": "the launch failed"}))
        screen = _view(home, queue)["chain"]["projects"][KEY]
        assert "Q1 a · failed: the launch failed" in screen["technical"]["lines"]


class TestTheConfirmWords:
    def _words(self, home, key, queue=None, rows=(), ledger=None, reading=None):
        derived = _derive(home, key, queue, rows, ledger, reading)
        return panel.chain_confirm_words(derived.action, derived.name, derived.facts,
                                         derived.argv, derived.cwd, now=NOW)

    def test_a_move_says_what_it_moves_past(self, home):
        words = self._words(home, "K|chain:move_up:Q2", _entries(("a", {}), ("b", {})))
        assert words["title"] == "Move Q2 up?"
        assert words["sentence"] == ("Puts Q2, b, before Q1, a, in Heap's queue. Nothing "
                                     "starts and nothing else moves.")
        assert words["button"] == "Move up" and words["destructive"] is False
        assert words["command"] == "invariant queue move Q2 --to 1 --if-queued-at 2"
        assert words["place"] == "In Heap › main checkout" and words["cwd"] == "/r"
        assert words["env"] == [] and words["launch"] is None

    def test_down_and_remove(self, home):
        queue = _entries(("a", {}), ("b", {}))
        assert self._words(home, "K|chain:move_down:Q1", queue)["button"] == "Move down"
        words = self._words(home, "K|chain:remove:Q2", queue)
        assert words["title"] == "Take Q2 off the queue?" and words["button"] == "Remove from queue"

    def test_start_pause_resume(self, home):
        words = self._words(home, "K|chain:start:Q1", _entries(("a", {"mode": "propose"})))
        assert words["title"] == "Start Q1 now?" and words["button"] == "Start session"
        assert self._words(home, "K|chain:pause")["button"] == "Pause chain"
        chainstep.pause(home, KEY, "x", NOW)
        assert self._words(home, "K|chain:resume")["title"] == "Resume the chain in Heap?"

    def test_a_backlog_start_names_its_item(self, home):
        words = self._words(home, "K|chain:start_item:IDEA_9",
                            ledger={KEY: {"proposed": {"backlog:IDEA_9": {"since": "t"}}}})
        assert words["title"] == "Start IDEA_9 now?"
        assert "Title of IDEA_9" in words["sentence"]

    def test_the_greenlight_is_for_every_project(self, home):
        _ledger(home, {}, usage={"held": {"kind": "unknown"}, "greenlight": None})
        words = self._words(home, "oracle|chain:greenlight", reading=_usage(read_at=NOW - 7200))
        assert words["title"] == "Let the chain go ahead?" and words["button"] == "Go ahead"
        assert words["place"] == "On the Mac, for every project"

    def test_continue_is_a_safe_sheet(self, home):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": "typed"}
        words = self._words(home, "K|chain:continue", rows=[_stalled_row()],
                            ledger={KEY: {"current": _current(stall=stall)}})
        assert words["destructive"] is False and words["button"] == "Type continue"

    @pytest.mark.parametrize("action, button", [("retry", "Close and retry"),
                                                ("close", "Close and mark failed")])
    def test_retry_and_close_use_the_destructive_sheet(self, home, action, button):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": "typed"}
        words = self._words(home, f"K|chain:{action}", rows=[_stalled_row()],
                            ledger={KEY: {"current": _current(stall=stall)}})
        assert words["destructive"] is True and words["button"] == button
        assert words["deletes_heading"] == "This ends"
        assert words["deletes"] == ["Claude in Heap · window 27885f4e",
                                    "Process 4100 · idle 3 h"]
        assert words["checked"] == ["It is still the chain's current start, and still stalled",
                                    f"Checked again the moment you press {button}"]

    def test_a_window_already_gone_says_so(self, home):
        stall = {"since_epoch": NOW, "cause": "error", "nudge": "no_tab"}
        words = self._words(home, "K|chain:close", ledger={KEY: {"current": _current(stall=stall)}})
        assert words["deletes"] == ["Its window has already closed; nothing is ended"]


class TestTheResultWords:
    @pytest.mark.parametrize("argv, deed", [
        (["invariant", "queue", "move", "Q2", "--to", "1", "--if-queued-at", "2"], "Move up"),
        (["invariant", "queue", "move", "Q1", "--to", "2", "--if-queued-at", "1"], "Move down"),
        (["invariant", "queue", "remove", "Q1", "--if-queued-at", "1"], "Remove from queue"),
        (["invariant", "oracle", "chain", "start", "K", "--entry", "Q1"], "Start session"),
        (["invariant", "oracle", "chain", "greenlight"], "Go ahead"),
        (["invariant", "oracle", "chain", "close", "K"], "Close and mark failed"),
    ])
    def test_each_chain_deed_is_named(self, argv, deed):
        job = {"argv": argv, "kind": "run", "state": "done", "ok": True, "elapsed": 1.0}
        assert panel.result_words(job)["line"] == deed

    def test_a_queue_move_without_its_guard_is_not_a_known_step(self):
        assert panel.step_words(["invariant", "queue", "move", "Q1", "--to", "2"]) is None

    def test_a_chain_retry_says_what_happened(self):
        line = "CHAIN  Heap  retried: Claude ended, and its Terminal tab closed."
        assert panel.outcome_words(["invariant", "oracle", "chain", "retry", "K"], True,
                                   line) == line


# -- the server: confirm and act (ruling 14, D32) -----------------------------------------


class ChainWorld:
    """A fresh read, a chain reader over a tmp ledger, a recording runner."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "proj"
        self.root.mkdir()
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.clock = Clock(NOW)
        self.runner = RecordingRunner(status=0, output="moved Q2 to 1\n")
        self.actions = Actions(runner=self.runner, environ=lambda: {"PATH": "/bin"},
                               strip=lambda env: env)
        self.queue = _entries(("a", {}), ("b", {}))
        self.document = _doc(root=str(self.root))
        self.reader = chainview.ChainReader(
            self.home, usage=lambda: _usage(), clock=self.clock,
            queues=lambda root: self.queue, titles=lambda root, ref: None)
        self.tokens = iter(f"tok{n:04d}" for n in range(10_000))
        self.spawned: list = []
        self.remote = RemoteActions(
            self.actions, lambda: json.loads(json.dumps(self.document)),
            AuditLog(self.home, clock=self.clock), clock=self.clock, timer=self.clock,
            spawn=lambda work: self.spawned.append(work) or work(),
            token=lambda: next(self.tokens), chains=self.reader)

    def audit(self) -> list[dict]:
        path = self.home / AUDIT_FILE
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.fixture
def chained(tmp_path) -> ChainWorld:
    return ChainWorld(tmp_path)


class TestTheChainActions:
    def test_the_confirm_answers_the_sheet_and_the_act_runs_the_derived_argv(self, chained):
        shown = chained.remote.confirm(PHONE, "K|chain:move_up:Q2")
        assert shown.status == 200, shown.body
        body = shown.body
        assert body["kind"] == "run" and body["subject"] == "Heap"
        assert body["words"]["title"] == "Move Q2 up?"
        assert body["lines"][0] == "run   invariant queue move Q2 --to 1 --if-queued-at 2"
        answer = chained.remote.act(PHONE, "K|chain:move_up:Q2", body["nonce"])
        assert answer.status == 202, answer.body
        argv, cwd, _ = chained.runner.calls[0]
        assert argv == ("invariant", "queue", "move", "Q2", "--to", "1", "--if-queued-at", "2")
        assert cwd == str(chained.root)
        job = answer.body["job"]
        assert job["row"] == "K|chain" and job["words"]["line"] == "Move up"
        assert [e["event"] for e in chained.audit()] == ["start", "done"]
        assert chained.audit()[0]["row"] == "K|chain"

    def test_a_queue_that_changed_since_the_confirm_is_a_409(self, chained):
        chained.queue = _entries(("a", {}), ("b", {}), ("c", {}))
        nonce = chained.remote.confirm(PHONE, "K|chain:move_up:Q3").body["nonce"]
        chained.queue.move("Q3", 2)
        answer = chained.remote.act(PHONE, "K|chain:move_up:Q3", nonce)
        assert answer.status == 409 and "changed" in answer.body["error"]
        assert chained.runner.calls == []

    def test_an_action_no_longer_offered_is_a_409(self, chained):
        nonce = chained.remote.confirm(PHONE, "K|chain:remove:Q2").body["nonce"]
        chained.queue.mark("Q2", "started")
        answer = chained.remote.act(PHONE, "K|chain:remove:Q2", nonce)
        assert answer.status == 409 and "not queued" in answer.body["error"]

    def test_a_refusal_at_confirm_offers_no_deed(self, chained):
        body = chained.remote.confirm(PHONE, "K|chain:resume").body
        assert body["nonce"] is None and "not paused" in body["refused"]

    def test_the_nonce_is_the_devices_and_the_actions(self, chained):
        nonce = chained.remote.confirm(PHONE, "K|chain:remove:Q2").body["nonce"]
        assert chained.remote.act(TABLET, "K|chain:remove:Q2", nonce).status == 403
        nonce = chained.remote.confirm(PHONE, "K|chain:remove:Q2").body["nonce"]
        assert chained.remote.act(PHONE, "K|chain:remove:Q1", nonce).status == 403
        assert chained.runner.calls == []

    def test_one_chain_action_per_project_at_a_time(self, chained):
        chained.remote.spawn = chained.spawned.append
        first = chained.remote.confirm(PHONE, "K|chain:remove:Q2").body["nonce"]
        second = chained.remote.confirm(PHONE, "K|chain:pause").body["nonce"]
        assert chained.remote.act(PHONE, "K|chain:remove:Q2", first).status == 202
        answer = chained.remote.act(PHONE, "K|chain:pause", second)
        assert answer.status == 409 and "already running" in answer.body["error"]

    def test_a_server_without_the_reader_has_no_chain_actions(self, chained):
        chained.remote.chains = None
        assert chained.remote.confirm(PHONE, "K|chain:pause").status == 404
        assert chained.remote.act(PHONE, "K|chain:pause", "x").status == 403

    def test_a_project_gone_from_the_read_is_refused(self, chained):
        nonce = chained.remote.confirm(PHONE, "K|chain:pause").body["nonce"]
        chained.document = {"now": NOW, "projects": []}
        answer = chained.remote.act(PHONE, "K|chain:pause", nonce)
        assert answer.status == 409 and "not registered" in answer.body["error"]

    def test_a_failed_read_is_a_503(self, chained):
        chained.remote.fresh = lambda: (_ for _ in ()).throw(OSError("disk"))
        assert chained.remote.confirm(PHONE, "K|chain:pause").status == 503
        assert chained.remote.act(PHONE, "K|chain:pause", "n").status == 403

    def test_a_retry_runs_on_the_destructive_sheet(self, chained):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": "typed"}
        _ledger(chained.home, {KEY: {"current": _current(stall=stall)}})
        chained.document = _doc([_stalled_row()], root=str(chained.root))
        body = chained.remote.confirm(PHONE, "K|chain:retry").body
        assert body["words"]["destructive"] is True
        assert chained.remote.act(PHONE, "K|chain:retry", body["nonce"]).status == 202
        assert chained.runner.calls[0][0][-2:] == ("retry", KEY)

    def test_an_audit_that_cannot_be_written_runs_nothing(self, chained, monkeypatch):
        def refuse(entry):
            raise webactions.StateError("read-only")
        monkeypatch.setattr(chained.remote.audit, "record", refuse)
        nonce = chained.remote.confirm(PHONE, "K|chain:pause").body["nonce"]
        assert chained.remote.act(PHONE, "K|chain:pause", nonce).status == 503
        assert chained.runner.calls == []

    def test_chains_reads_the_document_it_is_given(self, chained):
        assert set(chained.remote.read_chains(chained.document)["projects"]) == {KEY}
        chained.remote.chains = None
        assert chained.remote.read_chains(chained.document) is None


def _store(tmp_path: Path) -> DeviceStore:
    return DeviceStore(tmp_path / "devhome")


def _get(app, path, token):
    headers = {"host": f"{TS4}:7341", "cookie": f"{COOKIE_NAME}={token}"}
    return app.handle(Request("GET", path, headers, b""))


def _post(app, token, body):
    headers = {"host": f"{TS4}:7341", "content-type": "application/json", "origin": ORIGIN,
               "cookie": f"{COOKIE_NAME}={token}"}
    return app.handle(Request("POST", "/api/act", headers, json.dumps(body).encode()))


class TestOverHttp:
    def test_the_state_carries_the_chain_and_the_page_acts_on_it(self, chained, tmp_path):
        store = _store(tmp_path)
        app = WebApp(store, lambda: _loaded(chained.document), [TS4], clock=Clock(),
                     nonce=lambda: "N0NCE", remote=chained.remote)
        _, token = store.redeem(store.issue("phone", ttl=300).code)
        state = json.loads(_get(app, "/api/state", token).body)
        assert state["view"]["projects"][0]["chain"] == "Chain: waiting · 2 queued"
        screen = state["view"]["chain"]["projects"][KEY]
        key = screen["queue"][1]["up"]["row"]
        shown = json.loads(_get(app, "/api/confirm?row=" + urllib.parse.quote(key), token).body)
        response = _post(app, token, {"row": key, "nonce": shown["nonce"]})
        assert response.status == 202, response.body
        assert chained.runner.calls[0][0][2:4] == ("move", "Q2")

    def test_a_read_only_page_draws_no_chain(self, chained, tmp_path):
        store = _store(tmp_path)
        app = WebApp(store, lambda: _loaded(chained.document), [TS4], clock=Clock(),
                     nonce=lambda: "N0NCE", remote=None)
        _, token = store.redeem(store.issue("phone", ttl=300).code)
        state = json.loads(_get(app, "/api/state", token).body)
        assert state["view"]["chain"] is None and state["view"]["projects"][0]["chain"] is None


class TestTheCommandWiresTheReader:
    def test_oracle_web_hands_the_page_a_chain_reader(self, capsys, oracle_web, project):
        oracle_web.addresses = [TS4]
        assert main(["oracle", "web"]) == 0
        capsys.readouterr()
        remote = oracle_web.servers[0].app.remote
        assert isinstance(remote.chains, chainview.ChainReader)
        assert remote.chains.cap == 95


# -- the page: the chain screen inside the one page (ruling 15) ----------------------------

needs_js = pytest.mark.skipif(ENGINE is None, reason="no JavaScript engine runs here")


def _page_state(home, queue=None, rows=(), ledger=None, acting=True, reading=None,
                usage_ledger=None):
    view = _view(home, queue, rows, ledger, reading=reading, acting=acting,
                 usage_ledger=usage_ledger)
    return {"document": {}, "view": view,
            "freshness": {"age": 1.0, "interval": 5, "stale_after": 15, "banner": ""},
            "jobs": []}


def _chain_confirm(home_unused=None):
    words = {"title": "Move Q2 up?", "sentence": "Puts Q2 before Q1.", "button": "Move up",
             "command": "invariant queue move Q2 --to 1 --if-queued-at 2",
             "place": "In Heap › main checkout", "cwd": "/r", "env": [], "launch": None,
             "destructive": False, "deletes_heading": "This deletes", "deletes": [],
             "checked": []}
    return {"row": "K|chain:move_up:Q2", "nonce": "C1", "refused": None, "kind": "run",
            "lines": [], "subject": "Heap", "expires_in": 120, "words": words}


@needs_js
class TestThePage:
    def test_the_projects_row_opens_the_chain_screen(self, home):
        state = _page_state(home, _entries(("a", {}), ("b", {"brief": "go"})))
        result = _run({"/api/state": [200, state]}, r"""
  const row = withClass(screen(), "chain-open")[0];
  const line = texts(row, "chain-line");
  row.fire("click");
  say({line: line, title: texts(screen(), "detail-title"), pushed: history.state,
       kicker: texts(screen(), "kicker"), rows: texts(screen(), "row-title"),
       briefs: texts(screen(), "row-brief"), pills: texts(screen(), "pill"),
       ups: withClass(screen(), "entry-up").map((n) => n.attrs["aria-label"]),
       downs: withClass(screen(), "entry-down").map((n) => n.attrs["aria-label"]),
       control: texts(screen(), "chain-control"), top: element("top").className});""")
        assert result["line"] == ["Chain: waiting · 2 queued"]
        assert result["title"] == ["Heap"] and result["kicker"] == ["Chain"]
        assert result["pushed"] == {"screen": "chain", "key": KEY}
        assert result["rows"] == ["Q1 · a", "Q2 · b"]
        assert result["briefs"] == ["go"]
        assert result["pills"] == ["Remove…", "Remove…"]
        assert result["ups"] == ["Move Q2 up…"] and result["downs"] == ["Move Q1 down…"]
        assert result["control"] == ["Pause chain…"]
        assert result["top"] == "sub"

    def test_move_up_opens_the_confirm_and_posts_the_chain_key(self, home):
        state = _page_state(home, _entries(("a", {}), ("b", {})))
        result = _run({"/api/state": [200, state], "/api/confirm": [200, _chain_confirm()],
                       "/api/act": [202, {"job": {
                           "id": "j1", "row": "K|chain", "state": "done", "ok": True,
                           "argv": [], "output": "", "remote_control": None,
                           "audit_error": "",
                           "words": {"headline": "Done in 1 s", "line": "Move up",
                                     "tone": "ok", "why": "", "outcome": ""}}}]}, r"""
  withClass(screen(), "chain-open")[0].fire("click");
  withClass(screen(), "entry-up")[0].fire("click");
  await settle();
  const shown = {title: texts(sheet(), "sheet-title"), fetched: fetched.slice(-1)[0]};
  withClass(sheet(), "primary")[0].fire("click");
  await settle();
  say({shown: shown, posted: posted, result: texts(sheet(), "result-title"),
       still: texts(screen(), "detail-title")});""")
        assert result["shown"]["title"] == ["Move Q2 up?"]
        assert result["shown"]["fetched"] == "/api/confirm?row=K%7Cchain%3Amove_up%3AQ2"
        assert result["posted"] == [{"row": "K|chain:move_up:Q2", "nonce": "C1"}]
        assert result["result"] == ["Done in 1 s"]
        assert result["still"] == ["Heap"]

    def test_a_stall_card_has_its_three_actions_and_details_opens_the_chain(self, home):
        stall = {"since_epoch": NOW - 3 * 3600, "cause": "limit", "nudge": "typed",
                 "still": True, "still_said": True}
        state = _page_state(home, rows=[_stalled_row()],
                            ledger={KEY: {"current": _current(stall=stall)}})
        result = _run({"/api/state": [200, state]}, r"""
  const card = withClass(screen(), "chain-card")[0];
  const buttons = withClass(card, "btn").map((n) => n.textContent);
  byText(card, "Details").fire("click");
  say({buttons: buttons, screen: history.state,
       now: texts(screen(), "now-title"), actions: withClass(screen(), "now-actions")
         .map((n) => n.textContent)});""")
        assert result["buttons"] == ["Continue…", "Retry…", "Close…", "Details"]
        assert result["screen"] == {"screen": "chain", "key": KEY}
        assert result["now"] == ["Q1 · IDEA_7"]
        assert result["actions"] == ["Continue…Retry…Close…"]

    def test_the_usage_card_opens_its_detail(self, home):
        state = _page_state(home, _entries(("a", {})), reading=_usage(read_at=NOW - 7200),
                            usage_ledger={"held": {"kind": "unknown"}, "greenlight": None})
        result = _run({"/api/state": [200, state]}, r"""
  const card = withClass(screen(), "chain-card")[0];
  const primary = texts(card, "primary");
  byText(card, "Details").fire("click");
  say({primary: primary, title: texts(screen(), "detail-title")});""")
        assert result["primary"] == ["Go ahead…"]
        assert result["title"] == ["The weekly usage cannot be read"]

    def test_a_chain_screen_whose_project_went_falls_back(self, home):
        state = _page_state(home)
        result = _run({"/api/state": [200, state]}, r"""
  withClass(screen(), "chain-open")[0].fire("click");
  const opened = texts(screen(), "detail-title");
  answers["/api/state"][1].view.chain.projects = {};
  await refresh();
  say({opened: opened, after: texts(screen(), "detail-title"),
       strip: withClass(screen(), "tile").length});""")
        assert result["opened"] == ["Heap"]
        assert result["after"] == [] and result["strip"] == 4

    def test_a_read_only_page_draws_the_chain_without_a_button(self, home):
        state = _page_state(home, _entries(("a", {}), ("b", {})), acting=False)
        result = _run({"/api/state": [200, state]}, r"""
  withClass(screen(), "chain-open")[0].fire("click");
  say({title: texts(screen(), "detail-title"), pills: withClass(screen(), "pill").length,
       ups: withClass(screen(), "entry-up").length,
       control: withClass(screen(), "chain-control").length,
       newSession: withClass(screen(), "row-pill").length});""")
        assert result == {"title": ["Heap"], "pills": 0, "ups": 0, "control": 0,
                          "newSession": 0}
