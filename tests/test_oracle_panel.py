"""The oracle's control panel view model (IDEA_027 increment 3, ADR-009 D2-D5, D8).

``oracle/panel`` is pure: it reads the ``assess`` document and answers what the
phone draws. These tests pin the four things the design of record
(docs/architecture/oracle-panel.md) asks of it:

* **the groups** and their order: needs you, problems, working, idle, with the
  quiet orphan hosts and leaked worktrees on a screen of their own;
* **the summary strip**, whose counts never move the layout (a 0 still shows);
* **the one wording table**: every chip, sentence, button and confirm title is
  spelled here, and a fact the table does not know gets no phrase;
* **nothing engine-shaped on the default view**: no path, argv, pid, host id or
  state name. Those live under "Technical details".
"""

from __future__ import annotations

import json
import re

import pytest
import test_oracle_scan

from invariant.oracle import panel
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.recommend import assess
from invariant.oracle.scan import ScanCache, scan_all

machine_world = test_oracle_scan.machine_world

NOW = 1_792_640_000.0


def iso(epoch: float) -> str:
    return test_oracle_scan.iso(epoch)


def sig(kind, cause="", alert=True, severity="high", detail="d"):
    return {"kind": kind, "cause": cause, "alert": alert, "severity": severity,
            "detail": detail, "key": f"K|s|{kind}|"}


def step(argv, kind="run", cwd="/r/.claude/worktrees/p-wt-s1", env=None, label="do it"):
    return {"label": label, "argv": list(argv), "cwd": cwd, "kind": kind,
            "env": dict(env or {}), "detail": ""}


def host(host_id="2abd173c-1111-2222-3333-444455556666", pid=4242, alive=True,
         current=True, attention=None):
    return {"host_session_id": host_id, "current": current, "pid": pid, "alive": alive,
            "attention": attention, "attention_written": None, "transcript_written": None}


def session(sid="s1", name="suggestion-band-height-s1", signals=(), recommendation=None,
            hosts=(), pipeline=None, state="isolated", stage="working", project="K",
            last_activity=None, worktree="/r/.claude/worktrees/p-wt-s1",
            branch="session/s1", heartbeat=None, plan_touched=None, host_ended=False,
            turn_state=None):
    return {
        "kind": "session", "project": project, "project_name": "Heap", "root": "/r",
        "session_id": sid, "name": name, "state": state, "stage": stage,
        "worktree": worktree, "branch": branch, "origin": "/r", "stranded": False,
        "host_ended": host_ended, "plan_done": 0, "plan_total": 0, "plan_complete": False,
        "heartbeat": heartbeat, "plan_touched": plan_touched, "pipeline": pipeline,
        "hosts": list(hosts), "last_activity": last_activity, "path": None, "error": None,
        "adoptable": [], "previous_session": None,
        "signals": list(signals), "recommendation": recommendation, "gates": None,
        "turn_state": turn_state,
    }


def orphan(host_id="9c1e02aa-0000-0000-0000-000000000000", signals=(), alive=True,
           last_activity=None):
    row = session(sid=None, name=None, signals=signals, hosts=[host(host_id, alive=alive)],
                  state=None, stage=None, worktree=None, branch=None,
                  last_activity=last_activity)
    row["kind"] = "host"
    return row


def leaked(path="/r/.claude/worktrees/p-wt-0c269de4", branch="session/0c269de4"):
    row = session(sid=None, name=None, state=None, stage=None, worktree=None, branch=branch)
    row["kind"] = "worktree"
    row["path"] = path
    row["signals"] = [sig("leaked_worktree", alert=False, severity="info")]
    return row


def pipeline(methodology="bugfix", state="fixing", terminal=False, task="", failed=None):
    last = {"phase": state, "passed": False, "failed": failed} if failed is not None else None
    return {"methodology": methodology, "state": state, "task": task, "terminal": terminal,
            "landed": False, "red_phase": "red", "test_runs": 1 if last else 0,
            "last_test": last, "transitions": 0, "written_ns": None}


def doc(*rows, name="Heap", key="K"):
    return {"now": NOW, "projects": [{"project": {"key": key, "name": name, "root": "/r"},
                                      "scanned_at": NOW, "elapsed": 0.0,
                                      "rows": list(rows)}]}


def waiting(kind="permission", since=NOW - 240, turn="running"):
    return {"waiting": {"kind": kind, "message": "Allow Bash?", "since": iso(since)},
            "turn": turn, "turn_since": iso(since - 60)}


def cards(shown, group):
    found = next((g for g in shown["groups"] if g["id"] == group), None)
    return found["cards"] if found else []


# -- groups and order ----------------------------------------------------------------


