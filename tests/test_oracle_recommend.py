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


# -- close host (IDEA_027 increment 6, ADR-009 D15) -------------------------------------

START = "2026-10-20T09:00:00"


def leftover(previous: str | None = "s0", adoptable=(), claimed: str | None = None,
             idle: float = 2 * 3600.0, **host_fields) -> Row:
    """An orphan host every guard lets close: alive by its record, idle, rooted here."""
    view = host(None, hid="h7")
    fields = {"pid": 41001, "pid_source": "record", "process_start": START,
              "tty": "ttys004", "cwd": ROOT, "rooted_here": True}
    fields.update(host_fields)
    for name, value in fields.items():
        setattr(view, name, value)
    return Row(kind=ROW_HOST, project=PROJECT, project_name="alpha", root=ROOT, hosts=[view],
               last_activity=NOW - idle, previous_session=previous,
               adoptable=list(adoptable), claimed_elsewhere=claimed)


CLOSE = ("invariant", "oracle", "close-host", "h7", "--pid", "41001", "--start", START,
         "--tty", "ttys004")


class TestCloseHost:
    def test_a_leftover_is_recommended_close_with_every_fact_to_check_again(self):
        advice = advise(leftover())
        assert advice.kind == rec.RUN
        assert advice.argv == CLOSE
        assert advice.cwd == ROOT
        assert advice.env == {}

    def test_the_close_command_parses_with_the_real_cli(self):
        parsed = build_parser().parse_args(list(CLOSE[1:]))
        assert callable(parsed.func)
        assert (parsed.host_id, parsed.pid, parsed.start, parsed.tty) == (
            "h7", 41001, START, "ttys004")

    def test_a_host_with_no_terminal_closes_without_a_tty(self):
        advice = advise(leftover(tty=None))
        assert advice.argv == CLOSE[:-2]
        build_parser().parse_args(list(advice.argv[1:]))

    def test_a_pid_both_sources_name_closes(self):
        assert advise(leftover(pid_source="both")).argv == CLOSE

    def test_close_comes_before_adopt_on_a_leftover(self):
        assert advise(leftover(adoptable=["s4"])).argv == CLOSE

    def test_an_unmanaged_host_closes_when_nothing_could_adopt_it(self):
        assert advise(leftover(previous=None)).argv == CLOSE

    def test_an_unmanaged_host_with_one_session_to_join_is_offered_adopt(self):
        advice = advise(leftover(previous=None, adoptable=["s4"]))
        assert advice.argv == ("invariant", "session", "adopt", "h7", "--id", "s4")

    @pytest.mark.parametrize(
        "row",
        [
            pytest.param(leftover(pid_source="attention"), id="pid-from-the-attention-record"),
            pytest.param(leftover(pid_source="conflict", pid=None, alive=None,
                                  pid_conflict=[{"source": "record", "pid": 1, "alive": True}]),
                         id="pid-conflict"),
            pytest.param(leftover(pid_conflict=[{"source": "record", "pid": 1, "alive": True}]),
                         id="a-listed-conflict"),
            pytest.param(leftover(process_start=None), id="no-start-token"),
            pytest.param(leftover(rooted_here=False), id="started-in-another-project"),
            pytest.param(leftover(rooted_here=None), id="no-recorded-cwd"),
            pytest.param(leftover(claimed="beta"), id="a-live-session-elsewhere-holds-it"),
            pytest.param(leftover(idle=300), id="recently-active"),
            pytest.param(leftover(alive=False), id="gone"),
        ],
    )
    def test_no_close_unless_every_guard_holds(self, row):
        advice = advise(row)
        assert advice is None or "close-host" not in advice.argv

    def test_assess_marks_a_host_a_live_session_elsewhere_holds(self):
        orphan = leftover()
        holder = session_row(project="/state/beta", project_name="beta", session_id="b1",
                             hosts=[host(None, hid="h7")])
        alpha = ProjectSnapshot(project=ProjectEntry(PROJECT, ROOT, "alpha", "manual"),
                                rows=[orphan])
        beta = ProjectSnapshot(project=ProjectEntry("/state/beta", "/repo/beta", "beta",
                                                    "manual"), rows=[holder])
        document = assess([alpha, beta], NOW)
        [row] = document["projects"][0]["rows"]
        assert row["claimed_elsewhere"] == "beta"
        assert "close-host" not in (row["recommendation"] or {}).get("argv", [])


# -- attach only when the match is unique both ways (IDEA_027 inc 6 follow-up) ---------


def contender(hid: str, pid: int, **fields) -> Row:
    """An unmanaged host every close guard lets go, that session s4 could take."""
    return leftover(previous=None, adoptable=["s4"], host_session_id=hid, pid=pid, **fields)


