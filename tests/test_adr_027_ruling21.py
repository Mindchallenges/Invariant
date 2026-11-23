"""ADR-027 ruling 21 (IDEA_062): a finished chained window is closed once its session consents.

When a chained session lands (D1's teardown with its plan complete), its
window still holds the project under one at a time. The chain step follows
that window in the ledger's ``landed``: once it has been quiet, the oracle
types one line into the session's own tab asking whether it may close it;
the session answers with ``invariant oracle chain consent``, an engine record,
never transcript text. On consent the window is closed through close-host's
path and the chain goes on; on "not yet" it is asked again later, three asks
in all; on no answer, or when it cannot be asked, one notice offers Close and
Leave open. A window the user typed in since the teardown is the user's: never
asked, never closed, one notice.

No test types into a terminal or closes a window: the step's hands are
recorders, and the CLI's typer, closer and window finder are replaced.
"""

from __future__ import annotations

import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from invariant.cli import main as cli_main
from invariant.cli.main import main
from invariant.core.errors import InvariantError, ManifestError
from invariant.core.manifest import Manifest
from invariant.core.queue import Queue
from invariant.core.store import JsonStore
from invariant.oracle import chain, chainstep, chainview, panel
from invariant.oracle.attention import AttentionStore
from invariant.oracle.chain import ChainSettings
from invariant.oracle.chainstep import ChainHands, ChainStep
from invariant.oracle.index import HomeStore
from invariant.oracle.notify import RecordingNotifier
from invariant.oracle.scan import ScanCache
from invariant.oracle.usage import PlanUsage

NOON = time.mktime((2026, 11, 20, 12, 0, 0, 0, 0, -1))
MIN = 60.0
KEY = "/state/demo"
NAME = "demo-IDEA_1"
#: When the session's record says it was torn down.
LANDED = NOON - 30


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


# -- the step's world ---------------------------------------------------------------------


class Hands:
    def __init__(self):
        self.calls: list[tuple] = []
        self.nudge_code = ("typed", "")
        self.close_error: Exception | None = None
        #: Run in place of the recorded answer, to meddle while it types or closes.
        self.on_nudge = None
        self.on_close = None

    def start(self, key, label, prompt):
        self.calls.append(("start", key, label))
        return {"name": f"demo-{label}", "pid": 4242}

    def begin(self, key, entry, ref):
        self.calls.append(("begin", key, entry, ref))
        return entry or "Q50"

    def mark(self, key, entry, state, session=None, reason=None):
        self.calls.append(("mark", key, entry, state, session, reason))

    def fail(self, key, entry, reason, template, retry):
        self.calls.append(("fail", key, entry, reason))
        return {"failed": entry, "triage": "Q90", "retry": None}

    def nudge(self, tty, text):
        self.calls.append(("nudge", tty, text))
        if self.on_nudge is not None:
            return self.on_nudge(tty, text)
        return self.nudge_code

    def close(self, host):
        self.calls.append(("close", host.get("pid")))
        if self.on_close is not None:
            return self.on_close(host)
        if self.close_error is not None:
            raise self.close_error
        return ["Claude ended, and its Terminal tab closed."]

    def kinds(self):
        return [c[0] for c in self.calls]

    def as_hands(self, closer=True, typer=True):
        return ChainHands(start=self.start, begin=self.begin, mark=self.mark, fail=self.fail,
                          nudge=self.nudge if typer else None,
                          close=self.close if closer else None)


class Inputs:
    def __init__(self, queue=None, settings=None):
        self.queue = queue if queue is not None else Queue()
        self.ready = []
        self.settings = settings or ChainSettings()


def _queue(*refs):
    q = Queue()
    for ref in refs:
        q.add(ref, added_by="user")
    return q


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


def _step(home, hands, settings=None, notifier=None, closer=True, typer=True, landed_at=LANDED):
    holder = {"inputs": Inputs(_queue("IDEA_1"), settings), "end": "live"}
    step = ChainStep(home, hands.as_hands(closer=closer, typer=typer),
                     notifier if notifier is not None else RecordingNotifier(),
                     reader=lambda root: holder["inputs"],
                     session_end=lambda root, sid: holder["end"],
                     landed_at=lambda root, sid: landed_at,
                     pid_alive=lambda pid: True, log=lambda message: None)
    return step, holder


def _doc(rows=()):
    return {"projects": [{"project": {"key": KEY, "name": "demo", "root": "/work/demo"},
                          "rows": list(rows)}]}


def _session_row(now):
    host = {"host_session_id": "h1", "current": True, "launched_name": NAME, "pid": 4242,
            "alive": True, "tty": "ttys004", "process_start": "x"}
    return {"kind": "session", "session_id": "s9", "name": "work", "state": "working",
            "hosts": [host], "last_activity": now, "signals": []}


def _window(now, quiet=10 * MIN, prompts=4, prompted=LANDED - 600, turn_state="idle",
            alive=True, tty="ttys004", attention=True, kind="host"):
    record = ({"turn": "ended", "prompts": prompts,
               "prompted": _iso(prompted) if prompted is not None else None}
              if attention else None)
    host = {"host_session_id": "h1", "current": True, "launched_name": NAME, "pid": 4242,
            "alive": alive, "tty": tty, "process_start": "x", "attention": record}
    row = {"kind": kind, "hosts": [host], "last_activity": now - quiet,
           "turn_state": turn_state, "signals": []}
    if kind == "session":
        row.update(session_id="s10", name="other", state="working")
    return row


def _ledger(home):
    return json.loads((home / "oracle" / "chain.json").read_text())


def _state(home):
    return _ledger(home)["projects"][KEY]


def _landed(home, hands, **kw):
    """A chained start that landed at ``NOON``: started, bound, then torn down."""
    step, holder = _step(home, hands, **kw)
    step.step(_doc(), NOON - 3600)
    holder["inputs"] = Inputs(Queue(), kw.get("settings"))
    step.step(_doc([_session_row(NOON - 3500)]), NOON - 3500)
    holder["end"] = "done"
    actions = step.step(_doc([_window(NOON, quiet=0)]), NOON)
    assert "done" in [a["action"] for a in actions]
    return step, holder