class TestGroups:
    def test_each_row_joins_the_first_group_whose_rule_holds(self):
        rows = [
            session("w", signals=[sig("waiting", "permission")],
                    hosts=[host(attention=waiting())]),
            session("f", signals=[sig("finished", "reviewed", severity="low")]),
            session("t", signals=[sig("error", "tests")], pipeline=pipeline(failed=52)),
            session("g", signals=[sig("gate_blocked", severity="medium")]),
            session("k", signals=[sig("stuck", severity="medium")]),
            session("h", signals=[sig("host_gone", alert=False, severity="info")]),
            session("x", signals=[sig("stranded", alert=False, severity="info")]),
            session("r", hosts=[host(attention={"turn": "running"})], turn_state="busy"),
            session("y", signals=[sig("your_turn", alert=False, severity="info")]),
            session("p", signals=[sig("parked", alert=False, severity="info")]),
            session("d", signals=[sig("teardown_pending", alert=False, severity="info")]),
            session("n"),
            orphan(),
            leaked(),
        ]
        shown = panel.view(doc(*rows))
        assert [g["id"] for g in shown["groups"]] == ["needs", "problems", "working", "idle"]
        ids = {g["id"]: [c["row"].split("|")[1] for c in g["cards"]] for g in shown["groups"]}
        assert sorted(ids["needs"]) == ["f", "w"]
        assert sorted(ids["problems"]) == ["g", "h", "k", "t", "x"]
        assert ids["working"] == ["r"]
        assert sorted(ids["idle"]) == ["d", "n", "p", "y"]
        assert shown["summary"] == {"needs": 2, "problems": 5, "working": 1, "leftovers": 2}

    def test_a_problem_outranks_a_running_turn(self):
        row = session("t", signals=[sig("error", "tests")], pipeline=pipeline(failed=77),
                      hosts=[host(attention={"turn": "running"})], turn_state="busy")
        shown = panel.view(doc(row))
        assert [g["id"] for g in shown["groups"]] == ["problems"]
        texts = [c["text"] for c in cards(shown, "problems")[0]["chips"]]
        assert texts == ["77 tests failing", "Claude is working"]

    def test_a_gone_host_is_not_working(self):
        row = session("t", hosts=[host(alive=False, attention={"turn": "running"})],
                      turn_state="idle")
        assert [g["id"] for g in panel.view(doc(row))["groups"]] == ["idle"]

    @pytest.mark.parametrize("state", ["idle", "waiting", None])
    def test_working_is_the_documents_turn_state_not_the_attention_turn(self, state):
        """BUG_087: a stale running turn the predicate calls idle is not working."""
        row = session("r", hosts=[host(attention={"turn": "running"})], turn_state=state)
        shown = panel.view(doc(row))
        assert [g["id"] for g in shown["groups"]] == ["idle"]
        assert "Claude is working" not in [c["text"] for c in cards(shown, "idle")[0]["chips"]]

    def test_a_host_only_its_own_status_says_is_waiting_needs_you(self):
        row = orphan(signals=[sig("waiting", "input")])
        row["turn_state"] = "waiting"
        shown = panel.view(doc(row))
        assert [g["id"] for g in shown["groups"]] == ["needs"]
        card = cards(shown, "needs")[0]
        assert [c["text"] for c in card["chips"]] == ["Waiting for you"]
        assert card["button"] is None and card["tap"] is True
        going = shown["details"][card["row"]]["going_on"][0]
        assert going == ("Claude is waiting for you in its window. Answer it in Claude "
                         "on your Mac or phone. This panel never answers prompts.")

    def test_an_orphan_host_that_alerts_stays_in_its_group(self):
        row = orphan(signals=[sig("waiting", "question")])
        row["hosts"][0]["attention"] = waiting("question")
        shown = panel.view(doc(row))
        assert [g["id"] for g in shown["groups"]] == ["needs"]
        assert shown["summary"]["leftovers"] == 0
        assert cards(shown, "needs")[0]["title"] == "A Claude window with no session"

    def test_an_unreadable_record_is_a_problem(self):
        row = session("bad", signals=[sig("error", "scan")])
        row["kind"] = "error"
        shown = panel.view(doc(row))
        card = cards(shown, "problems")[0]
        assert card["title"] == "A record invariant cannot read"
        assert [c["text"] for c in card["chips"]] == ["Cannot read this project"]

    def test_within_a_group_severity_then_the_oldest_condition_first(self):
        rows = [
            session("low", signals=[sig("finished", "ready", severity="low")]),
            session("new", signals=[sig("waiting", "permission")],
                    hosts=[host(attention=waiting(since=NOW - 60))]),
            session("old", signals=[sig("waiting", "question")],
                    hosts=[host(attention=waiting("question", since=NOW - 3600))]),
            session("med", signals=[sig("waiting", "permission", severity="medium")],
                    hosts=[host(attention=waiting(since=NOW - 9 * 3600))]),
        ]
        order = [c["row"].split("|")[1] for c in cards(panel.view(doc(*rows)), "needs")]
        assert order == ["old", "new", "med", "low"]

    def test_ties_keep_the_documents_order(self):
        rows = [session(s, signals=[sig("finished", "ready", severity="low")])
                for s in ("b", "a", "c")]
        order = [c["row"].split("|")[1] for c in cards(panel.view(doc(*rows)), "needs")]
        assert order == ["b", "a", "c"]

    def test_an_empty_group_is_not_drawn_and_an_empty_machine_says_so(self):
        shown = panel.view({"now": NOW, "projects": []})
        assert shown["groups"] == []
        assert shown["summary"] == {"needs": 0, "problems": 0, "working": 0, "leftovers": 0}
        assert shown["empty"] == "Nothing needs you."
        assert shown["leftovers_card"] is None
        assert panel.view(doc(session("n")))["empty"] is None

    def test_the_strip_has_four_tiles_and_a_zero_still_shows(self):
        tiles = panel.view(doc(session("n")))["tiles"]
        assert [(t["id"], t["label"], t["count"], t["href"]) for t in tiles] == [
            ("needs", "Need you", 0, "#needs"),
            ("problems", "Problems", 0, "#problems"),
            ("working", "Working", 0, "#working"),
            ("leftovers", "Leftovers", 0, "#leftovers"),
        ]

    def test_the_document_title_counts_what_needs_attention(self):
        rows = [session("w", signals=[sig("waiting", "permission")],
                        hosts=[host(attention=waiting())]),
                session("t", signals=[sig("error", "tests")])]
        assert panel.view(doc(*rows))["doc_title"] == "(2) Sessions"
        assert panel.view(doc(session("n")))["doc_title"] == "Sessions"

    def test_full_cards_for_needs_and_problems_compact_for_the_rest(self):
        rows = [session("t", signals=[sig("error", "tests")]),
                session("r", hosts=[host(attention={"turn": "running"})], turn_state="busy"),
                session("n")]
        shown = panel.view(doc(*rows))
        assert {g["id"]: g["compact"] for g in shown["groups"]} == {
            "problems": False, "working": True, "idle": True}
        assert [g["heading"] for g in shown["groups"]] == ["Problems", "Working", "Idle"]


# -- the cards ---------------------------------------------------------------------------


class TestCards:
    def test_a_waiting_card_has_no_button_and_says_this_panel_never_answers(self):
        row = session("w", signals=[sig("waiting", "permission")],
                      hosts=[host(attention=waiting())],
                      recommendation=step((), kind="info"))
        card = cards(panel.view(doc(row), acting=True), "needs")[0]
        assert card["button"] is None and card["tap"] is True
        assert card["note"] == ("Answer it in Claude on your Mac or phone. "
                                "This panel never answers prompts.")
        assert card["age"] == "4 min"
        assert card["project"] == "Heap"
        assert [c["text"] for c in card["chips"]] == ["Waiting for your permission"]

    def test_a_waiting_row_gets_no_button_even_with_a_runnable_step(self):
        row = session("w", signals=[sig("waiting", "question")],
                      hosts=[host(attention=waiting("question"))],
                      recommendation=step(("invariant", "pipeline", "test")))
        shown = panel.view(doc(row), acting=True)
        assert cards(shown, "needs")[0]["button"] is None
        assert shown["details"]["K|w"]["step"]["button"] is None

    def test_a_runnable_step_gets_its_button_only_when_the_page_acts(self):
        row = session("t", signals=[sig("error", "tests")], pipeline=pipeline(failed=52),
                      recommendation=step(("invariant", "pipeline", "test"),
                                          env={"INVARIANT_SESSION_ID": "s1"}))
        card = cards(panel.view(doc(row), acting=True), "problems")[0]
        assert card["button"] == {"text": "Re-run tests…"} and card["tap"] is False
        assert cards(panel.view(doc(row)), "problems")[0]["button"] is None

    def test_the_title_is_the_name_without_its_id_else_the_task(self):
        named = session("4b7b00c1", name="suggestion-band-height-4b7b00c1")
        tasked = session("s2", name=None, pipeline=pipeline(task="Fix the band"))
        bare = session("s3", name=None)
        shown = panel.view(doc(named, tasked, bare))
        titles = [c["title"] for c in cards(shown, "idle")]
        assert titles == ["Suggestion band height", "Fix the band", "Unnamed session"]

    def test_the_note_says_the_window_closed_and_the_phase(self):
        row = session("t", signals=[sig("error", "tests"), sig("parked", alert=False)],
                      pipeline=pipeline("bugfix", "fixing", failed=52), host_ended=True)
        card = cards(panel.view(doc(row)), "problems")[0]
        assert card["note"] == "Its Claude window closed. Fixing a bug."
        assert [c["text"] for c in card["chips"]] == ["52 tests failing", "Paused"]
        assert [c["tone"] for c in card["chips"]] == ["problem", "idle"]

    def test_a_compact_card_reads_phase_and_last_activity(self):
        row = session("r", hosts=[host(attention={"turn": "running"})], turn_state="busy",
                      pipeline=pipeline("tdd", "red"), last_activity=NOW - 70)
        card = cards(panel.view(doc(row)), "working")[0]
        assert card["line"] == "Writing tests · active 1 min ago"
        bare = session("n", last_activity=NOW - 5)
        assert cards(panel.view(doc(bare)), "idle")[0]["line"] == "Active now"
        quiet = session("q")
        assert cards(panel.view(doc(quiet)), "idle")[0]["line"] == ""

    def test_a_terminal_or_unknown_phase_is_never_shown(self):
        done = session("d", pipeline=pipeline("tdd", "committed", terminal=True),
                       last_activity=NOW - 120)
        own = session("o", pipeline=pipeline("house-rules", "polishing"),
                      last_activity=NOW - 120)
        lines = [c["line"] for c in cards(panel.view(doc(done, own)), "idle")]
        assert lines == ["Active 2 min ago", "Active 2 min ago"]

    def test_unknown_signals_get_no_chip(self):
        row = session("u", signals=[sig("novel_kind", alert=False, severity="info")])
        assert cards(panel.view(doc(row)), "idle")[0]["chips"] == []


