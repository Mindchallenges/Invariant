"""ADR-021 increments 1 and 2: a project's own steps, owed, and after-merge.

A step is declared under ``steps`` (D1) at one lifecycle point (D2), and is a
rule, ``step.<name>``, judged from its recorded runs (D3). The lifecycle
command runs it and records the run; ``check`` and the gates only read the
record. A run that exits with the step's ``owed_exit`` is owed: the rule
passes and a debt is recorded, which the next pass of the step clears (D4).
A failing ``block`` step after the merge leaves the merge standing and holds
teardown and handoff until a run passes or exits owed (ruling 2, D5).

Every repository here is a scratch one, every command a shell one-liner, and
nothing reaches a network.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml

from invariant.cli.main import main
from invariant.core import git as gitq
from invariant.core import steps as steps_reader
from invariant.core.errors import ManifestError, UnknownRuleError
from invariant.core.machines import load_pipeline_machine, load_session_machine
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest
from invariant.core.rules import RuleContext, evaluate, rules_for
from invariant.core.store import JsonStore
from invariant.domains.pipeline import run_command
from invariant.domains.steps import StepsDomain
from invariant.hosts import claude_code_launch as launch
from invariant.oracle.home import oracle_home
from invariant.oracle.index import ProjectIndex
from invariant.oracle.scan import scan_project

SID = "s0000021"


# -- fixtures ------------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def run(capsys, *argv) -> tuple[int, str]:
    capsys.readouterr()
    code = main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def run_json(capsys, *argv) -> tuple[int, dict]:
    code, text = run(capsys, *argv, "--json")
    lines = text.splitlines()
    for start in range(len(lines)):
        if lines[start].lstrip().startswith("{"):
            try:
                return code, json.loads("\n".join(lines[start:]))
            except json.JSONDecodeError:
                continue
    raise AssertionError(text)


def write_manifest(root: Path, extra: dict) -> None:
    body = {"version": 1, "project": {"name": "widgets"}, **extra}
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


def new_project(tmp_path: Path, extra: dict | None = None) -> Path:
    root = tmp_path / "widgets"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.invalid")
    git(root, "config", "user.name", "T")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    write_manifest(root, extra or {})
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


def load(root: Path, extra: dict) -> Manifest:
    write_manifest(root, extra)
    return Manifest.locate(root)


@pytest.fixture
def flags(tmp_path) -> Path:
    """Where a step's command looks for its go-ahead: outside every tree."""
    directory = tmp_path / "flags"
    directory.mkdir()
    return directory


def step(at: str, run: str, **more) -> dict:
    return {"at": at, "run": run, **more}


def gated(flags: Path, name: str = "ok") -> str:
    """A command that passes once the flag exists, and fails before."""
    return f"test -f {flags / name}"


# -- D1: the declaration -------------------------------------------------------


class TestTheDeclaration:
    def test_a_manifest_with_no_steps_resolves_exactly_as_before(self, tmp_path):
        root = new_project(tmp_path)
        manifest = Manifest.locate(root)
        assert "steps" not in manifest.data
        assert not [p for p in manifest.provenance if p.startswith("steps")]
        assert steps_reader.declared(manifest) == {}
        assert not [s for s in rules_for(manifest) if s.id.startswith("step.")]
        machine = load_pipeline_machine(manifest)
        assert not [g for g in machine.gate_ids() if g.startswith("step.")]
        assert not [
            g for g in load_session_machine(manifest).gate_ids() if g.startswith("step.")
        ]

    def test_a_step_loads_with_its_defaults(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step("session.before_merge", "true")}})
        lint = steps_reader.declared(manifest)["lint"]
        assert lint.level == "block"
        assert lint.timeout == 600
        assert lint.owed_exit is None
        assert lint.summary
        assert manifest.rule_level("step.lint") is Level.BLOCK

    def test_the_steps_level_is_the_rules_level_and_enforcement_wins(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step(
            "session.before_merge", "true", level="warn")}})
        assert manifest.rule_level("step.lint") is Level.WARN
        manifest = load(root, {
            "steps": {"lint": step("session.before_merge", "true", level="warn")},
            "enforcement": {"rules": {"step.lint": "block"}},
        })
        assert manifest.rule_level("step.lint") is Level.BLOCK

    def test_off_written_bare_reads_as_off(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step(
            "session.before_merge", "true", level=False)}})
        assert manifest.rule_level("step.lint") is Level.OFF

    def test_an_override_naming_an_undeclared_step_is_refused(self, tmp_path):
        root = new_project(tmp_path)
        with pytest.raises(UnknownRuleError, match="step.lnt"):
            load(root, {
                "steps": {"lint": step("session.before_merge", "true")},
                "enforcement": {"rules": {"step.lnt": "warn"}},
            })

    @pytest.mark.parametrize(
        ("declaration", "named"),
        [
            ({"Lint": step("session.before_merge", "true")}, "Lint"),
            ({"lint": step("session.before_merge", "true", colour="red")}, "colour"),
            ({"lint": step("session.after_commit", "true")}, "session.after_commit"),
            ({"lint": step("pipeline.enter.nowhere", "true")}, "nowhere"),
            ({"lint": step("pipeline.enter.triaging", "true")}, "never run"),
            ({"lint": step("pipeline.enter.working", "true")}, "never run"),
            ({"lint": {"run": "true"}}, "at"),
            ({"lint": {"at": "session.before_merge"}}, "run"),
            ({"lint": step("session.before_merge", "")}, "run"),
            ({"lint": step("session.before_merge", "true", level="ask")}, "ask"),
            ({"lint": step("session.before_merge", "true", owed_exit=0)}, "owed_exit"),
            ({"lint": step("session.before_merge", "true", owed_exit=300)}, "owed_exit"),
            ({"lint": step("session.before_merge", "true", owed_exit=True)}, "owed_exit"),
            ({"lint": step("session.before_merge", "true", timeout=0)}, "timeout"),
            ({"lint": step("session.before_merge", "true", timeout="soon")}, "timeout"),
            ({"lint": step("session.before_merge", "true", summary="a\nb")}, "summary"),
            ({"lint": step("session.before_merge", "run {nothing}")}, "nothing"),
            ({"lint": "true"}, "mapping"),
            (["lint"], "mapping"),
        ],
    )
    def test_a_wrong_declaration_is_refused_naming_it(self, tmp_path, declaration, named):
        root = new_project(tmp_path)
        with pytest.raises(ManifestError, match=named.replace(".", r"\.")):
            load(root, {"steps": declaration})

    def test_a_state_any_lane_has_is_a_point(self, tmp_path):
        """`doing` is chore's alone; the configured lane is tdd."""
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step("pipeline.enter.doing", "true")}})
        assert steps_reader.declared(manifest)["lint"].at == "pipeline.enter.doing"

    def test_an_initial_state_no_lane_enters_is_refused_naming_its_lanes(self, tmp_path):
        """`triaging` is where bugfix starts, and no lane has an edge into it:
        a step there would load and never run."""
        root = new_project(tmp_path)
        with pytest.raises(ManifestError, match=r"bugfix lane\(s\) start in") as refused:
            load(root, {"steps": {"lint": step("pipeline.enter.triaging", "true")}})
        assert "never advanced into it" in str(refused.value)

    def test_an_initial_state_another_lane_enters_is_a_point(self, tmp_path):
        """`designing` is where tdd starts, and spec-first has an edge back
        into it, so a step there runs in spec-first."""
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step("pipeline.enter.designing", "true")}})
        assert steps_reader.declared(manifest)["lint"].at == "pipeline.enter.designing"
        assert "step.lint" in load_pipeline_machine(manifest, "spec-first").state(
            "designing").entry_gates

    def test_vars_fill_the_command(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {
            "vars": {"device": "phone-1"},
            "steps": {"install": step("session.before_merge", "echo {device}")},
        })
        assert manifest.step_command("install") == "echo phone-1"

    def test_null_steps_is_no_steps(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": None})
        assert steps_reader.declared(manifest) == {}

    def test_invariant_disable_turns_a_step_off(self, tmp_path):
        root = new_project(tmp_path)
        write_manifest(root, {"steps": {"lint": step("session.before_merge", "true")}})
        manifest = Manifest.locate(root, env={"INVARIANT_DISABLE": "step.lint"})
        assert manifest.rule_level("step.lint") is Level.OFF


# -- D2, D3: the points become gates -------------------------------------------


class TestThePointsAreGates:
    def test_a_pipeline_enter_step_is_an_entry_gate_of_its_state(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {
            "lint": step("pipeline.enter.reviewing", "true"),
            "chores": step("pipeline.enter.doing", "true"),
        }})
        tdd = load_pipeline_machine(manifest, "tdd")
        assert "step.lint" in tdd.state("reviewing").entry_gates
        assert "step.chores" not in tdd.gate_ids()
        chore = load_pipeline_machine(manifest, "chore")
        assert "step.lint" in chore.state("reviewing").entry_gates
        assert "step.chores" in chore.state("doing").entry_gates

    def test_session_points_ride_the_states_they_guard(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {
            "lint": step("session.before_merge", "true"),
            "tidy": step("session.before_teardown", "true"),
            "note": step("session.before_handoff", "true"),
        }})
        machine = load_session_machine(manifest)
        assert "step.lint" in machine.state("merged").entry_gates
        assert "step.tidy" in machine.state("torn_down").entry_gates
        assert "step.note" not in machine.gate_ids()

    def test_an_after_merge_step_loads_and_holds_teardown_only(self, tmp_path):
        """D5: the step rides `torn_down`, never `merged`, which it follows."""
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"install": step("session.after_merge", "true")}})
        assert steps_reader.declared(manifest)["install"].at == "session.after_merge"
        machine = load_session_machine(manifest)
        assert "step.install" in machine.state("torn_down").entry_gates
        assert "step.install" not in machine.state("merged").entry_gates
        assert "step.install" not in machine.state("pushed").entry_gates

    def test_rules_list_shows_a_step_with_its_provenance(self, tmp_path, capsys, monkeypatch):
        root = new_project(tmp_path, {"steps": {"lint": step(
            "session.before_merge", "true", summary="lint before landing")}})
        monkeypatch.chdir(root)
        code, payload = run_json(capsys, "rules", "list")
        assert code == 0
        row = next(r for r in payload["rules"] if r["id"] == "step.lint")
        assert row["level"] == "block"
        assert row["summary"] == "lint before landing"
        assert row["provenance"] == "repo:.claude/invariant.yaml"
        assert all("provenance" not in r for r in payload["rules"] if r["id"] != "step.lint")


