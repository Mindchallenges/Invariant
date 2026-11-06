"""`agents.rendered_current`: drift between the library agents and their copies.

IDEA_031 A6 increment 4 (ADR-012 D8, the A6 ruling 10). Every library agent's
installed copy is compared with what the host would render now from its
neutral source and its overlay. "Current" is exactly what `agents install
--dry-run` reports, because the rule asks the same install plan the command
prints. `core/` never imports a host, so the plan reaches the rule as a
callable the CLI puts on the rule context, the way the host's instruction
files reach `guidelines.generated_current`. With no adapter for `host.kind`
the rule is N/A. It warns in every profile and only reports: it never
installs. `doctor`'s `library agents current` row is the same answer.
"""

from __future__ import annotations

import json
import shutil
from functools import partial

import pytest
import yaml

from invariant.core.machines import load_pipeline_machine
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, evaluate, get_rule
from invariant.domains.pipeline import PipelineDomain
from invariant.hosts import assets
from invariant.hosts import claude_code as cc
from invariant.hosts.claude_code import ClaudeCodeHost

RULE = "agents.rendered_current"
NAME = "feature-architect"
OVERLAY_REL = f"docs/agents/{NAME}.yaml"
ROW = cc.CHECK_AGENTS_CURRENT


@pytest.fixture
def project(tmp_path, monkeypatch):
    from invariant.cli.main import main

    root = tmp_path / "proj"
    (root / "docs" / "architecture").mkdir(parents=True)
    monkeypatch.chdir(root)
    assert main(["init", "--name", "demo"]) == 0
    return root


def run(*argv):
    from invariant.cli.main import main

    return main(list(argv))


def configure(root, **sections):
    path = root / ".claude" / "invariant.yaml"
    body = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key, value in sections.items():
        if isinstance(value, dict):
            body.setdefault(key, {}).update(value)
        else:
            body[key] = value
    path.write_text(yaml.safe_dump(body), encoding="utf-8")


def write_overlay(root, text="Our house rule."):
    path = root / OVERLAY_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            {"version": 1, "operations": [{"append": "phase-1", "text": text}]}
        ),
        encoding="utf-8",
    )
    configure(root, agents={"overlays": {NAME: OVERLAY_REL}})


def target(root, name=NAME):
    return root / ".claude" / "agents" / f"{name}.md"


def agent_files(root):
    return {p.name: p.read_bytes() for p in sorted((root / ".claude" / "agents").glob("*"))}


def host_plan(root):
    """The plan callable the CLI hands the rule, for the project at ``root``."""
    from invariant.cli.main import host_agent_plan

    return host_agent_plan(Manifest.locate(root))


def context(root, plan=None):
    manifest = Manifest.locate(root)
    return RuleContext(manifest=manifest, cwd=root, host_agent_plan=plan)


#: Ask the rule with the plan the CLI would hand it.
HOST = object()


def current(root, plan=HOST):
    (result,) = evaluate([RULE], context(root, host_plan(root) if plan is HOST else plan))
    return result


def edit(root, name=NAME):
    path = target(root, name)
    path.write_text(path.read_text(encoding="utf-8") + "mine\n", encoding="utf-8")


def older(root, name=NAME, source_digest="0" * 16):
    """A copy an earlier release wrote and nobody touched: its body matches
    its own witness, not today's render, and its source digest is not
    today's, so the library is the input that moved."""
    shipped = ClaudeCodeHost(root, manifest=Manifest.locate(root))
    (step,) = [s for s in shipped.plan_assets(only="agent") if s.identity == name]
    text = step.text + "Older wording.\n"
    target(root, name).write_text(
        assets.render_managed(text, source_digest=source_digest, overlay_digest=None),
        encoding="utf-8",
    )


def unreadable(root, name=NAME):
    target(root, name).write_text("no frontmatter here\n", encoding="utf-8")


def absent(root, name=NAME):
    target(root, name).unlink()


def a_changed_overlay(root):
    write_overlay(root, "First wording.")
    assert run("agents", "install") == 0
    write_overlay(root, "Second wording.")


@pytest.fixture
def broken_source(tmp_path, monkeypatch):
    """A library whose shipped `feature-architect` source does not parse."""
    data = tmp_path / "library"
    shutil.copytree(cc.DATA_DIR / "agents", data / "agents")
    path = data / "agents" / f"{NAME}.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(ln for ln in text.split("\n") if not ln.startswith("capabilities:")),
        encoding="utf-8",
    )
    monkeypatch.setattr(cc, "DATA_DIR", data)
    return data


