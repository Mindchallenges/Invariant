"""ADR-027 increment 2b: the plan_usage probe, the weekly-usage hold, stalls.

Ruling 10: an opt-in status line records the plan's usage as a host-neutral
fact in the oracle home; a missing, stale or over-the-cap reading holds every
auto-start and every proposal, and a greenlight lifts the unknown-reading hold
until a fresh reading arrives. Ruling 11: every live session can be
``stalled``; only a chained one gets one "continue" typed into its tab.
Ruling 12: still stalled one fuse after the nudge is a notice, and Continue,
Retry and Close are verbs. Ruling 13: a chained session the chain has finished
with stops holding its project.

No test types into a terminal, closes a window or reads the real ``~/.claude``:
the step's hands are recorders, and the host readers are fed files under
``tmp_path``.
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
from invariant.core.home import load_user_config
from invariant.core.manifest import Manifest
from invariant.core.queue import Queue
from invariant.core.store import JsonStore
from invariant.domains.queue import QueueDomain
from invariant.hosts import claude_code_launch as launch
from invariant.hosts import claude_code_telemetry as telemetry
from invariant.hosts import claude_code_usage as host_usage
from invariant.oracle import chain, chainstep, usage
from invariant.oracle.chain import ChainSettings
from invariant.oracle.chainstep import ChainHands, ChainStep
from invariant.oracle.notify import RecordingNotifier
from invariant.oracle.panel import group_of
from invariant.oracle.recommend import recommend
from invariant.oracle.scan import ROW_SESSION, HostView, Row, ScanCache
from invariant.oracle.signals import STALLED, Thresholds, classify

NOON = time.mktime((2026, 11, 18, 12, 0, 0, 0, 0, -1))
HOUR = 3600.0
KEY = "/state/demo"


# -- the host: the status line's JSON, its text, its install ----------------------------


def _payload(week=41.0, five=12.0, week_reset=NOON + 86400, five_reset=NOON + 3600):
    return {"session_id": "abc", "rate_limits": {
        "five_hour": {"used_percentage": five, "resets_at": five_reset},
        "seven_day": {"used_percentage": week, "resets_at": week_reset}}}


class TestTheStatusLine:
    def test_a_reading_is_host_neutral(self):
        fact = host_usage.reading_from_statusline(_payload(), NOON)
        assert fact == {"weekly": 41.0, "weekly_resets_at": NOON + 86400, "five_hour": 12.0,
                        "five_hour_resets_at": NOON + 3600, "read_at": NOON,
                        "host": "claude_code"}

    def test_reset_times_in_milliseconds_or_iso_are_read(self):
        p = _payload(week_reset=(NOON + 60) * 1000, five_reset="2026-11-18T12:00:00+00:00")
        fact = host_usage.reading_from_statusline(p, NOON)
        assert fact["weekly_resets_at"] == NOON + 60
        assert fact["five_hour_resets_at"] == datetime(2026, 11, 18, 12,
                                                        tzinfo=timezone.utc).timestamp()

    def test_no_rate_limits_is_no_reading(self):
        assert host_usage.reading_from_statusline({"session_id": "x"}, NOON) is None
        assert host_usage.reading_from_statusline("nope", NOON) is None
        assert host_usage.reading_from_statusline({"rate_limits": {"seven_day": {}}},
                                                  NOON) is None

    def test_a_partial_reading_keeps_what_it_has(self):
        p = {"rate_limits": {"five_hour": {"used_percentage": "7", "resets_at": "bad"}}}
        fact = host_usage.reading_from_statusline(p, NOON)
        assert (fact["weekly"], fact["five_hour"], fact["five_hour_resets_at"]) == (
            None, 7.0, None)

    def test_the_text(self):
        assert host_usage.statusline_text({"weekly": 41.4, "five_hour": 12}) == (
            "week 41% · 5h 12%")
        assert host_usage.statusline_text({"weekly": None, "five_hour": 3}) == "week ? · 5h 3%"
        assert host_usage.statusline_text(None) == "usage ?"

    def test_install_writes_a_status_line_and_refuses_to_replace_one(self, tmp_path):
        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"model": "x"}))
        assert host_usage.install_statusline(settings, "invariant hook statusline") == "installed"
        written = json.loads(settings.read_text())
        assert written["model"] == "x"
        assert written["statusLine"] == {"type": "command",
                                          "command": "invariant hook statusline"}
        assert host_usage.install_statusline(settings, "invariant hook statusline") == (
            "already")
        settings.write_text(json.dumps({"statusLine": {"type": "command", "command": "mine"}}))
        with pytest.raises(InvariantError, match="already has a statusLine"):
            host_usage.install_statusline(settings, "invariant hook statusline")
        assert json.loads(settings.read_text())["statusLine"]["command"] == "mine"

    def test_install_creates_the_file_and_refuses_one_that_is_not_json(self, tmp_path):
        settings = tmp_path / "claude" / "settings.json"
        assert host_usage.install_statusline(settings, "c") == "installed"
        settings.write_text("{")
        with pytest.raises(InvariantError, match="not JSON"):
            host_usage.install_statusline(settings, "c")
        settings.write_text("[]")
        with pytest.raises(InvariantError, match="not a JSON object"):
            host_usage.install_statusline(settings, "c")


# -- the oracle: the fact, its file, the hold -------------------------------------------


def _reading(weekly=41.0, read_at=NOON, five=None, resets=None):
    return usage.PlanUsage(weekly=weekly, weekly_resets_at=None, five_hour=five,
                           five_hour_resets_at=resets, read_at=read_at, host="claude_code")


class TestTheFact:
    def test_record_and_read(self, tmp_path):
        fact = host_usage.reading_from_statusline(_payload(), NOON)
        assert usage.record_usage(tmp_path, fact, NOON) is True
        got = usage.read_usage(tmp_path)
        assert (got.weekly, got.five_hour, got.read_at) == (41.0, 12.0, NOON)

    def test_an_unchanged_reading_is_written_once_a_minute(self, tmp_path):
        fact = host_usage.reading_from_statusline(_payload(), NOON)
        assert usage.record_usage(tmp_path, fact, NOON)
        later = dict(fact, read_at=NOON + 30)
        assert usage.record_usage(tmp_path, later, NOON + 30) is False
        changed = dict(later, weekly=42.0)
        assert usage.record_usage(tmp_path, changed, NOON + 30) is True
        assert usage.record_usage(tmp_path, dict(changed, read_at=NOON + 91), NOON + 91)
        assert usage.read_usage(tmp_path).read_at == NOON + 91

    def test_a_missing_or_broken_file_is_no_reading(self, tmp_path):
        assert usage.read_usage(tmp_path) is None
        (tmp_path / "oracle").mkdir()
        (tmp_path / "oracle" / "usage.json").write_text("{")
        assert usage.read_usage(tmp_path) is None
        (tmp_path / "oracle" / "usage.json").write_text(json.dumps({"weekly": 3}))
        assert usage.read_usage(tmp_path) is None
        assert usage.PlanUsage.from_dict([]) is None

    def test_weekly_max(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INVARIANT_HOME", str(tmp_path))
        assert usage.weekly_max(load_user_config()) == 95
        (tmp_path / "config.yaml").write_text("chain: {weekly_usage_max: 80}\n")
        assert usage.weekly_max(load_user_config()) == 80
        (tmp_path / "config.yaml").write_text("chain: {weekly_usage_max: 101}\n")
        with pytest.raises(ManifestError, match="weekly_usage_max"):
            load_user_config()


class TestTheHold:
    def test_a_fresh_reading_under_the_cap_holds_nothing(self):
        assert usage.hold(_reading(94.9), NOON + 60, 95, None) is None

    def test_at_the_cap_holds(self):
        held = usage.hold(_reading(95), NOON, 95, None)
        assert held.kind == usage.OVER and "95%" in held.reason

    def test_missing_holds(self):
        held = usage.hold(None, NOON, 95, None)
        assert held.kind == usage.UNKNOWN
        assert "invariant hooks statusline" in held.reason
        assert "greenlight" in held.reason

    def test_stale_or_without_a_weekly_figure_holds(self):
        assert usage.hold(_reading(10, read_at=NOON - 1801), NOON, 95, None).kind == (
            usage.UNKNOWN)
        assert "31 min old" in usage.hold(_reading(10, read_at=NOON - 1860), NOON, 95,
                                          None).reason
        assert usage.hold(_reading(None), NOON, 95, None).kind == usage.UNKNOWN

    def test_a_greenlight_lifts_the_unknown_hold_until_a_fresh_reading(self):
        assert usage.hold(None, NOON, 95, NOON - 5) is None
        assert usage.hold(_reading(10, read_at=NOON - 4000), NOON, 95, NOON - 5) is None
        # a reading after the greenlight spends it: stale, it holds again
        assert usage.hold(_reading(10, read_at=NOON - 1), NOON + 2000, 95, NOON - 5).kind == (
            usage.UNKNOWN)
        # and a fresh one at the cap holds whatever the greenlight said
        assert usage.hold(_reading(99, read_at=NOON), NOON, 95, NOON - 5).kind == usage.OVER

    def test_spent(self):
        assert usage.spent(_reading(read_at=NOON), NOON - 1) is True
        assert usage.spent(_reading(read_at=NOON), NOON) is False
        assert usage.spent(None, NOON) is False


class TestTheDecision:
    def _decide(self, queue=None, settings=None, **kw):
        return chain.decide(queue=queue or Queue(), ready=[], settings=settings or ChainSettings(),
                            live=[], windows=0,
                            now=datetime.fromtimestamp(NOON, timezone.utc).astimezone(), **kw)

    def test_the_hold_turns_a_start_into_a_wait(self):
        q = Queue()
        q.add("IDEA_1", added_by="user")
        verdict = self._decide(q, usage_hold="the plan is spent")
        assert (verdict.verdict, verdict.limit) == ("wait", chain.WEEKLY_USAGE)
        assert verdict.reasons[-1] == "the plan is spent"

    def test_the_hold_holds_a_proposal_too(self):
        q = Queue()
        q.add("BUG_1", added_by="user", mode="propose")
        verdict = self._decide(q, usage_hold="spent")
        assert (verdict.verdict, verdict.limit) == ("wait", chain.WEEKLY_USAGE)
        assert verdict.candidate is None and verdict.proposals

    def test_nothing_to_hold_stays_idle(self):
        assert self._decide(usage_hold="spent").verdict == "idle"

    def test_the_hold_comes_before_the_caps(self):
        q = Queue()
        q.add("a", added_by="user")
        verdict = self._decide(q, usage_hold="spent", started_today=99)
        assert verdict.limit == chain.WEEKLY_USAGE


# -- the transcript's last turn: the diagnosis --------------------------------------------


def _write_transcript(path: Path, lines: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return path


def _limit_line(kind="five_hour", resets=1793554800, error="rate_limit"):
    return {"type": "assistant", "timestamp": "2026-11-18T10:00:00Z", "isApiErrorMessage": True,
            "error": error, "quotaLimits": {"rateLimitType": kind, "resetsAt": resets},
            "message": {"content": [{"type": "text", "text": "You've hit your limit"}]}}


class TestTheDiagnosis:
    def test_a_limit_stop_is_read_from_the_last_turn(self, tmp_path):
        path = _write_transcript(tmp_path / "t.jsonl", [_limit_line()])
        turn = telemetry.last_turn(path)
        assert (turn.error, turn.limit, turn.resets_at) == ("rate_limit", "five_hour",
                                                            1793554800.0)
        assert telemetry.turn_end(turn) == {"error": "rate_limit", "limit": "five_hour",
                                            "resets_at": 1793554800.0}

    def test_an_error_without_a_quota(self, tmp_path):
        line = {"type": "assistant", "timestamp": "2026-11-18T10:00:00Z",
                "isApiErrorMessage": True, "error": "authentication_failed"}
        turn = telemetry.last_turn(_write_transcript(tmp_path / "t.jsonl", [line]))
        assert telemetry.turn_end(turn) == {"error": "authentication_failed", "limit": None,
                                            "resets_at": None}
        bare = {"type": "assistant", "timestamp": "2026-11-18T10:00:00Z",
                "isApiErrorMessage": True, "quotaLimits": "x"}
        turn = telemetry.last_turn(_write_transcript(tmp_path / "u.jsonl", [bare]))
        assert telemetry.turn_end(turn)["error"] == "api_error"

    def test_a_plain_turn_has_no_end(self, tmp_path):
        line = {"type": "assistant", "timestamp": "2026-11-18T10:00:00Z", "message": {}}
        turn = telemetry.last_turn(_write_transcript(tmp_path / "t.jsonl", [line]))
        assert telemetry.turn_end(turn) is None
        assert telemetry.turn_end(None) is None

    def test_the_source_answers_turn_ends_per_host(self, tmp_path):
        home = tmp_path / "claude"
        project = home / "projects" / "p"
        _write_transcript(project / "h1.jsonl", [_limit_line()])
        _write_transcript(project / "h2.jsonl", [{"type": "user", "timestamp":
                                                  "2026-11-18T10:00:00Z", "message": {}}])
        source = telemetry.ClaudeCodeTranscripts(home=home)
        assert set(source.activity()) == {"h1", "h2"}
        assert source.turn_ends() == {"h1": {"error": "rate_limit", "limit": "five_hour",
                                             "resets_at": 1793554800.0}}

    def test_the_scan_carries_it_on_the_host(self):
        class Source:
            def activity(self):
                return {"h1": NOON}

            def turn_ends(self):
                return {"h1": {"error": "x", "limit": None, "resets_at": None}}

        class Probe:
            def transcripts(self, root):
                return Source()

            def is_host_process(self, pid):
                return True

        cache = ScanCache(probe=Probe())
        assert cache.transcript_ends(Path("/r")) == {"h1": {"error": "x", "limit": None,
                                                            "resets_at": None}}
        assert ScanCache().transcript_ends(Path("/r")) == {}

        class Old:
            def transcripts(self, root):
                class S:
                    def activity(self):
                        return {}
                return S()

        assert ScanCache(probe=Old()).transcript_ends(Path("/r")) == {}


# -- the stalled signal -----------------------------------------------------------------------


def _row(quiet_hours=3.0, alive=True, attention=None, status=None, turn_end=None,
         stall_after=None):
    host = HostView("h1", current=True, pid=10, alive=alive, attention=attention,
                    status=status, turn_end=turn_end)
    return Row(kind=ROW_SESSION, project=KEY, session_id="s1", name="work", state="working",
               hosts=[host], last_activity=NOON - quiet_hours * HOUR, stall_after=stall_after)


def _stalled(row, now=NOON):
    return [s for s in classify(row, now) if s.kind == STALLED]


class TestTheSignal:
    def test_quiet_past_the_fuse_is_stalled(self):
        [signal] = _stalled(_row())
        assert signal.alert and signal.cause == "unknown"
        assert signal.key.endswith(f"|stalled|{int(NOON - 3 * HOUR)}")
        assert "180 min" in signal.detail

    def test_not_before_the_fuse(self):
        assert _stalled(_row(1.9)) == []

    def test_the_projects_fuse(self):
        assert _stalled(_row(1.0, stall_after=0.5))
        assert _stalled(_row(3.0, stall_after=4)) == []
        assert Thresholds().stall_hours == 2.0

    def test_waiting_on_a_prompt_is_not_a_stall(self):
        asking = {"waiting": {"kind": "permission", "since": "x"}}
        assert _stalled(_row(attention=asking)) == []
        assert _stalled(_row(status="waiting")) == []

    def test_a_gone_or_absent_host_is_not_stalled(self):
        assert _stalled(_row(alive=False)) == []
        assert _stalled(_row(alive=None)) == []
        row = _row()
        row.hosts = []
        assert _stalled(row) == []
        row = _row()
        row.last_activity = None
        assert _stalled(row) == []

    def test_the_diagnosis(self):
        [limit] = _stalled(_row(turn_end={"error": "rate_limit", "limit": "five_hour",
                                          "resets_at": NOON + 1800}))
        assert limit.cause == "limit"
        assert "five-hour limit" in limit.detail and "resets in 30 min" in limit.detail
        [weekly] = _stalled(_row(turn_end={"error": "rate_limit", "limit": "seven_day",
                                           "resets_at": None}))
        assert "weekly limit" in weekly.detail
        [error] = _stalled(_row(turn_end={"error": "overloaded", "limit": None}))
        assert error.cause == "error" and "overloaded" in error.detail

    def test_the_row_drops_an_unset_fuse(self):
        assert "stall_after" not in _row().to_dict()
        assert _row(stall_after=2.0).to_dict()["stall_after"] == 2.0

    def test_the_panel_and_the_recommendation(self):
        row = _row()
        signals = classify(row, NOON)
        body = row.to_dict()
        body["signals"] = [s.to_dict() for s in signals]
        body["turn_state"] = "idle"
        assert group_of(body) == "problems"
        advice = recommend(row, signals, None)
        assert advice.argv[1] == "context"


# -- the step: the weekly hold ----------------------------------------------------------------


class Hands:
    def __init__(self):
        self.calls: list[tuple] = []
        self.nudge_code = ("typed", "")
        self.close_error: Exception | None = None

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
        return {"failed": entry, "triage": "Q90", "retry": "Q91" if retry else None}

    def nudge(self, tty, text):
        self.calls.append(("nudge", tty, text))
        return self.nudge_code

    def close(self, host):
        self.calls.append(("close", host.get("pid")))
        if self.close_error is not None:
            raise self.close_error
        return ["closed"]

    def requeue(self, key, entry):
        self.calls.append(("requeue", key, entry))
        return entry

    def kinds(self):
        return [c[0] for c in self.calls]

    def as_hands(self):
        return ChainHands(start=self.start, begin=self.begin, mark=self.mark, fail=self.fail,
                          nudge=self.nudge, close=self.close, requeue=self.requeue)


class Inputs:
    def __init__(self, queue=None, settings=None):
        self.queue = queue if queue is not None else Queue()
        self.ready = []
        self.settings = settings or ChainSettings()


def _queue(*refs, mode=None):
    q = Queue()
    for ref in refs:
        q.add(ref, added_by="user", mode=mode)
    return q


def _step(home, hands, inputs=None, notifier=None, reading="fresh"):
    holder = {"inputs": inputs or Inputs(), "reading": reading}

    def read():
        value = holder["reading"]
        if value == "fresh":
            # read "now" whatever the simulated clock says: never stale
            return _reading(10.0, read_at=10.0**12)
        return value

    step = ChainStep(home, hands.as_hands(),
                     notifier if notifier is not None else RecordingNotifier(),
                     reader=lambda root: holder["inputs"],
                     session_end=lambda root, sid: "live",
                     pid_alive=lambda pid: True, log=lambda message: None,
                     usage=read, weekly_max=95)
    return step, holder


def _doc(rows=(), key=KEY):
    return {"projects": [{"project": {"key": key, "name": "demo", "root": "/work/demo"},
                          "rows": list(rows)}]}


def _ledger(home):
    return json.loads((home / "oracle" / "chain.json").read_text())


@pytest.fixture
def home(tmp_path) -> Path:
    path = tmp_path / "home"
    path.mkdir()
    return path


class TestTheWeeklyHold:
    def test_a_fresh_low_reading_starts(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("IDEA_1")))
        assert [a["action"] for a in step.step(_doc(), NOON)] == ["started"]

    def test_no_reading_holds_with_one_notice(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, _ = _step(home, hands, Inputs(_queue("IDEA_1")), notifier, reading=None)
        first = step.step(_doc(), NOON)
        assert [a["action"] for a in first] == ["held"]
        assert "weekly usage cannot be read" in first[0]["detail"]
        assert step.step(_doc(), NOON + 5) == []
        assert hands.calls == []
        assert len(notifier.sent) == 1
        assert _ledger(home)["usage"]["held"]["kind"] == "unknown"

    def test_a_proposal_is_not_made_under_the_hold(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, _ = _step(home, hands, Inputs(_queue("BUG_1", mode="propose")), notifier,
                        reading=_reading(99, read_at=NOON))
        actions = step.step(_doc(), NOON)
        assert [a["action"] for a in actions] == ["held"]
        assert "99%" in actions[0]["detail"]
        assert _ledger(home)["projects"][KEY]["proposed"] == {}

    def test_the_hold_lifts_and_a_new_one_notifies_again(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step, holder = _step(home, hands, Inputs(_queue("a", "b")), notifier, reading=None)
        step.step(_doc(), NOON)
        holder["reading"] = _reading(10, read_at=NOON + 10)
        assert [a["action"] for a in step.step(_doc(), NOON + 10)] == ["started"]
        assert _ledger(home)["usage"]["held"] is None

    def test_nothing_to_start_says_nothing(self, home):
        notifier = RecordingNotifier()
        step, _ = _step(home, Hands(), Inputs(), notifier, reading=None)
        assert step.step(_doc(), NOON) == []
        assert notifier.sent == []

    def test_a_greenlight_lifts_an_unknown_hold(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("a")), reading=None)
        step.step(_doc(), NOON)
        out = chainstep.greenlight(home, NOON + 1, None, 95)
        assert out["greenlit"] is True
        assert [a["action"] for a in step.step(_doc(), NOON + 2)] == ["started"]

    def test_a_reading_after_the_greenlight_spends_it(self, home):
        chainstep.greenlight(home, NOON, None, 95)
        step, _ = _step(home, Hands(), Inputs(), reading=_reading(10, read_at=NOON + 5))
        step.step(_doc(), NOON + 6)
        assert _ledger(home)["usage"]["greenlight"] is None

    def test_a_greenlight_over_the_cap_is_refused(self, home):
        with pytest.raises(InvariantError, match="at or over"):
            chainstep.greenlight(home, NOON, _reading(97, read_at=NOON), 95)

    def test_a_greenlight_with_nothing_held_records_nothing(self, home):
        out = chainstep.greenlight(home, NOON, _reading(5, read_at=NOON), 95)
        assert out["greenlit"] is False
        assert not (home / "oracle" / "chain.json").exists()

    def test_limits_carry_the_hold(self, home):
        out = chainstep.limits(home, KEY, NOON, None, 95)
        assert out["usage"].startswith("the plan's weekly usage cannot be read")
        chainstep.greenlight(home, NOON, None, 95)
        assert chainstep.limits(home, KEY, NOON + 1, None, 95)["usage"] is None
        assert chainstep.limits(home, KEY, NOON)["usage"] is None

    def test_status_shows_the_usage_block(self, home):
        chainstep.greenlight(home, NOON, None, 95)
        assert chainstep.status(home, NOON)["usage"]["greenlight"]["at_epoch"] == NOON


# -- the step: stalls, the nudge, the verbs ------------------------------------------------------


def _chained_row(quiet=3 * HOUR, cause="unknown", tty="ttys004", resets=None, report=None,
                 now=NOON, stall_after=2.0, stalled=True):
    last = now - quiet
    host = {"host_session_id": "h1", "current": True, "launched_name": "demo-IDEA_1",
            "pid": 4242, "alive": True, "tty": tty, "process_start": "2026-11-18T09:00:00",
            "turn_end": {"error": "rate_limit", "limit": "five_hour", "resets_at": resets}
            if cause == "limit" else None}
    row = {"kind": "session", "session_id": "s9", "name": "work", "state": "working",
           "hosts": [host], "last_activity": last, "stall_after": stall_after,
           "signals": ([{"kind": "stalled", "cause": cause, "alert": True}]
                       if stalled else [])}
    if report is not None:
        row["report"] = report
    return row


def _started(home, hands, notifier=None):
    step, holder = _step(home, hands, Inputs(_queue("IDEA_1")), notifier)
    step.step(_doc(), NOON - 5 * HOUR)
    holder["inputs"] = Inputs()
    # bind it
    step.step(_doc([_chained_row(quiet=0, stalled=False, now=NOON - 5 * HOUR + 10)]),
              NOON - 5 * HOUR + 10)
    return step


class TestTheNudge:
    def test_a_stalled_chained_session_gets_one_continue(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step = _started(home, hands, notifier)
        actions = step.step(_doc([_chained_row()]), NOON)
        assert ("nudge", "ttys004", "continue") in hands.calls
        assert [a["action"] for a in actions] == ["nudged"]
        current = _ledger(home)["projects"][KEY]["current"]
        assert current["stall"]["nudge"] == "typed"
        assert current["nudged_epoch"] == NOON
        # the same stall is nudged once
        assert step.step(_doc([_chained_row()]), NOON + 5) == []
        assert hands.kinds().count("nudge") == 1

    def test_still_stalled_one_fuse_after_the_nudge_is_a_notice(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step = _started(home, hands, notifier)
        step.step(_doc([_chained_row()]), NOON)
        later = NOON + 2 * HOUR
        actions = step.step(_doc([_chained_row(quiet=5 * HOUR, now=later)]), later)
        assert [a["action"] for a in actions] == ["still stalled"]
        assert "oracle chain retry demo" in actions[0]["detail"]
        assert step.step(_doc([_chained_row(quiet=5 * HOUR, now=later)]), later + 5) == []

    def test_a_new_stall_soon_after_the_nudge_is_the_same_stall(self, home):
        hands = Hands()
        step = _started(home, hands)
        step.step(_doc([_chained_row()]), NOON)
        # the nudge woke it for a minute, then it went quiet again
        step.step(_doc([_chained_row(stalled=False, quiet=0, now=NOON + 60)]), NOON + 60)
        assert _ledger(home)["projects"][KEY]["current"]["stall"] is None
        again = NOON + 60 + 2 * HOUR
        actions = step.step(_doc([_chained_row(quiet=2 * HOUR, now=again)]), again)
        assert [a["action"] for a in actions] == ["still stalled"]
        assert hands.kinds().count("nudge") == 1

    def test_a_stall_after_a_long_recovery_is_nudged_again(self, home):
        hands = Hands()
        step = _started(home, hands)
        step.step(_doc([_chained_row()]), NOON)
        step.step(_doc([_chained_row(stalled=False, quiet=0, now=NOON + 3 * HOUR)]),
                  NOON + 3 * HOUR)
        again = NOON + 5 * HOUR
        step.step(_doc([_chained_row(quiet=2 * HOUR, now=again)]), again)
        assert hands.kinds().count("nudge") == 2

    def test_a_limit_stop_waits_for_its_reset(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step = _started(home, hands, notifier)
        row = _chained_row(cause="limit", resets=NOON + 600)
        actions = step.step(_doc([row]), NOON)
        assert [a["action"] for a in actions] == ["stalled"]
        assert "after its reset" in actions[0]["detail"]
        assert "nudge" not in hands.kinds()
        assert step.step(_doc([row]), NOON + 5) == []
        step.step(_doc([row]), NOON + 600 + chainstep.RESET_MARGIN_SECONDS)
        assert "nudge" in hands.kinds()

    def test_no_tab_no_nudge_and_the_user_is_told(self, home):
        hands, notifier = Hands(), RecordingNotifier()
        step = _started(home, hands, notifier)
        actions = step.step(_doc([_chained_row(tty=None)]), NOON)
        assert [a["action"] for a in actions] == ["not nudged"]
        assert "cannot be found" in actions[0]["detail"]
        assert "nudge" not in hands.kinds()

    def test_a_nudge_that_fails_is_said(self, home):
        hands = Hands()
        hands.nudge_code = ("no_claude", "only a shell runs there")
        step = _started(home, hands)
        actions = step.step(_doc([_chained_row()]), NOON)
        assert [a["action"] for a in actions] == ["not nudged"]
        assert "only a shell" in actions[0]["detail"]

    def test_a_nudge_that_raises_is_said(self, home):
        hands = Hands()

        def boom(tty, text):
            raise OSError("osascript is missing")

        hands.nudge = boom
        step = _started(home, hands)
        actions = step.step(_doc([_chained_row()]), NOON)
        assert [a["action"] for a in actions] == ["not nudged"]

    def test_the_nudge_types_under_the_tabs_typist_lock(self, home):
        """ADR-029 D6: the chain's nudge and the page's talk path take one lock
        per tab, so they never type into it at once."""
        from invariant.core.errors import StateError
        from invariant.oracle.talk import typist

        hands = Hands()
        held, paused = [], []

        def nudge(tty, text):
            try:
                with typist(home, tty, wait=0.05):
                    held.append(False)
            except StateError:
                held.append(True)
            return "typed", ""

        hands.nudge = nudge
        step = _started(home, hands)
        step.pause = paused.append
        step.step(_doc([_chained_row()]), NOON)
        assert held == [True] and paused == [0.5]

    def test_a_tab_another_typist_holds_is_not_nudged(self, home, monkeypatch):
        import invariant.oracle.chainstep as chainstep_module
        from invariant.oracle.talk import TYPIST_WAIT, typist

        hands = Hands()
        step = _started(home, hands)
        assert TYPIST_WAIT == 10.0
        monkeypatch.setattr(chainstep_module, "typist",
                            lambda h, tty: typist(h, tty, wait=0.05))
        with typist(home, "ttys004"):
            actions = step.step(_doc([_chained_row()]), NOON)
        assert [a["action"] for a in actions] == ["not nudged"]
        assert "nudge" not in hands.kinds()

    def test_a_session_that_reported_is_not_nudged(self, home):
        hands = Hands()
        step = _started(home, hands)
        step.step(_doc([_chained_row(report={"outcome": "done", "at": NOON})]), NOON)
        assert "nudge" not in hands.kinds()

    def test_a_hand_started_session_is_never_nudged(self, home):
        hands = Hands()
        step, _ = _step(home, hands)
        row = _chained_row()
        row["hosts"][0]["launched_name"] = "mine"
        row["hosts"][0]["pid"] = 1
        step.step(_doc([row]), NOON)
        assert "nudge" not in hands.kinds()

    def test_hands_without_a_nudge_cannot_type(self, home):
        hands = Hands()
        step = _started(home, hands)
        step.hands = ChainHands(start=hands.start, begin=hands.begin, mark=hands.mark,
                                fail=hands.fail)
        actions = step.step(_doc([_chained_row()]), NOON)
        assert [a["action"] for a in actions] == ["not nudged"]


class TestTheVerbs:
    def _stalled_step(self, home, hands):
        step = _started(home, hands)
        step.step(_doc([_chained_row()]), NOON)
        return step

    def test_continue_types_again_and_rearms_the_fuse(self, home):
        hands = Hands()
        step = self._stalled_step(home, hands)
        later = NOON + 2 * HOUR
        step.step(_doc([_chained_row(quiet=5 * HOUR, now=later)]), later)
        actions = step.stall_action(KEY, "demo", "continue", _chained_row()["hosts"][0],
                                    later + 10)
        assert [a["action"] for a in actions] == ["continued"]
        assert hands.kinds().count("nudge") == 2
        stall = _ledger(home)["projects"][KEY]["current"]["stall"]
        assert (stall["still_said"], stall["nudged_epoch"]) == (False, later + 10)

    def test_continue_without_a_tab_is_refused(self, home):
        step = self._stalled_step(home, Hands())
        with pytest.raises(InvariantError, match="tab"):
            step.stall_action(KEY, "demo", "continue", None, NOON + 1)

    def test_continue_that_cannot_type_is_an_error(self, home):
        hands = Hands()
        step = self._stalled_step(home, hands)
        hands.nudge_code = ("not_allowed", "Automation")
        with pytest.raises(InvariantError, match="Automation"):
            step.stall_action(KEY, "demo", "continue", _chained_row()["hosts"][0], NOON + 1)

    def test_close_closes_and_fails_the_entry_without_triage(self, home):
        hands = Hands()
        step = self._stalled_step(home, hands)
        actions = step.stall_action(KEY, "demo", "close", _chained_row()["hosts"][0], NOON + 1)
        assert [a["action"] for a in actions] == ["closed"]
        assert ("close", 4242) in hands.calls
        assert ("mark", KEY, "Q1", "failed", None, chainstep.CLOSED_BY_USER) in hands.calls
        assert "fail" not in hands.kinds()
        state = _ledger(home)["projects"][KEY]
        assert state["current"] is None and state["paused"] is None
        assert state["released"] == ["s9"]
        assert state["starts"][-1]["outcome"] == "failed"

    def test_retry_requeues_the_same_entry(self, home):
        hands = Hands()
        step = self._stalled_step(home, hands)
        actions = step.stall_action(KEY, "demo", "retry", _chained_row()["hosts"][0], NOON + 1)
        assert [a["action"] for a in actions] == ["retried"]
        assert ("requeue", KEY, "Q1") in hands.calls
        assert _ledger(home)["projects"][KEY]["released"] == ["s9"]

    def test_a_gone_window_is_not_closed_again(self, home):
        hands = Hands()
        step = self._stalled_step(home, hands)
        step.stall_action(KEY, "demo", "close", None, NOON + 1)
        assert "close" not in hands.kinds()

    def test_a_close_that_fails_changes_nothing(self, home):
        hands = Hands()
        hands.close_error = InvariantError("Claude did not exit")
        step = self._stalled_step(home, hands)
        with pytest.raises(InvariantError, match="did not exit"):
            step.stall_action(KEY, "demo", "close", _chained_row()["hosts"][0], NOON + 1)
        assert _ledger(home)["projects"][KEY]["current"]["entry"] == "Q1"

    def test_the_verbs_need_a_recorded_stall(self, home):
        hands = Hands()
        step = _started(home, hands)
        with pytest.raises(InvariantError, match="not stalled"):
            step.stall_action(KEY, "demo", "close", None, NOON)
        with pytest.raises(InvariantError, match="no chained start"):
            step.stall_action("/state/other", "other", "close", None, NOON)
        with pytest.raises(ValueError):
            step.stall_action(KEY, "demo", "explode", None, NOON)


# -- ruling 13: a session the chain has finished with releases its project ------------------


class TestTheRelease:
    def test_after_close_the_next_work_starts_beside_the_parked_session(self, home):
        hands = Hands()
        step, holder = _step(home, hands, Inputs(_queue("IDEA_1")))
        step.step(_doc(), NOON - 5 * HOUR)
        bound = _chained_row(quiet=0, stalled=False, now=NOON - 5 * HOUR + 10)
        step.step(_doc([bound]), NOON - 5 * HOUR + 10)
        step.step(_doc([_chained_row()]), NOON)
        step.stall_action(KEY, "demo", "close", None, NOON + 1)
        holder["inputs"] = Inputs(_queue("IDEA_2"))
        parked = _chained_row(stalled=False)
        parked["hosts"][0]["alive"] = False
        actions = step.step(_doc([parked]), NOON + 10)
        assert [a["action"] for a in actions] == ["started"]

    def test_a_window_gone_failure_releases_the_session(self, home):
        hands = Hands()
        step, holder = _step(home, hands, Inputs(_queue("IDEA_1")))
        step.step(_doc(), NOON)
        row = _chained_row(quiet=0, stalled=False, now=NOON + 10)
        step.step(_doc([row]), NOON + 10)
        holder["inputs"] = Inputs(_queue("triage Q1"))
        row["hosts"][0]["alive"] = False
        step.step(_doc([row]), NOON + 20)
        actions = step.step(_doc([row]), NOON + 100)
        assert [a["action"] for a in actions] == ["failed", "started"]
        assert _ledger(home)["projects"][KEY]["released"] == ["s9"]

    def test_a_hand_started_session_still_holds(self, home):
        hands = Hands()
        step, _ = _step(home, hands, Inputs(_queue("IDEA_1")))
        assert step.step(_doc([_chained_row(stalled=False)]), NOON) == []

    def test_limits_name_the_released_sessions(self, home):
        (home / "oracle").mkdir()
        (home / "oracle" / "chain.json").write_text(json.dumps(
            {"version": 1, "projects": {KEY: {"released": ["s1"]}}}))
        assert chainstep.limits(home, KEY, NOON)["released"] == ["s1"]


# -- the queue's requeue --------------------------------------------------------------------------


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
    assert main(["init", "--home", "repo", "--name", "demo"]) == 0
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "manifest")
    return root


def _domain(root: Path) -> QueueDomain:
    manifest = Manifest.locate(root)
    return QueueDomain(manifest, JsonStore(manifest.state_root))


class TestRequeue:
    def test_a_started_entry_goes_back_to_the_head(self, project):
        domain = _domain(project)
        domain.add("one")
        domain.add("two")
        domain.begin("Q2", "two")
        domain.mark("Q2", "started", session="s1")
        entry = domain.requeue("Q2")
        assert (entry.state, entry.session, entry.retry_of) == ("queued", None, None)
        assert [e.id for e in domain.list().entries] == ["Q2", "Q1"]


class TestTheSetting:
    def test_stall_after_is_read_and_checked(self, tmp_path):
        m = Manifest.from_raw({}, root=tmp_path, path=tmp_path / "m.yaml", env={})
        assert m.get("oracle.chain.stall_after") == 2
        m = Manifest.from_raw({"oracle": {"chain": {"stall_after": 0.5}}}, root=tmp_path,
                              path=tmp_path / "m.yaml", env={})
        assert m.get("oracle.chain.stall_after") == 0.5
        for bad in (0, -1, True, "2h"):
            with pytest.raises(ManifestError, match="stall_after"):
                Manifest.from_raw({"oracle": {"chain": {"stall_after": bad}}}, root=tmp_path,
                                  path=tmp_path / "m.yaml", env={})


# -- the commands -----------------------------------------------------------------------------------


def _json(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


class TestTheCommands:
    def test_hook_statusline_records_and_prints(self, tmp_path, monkeypatch, capsys):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_payload())))
        assert main(["hook", "statusline"]) == 0
        assert capsys.readouterr().out.strip() == "week 41% · 5h 12%"
        home = Path(__import__("os").environ["INVARIANT_HOME"])
        assert usage.read_usage(home).weekly == 41.0

    def test_hook_statusline_without_a_reading_prints_and_records_nothing(
            self, monkeypatch, capsys):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
        assert main(["hook", "statusline"]) == 0
        assert capsys.readouterr().out.strip() == "usage ?"

    def test_hooks_statusline_installs(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
        assert main(["hooks", "statusline"]) == 0
        assert "installed" in capsys.readouterr().out
        assert main(["hooks", "statusline"]) == 0
        assert "already" in capsys.readouterr().out
        settings = tmp_path / "claude" / "settings.json"
        settings.write_text(json.dumps({"statusLine": {"type": "command", "command": "x"}}))
        assert main(["hooks", "statusline"]) == 2

    def test_greenlight_and_status(self, project, capsys):
        assert main(["oracle", "chain", "greenlight", "--json"]) == 0
        assert _json(capsys)["greenlit"] is True
        assert main(["oracle", "chain", "status", "--json"]) == 0
        out = _json(capsys)
        assert out["usage"]["hold"] is None and out["weekly_usage_max"] == 95
        assert main(["oracle", "chain", "status"]) == 0
        assert "weekly usage" in capsys.readouterr().out

    def test_next_shows_the_hold(self, project, capsys):
        assert main(["queue", "add", "work"]) == 0
        capsys.readouterr()
        assert main(["oracle", "next", "demo", "--json"]) == 0
        out = _json(capsys)
        assert out["limit"] == "weekly_usage"
        assert out["facts"]["usage"]["weekly"] is None

    def test_the_stall_verbs_refuse_without_a_stall(self, project, capsys):
        for verb in ("continue", "retry", "close"):
            assert main(["oracle", "chain", verb, "demo"]) == 2
            assert "no chained start" in capsys.readouterr().err

    def test_the_stall_verbs_find_the_host_and_act(self, project, capsys, monkeypatch):
        home = Path(__import__("os").environ["INVARIANT_HOME"])
        key = cli_main._project_index().match("demo").key
        _domain(project).add("work")
        _domain(project).begin("Q1", "work")
        (home / "oracle").mkdir(exist_ok=True)
        (home / "oracle" / "chain.json").write_text(json.dumps({"version": 1, "projects": {
            key: {"current": {"id": "C1", "state": "running", "entry": "Q1", "ref": "work",
                              "name": "demo-work", "pid": 4242, "session": "s9",
                              "stall": {"since_epoch": 1.0, "nudge": "typed"}},
                  "starts": [{"id": "C1", "entry": "Q1"}]}}}))
        host = {"host_session_id": "h1", "current": True, "pid": 4242, "alive": True,
                "tty": "ttys004", "process_start": "x"}
        monkeypatch.setattr(cli_main, "_chain_stalled_host", lambda index, entry, current: host)
        typed, closed = [], []
        monkeypatch.setattr(cli_main, "_chain_typer",
                            lambda: lambda tty, text: typed.append(tty) or ("typed", ""))
        monkeypatch.setattr(cli_main, "_chain_closer",
                            lambda index: lambda h: closed.append(h["pid"]) or ["closed it"])
        assert main(["oracle", "chain", "continue", "demo"]) == 0
        assert typed == ["ttys004"]
        assert "continued" in capsys.readouterr().out
        assert main(["oracle", "chain", "retry", "demo", "--json"]) == 0
        assert _json(capsys)["actions"][0]["action"] == "retried"
        assert closed == [4242]
        assert _domain(project).list().find("Q1").state == "queued"


class TestTheTyper:
    def test_types_through_osascript(self):
        calls = []

        class Done:
            returncode, stdout, stderr = 0, "typed\n", ""

        def run(argv, **kw):
            calls.append(argv)
            return Done()

        assert launch.TerminalTyper(run=run).type("ttys004", "continue") == ("typed", "")
        assert calls[0][0] == "osascript" and calls[0][-2:] == ["/dev/ttys004", "continue"]

    def test_refuses_a_bad_tty_and_reports_failures(self):
        typer = launch.TerminalTyper(run=lambda *a, **k: None)
        assert typer.type("../x", "continue")[0] == "no_tab"

        class Said:
            def __init__(self, code, out, err=""):
                self.returncode, self.stdout, self.stderr = code, out, err

        assert launch.TerminalTyper(run=lambda *a, **k: Said(0, "no_claude zsh")).type(
            "ttys1", "c") == ("no_claude", "zsh")
        assert launch.TerminalTyper(run=lambda *a, **k: Said(1, "", "error -1743")).type(
            "ttys1", "c")[0] == "not_allowed"
        assert launch.TerminalTyper(run=lambda *a, **k: Said(1, "", "boom")).type(
            "ttys1", "c") == ("failed", "boom")
        assert launch.TerminalTyper(run=lambda *a, **k: Said(0, "")).type(
            "ttys1", "c")[0] == "failed"

        def timeout(*a, **k):
            raise subprocess.TimeoutExpired("osascript", 30)

        def oserror(*a, **k):
            raise OSError("no osascript")

        assert launch.TerminalTyper(run=timeout).type("ttys1", "c")[0] == "not_allowed"
        assert launch.TerminalTyper(run=oserror).type("ttys1", "c") == (
            "failed", "no osascript")


class TestTheCliSeams:
    def test_the_closer_says_a_gone_window_and_closes_a_live_one(self, monkeypatch):
        class Seams:
            def __init__(self, alive):
                self._alive = alive

            def alive(self, pid):
                return self._alive

        asked = []
        monkeypatch.setattr(cli_main, "_oracle_host_seams", lambda: Seams(False))
        close = cli_main._chain_closer(None)
        assert close({"pid": 7}) == ["its window had already closed"]
        assert close({"pid": None}) == ["its window had already closed"]
        monkeypatch.setattr(cli_main, "_oracle_host_seams", lambda: Seams(True))
        monkeypatch.setattr(
            "invariant.oracle.closehost.close_host",
            lambda request, seams, bound: asked.append((request, bound())) or ["ended"])
        assert cli_main._chain_closer(None)({"pid": 7, "host_session_id": "h1",
                                            "process_start": "s", "tty": "ttys1"}) == ["ended"]
        request, bound = asked[0]
        assert (request.host_id, request.pid, request.start, request.tty, bound) == (
            "h1", 7, "s", "ttys1", None)

    def test_the_stalled_host_is_the_sessions_live_current_host(self, project, monkeypatch):
        index = cli_main._project_index()
        entry = index.match("demo")
        assert cli_main._chain_stalled_host(index, entry, {"session": "s9"}) is None
        live = {"host_session_id": "h1", "current": True, "alive": True}
        document = {"projects": [{"rows": [
            {"kind": "host", "session_id": None, "hosts": [live]},
            {"kind": "session", "session_id": "s8", "hosts": [live]},
            {"kind": "session", "session_id": "s9", "hosts": [
                {"host_session_id": "h0", "current": False, "alive": True}, live]}]}]}
        monkeypatch.setattr("invariant.oracle.recommend.assess", lambda snaps, now: document)
        assert cli_main._chain_stalled_host(index, entry, {"session": "s9"}) == live
        live["alive"] = None
        assert cli_main._chain_stalled_host(index, entry, {"session": "s9"}) is None

    def test_the_real_hands_carry_the_typer_closer_and_requeue(self, project, monkeypatch):
        monkeypatch.setattr(cli_main, "_chain_typer", lambda: "typer")
        hands = cli_main._real_chain_hands(cli_main._project_index())
        assert hands.nudge == "typer" and hands.close is not None
        key = cli_main._project_index().match("demo").key
        _domain(project).add("work")
        _domain(project).begin("Q1", "work")
        assert hands.requeue(key, "Q1").state == "queued"

    def test_the_real_typer_is_the_terminal_typer(self):
        assert cli_main._real_chain_typer().__self__.__class__ is launch.TerminalTyper
