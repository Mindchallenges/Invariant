"""`guidelines.generated_current`: drift between the ruleset and its outputs.

IDEA_031 A2 increment 5 (ADR-012 D3, "Drift is a rule"; ruling 7). Every
generated output of the guidelines domain, the checklist and each
`guidelines/index` block in an instruction file, is compared with what
`guidelines render` would produce now from the ruleset in the tree in hand.
"Current" is exactly what `render --dry-run` reports, because the rule asks
the same plan render writes from. It warns in every profile and only
reports: it never renders. An absent block nobody inserted is no finding.
"""

from __future__ import annotations

from functools import partial

import pytest
from conftest import write_manifest
from test_cli import out, payload, run
from test_guidelines import WITH_ALWAYS
from test_guidelines_domain import (
    CHECKLIST_REL,
    CLAUDE,
    INDEX,
    OURS,
    RULESET_REL,
    domain,
    write,
)

from invariant.core import guidelines
from invariant.core.machines import load_pipeline_machine
from invariant.core.rules import RuleContext, evaluate
from invariant.domains.pipeline import PipelineDomain

RULE = "guidelines.generated_current"


def context(make_manifest, root, guidelines_cfg=None, *, in_session=None, host=(), **extra):
    body = {
        "version": 1,
        # The ruleset's pattern rule reads code, which needs a masking adapter.
        "project": {"name": "demo", "ecosystems": ["python"]},
        "guidelines": {"ruleset": RULESET_REL, **(guidelines_cfg or {})},
        **extra,
    }
    if guidelines_cfg is False:
        body.pop("guidelines")
    return RuleContext(
        manifest=make_manifest(body), cwd=root, session=in_session, host_instruction_files=host
    )


def current(ctx):
    (result,) = evaluate([RULE], ctx)
    return result


def render(make_manifest, root, *, host=(), **kwargs):
    return domain(make_manifest, root, instruction_files=host).render(**kwargs)


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, WITH_ALWAYS)
    return tmp_path


class TestTheChecklist:
    def test_no_ruleset_configured_is_not_applicable(self, make_manifest, tmp_path):
        result = current(context(make_manifest, tmp_path, False))
        assert result.verdict.skipped
        assert "no guidelines ruleset configured" in result.verdict.message

    @pytest.mark.parametrize("profile", ["loose", "standard", "strict"])
    def test_it_warns_in_every_profile(self, make_manifest, tmp_path, profile):
        ctx = context(make_manifest, tmp_path, enforcement={"profile": profile})
        assert ctx.manifest.rule_level(RULE).value == "warn"

    def test_a_checklist_never_rendered_is_missing(self, make_manifest, tree):
        result = current(context(make_manifest, tree))
        assert not result.verdict.ok and not result.blocks
        assert f"{CHECKLIST_REL} is missing" in result.verdict.message
        assert "invariant guidelines render" in result.verdict.fixes[0]

    def test_a_rendered_checklist_is_current(self, make_manifest, tree):
        render(make_manifest, tree)
        result = current(context(make_manifest, tree))
        assert result.verdict.ok and not result.verdict.skipped
        assert guidelines.digest_of(WITH_ALWAYS.encode("utf-8")) in result.verdict.message

    def test_a_changed_ruleset_makes_it_stale(self, make_manifest, tree):
        render(make_manifest, tree)
        write(tree, RULESET_REL, WITH_ALWAYS.replace("severity: warn", "severity: blocker"))
        result = current(context(make_manifest, tree))
        assert not result.verdict.ok
        assert f"{CHECKLIST_REL} is stale" in result.verdict.message
        assert "invariant guidelines render" in result.verdict.fixes[0]

    def test_a_hand_edit_is_named_and_render_is_the_fix(self, make_manifest, tree):
        render(make_manifest, tree)
        path = tree / CHECKLIST_REL
        path.write_text(path.read_text(encoding="utf-8") + "\nA hand edit.\n", encoding="utf-8")
        result = current(context(make_manifest, tree))
        assert f"{CHECKLIST_REL} was edited by hand" in result.verdict.message
        assert "move the edit into" in result.verdict.fixes[0]
        assert not any("--overwrite-local" in fix for fix in result.verdict.fixes)

    def test_it_never_renders(self, make_manifest, tree):
        current(context(make_manifest, tree))
        assert not (tree / CHECKLIST_REL).exists()

    def test_a_ruleset_that_does_not_load_fails(self, make_manifest, tree):
        """Every gate that needs the ruleset fails rather than skips (D3)."""
        write(tree, RULESET_REL, "version: 9\n")
        result = current(context(make_manifest, tree))
        assert not result.verdict.ok and not result.verdict.skipped
        assert "does not validate" in result.verdict.message

    def test_a_render_refusal_fails_naming_it(self, make_manifest, tree):
        """A target render would refuse is one drift could never be fixed in."""
        write(tree, CHECKLIST_REL, "# Somebody else's checklist\n")
        result = current(context(make_manifest, tree))
        assert not result.verdict.ok
        assert "no invariant-managed marker" in result.verdict.message
        assert result.verdict.fixes

    def test_an_exempt_checklist_fails(self, make_manifest, tree):
        """Generated files are never exempt from isolation (ruling 13)."""
        result = current(context(
            make_manifest, tree, session={"exempt_paths": ["docs/guidelines/*"]}
        ))
        assert "exempt from session isolation" in result.verdict.message

    def test_a_privacy_finding_fails_as_render_would_refuse(self, make_manifest, tree):
        render(make_manifest, tree)
        write(tree, RULESET_REL, WITH_ALWAYS.replace("title: Testing", "title: TICKET-4242"))
        result = current(context(make_manifest, tree, privacy={"extra": ["TICKET-[0-9]+"]}))
        assert not result.verdict.ok
        assert "privacy" in result.verdict.message
        assert "TICKET-4242" not in result.verdict.message

    def test_the_session_worktree_is_the_tree_judged(self, make_manifest, tree):
        render(make_manifest, tree)
        worktree = tree / "wt"
        write(worktree, RULESET_REL, WITH_ALWAYS)
        result = current(context(make_manifest, tree, in_session={"worktree": str(worktree)}))
        assert f"{CHECKLIST_REL} is missing" in result.verdict.message


