"""`guidelines.baseline_only_shrinks` (IDEA_031 A3 increment 6, ADR-012 D6).

The baseline in the change is compared with the base branch's committed copy,
read through git at the merge base, at the path the BASE's own manifest names.
An added entry or a raised count fails, unless DN-6 admits it:

- the base configured no baseline;
- the entry's rule is absent from the base's ruleset;
- the entry's rule has a different `applies_to`, `check` or `exceptions`
  there, compared structurally, not as text.

A stale entry fails it too (DN-5): an entry for a touched file whose recorded
count exceeds what the file holds now, named with `invariant guidelines
baseline --prune` as its fix. An entry follows a rename git detects (DN-7):
`--prune` rewrites the path, and the move is accepted while rule, fingerprint
and count do not grow. Deleting entries is shrinking. A base copy that does
not parse fails rather than admitting everything.

The rule is N/A with no ruleset or no baseline, rides the landing entry only
when a baseline is configured, and blocks in every profile.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml
from test_cli import payload, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write
from test_guidelines_findings import RULESET

from invariant.core import findings, git, guidelines
from invariant.core.findings import Growth, fingerprint, growth
from invariant.core.guidelines import BaselineEntry, definition_of, parse
from invariant.core.machines import (
    BASELINE_GATES,
    GUIDELINES_GATES,
    load_pipeline_machine,
)
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.rules import RuleContext, all_rules
from invariant.core.rules import evaluate as evaluate_rules

RULE = "guidelines.baseline_only_shrinks"
REGISTERED = frozenset(spec.id for spec in all_rules())
BASELINE_REL = "docs/guidelines/baseline.txt"
PRINT = "style.no-print"
TODO = "style.no-todo"
PYTHON = {"name": "demo", "ecosystems": ["python"]}
FP = "3f2a9c1b7e44"
FP2 = "0123456789ab"
PRUNE = "invariant guidelines baseline --prune"


def stamp(line: str, rule: str = PRINT) -> str:
    return fingerprint(rule, findings.collapse(line))


def entry_line(rule: str, path: str, fp: str, count: int) -> str:
    return f"{rule}  {path}  {fp}  {count}\n"


def entry(count, *, rule=PRINT, path="app/m.py", fp=FP) -> BaselineEntry:
    return BaselineEntry(rule, path, fp, count)


# -- the arithmetic ----------------------------------------------------------------


class TestGrowth:
    def test_an_unchanged_baseline_has_no_growth(self):
        assert growth([entry(2)], [entry(2)]) == ()

    def test_a_lowered_count_and_a_deleted_entry_are_shrinking(self):
        assert growth([entry(3), entry(1, fp=FP2)], [entry(1)]) == ()

    def test_an_added_entry_grows(self):
        (grown,) = growth([entry(1)], [entry(1), entry(2, fp=FP2)])
        assert grown == Growth(PRINT, "app/m.py", FP2, 0, 2)
        assert grown.describe() == f"added {PRINT} app/m.py {FP2} (count 2)"

    def test_a_raised_count_grows(self):
        (grown,) = growth([entry(1)], [entry(3)])
        assert (grown.before, grown.after) == (1, 3)
        assert grown.describe() == f"raised {PRINT} app/m.py {FP} from 1 to 3"

    def test_another_rule_or_path_is_another_entry(self):
        grown = growth([entry(1)], [entry(1, rule=TODO), entry(1, path="app/n.py")])
        assert {(g.rule, g.path) for g in grown} == {(TODO, "app/m.py"), (PRINT, "app/n.py")}

    def test_an_entry_follows_a_rename(self):
        """DN-7: the move is accepted while rule, fingerprint and count do not grow."""
        assert growth([entry(2)], [entry(2, path="app/k.py")], {"app/m.py": "app/k.py"}) == ()

    def test_a_moved_entry_may_not_grow(self):
        (grown,) = growth([entry(1)], [entry(2, path="app/k.py")], {"app/m.py": "app/k.py"})
        assert (grown.path, grown.before, grown.after) == ("app/k.py", 1, 2)

    def test_a_move_is_not_a_copy(self):
        """An entry left at the old path and one at the new share one budget."""
        tip = [entry(1), entry(1, path="app/k.py")]
        (grown,) = growth([entry(1)], tip, {"app/m.py": "app/k.py"})
        assert (grown.path, grown.before, grown.after) == ("app/k.py", 1, 2)

    def test_a_move_without_the_rename_is_an_added_entry(self):
        (grown,) = growth([entry(1)], [entry(1, path="app/k.py")])
        assert (grown.path, grown.before) == ("app/k.py", 0)


# -- structural comparison of a rule's definition (DN-6) ------------------------------


def rule_from(text: str, rule_id: str = PRINT):
    return parse(text, registered=REGISTERED).by_id(rule_id)


class TestDefinitionOf:
    def test_the_same_rule_has_the_same_definition(self):
        assert definition_of(rule_from(RULESET)) == definition_of(rule_from(RULESET))

    def test_the_spelling_of_the_yaml_does_not_matter(self):
        respelled = RULESET.replace(
            "    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}",
            "    check:\n      match: \"\\\\bprint\\\\(\"\n      in: code\n"
            "      engine: regex\n      kind: pattern",
        ).replace('applies_to: ["app/**"]', "applies_to:\n      - ./app/**", 1)
        assert respelled != RULESET
        assert definition_of(rule_from(respelled)) == definition_of(rule_from(RULESET))

    def test_the_order_of_globs_and_exceptions_does_not_matter(self):
        one = RULESET.replace('applies_to: ["app/**"]', 'applies_to: ["app/**", "lib/**"]', 1)
        two = RULESET.replace('applies_to: ["app/**"]', 'applies_to: ["lib/**", "app/**"]', 1)
        assert definition_of(rule_from(one)) == definition_of(rule_from(two))

    def test_the_order_of_a_forbid_list_does_not_matter(self):
        """Reordering a boundary's targets is not a redefinition, so it admits nothing."""
        from test_guidelines_domain import BASELINE_RULESET as DOMAIN

        boundary = "style.app-never-imports-tools"
        one = DOMAIN.replace('forbid: ["tools/**"]', 'forbid: ["tools/**", "scripts/**"]', 1)
        two = DOMAIN.replace('forbid: ["tools/**"]', 'forbid: ["scripts/**", "tools/**"]', 1)
        assert one != two
        assert definition_of(rule_from(one, boundary)) == definition_of(rule_from(two, boundary))
        assert definition_of(rule_from(one, boundary)) != definition_of(rule_from(DOMAIN, boundary))

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ('applies_to: ["app/**"]', 'applies_to: ["app/**", "lib/**"]'),
            ("match: '\\bprint\\('}", "match: '\\bprint\\s*\\('}"),
            ("in: code, match", "in: any, match"),
            (
                "    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}\n",
                (
                    "    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}\n"
                    "    exceptions: [{path: app/cli.py, reason: the console is its job}]\n"
                ),
            ),
        ],
        ids=["applies_to", "match", "in", "exceptions"],
    )
    def test_a_different_scope_check_or_exceptions_is_a_different_definition(self, old, new):
        changed = RULESET.replace(old, new, 1)
        assert changed != RULESET
        assert definition_of(rule_from(changed)) != definition_of(rule_from(RULESET))

    def test_an_exception_s_reason_is_not_the_definition(self):
        def excepted(reason):
            return RULESET.replace(
                "    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}\n",
                "    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}\n"
                f"    exceptions: [{{path: app/cli.py, reason: {reason}}}]\n",
                1,
            )

        one, two = excepted("the console"), excepted("printing is its job")
        assert one != two and "app/cli.py" in one
        assert definition_of(rule_from(one)) == definition_of(rule_from(two))

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ("title: No print calls in the application", "title: Never print"),
            ("statement: The application never prints.", "statement: It never prints."),
            ("severity: blocker", "severity: warn"),
            ("phases: [review]", "phases: [review, landing]"),
        ],
    )
    def test_wording_severity_and_phases_are_not_the_definition(self, old, new):
        changed = RULESET.replace(old, new, 1)
        assert changed != RULESET
        assert definition_of(rule_from(changed)) == definition_of(rule_from(RULESET))