def _ask_line():
    return chain.ask_line("demo")


def _asked(home, hands, notifier=None, **kw):
    step, holder = _landed(home, hands, notifier=notifier, **kw)
    actions = step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN)]), NOON + 6 * MIN)
    assert [a["action"] for a in actions] == ["asked"]
    return step, holder


# -- the settings ---------------------------------------------------------------------------


class TestTheSettings:
    def test_the_defaults(self, tmp_path):
        m = Manifest.from_raw({}, root=tmp_path, path=tmp_path / "m.yaml", env={})
        assert m.get("oracle.chain.close_ask_after") == 5
        assert m.get("oracle.chain.close_ask_again") == 30
        assert m.get("oracle.chain.close_asks") == 3
        assert m.get("oracle.chain.close_answer_within") == 10
        settings = ChainSettings.from_manifest(m)
        assert (settings.close_ask_after, settings.close_ask_again, settings.close_asks,
                settings.close_answer_within) == (5, 30, 3, 10)

    def test_set(self, tmp_path):
        m = Manifest.from_raw({"oracle": {"chain": {
            "close_ask_after": 0.5, "close_ask_again": 60, "close_asks": 1,
            "close_answer_within": 2}}}, root=tmp_path, path=tmp_path / "m.yaml", env={})
        settings = ChainSettings.from_manifest(m)
        assert (settings.close_ask_after, settings.close_ask_again, settings.close_asks,
                settings.close_answer_within) == (0.5, 60, 1, 2)

    @pytest.mark.parametrize("key, bad", [
        ("close_ask_after", 0), ("close_ask_after", True), ("close_ask_again", -1),
        ("close_answer_within", "10m"), ("close_asks", 0), ("close_asks", 1.5),
        ("close_asks", True)])
    def test_refused_at_load(self, tmp_path, key, bad):
        with pytest.raises(ManifestError, match=key):
            Manifest.from_raw({"oracle": {"chain": {key: bad}}}, root=tmp_path,
                              path=tmp_path / "m.yaml", env={})


class TestTheAskLine:
    def test_it_is_the_rulings_line(self):
        assert chain.ask_line("Heap") == (
            "invariant oracle: this session has landed and the chain wants its window back. "
            "If you have nothing more to do, run: invariant oracle chain consent Heap --close"
            "   Otherwise run: invariant oracle chain consent Heap --not-yet --note "
            "\"<why, one line>\"   Answer only with that command.")


# -- the person's prompts on the attention record (D54) -----------------------------------


class TestThePromptCount:
    def test_a_prompt_counts_and_stamps(self, tmp_path):
        store = AttentionStore(JsonStore(tmp_path), clock=lambda: "2026-11-20T12:00:00+00:00")
        store.prompt("h1")
        store.prompt("h1")
        record = store.read("h1")
        assert record.prompts == 2 and record.prompted == "2026-11-20T12:00:00+00:00"

    def test_a_task_notification_is_not_the_person(self, tmp_path):
        store = AttentionStore(JsonStore(tmp_path), clock=lambda: "2026-11-20T12:00:00+00:00")
        store.background_done("h1", ["t1"])
        record = store.read("h1")
        assert record.prompts is None and record.prompted is None

    def test_an_older_record_counts_from_nothing(self, tmp_path):
        (tmp_path / "attention").mkdir()
        (tmp_path / "attention" / "h1.json").write_text(json.dumps(
            {"host_session_id": "h1", "turn": "ended"}))
        store = AttentionStore(JsonStore(tmp_path), clock=lambda: "t")
        store.prompt("h1")
        assert store.read("h1").prompts == 1


class TestSessionLanded:
    def test_the_heartbeat_of_a_torn_down_session(self, project):
        root = project
        assert chain.session_landed(str(root), "nope") is None
        assert main(["session", "start", "work"]) == 0
        domain = cli_main.Context().sessions
        session = domain.list()[0]
        session.heartbeat = 1234.5
        domain._save(session)
        assert chain.session_landed(str(root), session.id) == 1234.5
        session.heartbeat = None
        domain._save(session)
        assert chain.session_landed(str(root), session.id) is None


# -- the landed window: following and asking ----------------------------------------------


class TestTheLanding:
    def test_a_landed_start_is_followed_with_the_settings_of_the_day(self, home):
        settings = ChainSettings(close_ask_after=2, close_ask_again=20, close_asks=2,
                                 close_answer_within=4)
        _landed(home, Hands(), settings=settings)
        landed = _state(home)["landed"]
        assert (landed["entry"], landed["ref"], landed["session"], landed["name"]) == (
            "Q1", "IDEA_1", "s9", NAME)
        assert landed["state"] == "watching" and landed["landed_epoch"] == LANDED
        assert landed["limits"] == {"ask_after": 120.0, "ask_again": 1200.0, "asks": 2,
                                    "answer_within": 240.0}
        assert landed["asks"] == [] and landed["answers"] == []

    def test_no_teardown_time_reads_as_the_tick(self, home):
        _landed(home, Hands(), landed_at=None)
        assert _state(home)["landed"]["landed_epoch"] == NOON

    def test_hands_without_a_closer_follow_no_window(self, home):
        _landed(home, Hands(), closer=False)
        assert _state(home).get("landed") is None

    def test_the_window_holds_the_project_while_it_is_open(self, home):
        hands = Hands()
        step, holder = _landed(home, hands)
        holder["inputs"] = Inputs(_queue("IDEA_2"))
        step.step(_doc([_window(NOON + 60, quiet=60)]), NOON + 60)
        assert hands.kinds().count("start") == 1