# -- wording ------------------------------------------------------------------------------


class TestWording:
    def test_ages(self):
        assert [panel.age_text(s) for s in (0, 59, 60, 3599, 3600, 2 * 86400 - 1,
                                            2 * 86400, 9 * 86400)] == [
            "now", "now", "1 min", "59 min", "1 h", "47 h", "2 days", "9 days"]

    def test_every_step_the_recommender_names_has_words(self):
        argvs = [
            ("invariant", "pipeline", "test"), ("invariant", "pipeline", "gates"),
            ("invariant", "context", "--id", "s"), ("invariant", "session", "review", "--id", "s"),
            ("invariant", "session", "merge", "--id", "s"),
            ("invariant", "session", "teardown", "--id", "s"),
            ("invariant", "session", "reap", "--dry-run"),
            ("invariant", "session", "resume", "--id", "s"),
            ("invariant", "session", "resume", "--id", "s", "--force"),
            ("invariant", "session", "adopt", "h", "--id", "s"), ("invariant", "doctor"),
        ]
        words = [panel.step_words(a) for a in argvs]
        assert all(w is not None for w in words)
        assert [w.button for w in words] == [
            "Re-run tests…", "Show blockers…", "Show its instructions…", "Review…", "Merge…",
            "Clean up…", "Check clean-up…", "Resume…", "Resume…", "Attach…", "Check setup…"]
        assert words[7].title == "Resume in a new Claude window?"
        assert words[8].title == "Take over in a new Claude window?"
        assert words[5].destructive is True
        assert sum(w.destructive for w in words) == 1
        for w in words:
            assert w.button.endswith("…") and not w.run.endswith("…")
            assert w.run not in ("Run it", "OK")

    def test_an_unknown_command_has_no_words(self):
        assert panel.step_words(("invariant", "frobnicate")) is None
        assert panel.step_words(("rm", "-rf", "/")) is None
        assert panel.step_words(()) is None

    def test_lanes_and_phases(self):
        assert panel.lane_text("bugfix") == "bug fix"
        assert panel.lane_text("tdd") == "feature"
        assert panel.lane_text("none") is None
        assert panel.lane_text("house-rules") is None
        assert panel.phase_text("bugfix", "reproducing") == "Reproducing the bug"
        assert panel.phase_text("spec-first", "implementing") == "Building"
        assert panel.phase_text("house-rules", "green") is None
        assert panel.phase_text("house-rules", "reviewing") is None

    def test_a_gate_refusal_names_the_rules_it_knows(self):
        known = session("g", signals=[sig("gate_blocked", severity="medium")],
                        hosts=[host(attention={"turn": "ended", "blocked": {
                            "rules": ["test.all_pass", "x.unknown"], "surface": "stop",
                            "since": iso(NOW - 300)}})])
        shown = panel.view(doc(known))
        assert shown["details"]["K|g"]["going_on"][0] == (
            "A check refused the last step: the tests must pass.")
        unknown = session("u", signals=[sig("gate_blocked", severity="medium")],
                          hosts=[host(attention={"turn": "ended", "blocked": {
                              "rules": ["x.unknown"], "since": iso(NOW - 300)}})])
        going = panel.view(doc(unknown))["details"]["K|u"]["going_on"]
        assert going[0] == "A check refused the last step."

    def test_the_fixed_strings_cover_what_the_page_says(self):
        for key in ("title", "live", "stale", "never", "refresh", "details", "back",
                    "going_on", "next_step", "more_actions", "recent", "technical",
                    "command", "copy", "copied", "exact", "runs_as", "cancel", "expires",
                    "expired", "look_again", "this_deletes", "cannot_undo", "show_output",
                    "done", "loading", "refresh_failed", "lost", "unreached",
                    "confirm_failed", "not_run", "remote_control"):
            assert panel.WORDS[key], key
        assert panel.WORDS["live"].format(age=6) == "Live · updated 6 s ago"
        assert panel.WORDS["stale"].format(age=60) == "Stale · updated 60 s ago"
        assert panel.WORDS["expires"].format(time="1:54") == "This confirm expires in 1:54"


# -- the detail view ------------------------------------------------------------------------