REGISTRY_REL = "docs/guidelines/duplication.yaml"
REGISTRY = "version: 1\nentries: []\n"
WITH_REGISTRY = RULESET + """\
  - id: quality.duplication
    section: style
    title: A touched file is not left in an open duplication entry
    statement: Resolve the entry in the same change.
    severity: warn
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: cochange, form: registry, registry: docs/guidelines/duplication.yaml,
            outcomes: {contained: warn}}
"""


class TestParseWithAReader:
    def test_a_registry_is_read_through_the_reader(self):
        asked = []

        def read(rel):
            asked.append(rel)
            return REGISTRY.encode() if rel == REGISTRY_REL else None

        ruleset = parse(WITH_REGISTRY, registered=REGISTERED, read=read)
        assert asked == [REGISTRY_REL]
        (registry,) = ruleset.registries
        assert registry.path == REGISTRY_REL

    def test_a_registry_the_reader_cannot_give_is_refused(self):
        with pytest.raises(guidelines.RulesetError, match="registry .* could not be read"):
            parse(WITH_REGISTRY, registered=REGISTERED, read=lambda rel: None)


# -- the rule, over a real repository -------------------------------------------------


def sh(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def manifest_body(baseline=BASELINE_REL, ruleset=RULESET_REL) -> dict:
    cfg = {}
    if ruleset is not None:
        cfg["ruleset"] = ruleset
    if baseline is not None:
        cfg["baseline"] = baseline
    return {"version": 1, "project": PYTHON, "guidelines": cfg}


def set_manifest(root: Path, body: dict) -> None:
    (root / MANIFEST_RELPATH).write_text(yaml.safe_dump(body), encoding="utf-8")


PRINTED = "print(x)\n"
RECORDED = entry_line(PRINT, "app/m.py", stamp("print(x)"), 1)


@pytest.fixture
def repo(tmp_path):
    """The base branch prints once in app/m.py, and its baseline records it."""
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", PRINTED)
    write(root, "app/n.py", "y = 1\n")
    write(root, BASELINE_REL, guidelines.BASELINE_HEADER + "\n" + RECORDED)
    git_project(root, manifest_body())
    sh(root, "checkout", "-q", "-b", "work")
    return root


def shrinks(root: Path, *, base="master", files=()):
    ctx = RuleContext(
        manifest=Manifest.locate(root), cwd=root, session={"files": list(files), "base": base}
    )
    (result,) = evaluate_rules([RULE], ctx)
    return result.verdict


def commit(root: Path, message: str = "change") -> None:
    sh(root, "add", "-A")
    sh(root, "commit", "-qm", message)


class TestNotApplicable:
    def test_no_ruleset_is_na(self, repo):
        set_manifest(repo, manifest_body(ruleset=None, baseline=None))
        verdict = shrinks(repo)
        assert verdict.ok and verdict.skipped
        assert "no guidelines ruleset" in verdict.message

    def test_no_baseline_is_na(self, repo):
        set_manifest(repo, manifest_body(baseline=None))
        verdict = shrinks(repo)
        assert verdict.ok and verdict.skipped
        assert "no baseline configured" in verdict.message


class TestOnlyShrinks:
    def test_an_unchanged_baseline_passes(self, repo):
        write(repo, "app/n.py", "y = 2\n")
        commit(repo)
        verdict = shrinks(repo)
        assert verdict.ok and not verdict.skipped, verdict.message
        assert "1 entr" in verdict.message

    def test_an_entry_added_by_hand_fails(self, repo):
        write(repo, "app/m.py", "print(x)\nprint(y)\n")
        write(repo, BASELINE_REL, RECORDED + entry_line(PRINT, "app/m.py", stamp("print(y)"), 1))
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok and not verdict.skipped
        assert f"added {PRINT} app/m.py {stamp('print(y)')} (count 1)" in verdict.message
        assert any("only shrinks" in fix for fix in verdict.fixes)

    def test_a_raised_count_fails(self, repo):
        write(repo, "app/m.py", "print(x)\nprint(x)\n")
        write(repo, BASELINE_REL, entry_line(PRINT, "app/m.py", stamp("print(x)"), 2))
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert f"raised {PRINT} app/m.py {stamp('print(x)')} from 1 to 2" in verdict.message

    def test_an_uncommitted_growth_is_seen(self, repo):
        write(repo, BASELINE_REL, RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        verdict = shrinks(repo)
        assert not verdict.ok
        assert f"added {TODO} app/n.py {FP2}" in verdict.message

    def test_deleting_an_entry_is_shrinking(self, repo):
        write(repo, BASELINE_REL, "")
        commit(repo)
        assert shrinks(repo).ok

    def test_deleted_comment_lines_are_harmless(self, repo):
        write(repo, BASELINE_REL, RECORDED)
        commit(repo)
        assert shrinks(repo).ok

    def test_the_base_copy_is_read_at_the_merge_base(self, repo):
        """What the base gained after the branch left is not the change's."""
        sh(repo, "checkout", "-q", "master")
        write(repo, BASELINE_REL, RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        commit(repo, "the base widened its baseline")
        sh(repo, "checkout", "-q", "work")
        write(repo, BASELINE_REL, RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert f"added {TODO} app/n.py {FP2}" in verdict.message


class TestTheBaseCopy:
    def test_pointing_the_key_at_a_new_file_is_no_amnesty(self, repo):
        set_manifest(repo, manifest_body(baseline="docs/guidelines/fresh.txt"))
        write(repo, "docs/guidelines/fresh.txt",
              RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert f"added {TODO} app/n.py {FP2}" in verdict.message
        assert BASELINE_REL in verdict.detail

    def test_moving_the_file_with_the_same_entries_passes(self, repo):
        set_manifest(repo, manifest_body(baseline="docs/guidelines/fresh.txt"))
        write(repo, "docs/guidelines/fresh.txt", RECORDED)
        commit(repo)
        assert shrinks(repo).ok

    def test_a_base_with_no_baseline_configured_admits_every_entry(self, tmp_path):
        """DN-6: the adoption change records the first baseline."""
        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, RULESET)
        write(root, "app/m.py", PRINTED)
        git_project(root, manifest_body(baseline=None))
        sh(root, "checkout", "-q", "-b", "work")
        set_manifest(root, manifest_body())
        write(root, BASELINE_REL, RECORDED)
        commit(root)
        verdict = shrinks(root)
        assert verdict.ok and not verdict.skipped, verdict.message
        assert "no baseline configured" in verdict.detail
        assert "admitted" in verdict.detail

    def test_a_base_with_no_manifest_admits_every_entry(self, tmp_path):
        root = tmp_path / "proj"
        root.mkdir()
        sh_init = ["init", "-q", "-b", "master"]
        sh(root, *sh_init)
        sh(root, "config", "user.email", "t@example.invalid")
        sh(root, "config", "user.name", "T")
        write(root, "README.md", "x\n")
        commit(root, "before invariant")
        sh(root, "checkout", "-q", "-b", "work")
        write(root, RULESET_REL, RULESET)
        write(root, "app/m.py", PRINTED)
        write(root, BASELINE_REL, RECORDED)
        (root / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
        set_manifest(root, manifest_body())
        commit(root)
        verdict = shrinks(root)
        assert verdict.ok, verdict.message
        assert "admitted" in verdict.detail

    def test_a_base_copy_that_does_not_parse_fails(self, repo):
        sh(repo, "checkout", "-q", "master")
        write(repo, BASELINE_REL, "not a baseline\n")
        commit(repo, "broken")
        sh(repo, "checkout", "-q", "-B", "work")
        write(repo, BASELINE_REL, RECORDED)
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert "base branch's baseline" in verdict.message
        assert "does not parse" in verdict.message
        assert "fewer than four fields" in verdict.detail

    def test_a_base_copy_that_is_missing_fails(self, repo):
        sh(repo, "checkout", "-q", "master")
        sh(repo, "rm", "-q", BASELINE_REL)
        commit(repo, "lost")
        sh(repo, "checkout", "-q", "-B", "work")
        write(repo, BASELINE_REL, RECORDED)
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert "does not exist at the merge base" in verdict.message

    def test_a_base_manifest_that_does_not_parse_fails(self, repo):
        sh(repo, "checkout", "-q", "master")
        (repo / MANIFEST_RELPATH).write_text("guidelines: [unclosed\n", encoding="utf-8")
        commit(repo, "broken manifest")
        sh(repo, "checkout", "-q", "-B", "work")
        set_manifest(repo, manifest_body())
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert "base branch's manifest" in verdict.message

    @pytest.mark.parametrize(
        "guidelines_value",
        [["a list"], {"baseline": 7}, {"baseline": ""}],
        ids=["not-a-mapping", "not-a-path", "empty"],
    )
    def test_a_base_manifest_with_a_bad_guidelines_value_fails(self, repo, guidelines_value):
        sh(repo, "checkout", "-q", "master")
        body = manifest_body()
        body["guidelines"] = guidelines_value
        (repo / MANIFEST_RELPATH).write_text(yaml.safe_dump(body), encoding="utf-8")
        commit(repo, "odd manifest")
        sh(repo, "checkout", "-q", "-B", "work")
        set_manifest(repo, manifest_body())
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert "base branch's manifest" in verdict.message


class TestAdmission:
    """DN-6: growth is admitted for a rule the change introduces or redefines."""

    NEW_RULE = """\
  - id: style.no-eval
    section: style
    title: No eval
    statement: Never eval.
    severity: blocker
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: pattern, engine: regex, in: code, match: '\\beval\\('}
"""

    def test_an_entry_for_a_rule_new_in_the_change_is_admitted(self, repo):
        write(repo, RULESET_REL, RULESET + self.NEW_RULE)
        write(repo, "app/n.py", "y = eval('1')\n")
        write(repo, BASELINE_REL, RECORDED + entry_line(
            "style.no-eval", "app/n.py", stamp("y = eval('1')", "style.no-eval"), 1))
        commit(repo)
        verdict = shrinks(repo)
        assert verdict.ok, verdict.message
        assert "admitted" in verdict.detail
        assert "style.no-eval" in verdict.detail
        assert "absent from the base branch's ruleset" in verdict.detail

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            ('applies_to: ["app/**"]', 'applies_to: ["app/**", "lib/**"]'),
            ("match: '\\bprint\\('}", "match: '\\bprint\\s*\\('}"),
        ],
        ids=["widened", "check"],
    )
    def test_an_entry_for_a_redefined_rule_is_admitted(self, repo, old, new):
        write(repo, RULESET_REL, RULESET.replace(old, new, 1))
        write(repo, "app/m.py", "print(x)\nprint(y)\n")
        write(repo, BASELINE_REL, RECORDED + entry_line(PRINT, "app/m.py", stamp("print(y)"), 1))
        commit(repo)
        verdict = shrinks(repo)
        assert verdict.ok, verdict.message
        assert "admitted" in verdict.detail
        assert "a different applies_to, check or exceptions" in verdict.detail

    def test_rewording_a_rule_admits_nothing(self, repo):
        write(repo, RULESET_REL, RULESET.replace(
            "title: No print calls in the application", "title: Never print", 1))
        write(repo, BASELINE_REL, RECORDED + entry_line(PRINT, "app/m.py", FP2, 1))
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert f"added {PRINT} app/m.py {FP2}" in verdict.message

    def test_admission_reads_a_base_ruleset_that_names_a_registry(self, tmp_path):
        root = tmp_path / "proj"
        root.mkdir()
        write(root, RULESET_REL, WITH_REGISTRY)
        write(root, REGISTRY_REL, REGISTRY)
        write(root, "app/m.py", PRINTED)
        write(root, BASELINE_REL, RECORDED)
        git_project(root, manifest_body())
        sh(root, "checkout", "-q", "-b", "work")
        write(root, BASELINE_REL, RECORDED + entry_line(TODO, "app/m.py", FP2, 1))
        commit(root)
        verdict = shrinks(root)
        assert not verdict.ok
        assert f"added {TODO} app/m.py {FP2}" in verdict.message

    def test_a_base_ruleset_that_does_not_parse_fails_an_addition(self, repo):
        sh(repo, "checkout", "-q", "master")
        write(repo, RULESET_REL, "version: 1\nsections: []\nrules: [{id: x}]\n")
        commit(repo, "broken ruleset")
        sh(repo, "checkout", "-q", "-B", "work")
        write(repo, RULESET_REL, RULESET)
        write(repo, BASELINE_REL, RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert "base branch's ruleset" in verdict.message

    def test_a_base_with_a_baseline_and_no_ruleset_admits_nothing_it_cannot_compare(
        self, repo
    ):
        """A base that configured a baseline and no ruleset had no rules, so
        every rule is absent from it, and every addition is admitted."""
        sh(repo, "checkout", "-q", "master")
        set_manifest(repo, manifest_body(ruleset=None))
        commit(repo, "no ruleset")
        sh(repo, "checkout", "-q", "-B", "work")
        set_manifest(repo, manifest_body())
        write(repo, BASELINE_REL, RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        commit(repo)
        verdict = shrinks(repo)
        assert verdict.ok, verdict.message
        assert "absent from the base branch's ruleset" in verdict.detail


class TestStale:
    """DN-5: a stale entry fails here, with the fix that prunes it."""

    def test_a_fixed_finding_that_was_not_pruned_fails(self, repo):
        write(repo, "app/m.py", "x = 1\n")
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert f"{PRINT} app/m.py {stamp('print(x)')} (recorded 1, found 0)" in verdict.message
        assert PRUNE in verdict.fixes

    def test_pruning_it_passes(self, repo, monkeypatch):
        write(repo, "app/m.py", "x = 1\n")
        commit(repo)
        monkeypatch.chdir(repo)
        assert run("session", "start", "fix", "--base", "master") == 0
        assert run("guidelines", "baseline", "--prune") == 0
        commit(repo, "prune")
        assert shrinks(repo).ok

    def test_a_deleted_file_with_an_entry_is_stale(self, repo):
        sh(repo, "rm", "-q", "app/m.py")
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert "(recorded 1, found 0)" in verdict.message

    def test_an_untouched_file_is_never_stale(self, repo):
        write(repo, "app/n.py", "y = 3\n")
        commit(repo)
        assert shrinks(repo).ok

    def test_stale_and_growth_are_both_named(self, repo):
        write(repo, "app/m.py", "x = 1\n")
        write(repo, BASELINE_REL, RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert "added" in verdict.message and "recorded 1, found 0" in verdict.message
        assert PRUNE in verdict.fixes


class TestRenames:
    """DN-7: an entry follows a rename git detects from the merge base."""

    def test_a_pruned_rename_passes(self, repo, monkeypatch):
        sh(repo, "mv", "app/m.py", "app/k.py")
        commit(repo)
        monkeypatch.chdir(repo)
        assert run("session", "start", "move", "--base", "master") == 0
        assert run("guidelines", "baseline", "--prune") == 0
        assert "app/k.py" in (repo / BASELINE_REL).read_text(encoding="utf-8")
        commit(repo, "prune")
        verdict = shrinks(repo)
        assert verdict.ok, verdict.message

    def test_an_unpruned_rename_is_stale_with_the_prune_fix(self, repo):
        sh(repo, "mv", "app/m.py", "app/k.py")
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert f"{PRINT} app/m.py" in verdict.message
        assert PRUNE in verdict.fixes

    def test_a_moved_entry_may_not_grow(self, repo):
        sh(repo, "mv", "app/m.py", "app/k.py")
        write(repo, "app/k.py", "print(x)\nprint(x)\n")
        write(repo, BASELINE_REL, entry_line(PRINT, "app/k.py", stamp("print(x)"), 2))
        commit(repo)
        verdict = shrinks(repo)
        assert not verdict.ok
        assert f"raised {PRINT} app/k.py {stamp('print(x)')} from 1 to 2" in verdict.message

    def test_renames_git_cannot_give_fail_the_rule(self, repo, monkeypatch):
        monkeypatch.setattr(git, "renamed_files", lambda cwd, base: None)
        verdict = shrinks(repo, files=["app/n.py"])
        assert not verdict.ok
        assert "renames" in verdict.message


class TestWhatFailsIt:
    def test_a_ruleset_that_does_not_load_fails(self, repo):
        write(repo, RULESET_REL, "version: 2\n")
        verdict = shrinks(repo)
        assert not verdict.ok and not verdict.skipped
        assert "does not validate" in verdict.message

    def test_a_baseline_that_does_not_parse_fails(self, repo):
        write(repo, BASELINE_REL, "not a baseline\n")
        verdict = shrinks(repo)
        assert not verdict.ok
        assert "does not parse" in verdict.message

    def test_a_changed_set_that_cannot_be_established_fails(self, repo):
        verdict = shrinks(repo, base="")
        assert not verdict.ok
        assert "changed set could not be established" in verdict.message

    def test_no_base_recorded_fails(self, repo):
        verdict = shrinks(repo, base="", files=["app/n.py"])
        assert not verdict.ok
        assert "no base" in verdict.message

    def test_a_merge_base_git_cannot_find_fails(self, repo):
        verdict = shrinks(repo, base="no-such-branch", files=["app/n.py"])
        assert not verdict.ok
        assert "merge base" in verdict.message


# -- where it runs ----------------------------------------------------------------------


class TestWhereItRuns:
    def test_it_rides_the_landing_entry(self):
        assert BASELINE_GATES == {"landing": {"entry": [RULE]}}
        assert RULE not in GUIDELINES_GATES["landing"]["entry"]

    @pytest.mark.parametrize("methodology", ["tdd", "bugfix", "spec-first", "chore", "docs"])
    def test_only_with_a_ruleset_and_a_baseline(self, make_manifest, methodology):
        def machine(cfg):
            body = {"version": 1, "methodology": {"pipeline": methodology}}
            if cfg:
                body["guidelines"] = cfg
            return load_pipeline_machine(make_manifest(body))

        assert RULE not in machine(None).gate_ids()
        assert RULE not in machine({"ruleset": RULESET_REL}).gate_ids()
        assert RULE not in machine({"baseline": BASELINE_REL}).gate_ids()
        both = machine({"ruleset": RULESET_REL, "baseline": BASELINE_REL})
        (landing,) = both.states_with_role("landing")
        assert RULE in both.state(landing).entry_gates
        assert "guidelines.mechanical_clean" in both.state(landing).entry_gates

    def test_a_pinned_pipeline_with_a_baseline_keeps_it(self, make_manifest):
        manifest = make_manifest({"version": 1, "guidelines": {"baseline": BASELINE_REL}})
        machine = load_pipeline_machine(manifest, guidelines=True)
        assert RULE in machine.gate_ids()

    @pytest.mark.parametrize("profile", ["loose", "standard", "strict"])
    def test_it_blocks_in_every_profile(self, make_manifest, profile):
        manifest = make_manifest({"version": 1, "enforcement": {"profile": profile}})
        assert manifest.rule_level(RULE).value == "block"


class TestTheCommand:
    def test_check_names_the_growth(self, repo, monkeypatch, capsys):
        monkeypatch.chdir(repo)
        assert run("session", "start", "grow", "--base", "master") == 0
        write(repo, BASELINE_REL, RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        capsys.readouterr()
        run("check", RULE, "--json")
        (verdict,) = payload(capsys)["verdicts"]
        assert verdict["ok"] is False
        assert f"added {TODO} app/n.py {FP2}" in verdict["message"]


class TestEveryBaseRead:
    """Each read of the base branch that cannot be given fails, never admits."""

    def on_base(self, repo, edit) -> None:
        sh(repo, "checkout", "-q", "master")
        edit()
        commit(repo, "the base")
        sh(repo, "checkout", "-q", "-B", "work")
        set_manifest(repo, manifest_body())
        write(repo, BASELINE_REL, RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        commit(repo)

    def test_an_existence_git_cannot_give_fails(self, repo, monkeypatch):
        monkeypatch.setattr(git, "exists_at", lambda cwd, rev, path: None)
        verdict = shrinks(repo, files=["app/n.py"])
        assert not verdict.ok and "could not read the base branch's manifest" in verdict.message

    def test_bytes_git_cannot_give_fail(self, repo, monkeypatch):
        monkeypatch.setattr(git, "blob_at", lambda cwd, rev, path: None)
        verdict = shrinks(repo, files=["app/n.py"])
        assert not verdict.ok and "could not read the base branch's manifest" in verdict.message

    def test_a_base_baseline_git_cannot_read_fails(self, repo, monkeypatch):
        real = git.exists_at
        monkeypatch.setattr(
            git, "exists_at",
            lambda cwd, rev, path: None if path == BASELINE_REL else real(cwd, rev, path),
        )
        verdict = shrinks(repo, files=["app/n.py"])
        assert not verdict.ok and f"baseline {BASELINE_REL}" in verdict.message

    def test_a_base_manifest_that_is_not_a_mapping_fails(self, repo):
        self.on_base(repo, lambda: (repo / MANIFEST_RELPATH).write_text("- a\n", encoding="utf-8"))
        verdict = shrinks(repo)
        assert not verdict.ok and "is not a mapping" in verdict.message

    def test_a_base_baseline_outside_the_project_fails(self, repo):
        self.on_base(repo, lambda: set_manifest(repo, manifest_body(baseline="../out.txt")))
        verdict = shrinks(repo)
        assert not verdict.ok and "nothing inside the project" in verdict.message

    def test_a_base_baseline_that_is_not_text_fails(self, repo):
        self.on_base(repo, lambda: (repo / BASELINE_REL).write_bytes(b"\xff\xfe"))
        verdict = shrinks(repo)
        assert not verdict.ok and "is not UTF-8 text" in verdict.message

    def test_a_base_ruleset_outside_the_project_fails_an_addition(self, repo):
        self.on_base(repo, lambda: set_manifest(repo, manifest_body(ruleset="../r.yaml")))
        verdict = shrinks(repo)
        assert not verdict.ok and "ruleset '../r.yaml' names nothing" in verdict.message

    def test_a_base_ruleset_that_is_missing_fails_an_addition(self, repo):
        self.on_base(repo, lambda: sh(repo, "rm", "-q", RULESET_REL))
        write(repo, RULESET_REL, RULESET)
        commit(repo, "restore")
        verdict = shrinks(repo)
        assert not verdict.ok and "does not exist at the merge base" in verdict.message

    def test_a_base_ruleset_that_is_not_text_fails_an_addition(self, repo):
        self.on_base(repo, lambda: (repo / RULESET_REL).write_bytes(b"\xff\xfe"))
        write(repo, RULESET_REL, RULESET)
        commit(repo, "restore")
        verdict = shrinks(repo)
        assert not verdict.ok and "is not UTF-8 text" in verdict.message

    def test_a_base_ruleset_git_cannot_read_fails_an_addition(self, repo, monkeypatch):
        write(repo, BASELINE_REL, RECORDED + entry_line(TODO, "app/n.py", FP2, 1))
        commit(repo)
        real = git.blob_at
        monkeypatch.setattr(
            git, "blob_at",
            lambda cwd, rev, path: None if path == RULESET_REL else real(cwd, rev, path),
        )
        verdict = shrinks(repo)
        assert not verdict.ok and f"ruleset {RULESET_REL}" in verdict.message
