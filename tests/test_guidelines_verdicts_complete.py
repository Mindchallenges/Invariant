"""`guidelines.verdicts_complete`: every owed judgement guideline has a verdict
that still holds (IDEA_031 A4 increment 3).

ADR-012 D5 ("The gate", "Staleness") and the A4 rulings. The gate rides the
landing entry beside the other guidelines gates. It asks, for the change in
hand, which judgement guidelines are owed a verdict, and whether each has one
that still holds:

- a rule is owed when its phases name review, landing or always (DN-2) and a
  changed file present in the tree falls under it, outside its path
  exceptions (DN-3); a deleted file owes nothing;
- a mechanical N/A condition the engine finds true answers the rule, and no
  verdict is owed (D5 item 5); a pass or fail recorded on it is set aside and
  named, because the gate is over applicable rules only;
- the latest recorded verdict on a rule answers it, and is checked again at
  the gate by the predicates submission ran (P1 to P6), through the same
  `core/verdicts.check`: a cited file whose blob changed, a ruleset that
  drifted from the pin (DN-6), a test whose latest run is no longer green
  (DN-8) and a mechanical N/A the engine no longer finds true each make it
  stale, and a stale verdict is a missing one;
- a current `fail` at blocker severity fails the gate; at warn it is reported.

The review-role predicate is submission's alone (DN-2): the gate is asked at
the landing, not in a review state.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write

from invariant.core import guidelines, verdicts
from invariant.core.findings import NaAnswer
from invariant.core.machines import GUIDELINES_GATES, load_pipeline_machine
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, all_rules, evaluate, plan_verdicts
from invariant.core.verdicts import Facts

REGISTERED = frozenset(spec.id for spec in all_rules())
RULE = "guidelines.verdicts_complete"

RULESET = """\
version: 1
sections:
  - id: data
    title: Data safety
  - id: style
    title: Style
rules:
  - id: data.single-open
    section: data
    title: Only one store is open at a time
    statement: No code path holds two store handles at once.
    severity: blocker
    phases: [review]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: citation
      scope: diff
      not_applicable_when:
        - id: no-code
          mechanical: {no_executable_change: true}
        - id: read-only
          claim: The change only reads through an already-open store.
    exceptions:
      - path: app/legacy.py
        reason: retired next release
  - id: data.tested
    section: data
    title: The store's invariant is tested
    statement: The single-open invariant is backed by a test.
    severity: blocker
    phases: [always]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: test
      scope: file
  - id: style.explained
    section: style
    title: The answer is explained
    statement: Wherever the answer is computed, its reason is stated.
    severity: warn
    phases: [landing]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: citation
      scope: file
  - id: data.design-only
    section: data
    title: A design-time rule
    statement: Seen while designing only.
    severity: blocker
    phases: [design]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: citation
      scope: file
  - id: style.no-print
    section: style
    title: No print calls
    statement: Code never prints.
    severity: blocker
    phases: [review]
    applies_to: ["app/*.py"]
    check:
      kind: pattern
      engine: regex
      in: any
      match: 'print\\('