class TestDetail:
    def row(self, **extra):
        return session(
            "4b7b00c1", name="suggestion-band-height-4b7b00c1",
            signals=[sig("error", "tests"), sig("parked", alert=False)],
            pipeline=pipeline("bugfix", "fixing", failed=52), host_ended=True,
            hosts=[host(pid=None, alive=None, attention={
                "turn": "ended", "turn_since": iso(NOW - 3 * 3600),
                "degraded": {"event": "Stop", "error": "boom", "since": iso(NOW - 600)}})],
            recommendation=step(("invariant", "pipeline", "test"),
                                env={"INVARIANT_SESSION_ID": "4b7b00c1"}),
            plan_touched=NOW - 7200, **extra)

    def test_the_header_what_is_going_on_and_the_step(self):
        detail = panel.view(doc(self.row()), acting=True)["details"]["K|4b7b00c1"]
        assert detail["kicker"] == "Heap · bug fix"
        assert detail["title"] == "Suggestion band height"
        assert detail["going_on"][:2] == [
            "The last test run had 52 failures.",
            "No Claude window is working on it. Resume it to go on.",
        ]
        assert detail["step"] == {
            "name": "Re-run the tests",
            "sentence": "Runs Heap's test suite in this session's folder and records the "
                        "result. Nothing is changed or deleted.",
            "button": {"text": "Re-run tests…"},
            "copy": "INVARIANT_SESSION_ID=4b7b00c1 invariant pipeline test",
        }

    def test_chips_carry_their_age_where_the_fact_has_a_time(self):
        row = session("w", signals=[sig("waiting", "permission")],
                      hosts=[host(attention=waiting(since=NOW - 3 * 3600))])
        chips = panel.view(doc(row))["details"]["K|w"]["chips"]
        assert [c["text"] for c in chips] == ["Waiting for your permission 3 h"]

    def test_recent_lists_only_dated_events_newest_first_with_this_devices_actions(self):
        jobs = [{"id": "j1", "row": "K|4b7b00c1", "argv": ["invariant", "pipeline", "test"],
                 "kind": "run", "state": "done", "ok": False, "status": 1, "elapsed": 2.2,
                 "started": NOW - 60},
                {"id": "j2", "row": "K|other", "argv": ["invariant", "doctor"], "kind": "run",
                 "state": "done", "ok": True, "status": 0, "elapsed": 1, "started": NOW}]
        detail = panel.view(doc(self.row()), jobs=jobs)["details"]["K|4b7b00c1"]
        assert detail["recent"] == [
            {"text": "Run tests: Failed, exit 1", "at": NOW - 60},
            {"text": "A hook failed", "at": NOW - 600},
            {"text": "Its plan was updated", "at": NOW - 7200},
            {"text": "Claude finished its turn", "at": NOW - 3 * 3600},
        ]

    def test_technical_details_hold_the_engine_names(self):
        detail = panel.view(doc(self.row()))["details"]["K|4b7b00c1"]
        assert detail["technical"]["lines"] == [
            "Session 4b7b00c1 · state isolated · lane bugfix:fixing",
            "Host 2abd173c-1111-2222-3333-444455556666 · process id not recorded",
        ]
        assert detail["technical"]["path"] == "/r/.claude/worktrees/p-wt-s1"

    def test_a_host_with_a_pid_and_a_row_with_no_pipeline(self):
        row = session("s9", hosts=[host(pid=4242)])
        detail = panel.view(doc(row))["details"]["K|s9"]
        assert detail["technical"]["lines"] == [
            "Session s9 · state isolated",
            "Host 2abd173c-1111-2222-3333-444455556666 · process id 4242",
        ]
        assert detail["kicker"] == "Heap"
        assert detail["step"] is None and detail["going_on"] == []

    def test_an_info_step_has_no_suggested_step(self):
        row = session("y", signals=[sig("your_turn", alert=False)],
                      recommendation=step((), kind="info"))
        detail = panel.view(doc(row))["details"]["K|y"]
        assert detail["step"] is None
        assert detail["going_on"] == ["Claude finished its turn and is waiting for you."]

    def test_every_sentence_of_the_table(self):
        rows = [
            session("q", signals=[sig("waiting", "question")],
                    hosts=[host(attention=waiting("question", since=NOW - 120))]),
            session("v", signals=[sig("finished", "reviewed", severity="low")]),
            session("y", signals=[sig("finished", "ready", severity="low")]),
            session("k", signals=[sig("stuck", severity="medium")],
                    last_activity=NOW - 1800),
            session("h", signals=[sig("host_gone", alert=False)]),
            session("x", signals=[sig("stranded", alert=False)]),
            session("r", hosts=[host(attention={"turn": "running"})], turn_state="busy",
                    last_activity=NOW - 300),
            session("d", signals=[sig("teardown_pending", alert=False)]),
            session("e", signals=[sig("error", "degraded")]),
            session("t", signals=[sig("error", "tests")]),
        ]
        details = panel.view(doc(*rows))["details"]
        going = {k.split("|")[1]: v["going_on"][0] for k, v in details.items()}
        assert going == {
            "q": "Claude asked you a question 2 min ago. Answer it in Claude.",
            "v": "The review checks passed. It can be merged.",
            "y": "Its plan is complete and its lane has landed. "
                 "It is ready for the review checks.",
            "k": "Claude has been working for 30 min with no sign of progress.",
            "h": "Its Claude window closed without saying so, so nothing is happening "
                 "until you resume it.",
            "x": "The session's folder no longer exists.",
            "r": "Claude is working on it; active 5 min ago.",
            "d": "It is merged. Its folder and branch can be removed.",
            "e": "One of invariant's hooks failed in this session.",
            "t": "The last test run failed.",
        }
        chips = {k.split("|")[1]: [c["text"] for c in v["chips"]] for k, v in details.items()}
        assert chips["k"] == ["No progress for 30 min"]
        assert chips["t"] == ["Tests failing"]
        assert chips["d"] == ["Merged, ready to clean up"]
        assert chips["e"] == ["A hook failed"]


# -- leftovers -------------------------------------------------------------------------------


class TestLeftovers:
    def test_the_card_counts_and_the_screen_lists(self):
        rows = [orphan(f"{n:08x}-0000", last_activity=NOW - 2 * 86400) for n in range(4)]
        rows[1]["hosts"][0]["alive"] = False
        rows[2]["hosts"][0]["alive"] = None
        rows.append(leaked())
        shown = panel.view(doc(*rows))
        assert shown["leftovers_card"] == {
            "title": "5 leftovers to tidy",
            "line": "4 Claude windows with no session, 1 left-over folder",
        }
        screen = shown["leftovers"]
        assert screen["title"] == "Leftovers"
        assert screen["intro"].startswith("Things that outlived their session.")
        hosts, folders = screen["sections"]
        assert (hosts["heading"], hosts["count"], hosts["shown"]) == (
            "Claude windows with no session", 4, 3)
        assert hosts["more"] == "Show 1 more"
        assert [r["title"] for r in hosts["rows"]] == ["Heap · idle 2 days"] * 4
        assert [r["line"] for r in hosts["rows"]] == [
            "Still open on the Mac · window 00000000",
            "Already closed · record only · window 00000001",
            "Process id not recorded · window 00000002",
            "Still open on the Mac · window 00000003",
        ]
        assert (folders["heading"], folders["count"], folders["more"]) == (
            "Left-over folders", 1, None)
        assert folders["rows"] == [{"title": "Heap · p-wt-0c269de4",
                                    "line": "On branch session/0c269de4"}]

    def test_one_of_each_is_singular_and_none_is_no_card(self):
        shown = panel.view(doc(orphan()))
        assert shown["leftovers_card"]["line"] == "1 Claude window with no session"
        assert shown["leftovers_card"]["title"] == "1 leftover to tidy"
        bare = leaked()
        bare["branch"] = None
        folder = panel.view(doc(bare))["leftovers"]["sections"][1]["rows"][0]
        assert folder["line"] == "Not on a branch"
        assert panel.view(doc(session("n")))["leftovers_card"] is None
        assert orphan()["hosts"][0]["host_session_id"] not in json.dumps(
            panel.view(doc(orphan()))["leftovers_card"])