class TestAsking:
    def test_a_quiet_window_is_asked_once_with_the_line(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, _ = _landed(home, hands, notifier=notifier)
        assert step.step(_doc([_window(NOON + 4 * MIN, quiet=4 * MIN)]), NOON + 4 * MIN) == []
        actions = step.step(_doc([_window(NOON + 5 * MIN, quiet=5 * MIN)]), NOON + 5 * MIN)
        assert [a["action"] for a in actions] == ["asked"]
        assert ("nudge", "ttys004", _ask_line()) in hands.calls
        landed = _state(home)["landed"]
        assert landed["state"] == "asked" and landed["baseline"] == 4
        assert [a["code"] for a in landed["asks"]] == ["typed"]
        # an ask is in the ledger, not a notification
        assert not [n for n in notifier.sent if "asked" in n.subtitle]
        assert step.step(_doc([_window(NOON + 6 * MIN, quiet=MIN, prompts=5)]),
                         NOON + 6 * MIN) == []
        assert hands.kinds().count("nudge") == 1

    def test_a_busy_window_is_not_asked(self, home):
        hands = Hands()
        step, _ = _landed(home, hands)
        step.step(_doc([_window(NOON + 20 * MIN, quiet=20 * MIN, turn_state="busy")]),
                  NOON + 20 * MIN)
        assert "nudge" not in hands.kinds()

    def test_a_window_with_no_activity_known_cannot_be_judged(self, home):
        hands = Hands()
        step, _ = _landed(home, hands)
        row = _window(NOON + 20 * MIN)
        row["last_activity"] = None
        actions = step.step(_doc([row]), NOON + 20 * MIN)
        assert [a["action"] for a in actions] == ["window open"]
        assert _state(home)["landed"]["notice"]["why"] == "unread"
        assert "nudge" not in hands.kinds()

    def test_no_tab_is_one_notice_and_no_ask(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, _ = _landed(home, hands, notifier=notifier)
        actions = step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN, tty=None)]),
                            NOON + 6 * MIN)
        assert [a["action"] for a in actions] == ["window open"]
        assert "close-window demo" in actions[0]["detail"]
        landed = _state(home)["landed"]
        assert (landed["state"], landed["notice"]["why"]) == ("notified", "not_typed")
        assert notifier.sent[-1].subtitle == "chain window open"
        step.step(_doc([_window(NOON + 60 * MIN, quiet=60 * MIN)]), NOON + 60 * MIN)
        assert "nudge" not in hands.kinds()

    def test_hands_without_a_typer_cannot_ask(self, home):
        hands = Hands()
        step, _ = _landed(home, hands, typer=False)
        step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN)]), NOON + 6 * MIN)
        assert _state(home)["landed"]["notice"]["why"] == "not_typed"

    def test_typing_that_fails_is_one_notice(self, home):
        hands = Hands()
        hands.nudge_code = ("no_claude", "only a shell runs there")
        step, _ = _landed(home, hands)
        actions = step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN)]), NOON + 6 * MIN)
        assert [a["action"] for a in actions] == ["window open"]
        landed = _state(home)["landed"]
        assert landed["notice"]["why"] == "not_typed"
        assert landed["asks"][0]["code"] == "no_claude"
        assert "only a shell" in landed["notice"]["detail"]

    def test_a_typer_that_raises_is_a_failed_ask(self, home):
        hands = Hands()

        def boom(tty, text):
            raise RuntimeError("osascript went away")

        hands.on_nudge = boom
        step, _ = _landed(home, hands)
        step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN)]), NOON + 6 * MIN)
        assert _state(home)["landed"]["asks"][0]["code"] == "failed"

    def test_an_ask_left_sending_by_a_dead_watcher_is_a_notice(self, home):
        hands = Hands()
        step, _ = _landed(home, hands)
        store = HomeStore(home)
        ledger = store.read(chainstep.LEDGER_FILE)
        landed = ledger["projects"][KEY]["landed"]
        landed["state"] = "asking"
        landed["baseline"] = 4
        landed["asks"] = [{"at": _iso(NOON), "at_epoch": NOON, "code": "sending"}]
        store.write(chainstep.LEDGER_FILE, ledger)
        step.step(_doc([_window(NOON + 60, quiet=60)]), NOON + 60)
        assert _state(home)["landed"]["state"] == "asking"
        step.step(_doc([_window(NOON + 200, quiet=200)]), NOON + 200)
        assert _state(home)["landed"]["notice"]["why"] == "not_typed"

    def test_a_window_whose_state_moved_while_it_was_typed_is_left(self, home):
        hands = Hands()
        step, _ = _landed(home, hands)

        def meddle(tty, text):
            store = HomeStore(home)
            ledger = store.read(chainstep.LEDGER_FILE)
            ledger["projects"][KEY]["landed"] = None
            store.write(chainstep.LEDGER_FILE, ledger)
            return ("typed", "")

        hands.on_nudge = meddle
        assert step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN)]), NOON + 6 * MIN) == []
        assert _state(home)["landed"] is None


class TestTheEdges:
    def test_a_teardown_time_that_cannot_be_read_reads_as_the_tick(self, home):
        def boom(root, sid):
            raise InvariantError("no manifest")

        hands = Hands()
        step, holder = _step(home, hands)
        step.landed_at = boom
        step.step(_doc(), NOON - 3600)
        holder["inputs"] = Inputs(Queue())
        step.step(_doc([_session_row(NOON - 3500)]), NOON - 3500)
        holder["end"] = "done"
        step.step(_doc([_window(NOON, quiet=0)]), NOON)
        assert _state(home)["landed"]["landed_epoch"] == NOON

    def test_rows_of_other_kinds_are_not_the_window(self, home):
        step, _ = _landed(home, Hands())
        other = {"kind": "worktree", "hosts": [_window(NOON)["hosts"][0]]}
        step.step(_doc([other, _window(NOON + 10, quiet=10)]), NOON + 10)
        assert _state(home)["landed"]["gone_since_epoch"] is None

    def test_a_time_that_is_not_iso_is_no_turn(self, home):
        hands = Hands()
        step, _ = _landed(home, hands)
        row = _window(NOON + 6 * MIN, quiet=6 * MIN)
        row["hosts"][0]["attention"]["prompted"] = "yesterday"
        step.step(_doc([row]), NOON + 6 * MIN)
        assert _state(home)["landed"]["state"] == "asked"
        assert chainstep._epoch(12) is None

    def test_a_failed_ask_with_no_detail(self, home):
        hands = Hands()
        hands.nudge_code = ("no_tab", "")
        step, _ = _landed(home, hands)
        step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN)]), NOON + 6 * MIN)
        landed = _state(home)["landed"]
        assert landed["notice"]["detail"] == "no_tab" and "detail" not in landed["asks"][0]

    def test_a_close_in_flight_is_left_alone(self, home):
        hands = Hands()
        step, _ = _asked(home, hands)
        chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 7 * MIN)
        store = HomeStore(home)
        ledger = store.read(chainstep.LEDGER_FILE)
        ledger["projects"][KEY]["landed"].update(state="closing", closing_epoch=NOON + 7 * MIN)
        store.write(chainstep.LEDGER_FILE, ledger)
        assert step.step(_doc([_window(NOON + 7 * MIN + 30, quiet=30, prompts=5)]),
                         NOON + 7 * MIN + 30) == []
        assert "close" not in hands.kinds()