class TestTheInstructionBlocks:
    @pytest.fixture
    def tree(self, tmp_path):
        write(tmp_path, RULESET_REL, WITH_ALWAYS)
        write(tmp_path, CLAUDE, OURS)
        return tmp_path

    def test_an_absent_block_is_no_finding(self, make_manifest, tree):
        render(make_manifest, tree, host=(CLAUDE,))
        result = current(context(make_manifest, tree, host=(CLAUDE,)))
        assert result.verdict.ok
        assert f"no {INDEX} block in {CLAUDE}" in result.verdict.detail

    def test_a_missing_instruction_file_is_no_finding(self, make_manifest, tree):
        render(make_manifest, tree)
        result = current(context(make_manifest, tree, {"instruction_files": ["NOTES.md"]}))
        assert result.verdict.ok

    def test_an_inserted_block_is_current(self, make_manifest, tree):
        render(make_manifest, tree, host=(CLAUDE,), insert=(INDEX,))
        assert current(context(make_manifest, tree, host=(CLAUDE,))).verdict.ok

    def test_a_stale_block_names_the_file_and_block(self, make_manifest, tree):
        render(make_manifest, tree, host=(CLAUDE,), insert=(INDEX,))
        write(tree, RULESET_REL, WITH_ALWAYS.replace("title: Testing", "title: Tests"))
        render(make_manifest, tree)  # the checklist only: it is current again
        result = current(context(make_manifest, tree, host=(CLAUDE,)))
        assert not result.verdict.ok
        assert f"{CLAUDE} ({INDEX} block) is stale" in result.verdict.message
        assert CHECKLIST_REL not in result.verdict.message

    def test_a_hand_edited_block_names_overwrite_local(self, make_manifest, tree):
        render(make_manifest, tree, host=(CLAUDE,), insert=(INDEX,))
        path = tree / CLAUDE
        text = path.read_text(encoding="utf-8")
        path.write_text(
            text.replace("<!-- invariant:end", "a hand edit\n<!-- invariant:end"), encoding="utf-8"
        )
        result = current(context(make_manifest, tree, host=(CLAUDE,)))
        assert f"{CLAUDE} ({INDEX} block) was edited by hand" in result.verdict.message
        assert any(
            "invariant guidelines render --overwrite-local" in fix for fix in result.verdict.fixes
        )

    def test_the_host_default_is_read_from_the_context(self, make_manifest, tree):
        """No domain or core module imports a host: the CLI supplies the
        default of guidelines.instruction_files on the rule context."""
        render(make_manifest, tree, host=(CLAUDE,), insert=(INDEX,))
        write(tree, RULESET_REL, WITH_ALWAYS.replace("title: Testing", "title: Tests"))
        render(make_manifest, tree)
        assert not current(context(make_manifest, tree, host=(CLAUDE,))).verdict.ok
        assert current(context(make_manifest, tree, host=())).verdict.ok

    def test_a_malformed_pair_fails_naming_the_line(self, make_manifest, tree):
        render(make_manifest, tree)
        write(tree, CLAUDE, OURS + f"<!-- invariant:begin {INDEX} digest=000000000000 -->\n")
        result = current(context(make_manifest, tree, host=(CLAUDE,)))
        assert not result.verdict.ok
        assert CLAUDE in result.verdict.message and "line" in result.verdict.message

    def test_an_exempt_instruction_file_fails(self, make_manifest, tree):
        render(make_manifest, tree)
        result = current(context(
            make_manifest, tree, host=(CLAUDE,), session={"exempt_paths": [CLAUDE]}
        ))
        assert "exempt from session isolation" in result.verdict.message


