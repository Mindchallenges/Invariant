"""The conformance harness's engine layer (IDEA_031 A1, ADR-012 D11).

Each shipped scenario is replayed through the real CLI, in-process, in a fresh
repository, and its gate outcomes are compared with the scenario's known
answer. A second replay in another fresh tree must reach identical outcomes:
that is the engine-layer half of the model-agnostic contract (D1 item 5).
Prose is never compared, only rule ids and their statuses.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import random
import shutil
import string
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml

import invariant
from invariant.cli.conformance import Replay, replay
from invariant.core.conformance import (
    SCENARIO_DIR,
    Divergence,
    Expected,
    Outcome,
    Scenario,
    ScenarioError,
    Step,
    StepResult,
    compare,
    load,
    load_named,
    reduce,
    render,
    scenario_names,
    status_of,
)

#: The gates a configured ruleset injects at the landing (ADR-012 D3).
GUIDELINES = (
    "guidelines.ruleset_valid",
    "guidelines.generated_current",
    "guidelines.mechanical_clean",
    "guidelines.baseline_only_shrinks",
    "guidelines.verdicts_complete",
)

SHIPPED = [
    "baseline-grown",
    "baselined",
    "clean",
    "docs-first-breach",
    "engine-answers",
    "fixed-not-pruned",
    "judgement-fail",
    "judgement-fail-standard",
    "malformed-ruleset",
    "missing-test",
    "red-first-breach",
    "seeded-pattern",
    "stale-verdict",
    "vague-verdict",
    "valid-ruleset",
]

#: The judgement rule the verdict scenarios owe a verdict (A1 increment 4).
STABLE = "app.answer-is-stable"


@pytest.fixture(scope="module")
def shipped(tmp_path_factory):
    """Each shipped scenario's plain replay, run once and shared.

    A replay is the slow part of this file and is deterministic (the test
    below holds it to that), so every test that only reads one shares it. Treat
    what it answers as read-only.
    """
    replays: dict[str, Replay] = {}

    def answer(name: str) -> Replay:
        if name not in replays:
            replays[name] = replay(load_named(name), workdir=tmp_path_factory.mktemp(name))
        return replays[name]

    return answer


# -- the known answers ------------------------------------------------------


def test_the_shipped_scenarios_are_the_first_set():
    assert scenario_names() == SHIPPED


def test_a_malformed_ruleset_is_the_one_refusal_at_the_landing(shipped):
    """A1 increment 2 (ADR-012 D3, D11): the ruleset gate rides the landing
    only where a ruleset is configured, and there it is the gate that stops a
    change otherwise identical to the clean one."""
    outcome = shipped("malformed-ruleset").outcome
    clean = load_named("clean").expected.gates["committed"]
    landing = outcome.gates["committed"]
    assert {k: v for k, v in landing.items() if k not in GUIDELINES} == clean
    assert landing["guidelines.ruleset_valid"] == "block"
    # Its outputs cannot be judged against a ruleset that does not load, and
    # the drift gate only ever warns (ruling 7), so it is no refusal.
    assert landing["guidelines.generated_current"] == "warn"
    # It cannot look for findings either, and the standard profile holds it
    # at warn (ruling 7), so it is no refusal.
    assert landing["guidelines.mechanical_clean"] == "warn"
    # Nor can it judge a verdict, and standard holds it at warn (ruling 7).
    assert landing["guidelines.verdicts_complete"] == "warn"
    assert outcome.refusals == {"land": ("guidelines.ruleset_valid",)}


def test_a_ruleset_using_every_kind_lands_like_the_clean_change(shipped):
    """A1 increment 2, the ruleset fixture for the kinds A2 increment 2
    validates: engine, pattern, boundary, both cochange forms with the
    registry a rule names, and judgement. The ruleset gate passes, and every
    other landing gate answers as it does for the clean change."""
    outcome = shipped("valid-ruleset").outcome
    clean = load_named("clean").expected.gates["committed"]
    landing = outcome.gates["committed"]
    assert {k: v for k, v in landing.items() if k not in GUIDELINES} == clean
    assert landing["guidelines.ruleset_valid"] == "pass"
    assert landing["guidelines.generated_current"] == "pass"
    assert landing["guidelines.mechanical_clean"] == "pass"
    # The judgement rule the change owes is answered by the cited pass the
    # scenario submits in review (A4 increment 3).
    assert landing["guidelines.verdicts_complete"] == "pass"
    assert outcome.refusals == {}


def test_a_seeded_pattern_blocks_naming_the_rule_and_line(shipped):
    """A1 increment 3, the seeded pattern (ADR-012 D11, DN-12): one new
    pattern violation, held at block by the scenario's manifest, is the one
    refusal at the landing, and the refusal names the rule and the line."""
    result = shipped("seeded-pattern")
    clean = load_named("clean").expected.gates["committed"]
    landing = result.outcome.gates["committed"]
    assert {k: v for k, v in landing.items() if k not in GUIDELINES} == clean
    assert landing["guidelines.ruleset_valid"] == "pass"
    assert landing["guidelines.generated_current"] == "pass"
    assert landing["guidelines.mechanical_clean"] == "block"
    assert result.outcome.refusals == {"land": ("guidelines.mechanical_clean",)}
    (land,) = [step for step in result.steps if step.label == "land"]
    (gate,) = [g for g in land.document["gates"] if g["rule"] == "guidelines.mechanical_clean"]
    assert "style.no-print app.py:2" in gate["message"]
    (record,) = gate["findings"]
    assert record["citations"] == [{"path": "app.py", "lines": [2, 2]}]


def test_a_baselined_finding_does_not_block_and_a_new_one_does(shipped):
    """A1 increment 3, the baselined scenario (ADR-012 D11, DN-12): the
    finding the baseline records is held as pre-existing and does not block;
    the change's new finding in the same file does, and is the one refusal at
    the landing, named by rule and line."""
    result = shipped("baselined")
    clean = load_named("clean").expected.gates["committed"]
    landing = result.outcome.gates["committed"]
    assert {k: v for k, v in landing.items() if k not in GUIDELINES} == clean
    assert landing["guidelines.mechanical_clean"] == "block"
    assert result.outcome.refusals == {"land": ("guidelines.mechanical_clean",)}
    (land,) = [step for step in result.steps if step.label == "land"]
    (gate,) = [g for g in land.document["gates"] if g["rule"] == "guidelines.mechanical_clean"]
    assert "style.no-print app.py:3" in gate["message"]
    assert "app.py:2" not in gate["message"]
    assert "1 baselined" in gate["message"]
    by_line = {r["citations"][0]["lines"][0]: r["finding"] for r in gate["findings"]}
    assert by_line == {2: "baselined", 3: "new"}


def test_a_baseline_entry_added_by_hand_blocks(shipped):
    """A1 increment 3, baseline-grown (ADR-012 D6, D11, DN-12): an entry
    written into the baseline by hand holds the new print as baselined, so
    the finding gate passes, and the baseline gate refuses the landing,
    naming the added entry."""
    result = shipped("baseline-grown")
    clean = load_named("clean").expected.gates["committed"]
    landing = result.outcome.gates["committed"]
    assert {k: v for k, v in landing.items() if k not in GUIDELINES} == clean
    assert landing["guidelines.mechanical_clean"] == "pass"
    assert result.outcome.refusals == {"land": ("guidelines.baseline_only_shrinks",)}
    (land,) = [step for step in result.steps if step.label == "land"]
    (gate,) = [g for g in land.document["gates"] if g["rule"] == "guidelines.baseline_only_shrinks"]
    assert "added style.no-print app.py 436046678e23 (count 1)" in gate["message"]


def test_a_fixed_finding_left_in_the_baseline_blocks_with_the_prune_fix(shipped):
    """A1 increment 3, fixed-not-pruned (ADR-012 D6, DN-5, DN-12): the change
    removes the recorded print and leaves its entry, which is stale, so the
    baseline gate refuses the landing and names the prune as the fix."""
    result = shipped("fixed-not-pruned")
    clean = load_named("clean").expected.gates["committed"]
    landing = result.outcome.gates["committed"]
    assert {k: v for k, v in landing.items() if k not in GUIDELINES} == clean
    assert landing["guidelines.mechanical_clean"] == "pass"
    assert result.outcome.refusals == {"land": ("guidelines.baseline_only_shrinks",)}
    (land,) = [step for step in result.steps if step.label == "land"]
    (gate,) = [g for g in land.document["gates"] if g["rule"] == "guidelines.baseline_only_shrinks"]
    assert "style.no-print app.py 66de9fe2b4b1 (recorded 1, found 0)" in gate["message"]
    assert "invariant guidelines baseline --prune" in gate["fixes"]


def test_a_missing_test_blocks_naming_the_rule_and_file(shipped):
    """A1 increment 3, the missing test (ADR-012 D11, DN-12): a behaviour
    change with no paired test diff, under a blocker cochange pair held at
    block by the scenario's manifest, is the one refusal at the landing. The
    refusal names the rule and the file, and the finding has no fingerprint,
    because a cochange finding is never baselined (DN-4)."""
    result = shipped("missing-test")
    clean = load_named("clean").expected.gates["committed"]
    landing = result.outcome.gates["committed"]
    assert {k: v for k, v in landing.items() if k not in GUIDELINES} == clean
    assert landing["guidelines.ruleset_valid"] == "pass"
    assert landing["guidelines.generated_current"] == "pass"
    assert landing["guidelines.mechanical_clean"] == "block"
    assert result.outcome.refusals == {"land": ("guidelines.mechanical_clean",)}
    (land,) = [step for step in result.steps if step.label == "land"]
    (gate,) = [g for g in land.document["gates"] if g["rule"] == "guidelines.mechanical_clean"]
    assert "tests.module-has-test app.py" in gate["message"]
    (record,) = gate["findings"]
    assert record["kind"] == "cochange"
    assert record["citations"] == [{"path": "app.py", "lines": None}]
    assert record["fingerprint"] is None
    assert "tests/test_app.py" in record["fix"]


# -- the verdict contract (A1 increment 4, A4 increment 7) -------------------------------


def labelled(result) -> dict:
    return {step.label: step for step in result.steps}


def landings(result) -> list:
    """The landing records ``pipeline status`` printed at the step labelled record."""
    return labelled(result)["record"].document["evidence"]["guidelines_landings"]


def standing_of(record: dict, rule: str) -> dict:
    (gate,) = [g for g in record["gates"] if g["rule"] == "guidelines.verdicts_complete"]
    (standing,) = [s for s in gate["standings"] if s["rule"] == rule]
    return standing


def test_a_vague_verdict_is_refused_naming_its_predicate_and_records_nothing(shipped):
    """ADR-012 D5, "What vague means", and D11's vague verdict: each vague
    answer is refused at submission naming the predicate it fails, the bulk
    file is refused whole (DN-11), nothing is recorded, so the rule stands
    missing, and the strict profile's verdicts_complete refuses the landing."""
    result = shipped("vague-verdict")
    steps = labelled(result)
    refused = [label for label, step in steps.items() if step.argv[1:3] in (
        ("guidelines", "verdict"), ("guidelines", "verdicts"))
        and "show" not in step.argv]
    assert len(refused) == 10
    for label in refused:
        assert steps[label].exit_code == 1, label
        assert steps[label].document["recorded"] == [], label
    (bulk,) = steps["bulk"].document["refused"]
    assert (bulk["entry"], bulk["rule"], bulk["predicate"]) == (2, "app.answer-is-fast", "P1")
    (diff,) = steps["outside-the-diff"].document["refused"]
    assert (diff["entry"], diff["rule"]) == (1, STABLE)
    assert result.outcome.verdicts == {"standing": {STABLE: "missing"}}
    assert result.outcome.refusals["land"] == ("guidelines.verdicts_complete",)
    (gate,) = [g for g in steps["land"].document["gates"]
               if g["rule"] == "guidelines.verdicts_complete"]
    assert f"{STABLE}: no verdict" in gate["message"]