class TestTheRule:
    def test_no_host_adapter_is_not_applicable(self, project):
        configure(project, host={"kind": "elsewhere"})
        result = current(project, plan=None)
        assert result.verdict.skipped
        assert "'elsewhere'" in result.verdict.message

    def test_the_cli_has_no_plan_for_a_kind_with_no_adapter(self, project):
        configure(project, host={"kind": "elsewhere"})
        assert host_plan(project) is None

    @pytest.mark.parametrize("profile", ["loose", "standard", "strict"])
    def test_it_warns_in_every_profile(self, project, profile):
        configure(project, enforcement={"profile": profile})
        assert current(project).level.value == "warn"

    def test_a_fresh_install_is_current(self, project):
        result = current(project)
        assert result.verdict.ok and not result.verdict.skipped
        assert "4 library agent(s) current" in result.verdict.message

    def test_an_absent_copy_is_named_and_install_is_the_fix(self, project):
        absent(project)
        verdict = current(project).verdict
        assert not verdict.ok
        assert f"{NAME} (absent)" in verdict.message
        assert verdict.fixes == ("invariant agents install",)

    def test_an_older_copy_names_the_input_that_moved(self, project):
        older(project)
        verdict = current(project).verdict
        assert not verdict.ok
        assert f"{NAME} ({assets.OLDER}) — {assets.LIBRARY_CHANGED}" in verdict.message
        assert "your edits are kept" not in verdict.detail

    def test_a_changed_overlay_is_named(self, project):
        a_changed_overlay(project)
        verdict = current(project).verdict
        assert f"{NAME} ({assets.OLDER}) — {assets.OVERLAY_CHANGED}" in verdict.message

    def test_an_edited_copy_names_the_overlay_fix(self, project):
        edit(project)
        verdict = current(project).verdict
        assert not verdict.ok
        assert f"{NAME} ({assets.LOCAL})" in verdict.message
        assert "your edits are kept" in verdict.detail
        assert verdict.fixes == (
            (
                "move the edit into an overlay (agents.overlays) so it survives "
                "upgrades, or a model line into agents.tiers and host.tier_map, "
                "then: invariant agents install --overwrite-local"
            ),
        )

    def test_an_unreadable_copy_is_named(self, project):
        unreadable(project)
        verdict = current(project).verdict
        assert f"{NAME} ({assets.UNREADABLE})" in verdict.message

    def test_each_kind_of_drift_is_named_once(self, project):
        edit(project)
        absent(project, "bug-reproducer")
        verdict = current(project).verdict
        assert "2 of 4 library agent(s) not current" in verdict.message
        assert verdict.fixes[0] == "invariant agents install"
        assert "--overwrite-local" in verdict.fixes[1]

    def test_it_never_installs(self, project):
        edit(project)
        absent(project, "bug-reproducer")
        before = agent_files(project)
        current(project)
        assert agent_files(project) == before

    def test_an_empty_library_is_not_applicable(self, project):
        configure(project, agents={"library": []})
        assert current(project).verdict.skipped

    def test_a_bad_overlay_fails_naming_it_rather_than_raising(self, project):
        write_overlay(project)
        configure(project, agents={"overlays": {NAME: "docs/agents/none.yaml"}})
        verdict = current(project).verdict
        assert not verdict.ok and not verdict.skipped
        assert "cannot be checked" in verdict.message
        assert "docs/agents/none.yaml" in verdict.message

    def test_an_unparseable_shipped_source_fails_rather_than_raising(
        self, project, broken_source
    ):
        verdict = current(project).verdict
        assert not verdict.ok and not verdict.skipped
        assert "cannot be checked" in verdict.message
        assert "capabilities" in verdict.message

    def test_the_answer_is_the_hosts_plan_read_once(self, project):
        from invariant.core.rules import AgentCopy

        calls = []

        def plan():
            calls.append(1)
            return [AgentCopy(name="a", state="absent", current=False)]

        verdict = current(project, plan=plan).verdict
        assert calls == [1]
        assert "a (absent)" in verdict.message


def a_moved_tier_hint(root):
    configure(root, host={"tier_map": {"deep": "placeholder-one"}})
    assert run("agents", "install") == 0
    configure(root, host={"tier_map": {"deep": "placeholder-two"}})


PREPARE = {
    "clean": lambda root: None,
    "tier hint moved": a_moved_tier_hint,
    "edited": edit,
    "older": older,
    "absent": absent,
    "unreadable": unreadable,
    "overlay changed": a_changed_overlay,
}


