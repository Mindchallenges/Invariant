"""Mechanical findings and `guidelines.mechanical_clean` (IDEA_031 A3.3).

ADR-012 D4, D5, D6 and D12, and the A3 rulings DN-2, DN-3, DN-12, DN-13 and
DN-16. `core/findings` evaluates the `pattern` and `boundary` kinds over the
changed files, purely: the file bytes, the lexical mask and the references
arrive as callables, and `core/rules` does every read. A pattern matches each
line of the masked text, one finding per matching line. A boundary reads each
reference a changed file under `applies_to` makes and checks its target
against the `forbid` or `only` list. An unanswerable file is a failing
finding, never a pass. A file that is not UTF-8 is not text, and is named.

No baseline exists yet, so every finding is new. The rule fails on a new or
unanswerable blocker finding and reports warn findings inside a pass.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from test_cli import out, payload, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write

from invariant.core import findings
from invariant.core.findings import Finding, evaluate, fingerprint
from invariant.core.guidelines import parse
from invariant.core.machines import GUIDELINES_GATES, load_pipeline_machine
from invariant.core.rules import RuleContext, all_rules
from invariant.core.rules import evaluate as evaluate_rules

RULE = "guidelines.mechanical_clean"
REGISTERED = frozenset(spec.id for spec in all_rules())

RULESET = """\
version: 1
sections:
  - id: style
    title: Style
  - id: layers
    title: Layers
rules:
  - id: style.no-print
    section: style
    title: No print calls in the application
    statement: The application never prints.
    severity: blocker
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}
  - id: style.no-todo
    section: style
    title: No inline TODO
    statement: A TODO is filed as an idea, never left in a comment.
    severity: warn
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: pattern, engine: regex, in: comments, match: 'TODO'}
  - id: layers.app-never-imports-tools
    section: layers
    title: The application imports nothing from tools
    statement: No module of the application reaches into tools.
    severity: blocker
    phases: [review, landing]
    applies_to: ["app/*.py"]
    check: {kind: boundary, engine: imports, forbid: ["tools/**"]}
"""


def ruleset(text: str = RULESET, engines=None):
    return parse(text, registered=REGISTERED, engines=engines)


def rules_of(text: str = RULESET):
    return ruleset(text).rules


def rule_named(rule_id: str, text: str = RULESET):
    return ruleset(text).by_id(rule_id)


class Ref:
    """What an adapter's `references` answer carries: target, line, text."""

    def __init__(self, target: str, line: int, text: str) -> None:
        self.target, self.line, self.text = target, line, text


def code_mask(rel: str, text: str, keep: str) -> str | None:
    """A toy masker for `#` comments, so the evaluator is tested on its own."""
    if not rel.endswith(".py"):
        return None
    lines = []
    for line in text.split("\n"):
        code, hashed, comment = line.partition("#")
        if keep == "code":
            lines.append(code + " " * len(hashed + comment))
        else:
            lines.append(" " * len(code) + hashed + comment)
    return "\n".join(lines)


def run_eval(rules, files, *, refs=None, mask=code_mask):
    reads: list[str] = []

    def read(rel: str):
        reads.append(rel)
        return files.get(rel)

    def references(rel: str):
        return (refs or {}).get(rel)

    result = evaluate(rules, sorted(files), read=read, mask=mask, references=references)
    return result, reads


# -- the fingerprint --------------------------------------------------------------


class TestTheFingerprint:
    def test_it_is_twelve_hex_of_sha256_over_the_rule_and_the_key(self):
        expected = hashlib.sha256(b"style.no-print\x00print(x)").hexdigest()[:12]
        assert fingerprint("style.no-print", "print(x)") == expected

    def test_whitespace_is_collapsed_before_it_is_taken(self):
        assert findings.collapse("  print( x )\t\r") == "print( x )"
        assert findings.collapse("a   b") == "a b"

    def test_it_depends_on_the_rule_id(self):
        assert fingerprint("a", "x") != fingerprint("b", "x")

    def test_the_boundary_key_carries_the_target(self):
        assert fingerprint("r", "import t", "tools/a.py") != fingerprint(
            "r", "import t", "tools/b.py"
        )

    def test_parts_cannot_be_shifted_between_rule_and_key(self):
        assert fingerprint("ab", "c") != fingerprint("a", "bc")