# -- the runner ------------------------------------------------------------------


class TestTheRunner:
    def test_run_command_takes_a_timeout_and_an_environment(self, tmp_path):
        proc = run_command("echo $STEP_X", tmp_path, env={"STEP_X": "seen"}, timeout=30)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "seen"

    def test_a_timeout_kills_the_whole_group(self, tmp_path):
        began = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            run_command("sleep 30 & sleep 30", tmp_path, timeout=1)
        assert time.monotonic() - began < 10

    def test_a_pass_is_recorded_at_the_tree_it_judged(self, tmp_path, flags):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step("session.before_merge", "true")}})
        record = StepsDomain(manifest).run("lint", session=None, cwd=root)
        assert record["outcome"] == "pass"
        assert record["exit_code"] == 0
        assert record["commit"] == git(root, "rev-parse", "HEAD")
        assert record["command"] == "true"
        assert record["at"] == "session.before_merge"
        assert record["tree"] == gitq.tree_signature(root)[1]

    def test_the_step_receives_its_environment_and_not_the_session_variable(
        self, tmp_path, monkeypatch
    ):
        root = new_project(tmp_path)
        out = tmp_path / "env.txt"
        manifest = load(root, {"steps": {"show": step(
            "session.before_merge",
            f"env | grep -E '^INVARIANT_(STEP|WORKTREE|SESSION_ID)' | sort > {out}")}})
        monkeypatch.setenv("INVARIANT_SESSION_ID", "leaked")
        StepsDomain(manifest).run(
            "show", session={"id": SID, "worktree": str(root)}, cwd=root
        )
        seen = out.read_text().splitlines()
        assert "INVARIANT_STEP=show" in seen
        assert f"INVARIANT_STEP_SESSION={SID}" in seen
        assert f"INVARIANT_WORKTREE={root}" in seen
        assert not [line for line in seen if line.startswith("INVARIANT_SESSION_ID")]

    def test_a_timeout_fails_and_is_never_owed(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"hang": step(
            "session.before_merge", "sleep 30", timeout=1, owed_exit=75)}})
        record = StepsDomain(manifest).run("hang", session=None, cwd=root)
        assert record["outcome"] == "fail"
        assert record["exit_code"] is None
        assert "timed out after 1" in record["tail"]
        assert steps_reader.open_debts(JsonStore(manifest.state_root)) == []

    def test_a_missing_command_fails(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"gone": step(
            "session.before_merge", "no-such-command-anywhere", owed_exit=75)}})
        record = StepsDomain(manifest).run("gone", session=None, cwd=root)
        assert record["outcome"] == "fail"
        assert record["exit_code"] == 127

    def test_a_tree_that_does_not_exist_fails_the_run(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"tidy": step("session.before_teardown", "true")}})
        record = StepsDomain(manifest).run(
            "tidy", session={"id": SID, "worktree": str(tmp_path / "gone")}, cwd=root
        )
        assert record["outcome"] == "fail"
        assert "does not exist" in record["tail"]

    def test_a_discovery_that_finds_nothing_fails_the_run(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {
            "discover": {"device": "exit 1"},
            "steps": {"install": step("session.before_merge", "echo {device}", owed_exit=75)},
        })
        record = StepsDomain(manifest).run("install", session=None, cwd=root)
        assert record["outcome"] == "fail"
        assert record["command"] == "echo {device}"
        assert "discovery for 'device'" in record["tail"]

    def test_discovery_fills_the_command_at_run_time(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {
            "discover": {"device": "echo phone-2"},
            "steps": {"install": step("session.before_merge", "echo {device}")},
        })
        record = StepsDomain(manifest).run("install", session=None, cwd=root)
        assert record["outcome"] == "pass"
        assert record["command"] == "echo phone-2"
        assert record["tail"] == "phone-2"

    def test_the_record_keeps_only_the_latest_runs(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step("session.before_merge", "true")}})
        domain = StepsDomain(manifest)
        for _ in range(steps_reader.RUNS_KEPT + 3):
            domain.run("lint", session=None, cwd=root)
        runs = steps_reader.runs(JsonStore(manifest.state_root), "lint")
        assert len(runs) == steps_reader.RUNS_KEPT