def test_a_judgement_fail_at_blocker_refuses_a_strict_landing_and_a_forced_one_keeps_it(
        shipped):
    """D11's judgement fail, under strict: the cited fail is accepted and
    current, blocks the landing, and the forced landing's record (A4.6)
    keeps the fail as the gate decided it."""
    result = shipped("judgement-fail")
    steps = labelled(result)
    assert steps["fail"].exit_code == 0
    assert result.outcome.verdicts == {"standing": {STABLE: "fail"}}
    assert result.outcome.refusals == {"land": ("guidelines.verdicts_complete",)}
    assert steps["forced"].exit_code == 0
    (record,) = landings(result)
    assert record["forced"] is True and record["to"] == "committed"
    standing = standing_of(record, STABLE)
    assert (standing["standing"], standing["status"], standing["blocks"]) == (
        "current", "fail", True)


def test_a_judgement_fail_at_blocker_warns_a_standard_landing_and_is_kept(shipped):
    """The same fail under the standard profile: the gate warns, the landing
    goes through unforced, and its record still keeps the fail."""
    result = shipped("judgement-fail-standard")
    assert result.outcome.gates["committed"]["guidelines.verdicts_complete"] == "warn"
    assert result.outcome.refusals == {}
    (record,) = landings(result)
    assert record["forced"] is False
    standing = standing_of(record, STABLE)
    assert (standing["standing"], standing["status"]) == ("current", "fail")


def test_a_verdict_goes_stale_when_its_cited_file_changes(shipped):
    """D5, "Staleness": a current pass goes stale once the cited file's blob
    changes, naming P3, and stale is missing at the strict landing."""
    result = shipped("stale-verdict")
    steps = labelled(result)
    assert result.outcome.verdicts == {
        "fresh": {STABLE: "pass"}, "edited": {STABLE: "stale"}}
    (owed,) = steps["edited"].document["owed"]
    assert [p["predicate"] for p in owed["problems"]] == ["P3"]
    assert result.outcome.refusals == {"land": ("guidelines.verdicts_complete",)}


def test_the_engines_na_answers_the_rule_and_sets_a_recorded_fail_aside(shipped):
    """D5 item 5 and the user's A4.3 ruling: on a comment-only change the
    mechanical N/A holds, so the engine answers the rule, the cited fail
    recorded on it is set aside and never counted, and the strict landing
    passes the gate."""
    result = shipped("engine-answers")
    steps = labelled(result)
    assert steps["set-aside"].exit_code == 0
    assert result.outcome.verdicts == {"standing": {STABLE: "engine"}}
    (owed,) = steps["standing"].document["owed"]
    assert owed["set_aside"] == "fail"
    assert result.outcome.gates["committed"]["guidelines.verdicts_complete"] == "pass"
    assert result.outcome.refusals == {}


@pytest.mark.parametrize("name", SHIPPED)
def test_a_replay_matches_the_known_answer(name, shipped):
    divergences = compare(load_named(name).expected, shipped(name).outcome)
    assert divergences == [], render(name, divergences)


@pytest.mark.parametrize("name", SHIPPED)
def test_two_replays_in_fresh_trees_agree(name, shipped, tmp_path):
    first = shipped(name).outcome
    second = replay(load_named(name), workdir=tmp_path / "second").outcome
    divergences = compare(first.as_expected(), second, exhaustive=True)
    assert divergences == [], render(name, divergences)
    assert first == second


def test_the_clean_landing_carries_every_landing_gate(shipped):
    """Today's landing set, so a gate added to it is a known-answer change.

    ``agents.rendered_current`` rides it because the scenario's ``host.kind``
    is the default, ``claude_code``, which has an adapter (A6 increment 4).
    It warns: a scenario tree installs no agents."""
    outcome = shipped("clean").outcome
    assert outcome.gates["committed"]["agents.rendered_current"] == "warn"
    assert sorted(outcome.gates["committed"]) == [
        "agents.design_agent_ran",
        "agents.rendered_current",
        "agents.review_agent_ran",
        "coverage.meets_floor",
        "docs.builds_strict",
        "docs.decisions_recorded",
        "docs.diagrams_current",
        "docs.mandate_kept",
        "docs.needs_traceable",
        "docs.updated",
        "items.decisions_linked",
        "privacy.clean",
        "test.all_pass",
    ]


