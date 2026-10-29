"""The edit-time guidelines advisory (IDEA_031 A3.8, ADR-012 ruling 9, D7, DN-8).

After an edit or a write lands, `core/rules.edit_advisory` projects what the
landing gate `guidelines.mechanical_clean` would count as new in that one
file: its pattern and boundary findings, with the baseline ratchet applied.
It never covers cochange, which describes the whole change. It is never a
deny, a rule event or a verdict, so no level applies. The host adapter only
formats it (tests/test_host_claude_code.py).

It is silent with no ruleset (and then reads no file and calls no git), for a
file outside every rule's applies_to, with no new finding, and for an edit
outside the session's tree. An unanswerable file, a ruleset or baseline that
does not load, and any exception inside it give one short line instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_guidelines_baseline import BASELINE_REL, entry_line, stamp
from test_guidelines_domain import RULESET_REL, write
from test_guidelines_findings import RULESET

from invariant.core import findings, rules
from invariant.core.rules import (
    ADVISED_KINDS,
    AdvisedFinding,
    EditAdvisory,
    RuleContext,
    edit_advisory,
)

PRINT = "style.no-print"
TODO = "style.no-todo"
LAYERS = "layers.app-never-imports-tools"
PYTHON = {"name": "demo", "ecosystems": ["python"]}

#: The findings ruleset, plus a cochange pair over the same files, which the
#: advisory must never evaluate.
WITH_PAIR = RULESET + """\
  - id: tests.paired
    section: style
    title: A module change touches its test
    statement: A change to app/X.py touches tests/test_X.py.
    severity: blocker
    phases: [landing]
    applies_to: ["app/*.py"]
    check: {kind: cochange, form: pair, when: {change: any}, then: {each: "tests/test_{stem}.py"}}