# -- the confirm and the result ------------------------------------------------------------


class TestConfirmWords:
    def test_a_safe_step(self):
        row = session("4b7b00c1")
        words = panel.confirm_words(row, "Heap", ("invariant", "pipeline", "test"), "run",
                                    "/r/.claude/worktrees/p-wt-s1",
                                    {"INVARIANT_SESSION_ID": "4b7b00c1"}, launch=None)
        assert words == {
            "title": "Re-run the tests?",
            "sentence": "Runs Heap's test suite in this session's folder and records the "
                        "result. Nothing is changed or deleted.",
            "button": "Run tests",
            "command": "INVARIANT_SESSION_ID=4b7b00c1 invariant pipeline test",
            "place": "In Heap › session folder 4b7b00c1",
            "cwd": "/r/.claude/worktrees/p-wt-s1",
            "env": ["INVARIANT_SESSION_ID=4b7b00c1"],
            "launch": None,
            "destructive": False,
            "deletes_heading": "This deletes",
            "deletes": [],
            "checked": [],
        }

    def test_the_main_checkout_and_elsewhere(self):
        row = session("s1")
        merge = panel.confirm_words(row, "Heap", ("invariant", "session", "merge", "--id", "s1"),
                                    "run", "/r", {}, launch=None)
        assert merge["place"] == "In Heap › main checkout"
        assert merge["button"] == "Merge"
        other = panel.confirm_words(row, "Heap", ("invariant", "doctor"), "run", "/x", {},
                                    launch=None)
        assert other["place"] == "In Heap"

    def test_a_terminal_step_shows_its_launch_line(self):
        words = panel.confirm_words(session("s1"), "Heap",
                                    ("invariant", "session", "resume", "--id", "s1"),
                                    "terminal", "/r", {}, launch="cd /r && claude")
        assert words["launch"] == "cd /r && claude"
        assert "Opens Terminal on your Mac" in words["sentence"]
        assert words["button"] == "Open window"

    def test_the_clean_up_says_what_it_deletes(self):
        words = panel.confirm_words(session("s1"), "Heap",
                                    ("invariant", "session", "teardown", "--id", "s1"),
                                    "run", "/r", {}, launch=None)
        assert words["destructive"] is True
        assert words["deletes"] == ["The session folder p-wt-s1", "The branch session/s1"]
        assert words["sentence"].endswith("This cannot be undone.")
        assert words["button"] == "Remove folder and branch"

    def test_an_unknown_step_has_no_words(self):
        assert panel.confirm_words(session("s1"), "Heap", ("rm",), "run", "/r", {},
                                   launch=None) is None


class TestResultWords:
    def job(self, **extra):
        base = {"argv": ["invariant", "pipeline", "test"], "kind": "run", "state": "done",
                "ok": True, "status": 0, "elapsed": 2.2}
        return {**base, **extra}

    def test_each_outcome(self):
        assert panel.result_words(self.job(state="running", ok=None, status=None)) == {
            "headline": "Running…", "line": "Run tests", "tone": "running"}
        assert panel.result_words(self.job()) == {
            "headline": "Done in 3 s", "line": "Run tests", "tone": "ok"}
        assert panel.result_words(self.job(ok=False, status=2, elapsed=0.2)) == {
            "headline": "Failed, exit 2", "line": "Run tests · stopped after 1 s",
            "tone": "failed"}
        resume = ["invariant", "session", "resume", "--id", "s"]
        assert panel.result_words(self.job(argv=resume, kind="terminal", status=None)) == {
            "headline": "Opened in a new window", "line": "Open window", "tone": "ok"}
        assert panel.result_words(self.job(argv=resume, kind="terminal", ok=False,
                                           status=None))["headline"] == "Failed"
        assert panel.result_words(self.job(argv=["rm"]))["line"] == "This step"

    @pytest.mark.parametrize("status, own, last, printed, why", [
        (1, "discovery for 'destination_id' produced nothing (exit 1). The command was: x",
         "4 simulators are booted", True,
         "invariant could not find a value for destination_id."),
        (2, 'discovery for "scheme" timed out; the command was: sleep 99', None, True,
         "invariant ran out of time finding a value for scheme."),
        (1, "the merge gates refused this session", "x", True,
         "invariant said: the merge gates refused this session"),
        (4, None, "error: no such table", True, "Failed, exit 4. error: no such table"),
        (None, None, "timed out after 600s", True, "Failed. timed out after 600s"),
        (2, None, None, False, "Failed, exit 2. It printed nothing."),
        (None, None, None, False, "Failed. It printed nothing."),
        (1, None, None, True, "Failed, exit 1. See the full output."),
    ])
    def test_why_names_a_cause_only_when_invariant_wrote_one(self, status, own, last, printed,
                                                              why):
        assert panel.why_words(status, own, last, printed) == why

    def test_the_why_label_is_a_fixed_word(self):
        assert panel.WORDS["why"] == "Why"


# -- the acceptance: nothing engine-shaped on the default view ------------------------------


#: The view keys that hold keys, links and tones, never words a person reads.
_NOT_SHOWN = {"row", "id", "href", "tone", "details", "leftovers"}


def _shown_strings(value, key=None):
    if key in _NOT_SHOWN:
        return
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _shown_strings(v, k)
    elif isinstance(value, list):
        for item in value:
            yield from _shown_strings(item)
    elif isinstance(value, str):
        yield value


def _engine_facts(document) -> set[str]:
    facts = set()
    for project in document["projects"]:
        facts.add(project["project"]["root"])
        for row in project["rows"]:
            for field in ("worktree", "path", "root", "origin", "name", "state", "error"):
                if row.get(field):
                    facts.add(str(row[field]))
            if row.get("pipeline"):
                pipe = row["pipeline"]
                facts.add(f"{pipe['methodology']}:{pipe['state']}")
                facts.add(pipe["state"])
            for h in row.get("hosts") or []:
                facts.add(h["host_session_id"])
                if h.get("pid"):
                    facts.add(str(h["pid"]))
            for s in row.get("signals") or []:
                facts.add(s["kind"])
            advice = row.get("recommendation") or {}
            if advice.get("argv"):
                facts.add(" ".join(advice["argv"]))
                facts.add(advice["argv"][0] + " ")
            if advice.get("cwd"):
                facts.add(advice["cwd"])
    return {f for f in facts if f}


