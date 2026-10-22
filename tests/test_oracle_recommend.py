"""``recommend``: the next command for an oracle row (IDEA_012 increment 3).

Table-driven over rows built by hand, classified with an injected clock. Every
command a recommendation names is parsed by the real CLI parser, so a flag
that does not exist fails here rather than in somebody's terminal. It never
answers a permission prompt: a waiting row is told to answer it itself.
"""

from __future__ import annotations

import pytest
from test_oracle_signals import (
    NOW,
    PROJECT,
    attention,
    host,
    iso,
    landed_pipeline,
    session_row,
    waiting,
    with_test,
)

from invariant.cli.main import build_parser
from invariant.oracle import recommend as rec
from invariant.oracle.gates import GateReport, OwedGate
from invariant.oracle.index import ProjectEntry
from invariant.oracle.recommend import Recommendation, assess, recommend
from invariant.oracle.scan import (
    ROW_ERROR,
    ROW_HOST,
    ROW_WORKTREE,
    STAGE_LANDED,
    STAGE_REVIEWED,
    PipelineView,
    ProjectSnapshot,
    Row,
)
from invariant.oracle.signals import Thresholds, classify

ROOT = "/repo/alpha"
WT = "/repo/alpha-wt"


def advise(row: Row) -> Recommendation | None:
    return recommend(row, classify(row, NOW))


CASES = [
    pytest.param(
        session_row(hosts=[host(attention(waiting=waiting("permission")))]),
        rec.INFO, (), None, {},
        id="waiting-answers-nothing",
    ),
    pytest.param(
        session_row(pipeline=with_test({"phase": "green", "passed": False, "failed": 1})),
        rec.RUN, ("invariant", "pipeline", "test"), WT, {"INVARIANT_SESSION_ID": "s1"},
        id="failed-tests",
    ),
    pytest.param(
        session_row(hosts=[host(attention(turn="ended", blocked={"rules": ["a"]}))]),
        rec.RUN, ("invariant", "pipeline", "gates"), WT, {"INVARIANT_SESSION_ID": "s1"},
        id="gate-blocked",
    ),
    pytest.param(
        session_row(
            hosts=[host(attention(turn="running", turn_since=iso(9999)))],
            last_activity=NOW - 3600,
        ),
        rec.RUN, ("invariant", "context", "--id", "s1"), WT, {},
        id="stuck",
    ),
    pytest.param(
        session_row(state="reviewed", stage=STAGE_REVIEWED),
        rec.RUN, ("invariant", "session", "merge", "--id", "s1"), ROOT, {},
        id="reviewed-merges-from-the-main-checkout",
    ),
    pytest.param(
        session_row(plan_complete=True, pipeline=landed_pipeline()),
        rec.RUN, ("invariant", "session", "review", "--id", "s1"), WT, {},
        id="ready-for-review",
    ),
    pytest.param(
        session_row(state="merged", stage=STAGE_LANDED),
        rec.RUN, ("invariant", "session", "teardown", "--id", "s1"), ROOT, {},
        id="merged-tears-down-from-the-main-checkout",
    ),
    pytest.param(
        session_row(stranded=True, host_ended=True),
        rec.RUN, ("invariant", "session", "reap", "--dry-run"), ROOT, {},
        id="stranded-dry-run-first",
    ),
    pytest.param(
        session_row(host_ended=True, origin="/repo/alpha/sub"),
        rec.TERMINAL, ("invariant", "session", "resume", "--id", "s1"), "/repo/alpha/sub", {},
        id="parked-resumes-in-a-new-host",
    ),
    pytest.param(
        session_row(hosts=[host(attention(turn="ended"), alive=False)]),
        rec.TERMINAL, ("invariant", "session", "resume", "--id", "s1", "--force"), ROOT, {},
        id="host-gone-takes-over",
    ),
    pytest.param(
        session_row(hosts=[host(attention(degraded={"event": "stop", "error": "x"}))]),
        rec.RUN, ("invariant", "doctor"), ROOT, {},
        id="degraded-hook",
    ),
    pytest.param(
        session_row(hosts=[host(attention(waiting=waiting("idle")))]),
        rec.INFO, (), None, {},
        id="your-turn",
    ),
    pytest.param(
        Row(kind=ROW_HOST, project=PROJECT, root=ROOT, hosts=[host(None, hid="h7")],
            adoptable=["s4"]),
        rec.RUN, ("invariant", "session", "adopt", "h7", "--id", "s4"), ROOT, {},
        id="orphan-host-adopts-the-only-candidate",
    ),
    pytest.param(
        Row(kind=ROW_HOST, project=PROJECT, root=ROOT,
            hosts=[host(None, alive=None, hid="h7")], adoptable=["s4"]),
        rec.INFO, (), None, {},
        id="orphan-host-of-unknown-liveness-adopts-nothing",
    ),
    pytest.param(
        Row(kind=ROW_HOST, project=PROJECT, root=ROOT,
            hosts=[host(None, alive=False, hid="h7")], adoptable=["s4"]),
        rec.INFO, (), None, {},
        id="orphan-host-gone-adopts-nothing",
    ),
    pytest.param(
        Row(kind=ROW_HOST, project=PROJECT, root=ROOT, hosts=[host(None, hid="h7")],
            adoptable=["s4", "s5"]),
        rec.INFO, (), None, {},
        id="orphan-host-with-two-candidates-adopts-nothing",
    ),
    pytest.param(
        Row(kind=ROW_HOST, project=PROJECT, root=ROOT,
            hosts=[host(attention(waiting=waiting("permission")), hid="h7")],
            adoptable=["s4"]),
        rec.INFO, (), None, {},
        id="orphan-host-waiting-is-answered-first",
    ),
    pytest.param(
        Row(kind=ROW_WORKTREE, project=PROJECT, root=ROOT, path="/repo/stray"),
        rec.INFO, (), None, {},
        id="leaked-worktree",
    ),
    pytest.param(
        Row(kind=ROW_ERROR, project=PROJECT, root=ROOT, error="bad"),
        rec.INFO, (), None, {},
        id="scan-error",
    ),
]