def test_a_replay_leaves_the_caller_environment_and_cwd_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("INVARIANT_HOST_SESSION_ID", "host-outer")
    monkeypatch.setenv("INVARIANT_HANDOFF", "outer")
    before_env = dict(os.environ)
    before_cwd = Path.cwd()
    replay(load_named("docs-first-breach"), workdir=tmp_path)
    assert dict(os.environ) == before_env
    assert Path.cwd() == before_cwd


# -- the replay, with a recording CLI ---------------------------------------


def write_scenario(root: Path, body: dict, tree: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "scenario.yaml").write_text(yaml.safe_dump(body), encoding="utf-8")
    files = tree if tree is not None else {"README.txt": "fixture\n"}
    for rel, text in files.items():
        path = root / "tree" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def body(steps, expect=None, **extra) -> dict:
    return {
        "description": "synthetic",
        "manifest": {"version": 1, "project": {"name": "demo"}},
        "steps": steps,
        "expect": expect if expect is not None else {"gates": {}, "refusals": {}},
        **extra,
    }


class RecordingCli:
    """Stands in for ``invariant.cli.main.main``: records, prints, answers."""

    def __init__(self, answers=None) -> None:
        self.calls: list[tuple[list[str], str, dict, Path]] = []
        self.answers = answers or {}

    def __call__(self, argv: list[str]) -> int:
        import sys

        stdin = sys.stdin.read()
        self.calls.append((list(argv), stdin, dict(os.environ), Path.cwd()))
        code, document = self.answers.get(argv[1] if argv[0] == "--json" else argv[0], (0, {}))
        print(json.dumps(document))
        return code


def test_replay_seeds_the_tree_runs_steps_and_reduces(tmp_path, monkeypatch):
    monkeypatch.setenv("INVARIANT_SESSION_ID", "outer")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/elsewhere")
    root = write_scenario(
        tmp_path / "s",
        body(
            [
                {"kind": "cli", "argv": ["pipeline", "advance", "red"], "id": "go"},
                {"kind": "hook", "event": "pre-tool-use",
                 "payload": {"cwd": "{root}", "paths": ["{root}/a"], "n": 1}},
                {"kind": "write", "path": "src/new.txt", "content": "made\n"},
                {"kind": "commit", "message": "step"},
            ],
            expect={"gates": {"red": {"docs.read_architecture": "block"}},
                    "refusals": {"go": ["docs.read_architecture"]}},
        ),
    )
    refused = {
        "advanced": False,
        "gates": [{"rule": "docs.read_architecture", "level": "block", "ok": False,
                   "skipped": False}],
    }
    cli = RecordingCli({"pipeline": (1, refused)})
    result = replay(load(root), workdir=tmp_path / "w", run=cli)

    (argv, _, env, cwd), (hook_argv, stdin, _, _) = cli.calls
    assert argv == ["--json", "pipeline", "advance", "red"]
    assert hook_argv == ["hook", "pre-tool-use"]
    sandbox = tmp_path / "w" / "repo"
    assert json.loads(stdin) == {"cwd": str(sandbox), "paths": [f"{sandbox}/a"], "n": 1}
    assert cwd == sandbox
    assert "INVARIANT_SESSION_ID" not in env and "CLAUDE_PROJECT_DIR" not in env
    assert env["INVARIANT_HOME"] == str(tmp_path / "w" / "home")
    assert (sandbox / ".claude" / "invariant.yaml").is_file()
    assert (sandbox / "README.txt").read_text() == "fixture\n"
    assert (sandbox / "src" / "new.txt").read_text() == "made\n"
    assert compare(load(root).expected, result.outcome) == []
    assert [r.label for r in result.steps] == ["go", "#2", "#3", "#4"]
    log = os.popen(f"git -C '{sandbox}' log --format=%s").read().split("\n")
    assert log[:2] == ["step", "scenario tree"]


def test_a_verdicts_step_submits_its_entries_from_a_file_outside_the_tree(tmp_path):
    """The bulk form (D5, DN-11) driven host-neutrally: the entries are
    written as JSON beside the replay, never into the seeded repository, and
    handed to ``guidelines verdicts submit``."""
    entries = [{"rule": "r.one", "status": "pass",
                "citations": [{"path": "a.py", "lines": [1, 1]}], "note": "{root}"}]
    root = write_scenario(
        tmp_path / "s",
        body([{"kind": "verdicts", "id": "bulk", "entries": entries},
              {"kind": "verdicts", "entries": [7, "{worktree}"]}]),
    )
    seen: list[object] = []

    def submit(argv):
        seen.append(json.loads(Path(argv[-1]).read_text(encoding="utf-8")))
        print(json.dumps({"recorded": []}))
        return 0

    result = replay(load(root), workdir=tmp_path / "w", run=submit)
    first, second = result.steps
    assert first.argv[:4] == ("--json", "guidelines", "verdicts", "submit")
    written = Path(first.argv[4])
    assert written.parent == tmp_path / "w" / "submissions"
    assert Path(second.argv[4]) != written
    sandbox = tmp_path / "w" / "repo"
    assert seen[0] == [{**entries[0], "note": str(sandbox)}]
    # An entry of the wrong shape is submitted as written: the engine refuses it.
    assert seen[1] == [7, str(sandbox)]
    assert not (sandbox / written.name).exists()
    assert result.outcome.refusals == {}


def test_a_worktree_step_with_no_worktree_is_refused(tmp_path):
    root = write_scenario(tmp_path / "s", body([{"kind": "commit", "in": "worktree"}]))
    with pytest.raises(ScenarioError, match="no session worktree"):
        replay(load(root), workdir=tmp_path / "w", run=RecordingCli())


def test_a_replay_refuses_a_used_workdir(tmp_path):
    root = write_scenario(tmp_path / "s", body([]))
    replay(load(root), workdir=tmp_path / "w", run=RecordingCli())
    with pytest.raises(ScenarioError, match="already holds"):
        replay(load(root), workdir=tmp_path / "w", run=RecordingCli())


def test_a_step_that_crashes_still_restores_the_caller(tmp_path):
    root = write_scenario(tmp_path / "s", body([{"kind": "cli", "argv": ["x"]}]))
    before = dict(os.environ), Path.cwd()

    def explode(argv):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        replay(load(root), workdir=tmp_path / "w", run=explode)
    assert (dict(os.environ), Path.cwd()) == before


def test_a_cli_that_prints_no_document_is_read_as_none(tmp_path):
    root = write_scenario(tmp_path / "s", body([{"kind": "cli", "argv": ["x"]}]))

    def silent(argv):
        print("plain text, not JSON")
        return 0

    result = replay(load(root), workdir=tmp_path / "w", run=silent)
    assert result.steps[0].document is None


def test_a_system_exit_is_read_as_its_code(tmp_path):
    root = write_scenario(tmp_path / "s", body([{"kind": "cli", "argv": ["x"]}]))

    def exits(argv):
        raise SystemExit(2)

    result = replay(load(root), workdir=tmp_path / "w", run=exits)
    assert result.steps[0].exit_code == 2
    assert result.outcome.refusals == {"#1": ()}


# -- the loader ---------------------------------------------------------------


def test_a_shipped_scenario_loads_with_its_tree():
    scenario = load_named("clean")
    assert scenario.name == "clean"
    assert scenario.tree == SCENARIO_DIR / "clean" / "tree"
    assert scenario.steps[0].kind == "cli"
    assert scenario.expected.refusals == {}