class TestTheUsersWindow:
    def test_a_turn_typed_after_the_teardown_makes_it_the_users(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, _ = _landed(home, hands, notifier=notifier)
        actions = step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN, prompts=5,
                                          prompted=LANDED + 10)]), NOON + 6 * MIN)
        assert [a["action"] for a in actions] == ["your window"]
        assert _state(home)["landed"]["state"] == "users"
        said = [n for n in notifier.sent if n.subtitle == "chain your window"]
        assert len(said) == 1 and "will not ask it or close it" in said[0].message
        later = NOON + 3 * 3600
        assert step.step(_doc([_window(later, quiet=3 * 3600, prompts=5,
                                       prompted=LANDED + 10)]), later) == []
        assert "nudge" not in hands.kinds() and "close" not in hands.kinds()

    def test_a_prompt_after_the_ask_beside_the_oracles_own_is_the_users(self, home):
        hands = Hands()
        step, _ = _asked(home, hands)
        # the ask itself is one prompt: five is the oracle's
        assert step.step(_doc([_window(NOON + 7 * MIN, quiet=MIN, prompts=5)]),
                         NOON + 7 * MIN) == []
        actions = step.step(_doc([_window(NOON + 8 * MIN, quiet=MIN, prompts=6)]),
                            NOON + 8 * MIN)
        assert [a["action"] for a in actions] == ["your window"]

    def test_a_live_session_in_the_window_makes_it_the_users(self, home):
        step, _ = _landed(home, Hands())
        actions = step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN, kind="session")]),
                            NOON + 6 * MIN)
        assert [a["action"] for a in actions] == ["your window"]

    def test_an_unreadable_attention_record_errs_toward_the_user(self, home):
        hands = Hands()
        step, _ = _landed(home, hands)
        actions = step.step(_doc([_window(NOON + 6 * MIN, quiet=6 * MIN, attention=False)]),
                            NOON + 6 * MIN)
        assert [a["action"] for a in actions] == ["window open"]
        assert _state(home)["landed"]["notice"]["why"] == "unread"
        assert "nudge" not in hands.kinds()

    def test_a_turn_after_consent_keeps_it_open(self, home):
        hands = Hands()
        step, _ = _asked(home, hands)
        chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 7 * MIN)
        actions = step.step(_doc([_window(NOON + 8 * MIN, quiet=MIN, prompts=6)]),
                            NOON + 8 * MIN)
        assert [a["action"] for a in actions] == ["your window"]
        assert "close" not in hands.kinds()