@pytest.mark.parametrize(("row", "kind", "argv", "cwd", "env"), CASES)
def test_the_recommendation_table(row, kind, argv, cwd, env):
    advice = advise(row)
    assert advice is not None
    assert advice.kind == kind
    assert advice.argv == argv
    assert advice.cwd == cwd
    assert advice.env == env
    assert advice.label


@pytest.mark.parametrize(("row", "kind", "argv", "cwd", "env"), CASES)
def test_every_command_parses_with_the_real_cli(row, kind, argv, cwd, env):
    advice = advise(row)
    if not advice.argv or advice.argv[0] != "invariant":
        return
    parsed = build_parser().parse_args(list(advice.argv[1:]))
    assert callable(parsed.func)


def test_waiting_never_carries_a_command():
    advice = advise(session_row(hosts=[host(attention(waiting=waiting("question")))]))
    assert advice.argv == ()
    assert "terminal" in advice.label


def test_a_session_with_no_worktree_runs_from_the_root():
    row = session_row(worktree=None, hosts=[host(attention(turn="ended",
                                                           blocked={"rules": ["a"]}))])
    assert advise(row).cwd == ROOT


def test_a_stranded_worktree_is_not_a_cwd():
    row = session_row(stranded=True, pipeline=with_test({"passed": False, "phase": "green"}))
    assert advise(row).cwd == ROOT


def test_a_quiet_session_needs_nothing():
    assert advise(session_row()) is None


def test_failed_tests_outrank_a_blocked_gate():
    row = session_row(
        pipeline=with_test({"phase": "green", "passed": False}),
        hosts=[host(attention(turn="ended", blocked={"rules": ["test.all_pass"]}))],
    )
    assert advise(row).argv == ("invariant", "pipeline", "test")


def test_a_recommendation_serialises():
    body = Recommendation("l", ("a", "b"), "/c", rec.RUN, {"K": "v"}).to_dict()
    assert body == {"label": "l", "argv": ["a", "b"], "cwd": "/c", "kind": "run",
                    "env": {"K": "v"}, "detail": ""}