@pytest.mark.parametrize(
    "prepare",
    [
        lambda root: None,
        lambda root: write(root, RULESET_REL, WITH_ALWAYS.replace("title: Testing", "title: T")),
        lambda root: write(
            root, CHECKLIST_REL, (root / CHECKLIST_REL).read_text(encoding="utf-8") + "x\n"
        ),
        lambda root: (root / CHECKLIST_REL).unlink(),
    ],
    ids=["current", "stale", "edited", "missing"],
)
def test_it_agrees_with_render_dry_run(make_manifest, tmp_path, prepare):
    """One plan, so the rule and `render --dry-run` cannot disagree."""
    write(tmp_path, RULESET_REL, WITH_ALWAYS)
    write(tmp_path, CLAUDE, OURS)
    render(make_manifest, tmp_path, host=(CLAUDE,), insert=(INDEX,))
    prepare(tmp_path)
    dry = render(make_manifest, tmp_path, host=(CLAUDE,), dry_run=True)
    result = current(context(make_manifest, tmp_path, host=(CLAUDE,)))
    assert result.verdict.ok == (
        dry.status == "current" and all(b.status in ("current", "absent") for b in dry.blocks)
    )


class TestWhereItRuns:
    @pytest.mark.parametrize("methodology", ["tdd", "bugfix", "spec-first", "chore", "docs"])
    def test_the_landing_entry_carries_it_only_with_a_ruleset(self, make_manifest, methodology):
        bare = load_pipeline_machine(
            make_manifest({"version": 1, "methodology": {"pipeline": methodology}})
        )
        assert RULE not in bare.gate_ids()
        machine = load_pipeline_machine(make_manifest({
            "version": 1,
            "methodology": {"pipeline": methodology},
            "guidelines": {"ruleset": RULESET_REL},
        }))
        (landing,) = machine.states_with_role("landing")
        assert RULE in machine.state(landing).entry_gates
        for state in machine.states.values():
            gates = (*state.exit_gates, *(() if state.id == landing else state.entry_gates))
            assert RULE not in gates, state.id

    def test_the_pipeline_domain_hands_the_host_default_to_its_gates(
        self, make_manifest, tmp_path, monkeypatch
    ):
        seen = []
        monkeypatch.setattr(
            "invariant.domains.pipeline.evaluate",
            lambda ids, ctx: seen.append(ctx.host_instruction_files) or [],
        )
        manifest = make_manifest({"version": 1, "project": {"name": "demo"}})
        pipelines = PipelineDomain(manifest, host_instruction_files=(CLAUDE,))
        monkeypatch.setattr(pipelines, "require", lambda sid: _Record())
        monkeypatch.setattr(pipelines, "machine_for", lambda record: pipelines.machine)
        pipelines.evaluate_gates("s1", target="red")
        assert seen == [(CLAUDE,)]


class _Record:
    state = "designing"

    def to_dict(self):
        return {"state": self.state, "evidence": {}}


