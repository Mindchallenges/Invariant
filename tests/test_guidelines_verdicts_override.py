"""Override by guideline id for judgement guidelines (IDEA_031 A4.5).

ADR-012 ruling 4 and D12, DN-9 and DN-12, and A3.7's mechanism. A grant
naming a judgement guideline is the grant A3.7 built: held in
`session.pending_overrides`, spent into `session.overrides` by the pipeline
advance it lets through, with surface `cli`. Its family gate is
`guidelines.verdicts_complete`, and in one evaluation it passes every failure
of that guideline (DN-9): no verdict, a stale one, and a current fail, at
blocker or at warn. It is named wherever it acts, and never hidden:

- the engine's mechanical N/A still answers the rule (the A4.3 ruling), so a
  grant on it passes nothing and is named as unused, as on a current pass;
- the gate and `guidelines verdicts show` read one `read_standings`, so the
  override reads identically in both;
- only a grant that turned a failing gate into a passing one at a stopping
  level is spent; a grant at warn is held for the session, loudly;
- no check reads the acting agent (DN-12).
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
from test_cli import payload, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, _review, write
from test_guidelines_override import mechanical, sessions_in
from test_guidelines_verdicts_complete import (
    CITE,
    PYTHON,
    RULESET,
    RULESET_OBJ,
    SELECTOR,
    context,
    every_pass,
    facts,
    gate,
    green,
    holding,
    pipeline,
    record,
    sh,
    submitted,
)

from invariant.cli.conformance import replay
from invariant.cli.main import _spend_guideline_overrides
from invariant.core import verdicts
from invariant.core.conformance import Step, load_named, status_of
from invariant.core.errors import UsageError
from invariant.core.manifest import Level
from invariant.core.rules import (
    MECHANICAL_GATE,
    VERDICTS_GATE,
    Evaluated,
    Verdict,
    evaluate,
    guideline_family_gate,
    guideline_gate_of,
    read_standings,
    standings_verdict,
)

RULE = "guidelines.verdicts_complete"
MECHANICAL = "guidelines.mechanical_clean"
SINGLE = "data.single-open"
EXPLAINED = "style.explained"
REASON = "reviewed by hand in the design call"


def grant(rule: str = SINGLE, reason: str = REASON) -> dict:
    return {"rule": rule, "reason": reason, "calls": 1, "granted": 0.0}


def standings(records, grants, f=None):
    return {
        s.rule: s
        for s in verdicts.standings(RULESET_OBJ, records, f or facts(), grants=grants)
    }


# -- the standing a grant leaves (core/verdicts, DN-9) ---------------------------------------


class TestAStandingUnderAGrant:
    def test_a_missing_verdict_is_passed_by_the_grant(self):
        s = standings([], {SINGLE: REASON})[SINGLE]
        assert s.standing == verdicts.MISSING
        assert s.overridden and s.failing
        assert not s.blocks and not s.warns
        assert s.override == REASON

    def test_its_description_names_the_grant_and_its_reason(self):
        told = standings([], {SINGLE: REASON})[SINGLE].describe()
        assert told.startswith(f"{SINGLE}: no verdict")
        assert "passed by override" in told
        assert f"invariant override {SINGLE}" in told and REASON in told

    def test_a_stale_verdict_is_passed_by_the_grant(self):
        s = standings([record(blob="0" * 12)], {SINGLE: REASON})[SINGLE]
        assert s.standing == verdicts.STALE and s.overridden and not s.blocks
        assert "no longer holds" in s.describe() and "passed by override" in s.describe()

    def test_a_current_fail_at_blocker_is_passed_by_the_grant(self):
        s = standings([record(status="fail")], {SINGLE: REASON})[SINGLE]
        assert s.standing == verdicts.CURRENT and s.overridden and not s.blocks
        assert "fail at blocker" in s.describe()

    def test_a_current_fail_at_warn_is_passed_too_and_decides_nothing(self):
        """DN-9: a warn guideline's failures are covered as well. A warn fail
        never blocked, so the grant passes it without having stopped anything."""
        s = standings([record(EXPLAINED, status="fail")], {EXPLAINED: REASON})[EXPLAINED]
        assert s.overridden and not s.warns and not s.blocks
        assert not s.failing
        assert "fail at warn" in s.describe() and "passed by override" in s.describe()

    def test_a_missing_verdict_on_a_warn_guideline_is_passed(self):
        s = standings([], {EXPLAINED: REASON})[EXPLAINED]
        assert s.overridden and s.failing and not s.blocks

    def test_a_current_pass_leaves_the_grant_unused(self):
        s = standings([record()], {SINGLE: REASON})[SINGLE]
        assert s.standing == verdicts.CURRENT and not s.overridden
        assert s.override is None

    def test_the_engines_na_answers_the_rule_and_the_grant_is_unused(self):
        na, _ = holding()
        s = standings([record(status="fail")], {SINGLE: REASON}, facts(na=na))[SINGLE]
        assert s.standing == verdicts.ENGINE and not s.overridden
        assert s.set_aside == "fail"

    def test_another_guidelines_standing_is_untouched(self):
        s = standings([], {SINGLE: REASON})["data.tested"]
        assert s.standing == verdicts.MISSING and s.blocks and not s.overridden

    def test_no_grant_changes_nothing(self):
        assert standings([], {}) == {
            s.rule: s for s in verdicts.standings(RULESET_OBJ, [], facts())
        }


class TestAGrantThatPassesNothing:
    def unused(self, records, grants, f=None):
        found = tuple(standings(records, grants, f).values())
        return {u.rule: u for u in verdicts.unused_grants(found, grants)}

    def test_a_grant_on_a_rule_with_a_current_pass_is_named_unused(self):
        u = self.unused([record()], {SINGLE: REASON})[SINGLE]
        assert "its latest verdict holds" in u.describe()
        assert REASON in u.describe()
        assert f"invariant override {SINGLE} --clear" in u.describe()

    def test_a_grant_on_a_rule_the_engine_answers_is_named_unused(self):
        na, _ = holding()
        u = self.unused([], {SINGLE: REASON}, facts(na=na))[SINGLE]
        assert "the engine answers it" in u.describe()

    def test_a_grant_on_a_rule_the_change_does_not_owe_is_named_unused(self):
        u = self.unused([], {"data.design-only": REASON})["data.design-only"]
        assert "the change does not owe it a verdict" in u.describe()

    def test_a_grant_that_passed_something_is_not_named_unused(self):
        assert self.unused([], {SINGLE: REASON}) == {}


# -- the gate reads the grant (core/rules) ---------------------------------------------------


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


def granted(root, *grants, verdicts_=(), runs=()):
    ctx = context(root, pipe=pipeline(verdicts_=list(verdicts_), runs=list(runs)))
    ctx.session["pending_overrides"] = list(grants)
    return ctx


class TestTheGateReadsTheGrant:
    def answered_but(self, root, rule_id):
        runs = [green(SELECTOR)]
        recorded = [r for r in every_pass(root, runs) if r["rule"] != rule_id]
        return recorded, runs

    def test_a_missing_verdict_passed_by_the_grant_passes_the_gate(self, repo):
        recorded, runs = self.answered_but(repo, SINGLE)
        verdict = gate(granted(repo, grant(), verdicts_=recorded, runs=runs)).verdict
        assert verdict.ok, verdict.message
        assert "1 passed by override" in verdict.message
        assert SINGLE in verdict.detail and REASON in verdict.detail
        assert "the pipeline advance this lets through spends the grant" in verdict.detail
        assert verdict.overridden == ((SINGLE, f"{SINGLE}: no verdict"),)

    def test_without_the_grant_the_gate_fails_on_it(self, repo):
        recorded, runs = self.answered_but(repo, SINGLE)
        verdict = gate(granted(repo, verdicts_=recorded, runs=runs)).verdict
        assert not verdict.ok and SINGLE in verdict.message
        assert verdict.overridden == ()

    def test_a_blocker_fail_passed_by_the_grant_passes_the_gate(self, repo):
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs) + submitted(
            repo, [{"rule": SINGLE, "status": "fail", "citations": CITE}], runs=runs
        )
        verdict = gate(granted(repo, grant(), verdicts_=recorded, runs=runs)).verdict
        assert verdict.ok, verdict.message
        assert verdict.overridden == ((SINGLE, f"{SINGLE}: fail at blocker"),)

    def test_a_stale_verdict_passed_by_the_grant_passes_the_gate(self, repo):
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs)
        write(repo, "app/m.py", "x = 1\ny = 3\n")
        # Both cited rules went stale with the edit; each needs its own grant.
        ctx = granted(repo, grant(), grant(EXPLAINED), verdicts_=recorded, runs=runs)
        verdict = gate(ctx).verdict
        assert verdict.ok, verdict.message
        passed = dict(verdict.overridden)
        assert set(passed) == {SINGLE, EXPLAINED}
        assert "no longer holds" in passed[SINGLE]

    def test_every_other_guideline_stays_in_force(self, repo):
        verdict = gate(granted(repo, grant())).verdict
        assert not verdict.ok
        assert "data.tested: no verdict" in verdict.message
        assert "1 passed by override" in verdict.message
        assert any("invariant override data.tested" in f for f in verdict.fixes)

    def test_a_warn_fail_passed_by_the_grant_is_named_as_deciding_nothing(self, repo):
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs) + submitted(
            repo, [{"rule": EXPLAINED, "status": "fail", "citations": CITE}], runs=runs
        )
        verdict = gate(granted(repo, grant(EXPLAINED), verdicts_=recorded, runs=runs)).verdict
        assert verdict.ok
        assert EXPLAINED in verdict.detail and "passed by override" in verdict.detail
        assert "never blocks, so nothing spends the grant" in verdict.detail
        assert verdict.overridden == ()

    def test_an_unused_grant_is_named_in_the_detail(self, repo):
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs)
        verdict = gate(granted(repo, grant(), verdicts_=recorded, runs=runs)).verdict
        assert verdict.ok
        assert f"override held for {SINGLE}" in verdict.detail
        assert "unused here" in verdict.detail
        assert verdict.overridden == ()

    def test_the_fixes_name_the_override_beside_the_verdict(self, repo):
        verdict = gate(granted(repo)).verdict
        assert any(
            f"invariant override {SINGLE} --reason '<why>'" in f for f in verdict.fixes
        )

    def test_a_mechanical_guidelines_grant_is_not_read_here(self, repo):
        verdict = gate(granted(repo, grant("style.no-print"))).verdict
        assert "style.no-print" not in verdict.detail
        assert "style.no-print" not in verdict.message

    def test_a_judgement_grant_is_not_read_by_the_mechanical_gate(self, repo):
        (result,) = evaluate([MECHANICAL], granted(repo, grant()))
        assert SINGLE not in result.verdict.detail

    def test_the_gate_and_the_read_agree_under_a_grant(self, repo):
        recorded, runs = self.answered_but(repo, SINGLE)
        ctx = granted(repo, grant(), grant("data.design-only"), verdicts_=recorded, runs=runs)
        read = read_standings(ctx)
        assert gate(ctx).verdict == standings_verdict(read)
        assert read.grants == {SINGLE: REASON, "data.design-only": REASON}
        assert [u.rule for u in read.unused] == ["data.design-only"]

    def test_no_check_reads_the_acting_agent(self, repo):
        """DN-12: the agent field is audit only."""
        recorded, runs = self.answered_but(repo, SINGLE)
        with_agent = [dict(r, agent="someone") for r in recorded]
        plain = gate(granted(repo, grant(), verdicts_=recorded, runs=runs)).verdict
        stamped = gate(granted(repo, grant(), verdicts_=with_agent, runs=runs)).verdict
        assert plain == stamped


# -- the family gate a guideline answers to ------------------------------------------------------


class TestTheFamilyGate:
    def test_the_two_family_gates(self):
        assert (MECHANICAL_GATE, VERDICTS_GATE) == (MECHANICAL, RULE)

    def test_an_engine_guideline_has_no_family_gate(self):
        from test_guidelines_override import ENGINE
        from test_guidelines_verdicts_complete import REGISTERED

        from invariant.core import guidelines

        ruleset = guidelines.parse(RULESET + ENGINE, registered=REGISTERED)
        assert guideline_family_gate(ruleset.by_id("tests.green")) is None

    def test_a_judgement_guideline_answers_to_the_verdicts_gate(self):
        assert guideline_family_gate(RULESET_OBJ.by_id(SINGLE)) == RULE

    def test_a_mechanical_guideline_answers_to_the_mechanical_gate(self):
        assert guideline_family_gate(RULESET_OBJ.by_id("style.no-print")) == MECHANICAL

    def test_the_gate_of_a_guideline_is_read_from_the_tree_in_hand(self, repo):
        ctx = context(repo)
        assert guideline_gate_of(ctx, SINGLE) == RULE
        assert guideline_gate_of(ctx, "style.no-print") == MECHANICAL
        assert guideline_gate_of(ctx, "style.nothing") is None

    def test_no_gate_is_read_where_no_ruleset_loads(self, repo):
        write(repo, RULESET_REL, RULESET.replace("severity: warn", "severity: loud"))
        assert guideline_gate_of(context(repo), SINGLE) is None


# -- granting one (domains/session) ----------------------------------------------------------


class TestGrantingOne:
    def test_a_blocker_judgement_guideline_is_granted(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p", RULESET)
        updated = sessions.grant_override(session.id, SINGLE, REASON, cwd=tmp_path / "p")
        (held,) = updated.pending_overrides
        assert set(held) == {"rule", "reason", "calls", "granted"}
        assert (held["rule"], held["reason"], held["calls"]) == (SINGLE, REASON, 1)

    def test_a_warn_judgement_guideline_is_granted(self, tmp_path):
        """DN-9: its missing or stale verdict blocks the gate at any severity."""
        sessions, session = sessions_in(tmp_path / "p", RULESET)
        updated = sessions.grant_override(session.id, EXPLAINED, REASON, cwd=tmp_path / "p")
        assert [o["rule"] for o in updated.pending_overrides] == [EXPLAINED]

    def test_a_judgement_guideline_never_owed_a_verdict_is_refused(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p", RULESET)
        with pytest.raises(UsageError, match="never owed a verdict"):
            sessions.grant_override(
                session.id, "data.design-only", REASON, cwd=tmp_path / "p"
            )

    def test_more_than_one_call_names_the_verdicts_gate(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p", RULESET)
        with pytest.raises(UsageError, match=f"one evaluation of {RULE}"):
            sessions.grant_override(
                session.id, SINGLE, REASON, calls=2, cwd=tmp_path / "p"
            )

    def test_a_reason_is_still_required(self, tmp_path):
        sessions, session = sessions_in(tmp_path / "p", RULESET)
        with pytest.raises(UsageError, match="requires --reason"):
            sessions.grant_override(session.id, SINGLE, " ", cwd=tmp_path / "p")


# -- spending it, at the CLI (one spend path for both family gates) -------------------------


def judged(level: Level, *, ok: bool, overridden=((SINGLE, f"{SINGLE}: no verdict"),)):
    return Evaluated(RULE, level, Verdict(ok=ok, message="m", overridden=overridden))


@pytest.fixture
def held(tmp_path):
    sessions, session = sessions_in(tmp_path / "p", RULESET)
    sessions.grant_override(session.id, SINGLE, REASON, cwd=tmp_path / "p")
    return SimpleNamespace(sessions=sessions), sessions.require(session.id)


class TestSpendingIt:
    def test_a_passing_gate_at_block_spends_it_and_records_it(self, held, capsys):
        ctx, session = held
        _spend_guideline_overrides(ctx, session, [judged(Level.BLOCK, ok=True)])
        after = ctx.sessions.require(session.id)
        assert after.pending_overrides == []
        (spent,) = after.overrides
        assert (spent["rule"], spent["reason"], spent["surface"]) == (SINGLE, REASON, "cli")
        err = capsys.readouterr().err
        assert f"[override] {SINGLE}: passed deliberately" in err
        assert f"it would have blocked {RULE}: {SINGLE}: no verdict" in err

    def test_at_warn_it_is_held_and_says_so(self, held, capsys):
        ctx, session = held
        _spend_guideline_overrides(ctx, session, [judged(Level.WARN, ok=True)])
        assert len(ctx.sessions.require(session.id).pending_overrides) == 1
        err = capsys.readouterr().err
        assert "held, not spent" in err and f"{RULE} is warn here" in err

    def test_a_gate_still_failing_is_held(self, held, capsys):
        ctx, session = held
        _spend_guideline_overrides(ctx, session, [judged(Level.BLOCK, ok=False)])
        assert len(ctx.sessions.require(session.id).pending_overrides) == 1
        assert f"{RULE} still failed" in capsys.readouterr().err

    def test_nothing_overridden_spends_nothing(self, held, capsys):
        ctx, session = held
        _spend_guideline_overrides(ctx, session, [judged(Level.BLOCK, ok=True, overridden=())])
        assert len(ctx.sessions.require(session.id).pending_overrides) == 1
        assert capsys.readouterr().err == ""

    def test_the_mechanical_gate_spends_through_the_same_path(self, tmp_path, capsys):
        sessions, session = sessions_in(tmp_path / "m")
        sessions.grant_override(session.id, "style.no-print", "on purpose", cwd=tmp_path / "m")
        ctx = SimpleNamespace(sessions=sessions)
        _spend_guideline_overrides(ctx, sessions.require(session.id), [mechanical(Level.BLOCK, ok=True)])
        assert sessions.require(session.id).pending_overrides == []
        assert f"it would have blocked {MECHANICAL}: app/m.py:2" in capsys.readouterr().err


# -- the command and the read say the same thing -------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    monkeypatch.chdir(root)
    assert run("session", "start", "verdicts") == 0
    assert run("pipeline", "start", "--task", "t") == 0
    write(root, "app/m.py", "x = 1\ny = 2\n")
    _review(root)
    capsys.readouterr()
    return root


class TestTheCommands:
    def test_the_grant_names_the_verdicts_gate(self, project, capsys):
        assert run("override", SINGLE, "--reason", REASON) == 0
        text = capsys.readouterr().out
        assert f"override granted for guideline {SINGLE}" in text
        assert f"one evaluation of {RULE} (warn in this project)" in text
        assert "missing, stale or failing verdict" in text

    def test_a_mechanical_grant_still_names_the_mechanical_gate(self, project, capsys):
        assert run("override", "style.no-print", "--reason", REASON) == 0
        assert f"one evaluation of {MECHANICAL}" in capsys.readouterr().out

    def test_status_names_the_grants_family_gate(self, project, capsys):
        run("override", SINGLE, "--reason", REASON)
        capsys.readouterr()
        run("session", "status")
        text = capsys.readouterr().out
        assert f"override   {SINGLE} — {REASON}" in text
        assert f"one evaluation of {RULE}" in text

    def test_show_names_the_overridden_guideline(self, project, capsys):
        run("override", SINGLE, "--reason", REASON)
        capsys.readouterr()
        assert run("guidelines", "verdicts", "show") == 0
        text = capsys.readouterr().out
        assert f"  missing  {SINGLE}: no verdict; passed by override" in text
        assert "  missing  data.tested: no verdict\n" in text

    def test_show_names_an_unused_grant(self, project, capsys):
        assert run("guidelines", "verdict", SINGLE, "--status", "pass",
                   "--cite", "app/m.py", "2", "2") == 0
        run("override", SINGLE, "--reason", REASON)
        capsys.readouterr()
        run("guidelines", "verdicts", "show")
        text = capsys.readouterr().out
        assert "grants on judgement guidelines that pass nothing here:" in text
        assert f"override held for {SINGLE}" in text

    def test_show_json_carries_the_override(self, project, capsys):
        run("override", SINGLE, "--reason", REASON)
        capsys.readouterr()
        run("guidelines", "verdicts", "show", "--json")
        doc = payload(capsys)
        owed = {o["rule"]: o for o in doc["owed"]}
        assert owed[SINGLE]["override"] == REASON
        assert owed[SINGLE]["overridden"] is True and owed[SINGLE]["blocks"] is False
        assert owed["data.tested"]["override"] is None
        assert doc["unused_grants"] == []

    def test_check_and_show_never_spend_it(self, project, capsys):
        run("override", SINGLE, "--reason", REASON)
        run("check", RULE)
        run("guidelines", "verdicts", "show")
        capsys.readouterr()
        run("session", "status", "--json")
        session = payload(capsys)["session"]
        assert [o["rule"] for o in session["pending_overrides"]] == [SINGLE]
        assert session["overrides"] == []


# -- end to end: the valid-ruleset scenario, its verdict replaced by a grant -------------------


ANSWER = "style.answer-is-documented"


def cli(label, *argv):
    return Step(kind="cli", label=label, where="worktree", argv=tuple(argv))


def scenario(*, grant_it=True, profile="strict"):
    base = load_named("valid-ruleset")
    steps = []
    for step in base.steps:
        argv = tuple(step.argv or ())
        if argv[:2] == ("guidelines", "verdict"):
            if grant_it:
                steps.append(cli("grant", "override", ANSWER, "--reason", REASON))
            continue
        if argv == ("pipeline", "advance", "committed"):
            steps.append(dataclasses.replace(step, label="land"))
            continue
        steps.append(step)
    steps.append(cli("after", "session", "status"))
    manifest = dict(base.manifest)
    if profile is not None:
        manifest["enforcement"] = {"profile": profile}
    return dataclasses.replace(base, steps=tuple(steps), manifest=manifest)


def by_label(replayed):
    return {r.label: r for r in replayed.steps if r.label}


class TestEndToEnd:
    def test_strict_the_grant_lets_the_landing_through_and_is_spent(self, tmp_path):
        steps = by_label(replay(scenario(), workdir=tmp_path / "r"))
        assert steps["grant"].exit_code == 0
        land = steps["land"]
        assert land.exit_code == 0
        (gate_,) = [g for g in land.document["gates"] if g["rule"] == RULE]
        assert status_of(gate_) == "pass"
        assert "passed by override" in gate_["message"]
        after = steps["after"].document["session"]
        assert after["pending_overrides"] == []
        (spent,) = after["overrides"]
        assert (spent["rule"], spent["surface"], spent["reason"]) == (ANSWER, "cli", REASON)

    def test_strict_without_the_grant_the_landing_is_refused(self, tmp_path):
        steps = by_label(replay(scenario(grant_it=False), workdir=tmp_path / "r"))
        assert steps["land"].exit_code != 0

    def test_standard_the_grant_is_held_for_the_session(self, tmp_path):
        steps = by_label(replay(scenario(profile=None), workdir=tmp_path / "r"))
        assert steps["land"].exit_code == 0
        after = steps["after"].document["session"]
        assert [o["rule"] for o in after["pending_overrides"]] == [ANSWER]
        assert after["overrides"] == []


# -- the acknowledgement refusal names the route ------------------------------------------------


class TestTheAckRefusalNamesTheRoute:
    def test_the_verdict_gate_names_the_guideline_grant_and_the_forced_advance(self, repo):
        from invariant.core.machines import load_pipeline_machine
        from invariant.core.manifest import Manifest
        from invariant.domains.pipeline import _override_routes

        machine = load_pipeline_machine(Manifest.locate(repo))
        text = _override_routes(RULE, (), machine)
        assert "invariant override <guideline-id> --reason" in text
        assert "missing, stale or failing verdict" in text
        assert "pipeline advance <state> --force" in text