class TestNothingEngineShaped:
    def test_no_default_view_string_holds_a_path_argv_pid_host_id_or_state(
            self, machine_world):
        snapshots = scan_all(ProjectIndex(oracle_home()), clock=lambda: test_oracle_scan.NOW,
                             cache=ScanCache(probe=machine_world["probe"]))
        document = assess(snapshots, test_oracle_scan.NOW)
        shown = panel.view(document, acting=True)
        assert shown["groups"], "the world has sessions to show"
        facts = _engine_facts(document)
        for text in _shown_strings(shown):
            for fact in facts:
                assert not re.search(rf"(?<![\w-]){re.escape(fact)}(?![\w-])", text), (
                    fact, text)
            assert "/" not in text, text

    def test_the_technical_details_hold_them_instead(self, machine_world):
        snapshots = scan_all(ProjectIndex(oracle_home()), clock=lambda: test_oracle_scan.NOW,
                             cache=ScanCache(probe=machine_world["probe"]))
        shown = panel.view(assess(snapshots, test_oracle_scan.NOW))
        live = next(d for k, d in shown["details"].items()
                    if k.endswith("|a-live"))["technical"]
        assert "Session a-live · state working · lane tdd:green" in live["lines"]
        assert any(line.startswith("Host h-live · process id ") for line in live["lines"])
        assert live["path"].endswith("live")


class TestTheEdges:
    def test_an_unreadable_record_with_no_signal_is_still_a_problem(self):
        row = session("bad")
        row["kind"] = "error"
        row["error"] = "JSONDecodeError: line 1"
        shown = panel.view(doc(row))
        assert [g["id"] for g in shown["groups"]] == ["problems"]
        assert "Error: JSONDecodeError: line 1" in shown["details"]["K|bad"]["technical"]["lines"]

    def test_an_unparseable_time_is_no_time(self):
        row = session("w", signals=[sig("waiting", "permission")],
                      hosts=[host(attention={"waiting": {"kind": "permission",
                                                         "since": "not a time"}})])
        card = cards(panel.view(doc(row)), "needs")[0]
        assert card["age"] == ""
        going = panel.view(doc(row))["details"]["K|w"]["going_on"][0]
        assert going.startswith("Claude asked for permission. ")

    def test_a_step_the_table_does_not_know_is_not_offered(self):
        row = session("u", recommendation=step(("invariant", "frobnicate")))
        assert panel.view(doc(row), acting=True)["details"]["K|u"]["step"] is None

    def test_a_clean_up_without_a_folder_or_branch_lists_nothing(self):
        row = session("s1", worktree=None, branch=None)
        words = panel.confirm_words(row, "Heap",
                                    ("invariant", "session", "teardown", "--id", "s1"),
                                    "run", "/r", {}, launch=None)
        assert words["deletes"] == []


# -- close host (IDEA_027 increment 6, ADR-009 D15) ----------------------------------------

HOST_ID = "27885f4e-1111-2222-3333-444455556666"
CLOSE = ["invariant", "oracle", "close-host", HOST_ID, "--pid", "41001",
         "--start", "2026-10-20T09:00:00", "--tty", "ttys004"]


def leftover_row(cause="leftover", alive=True, pid=41001, recommendation="close",
                 conflict=(), idle=2 * 86400):
    row = orphan(HOST_ID, alive=alive, last_activity=NOW - idle,
                 signals=[sig("orphan_host", cause=cause, alert=False, severity="info")])
    view = row["hosts"][0]
    view.update(pid=pid, pid_source="conflict" if conflict else "record",
                pid_conflict=list(conflict), tty="ttys004")
    if recommendation == "close":
        row["recommendation"] = step(CLOSE, cwd="/r")
    elif recommendation == "adopt":
        row["recommendation"] = step(["invariant", "session", "adopt", HOST_ID, "--id", "s4"],
                                     cwd="/r")
    return row


def host_rows(*rows, acting=True):
    return panel.view(doc(*rows), acting=acting)["leftovers"]["sections"][0]["rows"]


class TestCloseHostWords:
    def test_the_step(self):
        words = panel.step_words(CLOSE)
        assert words.button == "Close…"
        assert words.title == "Close this Claude window?"
        assert words.run == "Close window"
        assert words.destructive is True

    def test_the_confirm_says_what_ends_and_what_was_checked(self):
        words = panel.confirm_words(leftover_row(), "Heap", CLOSE, "run", "/r", {},
                                    launch=None, now=NOW)
        assert words["title"] == "Close this Claude window?"
        assert words["sentence"].startswith(
            "Ends Claude in this window, then closes the Terminal tab.")
        assert "claude --resume" in words["sentence"]
        assert words["button"] == "Close window"
        assert words["destructive"] is True
        assert words["deletes_heading"] == "This ends"
        assert words["deletes"] == [
            "Claude in Heap · window 27885f4e",
            "Process 41001 · idle 2 days",
        ]
        assert words["checked"] == [
            "No invariant session is using this Claude window",
            "Checked again the moment you press Close window",
        ]
        assert words["command"] == " ".join(CLOSE)
        assert words["place"] == "In Heap › main checkout"

    def test_an_idle_time_not_known_is_left_out(self):
        row = leftover_row()
        row["last_activity"] = None
        words = panel.confirm_words(row, "Heap", CLOSE, "run", "/r", {}, launch=None, now=NOW)
        assert words["deletes"][1] == "Process 41001"

    def test_the_clean_up_keeps_its_own_heading_and_checks_nothing_yet(self):
        words = panel.confirm_words(session("s1"), "Heap",
                                    ("invariant", "session", "teardown", "--id", "s1"),
                                    "run", "/r", {}, launch=None)
        assert words["deletes_heading"] == "This deletes"
        assert words["checked"] == []

    def test_only_a_close_reports_its_outcome(self):
        assert panel.outcome_words(CLOSE, True, "Claude ended, and its Terminal tab "
                                   "closed.") == "Claude ended, and its Terminal tab closed."
        assert panel.outcome_words(CLOSE, False, "x") == ""
        assert panel.outcome_words(["invariant", "pipeline", "test"], True, "ok") == ""
        assert panel.outcome_words(CLOSE, True, None) == ""

    def test_the_fixed_words(self):
        assert panel.WORDS["this_ends"] == "This ends"
        assert panel.WORDS["checked_now"] == "Checked just now"
        assert panel.WORDS["outcome"] == "What happened"


class TestLeftoverClasses:
    @pytest.mark.parametrize(("cause", "alive", "line"), [
        ("leftover", True, "Still open on the Mac"),
        ("unmanaged", True, "Open, never had a session"),
        ("active", True, "Active, no session"),
        ("gone", False, "Already closed · record only"),
        ("unknown", None, "Process id not recorded"),
        ("unknown", True, "Still open on the Mac"),
    ])
    def test_each_class_has_its_line(self, cause, alive, line):
        [row] = host_rows(leftover_row(cause, alive=alive, recommendation=None))
        assert row["line"] == f"{line} · window 27885f4e"

    def test_a_conflict_says_its_process_ids_disagree(self):
        conflict = [{"source": "attention", "pid": 3, "alive": False},
                    {"source": "record", "pid": 4, "alive": True}]
        [row] = host_rows(leftover_row("unknown", alive=None, pid=None, conflict=conflict,
                                       recommendation=None))
        assert row["line"] == "Its process ids disagree · window 27885f4e"
        assert row["button"] is None

    def test_a_closable_row_has_a_close_pill_by_its_key(self):
        [row] = host_rows(leftover_row())
        assert row["button"] == {"text": "Close…"}
        assert row["row"] == f"K|host:{HOST_ID}"

    def test_an_adoptable_row_has_an_attach_pill(self):
        [row] = host_rows(leftover_row("unmanaged", recommendation="adopt"))
        assert row["button"] == {"text": "Attach…"}

    def test_a_read_only_page_has_no_pill(self):
        [row] = host_rows(leftover_row(), acting=False)
        assert row["button"] is None

    def test_an_info_step_has_no_pill(self):
        row = leftover_row(recommendation=None)
        row["recommendation"] = step([], kind="info")
        assert host_rows(row)[0]["button"] is None