class TestTheAnswers:
    def test_consent_closes_the_window_on_the_next_tick_and_releases(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, holder = _asked(home, hands, notifier=notifier)
        out = chainstep.consent(home, KEY, "demo", "close", None, 4242, NOON + 7 * MIN)
        assert out["state"] == "consented"
        actions = step.step(_doc([_window(NOON + 7 * MIN + 5, quiet=5, prompts=5)]),
                            NOON + 7 * MIN + 5)
        assert [a["action"] for a in actions] == ["closed"]
        assert ("close", 4242) in hands.calls
        state = _state(home)
        assert state["landed"] is None and "s9" in state["released"]
        ended = state["landings"][-1]
        assert ended["outcome"] == "closed" and ended["answers"][0]["answer"] == "close"
        assert notifier.sent[-1].subtitle == "chain closed"
        # the window gone, the next work starts
        holder["inputs"] = Inputs(_queue("IDEA_2"))
        step.step(_doc(), NOON + 8 * MIN)
        assert hands.kinds().count("start") == 2

    def test_consent_waits_for_a_turn_in_flight_then_closes_anyway(self, home):
        hands = Hands()
        step, _ = _asked(home, hands)
        chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 7 * MIN)
        assert step.step(_doc([_window(NOON + 7 * MIN + 5, quiet=5, prompts=5,
                                       turn_state="busy")]), NOON + 7 * MIN + 5) == []
        actions = step.step(_doc([_window(NOON + 8 * MIN + 1, quiet=1, prompts=5,
                                          turn_state="busy")]), NOON + 8 * MIN + 1)
        assert [a["action"] for a in actions] == ["closed"]

    def test_a_close_that_fails_is_a_notice(self, home):
        hands = Hands()
        hands.close_error = InvariantError("Claude did not exit within 10 s")
        step, _ = _asked(home, hands)
        chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 7 * MIN)
        actions = step.step(_doc([_window(NOON + 8 * MIN, quiet=MIN, prompts=5)]),
                            NOON + 8 * MIN)
        assert [a["action"] for a in actions] == ["window open"]
        landed = _state(home)["landed"]
        assert landed["notice"]["why"] == "close_failed"
        assert "did not exit" in landed["notice"]["detail"]

    def test_a_close_whose_record_moved_meanwhile_is_left(self, home):
        hands = Hands()
        step, _ = _asked(home, hands)
        chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 7 * MIN)

        def meddle(host):
            store = HomeStore(home)
            ledger = store.read(chainstep.LEDGER_FILE)
            ledger["projects"][KEY]["landed"] = None
            store.write(chainstep.LEDGER_FILE, ledger)
            return ["closed"]

        hands.on_close = meddle
        assert step.step(_doc([_window(NOON + 8 * MIN, quiet=MIN, prompts=5)]),
                         NOON + 8 * MIN) == []

    def test_a_close_left_in_flight_by_a_dead_watcher_is_a_notice(self, home):
        hands = Hands()
        step, _ = _asked(home, hands)
        chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 7 * MIN)
        store = HomeStore(home)
        ledger = store.read(chainstep.LEDGER_FILE)
        ledger["projects"][KEY]["landed"].update(state="closing", closing_epoch=NOON + 7 * MIN)
        store.write(chainstep.LEDGER_FILE, ledger)
        step.step(_doc([_window(NOON + 7 * MIN + 30, quiet=30, prompts=5)]),
                  NOON + 7 * MIN + 30)
        assert _state(home)["landed"]["state"] == "closing"
        step.step(_doc([_window(NOON + 10 * MIN, quiet=MIN, prompts=5)]), NOON + 10 * MIN)
        assert _state(home)["landed"]["notice"]["why"] == "close_failed"

    def test_not_yet_is_asked_again_later_three_asks_in_all(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, _ = _asked(home, hands, notifier=notifier)
        t = NOON + 7 * MIN
        out = chainstep.consent(home, KEY, "demo", "not_yet", "still writing the docs",
                                None, t)
        assert out["state"] == "watching"
        # 30 minutes after the answer, and quiet
        step.step(_doc([_window(t + 29 * MIN, quiet=20 * MIN, prompts=5)]), t + 29 * MIN)
        assert hands.kinds().count("nudge") == 1
        step.step(_doc([_window(t + 30 * MIN, quiet=20 * MIN, prompts=5)]), t + 30 * MIN)
        assert hands.kinds().count("nudge") == 2
        chainstep.consent(home, KEY, "demo", "not_yet", "two", None, t + 31 * MIN)
        step.step(_doc([_window(t + 61 * MIN, quiet=20 * MIN, prompts=6)]), t + 61 * MIN)
        assert hands.kinds().count("nudge") == 3
        out = chainstep.consent(home, KEY, "demo", "not_yet", "three", None, t + 62 * MIN)
        assert out["state"] == "notified"
        landed = _state(home)["landed"]
        assert landed["notice"] == {"why": "not_yet", "at": _iso(t + 62 * MIN),
                                    "note": "three", "detail": None}
        assert [a["note"] for a in landed["answers"]] == ["still writing the docs", "two",
                                                          "three"]
        step._deliver()
        assert "said not yet 3 times" in notifier.sent[-1].message
        assert "three" in notifier.sent[-1].message
        step.step(_doc([_window(t + 200 * MIN, quiet=100 * MIN, prompts=7)]), t + 200 * MIN)
        assert hands.kinds().count("nudge") == 3

    def test_no_answer_within_ten_minutes_is_one_notice_and_no_more_asks(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, _ = _asked(home, hands, notifier=notifier)
        asked = NOON + 6 * MIN
        assert step.step(_doc([_window(asked + 9 * MIN, quiet=8 * MIN, prompts=5)]),
                         asked + 9 * MIN) == []
        actions = step.step(_doc([_window(asked + 10 * MIN, quiet=9 * MIN, prompts=5)]),
                            asked + 10 * MIN)
        assert [a["action"] for a in actions] == ["window open"]
        assert _state(home)["landed"]["notice"]["why"] == "no_answer"
        assert "did not answer" in notifier.sent[-1].message
        step.step(_doc([_window(asked + 99 * MIN, quiet=90 * MIN, prompts=5)]),
                  asked + 99 * MIN)
        assert hands.kinds().count("nudge") == 1
        assert [n.subtitle for n in notifier.sent].count("chain window open") == 1

    def test_a_late_consent_after_the_notice_still_closes(self, home):
        hands = Hands()
        step, _ = _asked(home, hands)
        step.step(_doc([_window(NOON + 17 * MIN, quiet=9 * MIN, prompts=5)]), NOON + 17 * MIN)
        assert _state(home)["landed"]["state"] == "notified"
        chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 20 * MIN)
        actions = step.step(_doc([_window(NOON + 21 * MIN, quiet=MIN, prompts=5)]),
                            NOON + 21 * MIN)
        assert [a["action"] for a in actions] == ["closed"]

    def test_a_late_not_yet_after_the_notice_is_kept_and_asks_nothing(self, home):
        hands = Hands()
        step, _ = _asked(home, hands)
        step.step(_doc([_window(NOON + 17 * MIN, quiet=9 * MIN, prompts=5)]), NOON + 17 * MIN)
        out = chainstep.consent(home, KEY, "demo", "not_yet", "late", None, NOON + 20 * MIN)
        assert out["state"] == "notified"
        assert _state(home)["landed"]["answers"][-1]["note"] == "late"

    def test_a_window_closed_by_hand_ends_the_landing(self, home):
        hands = Hands()
        step, _ = _asked(home, hands)
        assert step.step(_doc(), NOON + 7 * MIN) == []
        actions = step.step(_doc([_window(NOON + 8 * MIN, alive=False)]), NOON + 8 * MIN)
        assert [a["action"] for a in actions] == ["window closed"]
        state = _state(home)
        assert state["landed"] is None and state["landings"][-1]["outcome"] == "gone"

    def test_a_window_that_comes_back_clears_the_count(self, home):
        step, _ = _landed(home, Hands())
        step.step(_doc(), NOON + 10)
        assert _state(home)["landed"]["gone_since_epoch"] == NOON + 10
        step.step(_doc([_window(NOON + 20, quiet=20)]), NOON + 20)
        assert _state(home)["landed"]["gone_since_epoch"] is None

    def test_the_landings_are_bounded(self, home):
        state = {"landings": [{"n": n} for n in range(30)], "released": []}
        chainstep._end_landed(state, {"session": "s1"}, "gone", NOON, release=False)
        assert len(state["landings"]) == chainstep.LANDINGS_KEPT
        assert state["landings"][-1]["outcome"] == "gone"


class TestConsentRefusals:
    def test_nothing_landed_is_refused(self, home):
        with pytest.raises(InvariantError, match="no landed window"):
            chainstep.consent(home, KEY, "demo", "close", None, None, NOON)

    def test_a_window_nobody_asked_is_refused(self, home):
        _landed(home, Hands())
        with pytest.raises(InvariantError, match="has not asked"):
            chainstep.consent(home, KEY, "demo", "close", None, None, NOON)

    def test_another_windows_answer_is_refused(self, home):
        _asked(home, Hands())
        with pytest.raises(InvariantError, match="another window"):
            chainstep.consent(home, KEY, "demo", "close", None, 999, NOON + 7 * MIN)

    def test_the_users_window_is_refused(self, home):
        step, _ = _asked(home, Hands())
        step.step(_doc([_window(NOON + 8 * MIN, quiet=MIN, prompts=6)]), NOON + 8 * MIN)
        with pytest.raises(InvariantError, match="yours|the user"):
            chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 9 * MIN)

    def test_a_second_consent_says_so(self, home):
        _asked(home, Hands())
        chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 7 * MIN)
        out = chainstep.consent(home, KEY, "demo", "close", None, None, NOON + 7 * MIN + 1)
        assert out["state"] == "consented" and out["already"] is True

    def test_an_unknown_answer_is_a_programming_error(self, home):
        with pytest.raises(ValueError):
            chainstep.consent(home, KEY, "demo", "maybe", None, None, NOON)