@pytest.mark.parametrize("prepare", PREPARE.values(), ids=PREPARE.keys())
def test_it_agrees_with_agents_install_dry_run(project, capsys, prepare):
    """One plan, so the rule and `agents install --dry-run` cannot disagree.

    One declared exception (the A7 ruling 6): a copy whose tier hint moved is a
    write in the dry run and current to the rule.
    """
    prepare(project)
    capsys.readouterr()
    assert run("--json", "agents", "install", "--dry-run") == 0
    dry = json.loads(capsys.readouterr().out)["plan"]
    verdict = current(project).verdict
    moved = [step for step in dry if step["state"] == assets.TIER_MOVED]
    assert all(step["write"] and step["identity"] not in verdict.message for step in moved)
    assert bool(moved) == (prepare is a_moved_tier_hint)
    stale = {
        step["identity"]: step
        for step in dry
        if step["state"] not in (assets.CURRENT, assets.TIER_MOVED)
    }
    assert verdict.ok == (not stale)
    for name, step in stale.items():
        assert f"{name} ({step['state']})" + (
            f" — {step['changed']}" if step["changed"] else ""
        ) in verdict.message


class TestWhereItRuns:
    @pytest.mark.parametrize("methodology", ["tdd", "bugfix", "spec-first", "chore", "docs"])
    def test_the_landing_entry_carries_it_only_with_a_host_plan(
        self, make_manifest, methodology
    ):
        manifest = make_manifest({"version": 1, "methodology": {"pipeline": methodology}})
        assert RULE not in load_pipeline_machine(manifest).gate_ids()
        machine = load_pipeline_machine(manifest, host_agents=True)
        (landing,) = machine.states_with_role("landing")
        assert RULE in machine.state(landing).entry_gates
        for state in machine.states.values():
            gates = (*state.exit_gates, *(() if state.id == landing else state.entry_gates))
            assert RULE not in gates, state.id

    def test_the_pipeline_domain_injects_it_with_a_plan_and_hands_it_on(
        self, make_manifest, monkeypatch
    ):
        def plan():
            return []

        manifest = make_manifest({"version": 1, "project": {"name": "demo"}})
        assert RULE not in PipelineDomain(manifest).machine.gate_ids()
        pipelines = PipelineDomain(manifest, host_agent_plan=plan)
        assert RULE in pipelines.machine.gate_ids()

        seen = []
        monkeypatch.setattr(
            "invariant.domains.pipeline.evaluate",
            lambda ids, ctx: seen.append(ctx.host_agent_plan) or [],
        )
        monkeypatch.setattr(pipelines, "require", lambda sid: _Record())
        monkeypatch.setattr(pipelines, "machine_for", lambda record: pipelines.machine)
        pipelines.evaluate_gates("s1", target="red")
        assert seen == [plan]

    def test_a_pinned_methodology_keeps_it(self, make_manifest):
        manifest = make_manifest({"version": 1, "methodology": {"pipeline": "tdd"}})
        pipelines = PipelineDomain(manifest, host_agent_plan=list)
        assert RULE in pipelines.machine_for(_Pinned("chore")).gate_ids()
        assert RULE in pipelines.machine_for(_Pinned("tdd", ruleset=True)).gate_ids()

    def test_the_cli_hands_the_plan_to_the_rule_context_and_the_pipelines(self, project):
        from invariant.cli.main import Context

        ctx = Context()
        assert ctx.rule_context().host_agent_plan is not None
        assert RULE in ctx.pipelines.machine.gate_ids()

    def test_the_oracle_cache_is_handed_the_host_plan(self):
        """The oracle evaluates the landing gates too, and may not import a host."""
        from invariant.cli.main import _oracle_gates, host_agent_plan
        from invariant.oracle.gates import evaluate_row

        cache = _oracle_gates()
        assert isinstance(cache.evaluate, partial)
        assert cache.evaluate.func is evaluate_row
        assert cache.evaluate.keywords["agent_plan_for"] is host_agent_plan

    def test_the_oracle_row_evaluation_passes_it_to_the_pipeline_domain(
        self, project, monkeypatch
    ):
        from invariant.oracle import gates

        seen = {}

        class Domain:
            def __init__(self, manifest, store=None, host_instruction_files=(), host_agent_plan=None):
                seen["plan"] = host_agent_plan

            def evaluate_gates_by_destination(self, session_id, session=None, cwd=None):
                return {}

        class Sessions:
            def __init__(self, manifest, store=None):
                pass

            def get(self, session_id):
                return None

        monkeypatch.setattr(gates, "PipelineDomain", Domain)
        monkeypatch.setattr(gates, "SessionDomain", Sessions)
        row = type("Row", (), {"worktree": str(project), "root": str(project),
                               "project": str(project), "session_id": "s1"})()
        monkeypatch.setattr(gates, "_workdir", lambda r: project)
        marker = object()
        gates.evaluate_row(row, agent_plan_for=lambda manifest: marker)
        assert seen["plan"] is marker


class _Record:
    state = "designing"

    def to_dict(self):
        return {"state": self.state, "evidence": {}}


class _Pinned:
    def __init__(self, methodology, ruleset=False):
        self.methodology = methodology
        self.ruleset = {"path": "r", "digest": "d", "error": None} if ruleset else None