class TestTechnicalConflict:
    def test_a_conflict_row_says_which_pids_disagree(self):
        conflict = [{"source": "attention", "pid": 3, "alive": False},
                    {"source": "record", "pid": 4, "alive": True}]
        row = session("s1", hosts=[host(HOST_ID, pid=None, alive=None)],
                      signals=[sig("waiting", "permission")])
        row["hosts"][0].update(pid_source="conflict", pid_conflict=conflict)
        lines = panel.view(doc(row))["details"]["K|s1"]["technical"]["lines"]
        assert (f"Host {HOST_ID} · process ids disagree: attention 3 (gone), "
                "record 4 (alive)") in lines
        assert not any("not recorded" in line for line in lines)


# -- IDEA_030 increment 2: start a new session (ADR-009 D16) --------------------------------

NAME = "heap-1026-0049"
NEW_HOST = "5e1f0000-aaaa-bbbb-cccc-000000000001"
TIMEOUT = ("The window did not appear on the Mac within a minute; check the Mac (a trust "
           "prompt or a login may be waiting).")


def start_job(state="done", ok=True, started=NOW - 10, name=NAME, appeared=False, status=None,
              why="", job_id="j1", project="K"):
    job = {"id": job_id, "row": f"{project}|start", "subject": "Heap", "kind": "start",
           "argv": ["/opt/bin/claude", "--name", name, "--remote-control", name], "cwd": "/r",
           "started": started, "remote_control": name, "state": state,
           "ok": ok if state == "done" else None, "status": status, "output": "",
           "elapsed": 3.0, "audit_error": "", "project": project, "appeared": appeared}
    job["words"] = {**panel.result_words(job), "why": why, "outcome": ""}
    return job


def named_orphan(name=NAME, cause="active", alive=True):
    row = orphan(NEW_HOST, signals=[sig("orphan_host", cause, alert=False, severity="info")],
                 alive=alive, last_activity=NOW - 5)
    row["hosts"][0]["launched_name"] = name
    return row


def started(*rows, jobs=(), acting=True, starting=True):
    return panel.view(doc(*rows), acting=acting, jobs=list(jobs), starting=starting)


class TestStartCards:
    def test_a_running_start_is_a_starting_card_under_working(self):
        shown = started(jobs=[start_job(state="running")])
        assert cards(shown, "working") == [{
            "start": "j1", "state": "starting", "project": "Heap", "title": NAME,
            "line": "Starting…", "chips": [], "result": None}]
        assert shown["summary"]["working"] == 1
        assert [g["id"] for g in shown["groups"]] == ["working"]

    def test_a_window_not_seen_yet_is_still_starting_for_a_minute(self):
        [card] = cards(started(jobs=[start_job(started=NOW - 59)]), "working")
        assert card["state"] == "starting"

    def test_a_window_not_seen_within_a_minute_is_a_failure_that_says_why(self):
        [card] = cards(started(jobs=[start_job(started=NOW - 61)]), "working")
        assert card["state"] == "failed"
        assert card["chips"] == [{"text": "Did not start", "tone": "problem"}]
        assert card["line"] == TIMEOUT
        assert card["result"] == {"headline": "Failed", "line": "Start session",
                                  "tone": "failed", "why": TIMEOUT, "outcome": ""}

    def test_a_launch_that_failed_says_its_own_why_at_once(self):
        job = start_job(ok=False, why="invariant said: no claude on PATH to start")
        [card] = cards(started(jobs=[job]), "working")
        assert card["state"] == "failed"
        assert card["line"] == "invariant said: no claude on PATH to start"
        assert card["result"] is None

    def test_a_failure_is_shown_for_an_hour(self):
        assert cards(started(jobs=[start_job(ok=False, started=NOW - 3601)]), "working") == []
        assert cards(started(jobs=[start_job(started=NOW - 3601)]), "working") == []

    def test_a_document_older_than_the_start_is_still_starting(self):
        [card] = cards(started(jobs=[start_job(started=NOW + 30)]), "working")
        assert card["state"] == "starting"

    def test_the_window_once_seen_is_the_normal_row_under_working_by_its_name(self):
        shown = started(named_orphan(), jobs=[start_job()])
        [card] = cards(shown, "working")
        key = f"K|host:{NEW_HOST}"
        assert card["row"] == key and card["title"] == NAME and "start" not in card
        assert shown["summary"]["leftovers"] == 0 and shown["leftovers_card"] is None
        detail = shown["details"][key]
        assert detail["title"] == NAME
        assert detail["going_on"][-1] == ("Claude is running in a new window on the Mac. Pick "
                                          f"it up from Remote Control on your phone as {NAME}.")

    def test_a_window_a_session_took_is_shown_as_that_session_only(self):
        row = session("s9", name="fix-it-s9", hosts=[host(NEW_HOST)])
        row["hosts"][0]["launched_name"] = NAME
        shown = started(row, jobs=[start_job()])
        assert not any("start" in c for g in shown["groups"] for c in g["cards"])
        assert cards(shown, "idle")[0]["title"] == "Fix it"

    def test_a_window_seen_and_gone_leaves_nothing(self):
        assert started(jobs=[start_job(appeared=True, started=NOW - 600)])["groups"] == []

    def test_a_seen_window_that_went_quiet_is_a_leftover_again(self):
        shown = started(named_orphan(cause="unmanaged"), jobs=[start_job()])
        assert cards(shown, "working") == []
        assert shown["summary"]["leftovers"] == 1

    def test_a_window_by_another_name_is_not_the_start(self):
        shown = started(named_orphan(name="heap-other"), jobs=[start_job()])
        assert cards(shown, "working")[0]["state"] == "starting"
        assert shown["summary"]["leftovers"] == 1

    def test_other_actions_make_no_start_card(self):
        job = {**start_job(), "kind": "run", "argv": ["invariant", "doctor"]}
        assert started(jobs=[job])["groups"] == []