class TestTheCommands:
    @pytest.fixture(autouse=True)
    def _inside(self, project, monkeypatch):
        monkeypatch.chdir(project)

    def configure(self, root, **extra):
        write_manifest(root, {
            "version": 1,
            "project": {"name": "demo", "ecosystems": ["python"]},
            "guidelines": {"ruleset": RULESET_REL},
            **extra,
        })
        write(root, RULESET_REL, WITH_ALWAYS)
        write(root, CLAUDE, OURS)

    def doctor_row(self, capsys):
        return next(ln for ln in out(capsys).splitlines() if "guidelines generated" in ln)

    def test_doctor_without_a_ruleset_passes_the_row(self, project, capsys):
        run("init", "--ecosystem", "python")
        capsys.readouterr()
        run("doctor")
        assert "no guidelines ruleset configured" in self.doctor_row(capsys)

    def doctor_rows(self, capsys):
        return {c["check"]: c for c in payload(capsys)["checks"]}

    def test_doctor_warns_the_row_on_drift_and_names_the_fix(self, project, capsys):
        """Drift warns, like the gate, and never fails the diagnosis (user ruling)."""
        self.configure(project)
        run("guidelines", "render", "--insert", INDEX)
        capsys.readouterr()
        clean_code = run("doctor")
        capsys.readouterr()
        write(project, RULESET_REL, WITH_ALWAYS.replace("title: Testing", "title: Tests"))
        code = run("doctor")
        row = self.doctor_row(capsys)
        assert row.lstrip().startswith("warn")
        assert "stale" in row and CHECKLIST_REL in row
        assert f"{CLAUDE} ({INDEX} block) is stale" in row
        assert "try: invariant guidelines render" in row
        assert code == clean_code

    def test_doctor_json_marks_drift_as_a_warning_not_a_failure(self, project, capsys):
        self.configure(project)
        capsys.readouterr()
        run("--json", "doctor")
        row = self.doctor_rows(capsys)["guidelines generated"]
        assert row["ok"] is True and row["warn"] is True
        assert "missing" in row["detail"]

    def test_doctor_json_carries_no_warning_on_a_current_row(self, project, capsys):
        self.configure(project)
        run("guidelines", "render", "--insert", INDEX)
        run("agents", "install")
        capsys.readouterr()
        run("--json", "doctor")
        rows = self.doctor_rows(capsys)
        assert rows["guidelines generated"]["warn"] is False
        # The docs assessment row (ADR-016 D3) reports this fixture's
        # one-heading overview as gaps; it is the docs', not the guidelines'.
        assert all(
            r["warn"] is False for k, r in rows.items() if k != "docs assessment"
        )

    def test_doctor_warns_the_row_on_a_refusal_the_ruleset_row_fails(self, project, capsys):
        """A ruleset that does not load fails its own row; this row only warns."""
        self.configure(project)
        write(project, RULESET_REL, "version: 1\nnot: [valid\n")
        capsys.readouterr()
        code = run("doctor")
        text = out(capsys)
        generated = next(ln for ln in text.splitlines() if "guidelines generated" in ln)
        ruleset = next(ln for ln in text.splitlines() if "guidelines ruleset" in ln)
        assert generated.lstrip().startswith("warn")
        assert "cannot be checked" in generated
        assert ruleset.lstrip().startswith("FAIL")
        assert code == 1

    def test_doctor_passes_the_row_once_rendered(self, project, capsys):
        self.configure(project)
        run("guidelines", "render", "--insert", INDEX)
        capsys.readouterr()
        run("doctor")
        assert "current" in self.doctor_row(capsys)

    def test_check_names_a_hand_edited_default_block(self, project, capsys):
        """The CLI's rule context carries the claude_code host's CLAUDE.md."""
        self.configure(project)
        run("guidelines", "render", "--insert", INDEX)
        path = project / CLAUDE
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "<!-- invariant:end", "a hand edit\n<!-- invariant:end"
            ),
            encoding="utf-8",
        )
        capsys.readouterr()
        run("check", RULE)
        assert f"{CLAUDE} ({INDEX} block) was edited by hand" in out(capsys)


def test_the_oracle_cache_is_handed_the_host_default():
    """The oracle evaluates the landing gates too, and may not import a host:
    the CLI hands its gate cache the host's instruction files."""
    from invariant.cli.main import _oracle_gates
    from invariant.oracle.gates import evaluate_row

    cache = _oracle_gates()
    assert isinstance(cache.evaluate, partial)
    assert cache.evaluate.func is evaluate_row
    manifest_like = type("M", (), {"get": lambda self, key: "claude_code"})()
    assert cache.evaluate.keywords["instruction_files_for"](manifest_like) == (CLAUDE,)