def test_an_unknown_scenario_name_is_refused():
    with pytest.raises(ScenarioError, match="no scenario named"):
        load_named("nope")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda b: b.update(extra=1), "unknown key"),
        (lambda b: b["steps"].append({"kind": "teleport"}), "unknown step kind"),
        (lambda b: b["steps"].append({"kind": "commit", "colour": 1}), "unknown key"),
        (lambda b: b["steps"].append({"kind": "cli", "argv": "pipeline"}), "list of strings"),
        (lambda b: b["steps"].append({"kind": "cli"}), "needs argv"),
        (lambda b: b["steps"].append({"kind": "hook", "event": "stop", "payload": []}),
         "mapping"),
        (lambda b: b["steps"].append({"kind": "write", "path": "/abs", "content": ""}),
         "relative"),
        (lambda b: b["steps"].append({"kind": "write", "path": "../up", "content": ""}),
         "relative"),
        (lambda b: b["steps"].append({"kind": "commit", "in": "elsewhere"}), "root or worktree"),
        (lambda b: b["steps"].append("cli"), "mapping"),
        (lambda b: b["steps"].extend([{"kind": "commit", "id": "a"}] * 2), "twice"),
        (lambda b: b["expect"]["gates"].update(red={"no.such_rule": "pass"}),
         "unregistered rule"),
        (lambda b: b["expect"]["gates"].update(red={"privacy.clean": "fine"}), "status"),
        (lambda b: b["expect"]["refusals"].update(ghost=["privacy.clean"]), "no step"),
        (lambda b: b["expect"].update(answers={}), "unknown key"),
        (lambda b: b.update(steps={}), "list"),
        (lambda b: b.pop("manifest"), "missing"),
        (lambda b: b.update(manifest=[]), "manifest must be a mapping"),
        (lambda b: b.update(expect=[]), "expect must be a mapping"),
        (lambda b: b["expect"].update(gates=[]), "must be mappings"),
        (lambda b: b["expect"]["gates"].update(red=["privacy.clean"]), "must be a mapping"),
        (lambda b: (b["steps"].append({"kind": "commit", "id": "c"}),
                    b["expect"]["refusals"].update(c="privacy.clean")), "list of rule ids"),
        (lambda b: b["steps"].append({"kind": "verdicts"}), "needs entries"),
        (lambda b: b["steps"].append({"kind": "verdicts", "entries": {}}), "must be a list"),
        (lambda b: b["expect"].update(verdicts=[]), "expect.verdicts must be a mapping"),
        (lambda b: b["expect"].update(verdicts={"ghost": {}}), "no step"),
        (lambda b: (b["steps"].append({"kind": "commit", "id": "c"}),
                    b["expect"].update(verdicts={"c": {}})), "does not show the verdicts"),
        (lambda b: (b["steps"].append(SHOW),
                    b["expect"].update(verdicts={"show": []})), "must be a mapping"),
        (lambda b: (b["steps"].append(SHOW),
                    b["expect"].update(verdicts={"show": {"r.one": "fine"}})),
         "is not one of"),
        (lambda b: (b["steps"].append({"kind": "commit", "id": "c"}),
                    b["expect"]["refusals"].update(c=["P9"])), "unregistered rule"),
    ],
)
def test_the_loader_refuses(tmp_path, mutate, message):
    data = body([])
    mutate(data)
    root = write_scenario(tmp_path / "s", data)
    with pytest.raises(ScenarioError, match=message):
        load(root)


#: A step that shows the verdicts, which an ``expect.verdicts`` entry names.
SHOW = {"kind": "cli", "id": "show", "argv": ["guidelines", "verdicts", "show"]}


def test_a_known_answer_may_name_verdict_predicates_and_standings(tmp_path):
    """A refusal names gate rules or a verdict submission's predicates, and a
    known answer names the standing each judgement rule must reach (D11)."""
    data = body(
        [{"kind": "verdicts", "id": "bulk", "entries": []}, SHOW],
        expect={"gates": {},
                "refusals": {"bulk": ["P3", "shape", "pipeline", "submission"]},
                "verdicts": {"show": {"r.one": "pass", "r.two": "stale", "r.three": "engine",
                                      "r.four": "missing", "r.five": "fail", "r.six": "na"}}},
    )
    scenario = load(write_scenario(tmp_path / "s", data))
    assert scenario.expected.refusals == {"bulk": ("P3", "pipeline", "shape", "submission")}
    assert scenario.expected.verdicts["show"]["r.two"] == "stale"
    assert scenario.steps[0].entries == ()


def test_a_refusal_naming_an_unregistered_rule_is_refused(tmp_path):
    data = body([{"kind": "commit", "id": "c"}],
                expect={"gates": {}, "refusals": {"c": ["no.such_rule"]}})
    root = write_scenario(tmp_path / "s", data)
    with pytest.raises(ScenarioError, match="unregistered rule"):
        load(root)


def test_a_scenario_without_a_tree_is_refused(tmp_path):
    root = tmp_path / "s"
    root.mkdir()
    (root / "scenario.yaml").write_text(yaml.safe_dump(body([])), encoding="utf-8")
    with pytest.raises(ScenarioError, match="tree"):
        load(root)


@pytest.mark.parametrize("text", ["- just a list\n", "key: [unclosed\n"])
def test_a_scenario_file_that_is_not_a_mapping_is_refused(tmp_path, text):
    root = write_scenario(tmp_path / "s", body([]))
    (root / "scenario.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ScenarioError):
        load(root)


def test_a_missing_scenario_file_is_refused(tmp_path):
    with pytest.raises(ScenarioError, match="scenario.yaml"):
        load(tmp_path)


def test_scenario_names_skip_directories_without_a_scenario(tmp_path):
    write_scenario(tmp_path / "b", body([]))
    write_scenario(tmp_path / "a", body([]))
    (tmp_path / "loose").mkdir()
    assert scenario_names(tmp_path) == ["a", "b"]
    assert scenario_names(tmp_path / "absent") == []


# -- reduction ---------------------------------------------------------------


def gate(rule, level="block", ok=True, skipped=False, message="prose"):
    return {"rule": rule, "level": level, "ok": ok, "skipped": skipped,
            "message": message, "detail": "", "fixes": []}


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        (gate("r", ok=True), "pass"),
        (gate("r", ok=True, skipped=True), "skip"),
        (gate("r", level="block", ok=False), "block"),
        (gate("r", level="ask", ok=False), "block"),
        (gate("r", level="warn", ok=False), "warn"),
        (gate("r", level="off", ok=False), "skip"),
    ],
)
def test_status_of(payload, status):
    assert status_of(payload) == status


def test_reduce_reads_every_gate_shape():
    results = [
        StepResult("#1", "cli", ("--json", "session", "start", "x"), 0, {"id": "s"}),
        StepResult("#2", "cli", ("--json", "pipeline", "gates"), 0, {
            "by_destination": {"red": [gate("docs.read_architecture", ok=False)],
                               "abandoned": []},
            "gates": [],
        }),
        StepResult("#3", "cli", ("--json", "pipeline", "gates", "--state", "green"), 0,
                   {"gates": [gate("test.new_test_fails", level="warn", ok=False)]}),
        StepResult("go", "cli", ("--json", "pipeline", "advance", "red"), 1, {
            "advanced": False,
            "gates": [gate("docs.read_architecture", ok=False),
                      gate("privacy.clean", level="warn", ok=False)],
        }),
        StepResult("#5", "cli", ("--json", "pipeline", "advance", "red", "--force"), 0,
                   {"advanced": True, "gates": [gate("docs.read_architecture")]}),
        StepResult("#6", "hook", ("hook", "pre-tool-use"), 0, {
            "hookSpecificOutput": {"permissionDecision": "deny"}}),
        StepResult("#7", "hook", ("hook", "post-tool-use"), 0, {}),
        StepResult("#9", "cli", ("--json", "pipeline", "gates"), 0,
                   {"gates": [gate("privacy.clean")]}),
        StepResult("#8", "write", (), 0, None),
    ]
    outcome = reduce(results)
    assert outcome.gates == {
        "red": {"docs.read_architecture": "pass"},
        "abandoned": {},
        "green": {"test.new_test_fails": "warn"},
    }
    assert outcome.refusals == {"go": ("docs.read_architecture",), "#6": ()}