class TestTheMatcher:
    def test_a_glob_matches_a_path_and_what_is_beneath_it(self):
        assert findings.matches("tools/a.py", "tools")
        assert findings.matches("tools/sub/a.py", "tools/**")
        assert findings.matches("app/x.py", "app/*.py")
        assert not findings.matches("other/x.py", "app/**")

    def test_a_glob_is_compared_in_its_canonical_spelling(self):
        assert findings.matches("tools/a.py", "./tools/")

    @pytest.mark.parametrize("glob", ["../tools/**", ".", "/abs/**"])
    def test_a_glob_naming_nothing_inside_the_project_matches_nothing(self, glob):
        assert not findings.matches("tools/a.py", glob)


# -- pattern ----------------------------------------------------------------------


class TestPattern:
    def test_one_finding_per_matching_line_with_its_line_number(self):
        rule = rule_named("style.no-print")
        text = "a = 1\nprint(a)\nb = 2\nprint(b)\n"
        result, _ = run_eval([rule], {"app/m.py": text.encode()})
        found = [f for f in result.findings if f.rule == "style.no-print"]
        assert [f.line for f in found] == [2, 4]
        assert all(f.status == "new" and f.kind == "pattern" for f in found)
        assert found[0].fingerprint == fingerprint("style.no-print", "print(a)")

    def test_the_mask_keeps_a_code_rule_out_of_comments(self):
        rule = rule_named("style.no-print")
        result, _ = run_eval([rule], {"app/m.py": b"x = 1  # print(x)\n"})
        assert result.findings == ()

    def test_a_comments_rule_reads_only_comments(self):
        rule = rule_named("style.no-todo")
        text = "TODO = 1\nx = 2  # TODO later\n"
        result, _ = run_eval([rule], {"app/m.py": text.encode()})
        assert [(f.line, f.severity) for f in result.findings] == [(2, "warn")]

    def test_the_fingerprint_is_taken_from_the_raw_line(self):
        rule = rule_named("style.no-print")
        result, _ = run_eval([rule], {"app/m.py": b"print(x)   # why\n"})
        (finding,) = result.findings
        assert finding.fingerprint == fingerprint("style.no-print", "print(x) # why")

    def test_moving_a_line_keeps_its_fingerprint(self):
        rule = rule_named("style.no-print")
        first, _ = run_eval([rule], {"app/m.py": b"print(x)\n"})
        moved, _ = run_eval([rule], {"app/m.py": b"\n\n  print(x)\n"})
        assert first.findings[0].fingerprint == moved.findings[0].fingerprint
        assert moved.findings[0].line == 3

    def test_any_reads_the_raw_text_and_asks_no_mask(self):
        text = RULESET.replace("in: code, match: '\\bprint", "in: any, match: '\\bprint")
        rule = rule_named("style.no-print", text)

        def no_mask(rel, text, keep):
            raise AssertionError("an `any` rule asks no mask")

        result, _ = run_eval([rule], {"app/m.js": b"// print(x)\n"}, mask=no_mask)
        assert [f.line for f in result.findings] == [1]

    def test_no_mask_is_an_unanswerable_failing_finding(self):
        rule = rule_named("style.no-print")
        result, _ = run_eval([rule], {"app/m.js": b"print(x)\n"})
        (finding,) = result.findings
        assert finding.status == "unanswerable"
        assert finding.line is None and finding.fingerprint is None
        assert finding.blocks
        assert "mask" in finding.note

    def test_a_mask_that_does_not_keep_the_lines_is_unanswerable(self):
        rule = rule_named("style.no-print")
        result, _ = run_eval(
            [rule], {"app/m.py": b"a\nb\n"}, mask=lambda rel, text, keep: "a"
        )
        assert [f.status for f in result.findings] == ["unanswerable"]

    def test_a_file_that_is_not_utf8_is_not_text_and_is_named(self):
        rule = rule_named("style.no-print")
        result, _ = run_eval([rule], {"app/blob.py": b"\xff\xfeprint(\n"})
        assert result.findings == ()
        assert result.not_text == ("app/blob.py",)

    def test_a_file_that_cannot_be_read_is_unanswerable(self):
        rule = rule_named("style.no-print")
        result, _ = run_eval([rule], {"app/gone.py": None})
        assert [f.status for f in result.findings] == ["unanswerable"]

    def test_only_files_under_applies_to_are_read(self):
        rule = rule_named("style.no-print")
        result, reads = run_eval([rule], {"lib/m.py": b"print(x)\n"})
        assert result.findings == () and reads == []