# -- D3: the rule reads the record ---------------------------------------------------


def judge(manifest: Manifest, root: Path, rule_id: str, session=None):
    ctx = RuleContext(manifest=manifest, cwd=root, session=session)
    return evaluate([rule_id], ctx)[0]


class TestTheRuleReadsTheRecord:
    def test_no_run_at_this_tree_fails_naming_step_run(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step("session.before_merge", "true")}})
        verdict = judge(manifest, root, "step.lint")
        assert verdict.blocks
        assert "no run" in verdict.verdict.message
        assert "invariant step run lint" in " ".join(verdict.verdict.fixes)

    def test_a_pass_here_passes_and_an_edit_after_it_does_not_count(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step("session.before_merge", "true")}})
        StepsDomain(manifest).run("lint", session=None, cwd=root)
        assert judge(manifest, root, "step.lint").verdict.ok
        (root / "app.py").write_text("x = 2\n", encoding="utf-8")
        assert judge(manifest, root, "step.lint").blocks
        git(root, "commit", "-qam", "edit")
        assert judge(manifest, root, "step.lint").blocks

    def test_an_untracked_file_is_part_of_the_tree(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step("session.before_merge", "true")}})
        StepsDomain(manifest).run("lint", session=None, cwd=root)
        (root / "new.py").write_text("y = 1\n", encoding="utf-8")
        assert judge(manifest, root, "step.lint").blocks

    def test_a_step_that_writes_its_own_output_still_counts(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"build": step(
            "session.before_merge", "echo built > artefact.txt")}})
        StepsDomain(manifest).run("build", session=None, cwd=root)
        assert judge(manifest, root, "step.build").verdict.ok

    def test_a_failed_run_fails_with_its_tail(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step(
            "session.before_merge", "echo 'lint: 3 problems'; exit 2")}})
        StepsDomain(manifest).run("lint", session=None, cwd=root)
        verdict = judge(manifest, root, "step.lint")
        assert verdict.blocks
        assert "lint: 3 problems" in verdict.verdict.message
        assert "exit 2" in verdict.verdict.message

    def test_a_warn_step_failing_does_not_block(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"lint": step(
            "session.before_merge", "exit 1", level="warn")}})
        StepsDomain(manifest).run("lint", session=None, cwd=root)
        verdict = judge(manifest, root, "step.lint")
        assert verdict.warns and not verdict.blocks

    def test_the_session_worktree_is_the_tree_judged(self, tmp_path):
        root = new_project(tmp_path)
        other = tmp_path / "elsewhere"
        git(root, "worktree", "add", "-q", "-b", "side", str(other))
        manifest = load(root, {"steps": {"lint": step("session.before_merge", "true")}})
        session = {"id": SID, "worktree": str(other)}
        StepsDomain(manifest).run("lint", session=session, cwd=root)
        assert judge(manifest, root, "step.lint", session=session).verdict.ok
        (other / "app.py").write_text("x = 3\n", encoding="utf-8")
        assert judge(manifest, root, "step.lint", session=session).blocks

    def test_a_handoff_step_judges_the_main_checkout(self, tmp_path):
        root = new_project(tmp_path)
        other = tmp_path / "elsewhere"
        git(root, "worktree", "add", "-q", "-b", "side", str(other))
        manifest = load(root, {"steps": {"note": step("session.before_handoff", "true")}})
        session = {"id": SID, "worktree": str(other)}
        record = StepsDomain(manifest).run("note", session=session, cwd=other)
        assert Path(record["cwd"]).resolve() == root.resolve()
        (other / "app.py").write_text("x = 3\n", encoding="utf-8")
        assert judge(manifest, other, "step.note", session=session).verdict.ok

    def test_check_judges_a_step(self, tmp_path, capsys, monkeypatch):
        root = new_project(tmp_path, {"steps": {"lint": step("session.before_merge", "true")}})
        monkeypatch.chdir(root)
        code, text = run(capsys, "check", "step.lint")
        assert code == 1
        assert "step.lint" in text


# -- D4: owed is a debt ------------------------------------------------------------------


class TestOwed:
    def test_an_owed_exit_passes_and_records_a_debt(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"install": step(
            "session.before_merge", "echo 'no phone attached'; exit 75", owed_exit=75)}})
        record = StepsDomain(manifest).run(
            "install", session={"id": SID, "worktree": str(root)}, cwd=root
        )
        assert record["outcome"] == "owed"
        verdict = judge(manifest, root, "step.install")
        assert verdict.verdict.ok and not verdict.verdict.skipped
        assert "owed" in verdict.verdict.message
        debts = steps_reader.open_debts(JsonStore(manifest.state_root))
        assert debts == [{
            "step": "install", "session": SID,
            "commit": git(root, "rev-parse", "HEAD"),
            "line": "no phone attached", "at": debts[0]["at"],
        }]

    def test_an_exit_other_than_owed_exit_is_a_fail(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"install": step(
            "session.before_merge", "exit 3", owed_exit=75)}})
        assert StepsDomain(manifest).run("install", session=None, cwd=root)["outcome"] == "fail"

    def test_with_no_owed_exit_nothing_is_owed(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"install": step("session.before_merge", "exit 75")}})
        assert StepsDomain(manifest).run("install", session=None, cwd=root)["outcome"] == "fail"

    def test_one_pass_clears_every_debt_of_its_step_and_no_other(self, tmp_path, flags):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {
            "install": step("session.before_merge",
                            f"test -f {flags / 'phone'} || exit 75", owed_exit=75),
            "notify": step("session.before_merge", "exit 75", owed_exit=75),
        }})
        domain = StepsDomain(manifest)
        store = JsonStore(manifest.state_root)
        domain.run("install", session=None, cwd=root)
        domain.run("install", session=None, cwd=root)
        domain.run("notify", session=None, cwd=root)
        assert len(steps_reader.open_debts(store, "install")) == 2
        (flags / "phone").write_text("", encoding="utf-8")
        record = domain.run("install", session=None, cwd=root)
        assert record["outcome"] == "pass"
        assert record["cleared"] == 2
        assert steps_reader.open_debts(store, "install") == []
        assert len(steps_reader.open_debts(store, "notify")) == 1

    def test_a_failed_run_leaves_the_debts(self, tmp_path):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"install": step(
            "session.before_merge", "exit 75", owed_exit=75)}})
        domain = StepsDomain(manifest)
        domain.run("install", session=None, cwd=root)
        manifest = load(root, {"steps": {"install": step(
            "session.before_merge", "exit 9", owed_exit=75)}})
        StepsDomain(manifest).run("install", session=None, cwd=root)
        assert len(steps_reader.open_debts(JsonStore(manifest.state_root))) == 1