class TestAssess:
    def test_every_row_carries_its_signals_and_advice(self):
        entry = ProjectEntry(key=PROJECT, root=ROOT, name="alpha", via="manual")
        rows = [
            session_row(state="reviewed", stage=STAGE_REVIEWED),
            session_row(session_id="s2", name="task-s2"),
        ]
        snapshot = ProjectSnapshot(project=entry, rows=rows, scanned_at=NOW, elapsed=0.25)
        document = assess([snapshot], NOW, Thresholds())
        assert document["now"] == NOW
        [project] = document["projects"]
        assert project["project"]["name"] == "alpha"
        assert project["elapsed"] == 0.25
        first, second = project["rows"]
        assert first["signals"][0]["kind"] == "finished"
        assert first["recommendation"]["argv"][:3] == ["invariant", "session", "merge"]
        assert second["signals"] == []
        assert second["recommendation"] is None

    def test_the_pipeline_view_is_plain_data(self):
        entry = ProjectEntry(key=PROJECT, root=ROOT, name="alpha", via="manual")
        row = session_row(pipeline=PipelineView(methodology="tdd", state="red"))
        document = assess([ProjectSnapshot(project=entry, rows=[row])], NOW)
        assert document["projects"][0]["rows"][0]["pipeline"]["state"] == "red"


# -- gate detail (increment 5) -------------------------------------------------

ALL_PASS = OwedGate("test.all_pass", "block", "2 failed", True, ("refactor", "reviewing"))
COVERAGE = OwedGate("coverage.meets_floor", "warn", "", False, ("committed",))


def blocked_row(**fields) -> Row:
    return session_row(
        hosts=[host(attention(turn="ended", blocked={"rules": ["test.all_pass"]}))], **fields
    )


def gated(row: Row, report: GateReport | None) -> Recommendation | None:
    return recommend(row, classify(row, NOW), report)


class TestGateDetail:
    def test_a_refused_row_names_every_gate_owed_and_what_it_wants(self):
        advice = gated(blocked_row(), GateReport("green", (ALL_PASS, COVERAGE)))
        assert advice.argv == ("invariant", "pipeline", "gates")
        assert advice.detail == (
            "owed: test.all_pass (block, to refactor, reviewing): 2 failed; "
            "coverage.meets_floor (warn, to committed)"
        )

    def test_a_stale_answer_says_which_state_it_is_from(self):
        advice = gated(blocked_row(), GateReport("red", (ALL_PASS,), stale=True))
        assert advice.detail.startswith("as of red, owed: test.all_pass")

    def test_nothing_owed_says_the_refusal_may_be_cleared(self):
        advice = gated(blocked_row(), GateReport("green"))
        assert "nothing is owed now" in advice.detail

    @pytest.mark.parametrize("report", [
        GateReport("green", timed_out=True), GateReport("green", error="boom"),
    ], ids=["timed-out", "error"])
    def test_no_answer_adds_no_detail(self, report):
        assert gated(blocked_row(), report).detail == ""

    def test_without_a_report_nothing_changes(self):
        assert gated(blocked_row(), None).detail == ""
        assert gated(session_row(), None) is None

    def test_a_quiet_row_gets_the_hint_as_its_step(self):
        advice = gated(session_row(), GateReport("green", (ALL_PASS, COVERAGE)))
        assert advice.kind == rec.INFO and advice.argv == ()
        assert advice.label == "next owed: test.all_pass (to refactor)"

    def test_another_step_carries_the_hint_as_detail(self):
        row = session_row(state="reviewed", stage=STAGE_REVIEWED)
        advice = gated(row, GateReport("green", (ALL_PASS,)))
        assert advice.argv[:3] == ("invariant", "session", "merge")
        assert advice.detail == "next owed: test.all_pass (to refactor)"

    @pytest.mark.parametrize("report", [
        GateReport("green", (ALL_PASS,), stale=True), GateReport("green", (COVERAGE,)),
    ], ids=["stale", "advisory-only"])
    def test_no_hint_from_a_stale_or_advisory_answer(self, report):
        assert gated(session_row(), report) is None

    def test_gates_never_change_the_signals(self):
        entry = ProjectEntry(key=PROJECT, root=ROOT, name="alpha", via="manual")
        rows = [blocked_row(), session_row(session_id="s2", name="task-s2")]
        snapshot = ProjectSnapshot(project=entry, rows=rows, scanned_at=NOW)
        reports = {(PROJECT, "s1"): GateReport("green", (ALL_PASS,)),
                   (PROJECT, "s2"): GateReport("green", (ALL_PASS,))}
        plain = assess([snapshot], NOW)
        with_gates = assess([snapshot], NOW, gates=reports)
        for before, after in zip(plain["projects"][0]["rows"],
                                 with_gates["projects"][0]["rows"], strict=True):
            assert before["signals"] == after["signals"]
            assert before["gates"] is None
            assert after["gates"]["owed"][0]["rule_id"] == "test.all_pass"