# -- exceptions -------------------------------------------------------------------


EXCEPTED = RULESET.replace(
    "    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}\n",
    "    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}\n"
    "    exceptions:\n"
    "      - path: app/cli.py\n"
    "        reason: the command line prints by design\n"
    "      - path: app/m.py\n"
    "        fingerprint: 'FP'\n"
    "        reason: one sanctioned print\n",
)


class TestExceptions:
    def test_a_path_exception_sets_the_file_aside_unread_and_names_it(self):
        rule = rule_named("style.no-print", EXCEPTED.replace("FP", "0" * 12))
        result, reads = run_eval([rule], {"app/cli.py": b"print(x)\n"})
        (finding,) = result.findings
        assert finding.status == "excepted" and not finding.counts
        assert finding.note == "the command line prints by design"
        assert reads == []

    def test_a_fingerprint_exception_passes_that_line_alone(self):
        fp = fingerprint("style.no-print", "print(a)")
        rule = rule_named("style.no-print", EXCEPTED.replace("FP", fp))
        result, _ = run_eval([rule], {"app/m.py": b"print(a)\nprint(b)\n"})
        assert [(f.line, f.status) for f in result.findings] == [
            (1, "excepted"),
            (2, "new"),
        ]
        assert result.findings[0].note == "one sanctioned print"

    def test_a_fingerprint_exception_never_excuses_an_unanswerable_file(self):
        fp = fingerprint("style.no-print", "print(a)")
        rule = rule_named("style.no-print", EXCEPTED.replace("FP", fp))
        result, _ = run_eval([rule], {"app/m.py": b"print(a)\n"}, mask=lambda *a: None)
        assert [f.status for f in result.findings] == ["unanswerable"]


# -- boundary ---------------------------------------------------------------------


class TestBoundary:
    def test_a_reference_into_a_forbidden_target_is_a_finding(self):
        rule = rule_named("layers.app-never-imports-tools")
        refs = {
            "app/m.py": [
                Ref("tools/helper.py", 3, "from tools import  helper"),
                Ref("app/other.py", 4, "from app import other"),
            ]
        }
        result, _ = run_eval([rule], {"app/m.py": b""}, refs=refs)
        (finding,) = result.findings
        assert (finding.kind, finding.line, finding.target) == ("boundary", 3, "tools/helper.py")
        assert finding.fingerprint == fingerprint(
            "layers.app-never-imports-tools", "from tools import helper", "tools/helper.py"
        )

    def test_an_only_list_finds_every_target_it_leaves_out(self):
        text = RULESET.replace('forbid: ["tools/**"]', 'only: ["app/*.py", "lib/**"]')
        rule = rule_named("layers.app-never-imports-tools", text)
        refs = {
            "app/m.py": [
                Ref("app/other.py", 1, "import app.other"),
                Ref("lib/x.py", 2, "import lib.x"),
                Ref("tools/t.py", 3, "import tools.t"),
            ]
        }
        result, _ = run_eval([rule], {"app/m.py": b""}, refs=refs)
        assert [f.target for f in result.findings] == ["tools/t.py"]

    def test_no_references_answer_is_an_unanswerable_failing_finding(self):
        rule = rule_named("layers.app-never-imports-tools")
        result, _ = run_eval([rule], {"app/m.py": b""}, refs={})
        (finding,) = result.findings
        assert finding.status == "unanswerable" and finding.blocks
        assert "references" in finding.note

    def test_an_empty_answer_is_a_file_importing_nothing_of_the_project(self):
        rule = rule_named("layers.app-never-imports-tools")
        result, _ = run_eval([rule], {"app/m.py": b""}, refs={"app/m.py": []})
        assert result.findings == ()

    def test_a_path_exception_sets_the_referencing_file_aside(self):
        text = RULESET.replace(
            '    check: {kind: boundary, engine: imports, forbid: ["tools/**"]}\n',
            '    check: {kind: boundary, engine: imports, forbid: ["tools/**"]}\n'
            "    exceptions:\n"
            "      - path: app/m.py\n"
            "        reason: the composition root\n",
        )
        rule = rule_named("layers.app-never-imports-tools", text)
        result, _ = run_eval([rule], {"app/m.py": b""}, refs={})
        assert [f.status for f in result.findings] == ["excepted"]