def standing(rule, standing="current", status=None):
    return {"rule": rule, "standing": standing, "status": status, "problems": []}


def test_reduce_reads_a_refused_submission_and_the_shown_standings():
    results = [
        StepResult("vague", "cli", ("--json", "guidelines", "verdict", "r"), 1, {
            "recorded": [],
            "refused": [{"entry": 1, "rule": "r", "predicate": "P3", "problem": "prose"},
                        {"entry": 1, "rule": "r", "predicate": "P3", "problem": "more"},
                        {"entry": 2, "rule": None, "predicate": "shape", "problem": "x"},
                        "not a mapping"],
        }),
        StepResult("ok", "cli", ("--json", "guidelines", "verdict", "r"), 0,
                   {"recorded": [{"rule": "r"}]}),
        StepResult("show", "cli", ("--json", "guidelines", "verdicts", "show"), 0, {
            "owed": [standing("r", status="pass"), standing("s", "stale"),
                     standing("t", "engine", "na"), standing("u", "missing"),
                     {"no": "rule"}],
        }),
        StepResult("show-none", "cli", ("--json", "guidelines", "verdicts", "show"), 0,
                   {"status": "no-pipeline", "owed": []}),
        StepResult("show-broken", "cli", ("--json", "guidelines", "verdicts", "show"), 1, None),
        StepResult("#6", "cli", ("--json", "guidelines", "show"), 0, {"owed": "ignored"}),
    ]
    outcome = reduce(results)
    assert outcome.refusals == {"vague": ("P3", "shape"), "show-broken": ()}
    assert outcome.verdicts == {
        "show": {"r": "pass", "s": "stale", "t": "engine", "u": "missing"},
        "show-none": {},
    }
    assert outcome.gates == {}


def test_reduce_ignores_an_advance_with_no_target():
    results = [StepResult("#1", "cli", ("--json", "pipeline", "advance"), 2, None)]
    assert reduce(results) == Outcome(gates={}, refusals={"#1": ()})


# -- comparison and rendering --------------------------------------------------


def expected(gates, refusals=None) -> Expected:
    return Expected(gates=gates, refusals={k: tuple(v) for k, v in (refusals or {}).items()})


def test_compare_finds_every_kind_of_divergence():
    want = expected(
        {
            "red": {"docs.read_architecture": "block", "privacy.clean": "pass"},
            "green": {"test.new_test_fails": "pass"},
        },
        {"go": ["docs.read_architecture"], "other": ["privacy.clean"]},
    )
    got = Outcome(
        gates={
            "red": {"docs.read_architecture": "pass", "test.all_pass": "pass"},
            "committed": {"privacy.clean": "pass"},
        },
        refusals={"go": ("privacy.clean",), "extra": ()},
    )
    found = {(d.where, d.expected, d.actual) for d in compare(want, got)}
    assert found == {
        ("gates to red: docs.read_architecture", "block", "pass"),
        ("gates to red: privacy.clean", "pass", "absent"),
        ("gates to red: test.all_pass", "absent", "pass"),
        ("gates to green", "observed", "never observed"),
        ("refusal at go", "docs.read_architecture", "privacy.clean"),
        ("refusal at other", "privacy.clean", "no refusal"),
        ("refusal at extra", "no refusal", "(none named)"),
    }


def test_compare_holds_the_verdicts_each_named_step_showed():
    want = Expected(gates={}, refusals={},
                    verdicts={"show": {"r": "pass", "s": "missing"}, "later": {"r": "stale"}})
    got = Outcome(gates={}, refusals={},
                  verdicts={"show": {"r": "fail", "t": "engine"}, "extra": {}})
    found = {(d.where, d.expected, d.actual) for d in compare(want, got)}
    assert found == {
        ("verdicts at show: r", "pass", "fail"),
        ("verdicts at show: s", "missing", "absent"),
        ("verdicts at show: t", "absent", "engine"),
        ("verdicts at later", "observed", "never observed"),
    }
    exhaustive = compare(got.as_expected(), Outcome(gates={}, refusals={}, verdicts={
        "show": {"r": "fail", "t": "engine"}, "extra": {}, "more": {}}), exhaustive=True)
    assert exhaustive == [Divergence("verdicts at more", "never observed", "observed")]


def test_exhaustive_compare_also_names_destinations_only_one_side_saw():
    got = Outcome(gates={"red": {}, "committed": {"privacy.clean": "pass"}}, refusals={})
    found = compare(expected({"red": {}}), got, exhaustive=True)
    assert found == [Divergence("gates to committed", "never observed", "observed")]
    assert compare(expected({"red": {}}), got) == []


def test_as_expected_round_trips():
    got = Outcome(gates={"red": {"privacy.clean": "pass"}}, refusals={"go": ("x.y",)},
                  verdicts={"show": {"r": "pass"}})
    assert compare(got.as_expected(), got, exhaustive=True) == []


def test_render_names_the_scenario_and_each_divergence():
    text = render("clean", [Divergence("gates to red: privacy.clean", "pass", "block")])
    assert "clean" in text
    assert "gates to red: privacy.clean" in text
    assert "expected pass, got block" in text
    assert "matches its known answer" in render("clean", [])


# -- the D1 test: no gate reads who did the work (A7 increment 4) ---------------
#
# ADR-012 D1 items 1 and 2, the A7 rulings 7, 8, 12 and 13, and A1 increment 4's
# owed test. Two halves: a scan of the source, and replays whose recorded
# identity is rewritten under them.
#
# This proves ENGINE INDEPENDENCE ONLY: the same recorded work reaches the same
# gates whatever the record says about who did it. Whether two models given the
# same brief reach the same verdicts is another question, and it is the driven
# layer's: A1 increments 5 and 6.
#
# Deliberately NOT permuted, because each is an input the engine is meant to read:
#
# - role membership. ADR-003's role gates read it: the gate needs the role filled
#   (the rename below shows it does not care what fills it).
# - host.kind. It is configuration, an input to which gates apply (ruling 8), so
#   only the recorded host, audit.host, is permuted.
# - hand-edited agent bytes. An edited copy is a change the project made, and
#   agents.rendered_current answers for it (A6.4). Ruling 6 covers a copy that
#   differs by its model line alone, and that is permuted.
#
# Also not permuted: an invalid agents.tiers or host.tier_map value. Those are
# refused at load, so no gate is ever reached.


SOURCE_ROOT = Path(invariant.__file__).resolve().parent

#: What ruling 13's scan covers.
SCANNED_MODULES = ("core/rules.py", "core/guidelines.py", "core/verdicts.py", "core/findings.py")

#: A string whose whole text is one of these is a key being read.
REFUSED_LITERALS = frozenset({"model", "tier", "tiers", "tier_map", "audit", "agent", "token",
                              "agents_run_how"})

#: Dotted configuration keys. Prose has no reason to hold these, and a lookup
#: might build a longer string around one, so they are refused anywhere in a string.
REFUSED_DOTTED = ("agents.tiers", "host.tier_map")

#: Attribute reads of the same things. ``agent`` and ``token`` are a verdict's.
REFUSED_ATTRIBUTES = frozenset({"model", "tier", "tiers", "tier_map", "audit", "agent", "token",
                                "TIERS", "AUDIT_KEY"})

#: Bare names and imports of the vocabulary and of the audit record's key.
REFUSED_NAMES = frozenset({"TIERS", "AUDIT_KEY"})

#: The module whose every import is refused: hosts/ is where a model choice lives.
REFUSED_PACKAGE = "hosts"


class Hit(NamedTuple):
    """One thing the scan refuses. ``scope`` is the enclosing class and function,
    or at module level the single name being assigned, so an allow-list entry
    can be as narrow as one definition."""

    scope: str
    kind: str
    name: str