# -- the lifecycle commands run their steps -------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A project whose manifest the test writes, before the first commit."""

    def make(steps: dict, **extra) -> Path:
        root = new_project(tmp_path, {"steps": steps, **extra})
        monkeypatch.chdir(root)
        return root

    return make


def isolated(capsys, root: Path, monkeypatch) -> Path:
    code, text = run(capsys, "session", "start", "work", "--id", SID)
    assert code == 0, text
    code, text = run(capsys, "session", "isolate", "--id", SID, "--execute")
    assert code == 0, text
    return root.parent / f"widgets-wt-{SID}"


def reviewed(capsys, root: Path, monkeypatch) -> Path:
    worktree = isolated(capsys, root, monkeypatch)
    (worktree / "app.py").write_text("x = 2\n", encoding="utf-8")
    git(worktree, "add", "-A")
    git(worktree, "commit", "-qm", "the code")
    for state in ("working", "reviewed"):
        code, text = run(
            capsys, "session", "transition", state, "--id", SID,
            "--force", "--reason", "test fixture",
        )
        assert code == 0, text
    return worktree


class TestPipelineEnter:
    def chore(self, capsys, project, monkeypatch, steps) -> Path:
        root = project(steps, methodology={"pipeline": "chore"})
        worktree = isolated(capsys, root, monkeypatch)
        monkeypatch.chdir(worktree)
        code, text = run(capsys, "pipeline", "start", "--task", "t", "--id", SID)
        assert code == 0, text
        return worktree

    def test_a_block_step_refuses_the_advance_and_passes_once_fixed(
        self, capsys, project, monkeypatch, flags
    ):
        worktree = self.chore(capsys, project, monkeypatch, {
            "lint": step("pipeline.enter.reviewing", gated(flags))})
        code, text = run(capsys, "pipeline", "advance", "reviewing", "--id", SID)
        assert code == 1, text
        assert "step.lint" in text
        runs = steps_reader.runs(JsonStore(Manifest.locate(worktree).state_root), "lint")
        assert [r["outcome"] for r in runs] == ["fail"]
        assert Path(runs[0]["cwd"]).resolve() == worktree.resolve()
        (flags / "ok").write_text("", encoding="utf-8")
        code, text = run(capsys, "pipeline", "advance", "reviewing", "--id", SID)
        assert code == 0, text

    def test_a_warn_step_does_not_refuse(self, capsys, project, monkeypatch, flags):
        self.chore(capsys, project, monkeypatch, {
            "lint": step("pipeline.enter.reviewing", gated(flags), level="warn")})
        code, text = run(capsys, "pipeline", "advance", "reviewing", "--id", SID)
        assert code == 0, text
        assert "step.lint" in text

    def test_a_warn_step_can_be_acknowledged_and_a_block_step_cannot(
        self, capsys, project, monkeypatch, flags
    ):
        self.chore(capsys, project, monkeypatch, {
            "lint": step("pipeline.enter.reviewing", gated(flags), level="warn"),
            "test": step("pipeline.enter.reviewing", "true")})
        code, text = run(capsys, "pipeline", "ack", "step.lint", "--id", SID,
                         "--reason", "lint is advisory here")
        assert code == 0, text
        code, text = run(capsys, "pipeline", "ack", "step.test", "--id", SID,
                         "--reason", "no")
        assert code == 2, text
        code, text = run(capsys, "pipeline", "advance", "reviewing", "--id", SID)
        assert code == 0, text

    def test_an_off_step_does_not_run(self, capsys, project, monkeypatch, flags):
        worktree = self.chore(capsys, project, monkeypatch, {
            "lint": step("pipeline.enter.reviewing", gated(flags), level="off")})
        code, text = run(capsys, "pipeline", "advance", "reviewing", "--id", SID)
        assert code == 0, text
        assert steps_reader.runs(JsonStore(Manifest.locate(worktree).state_root), "lint") == []

    def test_a_forced_advance_runs_nothing_and_records_the_override(
        self, capsys, project, monkeypatch, flags
    ):
        worktree = self.chore(capsys, project, monkeypatch, {
            "lint": step("pipeline.enter.reviewing", gated(flags))})
        code, text = run(capsys, "pipeline", "advance", "reviewing", "--id", SID,
                         "--force", "--reason", "device away")
        assert code == 0, text
        state_root = Manifest.locate(worktree).state_root
        assert steps_reader.runs(JsonStore(state_root), "lint") == []
        record = JsonStore(state_root).read(f"pipelines/{SID}.json")
        assert record["current"]["overrides"][-1]["gates"] == ["step.lint"]

    def test_pipeline_gates_reads_and_runs_nothing(self, capsys, project, monkeypatch, flags):
        worktree = self.chore(capsys, project, monkeypatch, {
            "lint": step("pipeline.enter.reviewing", gated(flags))})
        _code, text = run(capsys, "pipeline", "gates", "--id", SID)
        assert "step.lint" in text
        assert steps_reader.runs(JsonStore(Manifest.locate(worktree).state_root), "lint") == []

    def test_an_owed_step_lets_the_advance_through_and_records_a_debt(
        self, capsys, project, monkeypatch
    ):
        worktree = self.chore(capsys, project, monkeypatch, {
            "install": step("pipeline.enter.reviewing", "exit 75", owed_exit=75)})
        code, text = run(capsys, "pipeline", "advance", "reviewing", "--id", SID)
        assert code == 0, text
        debts = steps_reader.open_debts(JsonStore(Manifest.locate(worktree).state_root))
        assert [d["step"] for d in debts] == ["install"]


