"""``classify``: what one oracle row means for the person (IDEA_012 increment 3).

Every case here builds a row by hand and passes the clock in, so nothing reads
a store, a process table or the wall clock. Alerts and show-only markers are
both covered, and so is the stable key each signal carries for the de-dup
ledger increment 4 adds.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from invariant.oracle import signals as sig
from invariant.oracle.scan import (
    ROW_ERROR,
    ROW_HOST,
    ROW_SESSION,
    ROW_WORKTREE,
    STAGE_EARLY,
    STAGE_LANDED,
    STAGE_REVIEWED,
    STAGE_WORKING,
    HostView,
    PipelineView,
    Row,
)
from invariant.oracle.signals import Signal, Thresholds, classify, signal_key

NOW = 1_800_000_000.0
PROJECT = "/state/alpha"


def iso(seconds_ago: float) -> str:
    moment = datetime.fromtimestamp(NOW - seconds_ago, tz=timezone.utc)
    return moment.isoformat(timespec="seconds")


def attention(**fields) -> dict:
    record = {
        "host_session_id": "h1",
        "pid": 4242,
        "waiting": None,
        "blocked": None,
        "turn": None,
        "turn_since": None,
        "ended": False,
        "degraded": None,
    }
    record.update(fields)
    return record


def host(record: dict | None = None, alive: bool | None = True, current=True, hid="h1"):
    return HostView(
        host_session_id=hid,
        current=current,
        pid=4242 if alive is not None else None,
        alive=alive,
        attention=record,
    )


def session_row(**fields) -> Row:
    base = {
        "kind": ROW_SESSION,
        "project": PROJECT,
        "project_name": "alpha",
        "root": "/repo/alpha",
        "session_id": "s1",
        "name": "task-s1",
        "state": "working",
        "stage": STAGE_WORKING,
        "worktree": "/repo/alpha-wt",
        "branch": "session/s1",
        "last_activity": NOW - 60,
    }
    base.update(fields)
    return Row(**base)


def waiting(kind: str, seconds_ago: float = 30, message: str = "Allow Bash?") -> dict:
    return {"kind": kind, "message": message, "since": iso(seconds_ago)}


def kinds(signals: list[Signal]) -> set[str]:
    return {s.kind for s in signals}


def only(signals: list[Signal], kind: str) -> Signal:
    found = [s for s in signals if s.kind == kind]
    assert len(found) == 1, signals
    return found[0]


# -- waiting -----------------------------------------------------------------


class TestWaiting:
    @pytest.mark.parametrize("kind", ["permission", "question"])
    def test_a_live_host_waiting_on_the_person_alerts(self, kind):
        row = session_row(hosts=[host(attention(waiting=waiting(kind)))])
        signal = only(classify(row, NOW), sig.WAITING)
        assert signal.alert is True
        assert signal.severity == sig.HIGH
        assert signal.cause == kind
        assert "Allow Bash?" in signal.detail
        assert signal.key == f"{PROJECT}|s1|waiting|{kind}@{iso(30)}"

    def test_a_host_with_no_recorded_pid_still_alerts(self):
        row = session_row(hosts=[host(attention(waiting=waiting("permission")), alive=None)])
        assert sig.WAITING in kinds(classify(row, NOW))

    def test_a_dead_host_does_not_alert_and_reads_as_gone(self):
        row = session_row(hosts=[host(attention(waiting=waiting("permission")), alive=False)])
        found = classify(row, NOW)
        assert sig.WAITING not in kinds(found)
        gone = only(found, sig.HOST_GONE)
        assert gone.alert is False
        assert "SessionEnd" in gone.detail

    def test_an_idle_host_is_your_turn_and_not_an_alert(self):
        row = session_row(hosts=[host(attention(waiting=waiting("idle")))])
        found = classify(row, NOW)
        assert sig.WAITING not in kinds(found)
        turn = only(found, sig.YOUR_TURN)
        assert turn.alert is False
        assert turn.severity == sig.INFO

    def test_a_wait_older_than_the_stale_bound_drops_to_medium(self):
        record = attention(waiting=waiting("permission", seconds_ago=7 * 3600))
        signal = only(classify(session_row(hosts=[host(record)]), NOW), sig.WAITING)
        assert signal.alert is True
        assert signal.severity == sig.MEDIUM

    def test_an_unparseable_since_stays_high(self):
        record = attention(waiting={"kind": "permission", "message": None, "since": "?"})
        signal = only(classify(session_row(hosts=[host(record)]), NOW), sig.WAITING)
        assert signal.severity == sig.HIGH
        assert "waiting on" in signal.detail

    def test_an_earlier_host_is_history_and_says_nothing(self):
        earlier = host(attention(waiting=waiting("permission")), current=False, hid="h0")
        assert classify(session_row(hosts=[earlier]), NOW) == []

    def test_an_unknown_waiting_kind_says_nothing(self):
        row = session_row(hosts=[host(attention(waiting={"kind": "sleeping"}))])
        assert classify(row, NOW) == []


# -- gate blocked --------------------------------------------------------------


class TestGateBlocked:
    def test_a_refusal_alerts_once_the_turn_has_ended(self):
        record = attention(
            turn="ended",
            blocked={"rules": ["test.all_pass", "docs.updated"], "surface": "stop"},
        )
        signal = only(classify(session_row(hosts=[host(record)]), NOW), sig.GATE_BLOCKED)
        assert signal.alert is True
        assert signal.severity == sig.MEDIUM
        assert signal.key == f"{PROJECT}|s1|gate_blocked|docs.updated,test.all_pass"
        assert "docs.updated" in signal.detail and "stop" in signal.detail

    def test_a_refusal_mid_turn_is_not_reported_yet(self):
        record = attention(turn="running", blocked={"rules": ["x"], "surface": "cli"})
        assert sig.GATE_BLOCKED not in kinds(classify(session_row(hosts=[host(record)]), NOW))

    def test_a_refusal_naming_no_rules_still_reports(self):
        record = attention(turn="ended", blocked={"rules": None, "surface": "cli"})
        signal = only(classify(session_row(hosts=[host(record)]), NOW), sig.GATE_BLOCKED)
        assert signal.key.endswith("|gate_blocked|")


# -- stuck ---------------------------------------------------------------------


class TestStuck:
    def running(self, idle_minutes: float, **fields) -> Row:
        record = attention(turn="running", turn_since=iso(3600), **fields)
        return session_row(hosts=[host(record)], last_activity=NOW - idle_minutes * 60)

    def test_a_running_turn_silent_past_the_threshold_is_stuck(self):
        signal = only(classify(self.running(21), NOW), sig.STUCK)
        assert signal.alert is True
        assert signal.key == f"{PROJECT}|s1|stuck|{iso(3600)}"
        assert "21 min" in signal.detail

    def test_under_the_threshold_is_not_stuck(self):
        assert sig.STUCK not in kinds(classify(self.running(19), NOW))

    def test_the_threshold_is_injected(self):
        found = classify(self.running(6), NOW, Thresholds(stuck_minutes=5))
        assert sig.STUCK in kinds(found)

    def test_waiting_is_not_stuck(self):
        row = self.running(60, waiting=waiting("permission"))
        found = classify(row, NOW)
        assert sig.STUCK not in kinds(found)
        assert sig.WAITING in kinds(found)

    def test_a_dead_host_is_not_stuck(self):
        record = attention(turn="running")
        row = session_row(hosts=[host(record, alive=False)], last_activity=NOW - 3600)
        assert sig.STUCK not in kinds(classify(row, NOW))

    def test_no_activity_known_is_not_stuck(self):
        record = attention(turn="running")
        row = session_row(hosts=[host(record)], last_activity=None)
        assert sig.STUCK not in kinds(classify(row, NOW))

    def test_an_ended_turn_is_not_stuck(self):
        record = attention(turn="ended")
        row = session_row(hosts=[host(record)], last_activity=NOW - 3600)
        assert sig.STUCK not in kinds(classify(row, NOW))


# -- finished --------------------------------------------------------------------


def landed_pipeline(**fields) -> PipelineView:
    base = {"methodology": "tdd", "state": "committed", "terminal": True, "landed": True}
    base.update(fields)
    return PipelineView(**base)


class TestFinished:
    def test_a_reviewed_session_is_finished_awaiting_merge(self):
        row = session_row(state="reviewed", stage=STAGE_REVIEWED)
        signal = only(classify(row, NOW), sig.FINISHED)
        assert signal.alert is True
        assert signal.severity == sig.LOW
        assert signal.cause == "reviewed"
        assert signal.key == f"{PROJECT}|s1|finished|reviewed"

    def test_working_with_a_complete_plan_and_a_landed_pipeline_is_ready(self):
        row = session_row(plan_complete=True, plan_done=3, plan_total=3,
                          pipeline=landed_pipeline())
        signal = only(classify(row, NOW), sig.FINISHED)
        assert signal.cause == "ready"
        assert "3/3" in signal.detail

    @pytest.mark.parametrize(
        "fields",
        [
            {"plan_complete": False, "pipeline": landed_pipeline()},
            {"plan_complete": True, "pipeline": landed_pipeline(landed=False)},
            {"plan_complete": True, "pipeline": None},
            {"plan_complete": True, "pipeline": landed_pipeline(), "stage": STAGE_EARLY},
        ],
    )
    def test_anything_less_is_not_finished(self, fields):
        assert sig.FINISHED not in kinds(classify(session_row(**fields), NOW))

    def test_a_landed_session_is_teardown_pending_and_not_finished(self):
        found = classify(session_row(state="merged", stage=STAGE_LANDED), NOW)
        assert kinds(found) == {sig.TEARDOWN_PENDING}
        assert found[0].alert is False


# -- error ---------------------------------------------------------------------


def with_test(run: dict, runs: int = 4, red_phase: str = "red") -> PipelineView:
    return PipelineView(
        methodology="tdd", state="green", test_runs=runs, last_test=run, red_phase=red_phase
    )


class TestError:
    def test_a_failed_last_run_alerts(self):
        run = {"phase": "green", "passed": False, "failed": 2, "read_as_red": False}
        signal = only(classify(session_row(pipeline=with_test(run)), NOW), sig.ERROR)
        assert signal.alert is True
        assert signal.severity == sig.HIGH
        assert signal.cause == "tests"
        assert signal.key == f"{PROJECT}|s1|error|tests:4"
        assert "2 failed" in signal.detail

    @pytest.mark.parametrize(
        "run",
        [
            {"phase": "green", "passed": True, "failed": 0},
            {"phase": "red", "passed": False, "failed": 1, "read_as_red": True},
            {"phase": "red", "passed": False, "failed": 1, "read_as_red": False},
        ],
    )
    def test_a_pass_or_a_red_proof_is_not_an_error(self, run):
        assert sig.ERROR not in kinds(classify(session_row(pipeline=with_test(run)), NOW))

    def test_the_red_phase_is_the_machines_own(self):
        run = {"phase": "reproducing", "passed": False, "failed": 1}
        row = session_row(pipeline=with_test(run, red_phase="reproducing"))
        assert sig.ERROR not in kinds(classify(row, NOW))

    def test_a_degraded_hook_alerts(self):
        record = attention(degraded={"event": "stop", "error": "boom", "since": iso(5)})
        signal = only(classify(session_row(hosts=[host(record)]), NOW), sig.ERROR)
        assert signal.cause == "degraded"
        assert signal.key == f"{PROJECT}|s1|error|degraded@{iso(5)}"
        assert "boom" in signal.detail

    def test_a_scan_error_row_alerts(self):
        row = Row(kind=ROW_ERROR, project=PROJECT, error="manifest is not YAML")
        signal = only(classify(row, NOW), sig.ERROR)
        assert signal.cause == "scan"
        assert signal.key == f"{PROJECT}|project|error|scan"
        assert "manifest is not YAML" in signal.detail

    def test_a_session_error_row_names_the_session(self):
        row = Row(kind=ROW_ERROR, project=PROJECT, session_id="s9", error="bad json")
        assert only(classify(row, NOW), sig.ERROR).key == f"{PROJECT}|s9|error|scan"


# -- markers -------------------------------------------------------------------


class TestMarkers:
    def test_a_parked_session(self):
        found = classify(session_row(host_ended=True), NOW)
        parked = only(found, sig.PARKED)
        assert parked.alert is False
        assert parked.severity == sig.INFO

    def test_a_stranded_session_is_not_also_parked(self):
        found = classify(session_row(host_ended=True, stranded=True), NOW)
        assert kinds(found) == {sig.STRANDED}

    def test_a_spent_binding_is_not_a_gone_host(self):
        record = attention(turn="ended")
        row = session_row(host_ended=True, hosts=[host(record, alive=False)])
        assert sig.HOST_GONE not in kinds(classify(row, NOW))

    def test_an_ended_record_is_not_a_gone_host(self):
        row = session_row(hosts=[host(attention(ended=True), alive=False)])
        assert sig.HOST_GONE not in kinds(classify(row, NOW))

    def test_a_leaked_worktree(self):
        row = Row(kind=ROW_WORKTREE, project=PROJECT, path="/repo/stray")
        signal = only(classify(row, NOW), sig.LEAKED_WORKTREE)
        assert signal.alert is False
        assert signal.key == f"{PROJECT}|worktree:/repo/stray|leaked_worktree|"

    def test_an_orphan_host_is_marked_and_still_alerts_when_waiting(self):
        record = attention(waiting=waiting("question"))
        row = Row(kind=ROW_HOST, project=PROJECT, hosts=[host(record, hid="h7")])
        found = classify(row, NOW)
        assert kinds(found) == {sig.ORPHAN_HOST, sig.WAITING}
        assert only(found, sig.WAITING).key.startswith(f"{PROJECT}|host:h7|waiting|")

    def test_an_orphan_host_bound_before_names_the_session(self):
        row = Row(kind=ROW_HOST, project=PROJECT, hosts=[host(None, hid="h7")],
                  previous_session="s0")
        assert "s0" in only(classify(row, NOW), sig.ORPHAN_HOST).detail

    def test_a_quiet_session_has_no_signals(self):
        assert classify(session_row(), NOW) == []


class TestKeys:
    def test_the_key_is_stable_across_calls(self):
        row = session_row(hosts=[host(attention(waiting=waiting("permission")))])
        first = [s.key for s in classify(row, NOW)]
        assert first == [s.key for s in classify(row, NOW + 5)]

    def test_the_key_spells_project_subject_kind_discriminator(self):
        assert signal_key(session_row(), "stuck", "x") == f"{PROJECT}|s1|stuck|x"

    def test_a_signal_serialises(self):
        signal = Signal("waiting", "high", True, "d", "k", "permission")
        assert signal.to_dict() == {
            "kind": "waiting", "severity": "high", "alert": True,
            "detail": "d", "key": "k", "cause": "permission",
        }

    def test_signals_come_alerts_first(self):
        record = attention(waiting=waiting("permission"))
        row = session_row(host_ended=False, stranded=True, hosts=[host(record)])
        found = classify(row, NOW)
        assert [s.alert for s in found] == sorted((s.alert for s in found), reverse=True)

    def test_the_default_thresholds(self):
        assert Thresholds() == Thresholds(stuck_minutes=20.0, stale_wait_hours=6.0)
        assert timedelta(minutes=Thresholds().stuck_minutes).seconds == 1200


class TestOrphanClasses:
    """IDEA_025's classes, as the orphan marker's cause (IDEA_027 increment 6).

    gone (no process), leftover (alive, its session ended, idle), unmanaged
    (alive, never bound here, idle), active (alive and recently heard from, or
    mid-turn), and unknown (no pid that can be asked, or no activity known).
    """

    @staticmethod
    def cause(row: Row, thresholds: Thresholds | None = None) -> str:
        return only(classify(row, NOW, thresholds), sig.ORPHAN_HOST).cause

    @staticmethod
    def orphan(alive: bool | None = True, idle: float | None = 2 * 3600.0,
               record: dict | None = None, previous: str | None = None) -> Row:
        return Row(kind=ROW_HOST, project=PROJECT, hosts=[host(record, alive=alive, hid="h7")],
                   last_activity=None if idle is None else NOW - idle,
                   previous_session=previous)

    def test_no_process_is_gone(self):
        assert self.cause(self.orphan(alive=False)) == sig.ORPHAN_GONE

    def test_no_pid_to_ask_is_unknown(self):
        assert self.cause(self.orphan(alive=None)) == sig.ORPHAN_UNKNOWN

    def test_alive_with_no_activity_known_is_unknown(self):
        assert self.cause(self.orphan(idle=None)) == sig.ORPHAN_UNKNOWN

    def test_idle_and_bound_to_an_ended_session_is_a_leftover(self):
        row = self.orphan(previous="s0")
        assert self.cause(row) == sig.ORPHAN_LEFTOVER
        assert "leftover" in only(classify(row, NOW), sig.ORPHAN_HOST).detail

    def test_idle_and_never_bound_is_unmanaged(self):
        assert self.cause(self.orphan()) == sig.ORPHAN_UNMANAGED

    def test_recently_heard_from_is_active(self):
        assert self.cause(self.orphan(idle=600, previous="s0")) == sig.ORPHAN_ACTIVE

    def test_a_running_turn_is_active_however_old_its_last_sign(self):
        row = self.orphan(record=attention(turn="running"), previous="s0")
        assert self.cause(row) == sig.ORPHAN_ACTIVE

    def test_the_idle_edge_is_a_threshold(self):
        row = self.orphan(idle=600)
        assert self.cause(row, Thresholds(orphan_idle_minutes=5)) == sig.ORPHAN_UNMANAGED

    def test_no_class_raises_an_alert(self):
        for alive, previous in ((False, None), (True, "s0"), (True, None)):
            found = classify(self.orphan(alive=alive, previous=previous), NOW)
            assert not any(s.alert for s in found)