class _IdentityScan(ast.NodeVisitor):
    """Reads imports, attributes, names and string values, and nothing else.

    A comment is not in the tree. A docstring, or any other string standing as
    a statement, is skipped, since it does nothing at run time.
    """

    def __init__(self) -> None:
        self.hits: list[Hit] = []
        self.scope: list[str] = []

    def _hit(self, kind: str, name: str) -> None:
        self.hits.append(Hit(".".join(self.scope) or "<module>", kind, name))

    def _scoped(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scoped(node, node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scoped(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scoped(node, node.name)

    def visit_Assign(self, node: ast.Assign) -> None:
        self._assigned(node, node.targets[0] if len(node.targets) == 1 else None)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._assigned(node, node.target)

    def _assigned(self, node: ast.AST, target: ast.expr | None) -> None:
        if not self.scope and isinstance(target, ast.Name):
            self._scoped(node, target.id)
        else:
            self.generic_visit(node)

    def visit_Expr(self, node: ast.Expr) -> None:
        if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if REFUSED_PACKAGE in alias.name.split("."):
                self._hit("import", alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            if REFUSED_PACKAGE in module.split(".") or alias.name == REFUSED_PACKAGE:
                self._hit("import", f"{module}.{alias.name}")
            elif alias.name in REFUSED_NAMES:
                self._hit("import", alias.name)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load) and node.attr in REFUSED_ATTRIBUTES:
            self._hit("attribute", node.attr)
        self.generic_visit(node)

    def visit_MatchClass(self, node: ast.MatchClass) -> None:
        # ``case Record(model=found)`` reads the attribute with no Attribute node.
        for name in node.kwd_attrs:
            if name in REFUSED_ATTRIBUTES:
                self._hit("attribute", name)
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load) and node.id in REFUSED_NAMES:
            self._hit("name", node.id)

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str):
            return
        if node.value in REFUSED_LITERALS:
            self._hit("literal", node.value)
        for key in REFUSED_DOTTED:
            if key in node.value:
                self._hit("literal", key)


def identity_hits(source: str) -> list[Hit]:
    scan = _IdentityScan()
    scan.visit(ast.parse(source))
    return scan.hits


#: Every read the scan finds in the shipped modules, with its exact count. The
#: scan fails on anything else, on a second read inside a listed scope, and on an
#: entry that no longer matches, so this cannot quietly outlive what it excuses.
ALLOWED_READS: dict[tuple[str, Hit], int] = {
    # The audit record's key, named once so nothing spells it twice.
    ("core/rules.py", Hit("AUDIT_KEY", "literal", "audit")): 1,
    # The one deliberate read: _without_audit looks for the key to remove it, on
    # the way into every RuleContext, which is why no rule can see it (ruling 10).
    ("core/rules.py", Hit("_without_audit", "name", "AUDIT_KEY")): 2,
    # Fix text an operator reads for an edited agent copy, naming where a model
    # line belongs. Nothing looks a key up by it (A7.2).
    ("core/rules.py", Hit("AGENT_OVERLAY_FIX", "literal", "agents.tiers")): 1,
    ("core/rules.py", Hit("AGENT_OVERLAY_FIX", "literal", "host.tier_map")): 1,
    # The record writing itself out: each stamp is written only when set. These
    # reads leave the engine as evidence and never reach a decision (DN-12).
    ("core/verdicts.py", Hit("VerdictRecord.record", "attribute", "agent")): 2,
    ("core/verdicts.py", Hit("VerdictRecord.record", "attribute", "token")): 2,
    ("core/verdicts.py", Hit("VerdictRecord.record", "attribute", "model")): 2,
    ("core/verdicts.py", Hit("VerdictRecord.record", "literal", "agent")): 1,
    ("core/verdicts.py", Hit("VerdictRecord.record", "literal", "token")): 1,
    ("core/verdicts.py", Hit("VerdictRecord.record", "literal", "model")): 1,
    # The keys a submission may not carry, and the ones resubmitted drops before
    # the gate re-checks a record. Naming them is how the gate never reads them.
    ("core/verdicts.py", Hit("_RECORD_ONLY_KEYS", "literal", "agent")): 1,
    ("core/verdicts.py", Hit("_RECORD_ONLY_KEYS", "literal", "token")): 1,
    ("core/verdicts.py", Hit("_RECORD_ONLY_KEYS", "literal", "model")): 1,
}


def test_the_scanned_modules_read_no_identity_beyond_the_named_exceptions():
    """D1 items 1 and 2, ruling 13: the source half."""
    found: Counter[tuple[str, Hit]] = Counter()
    for module in SCANNED_MODULES:
        for hit in identity_hits((SOURCE_ROOT / module).read_text(encoding="utf-8")):
            found[(module, hit)] += 1
    assert dict(found) == ALLOWED_READS


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from invariant.core.agents import TIERS", ("import", "TIERS")),
        ("from .agents import TIERS as vocabulary", ("import", "TIERS")),
        ("from ..hosts import claude_code", ("import", "hosts.claude_code")),
        ("from ..hosts.assets import classify", ("import", "hosts.assets.classify")),
        ("from .. import hosts", ("import", ".hosts")),
        ("import invariant.hosts.claude_code", ("import", "invariant.hosts.claude_code")),
        ("def f(agents):\n    return agents.TIERS", ("attribute", "TIERS")),
        ("def f():\n    return TIERS", ("name", "TIERS")),
        ("def f(rules):\n    return rules.AUDIT_KEY", ("attribute", "AUDIT_KEY")),
        ("def f(e):\n    return e[AUDIT_KEY]", ("name", "AUDIT_KEY")),
        ("def f(v):\n    return v.model", ("attribute", "model")),
        ("def f(v):\n    return v.agent", ("attribute", "agent")),
        ("def f(v):\n    return v.token", ("attribute", "token")),
        ("def f(m):\n    return m.host.tier_map", ("attribute", "tier_map")),
        ("def f(m):\n    return m.agents.tiers", ("attribute", "tiers")),
        ("def f(s):\n    return s.tier", ("attribute", "tier")),
        ("def f(p):\n    return p.audit", ("attribute", "audit")),
        ("def f(ctx):\n    return ctx.evidence('audit')", ("literal", "audit")),
        ("def f(r):\n    return r.get('agent')", ("literal", "agent")),
        ("def f(r):\n    return r['token']", ("literal", "token")),
        ("def f(v):\n    return getattr(v, 'model')", ("literal", "model")),
        ("def f(m):\n    return m.data['host']['tier_map']", ("literal", "tier_map")),
        ("def f(m):\n    return m.get('agents.tiers')", ("literal", "agents.tiers")),
        ("def f(m):\n    return m.get('host.tier_map')", ("literal", "host.tier_map")),
        ("def f(m):\n    return m.get('x.host.tier_map.deep')", ("literal", "host.tier_map")),
        ("def f(v):\n    return f'{v.model} ran'", ("attribute", "model")),
        ("def f(ctx):\n    return ctx.evidence('agents_run_how')", ("literal", "agents_run_how")),
        ("def f(v):\n    match v:\n        case R(model=m):\n            return m",
         ("attribute", "model")),
        ("def f(v):\n    match v:\n        case {'token': t}:\n            return t",
         ("literal", "token")),
    ],
)
def test_the_scan_refuses_each_kind_of_read(source, expected):
    assert expected in [(hit.kind, hit.name) for hit in identity_hits(source)]


@pytest.mark.parametrize(
    "source",
    [
        '"""The model and the tier, agents.tiers and host.tier_map."""',
        'def f():\n    """token and agent and audit."""',
        'class C:\n    """model"""',
        '"model"',
        "# from ..hosts import claude_code; v.model; 'tier'\nx = 1",
        "FIX = 'whichever model ran, at whatever tier'",
        "def f(host):\n    return host.kind",
        "def f(model):\n    return model",
        "def f(v):\n    return v.models",
        "class C:\n    model: str | None = None",
        "def f(v, x):\n    v.model = x",
        "from .agents import AgentSource",
        "import invariant.core.rules",
    ],
)
def test_the_scan_leaves_prose_and_unrelated_code_alone(source):
    assert identity_hits(source) == []


