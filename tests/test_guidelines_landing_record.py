"""The landing's guidelines records, kept in pipeline evidence (IDEA_031 A4.6).

ADR-012 DN-7: what the guidelines family decided at a landing is saved into
pipeline evidence. A successful advance into a landing-role state appends one
entry to ``evidence["guidelines_landings"]``, in the same save as the
transition, copied from the verdicts that advance evaluated:

- each ``guidelines.*`` gate's outcome, at the level it ran at, with the
  digest it judged against;
- ``mechanical_clean``'s findings in D5's record shape, and
  ``verdicts_complete``'s standing for each owed rule;
- each grant either family gate read, spent or held, with why; the decision
  is ``core.rules.grant_outcomes``, which the CLI's spend asks too.

It is the gates' own answer, never a re-derivation. A refused advance records
nothing, a forced landing records what the gates said and that it was
forced, and nothing in the record is a clock time. No rule reads the record.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
from test_cli import payload, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, _review, write
from test_guidelines_override import sessions_in
from test_guidelines_verdicts_complete import (
    PYTHON,
    RULESET,
    SELECTOR,
    context,
    every_pass,
    gate,
    green,
    pipeline,
    sh,
)
from test_guidelines_verdicts_override import (
    ANSWER,
    REASON,
    by_label,
    cli,
    grant,
    scenario,
)

from invariant.cli.conformance import replay
from invariant.cli.main import _spend_guideline_overrides
from invariant.core import rules, verdicts
from invariant.core.conformance import load_named
from invariant.core.errors import GateFailure
from invariant.core.manifest import Level
from invariant.core.rules import Evaluated, Verdict, apply_acknowledgements, evaluate
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

KEY = "guidelines_landings"
VERDICTS = "guidelines.verdicts_complete"
MECHANICAL = "guidelines.mechanical_clean"
SINGLE = "data.single-open"


# -- a standing's record (core/verdicts) -------------------------------------------------


class TestAStandingsRecord:
    def test_it_carries_every_field_the_gate_decided_on(self):
        s = verdicts.Standing(
            rule=SINGLE, severity="blocker", standing=verdicts.STALE,
            problems=(verdicts.Problem("P3", "the blob changed", "answer it again"),),
            override="reviewed",
        )
        assert s.record() == {
            "rule": SINGLE, "severity": "blocker", "standing": "stale",
            "status": None, "reason": "",
            "problems": [{"predicate": "P3", "message": "the blob changed",
                          "fix": "answer it again"}],
            "set_aside": None, "blocks": False, "warns": False,
            "override": "reviewed", "overridden": True,
        }

    def test_verdicts_show_json_is_that_record(self, tmp_path, monkeypatch, capsys):
        _project(tmp_path, monkeypatch, capsys)
        assert run("guidelines", "verdicts", "show", "--json") == 0
        owed = payload(capsys)["owed"]
        assert SINGLE in {o["rule"] for o in owed}
        assert set(owed[0]) == set(verdicts.Standing("r", "warn", "missing").record())


# -- what each family gate carries for the record (core/rules) ------------------------------


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    write(root, "app/m.py", "x = 1\ny = 2\n")
    return root


class TestTheGatesCarryTheirDecision:
    def test_the_verdict_gate_carries_its_standings_digest_and_grants(self, repo):
        runs = [green(SELECTOR)]
        ctx = context(repo, pipe=pipeline(verdicts_=every_pass(repo, runs), runs=runs))
        ctx.session["pending_overrides"] = [grant()]
        verdict = gate(ctx).verdict
        assert verdict.ruleset == ctx.pipeline["ruleset"]["digest"]
        assert [s["rule"] for s in verdict.standings] == [
            "data.single-open", "data.tested", "style.explained",
        ]
        assert all(s["standing"] == "current" for s in verdict.standings)
        assert verdict.grants == ((SINGLE, REASON),)

    def test_owing_nothing_is_an_empty_standing_list_not_none(self, repo):
        sh(repo, "checkout", "--", "app/m.py")
        verdict = gate(context(repo)).verdict
        assert verdict.ok and verdict.standings == ()

    def test_an_na_gate_carries_no_standings(self, repo):
        ctx = context(repo)
        ctx.pipeline = None
        assert gate(ctx).verdict.standings is None

    def test_the_mechanical_gate_carries_its_digest_and_grants(self, repo):
        ctx = context(repo)
        ctx.session["pending_overrides"] = [grant("style.no-print")]
        (result,) = evaluate([MECHANICAL], ctx)
        assert result.verdict.ruleset == ctx.pipeline["ruleset"]["digest"]
        assert result.verdict.grants == (("style.no-print", REASON),)
        assert result.verdict.findings == ()

    def test_no_other_rule_carries_any_of_it(self, repo):
        (result,) = evaluate(["docs.updated"], context(repo))
        v = result.verdict
        assert (v.standings, v.ruleset, v.grants) == (None, None, ())


class TestAnAcknowledgementKeepsTheRecord:
    def test_the_findings_and_grants_survive_an_acknowledged_warn(self):
        record = {"rule": "style.no-print", "status": "fail"}
        failed = Evaluated(MECHANICAL, Level.WARN, Verdict(
            ok=False, message="1 finding", detail="d", fixes=("f",), findings=(record,),
            ruleset="abcdefabcdef", grants=(("style.no-print", REASON),),
        ))
        (acked,) = apply_acknowledgements(
            {"evidence": {"acknowledged": {MECHANICAL: "known"}}}, [failed]
        )
        v = acked.verdict
        assert v.ok and v.skipped and v.message == "acknowledged — known"
        assert (v.detail, v.fixes) == ("", ())
        assert v.findings == (record,) and v.ruleset == "abcdefabcdef"
        assert v.grants == (("style.no-print", REASON),)


# -- spent or held: the one decision (core/rules.grant_outcomes) ------------------------------


def judged(level, *, ok, overridden=((SINGLE, f"{SINGLE}: no verdict"),), grants=None,
           skipped=False):
    grants = ((SINGLE, REASON),) if grants is None else grants
    return Evaluated(VERDICTS, level, Verdict(
        ok=ok, message="m", overridden=overridden, grants=grants, skipped=skipped,
    ))


class TestGrantOutcomes:
    def test_a_grant_that_decided_a_stopping_gate_is_spent(self):
        (o,) = rules.grant_outcomes(judged(Level.BLOCK, ok=True))
        assert (o.guideline, o.gate, o.reason, o.spent) == (SINGLE, VERDICTS, REASON, True)
        assert o.passed == f"{SINGLE}: no verdict" and o.why == ""
        assert o.record() == {
            "guideline": SINGLE, "gate": VERDICTS, "reason": REASON,
            "outcome": "spent", "passed": f"{SINGLE}: no verdict", "why": "",
        }

    def test_an_ask_level_gate_spends_it_too(self):
        (o,) = rules.grant_outcomes(judged(Level.ASK, ok=True))
        assert o.spent

    def test_at_warn_it_is_held(self):
        (o,) = rules.grant_outcomes(judged(Level.WARN, ok=True))
        assert not o.spent and o.why == f"{VERDICTS} is warn here, so nothing was stopped"
        assert o.record()["outcome"] == "held"

    def test_a_gate_still_failing_holds_it(self):
        (o,) = rules.grant_outcomes(judged(Level.BLOCK, ok=False))
        assert not o.spent
        assert o.why == f"{VERDICTS} still failed on another guideline, so the grant passed nothing"

    def test_a_skipped_gate_holds_it(self):
        (o,) = rules.grant_outcomes(judged(Level.BLOCK, ok=True, skipped=True))
        assert not o.spent

    def test_a_grant_that_passed_nothing_is_held_and_says_so(self):
        (o,) = rules.grant_outcomes(judged(Level.BLOCK, ok=True, overridden=()))
        assert not o.spent and o.passed is None
        assert o.why == f"it passed nothing that would have failed {VERDICTS}"

    def test_a_passed_guideline_with_no_grant_read_has_no_reason(self):
        (o,) = rules.grant_outcomes(judged(Level.BLOCK, ok=True, grants=()))
        assert o.spent and o.reason is None

    def test_grants_come_first_in_their_order_then_the_rest(self):
        found = rules.grant_outcomes(judged(
            Level.BLOCK, ok=True,
            overridden=(("b", "b: no verdict"), ("c", "c: no verdict")),
            grants=(("a", "ra"), ("b", "rb")),
        ))
        assert [o.guideline for o in found] == ["a", "b", "c"]

    def test_a_gate_with_no_grant_has_no_outcome(self):
        assert rules.grant_outcomes(judged(Level.BLOCK, ok=True, overridden=(), grants=())) == ()

    def test_the_spend_leaves_a_grant_that_passed_nothing_alone(self, tmp_path, capsys):
        sessions, session = sessions_in(tmp_path / "p", RULESET)
        sessions.grant_override(session.id, SINGLE, REASON, cwd=tmp_path / "p")
        ctx = SimpleNamespace(sessions=sessions)
        unused = judged(Level.BLOCK, ok=True, overridden=())
        _spend_guideline_overrides(ctx, sessions.require(session.id), [unused])
        assert len(sessions.require(session.id).pending_overrides) == 1
        assert capsys.readouterr().err == ""


# -- the write, in domains/pipeline -------------------------------------------------------


@pytest.fixture
def reviewing(tmp_path):
    m = git_project(tmp_path / "proj")
    session = SessionDomain(m).start("landing")
    pipelines = PipelineDomain(m)
    pipelines.start(session.id, task="t")
    p = pipelines.require(session.id)
    p.state = "reviewing"
    pipelines._save(session.id, p)
    return pipelines, session.id


def given(pipelines, monkeypatch, *verdicts_):
    monkeypatch.setattr(pipelines, "evaluate_gates", lambda *a, **k: list(verdicts_))


FINDING = {"rule": "style.no-print", "status": "fail", "finding": "new"}


def standing() -> dict:
    return verdicts.Standing(SINGLE, "blocker", verdicts.MISSING, override=REASON).record()


def landing_gates(*, mechanical_ok=True, level=Level.WARN):
    return [
        Evaluated("docs.updated", Level.BLOCK, Verdict.clean("docs updated")),
        Evaluated("guidelines.ruleset_valid", Level.BLOCK, Verdict.clean("ruleset loads")),
        Evaluated(MECHANICAL, level, Verdict(
            ok=mechanical_ok, message="mech", detail="long detail", findings=(FINDING,),
            ruleset="abcdefabcdef",
        )),
        Evaluated(VERDICTS, Level.BLOCK, Verdict(
            ok=True, message="verd", standings=(standing(),), ruleset="abcdefabcdef",
            overridden=((SINGLE, f"{SINGLE}: no verdict"),), grants=((SINGLE, REASON),),
        )),
    ]


class TestTheLandingRecord:
    def test_a_landing_appends_what_the_gates_decided(self, reviewing, monkeypatch):
        pipelines, sid = reviewing
        given(pipelines, monkeypatch, *landing_gates())
        pipelines.advance(sid, "committed")
        (entry,) = pipelines.current(sid).evidence[KEY]
        assert entry == {
            "from": "reviewing", "to": "committed", "forced": False,
            "gates": [
                {"rule": "guidelines.ruleset_valid", "level": "block", "ok": True,
                 "skipped": False, "blocks": False, "message": "ruleset loads"},
                {"rule": MECHANICAL, "level": "warn", "ok": True, "skipped": False,
                 "blocks": False, "message": "mech", "ruleset": "abcdefabcdef",
                 "findings": [FINDING]},
                {"rule": VERDICTS, "level": "block", "ok": True, "skipped": False,
                 "blocks": False, "message": "verd", "ruleset": "abcdefabcdef",
                 "standings": [standing()]},
            ],
            "grants": [{
                "guideline": SINGLE, "gate": VERDICTS, "reason": REASON,
                "outcome": "spent", "passed": f"{SINGLE}: no verdict", "why": "",
            }],
        }

    def test_it_survives_a_reload(self, reviewing, monkeypatch):
        pipelines, sid = reviewing
        given(pipelines, monkeypatch, *landing_gates())
        pipelines.advance(sid, "committed")
        fresh = PipelineDomain(pipelines.manifest)
        assert len(fresh.current(sid).evidence[KEY]) == 1

    def test_a_forced_landing_records_the_gate_it_went_past(self, reviewing, monkeypatch):
        pipelines, sid = reviewing
        given(pipelines, monkeypatch, *landing_gates(mechanical_ok=False, level=Level.BLOCK))
        pipelines.advance(sid, "committed", force=True, reason="known finding")
        (entry,) = pipelines.current(sid).evidence[KEY]
        assert entry["forced"] is True
        mech = next(g for g in entry["gates"] if g["rule"] == MECHANICAL)
        assert mech["blocks"] is True and mech["ok"] is False
        assert pipelines.current(sid).overrides[-1]["gates"] == [MECHANICAL]

    def test_a_refused_landing_records_nothing(self, reviewing, monkeypatch):
        pipelines, sid = reviewing
        given(pipelines, monkeypatch, *landing_gates(mechanical_ok=False, level=Level.BLOCK))
        with pytest.raises(GateFailure):
            pipelines.advance(sid, "committed")
        assert KEY not in pipelines.current(sid).evidence

    def test_an_advance_into_a_state_that_is_not_a_landing_records_nothing(
        self, reviewing, monkeypatch
    ):
        pipelines, sid = reviewing
        given(pipelines, monkeypatch, *landing_gates())
        pipelines.advance(sid, "refactor")
        assert KEY not in pipelines.current(sid).evidence

    def test_a_landing_with_no_guidelines_gate_records_nothing(self, reviewing, monkeypatch):
        pipelines, sid = reviewing
        given(pipelines, monkeypatch, Evaluated("docs.updated", Level.BLOCK, Verdict.clean()))
        pipelines.advance(sid, "committed")
        assert KEY not in pipelines.current(sid).evidence

    def test_a_disabled_family_gate_is_recorded_as_it_ran(self, reviewing, monkeypatch):
        pipelines, sid = reviewing
        given(pipelines, monkeypatch, Evaluated(MECHANICAL, Level.OFF, Verdict.na("disabled")))
        pipelines.advance(sid, "committed")
        (entry,) = pipelines.current(sid).evidence[KEY]
        assert entry["gates"] == [{"rule": MECHANICAL, "level": "off", "ok": True,
                                   "skipped": True, "blocks": False, "message": "disabled"}]
        assert entry["grants"] == []

    def test_a_second_landing_is_appended_beside_the_first(self, reviewing, monkeypatch):
        pipelines, sid = reviewing
        given(pipelines, monkeypatch, *landing_gates())
        pipelines.advance(sid, "committed")
        pipelines.advance(sid, "committed", force=True, reason="again")
        assert [e["forced"] for e in pipelines.current(sid).evidence[KEY]] == [False, True]

    def test_the_record_holds_no_clock_time(self, reviewing, monkeypatch):
        pipelines, sid = reviewing
        pipelines.clock = lambda: 1234567.0
        given(pipelines, monkeypatch, *landing_gates())
        pipelines.advance(sid, "committed")
        (entry,) = pipelines.current(sid).evidence[KEY]
        assert "1234567" not in repr(entry)


# -- end to end, through the CLI ------------------------------------------------------------------


def _project(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    monkeypatch.chdir(root)
    assert run("session", "start", "landing") == 0
    assert run("pipeline", "start", "--task", "t") == 0
    write(root, "app/m.py", "x = 1\ny = 2\n")
    _review(root)
    capsys.readouterr()
    return root


def with_status(base):
    return dataclasses.replace(
        base, steps=(*base.steps, cli("record", "pipeline", "status"))
    )


def landed(tmp_path, **kwargs):
    steps = by_label(replay(with_status(scenario(**kwargs)), workdir=tmp_path / "r"))
    assert steps["land"].exit_code == 0
    (entry,) = steps["record"].document["evidence"][KEY]
    return steps, entry


class TestEndToEnd:
    def test_strict_the_spent_grant_is_on_the_record(self, tmp_path):
        steps, entry = landed(tmp_path)
        (o,) = entry["grants"]
        assert (o["guideline"], o["gate"], o["outcome"]) == (ANSWER, VERDICTS, "spent")
        verd = next(g for g in entry["gates"] if g["rule"] == VERDICTS)
        (standing,) = verd["standings"]
        assert standing["rule"] == ANSWER and standing["override"] == REASON
        assert steps["after"].document["session"]["overrides"][0]["rule"] == ANSWER

    def test_standard_the_held_grant_is_on_the_record(self, tmp_path):
        _, entry = landed(tmp_path, profile=None)
        (o,) = entry["grants"]
        assert o["outcome"] == "held" and "is warn here" in o["why"]

    def test_the_answered_landing_records_the_cited_pass(self, tmp_path):
        base = load_named("valid-ruleset")
        steps = tuple(
            dataclasses.replace(s, label="land")
            if tuple(s.argv or ()) == ("pipeline", "advance", "committed") else s
            for s in base.steps
        )
        replayed = by_label(replay(
            with_status(dataclasses.replace(base, steps=steps)), workdir=tmp_path / "r"
        ))
        assert replayed["land"].exit_code == 0
        (entry,) = replayed["record"].document["evidence"][KEY]
        assert entry["grants"] == []
        verd = next(g for g in entry["gates"] if g["rule"] == VERDICTS)
        assert verd["ok"] is True and verd["ruleset"]
        (standing,) = verd["standings"]
        assert (standing["rule"], standing["standing"], standing["status"]) == (
            ANSWER, "current", "pass",
        )
        mech = next(g for g in entry["gates"] if g["rule"] == MECHANICAL)
        # The scenario's warn cochange finding is kept as the gate reported it.
        assert [f["finding"] for f in mech["findings"]] == ["new"]
        assert mech["ok"] is True and mech["ruleset"] == verd["ruleset"]
        assert {g["rule"] for g in entry["gates"]} >= {
            "guidelines.ruleset_valid", "guidelines.generated_current", MECHANICAL, VERDICTS,
        }

    def test_the_record_is_the_advance_s_own_verdicts(self, tmp_path):
        steps, entry = landed(tmp_path, profile=None)
        gates = {g["rule"]: g for g in steps["land"].document["gates"]}
        for recorded in entry["gates"]:
            shown = gates[recorded["rule"]]
            assert (recorded["ok"], recorded["message"]) == (shown["ok"], shown["message"])