class TestSessionPoints:
    def test_a_block_step_refuses_the_merge_with_nothing_merged(
        self, capsys, project, monkeypatch, flags
    ):
        root = project({"lint": step("session.before_merge", gated(flags))})
        reviewed(capsys, root, monkeypatch)
        before = git(root, "rev-parse", "main")
        code, text = run(capsys, "session", "merge", "--id", SID)
        assert code == 1, text
        assert "step.lint" in text
        assert git(root, "rev-parse", "main") == before
        (flags / "ok").write_text("", encoding="utf-8")
        code, text = run(capsys, "session", "merge", "--id", SID)
        assert code == 0, text
        assert git(root, "rev-parse", "main") != before

    def test_a_warn_step_does_not_refuse_the_merge(self, capsys, project, monkeypatch, flags):
        root = project({"lint": step("session.before_merge", gated(flags), level="warn")})
        reviewed(capsys, root, monkeypatch)
        code, text = run(capsys, "session", "merge", "--id", SID)
        assert code == 0, text

    def test_a_forced_merge_runs_nothing(self, capsys, project, monkeypatch, flags):
        root = project({"lint": step("session.before_merge", gated(flags))})
        reviewed(capsys, root, monkeypatch)
        code, text = run(capsys, "session", "merge", "--id", SID, "--force")
        assert code == 0, text
        assert steps_reader.runs(JsonStore(Manifest.locate(root).state_root), "lint") == []

    def test_a_transition_into_merged_runs_the_step_too(
        self, capsys, project, monkeypatch, flags
    ):
        root = project({"lint": step("session.before_merge", gated(flags))})
        reviewed(capsys, root, monkeypatch)
        code, text = run(capsys, "session", "transition", "merged", "--id", SID)
        assert code == 1, text
        runs = steps_reader.runs(JsonStore(Manifest.locate(root).state_root), "lint")
        assert [r["outcome"] for r in runs] == ["fail"]

    def test_a_block_step_refuses_teardown_and_removes_nothing(
        self, capsys, project, monkeypatch, flags
    ):
        root = project({"tidy": step("session.before_teardown", gated(flags))})
        worktree = reviewed(capsys, root, monkeypatch)
        code, text = run(capsys, "session", "merge", "--id", SID)
        assert code == 0, text
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 1, text
        assert "step.tidy" in text
        assert worktree.is_dir()
        runs = steps_reader.runs(JsonStore(Manifest.locate(root).state_root), "tidy")
        assert Path(runs[-1]["cwd"]).resolve() == worktree.resolve()
        (flags / "ok").write_text("", encoding="utf-8")
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 0, text
        assert not worktree.exists()

    def test_teardown_with_no_steps_is_unchanged(self, capsys, project, monkeypatch):
        root = project({})
        worktree = reviewed(capsys, root, monkeypatch)
        assert run(capsys, "session", "merge", "--id", SID)[0] == 0
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 0, text
        assert not worktree.exists()


@pytest.fixture
def can_launch(monkeypatch, tmp_path, _no_real_successor_watch):
    """A host that can launch a successor, and a recorder for what it launched."""
    plans = []

    class Recorder:
        def launch(self, plan) -> None:
            plans.append(plan)

    monkeypatch.setattr("invariant.cli.main._handoff_launcher", lambda: Recorder())
    monkeypatch.setattr(launch, "launch_dir", lambda: tmp_path / "launch")
    monkeypatch.setattr(launch, "host_platform", lambda: "darwin")
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(launch, "resolve_claude", lambda: str(fake))
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    _no_real_successor_watch.pid = 31337
    return plans


class TestHandoff:
    def test_a_block_step_refuses_the_handoff_and_records_no_note(
        self, capsys, project, monkeypatch, flags, can_launch
    ):
        root = project({"note": step("session.before_handoff", gated(flags))})
        code, text = run(capsys, "session", "start", "work", "--id", SID)
        assert code == 0, text
        code, text = run(capsys, "session", "handoff", "--note", "next: ship it",
                         "--id", SID, "--keep-open")
        assert code == 1, text
        assert "step.note" in text
        assert can_launch == []
        session = JsonStore(Manifest.locate(root).state_root).read(f"sessions/{SID}.json")
        assert not session.get("handoffs")
        (flags / "ok").write_text("", encoding="utf-8")
        code, text = run(capsys, "session", "handoff", "--note", "next: ship it",
                         "--id", SID, "--keep-open")
        assert code == 0, text
        assert len(can_launch) == 1

    def test_a_dry_run_runs_no_step(self, capsys, project, monkeypatch, flags):
        root = project({"note": step("session.before_handoff", gated(flags))})
        run(capsys, "session", "start", "work", "--id", SID)
        code, text = run(capsys, "session", "handoff", "--note", "next: ship it",
                         "--id", SID, "--dry-run")
        assert code == 0, text
        assert steps_reader.runs(JsonStore(Manifest.locate(root).state_root), "note") == []

    def test_the_open_debts_ride_the_note(
        self, capsys, project, monkeypatch, can_launch
    ):
        root = project({"install": step(
            "session.before_merge", "echo away; exit 75", owed_exit=75)})
        run(capsys, "session", "start", "work", "--id", SID)
        StepsDomain(Manifest.locate(root)).run("install", session=None, cwd=root)
        code, payload = run_json(capsys, "session", "handoff", "--note", "next: ship it",
                                 "--id", SID, "--keep-open")
        assert code == 0, payload
        sha = git(root, "rev-parse", "--short=7", "HEAD")
        assert payload["note"] == (
            f"next: ship it · owed steps: install (1, since {sha})"
        )
        recorded = JsonStore(Manifest.locate(root).state_root).read(f"sessions/{SID}.json")
        assert recorded["handoffs"][-1]["note"] == payload["note"]

    def test_with_no_debts_the_note_is_as_given(
        self, capsys, project, monkeypatch, can_launch
    ):
        project({})
        run(capsys, "session", "start", "work", "--id", SID)
        code, payload = run_json(capsys, "session", "handoff", "--note", "next: ship it",
                                 "--id", SID, "--keep-open")
        assert code == 0, payload
        assert payload["note"] == "next: ship it"


# -- step run, step list, and the debts shown --------------------------------------


