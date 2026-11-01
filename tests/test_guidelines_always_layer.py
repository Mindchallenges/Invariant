"""The always-layer warning (IDEA_031 A5 increment 4, ADR-012 D7, A5 ruling D12).

The managed `guidelines/index` block is where a host reads a ruleset's
`always` rules as standing instructions. When the ruleset has always rules and
no instruction file carries the block, two places say so:

* `guidelines.generated_current` notes it in its detail and does not fail,
  since the turn index still lists the always rules on every turn (A5.2);
* `doctor` prints a `guidelines always layer` row that warns, naming the
  always rules' count and the fix, `invariant guidelines render --insert
  guidelines/index`.

Which files are instruction files is `guidelines.instruction_files`, or with
that key null the host adapter's default the CLI hands the rule context. The
core never names a host's file itself. With no always rules, or with the
block present, there is no row and no note.
"""

from __future__ import annotations

import pytest
from conftest import write_manifest
from test_cli import out, payload, run
from test_guidelines import RULESET, WITH_ALWAYS
from test_guidelines_current import context, current, render
from test_guidelines_domain import (
    CHECKLIST_REL,
    CLAUDE,
    INDEX,
    OURS,
    RULESET_REL,
    write,
)

from invariant.core import rules

FIX = f"invariant guidelines render --insert {INDEX}"
ROW = "guidelines always layer"


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, WITH_ALWAYS)
    write(tmp_path, CLAUDE, OURS)
    return tmp_path


class TestTheDriftGateNotesIt:
    def test_always_rules_and_no_block_pass_with_a_note(self, make_manifest, tree):
        render(make_manifest, tree, host=(CLAUDE,))
        result = current(context(make_manifest, tree, host=(CLAUDE,)))
        assert result.verdict.ok and not result.verdict.skipped
        detail = result.verdict.detail
        assert f"1 always rule but no instruction file carries the {INDEX} block" in detail
        assert CLAUDE in detail
        assert "the turn index still lists them on every turn" in detail
        assert FIX in detail

    def test_the_absent_block_is_still_named_beside_the_note(self, make_manifest, tree):
        render(make_manifest, tree, host=(CLAUDE,))
        detail = current(context(make_manifest, tree, host=(CLAUDE,))).verdict.detail
        assert f"no {INDEX} block in {CLAUDE}" in detail

    def test_an_inserted_block_carries_no_note(self, make_manifest, tree):
        render(make_manifest, tree, host=(CLAUDE,), insert=(INDEX,))
        result = current(context(make_manifest, tree, host=(CLAUDE,)))
        assert result.verdict.ok
        assert "always" not in (result.verdict.detail or "")

    def test_a_hand_edited_block_still_carries_them(self, make_manifest, tree):
        """The drift is the gate's own finding; the block is there."""
        render(make_manifest, tree, host=(CLAUDE,), insert=(INDEX,))
        path = tree / CLAUDE
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "<!-- invariant:end", "a hand edit\n<!-- invariant:end"
            ),
            encoding="utf-8",
        )
        result = current(context(make_manifest, tree, host=(CLAUDE,)))
        assert not result.verdict.ok
        assert "always rule" not in result.verdict.detail

    def test_no_always_rules_carry_no_note(self, make_manifest, tree):
        write(tree, RULESET_REL, RULESET)
        render(make_manifest, tree, host=(CLAUDE,))
        result = current(context(make_manifest, tree, host=(CLAUDE,)))
        assert result.verdict.ok
        assert "always" not in (result.verdict.detail or "")

    def test_one_block_among_several_files_is_enough(self, make_manifest, tree):
        render(make_manifest, tree, host=(CLAUDE,), insert=(INDEX,))
        cfg = {"instruction_files": [CLAUDE, "AGENTS.md"]}
        result = current(context(make_manifest, tree, cfg))
        assert result.verdict.ok
        assert "always rule" not in (result.verdict.detail or "")

    def test_the_configured_files_are_the_ones_named(self, make_manifest, tree):
        """The manifest's list wins over the host's default, so the note names it."""
        render(make_manifest, tree, host=(CLAUDE,), insert=(INDEX,))
        result = current(context(
            make_manifest, tree, {"instruction_files": ["AGENTS.md"]}, host=(CLAUDE,)
        ))
        assert result.verdict.ok
        assert "carries the guidelines/index block (AGENTS.md)" in result.verdict.detail

    def test_with_no_instruction_file_the_fix_names_the_key(self, make_manifest, tree):
        """A host kind with no adapter has no default instruction file."""
        render(make_manifest, tree)
        detail = current(context(make_manifest, tree, host=())).verdict.detail
        assert "no instruction file is configured" in detail
        assert "set guidelines.instruction_files" in detail
        assert FIX in detail

    def test_a_failing_verdict_carries_the_note_and_still_fails_as_before(
        self, make_manifest, tree
    ):
        result = current(context(make_manifest, tree, host=(CLAUDE,)))
        assert not result.verdict.ok
        assert f"{CHECKLIST_REL} is missing" in result.verdict.message
        assert "This rule reports; it never renders." in result.verdict.detail
        assert FIX in result.verdict.detail
        assert not any("--insert" in fix for fix in result.verdict.fixes)

    def test_several_always_rules_are_counted(self, make_manifest, tree):
        second = (
            "  - id: tests.never-mock-the-clock\n"
            "    section: tests\n"
            "    title: Never mock the clock.\n"
            "    statement: Inject it.\n"
            "    severity: warn\n"
            "    phases: [always]\n"
            "    applies_to: [\"**\"]\n"
            "    check: {kind: engine, rule: test.all_pass}\n"
        )
        write(tree, RULESET_REL, WITH_ALWAYS + second)
        render(make_manifest, tree, host=(CLAUDE,))
        detail = current(context(make_manifest, tree, host=(CLAUDE,))).verdict.detail
        assert "2 always rules but no instruction file carries" in detail