class TestStartPicker:
    def test_it_lists_the_registered_projects_with_their_live_sessions(self):
        document = {"now": NOW, "projects": [
            {"project": {"key": "K", "name": "Heap", "root": "/r"},
             "rows": [session("s1"), session("s2"), orphan()]},
            {"project": {"key": "L", "name": "Lark", "root": "/l"}, "rows": [session("t1")]},
            {"project": {"key": "M", "name": "Moss", "root": "/m"}, "rows": []},
        ]}
        shown = panel.view(document, acting=True, starting=True)
        assert shown["start"] == {"projects": [
            {"id": "K", "name": "Heap", "line": "2 live sessions"},
            {"id": "L", "name": "Lark", "line": "1 live session"},
            {"id": "M", "name": "Moss", "line": "No live session"},
        ]}

    def test_a_read_only_page_or_one_without_a_starter_offers_none(self):
        assert started(acting=False)["start"] is None
        assert started(starting=False)["start"] is None
        assert panel.view(doc())["start"] is None


class TestStartWords:
    def prepared(self, prompt=None):
        argv = ["/opt/bin/claude", "--name", NAME, "--remote-control", NAME,
                *([prompt] if prompt else [])]
        return {"name": NAME, "label": "1026-0049", "argv": argv, "cwd": "/r", "prompt": prompt,
                "facts": {"open_windows": 2, "uncommitted": 3, "dirty": True}}

    def test_the_sheet_says_what_starts_where_and_by_what_name(self):
        assert panel.start_words(self.prepared("go"), "Heap") == {
            "title": "Start a new session?",
            "sentence": ("A new Terminal window on the Mac runs Claude with Remote Control in "
                         "Heap's main folder."),
            "button": "Start session",
            "name": NAME,
            "name_line": f"Remote Control name: {NAME}",
            "label": "1026-0049",
            "prompt": "go",
            "command": f"/opt/bin/claude --name {NAME} --remote-control {NAME} go",
            "place": "In Heap › main checkout",
            "cwd": "/r",
            "facts": ["2 Claude windows already open here",
                      "Main folder has uncommitted changes (3 files)"],
        }

    def test_no_prompt_is_an_empty_one(self):
        assert panel.start_words(self.prepared(), "Heap")["prompt"] == ""

    def test_a_starts_result_is_worded_as_a_window(self):
        job = start_job(state="running")
        assert panel.result_words(job) == {"headline": "Running…", "line": "Start session",
                                           "tone": "running"}
        assert panel.result_words(start_job())["headline"] == "Opened in a new window"
        failed = panel.result_words(start_job(ok=False))
        assert failed["headline"] == "Failed" and failed["line"].startswith("Start session · ")

    def test_the_fixed_strings_the_sheet_needs_are_in_the_table(self):
        for key in ("new_session", "projects", "project_field", "label_field", "prompt_field",
                    "prompt_hint", "exact_start", "checked_now"):
            assert panel.WORDS[key], key
        assert panel.WORDS["new_session"] == "New session…"


# -- an oracle older than the install (BUG_089) --------------------------------------


def skewed(reasons=("rules",), unread=("Heap",), rules=("sweep.no_full_run",)):
    shown = doc()
    shown["projects"][0]["unread"] = "enforcement.rules names 'sweep.no_full_run'"
    shown["oracle"] = {
        "skew": {"reasons": list(reasons), "rules": list(rules),
                 "started": {"digest": "aaaa"}, "installed": {"digest": "bbbb"},
                 "detail": "d"},
        "unread": list(unread),
    }
    return shown


class TestAnOlderOracle:
    def test_it_is_one_problem_first_naming_the_projects_it_left(self):
        shown = panel.view(skewed())
        [card] = cards(shown, panel.PROBLEMS)
        assert card["row"] == panel.ORACLE_ROW
        assert card["title"] == "The oracle is out of date"
        assert card["project"] == "Session Oracle"
        assert card["chips"] == [{"text": "Restart needed", "tone": "problem"}]
        assert card["note"] == "Not read: Heap"
        assert card["button"] is None and card["tap"] is False
        assert shown["summary"][panel.PROBLEMS] == 1
        assert "Cannot read this project" not in json.dumps(shown)

    def test_it_comes_before_every_project_problem(self):
        shown = skewed()
        shown["projects"][0]["rows"] = [session(signals=[sig("error", "tests")])]
        first, second = cards(panel.view(shown), panel.PROBLEMS)
        assert first["row"] == panel.ORACLE_ROW and second["row"] != panel.ORACLE_ROW

    def test_its_detail_says_what_happened_and_how_to_restart_it(self):
        detail = panel.view(skewed())["details"][panel.ORACLE_ROW]
        assert detail["kicker"] == "Session Oracle" and detail["step"] is None
        assert detail["going_on"] == [
            ("The oracle on the Mac is older than the invariant installed there. "
             "Restarting it fixes this; Technical details has the command."),
            "Until then it cannot read 1 project: Heap.",
        ]
        assert detail["technical"]["lines"] == [
            "Started from aaaa, installed bbbb",
            "Rules this build lacks: sweep.no_full_run",
            "Heap: enforcement.rules names 'sweep.no_full_run'",
            ("Restart by hand: invariant oracle install, and invariant oracle install --web "
             "for this page"),
        ]

    def test_a_moved_install_says_it_restarts_itself(self):
        shown = skewed(reasons=("changed",), unread=("Heap", "Other"), rules=())
        detail = panel.view(shown)["details"][panel.ORACLE_ROW]
        assert detail["chips"] == [{"text": "Restarting", "tone": "problem"}]
        assert detail["going_on"] == [
            ("invariant was updated on the Mac after the oracle started. The oracle "
             "restarts itself to catch up, within a minute."),
            "Until then it cannot read 2 projects: Heap, Other.",
        ]
        assert not any(line.startswith("Rules") for line in detail["technical"]["lines"])

    def test_with_every_project_read_there_is_no_note(self):
        shown = skewed(reasons=("changed",), unread=(), rules=())
        del shown["projects"][0]["unread"]
        [card] = cards(panel.view(shown), panel.PROBLEMS)
        assert card["note"] is None
        assert len(panel.view(shown)["details"][panel.ORACLE_ROW]["going_on"]) == 1

    @pytest.mark.parametrize("oracle", [None, {"skew": None, "unread": []}, {"skew": "x"}])
    def test_no_skew_no_card(self, oracle):
        shown = doc()
        if oracle is not None:
            shown["oracle"] = oracle
        view = panel.view(shown)
        assert panel.ORACLE_ROW not in view["details"]
        assert cards(view, panel.PROBLEMS) == []

    def test_a_real_stale_scan_reads_as_one_problem(self, tmp_path, monkeypatch):
        from invariant.core import rules as rules_module

        test_oracle_scan.make_project(tmp_path, "one")
        test_oracle_scan.make_project(tmp_path, "two")
        monkeypatch.delitem(rules_module._REGISTRY, "sweep.no_full_run")
        snapshots = scan_all(ProjectIndex(oracle_home()), clock=lambda: NOW, cache=ScanCache())
        shown = panel.view(assess(snapshots, NOW))
        [card] = cards(shown, panel.PROBLEMS)
        assert card["row"] == panel.ORACLE_ROW and card["note"] == "Not read: one, two"