class TestTheStepCommands:
    def test_step_run_records_and_says_how_it_went(self, capsys, project, monkeypatch, flags):
        root = project({"lint": step("session.before_merge", gated(flags))})
        code, text = run(capsys, "step", "run", "lint")
        assert code == 1, text
        assert "lint" in text and "fail" in text
        (flags / "ok").write_text("", encoding="utf-8")
        code, payload = run_json(capsys, "step", "run", "lint")
        assert code == 0, payload
        assert payload["run"]["outcome"] == "pass"
        assert len(steps_reader.runs(JsonStore(Manifest.locate(root).state_root), "lint")) == 2

    def test_step_run_of_an_owed_step_exits_clean(self, capsys, project, monkeypatch):
        project({"install": step("session.before_merge", "exit 75", owed_exit=75)})
        code, text = run(capsys, "step", "run", "install")
        assert code == 0, text
        assert "owed" in text

    def test_step_run_refuses_a_name_not_declared(self, capsys, project, monkeypatch):
        project({"lint": step("session.before_merge", "true")})
        code, text = run(capsys, "step", "run", "lnt")
        assert code == 2, text
        assert "lint" in text

    def test_step_run_uses_the_sessions_worktree(self, capsys, project, monkeypatch):
        root = project({"lint": step("session.before_merge", "true")})
        worktree = isolated(capsys, root, monkeypatch)
        code, payload = run_json(capsys, "step", "run", "lint", "--id", SID)
        assert code == 0, payload
        assert Path(payload["run"]["cwd"]).resolve() == worktree.resolve()
        assert payload["run"]["session"] == SID

    def test_step_list_shows_point_level_last_outcome_and_debts(
        self, capsys, project, monkeypatch
    ):
        project({
            "install": step("session.before_merge", "exit 75", owed_exit=75),
            "lint": step("pipeline.enter.reviewing", "true", level="warn"),
        })
        run(capsys, "step", "run", "install")
        code, payload = run_json(capsys, "step", "list")
        assert code == 0
        rows = {row["name"]: row for row in payload["steps"]}
        assert rows["install"]["at"] == "session.before_merge"
        assert rows["install"]["level"] == "block"
        assert rows["install"]["last"]["outcome"] == "owed"
        assert rows["install"]["debts"] == 1
        assert rows["lint"]["last"] is None
        code, text = run(capsys, "step", "list")
        assert "install" in text and "owed" in text and "never run" in text

    def test_step_list_with_no_steps_says_so(self, capsys, project, monkeypatch):
        project({})
        code, text = run(capsys, "step", "list")
        assert code == 0
        assert "no steps" in text

    def test_session_status_shows_the_projects_debts(self, capsys, project, monkeypatch):
        root = project({"install": step("session.before_merge", "exit 75", owed_exit=75)})
        run(capsys, "session", "start", "work", "--id", SID)
        code, text = run(capsys, "session", "status", "--id", SID)
        assert "owed" not in text
        StepsDomain(Manifest.locate(root)).run("install", session=None, cwd=root)
        code, text = run(capsys, "session", "status", "--id", SID)
        assert code == 0
        assert "owed" in text and "install" in text
        assert "invariant step run install" in text


class TestTheOracleShowsDebts:
    def test_a_debt_rides_each_live_session_row_and_none_is_absent(
        self, capsys, project, monkeypatch
    ):
        root = project({"install": step("session.before_merge", "exit 75", owed_exit=75)})
        run(capsys, "session", "start", "work", "--id", SID)
        manifest = Manifest.locate(root)
        entry, _ = ProjectIndex(oracle_home()).register(manifest, via="manual")
        row = scan_project(entry).rows[0].to_dict()
        assert "step_debts" not in row
        StepsDomain(manifest).run("install", session=None, cwd=root)
        row = scan_project(entry).rows[0].to_dict()
        assert row["step_debts"] == [{
            "step": "install", "count": 1,
            "since": git(root, "rev-parse", "HEAD"),
        }]

    def test_the_panel_names_the_debt_under_technical_details(self):
        from invariant.oracle.panel import _technical

        lines = _technical({
            "session_id": SID, "state": "working",
            "step_debts": [{"step": "install", "count": 2, "since": "a" * 40}],
        })["lines"]
        assert any("install" in line and "2" in line and "aaaaaaa" in line
                   for line in lines)


# -- increment 2: after the merge (ruling 2, D5) ---------------------------------


def owed_or_gated(flags: Path) -> str:
    """Passes once ``ok`` exists, exits owed (75) once ``away`` does, and
    fails before either."""
    return (
        f"if test -f {flags / 'ok'}; then exit 0; "
        f"elif test -f {flags / 'away'}; then echo phone away; exit 75; "
        f"else echo no phone; exit 1; fi"
    )


class TestHoldingRun:
    """``core/steps.holding_run``: which recorded run decides the hold."""

    MERGED = "m" * 40

    @staticmethod
    def later(commit: str) -> bool:
        return commit in (TestHoldingRun.MERGED, "d" * 40)

    def decide(self, *runs):
        found = [{"commit": c, "outcome": o} for c, o in runs]
        return steps_reader.holding_run(found, self.MERGED, self.later)

    def test_no_run_decides_nothing(self):
        assert self.decide() is None

    def test_a_fail_at_the_merged_commit_holds(self):
        assert self.decide((self.MERGED, "fail"))["outcome"] == "fail"

    def test_a_later_pass_or_owed_at_a_commit_containing_it_releases(self):
        for outcome in ("pass", "owed"):
            assert self.decide(
                (self.MERGED, "fail"), ("d" * 40, outcome))["outcome"] == outcome

    def test_a_later_fail_at_a_commit_containing_it_holds(self):
        """Fail closed (the parent's ruling, 2026-11-15): the newest run at
        or after the merged commit decides, a fail there included."""
        assert self.decide((self.MERGED, "pass"), ("d" * 40, "fail"))["outcome"] == "fail"
        assert self.decide(("d" * 40, "fail"))["outcome"] == "fail"

    def test_a_run_at_a_commit_not_containing_the_merge_is_not_read(self):
        assert self.decide((self.MERGED, "fail"), ("x" * 40, "pass"))["outcome"] == "fail"

    def test_a_run_with_no_commit_is_not_read(self):
        found = [{"commit": self.MERGED, "outcome": "fail"}, {"commit": None, "outcome": "pass"}]
        assert steps_reader.holding_run(found, self.MERGED, self.later)["outcome"] == "fail"