"""

RULESET_OBJ = guidelines.parse(RULESET, registered=REGISTERED)
DIGEST = RULESET_OBJ.digest
SELECTOR = "tests/test_m.py::test_open"
M_BYTES = b"one\ntwo\nthree\n"
OWED = ("data.single-open", "data.tested", "style.explained")


def green(*selectors, passed=True, read_as_red=False):
    return {"selectors": list(selectors), "passed": passed, "read_as_red": read_as_red}


def facts(**overrides) -> Facts:
    values = {
        "tree_digest": DIGEST,
        "pin": DIGEST,
        # The gate asks at the landing: no review role, and that must not matter.
        "state": "committed",
        "roles": ("landing",),
        "touched": ("app/m.py",),
        "read": {"app/m.py": M_BYTES}.get,
        "added": lambda: {"app/m.py": frozenset({2})},
        "test_runs": (green(SELECTOR),),
        "na": lambda rule, condition, files: NaAnswer(rule.id, condition.id, False, "app/m.py changed"),
    }
    values.update(overrides)
    return Facts(**values)


def record(rule="data.single-open", status="pass", *, blob=None, **fields):
    """A record as `evidence["verdicts"]` holds it."""
    recorded = {
        "rule": rule, "status": status, "citations": [], "tests": [],
        "na_condition": None, "note": None, "ruleset": DIGEST,
        "state": "reviewing", "surface": "cli",
    }
    if rule in ("data.single-open", "style.explained") and status != "na":
        recorded["citations"] = [{
            "path": "app/m.py", "lines": [2, 2],
            "blob": blob or guidelines.digest_of(M_BYTES),
        }]
    if rule == "data.tested" and status != "na":
        recorded["tests"] = [SELECTOR]
    recorded.update(fields)
    return recorded


ALL_PASS = [record("data.single-open"), record("data.tested"), record("style.explained")]


def standings(records, f=None):
    return {s.rule: s for s in verdicts.standings(RULESET_OBJ, records, f or facts())}


# -- which rules are owed (DN-2, DN-3) -------------------------------------------------------


class TestOwed:
    def test_a_judgement_rule_owed_in_review_landing_or_always_is_owed(self):
        assert tuple(r.id for r in verdicts.owed(RULESET_OBJ, facts())) == OWED

    def test_a_design_only_rule_owes_none(self):
        assert "data.design-only" not in standings([])

    def test_a_mechanical_rule_is_never_owed_a_verdict(self):
        assert "style.no-print" not in standings([])

    def test_a_change_meeting_no_rule_owes_nothing(self):
        assert standings([], facts(touched=("docs/notes.md",))) == {}

    def test_a_path_excepted_file_owes_nothing_under_that_rule(self):
        owed = standings([], facts(touched=("app/legacy.py",)))
        assert "data.single-open" not in owed and "data.tested" in owed

    def test_no_changed_set_owes_nothing_to_ask(self):
        assert verdicts.owed(RULESET_OBJ, facts(touched=None)) == ()


# -- a missing verdict, and a current one ------------------------------------------------------


class TestAnswered:
    def test_no_verdict_is_missing_and_blocks(self):
        s = standings([])["data.single-open"]
        assert s.standing == verdicts.MISSING and s.blocks
        assert "no verdict" in s.describe()

    def test_a_current_pass_answers_the_rule(self):
        got = standings(ALL_PASS)
        assert all(got[r].standing == verdicts.CURRENT for r in OWED)
        assert not any(s.blocks or s.warns for s in got.values())
        assert got["data.tested"].describe() == "data.tested: pass"

    def test_the_review_role_is_not_asked_at_the_gate(self):
        s = standings(ALL_PASS, facts(roles=(), roles_problem="no machine"))["data.single-open"]
        assert s.standing == verdicts.CURRENT, s.problems

    def test_a_claimed_na_answers_the_rule(self):
        s = standings([record(status="na", na_condition="read-only")])["data.single-open"]
        assert s.standing == verdicts.CURRENT and s.status == "na" and not s.blocks

    def test_the_latest_verdict_on_a_rule_answers_it(self):
        s = standings([record(), record(status="fail")])["data.single-open"]
        assert s.status == "fail" and s.blocks

    def test_a_later_pass_answers_an_earlier_fail(self):
        s = standings([record(status="fail"), record()])["data.single-open"]
        assert s.status == "pass" and not s.blocks

    def test_a_stale_latest_verdict_is_not_rescued_by_an_earlier_one(self):
        s = standings([record(), record(blob="000000000000")])["data.single-open"]
        assert s.standing == verdicts.STALE and s.blocks

    def test_a_record_that_is_not_an_object_is_skipped(self):
        assert standings(["junk", record()])["data.single-open"].standing == verdicts.CURRENT

    def test_a_record_of_another_rule_does_not_answer_this_one(self):
        assert standings([record("data.tested")])["data.single-open"].standing == verdicts.MISSING


# -- a fail -------------------------------------------------------------------------------------


class TestFail:
    def test_a_fail_at_blocker_blocks(self):
        s = standings([record(status="fail")])["data.single-open"]
        assert s.standing == verdicts.CURRENT and s.blocks and not s.warns
        assert "fail" in s.describe() and "blocker" in s.describe()

    def test_a_fail_at_warn_is_reported_and_does_not_block(self):
        s = standings([record("style.explained", status="fail")])["style.explained"]
        assert s.warns and not s.blocks


# -- staleness: every recorded verdict checked again --------------------------------------------


class TestStale:
    def test_a_cited_file_changed_since_the_verdict_is_stale(self):
        s = standings([record(blob="000000000000")])["data.single-open"]
        assert s.standing == verdicts.STALE and s.blocks
        assert [p.predicate for p in s.problems] == ["P3"]
        assert "no longer holds" in s.describe() and "P3" in s.describe()

    def test_a_ruleset_drifted_from_the_pin_makes_every_verdict_stale(self):
        drifted = facts(tree_digest="111111111111")
        got = standings(ALL_PASS, drifted)
        assert all(got[r].standing == verdicts.STALE for r in OWED)
        assert "P1" in [p.predicate for p in got["data.tested"].problems]

    def test_a_null_pin_lets_the_trees_digest_stand(self):
        assert standings(ALL_PASS, facts(pin=None))["data.tested"].standing == verdicts.CURRENT

    def test_a_null_pin_and_a_ruleset_edited_since_the_verdict_is_stale(self):
        s = standings(ALL_PASS, facts(pin=None, tree_digest="111111111111"))["data.tested"]
        assert s.standing == verdicts.STALE and "P1" in [p.predicate for p in s.problems]

    def test_a_test_whose_latest_run_went_red_is_stale(self):
        red = facts(test_runs=(green(SELECTOR), green(SELECTOR, passed=False)))
        s = standings(ALL_PASS, red)["data.tested"]
        assert s.standing == verdicts.STALE and [p.predicate for p in s.problems] == ["P4"]

    def test_a_cited_range_no_longer_meeting_the_diff_is_stale(self):
        s = standings(ALL_PASS, facts(added=lambda: {"app/m.py": frozenset({3})}))
        assert s["data.single-open"].standing == verdicts.STALE

    def test_a_cited_file_the_change_no_longer_touches_is_stale(self):
        got = standings(ALL_PASS, facts(touched=("app/n.py",)))
        assert got["style.explained"].standing == verdicts.STALE
        assert [p.predicate for p in got["style.explained"].problems] == ["P3"]

    def test_a_mechanical_na_the_engine_no_longer_finds_true_is_stale(self):
        s = standings([record(status="na", na_condition="no-code")])["data.single-open"]
        assert s.standing == verdicts.STALE and [p.predicate for p in s.problems] == ["P5"]

    def test_a_record_that_no_longer_parses_is_stale(self):
        s = standings([record(status="maybe")])["data.single-open"]
        assert s.standing == verdicts.STALE and s.problems[0].predicate == verdicts.SHAPE

    def test_engine_owned_keys_are_not_read_as_unknown(self):
        s = standings([record(agent="someone")])["data.single-open"]
        assert s.standing == verdicts.CURRENT, s.problems


# -- a mechanical N/A the engine finds true (D5 item 5) ------------------------------------------


def holding(condition_id="no-code"):
    seen = []

    def na(rule, condition, files):
        seen.append((rule.id, condition.id))
        return NaAnswer(rule.id, condition.id, condition.id == condition_id, "comment-only")

    return na, seen


class TestEngineAnswers:
    def test_a_true_mechanical_condition_answers_the_rule_with_no_verdict(self):
        na, seen = holding()
        s = standings([], facts(na=na))["data.single-open"]
        assert s.standing == verdicts.ENGINE and not s.blocks and not s.warns
        assert "no-code" in s.describe() and "comment-only" in s.describe()
        assert seen == [("data.single-open", "no-code")]

    @pytest.mark.parametrize("status", ["pass", "fail"])
    def test_a_pass_or_fail_recorded_on_it_is_set_aside_and_named(self, status):
        na, _ = holding()
        s = standings([record(status=status)], facts(na=na))["data.single-open"]
        assert s.standing == verdicts.ENGINE and not s.blocks
        assert s.set_aside == status
        assert f"the {status} recorded on it is set aside" in s.describe()

    def test_an_na_recorded_on_it_is_not_set_aside(self):
        na, _ = holding()
        s = standings([record(status="na", na_condition="no-code")], facts(na=na))
        assert s["data.single-open"].set_aside is None

    def test_an_unanswerable_condition_leaves_the_rule_owed(self):
        na = lambda rule, condition, files: NaAnswer(rule.id, condition.id, None, "no mask")
        assert standings([], facts(na=na))["data.single-open"].standing == verdicts.MISSING

    def test_no_engine_answer_read_leaves_the_rule_owed(self):
        assert standings([], facts(na=None))["data.single-open"].standing == verdicts.MISSING

    def test_a_claim_is_never_put_to_the_engine(self):
        na, seen = holding("read-only")
        standings([], facts(na=na))
        assert ("data.single-open", "read-only") not in seen


# -- the gate, over a real repository --------------------------------------------------------------


PYTHON = {"name": "demo", "ecosystems": ["python"]}


def sh(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    return root


def pipeline(state="reviewing", *, verdicts_=(), runs=(), digest=DIGEST):
    return {
        "methodology": "tdd", "state": state,
        "ruleset": {"path": RULESET_REL, "digest": digest, "error": None},
        "evidence": {"test_runs": list(runs), "verdicts": list(verdicts_)},
    }


def context(root, *, base="master", files=(), pipe=None) -> RuleContext:
    return RuleContext(
        manifest=Manifest.locate(root), cwd=root,
        session={"id": "s", "base": base, "files": list(files)},
        pipeline=pipeline() if pipe is None else pipe,
    )


def gate(ctx):
    (result,) = evaluate([RULE], ctx)
    return result


def submitted(root, entries, runs=()):
    """Verdicts accepted by the plan the command uses, as the record holds them."""
    plan = plan_verdicts(context(root, pipe=pipeline(runs=runs)), entries)
    assert plan.ok, plan.problems
    return [r.record() for r in plan.records]


CITE = [{"path": "app/m.py", "lines": [1, 2]}]


def every_pass(root, runs):
    return submitted(root, [
        {"rule": "data.single-open", "status": "pass", "citations": CITE},
        {"rule": "data.tested", "status": "pass", "tests": [SELECTOR]},
        {"rule": "style.explained", "status": "pass", "citations": CITE},
    ], runs=runs)


class TestTheGate:
    def test_it_rides_the_landing_entry_with_a_ruleset(self, repo):
        assert RULE in GUIDELINES_GATES["landing"]["entry"]
        machine = load_pipeline_machine(Manifest.locate(repo))
        (landing,) = machine.states_with_role("landing")
        assert RULE in machine.state(landing).entry_gates

    @pytest.mark.parametrize(
        ("profile", "level"), [("loose", "warn"), ("standard", "warn"), ("strict", "block")]
    )
    def test_its_level_per_profile(self, tmp_path, profile, level):
        manifest = git_project(tmp_path / "p", {
            "version": 1, "project": PYTHON, "enforcement": {"profile": profile},
        })
        assert manifest.rule_level(RULE).value == level

    def test_no_ruleset_configured_is_not_applicable(self, tmp_path):
        root = tmp_path / "bare"
        git_project(root, {"version": 1, "project": PYTHON})
        result = gate(context(root))
        assert result.verdict.skipped and "no guidelines ruleset" in result.verdict.message

    def test_no_pipeline_is_not_applicable(self, repo):
        ctx = context(repo)
        ctx = RuleContext(manifest=ctx.manifest, cwd=repo, session=ctx.session, pipeline=None)
        assert gate(ctx).verdict.skipped

    def test_a_ruleset_that_does_not_load_fails(self, repo):
        write(repo, RULESET_REL, RULESET.replace("severity: warn", "severity: loud"))
        verdict = gate(context(repo)).verdict
        assert not verdict.ok and not verdict.skipped

    def test_a_changed_set_that_cannot_be_established_fails(self, repo):
        verdict = gate(context(repo, base="")).verdict
        assert not verdict.ok

    def test_a_change_owing_nothing_passes(self, repo):
        write(repo, "docs/notes.md", "prose\n")
        verdict = gate(context(repo)).verdict
        assert verdict.ok and "no judgement guideline is owed" in verdict.message

    def test_missing_verdicts_fail_naming_each_rule_and_the_fix(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        verdict = gate(context(repo)).verdict
        assert not verdict.ok
        for rule_id in OWED[:2]:
            assert rule_id in verdict.message
        assert any("invariant guidelines verdict data.single-open" in f for f in verdict.fixes)

    def test_a_missing_warn_rule_still_fails_the_gate(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs)[:2]
        verdict = gate(context(repo, pipe=pipeline(verdicts_=recorded, runs=runs))).verdict
        assert not verdict.ok and "style.explained" in verdict.message

    def test_every_owed_rule_answered_passes(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs)
        verdict = gate(context(repo, pipe=pipeline(verdicts_=recorded, runs=runs))).verdict
        assert verdict.ok, verdict.message
        assert "3 judgement guideline(s)" in verdict.message and DIGEST in verdict.message

    def test_an_edit_after_the_verdict_makes_it_stale(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs)
        write(repo, "app/m.py", "x = 1\ny = 3\n")
        verdict = gate(context(repo, pipe=pipeline(verdicts_=recorded, runs=runs))).verdict
        assert not verdict.ok
        assert "data.single-open" in verdict.message and "no longer holds" in verdict.message
        assert "data.tested" not in verdict.message

    def test_a_blocker_fail_fails_the_gate(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs) + submitted(repo, [
            {"rule": "data.single-open", "status": "fail", "citations": CITE},
        ], runs=runs)
        verdict = gate(context(repo, pipe=pipeline(verdicts_=recorded, runs=runs))).verdict
        assert not verdict.ok and "data.single-open" in verdict.message
        assert "fail" in verdict.message

    def test_a_warn_fail_is_reported_inside_a_pass(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs) + submitted(repo, [
            {"rule": "style.explained", "status": "fail", "citations": CITE},
        ], runs=runs)
        verdict = gate(context(repo, pipe=pipeline(verdicts_=recorded, runs=runs))).verdict
        assert verdict.ok
        assert "style.explained" in verdict.detail and "warn" in verdict.detail

    def test_a_comment_only_change_is_answered_by_the_engine(self, repo):
        write(repo, "app/m.py", "x = 1  # explained\n")
        sh(repo, "commit", "-qam", "comment")
        runs = [green(SELECTOR)]
        recorded = submitted(repo, [
            {"rule": "data.tested", "status": "pass", "tests": [SELECTOR]},
            {"rule": "style.explained", "status": "pass",
             "citations": [{"path": "app/m.py", "lines": [1, 1]}]},
        ], runs=runs)
        verdict = gate(context(repo, pipe=pipeline(verdicts_=recorded, runs=runs))).verdict
        assert verdict.ok, verdict.message
        assert "data.single-open" in verdict.detail and "no-code" in verdict.detail

    def test_a_deleted_file_owes_nothing(self, repo):
        sh(repo, "rm", "-q", "app/m.py")
        sh(repo, "commit", "-qm", "gone")
        verdict = gate(context(repo)).verdict
        assert verdict.ok and "no judgement guideline is owed" in verdict.message

    def test_a_ruleset_edited_mid_pipeline_makes_verdicts_stale(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        runs = [green(SELECTOR)]
        recorded = every_pass(repo, runs)
        write(repo, RULESET_REL, RULESET.replace("Code never prints.", "Code never prints!"))
        verdict = gate(context(repo, pipe=pipeline(verdicts_=recorded, runs=runs))).verdict
        assert not verdict.ok and "DN-6" in verdict.message