def assessed(*rows: Row) -> list[dict]:
    snapshot = ProjectSnapshot(project=ProjectEntry(PROJECT, ROOT, "alpha", "manual"),
                               rows=list(rows), scanned_at=NOW)
    return assess([snapshot], NOW)["projects"][0]["rows"]


def argv_of(row: dict) -> tuple[str, ...]:
    return tuple((row["recommendation"] or {}).get("argv") or ())


class TestAttachIsUnique:
    def test_six_hosts_competing_for_one_session_are_each_offered_close(self):
        rows = assessed(*(contender(f"h{n}", 41000 + n) for n in range(6)))
        for n, row in enumerate(rows):
            assert row["adopt_rivals"] == 5
            assert argv_of(row)[:4] == ("invariant", "oracle", "close-host", f"h{n}")
            assert argv_of(row)[5] == str(41000 + n)

    def test_one_host_with_one_session_is_offered_attach(self):
        [row] = assessed(contender("h1", 41001))
        assert row["adopt_rivals"] == 0
        assert argv_of(row) == ("invariant", "session", "adopt", "h1", "--id", "s4")

    def test_a_gone_or_unknown_host_is_no_rival(self):
        gone = contender("h2", 41002, alive=False)
        unknown = contender("h3", 41003, alive=None)
        live, *_ = assessed(contender("h1", 41001), gone, unknown)
        assert live["adopt_rivals"] == 0
        assert argv_of(live)[:3] == ("invariant", "session", "adopt")

    def test_a_host_in_another_project_is_no_rival(self):
        other = contender("h9", 41009)
        other.project, other.project_name, other.root = "/state/beta", "beta", "/repo/beta"
        alpha = ProjectSnapshot(project=ProjectEntry(PROJECT, ROOT, "alpha", "manual"),
                                rows=[contender("h1", 41001)])
        beta = ProjectSnapshot(project=ProjectEntry("/state/beta", "/repo/beta", "beta",
                                                    "manual"), rows=[other])
        document = assess([alpha, beta], NOW)
        [row] = document["projects"][0]["rows"]
        assert argv_of(row)[:3] == ("invariant", "session", "adopt")

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"rooted_here": False}, id="started-in-another-project"),
            pytest.param({"pid_source": "attention"}, id="pid-from-the-attention-record"),
            pytest.param({"process_start": None}, id="no-start-token"),
        ],
    )
    def test_competing_hosts_that_fail_a_close_guard_get_neither(self, fields):
        for row in assessed(*(contender(f"h{n}", 41000 + n, **fields) for n in range(6))):
            assert row["recommendation"]["kind"] == rec.INFO
            assert argv_of(row) == ()

    def test_competing_hosts_a_live_session_elsewhere_holds_get_neither(self):
        hids = [f"h{n}" for n in range(6)]
        holder = session_row(project="/state/beta", project_name="beta", session_id="b1",
                             hosts=[host(None, hid=hid) for hid in hids])
        alpha = ProjectSnapshot(project=ProjectEntry(PROJECT, ROOT, "alpha", "manual"),
                                rows=[contender(hid, 41000 + n) for n, hid in enumerate(hids)])
        beta = ProjectSnapshot(project=ProjectEntry("/state/beta", "/repo/beta", "beta",
                                                    "manual"), rows=[holder])
        for row in assess([alpha, beta], NOW)["projects"][0]["rows"]:
            assert row["claimed_elsewhere"] == "beta"
            assert argv_of(row) == ()

    @pytest.mark.parametrize(
        "fields",
        [
            pytest.param({"alive": False}, id="gone"),
            pytest.param({"alive": None, "pid": None, "pid_source": "conflict",
                          "pid_conflict": [{"source": "record", "pid": 1, "alive": True}]},
                         id="pid-conflict"),
        ],
    )
    def test_a_lone_host_that_is_not_known_alive_gets_neither(self, fields):
        pid = fields.pop("pid", 41001)
        [row] = assessed(contender("h1", pid, **fields))
        assert argv_of(row) == ()

    def test_a_rival_turns_attach_into_close(self):
        assert advise(contender("h7", 41001)).argv[:3] == ("invariant", "session", "adopt")
        rivalled = contender("h7", 41001)
        rivalled.adopt_rivals = 1
        assert advise(rivalled).argv == CLOSE

    def test_a_rivalled_host_no_guard_lets_close_is_offered_nothing_to_run(self):
        row = contender("h7", 41001, rooted_here=False)
        row.adopt_rivals = 2
        advice = advise(row)
        assert advice.kind == rec.INFO and advice.argv == ()