class TestTheAfterMergeRule:
    """The rule an after-merge step is, judged at the session's merged commit."""

    def setup(self, tmp_path, flags, **more):
        root = new_project(tmp_path)
        manifest = load(root, {"steps": {"install": step(
            "session.after_merge", owed_or_gated(flags), **more)}})
        git(root, "add", "-A")
        git(root, "commit", "-qm", "the step")
        git(root, "branch", "work")
        session = {"id": SID, "state": "merged", "branch": "work"}
        return root, manifest, session

    def advance_main(self, root: Path) -> None:
        (root / "app.py").write_text("x = 3\n", encoding="utf-8")
        git(root, "commit", "-qam", "another merge")

    def test_a_session_that_has_not_merged_is_never_held(self, tmp_path, flags):
        root, manifest, session = self.setup(tmp_path, flags)
        StepsDomain(manifest).run("install", session=None, cwd=root)
        for state in ("working", "reviewed"):
            verdict = judge(manifest, root, "step.install", {**session, "state": state})
            assert not verdict.blocks
            assert "has not merged" in verdict.verdict.message

    def test_a_fail_at_the_merged_commit_holds_naming_the_fix(self, tmp_path, flags):
        root, manifest, session = self.setup(tmp_path, flags)
        StepsDomain(manifest).run("install", session=session, cwd=root)
        verdict = judge(manifest, root, "step.install", session)
        assert verdict.blocks
        assert "no phone" in verdict.verdict.message
        assert "invariant step run install" in " ".join(verdict.verdict.fixes)
        verdict = judge(manifest, root, "step.install", {**session, "state": "pushed"})
        assert verdict.blocks

    def test_no_run_since_the_merge_holds(self, tmp_path, flags):
        """Fail closed: a merge that ran no step (forced, a transition, or
        before the step was declared) is held until one runs."""
        root, manifest, session = self.setup(tmp_path, flags)
        verdict = judge(manifest, root, "step.install", session)
        assert verdict.blocks
        assert "no run" in verdict.verdict.message
        assert "invariant step run install" in " ".join(verdict.verdict.fixes)

    def test_a_fail_at_a_later_commit_holds(self, tmp_path, flags):
        """The base moved past the branch tip (an already-contained landing):
        the run's commit contains the merged one, and its fail holds."""
        root, manifest, session = self.setup(tmp_path, flags)
        self.advance_main(root)
        StepsDomain(manifest).run("install", session=session, cwd=root)
        assert judge(manifest, root, "step.install", session).blocks

    def test_a_branch_git_cannot_read_holds_nothing(self, tmp_path, flags):
        root, manifest, session = self.setup(tmp_path, flags)
        StepsDomain(manifest).run("install", session=session, cwd=root)
        verdict = judge(manifest, root, "step.install", {**session, "branch": "gone"})
        assert not verdict.blocks
        verdict = judge(manifest, root, "step.install", {**session, "branch": None})
        assert not verdict.blocks

    def test_a_pass_at_a_later_commit_releases_it(self, tmp_path, flags):
        root, manifest, session = self.setup(tmp_path, flags)
        StepsDomain(manifest).run("install", session=session, cwd=root)
        self.advance_main(root)
        (flags / "ok").write_text("", encoding="utf-8")
        StepsDomain(manifest).run("install", session=None, cwd=root)
        verdict = judge(manifest, root, "step.install", session)
        assert not verdict.blocks
        assert "passed" in verdict.verdict.message

    def test_an_owed_run_releases_it_and_names_the_debt(self, tmp_path, flags):
        root, manifest, session = self.setup(tmp_path, flags, owed_exit=75)
        StepsDomain(manifest).run("install", session=session, cwd=root)
        (flags / "away").write_text("", encoding="utf-8")
        StepsDomain(manifest).run("install", session=session, cwd=root)
        verdict = judge(manifest, root, "step.install", session)
        assert not verdict.blocks
        assert "owed" in verdict.verdict.message and "phone away" in verdict.verdict.message

    def test_a_later_failure_holds_a_session_whose_own_run_passed(self, tmp_path, flags):
        root, manifest, session = self.setup(tmp_path, flags)
        (flags / "ok").write_text("", encoding="utf-8")
        StepsDomain(manifest).run("install", session=session, cwd=root)
        self.advance_main(root)
        (flags / "ok").unlink()
        StepsDomain(manifest).run("install", session=None, cwd=root)
        assert judge(manifest, root, "step.install", session).blocks

    def test_a_run_at_a_commit_not_containing_the_merge_does_not_release(
        self, tmp_path, flags
    ):
        root, manifest, session = self.setup(tmp_path, flags)
        git(root, "switch", "-qc", "side", "HEAD~1")
        (flags / "ok").write_text("", encoding="utf-8")
        StepsDomain(manifest).run("install", session=None, cwd=root)
        git(root, "switch", "-q", "main")
        assert judge(manifest, root, "step.install", session).blocks

    def test_an_uncommitted_edit_in_the_main_checkout_is_not_read(self, tmp_path, flags):
        root, manifest, session = self.setup(tmp_path, flags)
        StepsDomain(manifest).run("install", session=session, cwd=root)
        (root / "scratch.txt").write_text("dirt\n", encoding="utf-8")
        assert judge(manifest, root, "step.install", session).blocks

    def test_a_warn_step_failing_holds_nothing(self, tmp_path, flags):
        root, manifest, session = self.setup(tmp_path, flags, level="warn")
        StepsDomain(manifest).run("install", session=session, cwd=root)
        verdict = judge(manifest, root, "step.install", session)
        assert not verdict.blocks
        assert not verdict.verdict.ok