# -- the record and the summary --------------------------------------------------


class TestTheRecord:
    def test_a_finding_is_recorded_in_the_verdict_shape(self):
        finding = Finding(
            rule="style.no-print", kind="pattern", severity="blocker",
            path="app/m.py", status="new", line=2, fingerprint="0123456789ab",
        )
        assert finding.record("5be0c2d91f7a") == {
            "rule": "style.no-print",
            "kind": "pattern",
            "severity": "blocker",
            "status": "fail",
            "finding": "new",
            "citations": [{"path": "app/m.py", "lines": [2, 2]}],
            "fingerprint": "0123456789ab",
            "target": None,
            "note": "",
            "fix": "",
            "ruleset": "5be0c2d91f7a",
        }

    def test_an_excepted_finding_records_a_pass_and_no_line_when_it_has_none(self):
        finding = Finding(
            rule="r", kind="boundary", severity="warn", path="a.py", status="excepted",
            note="why",
        )
        record = finding.record("d")
        assert record["status"] == "pass"
        assert record["citations"] == [{"path": "a.py", "lines": None}]

    def test_describe_names_the_rule_the_place_and_the_target(self):
        found = Finding(
            rule="r", kind="boundary", severity="blocker", path="a.py", status="new",
            line=3, target="t.py",
        )
        assert found.describe() == "r a.py:3 -> t.py"
        lost = Finding(
            rule="r", kind="pattern", severity="blocker", path="a.js",
            status="unanswerable", note="no mask",
        )
        assert lost.describe() == "r a.js: unanswerable (no mask)"

    def test_warn_counts_and_does_not_block(self):
        finding = Finding(rule="r", kind="pattern", severity="warn", path="a", status="new")
        assert finding.counts and not finding.blocks

    def test_other_kinds_are_not_evaluated_and_read_nothing(self):
        text = RULESET + (
            "  - id: style.judged\n"
            "    section: style\n"
            "    title: Judged\n"
            "    statement: A judgement.\n"
            "    severity: blocker\n"
            "    phases: [review]\n"
            "    applies_to: [\"app/**\"]\n"
            "    check: {kind: judgement, evidence: citation, scope: diff}\n"
        )
        rule = rule_named("style.judged", text)
        result, reads = run_eval([rule], {"app/m.py": b"print(x)\n"})
        assert result.findings == () and reads == [] and result.rules == 0

    def test_it_counts_the_rules_and_files_it_scanned(self):
        result, _ = run_eval(rules_of(), {"app/m.py": b"x = 1\n"}, refs={"app/m.py": []})
        assert (result.rules, result.scanned) == (3, 1)


# -- the rule ---------------------------------------------------------------------


PYTHON = {"name": "demo", "ecosystems": ["python"]}


def context(make_manifest, root, files, *, cfg=True, **extra):
    body = {"version": 1, "project": PYTHON, **extra}
    if cfg:
        body["guidelines"] = {"ruleset": RULESET_REL}
    session = None if files is None else {"files": list(files), "base": ""}
    return RuleContext(manifest=make_manifest(body, root), cwd=root, session=session)


def clean_of(ctx):
    (result,) = evaluate_rules([RULE], ctx)
    return result


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, RULESET)
    return tmp_path