class TestTheDoctorRow:
    def rows(self, capsys):
        capsys.readouterr()
        code = run("--json", "doctor")
        return code, {c["check"]: c for c in json.loads(capsys.readouterr().out)["checks"]}

    def test_the_row_is_the_rules_answer(self, project, capsys):
        edit(project)
        absent(project, "bug-reproducer")
        _, rows = self.rows(capsys)
        verdict = current(project).verdict
        detail = rows[ROW]["detail"]
        # Drift warns, as the rule does: ok with the warned flag, exit clean.
        assert rows[ROW]["ok"] is True
        assert rows[ROW]["warn"] is True
        assert detail.startswith(verdict.message)
        assert verdict.detail in detail
        for fix in verdict.fixes:
            assert f"try: {fix}" in detail

    def test_drift_alone_leaves_doctor_clean(self, project, capsys):
        edit(project)
        absent(project, "bug-reproducer")
        _, rows = self.rows(capsys)
        assert rows[ROW]["warn"] is True
        # The exit code is clean on drift alone (the CLI test covers a project
        # with nothing else wrong); here, the row is not among the failures.
        assert ROW not in [c for c, row in rows.items() if not row["ok"]]

    def test_a_current_install_passes_the_row(self, project, capsys):
        _, rows = self.rows(capsys)
        assert rows[ROW]["ok"] is True
        assert rows[ROW]["warn"] is False
        assert rows[ROW]["detail"] == current(project).verdict.message

    def test_an_unparseable_shipped_source_fails_the_row_rather_than_crashing(
        self, project, capsys, broken_source
    ):
        code, rows = self.rows(capsys)
        assert code == 1
        assert rows[ROW]["ok"] is False
        assert rows[ROW]["warn"] is False
        assert "cannot be checked" in rows[ROW]["detail"]
        assert "capabilities" in rows[ROW]["detail"]

    def test_check_names_an_edited_copy(self, project, capsys):
        edit(project)
        capsys.readouterr()
        run("check", RULE)
        text = capsys.readouterr().out
        assert f"[warn] {RULE}" in text
        assert f"{NAME} ({assets.LOCAL})" in text


def test_the_rule_is_registered_with_no_event():
    spec = get_rule(RULE)
    assert spec is not None and spec.events == ()


class TestUninstallWithARefusedRender:
    """`uninstall --remove-assets` once refused whole on a broken overlay. It
    now classifies such an agent against its render without the overlay, and
    keeps any copy it cannot classify (the A6.3 review)."""

    def removal(self, project, capsys, *argv):
        capsys.readouterr()
        code = run("--json", "uninstall", "--remove-assets", *argv)
        return code, json.loads(capsys.readouterr().out)

    def test_a_broken_overlay_still_removes_what_the_install_wrote(self, project, capsys):
        write_overlay(project)
        assert run("agents", "install") == 0
        configure(project, agents={"overlays": {NAME: "docs/agents/none.yaml"}})
        code, result = self.removal(project, capsys)
        assert code == 0
        assert str(target(project)) in result["assets"]
        assert not target(project).exists()

    def test_a_broken_overlay_keeps_an_edited_copy(self, project, capsys):
        write_overlay(project)
        assert run("agents", "install") == 0
        edit(project)
        configure(project, agents={"overlays": {NAME: "docs/agents/none.yaml"}})
        code, result = self.removal(project, capsys)
        assert code == 0
        assert f"agent:{NAME}" in result["kept"]
        assert target(project).read_text(encoding="utf-8").endswith("mine\n")

    def test_a_dry_run_says_the_same_and_removes_nothing(self, project, capsys):
        write_overlay(project)
        assert run("agents", "install") == 0
        edit(project)
        configure(project, agents={"overlays": {NAME: "docs/agents/none.yaml"}})
        before = agent_files(project)
        code, result = self.removal(project, capsys, "--dry-run")
        assert code == 0 and f"agent:{NAME}" in result["kept"]
        assert agent_files(project) == before

    def test_an_unparseable_source_keeps_its_copy_and_removes_the_rest(
        self, project, capsys, broken_source
    ):
        code, result = self.removal(project, capsys)
        assert code == 0
        assert result["kept"] == [f"agent:{NAME}"]
        assert target(project).exists()
        assert not target(project, "bug-reproducer").exists()

    def test_an_unparseable_source_with_no_copy_keeps_nothing(
        self, project, capsys, broken_source
    ):
        absent(project)
        code, result = self.removal(project, capsys)
        assert code == 0 and result["kept"] == []

    def test_the_install_still_refuses_a_broken_overlay(self, project, capsys):
        """The fallback is the removal's alone: an install never writes a
        render the overlay was meant to change."""
        configure(project, agents={"overlays": {NAME: "docs/agents/none.yaml"}})
        before = agent_files(project)
        capsys.readouterr()
        assert run("agents", "install") == 1
        assert agent_files(project) == before
