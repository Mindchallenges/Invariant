"""ADR-032 increment 5: the ``fitness`` boundary engine (D3, DEC_ADR_032_I5).

``{kind: boundary, engine: fitness, test: <selector>}`` names a project's own
test as a rule's evidence. It needs no adapter: it reads the pipeline's
recorded ``test_runs`` and answers from the latest run that included the
selector over the final tree. Passed is clean for every file the rule applies
to, failed is one finding naming the test, and no such run is unanswerable,
its fix the ``invariant pipeline test --selector`` line that records one.
invariant does not baseline it: the test keeps its own ratchet. The layer
block's ``evidence: fitness`` with ``test`` derives fitness rules, and
``guidelines.ruleset_valid`` names ``fitness`` beside judgement where it
refuses an ``imports`` rule no configured adapter answers.

The deliverable test is :class:`TestDeliverable`: a ``fitness`` rule passes on
a recorded green run of its selector, fails on a red one, and fails unanswered
with none.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_cli import payload, run
from test_guidelines_findings import run_eval

from invariant.core import findings, guidelines
from invariant.core.findings import FitnessAnswer, fitness_answer
from invariant.core.guidelines import (
    BaselineEntry,
    RulesetError,
    baseline_rule_problems,
    baselined,
    parse,
)
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.rules import all_rules

REGISTERED = frozenset(spec.id for spec in all_rules())
SELECTOR = "tests/test_arch.py"
SIGNATURE = ("c0ffee", "d1gest")


def fitness_rule(**check) -> dict:
    return {
        "id": "arch.layering", "section": "s", "title": "The layering test passes",
        "statement": "The project's layering test passes over the change.",
        "severity": "blocker", "phases": ["landing"], "applies_to": ["app/**"],
        "check": {"kind": "boundary", "engine": "fitness", "test": SELECTOR, **check},
    }


def ruleset_text(*rules: dict, version=2, **top) -> str:
    raw = {"version": version, "sections": [{"id": "s", "title": "S"}],
           "rules": list(rules), **top}
    return yaml.safe_dump(raw, sort_keys=False)


def load(text: str, engines=None):
    return parse(text, registered=REGISTERED, engines=engines)


def problems(text: str, engines=None) -> tuple[str, ...]:
    with pytest.raises(RulesetError) as caught:
        load(text, engines)
    return caught.value.problems


def a_run(*, selectors=(SELECTOR,), passed=True, executed=1, failed=0, hazards=(),
          tree=SIGNATURE, held=True, phase="green") -> dict:
    signed = None if tree is None else {
        "root": "/x", "commit": tree[0], "before": tree[1],
        "commit_after": tree[0], "after": tree[1] if held else "moved",
    }
    return {
        "phase": phase, "selectors": list(selectors),
        "scope": "scoped" if selectors else "full", "passed": passed,
        "executed": executed, "failed": failed, "hazards": list(hazards), "tree": signed,
    }


# -- the check's shape ----------------------------------------------------------------------


class TestTheCheck:
    def test_a_fitness_boundary_names_its_test_and_needs_no_adapter(self):
        (rule,) = load(ruleset_text(fitness_rule()), engines=frozenset()).rules
        assert (rule.check.kind, rule.check.engine, rule.check.test) == (
            "boundary", "fitness", SELECTOR,
        )
        assert rule.check.forbid == () and rule.check.only == ()

    def test_it_is_summarised_naming_the_test(self):
        ruleset = load(ruleset_text(fitness_rule()))
        text = guidelines.render_checklist(ruleset, "markdown", source="docs/guidelines/ruleset.yaml")
        assert "boundary (fitness)" in text and SELECTOR in text

    @pytest.mark.parametrize(("check", "said"), [
        ({"forbid": ["x/**"]}, "in place of forbid and only"),
        ({"only": ["x/**"]}, "in place of forbid and only"),
        ({"test": ""}, "test must be a non-empty string"),
        ({"test": ["a", "b"]}, "test must be a non-empty string"),
    ])
    def test_a_malformed_fitness_check_is_refused(self, check, said):
        found = problems(ruleset_text(fitness_rule(**check)))
        assert any(said in p for p in found), found

    def test_a_missing_test_is_refused(self):
        rule = fitness_rule()
        del rule["check"]["test"]
        found = problems(ruleset_text(rule))
        assert any("missing test" in p for p in found), found

    def test_test_on_the_imports_engine_is_refused(self):
        rule = fitness_rule(engine="imports", forbid=["x/**"])
        found = problems(ruleset_text(rule))
        assert any("test belongs to engine fitness" in p for p in found), found

    def test_fitness_is_a_version_2_engine(self):
        found = problems(ruleset_text(fitness_rule(), version=1))
        assert any("version 2" in p for p in found), found

    def test_an_imports_rule_no_adapter_answers_names_fitness_and_the_adapters(self):
        rule = fitness_rule(engine="imports", forbid=["x/**"])
        del rule["check"]["test"]
        found = problems(ruleset_text(rule), engines=frozenset({"mask"}))
        assert any(
            "judgement" in p and "fitness" in p and "dart" in p and "node" in p for p in found
        ), found


LAYER_BLOCK = {"evidence": "fitness", "test": SELECTOR, "map": [
    {"id": "inner", "title": "Inner", "paths": ["inner/**"]},
    {"id": "middle", "title": "Middle", "paths": ["middle/**"]},
    {"id": "outer", "title": "Outer", "paths": ["outer/**"]},
]}


class TestTheLayerEvidence:
    def test_evidence_fitness_derives_fitness_rules_carrying_the_test(self):
        ruleset = load(ruleset_text(layers=LAYER_BLOCK), engines=frozenset())
        assert [r.id for r in ruleset.rules] == [
            "layers.inner-depends-inward", "layers.middle-depends-inward",
        ]
        for rule in ruleset.rules:
            assert (rule.check.kind, rule.check.engine, rule.check.test) == (
                "boundary", "fitness", SELECTOR,
            )

    def test_fitness_evidence_needs_its_test(self):
        block = {k: v for k, v in LAYER_BLOCK.items() if k != "test"}
        found = problems(ruleset_text(layers=block))
        assert any("layers: evidence fitness needs test" in p for p in found), found

    def test_test_without_fitness_evidence_is_refused(self):
        block = {**LAYER_BLOCK, "evidence": "imports"}
        found = problems(ruleset_text(layers=block))
        assert any("layers: test belongs to evidence fitness" in p for p in found), found

    def test_imports_evidence_no_adapter_answers_names_fitness(self):
        block = {**LAYER_BLOCK, "evidence": "imports"}
        del block["test"]
        found = problems(ruleset_text(layers=block), engines=frozenset())
        assert any("evidence: fitness" in p and "layers.test" in p for p in found), found

    def test_changing_the_maps_test_is_a_change_the_user_rules(self):
        ruled = {"by": "user", "date": "2026-11-23", "reason": "r"}
        base = load(ruleset_text(layers={**LAYER_BLOCK, "owner": "user", "ruled": ruled}))
        tip = load(ruleset_text(layers={
            **LAYER_BLOCK, "owner": "user", "ruled": ruled, "test": "tests/other.py",
        }))
        changes = guidelines.unruled_changes(base, tip)
        assert any("test" in change for change in changes), changes


# -- which run answers ---------------------------------------------------------------------------


class TestFitnessAnswer:
    def test_a_green_run_over_the_tree_passes(self):
        assert fitness_answer([a_run()], SELECTOR, SIGNATURE).outcome == "pass"

    def test_a_red_run_over_the_tree_fails_naming_the_test(self):
        answer = fitness_answer([a_run(passed=False, failed=2)], SELECTOR, SIGNATURE)
        assert answer.outcome == "fail"
        assert SELECTOR in answer.note and "2 failing" in answer.note

    def test_the_latest_run_over_the_tree_decides(self):
        runs = [a_run(passed=False, failed=1), a_run(), a_run(selectors=("other",),
                                                              passed=False)]
        assert fitness_answer(runs, SELECTOR, SIGNATURE).outcome == "pass"
        runs = [a_run(), a_run(passed=False, failed=None)]
        answer = fitness_answer(runs, SELECTOR, SIGNATURE)
        assert answer.outcome == "fail" and "an unknown number of" in answer.note

    def test_a_full_run_includes_every_selector(self):
        assert fitness_answer([a_run(selectors=())], SELECTOR, SIGNATURE).outcome == "pass"

    def test_a_run_of_another_selector_is_none(self):
        answer = fitness_answer([a_run(selectors=("tests/test_arch.py::one",))],
                                SELECTOR, SIGNATURE)
        assert answer.outcome == "unanswerable"
        assert "no run of tests/test_arch.py is recorded" in answer.note

    def test_a_run_over_an_older_tree_is_none_and_says_so(self):
        runs = [a_run(tree=("c0ffee", "older")), a_run(held=False), a_run(tree=None)]
        answer = fitness_answer(runs, SELECTOR, SIGNATURE)
        assert answer.outcome == "unanswerable"
        assert "3 run(s) of tests/test_arch.py are recorded, none over this tree" in answer.note

    def test_a_record_without_a_tree_mapping_is_not_over_the_tree(self):
        runs = [{**a_run(), "tree": "garbage"}, "not a record"]
        assert fitness_answer(runs, SELECTOR, SIGNATURE).outcome == "unanswerable"

    def test_no_signature_is_unanswerable(self):
        answer = fitness_answer([a_run()], SELECTOR, None)
        assert answer.outcome == "unanswerable" and "git" in answer.note

    def test_a_pass_that_executed_nothing_is_unanswerable(self):
        answer = fitness_answer([a_run(executed=0)], SELECTOR, SIGNATURE)
        assert answer.outcome == "unanswerable" and "executed no test" in answer.note

    def test_a_pass_with_a_hazard_is_unanswerable(self):
        answer = fitness_answer([a_run(hazards=("h.one",))], SELECTOR, SIGNATURE)
        assert answer.outcome == "unanswerable" and "h.one" in answer.note


# -- the evaluation ----------------------------------------------------------------------------------


def evaluate_fitness(rules, files, answer: FitnessAnswer | None):
    asked: list[str] = []

    def fitness(selector: str) -> FitnessAnswer:
        asked.append(selector)
        assert answer is not None
        return answer

    result = findings.evaluate(
        rules, sorted(files), read=lambda rel: files.get(rel),
        mask=lambda *_: None, references=lambda rel: None,
        fitness=None if answer is None else fitness,
    )
    return result, asked


class TestTheEvaluation:
    def rules(self, **more):
        return load(ruleset_text(fitness_rule(**more))).rules

    def test_a_pass_is_clean_for_every_file_and_asks_once(self):
        files = {"app/a.py": b"x\n", "app/b.py": b"y\n", "other/c.py": b"z\n"}
        result, asked = evaluate_fitness(self.rules(), files, FitnessAnswer("pass"))
        assert result.findings == () and asked == [SELECTOR]
        assert (result.scanned, result.rules) == (2, 1)

    def test_a_failure_is_one_finding_naming_the_test_with_no_fingerprint(self):
        files = {"app/a.py": b"x\n", "app/b.py": b"y\n"}
        result, _ = evaluate_fitness(self.rules(), files, FitnessAnswer("fail", "it failed"))
        (finding,) = result.findings
        assert (finding.status, finding.path, finding.target) == ("new", "app/a.py", SELECTOR)
        assert finding.fingerprint is None and finding.line is None
        assert f"invariant pipeline test --selector {SELECTOR}" in finding.fix
        assert finding.describe() == f"arch.layering app/a.py -> {SELECTOR}"
        assert findings.counted([finding]) == {}

    def test_no_run_is_one_unanswerable_finding_with_the_line_that_records_one(self):
        result, _ = evaluate_fitness(self.rules(), {"app/a.py": b"x\n"},
                                     FitnessAnswer("unanswerable", "no run"))
        (finding,) = result.findings
        assert finding.status == "unanswerable" and finding.note == "no run"
        assert finding.fix == f"invariant pipeline test --selector {SELECTOR}"

    def test_no_covered_file_asks_nothing(self):
        result, asked = evaluate_fitness(self.rules(), {"other/c.py": b"z\n"},
                                         FitnessAnswer("fail"))
        assert result.findings == () and asked == []

    def test_an_excepted_file_is_named_and_not_asked_for(self):
        rules = self.rules()
        rule = rules[0]
        excepted = guidelines.GuidelineException(path="app/a.py", reason="generated")
        rules = (rule.__class__(**{**rule.__dict__, "exceptions": (excepted,)}),)
        result, asked = evaluate_fitness(rules, {"app/a.py": b"x\n"}, FitnessAnswer("fail"))
        (finding,) = result.findings
        assert finding.status == "excepted" and asked == []

    def test_without_the_runs_a_fitness_rule_is_not_evaluated(self):
        result, _ = run_eval(self.rules(), {"app/a.py": "x\n"})
        assert result.findings == () and result.rules == 0

    def test_a_file_in_two_layers_stays_unanswerable_under_fitness(self):
        block = {"evidence": "fitness", "test": SELECTOR, "map": [
            {"id": "a", "title": "A", "paths": ["a/**", "b/shared/**"]},
            {"id": "b", "title": "B", "paths": ["b/**"]},
            {"id": "c", "title": "C", "paths": ["c/**"]},
        ]}
        rules = load(ruleset_text(layers=block)).rules
        result, asked = evaluate_fitness(rules, {"b/shared/m.py": b"x\n"}, FitnessAnswer("pass"))
        assert result.findings and all(f.status == "unanswerable" for f in result.findings)
        assert asked == []


class TestNotBaselined:
    def test_a_fitness_rule_is_not_baselined(self):
        (rule,) = load(ruleset_text(fitness_rule())).rules
        assert not baselined(rule)

    def test_an_entry_naming_a_fitness_rule_is_refused(self):
        ruleset = load(ruleset_text(fitness_rule()))
        entries = [BaselineEntry("arch.layering", "app/a.py", "0123456789ab", 1, line=2)]
        (problem,) = baseline_rule_problems(entries, ruleset)
        assert "fitness" in problem and "own ratchet" in problem


# -- the deliverable: mechanical_clean over recorded runs -------------------------------------------


FAKE_TEST = """#!/bin/sh
if [ -f RED ]; then echo '1 failed in 0.01s'; exit 1; fi
echo '1 passed in 0.01s'
exit 0
"""


def git_in(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    root.mkdir()
    git_in(root, "init", "-q", "-b", "master")
    git_in(root, "config", "user.email", "t@example.invalid")
    git_in(root, "config", "user.name", "T")
    (root / ".gitignore").write_text(".build/\nRED\n", encoding="utf-8")
    (root / "t.sh").write_text(FAKE_TEST, encoding="utf-8")
    (root / "app").mkdir()
    (root / "app" / "m.py").write_text("x = 1\n", encoding="utf-8")
    ruleset = root / "docs" / "guidelines" / "ruleset.yaml"
    ruleset.parent.mkdir(parents=True)
    ruleset.write_text(ruleset_text(fitness_rule()), encoding="utf-8")
    body = {
        "version": 1,
        "project": {"name": "demo", "ecosystems": ["python"]},
        "commands": {"test": "sh t.sh", "test_one": "sh t.sh {selector}"},
        "guidelines": {"ruleset": "docs/guidelines/ruleset.yaml"},
    }
    manifest = root / MANIFEST_RELPATH
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(yaml.safe_dump(body), encoding="utf-8")
    git_in(root, "add", "-A")
    git_in(root, "commit", "-qm", "base")
    monkeypatch.chdir(root)
    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    assert run("session", "start", "x") == 0
    assert run("pipeline", "start", "--task", "t") == 0
    (root / "app" / "m.py").write_text("x = 2\n", encoding="utf-8")
    return root


def mechanical(capsys) -> dict:
    run("check", "guidelines.mechanical_clean", "--json")
    (verdict,) = payload(capsys)["verdicts"]
    assert verdict["rule"] == "guidelines.mechanical_clean"
    return verdict


def record(capsys, *, red: bool, root: Path) -> None:
    flag = root / "RED"
    if red:
        flag.write_text("1", encoding="utf-8")
    elif flag.exists():
        flag.unlink()
    run("pipeline", "test", "--selector", SELECTOR)
    capsys.readouterr()


def fitness_findings(verdict: dict) -> list[dict]:
    return [f for f in verdict.get("findings") or [] if f.get("rule") == "arch.layering"]


class TestDeliverable:
    def test_with_no_run_it_fails_unanswered_naming_the_line(self, project, capsys):
        verdict = mechanical(capsys)
        (finding,) = fitness_findings(verdict)
        assert finding["finding"] == "unanswerable"
        assert f"invariant pipeline test --selector {SELECTOR}" in finding["fix"]

    def test_a_recorded_green_run_passes(self, project, capsys):
        record(capsys, red=False, root=project)
        verdict = mechanical(capsys)
        assert fitness_findings(verdict) == [], verdict

    def test_a_recorded_red_run_fails_naming_the_test(self, project, capsys):
        record(capsys, red=True, root=project)
        verdict = mechanical(capsys)
        (finding,) = fitness_findings(verdict)
        assert finding["finding"] == "new" and finding["target"] == SELECTOR

    def test_an_edit_after_the_green_run_leaves_it_unanswered(self, project, capsys):
        record(capsys, red=False, root=project)
        (project / "app" / "m.py").write_text("x = 3\n", encoding="utf-8")
        verdict = mechanical(capsys)
        (finding,) = fitness_findings(verdict)
        assert finding["finding"] == "unanswerable"
        assert "none over this tree" in finding["note"]

    def test_a_recorded_run_signs_its_tree(self, project, capsys):
        record(capsys, red=False, root=project)
        state = Manifest.locate(project).state_root
        (record_file,) = Path(state).glob("pipelines/*.json")
        current = json.loads(record_file.read_text(encoding="utf-8"))["current"]
        tree = current["evidence"]["test_runs"][-1]["tree"]
        assert tree["before"] and tree["before"] == tree["after"]
        assert tree["commit"] == tree["commit_after"]
