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
            branch="session/s1", heartbeat=None, plan_touched=None, host_ended=False):
    return {
        "kind": "session", "project": project, "project_name": "Heap", "root": "/r",
        "session_id": sid, "name": name, "state": state, "stage": stage,
        "worktree": worktree, "branch": branch, "origin": "/r", "stranded": False,
        "host_ended": host_ended, "plan_done": 0, "plan_total": 0, "plan_complete": False,
        "heartbeat": heartbeat, "plan_touched": plan_touched, "pipeline": pipeline,
        "hosts": list(hosts), "last_activity": last_activity, "path": None, "error": None,
        "adoptable": [], "previous_session": None,
        "signals": list(signals), "recommendation": recommendation, "gates": None,
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
            session("r", hosts=[host(attention={"turn": "running"})]),
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
                      hosts=[host(attention={"turn": "running"})])
        shown = panel.view(doc(row))
        assert [g["id"] for g in shown["groups"]] == ["problems"]
        texts = [c["text"] for c in cards(shown, "problems")[0]["chips"]]
        assert texts == ["77 tests failing", "Claude is working"]

    def test_a_gone_host_is_not_working(self):
        row = session("t", hosts=[host(alive=False, attention={"turn": "running"})])
        assert [g["id"] for g in panel.view(doc(row))["groups"]] == ["idle"]

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
                session("r", hosts=[host(attention={"turn": "running"})]),
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
        row = session("r", hosts=[host(attention={"turn": "running"})],
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
            session("r", hosts=[host(attention={"turn": "running"})],
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
            "deletes": [],
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