def test_a_scope_narrows_to_the_assigned_name_and_the_definition():
    source = (
        "KEY = 'audit'\n"
        "class C:\n"
        "    def read(self):\n"
        "        return self.token\n"
    )
    assert identity_hits(source) == [
        Hit("KEY", "literal", "audit"),
        Hit("C.read", "attribute", "token"),
    ]


def test_an_excused_read_inside_a_new_scope_is_still_a_finding():
    """The allow-list is by definition, so a rule cannot borrow its entry."""
    source = "def _a_rule(ctx):\n    return ctx.pipeline['evidence'][AUDIT_KEY]\n"
    (hit,) = identity_hits(source)
    assert ("core/rules.py", hit) not in ALLOWED_READS


# The replays. Each rewrites the identity fields of the state record before every
# CLI call, so the engine only ever reads hostile values, and the outcome must
# still be the known answer.


#: Hostile values, one kind per entry. None is a host's real model name (ruling 4).
HOSTILE_VALUES: dict[str, Any] = {
    "null": None,
    "number": 7,
    "boolean": True,
    "list": ["placeholder-a", 1, None],
    "mapping": {"model": {"deep": []}, "tier": None},
    "empty": "",
    "long": "x" * 20_000,
    "control": "line\nbreak\x00 {root} #: 'quoted'",
    "another-role": "compliance-reviewer",
    "another-host": "placeholder-host",
}


@dataclass(frozen=True)
class Permutation:
    """What every identity field is rewritten to.

    ``pick`` answers a value per field per rewrite. ``whole`` replaces the audit
    record and ``agents_run_how`` outright, where the other form keeps their
    shape and rewrites each field inside.
    """

    name: str
    pick: Callable[[], Any]
    whole: bool = False


def fixed(name: str, *, whole: bool = False) -> Permutation:
    value = HOSTILE_VALUES[name]
    return Permutation(f"{name}-whole" if whole else name, lambda: value, whole)


def seeded(seed: int) -> Permutation:
    """Ruling 12: stdlib random under a fixed seed, so a failure repeats."""
    rng = random.Random(seed)
    pool = list(HOSTILE_VALUES.values())

    def pick() -> Any:
        if rng.random() < 0.5:
            return rng.choice(pool)
        return "".join(rng.choices(string.printable, k=rng.randrange(1, 64)))

    return Permutation(f"seed-{seed}", pick)


def permute_evidence(evidence: dict, permutation: Permutation) -> None:
    pick = permutation.pick
    agents = [a for a in evidence.get("agents_run") or () if isinstance(a, str)]
    if permutation.whole:
        evidence["audit"] = pick()
        evidence["agents_run_how"] = pick()
    else:
        evidence["audit"] = {
            "host": pick(),
            "agents": {a: {"tier": pick(), "asked": pick(), "models": pick(), "runs": pick()}
                       for a in agents},
        }
        evidence["agents_run_how"] = {a: pick() for a in agents}
    for verdict in evidence.get("verdicts") or ():
        verdict.update(agent=pick(), model=pick(), token=pick())


class PermutingCli:
    """The real CLI, with the state record rewritten before each call."""

    def __init__(self, workdir: Path, permutation: Permutation, rewrite=permute_evidence) -> None:
        self.records = workdir / "repo" / ".git" / "invariant" / "state" / "pipelines"
        self.permutation = permutation
        self.rewrite = rewrite
        self.rewrites = 0
        self.verdicts_seen = 0

    def __call__(self, argv: list[str]) -> int:
        from invariant.cli.main import main

        for path in self.records.glob("*.json"):
            document = json.loads(path.read_text(encoding="utf-8"))
            current = document.get("current")
            if isinstance(current, dict) and isinstance(current.get("evidence"), dict):
                self.rewrite(current["evidence"], self.permutation)
                self.rewrites += 1
                self.verdicts_seen += len(current["evidence"].get("verdicts") or ())
                path.write_text(json.dumps(document), encoding="utf-8")
        return main(argv)


#: Configuration: each is a valid manifest the operator could write (rulings 1, 4).
#: Every value is opaque text, chosen to be awkward as YAML and as one line.
CONFIGS: dict[str, dict[str, dict[str, str]]] = {
    "unset": {"tiers": {}, "tier_map": {}},
    "tiers-only": {
        "tiers": {"feature-architect": "light", "compliance-reviewer": "deep",
                  "bug-reproducer": "standard", "backlog-curator": "light"},
        "tier_map": {},
    },
    "map-only": {
        "tiers": {},
        "tier_map": {"light": "placeholder-small", "standard": "x: y", "deep": "a #b"},
    },
    "both": {
        "tiers": {"feature-architect": "standard", "compliance-reviewer": "light"},
        "tier_map": {"light": "true", "standard": "y" * 5_000, "deep": "{root}"},
    },
}


def configured(manifest: Mapping[str, Any], config: str) -> dict[str, Any]:
    chosen = CONFIGS[config]
    return {
        **manifest,
        "agents": {**manifest.get("agents", {}), "tiers": chosen["tiers"]},
        "host": {**manifest.get("host", {}), "tier_map": chosen["tier_map"]},
    }


def with_installed_copies(scenario: Scenario, workdir: Path, installed_under: str) -> Scenario:
    """``scenario`` seeded with agent copies rendered under another configuration.

    The copies come from the real ``agents install``, run in a replay of their
    own, and join a copy of the tree, so a scenario's positional step labels stay
    what its known answer names.
    """
    install = replace(
        scenario,
        manifest=configured(scenario.manifest, installed_under),
        steps=(Step("cli", "install", argv=("agents", "install")),),
        expected=Expected(gates={}, refusals={}),
    )
    done = replay(install, workdir=workdir / "install")
    assert done.outcome.refusals == {}, done.steps[0].document
    tree = workdir / "tree"
    shutil.copytree(scenario.tree, tree)
    shutil.copytree(workdir / "install" / "repo" / ".claude" / "agents",
                    tree / ".claude" / "agents")
    return replace(scenario, tree=tree)


def copies_current(gates: Mapping[str, Mapping[str, str]]) -> dict:
    """Gates with installed copies: agents.rendered_current passes where it warned."""
    return {
        destination: {rule: "pass" if rule == "agents.rendered_current" else status
                      for rule, status in rules.items()}
        for destination, rules in gates.items()
    }


#: The matrix (ruling 12; each entry is one replay, and a replay is the slow part
#: of this file). Every scenario runs once, so every scenario meets a hostile
#: record and a non-default configuration, and half of them meet installed copies
#: rendered under a different configuration, which is the ruling 6 case. The five
#: scenarios that record a verdict get five different value kinds between them,
#: and `clean` runs under every kind of value below.
#:
#: Left out, to keep this file's run time down: every scenario under every value
#: and every configuration. That product is about 600 replays. What it would add
#: over this is a scenario that reads one value kind differently from another,
#: and only a field read by a gate could do that, which the scan looks for.
Matrix = tuple[str, Callable[[], Permutation], str, str | None]
MATRIX: tuple[Matrix, ...] = (
    ("baseline-grown", lambda: fixed("number"), "map-only", None),
    ("baselined", lambda: fixed("control"), "tiers-only", "unset"),
    ("clean", lambda: seeded(1), "both", "map-only"),
    ("docs-first-breach", lambda: fixed("null", whole=True), "both", None),
    ("engine-answers", lambda: fixed("mapping"), "map-only", "both"),
    ("fixed-not-pruned", lambda: fixed("empty"), "tiers-only", None),
    ("judgement-fail", lambda: fixed("long"), "both", None),
    ("judgement-fail-standard", lambda: fixed("number"), "both", "unset"),
    ("malformed-ruleset", lambda: fixed("list", whole=True), "map-only", "tiers-only"),
    ("missing-test", lambda: fixed("boolean"), "unset", None),
    ("red-first-breach", lambda: fixed("mapping", whole=True), "tiers-only", "both"),
    ("seeded-pattern", lambda: fixed("another-role"), "both", "map-only"),
    ("stale-verdict", lambda: seeded(2), "map-only", None),
    ("vague-verdict", lambda: seeded(3), "tiers-only", "both"),
    ("valid-ruleset", lambda: fixed("null"), "both", "unset"),
)