class TestCloseAndLeaveOpen:
    def _notified(self, home, hands):
        step, _ = _asked(home, hands)
        step.step(_doc([_window(NOON + 17 * MIN, quiet=9 * MIN, prompts=5)]), NOON + 17 * MIN)
        return step

    def test_leave_open_stops_the_asking_and_holds(self, home):
        hands = Hands()
        step = self._notified(home, hands)
        actions = step.landed_action(KEY, "demo", "leave-open", None, NOON + 18 * MIN)
        assert [a["action"] for a in actions] == ["left open"]
        landed = _state(home)["landed"]
        assert landed["state"] == "left_open" and landed["decided"]["what"] == "leave_open"
        step.step(_doc([_window(NOON + 99 * MIN, quiet=80 * MIN, prompts=5)]), NOON + 99 * MIN)
        assert hands.kinds().count("nudge") == 1 and "close" not in hands.kinds()

    def test_close_window_closes_through_the_hands_and_releases(self, home):
        hands = Hands()
        step = self._notified(home, hands)
        host = _window(NOON)["hosts"][0]
        actions = step.landed_action(KEY, "demo", "close-window", host, NOON + 18 * MIN)
        assert [a["action"] for a in actions] == ["closed"]
        assert ("close", 4242) in hands.calls
        state = _state(home)
        assert state["landed"] is None and state["landings"][-1]["outcome"] == "closed_by_user"
        assert "s9" in state["released"]

    def test_a_window_already_gone_is_said(self, home):
        step = self._notified(home, Hands())
        actions = step.landed_action(KEY, "demo", "close-window", None, NOON + 18 * MIN)
        assert "already closed" in actions[0]["detail"]

    def test_they_need_a_notice(self, home):
        step, _ = _asked(home, Hands())
        for action in ("close-window", "leave-open"):
            with pytest.raises(InvariantError, match="waits for you"):
                step.landed_action(KEY, "demo", action, None, NOON + 7 * MIN)

    def test_nothing_landed_is_refused(self, home):
        step, _ = _step(home, Hands())
        with pytest.raises(InvariantError, match="no landed window"):
            step.landed_action(KEY, "demo", "leave-open", None, NOON)
        with pytest.raises(ValueError):
            step.landed_action(KEY, "demo", "explode", None, NOON)

    def test_a_close_whose_record_moved_meanwhile_is_refused(self, home):
        hands = Hands()
        step = self._notified(home, hands)

        def meddle(host):
            store = HomeStore(home)
            ledger = store.read(chainstep.LEDGER_FILE)
            ledger["projects"][KEY]["landed"]["state"] = "left_open"
            store.write(chainstep.LEDGER_FILE, ledger)
            return ["closed"]

        hands.on_close = meddle
        with pytest.raises(InvariantError, match="moved on"):
            step.landed_action(KEY, "demo", "close-window", _window(NOON)["hosts"][0],
                               NOON + 18 * MIN)

    def test_leave_open_whose_record_moved_meanwhile_is_refused(self, home, monkeypatch):
        step = self._notified(home, Hands())
        seen = step.landed(KEY)
        monkeypatch.setattr(step, "landed", lambda key: dict(seen, id="C99"))
        with pytest.raises(InvariantError, match="moved on"):
            step.landed_action(KEY, "demo", "leave-open", None, NOON + 18 * MIN)

    def test_landed_of_a_project_and_an_unreadable_ledger(self, home):
        step, _ = _asked(home, Hands())
        assert step.landed(KEY)["state"] == "asked"
        (home / chainstep.LEDGER_FILE).write_text("[]")
        assert step.landed(KEY) is None


# -- the phone: the read, the offers, the words ----------------------------------------------

NOW = NOON + 20 * MIN


def _landed_record(state="notified", why="no_answer", note=None, asks=1):
    return {"id": "C1", "entry": "Q1", "ref": "IDEA_7", "session": "s9", "name": NAME,
            "pid": 4242, "landed_epoch": NOON, "landed": _iso(NOON), "state": state,
            "asks": [{"at": _iso(NOON), "at_epoch": NOON, "code": "typed"}] * asks,
            "answers": ([{"answer": "not_yet", "note": note, "at": _iso(NOON),
                          "at_epoch": NOON}] if note else []),
            "notice": ({"why": why, "at": _iso(NOON + 10 * MIN), "note": note, "detail": None}
                       if state == "notified" else None),
            "limits": {"ask_after": 300.0, "ask_again": 1800.0, "asks": 3,
                       "answer_within": 600.0}}


def _write(home, landed):
    HomeStore(home).write(chainstep.LEDGER_FILE, {
        "version": 1, "projects": {KEY: {"landed": landed}}, "notices": []})


def _reader(home):
    return chainview.ChainReader(
        home, usage=lambda: PlanUsage(weekly=10.0, weekly_resets_at=None, five_hour=1.0,
                                      five_hour_resets_at=None, read_at=NOW - 60),
        clock=lambda: NOW, queues=lambda root: Queue(), titles=lambda root, ref: None)


def _pdoc(rows=()):
    return {"now": NOW, "projects": [
        {"project": {"key": KEY, "name": "Heap", "root": "/r"}, "rows": list(rows)}]}