class TestTheRule:
    def test_no_ruleset_configured_is_not_applicable(self, make_manifest, tmp_path):
        result = clean_of(context(make_manifest, tmp_path, ["app/m.py"], cfg=False))
        assert result.verdict.skipped
        assert "no guidelines ruleset configured" in result.verdict.message

    @pytest.mark.parametrize(
        ("profile", "level"), [("loose", "warn"), ("standard", "warn"), ("strict", "block")]
    )
    def test_its_level_per_profile(self, make_manifest, tmp_path, profile, level):
        ctx = context(make_manifest, tmp_path, [], enforcement={"profile": profile})
        assert ctx.manifest.rule_level(RULE).value == level

    def test_a_ruleset_that_does_not_load_fails(self, make_manifest, tree):
        write(tree, RULESET_REL, RULESET.replace("severity: warn", "severity: loud"))
        result = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert not result.verdict.ok and not result.verdict.skipped
        assert "does not validate" in result.verdict.message

    def test_a_changed_set_that_cannot_be_established_fails(self, make_manifest, tree):
        result = clean_of(context(make_manifest, tree, None))
        assert not result.verdict.ok
        assert "changed set could not be established" in result.verdict.message

    def test_nothing_changed_is_clean(self, tmp_path):
        """A branch with no commits past its base is an answer: nothing changed."""
        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, RULESET)
        m = git_project(root, {"version": 1, "project": PYTHON,
                               "guidelines": {"ruleset": RULESET_REL}})
        ctx = RuleContext(manifest=m, cwd=root, session={"files": [], "base": "master"})
        result = clean_of(ctx)
        assert result.verdict.ok and not result.verdict.skipped
        assert result.verdict.findings == ()
        assert "0 changed file(s)" in result.verdict.message

    def test_a_new_blocker_finding_fails_naming_the_rule_and_line(self, make_manifest, tree):
        write(tree, "app/m.py", "x = 1\nprint(x)\n")
        result = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert not result.verdict.ok
        assert "style.no-print app/m.py:2" in result.verdict.message
        (record,) = [r for r in result.verdict.findings if r["rule"] == "style.no-print"]
        assert record["citations"] == [{"path": "app/m.py", "lines": [2, 2]}]
        assert record["ruleset"] == hashlib.sha256(RULESET.encode()).hexdigest()[:12]

    def test_it_blocks_where_the_profile_blocks(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\n")
        result = clean_of(
            context(make_manifest, tree, ["app/m.py"], enforcement={"profile": "strict"})
        )
        assert result.blocks

    def test_warn_findings_are_reported_inside_a_pass(self, make_manifest, tree):
        write(tree, "app/m.py", "x = 1  # TODO later\n")
        result = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert result.verdict.ok and not result.verdict.skipped
        assert "style.no-todo app/m.py:1" in result.verdict.detail
        assert [r["severity"] for r in result.verdict.findings] == ["warn"]

    def test_a_forbidden_import_is_a_boundary_finding(self, make_manifest, tree):
        write(tree, "tools/__init__.py", "")
        write(tree, "tools/helper.py", "X = 1\n")
        write(tree, "app/m.py", "from tools import helper\n")
        result = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert not result.verdict.ok
        assert "layers.app-never-imports-tools app/m.py:1 -> tools/helper.py" in (
            result.verdict.message
        )

    def test_a_file_no_adapter_can_read_is_unanswerable(self, make_manifest, tree):
        write(tree, "app/broken.py", "def (:\n")
        result = clean_of(context(make_manifest, tree, ["app/broken.py"]))
        assert not result.verdict.ok
        assert "unanswerable" in result.verdict.message
        assert result.verdict.fixes

    def test_a_file_that_is_not_text_is_named_in_the_verdict(self, make_manifest, tree):
        (tree / "app").mkdir()
        (tree / "app" / "blob.bin").write_bytes(b"\xff\xfe\x00print(")
        result = clean_of(context(make_manifest, tree, ["app/blob.bin"]))
        assert result.verdict.ok
        assert "app/blob.bin" in result.verdict.detail
        assert "not UTF-8 text" in result.verdict.detail

    def test_an_exception_that_applied_is_named_in_the_verdict(self, make_manifest, tree):
        write(tree, RULESET_REL, EXCEPTED.replace("FP", "abcdefabcdef"))
        write(tree, "app/cli.py", "print(x)\n")
        result = clean_of(context(make_manifest, tree, ["app/cli.py"]))
        assert result.verdict.ok
        assert "1 excepted: style.no-print app/cli.py (the command line prints by design)" in (
            result.verdict.detail
        )

    def test_a_file_that_cannot_be_read_is_unanswerable(self, make_manifest, tree):
        path = write(tree, "app/locked.py", "print(x)\n")
        path.chmod(0)
        try:
            result = clean_of(context(make_manifest, tree, ["app/locked.py"]))
        finally:
            path.chmod(0o644)
        assert not result.verdict.ok
        assert "style.no-print app/locked.py: unanswerable (the file could not be read)" in (
            result.verdict.message
        )

    def test_only_changed_files_are_scanned(self, make_manifest, tree):
        write(tree, "app/old.py", "print(x)\n")
        write(tree, "app/m.py", "x = 1\n")
        result = clean_of(context(make_manifest, tree, ["app/m.py"]))
        assert result.verdict.ok

    def test_a_changed_file_that_no_longer_exists_is_not_read(self, make_manifest, tree):
        result = clean_of(context(make_manifest, tree, ["app/deleted.py"]))
        assert result.verdict.ok

    def test_a_changed_path_is_spelled_project_relative(self, make_manifest, tree):
        write(tree, "app/m.py", "print(x)\n")
        result = clean_of(context(make_manifest, tree, ["./app//m.py"]))
        assert "style.no-print app/m.py:1" in result.verdict.message

    def test_the_session_worktree_is_the_tree_read(self, make_manifest, tmp_path):
        main, worktree = tmp_path / "main", tmp_path / "wt"
        write(main, RULESET_REL, RULESET)
        write(main, "app/m.py", "x = 1\n")
        write(worktree, RULESET_REL, RULESET)
        write(worktree, "app/m.py", "print(x)\n")
        ctx = context(make_manifest, main, ["app/m.py"])
        ctx.session["worktree"] = str(worktree)
        assert "app/m.py:1" in clean_of(ctx).verdict.message


class TestWhereItRuns:
    def test_it_rides_the_landing_entry_beside_the_other_guidelines_gates(self):
        assert RULE in GUIDELINES_GATES["landing"]["entry"]

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


class TestTheCommand:
    def test_check_json_prints_the_findings_in_the_record_shape(
        self, tmp_path, monkeypatch, capsys
    ):
        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, RULESET)
        git_project(root, {"version": 1, "project": PYTHON,
                           "guidelines": {"ruleset": RULESET_REL}})
        monkeypatch.chdir(root)
        assert run("session", "start", "findings") == 0
        write(root, "app/m.py", "print(x)\n")
        assert run("session", "files", "add", "app/m.py") == 0
        capsys.readouterr()
        run("check", RULE, "--json")
        (verdict,) = payload(capsys)["verdicts"]
        (record,) = verdict["findings"]
        assert record["rule"] == "style.no-print"
        assert record["finding"] == "new"
        assert record["citations"] == [{"path": "app/m.py", "lines": [1, 1]}]

    def test_check_prints_the_rule_and_line(self, tmp_path, monkeypatch, capsys):
        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, RULESET)
        git_project(root, {"version": 1, "project": PYTHON,
                           "guidelines": {"ruleset": RULESET_REL}})
        monkeypatch.chdir(root)
        run("session", "start", "findings")
        write(root, "app/m.py", "print(x)\n")
        run("session", "files", "add", "app/m.py")
        capsys.readouterr()
        run("check", RULE)
        assert "style.no-print app/m.py:1" in out(capsys)


def test_a_verdict_without_findings_prints_no_findings_key(make_manifest, tmp_path):
    from invariant.cli.main import verdicts_payload

    (result,) = evaluate_rules(
        ["guidelines.ruleset_valid"],
        RuleContext(manifest=make_manifest({"version": 1}), cwd=Path(tmp_path)),
    )
    assert "findings" not in verdicts_payload([result])[0]