class TestAfterMerge:
    def merged(self, capsys, project, monkeypatch, flags, **more):
        root = project({"install": step("session.after_merge", owed_or_gated(flags), **more)})
        worktree = reviewed(capsys, root, monkeypatch)
        before = git(root, "rev-parse", "main")
        code, payload = run_json(capsys, "session", "merge", "--id", SID)
        assert code == 0, payload
        assert git(root, "rev-parse", "main") != before
        return root, worktree, payload

    def runs(self, root: Path) -> list[dict]:
        return steps_reader.runs(JsonStore(Manifest.locate(root).state_root), "install")

    def test_the_merge_stands_with_a_failing_step(self, capsys, project, monkeypatch, flags):
        root, _worktree, payload = self.merged(capsys, project, monkeypatch, flags)
        session = JsonStore(Manifest.locate(root).state_root).read(f"sessions/{SID}.json")
        assert session["state"] == "merged"
        runs = self.runs(root)
        assert [r["outcome"] for r in runs] == ["fail"]
        assert runs[0]["commit"] == git(root, "rev-parse", "main")
        assert Path(runs[0]["cwd"]).resolve() == root.resolve()
        assert [r["outcome"] for r in payload["after_merge"]] == ["fail"]

    def test_the_merge_says_what_it_holds(self, capsys, project, monkeypatch, flags):
        root = project({"install": step("session.after_merge", owed_or_gated(flags))})
        reviewed(capsys, root, monkeypatch)
        code, text = run(capsys, "session", "merge", "--id", SID)
        assert code == 0, text
        assert "the merge stands" in text
        assert "invariant step run install" in text
        assert "teardown and handoff" in text

    def test_the_step_runs_in_the_main_checkout_with_the_merged_sha(
        self, capsys, project, monkeypatch, flags
    ):
        root = project({"install": step(
            "session.after_merge",
            f'echo "$INVARIANT_MERGED_SHA $INVARIANT_STEP $PWD" > {flags / "env"}')})
        reviewed(capsys, root, monkeypatch)
        code, text = run(capsys, "session", "merge", "--id", SID)
        assert code == 0, text
        sha, name, cwd = (flags / "env").read_text(encoding="utf-8").split()
        assert sha == git(root, "rev-parse", "main")
        assert name == "install"
        assert Path(cwd).resolve() == root.resolve()
        assert "install: pass" in text

    def test_teardown_is_refused_and_removes_nothing(self, capsys, project, monkeypatch, flags):
        root, worktree, _ = self.merged(capsys, project, monkeypatch, flags)
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 1, text
        assert "step.install" in text and "nothing was removed" in text
        assert worktree.is_dir()
        branch = JsonStore(Manifest.locate(root).state_root).read(
            f"sessions/{SID}.json")["branch"]
        assert git(root, "branch", "--list", branch)
        assert [r["outcome"] for r in self.runs(root)] == ["fail"], "teardown re-ran it"

    def test_a_transition_into_torn_down_is_held_too(self, capsys, project, monkeypatch, flags):
        self.merged(capsys, project, monkeypatch, flags)
        code, text = run(capsys, "session", "transition", "torn_down", "--id", SID)
        assert code == 1, text
        assert "step.install" in text

    def test_handoff_is_refused(self, capsys, project, monkeypatch, flags, can_launch):
        root, _worktree, _ = self.merged(capsys, project, monkeypatch, flags)
        code, text = run(capsys, "session", "handoff", "--note", "next: ship it",
                         "--id", SID, "--keep-open")
        assert code == 1, text
        assert "step.install" in text
        assert can_launch == []
        session = JsonStore(Manifest.locate(root).state_root).read(f"sessions/{SID}.json")
        assert not session.get("handoffs")
        assert len(self.runs(root)) == 1, "handoff re-ran it"

    def test_step_run_releases_them(self, capsys, project, monkeypatch, flags, can_launch):
        root, worktree, _ = self.merged(capsys, project, monkeypatch, flags)
        (flags / "ok").write_text("", encoding="utf-8")
        code, payload = run_json(capsys, "step", "run", "install", "--id", SID)
        assert code == 0, payload
        assert Path(payload["run"]["cwd"]).resolve() == root.resolve()
        code, text = run(capsys, "session", "handoff", "--note", "next: ship it",
                         "--id", SID, "--keep-open")
        assert code == 0, text
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 0, text
        assert not worktree.exists()

    def test_an_owed_run_releases_them(self, capsys, project, monkeypatch, flags, can_launch):
        root, worktree, _ = self.merged(capsys, project, monkeypatch, flags, owed_exit=75)
        (flags / "away").write_text("", encoding="utf-8")
        code, text = run(capsys, "step", "run", "install")
        assert code == 0, text
        assert "owed" in text
        code, payload = run_json(capsys, "session", "handoff", "--note", "next: ship it",
                                 "--id", SID, "--keep-open")
        assert code == 0, payload
        assert "owed steps: install (1" in payload["note"]
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 0, text
        assert not worktree.exists()
        debts = steps_reader.open_debts(JsonStore(Manifest.locate(root).state_root))
        assert [d["step"] for d in debts] == ["install"]

    def test_a_warn_step_failing_holds_nothing(self, capsys, project, monkeypatch, flags):
        _root, worktree, _ = self.merged(capsys, project, monkeypatch, flags, level="warn")
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 0, text
        assert not worktree.exists()

    def test_an_off_step_does_not_run(self, capsys, project, monkeypatch, flags):
        root, _worktree, payload = self.merged(capsys, project, monkeypatch, flags, level="off")
        assert self.runs(root) == []
        assert payload["after_merge"] == []

    def test_a_forced_merge_runs_no_step_and_is_held(self, capsys, project, monkeypatch, flags):
        root = project({"install": step("session.after_merge", owed_or_gated(flags))})
        worktree = reviewed(capsys, root, monkeypatch)
        code, text = run(capsys, "session", "merge", "--id", SID, "--force")
        assert code == 0, text
        assert "forced: step install did not run" in text
        assert self.runs(root) == []
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 1, text
        assert "step.install" in text
        assert worktree.is_dir()
        (flags / "ok").write_text("", encoding="utf-8")
        assert run(capsys, "step", "run", "install")[0] == 0
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 0, text

    def test_a_transition_into_merged_after_a_manual_merge_is_held(
        self, capsys, project, monkeypatch, flags
    ):
        root = project({"install": step("session.after_merge", owed_or_gated(flags))})
        worktree = reviewed(capsys, root, monkeypatch)
        branch = JsonStore(Manifest.locate(root).state_root).read(
            f"sessions/{SID}.json")["branch"]
        git(root, "merge", "-q", "--ff-only", branch)
        code, text = run(capsys, "session", "transition", "merged", "--id", SID)
        assert code == 0, text
        assert self.runs(root) == []
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 1, text
        assert "step.install" in text and worktree.is_dir()

    def test_an_already_contained_landing_with_the_base_moved_holds_on_a_fail(
        self, capsys, project, monkeypatch, flags
    ):
        """The branch is in the base and the base has moved past it: the
        landing records "already merged", the step runs at the base's newer
        commit, and its fail holds."""
        from invariant.cli import main as cli

        root = project({"install": step("session.after_merge", owed_or_gated(flags))})
        worktree = reviewed(capsys, root, monkeypatch)
        state = Manifest.locate(root).state_root
        branch = JsonStore(state).read(f"sessions/{SID}.json")["branch"]
        git(root, "merge", "-q", "--ff-only", branch)
        (root / "later.txt").write_text("later\n", encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "the base moves on")
        record = JsonStore(state).read(f"sessions/{SID}.json")
        record["landing"] = {"failed": "an earlier merge failed"}
        JsonStore(state).write(f"sessions/{SID}.json", record)
        monkeypatch.setattr(cli, "_check_store_landing", lambda *a, **k: None)
        code, text = run(capsys, "session", "merge", "--id", SID)
        assert code == 0, text
        assert "already merged" in text
        runs = self.runs(root)
        assert [r["outcome"] for r in runs] == ["fail"]
        assert runs[0]["commit"] == git(root, "rev-parse", "main")
        assert runs[0]["commit"] != git(root, "rev-parse", branch)
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 1, text
        assert "step.install" in text and worktree.is_dir()

    def test_a_merge_from_before_the_step_was_declared_is_released_by_one_run(
        self, capsys, project, monkeypatch, flags
    ):
        root = project({})
        worktree = reviewed(capsys, root, monkeypatch)
        assert run(capsys, "session", "merge", "--id", SID)[0] == 0
        write_manifest(root, {"steps": {
            "install": step("session.after_merge", owed_or_gated(flags))}})
        git(root, "commit", "-qam", "declare the step")
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 1, text
        assert "step.install" in text and worktree.is_dir()
        (flags / "ok").write_text("", encoding="utf-8")
        assert run(capsys, "step", "run", "install")[0] == 0
        code, text = run(capsys, "session", "teardown", "--id", SID)
        assert code == 0, text
        assert not worktree.exists()

    def test_a_forced_transition_steps_past_the_hold_and_records_it(
        self, capsys, project, monkeypatch, flags
    ):
        root, _worktree, _ = self.merged(capsys, project, monkeypatch, flags)
        code, text = run(capsys, "session", "transition", "torn_down", "--id", SID,
                         "--force", "--reason", "phone lost")
        assert code == 0, text
        session = JsonStore(Manifest.locate(root).state_root).read(f"sessions/{SID}.json")
        assert session["overrides"][-1]["gates"] == ["step.install"]

    def test_abandoning_a_merged_session_is_not_held(self, capsys, project, monkeypatch, flags):
        self.merged(capsys, project, monkeypatch, flags)
        code, text = run(capsys, "session", "transition", "abandoned", "--id", SID)
        assert code == 0, text

    def test_a_project_with_no_steps_merges_as_before(self, capsys, project, monkeypatch):
        root = project({})
        reviewed(capsys, root, monkeypatch)
        code, payload = run_json(capsys, "session", "merge", "--id", SID)
        assert code == 0, payload
        assert payload["after_merge"] == []