"""


def manifest_for(make_manifest, root, *, ruleset=RULESET_REL, baseline=None):
    cfg = {}
    if ruleset:
        cfg["ruleset"] = ruleset
    if baseline:
        cfg["baseline"] = baseline
    body = {"version": 1, "project": PYTHON}
    if cfg:
        body["guidelines"] = cfg
    return make_manifest(body, root)


def advise(manifest, root, target, *, tree=None):
    ctx = RuleContext(
        manifest=manifest, cwd=root, session={"worktree": str(tree or root)}
    )
    return edit_advisory(manifest, Path(target), lambda: ctx)


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, RULESET)
    return tmp_path


class TestWhatIsAdvised:
    def test_the_kinds_are_pattern_and_boundary(self):
        assert ADVISED_KINDS == ("pattern", "boundary")

    def test_a_new_pattern_finding_is_advised_as_plain_facts(self, make_manifest, tree):
        write(tree, "app/m.py", "x = 1\nif x:\n    print(  x )   \n")
        advisory = advise(manifest_for(make_manifest, tree), tree, tree / "app/m.py")
        assert isinstance(advisory, EditAdvisory)
        assert advisory.path == "app/m.py"
        assert advisory.problem is None
        (found,) = advisory.findings
        assert isinstance(found, AdvisedFinding)
        assert (found.rule, found.severity, found.path, found.line) == (
            PRINT, "blocker", "app/m.py", 3,
        )
        assert found.text == "print( x )"
        assert found.target is None
        assert stamp("    print(  x )   ") in found.fix
        assert PRINT in found.fix and "app/m.py" in found.fix

    def test_a_warn_finding_is_advised_with_its_severity(self, make_manifest, tree):
        write(tree, "app/m.py", "x = 1  # TODO later\n")
        advisory = advise(manifest_for(make_manifest, tree), tree, tree / "app/m.py")
        (found,) = advisory.findings
        assert (found.rule, found.severity, found.line) == (TODO, "warn", 1)
        assert found.text == "x = 1 # TODO later"

    def test_a_new_boundary_finding_names_its_target(self, make_manifest, tree):
        write(tree, "tools/x.py", "y = 1\n")
        write(tree, "app/m.py", "import tools.x\n")
        advisory = advise(manifest_for(make_manifest, tree), tree, tree / "app/m.py")
        (found,) = advisory.findings
        assert (found.rule, found.line, found.target) == (LAYERS, 1, "tools/x.py")
        assert found.text == "import tools.x"
        assert "tools/x.py" in found.fix
        assert findings.fingerprint(LAYERS, "import tools.x", "tools/x.py") in found.fix

    def test_findings_come_in_rule_then_line_order(self, make_manifest, tree):
        write(tree, "app/m.py", "print(1)  # TODO a\nprint(2)\n")
        advisory = advise(manifest_for(make_manifest, tree), tree, tree / "app/m.py")
        assert [(f.rule, f.line) for f in advisory.findings] == [
            (PRINT, 1), (PRINT, 2), (TODO, 1),
        ]

    def test_a_long_line_is_cut(self, make_manifest, tree):
        write(tree, "app/m.py", "print(" + "a" * 400 + ")\n")
        advisory = advise(manifest_for(make_manifest, tree), tree, tree / "app/m.py")
        (found,) = advisory.findings
        assert len(found.text) == rules.ADVISED_LINE_CHARS
        assert found.text.endswith("...")

    def test_a_relative_target_is_read_against_the_working_directory(
        self, make_manifest, tree
    ):
        write(tree, "app/m.py", "print(x)\n")
        advisory = advise(manifest_for(make_manifest, tree), tree, "app/m.py")
        assert [f.rule for f in advisory.findings] == [PRINT]


class TestTheRatchet:
    def test_a_baselined_finding_is_not_advised(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\n")
        write(tree, BASELINE_REL, entry_line(PRINT, "app/m.py", stamp("print(x)"), 1))
        manifest = manifest_for(make_manifest, tree, baseline=BASELINE_REL)
        assert advise(manifest, tree, tree / "app/m.py") is None

    def test_only_the_findings_beyond_the_recorded_count_are_advised(
        self, make_manifest, tree
    ):
        write(tree, "app/m.py", "print(x)\nprint(x)\n")
        write(tree, BASELINE_REL, entry_line(PRINT, "app/m.py", stamp("print(x)"), 1))
        manifest = manifest_for(make_manifest, tree, baseline=BASELINE_REL)
        (found,) = advise(manifest, tree, tree / "app/m.py").findings
        assert found.line == 2

    def test_an_excepted_finding_is_not_advised(self, make_manifest, tmp_path):
        excepted = RULESET.replace(
            "    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}\n",
            "    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}\n"
            "    exceptions:\n"
            "      - path: app/m.py\n"
            "        reason: the one place that prints\n",
        )
        assert excepted != RULESET
        write(tmp_path, RULESET_REL, excepted)
        write(tmp_path, "app/m.py", "print(x)\n")
        assert advise(manifest_for(make_manifest, tmp_path), tmp_path,
                      tmp_path / "app/m.py") is None

    def test_a_cochange_rule_is_never_advised(self, make_manifest, tmp_path):
        write(tmp_path, RULESET_REL, WITH_PAIR)
        write(tmp_path, "app/m.py", "x = 1\n")
        assert advise(manifest_for(make_manifest, tmp_path), tmp_path,
                      tmp_path / "app/m.py") is None


class TestSilence:
    def test_no_ruleset_reads_nothing_and_calls_no_git(
        self, make_manifest, tree, monkeypatch
    ):
        def forbidden(*args, **kwargs):
            raise AssertionError("the advisory must cost nothing without a ruleset")

        write(tree, "app/m.py", "print(x)\n")
        manifest = manifest_for(make_manifest, tree, ruleset=None)
        for name in ("toplevel", "main_checkout", "merge_base", "changed_lines"):
            monkeypatch.setattr(rules.git, name, forbidden)
        monkeypatch.setattr(rules, "_read_bytes", forbidden)
        monkeypatch.setattr(Path, "read_text", forbidden)
        monkeypatch.setattr(Path, "read_bytes", forbidden)

        def context():
            raise AssertionError("no context is built without a ruleset")

        assert edit_advisory(manifest, tree / "app/m.py", context) is None

    def test_a_file_outside_every_rule_is_silent(self, make_manifest, tree):
        write(tree, "lib/m.py", "print(x)\n")
        assert advise(manifest_for(make_manifest, tree), tree, tree / "lib/m.py") is None

    def test_a_file_with_no_new_finding_is_silent(self, make_manifest, tree):
        write(tree, "app/m.py", "x = 1\n")
        assert advise(manifest_for(make_manifest, tree), tree, tree / "app/m.py") is None

    def test_an_edit_outside_the_sessions_tree_is_silent(self, make_manifest, tree):
        worktree = tree / "wt"
        write(worktree, RULESET_REL, RULESET)
        write(tree, "app/m.py", "print(x)\n")
        manifest = manifest_for(make_manifest, tree)
        assert advise(manifest, tree, tree / "app/m.py", tree=worktree) is None

    def test_a_path_that_is_no_file_is_silent(self, make_manifest, tree):
        assert advise(manifest_for(make_manifest, tree), tree, tree / "app/gone.py") is None

    def test_a_file_that_is_not_text_is_silent(self, make_manifest, tree):
        (tree / "app").mkdir()
        (tree / "app" / "blob.bin").write_bytes(b"\xff\xfe print(")
        assert advise(manifest_for(make_manifest, tree), tree,
                      tree / "app/blob.bin") is None


class TestOneShortLine:
    def test_an_unanswerable_file_says_so(self, make_manifest, tree):
        write(tree, "app/notes.txt", "print(x)\n")
        advisory = advise(manifest_for(make_manifest, tree), tree, tree / "app/notes.txt")
        assert advisory.findings == ()
        assert "\n" not in advisory.problem
        assert "app/notes.txt could not be checked" in advisory.problem
        assert PRINT in advisory.problem and TODO in advisory.problem
        assert "no configured adapter can mask" in advisory.problem

    def test_an_unanswerable_rule_beside_a_finding_keeps_both(self, make_manifest, tree):
        write(tree, "tools/x.py", "y = 1\n")
        write(tree, "app/m.py", "import tools.missing\nprint(x)\n")
        advisory = advise(manifest_for(make_manifest, tree), tree, tree / "app/m.py")
        assert [(f.rule, f.line) for f in advisory.findings] == [(PRINT, 2)]
        assert LAYERS in (advisory.problem or "")
        assert PRINT not in advisory.problem
        assert "app/m.py could not be checked" in advisory.problem

    def test_a_ruleset_that_does_not_load_says_so(self, make_manifest, tmp_path):
        write(tmp_path, RULESET_REL, "version: 1\nrules: [{id: x}]\n")
        write(tmp_path, "app/m.py", "print(x)\n")
        advisory = advise(manifest_for(make_manifest, tmp_path), tmp_path,
                          tmp_path / "app/m.py")
        assert advisory.findings == ()
        assert "\n" not in advisory.problem
        assert "ruleset does not load" in advisory.problem
        assert "invariant check guidelines.ruleset_valid" in advisory.problem

    def test_a_baseline_that_does_not_load_says_so(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\n")
        manifest = manifest_for(make_manifest, tree, baseline=BASELINE_REL)
        advisory = advise(manifest, tree, tree / "app/m.py")
        assert advisory.findings == ()
        assert "\n" not in advisory.problem
        assert "baseline does not load" in advisory.problem
        assert "does not exist" in advisory.problem

    def test_an_exception_inside_is_caught(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\n")
        manifest = manifest_for(make_manifest, tree)

        def context():
            raise RuntimeError("boom")

        advisory = edit_advisory(manifest, tree / "app/m.py", context)
        assert advisory.findings == ()
        assert "could not be computed" in advisory.problem
        assert "RuntimeError" in advisory.problem
        assert "guidelines.mechanical_clean" in advisory.problem