class TestTheSummary:
    def test_no_ruleset_is_no_row(self, make_manifest, tmp_path):
        assert rules.always_layer_summary(context(make_manifest, tmp_path, False)) is None

    def test_a_refused_plan_is_no_row(self, make_manifest, tree):
        """The `guidelines generated` row already warns on the refusal."""
        write(tree, RULESET_REL, "version: 9\n")
        assert rules.always_layer_summary(context(make_manifest, tree, host=(CLAUDE,))) is None

    def test_a_gap_names_the_count_the_files_and_the_fix(self, make_manifest, tree):
        text = rules.always_layer_summary(context(make_manifest, tree, host=(CLAUDE,)))
        assert text.startswith(f"1 always rule but no instruction file carries the {INDEX}")
        assert f"({CLAUDE})" in text
        assert text.endswith(f"; try: {FIX}")

    def test_the_block_present_is_no_row(self, make_manifest, tree):
        render(make_manifest, tree, host=(CLAUDE,), insert=(INDEX,))
        assert rules.always_layer_summary(context(make_manifest, tree, host=(CLAUDE,))) is None


class TestTheDoctorRow:
    @pytest.fixture(autouse=True)
    def _inside(self, project, monkeypatch):
        monkeypatch.chdir(project)

    def configure(self, root, ruleset=WITH_ALWAYS):
        write_manifest(root, {
            "version": 1,
            "project": {"name": "demo", "ecosystems": ["python"]},
            "guidelines": {"ruleset": RULESET_REL},
        })
        write(root, RULESET_REL, ruleset)
        write(root, CLAUDE, OURS)

    def rows(self, capsys):
        return {c["check"]: c for c in payload(capsys)["checks"]}

    def test_it_warns_naming_the_count_and_the_fix(self, project, capsys):
        self.configure(project)
        run("guidelines", "render")
        capsys.readouterr()
        code = run("doctor")
        text = out(capsys)
        row = next(ln for ln in text.splitlines() if ROW in ln)
        assert row.lstrip().startswith("warn")
        assert "1 always rule" in row and CLAUDE in row
        assert f"try: {FIX}" in row
        run("guidelines", "render", "--insert", INDEX)
        capsys.readouterr()
        assert run("doctor") == code

    def test_json_marks_it_a_warning(self, project, capsys):
        self.configure(project)
        run("guidelines", "render")
        capsys.readouterr()
        run("--json", "doctor")
        row = self.rows(capsys)[ROW]
        assert row["ok"] is True and row["warn"] is True

    def test_the_block_present_is_no_row(self, project, capsys):
        self.configure(project)
        run("guidelines", "render", "--insert", INDEX)
        capsys.readouterr()
        run("--json", "doctor")
        assert ROW not in self.rows(capsys)

    def test_no_always_rules_is_no_row(self, project, capsys):
        self.configure(project, RULESET)
        run("guidelines", "render")
        capsys.readouterr()
        run("--json", "doctor")
        assert ROW not in self.rows(capsys)

    def test_no_ruleset_is_no_row(self, project, capsys):
        run("--json", "doctor")
        assert ROW not in self.rows(capsys)