def _view(home, landed, rows=(), acting=True):
    _write(home, landed)
    document = _pdoc(rows)
    chains = _reader(home).read(document)
    return panel.view(document, acting=acting, jobs=[], starting=True, chains=chains)


def _needs(view):
    group = next((g for g in view["groups"] if g["id"] == "needs"), None)
    return group["cards"] if group else []


def _host_row():
    return {"kind": "host", "last_activity": NOW - 15 * MIN, "turn_state": "idle",
            "hosts": [{"host_session_id": "27885f4e-aaaa-bbbb-cccc-ddddeeeeffff",
                       "current": True, "launched_name": NAME, "pid": 4242, "alive": True,
                       "tty": "ttys004", "process_start": "x"}]}


class TestTheRead:
    def test_a_notified_window_offers_close_and_leave_open(self, home):
        _write(home, _landed_record())
        read = _reader(home).read(_pdoc())
        project = read["projects"][KEY]
        assert project["landed"]["state"] == "notified"
        assert project["landed"]["why"] == "no_answer"
        offers = [(o["action"], o["arg"]) for o in project["offered"]]
        assert ("close-window", None) in offers and ("leave-open", None) in offers

    @pytest.mark.parametrize("state", ["watching", "asked", "consented", "left_open", "users"])
    def test_no_other_state_offers_them(self, home, state):
        _write(home, _landed_record(state=state))
        offers = [o["action"] for o in _reader(home).read(_pdoc())["projects"][KEY]["offered"]]
        assert "close-window" not in offers and "leave-open" not in offers

    def test_the_keys_parse(self):
        assert chainview.parse_key("K|chain:close-window") == ("K", "close-window", None)
        assert chainview.parse_key("K|chain:leave-open") == ("K", "leave-open", None)
        assert chainview.parse_key("K|chain:leave-open:Q1") is None

    def test_derive_close_window_names_the_window(self, home):
        _write(home, _landed_record())
        document = _pdoc([_host_row()])
        read = _reader(home).read(document)
        derived = chainview.derive(read, document, f"{KEY}|chain:close-window", home)
        assert derived.argv == ("invariant", "oracle", "chain", "close-window", KEY)
        assert derived.facts["entry"] == "Q1" and derived.facts["ref"] == "IDEA_7"
        assert derived.facts["window"] == "27885f4e" and derived.facts["pid"] == 4242
        derived = chainview.derive(read, document, f"{KEY}|chain:leave-open", home)
        assert derived.argv[-2:] == ("leave-open", KEY)

    def test_a_gone_window_has_none(self, home):
        _write(home, _landed_record())
        document = _pdoc()
        read = _reader(home).read(document)
        derived = chainview.derive(read, document, f"{KEY}|chain:close-window", home)
        assert derived.facts["window"] is None

    def test_not_offered_says_why(self, home):
        _write(home, _landed_record(state="asked"))
        document = _pdoc()
        read = _reader(home).read(document)
        why = chainview.derive(read, document, f"{KEY}|chain:close-window", home)
        assert "waits for you" in why
        _write(home, None)
        read = _reader(home).read(document)
        assert "no landed window" in chainview.derive(read, document, f"{KEY}|chain:leave-open",
                                                       home)


class TestTheWords:
    @pytest.mark.parametrize("why, needle", [
        ("no_answer", "did not answer"), ("not_yet", "said not yet"),
        ("not_typed", "could not ask"), ("unread", "cannot tell"),
        ("close_failed", "closing its window failed")])
    def test_the_card(self, home, why, needle):
        view = _view(home, _landed_record(why=why, note="docs" if why == "not_yet" else None))
        card = next(c for c in _needs(view) if c["row"].endswith("chain:card:landed"))
        assert card["chips"] == [{"text": "Window open", "tone": "needs"}]
        assert card["title"] == "IDEA_7" and needle in card["note"]
        assert [a["text"] for a in card["actions"]] == ["Close…", "Leave open…"]
        assert [a["row"] for a in card["actions"]] == [f"{KEY}|chain:close-window",
                                                       f"{KEY}|chain:leave-open"]
        assert "invariant oracle" not in card["note"]

    def test_a_not_yet_card_quotes_the_note(self, home):
        view = _view(home, _landed_record(why="not_yet", note="still writing", asks=3))
        card = next(c for c in _needs(view) if c["row"].endswith("chain:card:landed"))
        assert "3 times" in card["note"] and "“still writing”" in card["note"]

    def test_no_card_before_the_notice(self, home):
        view = _view(home, _landed_record(state="asked"))
        assert not [c for c in _needs(view) if c["row"].endswith("chain:card:landed")]

    @pytest.mark.parametrize("state, line", [
        ("watching", "Landed · its window is asked to close once it is quiet"),
        ("asking", "Landed · asked whether its window may close"),
        ("asked", "Landed · asked whether its window may close"),
        ("consented", "Landed · closing its window, as it agreed"),
        ("closing", "Landed · closing its window, as it agreed"),
        ("notified", "Landed · its window is still open and waits for you"),
        ("left_open", "Landed · left open by you; the chain waits until you close it"),
        ("users", ("Landed · you typed in its window, so it is yours; the chain waits "
                   "until you close it")),
    ])
    def test_the_now_card(self, home, state, line):
        screen = _view(home, _landed_record(state=state))["chain"]["projects"][KEY]
        assert screen["now"]["title"] == "Q1 · IDEA_7"
        assert screen["now"]["line"] == line
        texts = [a["text"] for a in screen["now"]["actions"]]
        assert texts == (["Close…", "Leave open…"] if state == "notified" else [])

    def test_a_watching_window_said_not_yet(self, home):
        screen = _view(home, _landed_record(state="watching", note="docs"))["chain"]
        assert screen["projects"][KEY]["now"]["line"] == (
            "Landed · said not yet: “docs”; asked again later")

    def test_a_read_only_page_has_no_buttons(self, home):
        view = _view(home, _landed_record(), acting=False)
        card = next(c for c in _needs(view) if c["row"].endswith("chain:card:landed"))
        assert card["actions"] == []

    def test_close_is_the_destructive_sheet_naming_the_window(self, home):
        _write(home, _landed_record())
        document = _pdoc([_host_row()])
        read = _reader(home).read(document)
        derived = chainview.derive(read, document, f"{KEY}|chain:close-window", home)
        words = panel.chain_confirm_words(derived.action, derived.name, derived.facts,
                                          derived.argv, derived.cwd, now=NOW)
        assert words["title"] == "Close Q1's window?"
        assert words["destructive"] is True and words["button"] == "Close window"
        assert words["deletes"] == ["Claude in Heap · window 27885f4e",
                                    "Process 4242 · idle 15 min"]
        assert words["checked"] == [("It is still the window where Q1 landed, and it still "
                                     "waits for you"),
                                    "Checked again the moment you press Close window"]
        assert words["command"] == f"invariant oracle chain close-window {KEY}"

    def test_leave_open_is_a_safe_sheet(self, home):
        _write(home, _landed_record())
        document = _pdoc()
        read = _reader(home).read(document)
        derived = chainview.derive(read, document, f"{KEY}|chain:leave-open", home)
        words = panel.chain_confirm_words(derived.action, derived.name, derived.facts,
                                          derived.argv, derived.cwd, now=NOW)
        assert words["destructive"] is False and words["button"] == "Leave open"
        assert words["title"] == "Leave Q1's window open?"

    @pytest.mark.parametrize("argv, deed", [
        (["invariant", "oracle", "chain", "close-window", "K"], "Close window"),
        (["invariant", "oracle", "chain", "leave-open", "K"], "Leave open"),
    ])
    def test_each_deed_is_named(self, argv, deed):
        job = {"argv": argv, "kind": "run", "state": "done", "ok": True, "elapsed": 1.0}
        assert panel.result_words(job)["line"] == deed