#: The scenarios where a verdict is on the record when a gate reads it.
RECORDS_A_VERDICT = {"engine-answers", "judgement-fail", "judgement-fail-standard",
                     "stale-verdict", "valid-ruleset"}


def test_the_matrix_covers_every_shipped_scenario():
    assert [entry[0] for entry in MATRIX] == SHIPPED


def assert_same_answer(name, scenario, plain, outcome):
    """The known answer, and every gate the plain replay saw, known or not."""
    divergences = compare(scenario.expected, outcome)
    assert divergences == [], render(name, divergences)
    divergences = compare(plain.as_expected(), outcome, exhaustive=True)
    assert divergences == [], render(name, divergences)


@pytest.mark.parametrize(("name", "permutation", "config", "installed_under"), MATRIX,
                         ids=[entry[0] for entry in MATRIX])
def test_a_hostile_record_and_configuration_change_no_outcome(
        name, permutation, config, installed_under, shipped, tmp_path):
    """D1 item 1: gate outcomes are a function of the ruleset, the tree and the
    evidence. Rewriting who did the work, and how it was configured to be done,
    changes none of them."""
    scenario = load_named(name)
    plain = shipped(name).outcome
    if installed_under is not None:
        scenario = with_installed_copies(scenario, tmp_path, installed_under)
        scenario = replace(scenario, expected=replace(
            scenario.expected, gates=copies_current(scenario.expected.gates)))
        plain = replace(plain, gates=copies_current(plain.gates))
    scenario = replace(scenario, manifest=configured(scenario.manifest, config))
    workdir = tmp_path / "replay"
    cli = PermutingCli(workdir, permutation())
    outcome = replay(scenario, workdir=workdir, run=cli).outcome
    assert cli.rewrites > 0
    assert (cli.verdicts_seen > 0) == (name in RECORDS_A_VERDICT)
    assert_same_answer(name, scenario, plain, outcome)


@pytest.mark.parametrize(
    "permutation",
    [fixed(n) for n in HOSTILE_VALUES] + [fixed("null", whole=True), fixed("number", whole=True),
                                          fixed("long", whole=True), seeded(4)],
    ids=lambda p: p.name,
)
def test_the_clean_change_lands_under_every_hostile_value(permutation, shipped, tmp_path):
    """Every value kind on one full run, from the first hook to the landing."""
    scenario = load_named("clean")
    cli = PermutingCli(tmp_path, permutation)
    outcome = replay(scenario, workdir=tmp_path, run=cli).outcome
    assert_same_answer("clean", scenario, shipped("clean").outcome, outcome)


def test_a_rewrite_reaches_the_record_the_gates_read(tmp_path):
    """The harness's own proof: what the last call found on disk is the hostile
    value, on every field the rulings name, on a run that recorded a verdict."""
    seen: list[dict] = []

    def keep(evidence: dict, permutation: Permutation) -> None:
        permute_evidence(evidence, permutation)
        seen.append(copy.deepcopy(evidence))

    value = "placeholder-model"
    permutation = Permutation("placeholder", lambda: value)
    replay(load_named("judgement-fail-standard"), workdir=tmp_path,
           run=PermutingCli(tmp_path, permutation, keep))
    last = seen[-1]
    assert last["audit"]["host"] == value
    for agent in ("feature-architect", "compliance-reviewer"):
        entry = last["audit"]["agents"][agent]
        assert (entry["tier"], entry["asked"], entry["models"], entry["runs"]) == (value,) * 4
        assert last["agents_run_how"][agent] == value
    (verdict,) = last["verdicts"]
    assert (verdict["agent"], verdict["model"], verdict["token"]) == (value, value, value)


def test_a_rewrite_of_what_a_gate_does_read_changes_the_outcome(tmp_path):
    """The other half of the harness's proof: this comparison can fail. Empty the
    field the role gates are meant to read and they stop passing. The scenario's
    standard profile holds them at warn."""

    def forget_the_agents(evidence: dict, permutation: Permutation) -> None:
        evidence["agents_run"] = []

    scenario = load_named("clean")
    cli = PermutingCli(tmp_path, fixed("null"), forget_the_agents)
    outcome = replay(scenario, workdir=tmp_path, run=cli).outcome
    assert outcome.gates["committed"]["agents.design_agent_ran"] == "warn"
    assert compare(scenario.expected, outcome) != []


def test_a_copy_differing_only_by_its_model_line_reads_as_current(tmp_path):
    """Ruling 6 through the whole replay: copies rendered under one tier map, a
    landing under another, and agents.rendered_current passes."""
    scenario = with_installed_copies(load_named("clean"), tmp_path, "map-only")
    copies = tmp_path / "tree" / ".claude" / "agents"
    assert "model: placeholder-small" not in (copies / "feature-architect.md").read_text()
    assert 'model: "a #b"' in (copies / "feature-architect.md").read_text()
    moved = replace(scenario, manifest=configured(scenario.manifest, "both"))
    outcome = replay(moved, workdir=tmp_path / "replay").outcome
    assert outcome.gates["committed"]["agents.rendered_current"] == "pass"
    assert outcome.refusals == {}


# The rename (ruling 7): the role gates need the role filled and do not care what
# fills it.

RENAMED = {"feature-architect": "plan-writer", "compliance-reviewer": "change-judge"}


def renamed_step(step: Step) -> Step:
    tool_input = step.payload.get("tool_input") or {}
    agent = tool_input.get("subagent_type") if isinstance(tool_input, Mapping) else None
    if agent not in RENAMED:
        return step
    payload = {**step.payload, "tool_input": {**tool_input, "subagent_type": RENAMED[agent]}}
    return replace(step, payload=payload)


def renamed_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {
        **manifest,
        "agents": {
            "library": ["backlog-curator", "bug-reproducer"],
            "local": sorted(RENAMED.values()),
            "roles": {"design": [RENAMED["feature-architect"]],
                      "review": [RENAMED["compliance-reviewer"]]},
        },
    }


@pytest.mark.parametrize("name", ["clean", "seeded-pattern", "engine-answers"])
def test_a_consistent_rename_of_the_role_agents_changes_no_outcome(name, shipped, tmp_path):
    """One landing that passes, one refused, and one with a verdict on it."""
    scenario = load_named(name)
    renamed = replace(scenario, manifest=renamed_manifest(scenario.manifest),
                      steps=tuple(renamed_step(step) for step in scenario.steps))
    outcome = replay(renamed, workdir=tmp_path).outcome
    assert_same_answer(name, scenario, shipped(name).outcome, outcome)
    (record,) = (tmp_path / "repo" / ".git" / "invariant" / "state" / "pipelines").glob("*.json")
    evidence = json.loads(record.read_text(encoding="utf-8"))["current"]["evidence"]
    assert evidence["agents_run"] == list(RENAMED.values())


def test_an_agent_outside_the_role_does_not_fill_it(tmp_path):
    """So the rename is not passing because the gates read nothing: the roles
    renamed and the dispatches not, and neither role gate passes. The scenario's
    standard profile holds them at warn."""
    scenario = load_named("clean")
    mismatched = replace(scenario, manifest=renamed_manifest(scenario.manifest))
    outcome = replay(mismatched, workdir=tmp_path).outcome
    landing = outcome.gates["committed"]
    assert landing["agents.design_agent_ran"] == "warn"
    assert landing["agents.review_agent_ran"] == "warn"
