"""The conformance harness's engine layer (IDEA_031 A1, ADR-012 D11).

Each shipped scenario is replayed through the real CLI, in-process, in a fresh
repository, and its gate outcomes are compared with the scenario's known
answer. A second replay in another fresh tree must reach identical outcomes:
that is the engine-layer half of the model-agnostic contract (D1 item 5).
Prose is never compared, only rule ids and their statuses.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from invariant.cli.conformance import replay
from invariant.core.conformance import (
    SCENARIO_DIR,
    Divergence,
    Expected,
    Outcome,
    ScenarioError,
    StepResult,
    compare,
    load,
    load_named,
    reduce,
    render,
    scenario_names,
    status_of,
)

SHIPPED = ["clean", "docs-first-breach", "red-first-breach"]


# -- the known answers ------------------------------------------------------


def test_the_shipped_scenarios_are_the_first_set():
    assert scenario_names() == SHIPPED


@pytest.mark.parametrize("name", SHIPPED)
def test_a_replay_matches_the_known_answer(name, tmp_path):
    scenario = load_named(name)
    outcome = replay(scenario, workdir=tmp_path).outcome
    divergences = compare(scenario.expected, outcome)
    assert divergences == [], render(name, divergences)


@pytest.mark.parametrize("name", SHIPPED)
def test_two_replays_in_fresh_trees_agree(name, tmp_path):
    scenario = load_named(name)
    first = replay(scenario, workdir=tmp_path / "first").outcome
    second = replay(scenario, workdir=tmp_path / "second").outcome
    divergences = compare(first.as_expected(), second, exhaustive=True)
    assert divergences == [], render(name, divergences)
    assert first == second


def test_the_clean_landing_carries_every_landing_gate(tmp_path):
    """Today's landing set, so a gate added to it is a known-answer change."""
    outcome = replay(load_named("clean"), workdir=tmp_path).outcome
    assert sorted(outcome.gates["committed"]) == [
        "agents.design_agent_ran",
        "agents.review_agent_ran",
        "coverage.meets_floor",
        "docs.diagrams_current",
        "docs.updated",
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
        (lambda b: b["expect"].update(verdicts={}), "unknown key"),
        (lambda b: b.update(steps={}), "list"),
        (lambda b: b.pop("manifest"), "missing"),
        (lambda b: b.update(manifest=[]), "manifest must be a mapping"),
        (lambda b: b.update(expect=[]), "expect must be a mapping"),
        (lambda b: b["expect"].update(gates=[]), "must be mappings"),
        (lambda b: b["expect"]["gates"].update(red=["privacy.clean"]), "must be a mapping"),
        (lambda b: (b["steps"].append({"kind": "commit", "id": "c"}),
                    b["expect"]["refusals"].update(c="privacy.clean")), "list of rule ids"),
    ],
)
def test_the_loader_refuses(tmp_path, mutate, message):
    data = body([])
    mutate(data)
    root = write_scenario(tmp_path / "s", data)
    with pytest.raises(ScenarioError, match=message):
        load(root)


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


def test_exhaustive_compare_also_names_destinations_only_one_side_saw():
    got = Outcome(gates={"red": {}, "committed": {"privacy.clean": "pass"}}, refusals={})
    found = compare(expected({"red": {}}), got, exhaustive=True)
    assert found == [Divergence("gates to committed", "never observed", "observed")]
    assert compare(expected({"red": {}}), got) == []


def test_as_expected_round_trips():
    got = Outcome(gates={"red": {"privacy.clean": "pass"}}, refusals={"go": ("x.y",)})
    assert compare(got.as_expected(), got, exhaustive=True) == []


def test_render_names_the_scenario_and_each_divergence():
    text = render("clean", [Divergence("gates to red: privacy.clean", "pass", "block")])
    assert "clean" in text
    assert "gates to red: privacy.clean" in text
    assert "expected pass, got block" in text
    assert "matches its known answer" in render("clean", [])