# -- the commands ------------------------------------------------------------------------------


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture(autouse=True)
def _scan_without_a_probe(monkeypatch):
    monkeypatch.setattr(cli_main, "_oracle_cache", lambda: ScanCache())


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
    monkeypatch.delenv("CLAUDE_PID", raising=False)
    assert main(["init", "--home", "repo", "--name", "demo"]) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "manifest")
    return root


def _cli_ledger(landed):
    import os

    home = Path(os.environ["INVARIANT_HOME"])
    key = cli_main._project_index().match("demo").key
    HomeStore(home).write(chainstep.LEDGER_FILE, {
        "version": 1, "projects": {key: {"landed": landed}}, "notices": []})
    return home, key


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestTheCommands:
    def test_consent_close_is_recorded(self, project, capsys):
        home, key = _cli_ledger(_landed_record(state="asked"))
        assert main(["oracle", "chain", "consent", "demo", "--close", "--json"]) == 0
        assert _json(capsys)["state"] == "consented"
        assert HomeStore(home).read(chainstep.LEDGER_FILE)["projects"][key]["landed"][
            "state"] == "consented"

    def test_consent_not_yet_needs_a_vetted_one_line_note(self, project, capsys):
        home, key = _cli_ledger(_landed_record(state="asked"))
        assert main(["oracle", "chain", "consent", "demo", "--not-yet"]) == 2
        assert "--note" in capsys.readouterr().err
        assert main(["oracle", "chain", "consent", "demo", "--not-yet", "--note",
                     "a\nb"]) == 2
        capsys.readouterr()
        assert main(["oracle", "chain", "consent", "demo", "--close", "--note", "x"]) == 2
        capsys.readouterr()
        assert main(["oracle", "chain", "consent", "demo", "--not-yet", "--note",
                     "still writing the docs"]) == 0
        assert "asks again" in capsys.readouterr().out
        landed = HomeStore(home).read(chainstep.LEDGER_FILE)["projects"][key]["landed"]
        assert landed["answers"][-1]["note"] == "still writing the docs"

    def test_consent_from_another_claude_is_refused(self, project, capsys, monkeypatch):
        _cli_ledger(_landed_record(state="asked"))
        monkeypatch.setenv("CLAUDE_PID", "999")
        assert main(["oracle", "chain", "consent", "demo", "--close"]) == 2
        assert "another window" in capsys.readouterr().err

    def test_consent_with_nothing_landed_is_refused(self, project, capsys):
        assert main(["oracle", "chain", "consent", "demo", "--close"]) == 2
        assert "no landed window" in capsys.readouterr().err

    def test_close_window_and_leave_open(self, project, capsys, monkeypatch):
        home, key = _cli_ledger(_landed_record())
        host = _host_row()["hosts"][0]
        found = []
        monkeypatch.setattr(cli_main, "_chain_landed_host",
                            lambda index, entry, landed: found.append(landed["name"]) or host)
        closed = []
        monkeypatch.setattr(cli_main, "_chain_closer",
                            lambda index: lambda h: closed.append(h["pid"]) or ["closed it"])
        assert main(["oracle", "chain", "leave-open", "demo"]) == 0
        assert "left open" in capsys.readouterr().out
        _cli_ledger(_landed_record())
        assert main(["oracle", "chain", "close-window", "demo", "--json"]) == 0
        assert _json(capsys)["actions"][0]["action"] == "closed"
        assert closed == [4242] and found[-1] == NAME
        landings = HomeStore(home).read(chainstep.LEDGER_FILE)["projects"][key]["landings"]
        assert landings[-1]["outcome"] == "closed_by_user"

    def test_close_window_with_nothing_landed_is_refused(self, project, capsys):
        assert main(["oracle", "chain", "close-window", "demo"]) == 2
        assert "no landed window" in capsys.readouterr().err

    def test_the_landed_host_is_found_by_its_name_in_a_fresh_scan(self, project, monkeypatch):
        index = cli_main._project_index()
        entry = index.match("demo")
        row = _host_row()
        monkeypatch.setattr(cli_main, "_oracle_landed_rows", lambda index, entry: [row])
        assert cli_main._chain_landed_host(index, entry, {"name": NAME, "pid": None}) == \
            row["hosts"][0]
        assert cli_main._chain_landed_host(index, entry, {"name": "other", "pid": 1}) is None

    def test_status_shows_the_landed_window(self, project, capsys):
        _cli_ledger(_landed_record())
        assert main(["oracle", "chain", "status"]) == 0
        assert "window of Q1 IDEA_7 landed: notified" in capsys.readouterr().out
