"""The `cochange` kind, evaluated (IDEA_031 A3 increment 4).

ADR-012 D4 and the check-kind amendment (DN-A4 to DN-A9), and the A3 rulings
DN-4 and DN-12. "If X changed in this way, did Y change too?" Two forms:

- **pair.** `when.change` is `any` (the file is in the changed set), `added`
  (it did not exist where the branch left its base) or `executable` (the
  adapter's `executable_text` differs). `when.match` narrows it to a file
  where a line the change added matches, through the mask its `in` names.
  `then` is `all_of`, `any_of` or an `each` template, and a file satisfies it
  only when git shows it changed.
- **registry.** A changed file listed in an open entry of the registry, as
  the registry stands after the change, gives the outcome its class maps to,
  with the class's fix.

`core/findings` evaluates, purely: what git knows about the change arrives as
callables (`History`), and `core/rules` makes every call. An unanswerable
fact makes the finding unanswerable, never a pass. A cochange finding
describes the change and not the tree, so it has no fingerprint and is never
baselined (DN-4).
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest
import yaml
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write

from invariant.core import findings
from invariant.core.findings import History, evaluate
from invariant.core.git import AddedLines
from invariant.core.guidelines import parse
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.core.rules import RuleContext, all_rules
from invariant.core.rules import evaluate as evaluate_rules

RULE = "guidelines.mechanical_clean"
REGISTERED = frozenset(spec.id for spec in all_rules())
REGISTRY_REL = "docs/guidelines/dup.yaml"

RULESET = """\
version: 1
sections:
  - id: tests
    title: Tests
  - id: docs
    title: Docs
  - id: di
    title: Dependency injection
  - id: quality
    title: Quality
rules:
  - id: tests.module-has-test
    section: tests
    title: A module change touches its paired test
    statement: A change to app/X.py touches tests/test_X.py.
    severity: blocker
    phases: [landing]
    applies_to: ["app/*.py"]
    exceptions:
      - {path: app/generated.py, reason: generated from the schema}
    check:
      kind: cochange
      form: pair
      when: {change: executable}
      then: {each: "tests/test_{stem}.py"}
  - id: docs.new-module-documented
    section: docs
    title: A new module is documented
    statement: A module new on the branch comes with a docs change.
    severity: warn
    phases: [landing]
    applies_to: ["app/*.py"]
    check:
      kind: cochange
      form: pair
      when: {change: added}
      then: {any_of: ["docs/**"]}
  - id: di.member-updates-mocks
    section: di
    title: A new container member updates both mocks
    statement: A member added to the container is added to both mocks.
    severity: blocker
    phases: [landing]
    applies_to: ["di/container.py"]
    check:
      kind: cochange
      form: pair
      when: {change: any, in: code, match: '^\\s*def\\s'}
      then: {all_of: [di/mock_a.py, di/mock_b.py]}
  - id: quality.duplication-registry
    section: quality
    title: A touched file is not left in an open duplication entry
    statement: A touched file in an open entry is resolved in the same change.
    severity: blocker
    phases: [landing]
    applies_to: ["app/**", "di/**"]
    check:
      kind: cochange
      form: registry
      registry: docs/guidelines/dup.yaml
      outcomes: {contained: blocker, advisory: warn}
      fixes: {contained: stop and scope a refactor session}
"""

REGISTRY = """\
version: 1
entries:
  - id: DUP-01
    paths: [app/twin_a.py, app/twin_b.py]
    status: open
    class: contained
  - id: DUP-02
    paths: [app/near.py]
    status: open
    class: advisory
  - id: DUP-03
    paths: [app/fixed.py]
    status: resolved
    class: contained
"""


@pytest.fixture
def loaded(tmp_path):
    write(tmp_path, REGISTRY_REL, REGISTRY)
    return parse(RULESET, registered=REGISTERED, root=tmp_path)


def only(ruleset, rule_id):
    return [ruleset.by_id(rule_id)]


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


def reduce(rel: str, text: str) -> str | None:
    """A toy executable_text: comments and blank lines dropped, `.py` only."""
    if not rel.endswith(".py"):
        return None
    kept = [line.partition("#")[0].rstrip() for line in text.split("\n")]
    return "\n".join(line for line in kept if line)


class Change:
    """What a change looks like to the evaluator: the tree, the base, git."""

    def __init__(self, tree=None, base=None, lines=None, gone=()):
        self.tree = dict(tree or {})
        self.base = dict(base or {})
        self.lines = lines
        self.gone = tuple(gone)

    def history(self, *, existed=None, before=None, executable=reduce):
        return History(
            existed=existed or (lambda rel: rel in self.base),
            before=before or (lambda rel: self.base.get(rel)),
            lines=lambda: self.lines,
            executable=executable,
        )

    def run(self, rules, *, history=None, mask=code_mask, registries=None, **kw):
        changed = sorted(self.tree)
        return evaluate(
            rules,
            changed,
            read=lambda rel: self.tree.get(rel),
            mask=mask,
            references=lambda rel: [],
            gone=self.gone,
            history=history if history is not None else self.history(**kw),
            registries=registries,
        )


def touched(*paths: str, whole=()):
    """The lines answer for files git shows changed: line 1 added in each."""
    answer = {p: AddedLines(lines=frozenset({1})) for p in paths}
    answer.update({p: AddedLines(whole=True) for p in whole})
    return answer


def counted(evaluation):
    return [f for f in evaluation.findings if f.counts]


# -- pair: executable ---------------------------------------------------------------


class TestExecutable:
    def test_a_code_change_with_no_paired_test_is_a_finding(self, loaded):
        change = Change(
            tree={"app/m.py": b"x = 2\n"}, base={"app/m.py": b"x = 1\n"},
            lines=touched("app/m.py"),
        )
        (finding,) = counted(change.run(only(loaded, "tests.module-has-test")))
        assert finding.rule == "tests.module-has-test"
        assert finding.kind == "cochange"
        assert finding.status == "new" and finding.severity == "blocker"
        assert finding.path == "app/m.py" and finding.line is None
        assert "tests/test_m.py" in finding.note
        assert "tests/test_m.py" in finding.fix

    def test_the_paired_test_in_the_change_satisfies_it(self, loaded):
        change = Change(
            tree={"app/m.py": b"x = 2\n", "tests/test_m.py": b"def test(): pass\n"},
            base={"app/m.py": b"x = 1\n"},
            lines=touched("app/m.py", "tests/test_m.py"),
        )
        assert counted(change.run(only(loaded, "tests.module-has-test"))) == []

    def test_a_paired_test_git_does_not_show_changed_does_not_satisfy_it(self, loaded):
        """A declared file is in the changed set without being changed; only a
        file git shows changed satisfies a then."""
        change = Change(
            tree={"app/m.py": b"x = 2\n", "tests/test_m.py": b"def test(): pass\n"},
            base={"app/m.py": b"x = 1\n", "tests/test_m.py": b"def test(): pass\n"},
            lines=touched("app/m.py"),
        )
        assert len(counted(change.run(only(loaded, "tests.module-has-test")))) == 1

    def test_an_untracked_paired_test_satisfies_it(self, loaded):
        change = Change(
            tree={"app/m.py": b"x = 2\n", "tests/test_m.py": b"def test(): pass\n"},
            base={"app/m.py": b"x = 1\n"},
            lines=touched("app/m.py", whole=["tests/test_m.py"]),
        )
        assert counted(change.run(only(loaded, "tests.module-has-test"))) == []

    def test_a_comment_only_edit_owes_no_test(self, loaded):
        change = Change(
            tree={"app/m.py": b"x = 1  # why\n"}, base={"app/m.py": b"x = 1\n"},
            lines=touched("app/m.py"),
        )
        assert change.run(only(loaded, "tests.module-has-test")).findings == ()

    def test_a_new_module_with_code_owes_a_test(self, loaded):
        change = Change(tree={"app/new.py": b"x = 1\n"}, lines=touched("app/new.py"))
        (finding,) = counted(change.run(only(loaded, "tests.module-has-test")))
        assert "tests/test_new.py" in finding.note

    def test_a_deleted_module_with_code_owes_a_test_change(self, loaded):
        change = Change(
            base={"app/old.py": b"x = 1\n"}, gone=["app/old.py"], lines={"app/old.py": AddedLines()},
        )
        (finding,) = counted(change.run(only(loaded, "tests.module-has-test")))
        assert finding.path == "app/old.py"

    def test_a_deleted_module_and_its_deleted_test_satisfy_it(self, loaded):
        change = Change(
            base={"app/old.py": b"x = 1\n", "tests/test_old.py": b"t = 1\n"},
            gone=["app/old.py", "tests/test_old.py"],
            lines={"app/old.py": AddedLines(), "tests/test_old.py": AddedLines()},
        )
        assert counted(change.run(only(loaded, "tests.module-has-test"))) == []

    def test_a_listed_path_that_never_existed_triggers_nothing(self, loaded):
        change = Change(gone=["app/typo.py"], lines={})
        assert change.run(only(loaded, "tests.module-has-test")).findings == ()

    def test_no_reduction_is_unanswerable_never_a_pass(self, loaded):
        change = Change(
            tree={"app/m.py": b"x = 2\n"}, base={"app/m.py": b"x = 1\n"},
            lines=touched("app/m.py"),
        )
        evaluation = change.run(
            only(loaded, "tests.module-has-test"), executable=lambda rel, text: None
        )
        (finding,) = counted(evaluation)
        assert finding.status == "unanswerable"
        assert "executable text" in finding.note

    def test_a_base_that_cannot_be_read_is_unanswerable(self, loaded):
        change = Change(tree={"app/m.py": b"x = 2\n"}, lines=touched("app/m.py"))
        (finding,) = counted(
            change.run(only(loaded, "tests.module-has-test"), existed=lambda rel: None)
        )
        assert finding.status == "unanswerable"
        assert "existed" in finding.note

    def test_a_base_blob_that_cannot_be_read_is_unanswerable(self, loaded):
        change = Change(
            tree={"app/m.py": b"x = 2\n"}, base={"app/m.py": b"x = 1\n"},
            lines=touched("app/m.py"),
        )
        (finding,) = counted(
            change.run(only(loaded, "tests.module-has-test"), before=lambda rel: None)
        )
        assert finding.status == "unanswerable"

    def test_a_file_that_is_not_text_is_unanswerable(self, loaded):
        change = Change(
            tree={"app/m.py": b"\xff\xfe"}, base={"app/m.py": b"x = 1\n"},
            lines=touched("app/m.py"),
        )
        (finding,) = counted(change.run(only(loaded, "tests.module-has-test")))
        assert finding.status == "unanswerable"

    def test_a_base_blob_that_is_not_text_is_unanswerable(self, loaded):
        change = Change(
            tree={"app/m.py": b"x = 1\n"}, base={"app/m.py": b"\xff\xfe"},
            lines=touched("app/m.py"),
        )
        (finding,) = counted(change.run(only(loaded, "tests.module-has-test")))
        assert finding.status == "unanswerable"

    def test_a_file_that_cannot_be_read_is_unanswerable(self, loaded):
        change = Change(base={"app/m.py": b"x = 1\n"}, lines=touched("app/m.py"))
        evaluation = evaluate(
            only(loaded, "tests.module-has-test"), ["app/m.py"],
            read=lambda rel: None, mask=code_mask, references=lambda rel: [],
            history=change.history(),
        )
        (finding,) = counted(evaluation)
        assert finding.status == "unanswerable"
        assert "could not be read" in finding.note

    def test_lines_git_cannot_give_make_a_triggered_rule_unanswerable(self, loaded):
        change = Change(
            tree={"app/m.py": b"x = 2\n"}, base={"app/m.py": b"x = 1\n"}, lines=None,
        )
        (finding,) = counted(change.run(only(loaded, "tests.module-has-test")))
        assert finding.status == "unanswerable"
        assert "git" in finding.note

    def test_no_history_at_all_is_unanswerable(self, loaded):
        evaluation = evaluate(
            only(loaded, "tests.module-has-test"), ["app/m.py"],
            read=lambda rel: b"x = 1\n", mask=code_mask, references=lambda rel: [],
        )
        (finding,) = counted(evaluation)
        assert finding.status == "unanswerable"

    def test_a_path_exception_sets_the_trigger_aside(self, loaded):
        change = Change(tree={"app/generated.py": b"x = 1\n"}, lines=touched("app/generated.py"))
        (finding,) = change.run(only(loaded, "tests.module-has-test")).findings
        assert finding.status == "excepted"
        assert finding.note == "generated from the schema"


# -- pair: added ----------------------------------------------------------------------


class TestAdded:
    def test_a_new_module_with_no_docs_change_is_a_warn_finding(self, loaded):
        change = Change(tree={"app/new.py": b"x = 1\n"}, lines=touched("app/new.py"))
        (finding,) = counted(change.run(only(loaded, "docs.new-module-documented")))
        assert finding.severity == "warn"
        assert "docs/**" in finding.note

    def test_any_docs_change_satisfies_it(self, loaded):
        change = Change(
            tree={"app/new.py": b"x = 1\n", "docs/a.md": b"a\n"},
            lines=touched("app/new.py", "docs/a.md"),
        )
        assert counted(change.run(only(loaded, "docs.new-module-documented"))) == []

    def test_an_existing_module_is_not_added(self, loaded):
        change = Change(
            tree={"app/m.py": b"x = 2\n"}, base={"app/m.py": b"x = 1\n"},
            lines=touched("app/m.py"),
        )
        assert change.run(only(loaded, "docs.new-module-documented")).findings == ()

    def test_a_deleted_module_is_not_added(self, loaded):
        change = Change(base={"app/m.py": b"x = 1\n"}, gone=["app/m.py"], lines={})
        assert change.run(only(loaded, "docs.new-module-documented")).findings == ()

    def test_whether_it_existed_unknown_is_unanswerable(self, loaded):
        change = Change(tree={"app/new.py": b"x = 1\n"}, lines=touched("app/new.py"))
        (finding,) = counted(
            change.run(only(loaded, "docs.new-module-documented"), existed=lambda rel: None)
        )
        assert finding.status == "unanswerable"


# -- pair: any, narrowed by an added line that matches ------------------------------------


CONTAINER = b"class Container:\n    def a(self): ...\n    def b(self): ...\n"


class TestMatch:
    def test_an_added_member_owes_both_mocks(self, loaded):
        change = Change(
            tree={"di/container.py": CONTAINER},
            lines={"di/container.py": AddedLines(lines=frozenset({3}))},
        )
        (finding,) = counted(change.run(only(loaded, "di.member-updates-mocks")))
        assert "di/mock_a.py" in finding.note and "di/mock_b.py" in finding.note

    def test_all_of_names_only_what_is_missing(self, loaded):
        change = Change(
            tree={"di/container.py": CONTAINER, "di/mock_a.py": b"m\n"},
            lines={
                "di/container.py": AddedLines(lines=frozenset({3})),
                "di/mock_a.py": AddedLines(lines=frozenset({1})),
            },
        )
        (finding,) = counted(change.run(only(loaded, "di.member-updates-mocks")))
        assert "di/mock_b.py" in finding.note and "di/mock_a.py" not in finding.note

    def test_both_mocks_in_the_change_satisfy_it(self, loaded):
        change = Change(
            tree={"di/container.py": CONTAINER, "di/mock_a.py": b"m\n", "di/mock_b.py": b"m\n"},
            lines={
                "di/container.py": AddedLines(lines=frozenset({3})),
                "di/mock_a.py": AddedLines(lines=frozenset({1})),
                "di/mock_b.py": AddedLines(lines=frozenset({1})),
            },
        )
        assert counted(change.run(only(loaded, "di.member-updates-mocks"))) == []

    def test_a_match_on_a_line_the_change_did_not_add_triggers_nothing(self, loaded):
        change = Change(
            tree={"di/container.py": CONTAINER + b"x = 1\n"},
            lines={"di/container.py": AddedLines(lines=frozenset({4}))},
        )
        assert change.run(only(loaded, "di.member-updates-mocks")).findings == ()

    def test_a_match_inside_a_comment_is_outside_code(self, loaded):
        change = Change(
            tree={"di/container.py": b"class C:\n    x = 1  # def later\n"},
            lines={"di/container.py": AddedLines(lines=frozenset({2}))},
        )
        assert change.run(only(loaded, "di.member-updates-mocks")).findings == ()

    def test_a_file_git_shows_unchanged_triggers_nothing(self, loaded):
        change = Change(tree={"di/container.py": CONTAINER}, lines={})
        assert change.run(only(loaded, "di.member-updates-mocks")).findings == ()

    def test_an_untracked_file_is_added_whole(self, loaded):
        change = Change(
            tree={"di/container.py": CONTAINER}, lines=touched(whole=["di/container.py"]),
        )
        assert len(counted(change.run(only(loaded, "di.member-updates-mocks")))) == 1

    def test_a_deleted_file_adds_no_line(self, loaded):
        change = Change(
            base={"di/container.py": CONTAINER}, gone=["di/container.py"],
            lines={"di/container.py": AddedLines()},
        )
        assert change.run(only(loaded, "di.member-updates-mocks")).findings == ()

    def test_no_mask_is_unanswerable(self, loaded):
        change = Change(
            tree={"di/container.py": CONTAINER},
            lines={"di/container.py": AddedLines(lines=frozenset({3}))},
        )
        (finding,) = counted(
            change.run(only(loaded, "di.member-updates-mocks"), mask=lambda r, t, k: None)
        )
        assert finding.status == "unanswerable"
        assert "mask" in finding.note

    def test_a_mask_that_moves_lines_is_unanswerable(self, loaded):
        change = Change(
            tree={"di/container.py": CONTAINER},
            lines={"di/container.py": AddedLines(lines=frozenset({3}))},
        )
        (finding,) = counted(
            change.run(only(loaded, "di.member-updates-mocks"), mask=lambda r, t, k: "x")
        )
        assert finding.status == "unanswerable"

    def test_no_lines_is_unanswerable(self, loaded):
        change = Change(tree={"di/container.py": CONTAINER}, lines=None)
        (finding,) = counted(change.run(only(loaded, "di.member-updates-mocks")))
        assert finding.status == "unanswerable"

    def test_a_file_that_is_not_text_triggers_nothing_and_is_named(self, loaded):
        change = Change(
            tree={"di/container.py": b"\xff\xfe"}, lines=touched("di/container.py"),
        )
        evaluation = change.run(only(loaded, "di.member-updates-mocks"))
        assert evaluation.findings == ()
        assert evaluation.not_text == ("di/container.py",)

    def test_in_any_reads_the_raw_text(self, tmp_path):
        write(tmp_path, REGISTRY_REL, REGISTRY)
        text = RULESET.replace("in: code, match", "in: any, match")
        rules = only(parse(text, registered=REGISTERED, root=tmp_path), "di.member-updates-mocks")
        change = Change(
            tree={"di/container.py": CONTAINER},
            lines={"di/container.py": AddedLines(lines=frozenset({3}))},
        )
        # No mask is asked for: a mask answering None would be unanswerable.
        (finding,) = counted(change.run(rules, mask=lambda r, t, k: None))
        assert finding.status == "new"


# -- each: the path template ---------------------------------------------------------------


class TestTheTemplate:
    @pytest.mark.parametrize(
        ("template", "rel", "derived"),
        [
            ("tests/test_{stem}.py", "app/m.py", "tests/test_m.py"),
            ("tests/{dir}/test_{name}", "app/sub/m.py", "tests/app/sub/test_m.py"),
            ("{dir}/test_{stem}{suffix}", "m.py", "test_m.py"),
            ("mirror/{path}", "app/m.py", "mirror/app/m.py"),
            ("{dir}/{stem}.md", "app/m.tar.gz", "app/m.tar.md"),
        ],
    )
    def test_each_token(self, template, rel, derived):
        assert findings.derive(template, rel) == derived

    def test_a_path_that_climbs_out_is_none(self):
        assert findings.derive("{dir}/../../{name}", "m.py") is None

    def test_a_derived_path_outside_the_project_is_unanswerable(self, tmp_path):
        write(tmp_path, REGISTRY_REL, REGISTRY)
        text = RULESET.replace('"tests/test_{stem}.py"', '"{dir}/../t/{name}"')
        (rule,) = only(parse(text, registered=REGISTERED, root=tmp_path), "tests.module-has-test")
        change = Change(tree={"m.py": b"x = 1\n"}, lines=touched("m.py"))
        rules = [dataclasses.replace(rule, applies_to=("*.py",))]
        (finding,) = counted(change.run(rules))
        assert finding.status == "unanswerable"


# -- registry ---------------------------------------------------------------------------------


class TestRegistry:
    def test_a_changed_file_in_an_open_contained_entry_blocks_with_its_fix(self, loaded):
        change = Change(tree={"app/twin_a.py": b"x = 1\n"}, lines=touched("app/twin_a.py"))
        (finding,) = counted(
            change.run(only(loaded, "quality.duplication-registry"), registries=loaded.registries)
        )
        assert finding.severity == "blocker"
        assert "DUP-01" in finding.note and "contained" in finding.note
        assert finding.fix == "stop and scope a refactor session"

    def test_an_advisory_entry_warns_with_the_default_fix(self, loaded):
        change = Change(tree={"app/near.py": b"x = 1\n"}, lines=touched("app/near.py"))
        (finding,) = counted(
            change.run(only(loaded, "quality.duplication-registry"), registries=loaded.registries)
        )
        assert finding.severity == "warn"
        assert REGISTRY_REL in finding.fix

    def test_a_resolved_entry_gives_nothing(self, loaded):
        change = Change(tree={"app/fixed.py": b"x = 1\n"}, lines=touched("app/fixed.py"))
        evaluation = change.run(
            only(loaded, "quality.duplication-registry"), registries=loaded.registries
        )
        assert evaluation.findings == ()

    def test_an_unlisted_file_gives_nothing(self, loaded):
        change = Change(tree={"app/m.py": b"x = 1\n"}, lines=touched("app/m.py"))
        evaluation = change.run(
            only(loaded, "quality.duplication-registry"), registries=loaded.registries
        )
        assert evaluation.findings == ()

    def test_a_deleted_file_still_listed_is_a_finding(self, loaded):
        change = Change(
            base={"app/twin_b.py": b"x\n"}, gone=["app/twin_b.py"], lines={"app/twin_b.py": AddedLines()},
        )
        evaluation = change.run(
            only(loaded, "quality.duplication-registry"), registries=loaded.registries
        )
        assert [f.path for f in counted(evaluation)] == ["app/twin_b.py"]

    def test_a_registry_that_was_not_read_is_unanswerable(self, loaded):
        change = Change(tree={"app/twin_a.py": b"x = 1\n"}, lines=touched("app/twin_a.py"))
        (finding,) = counted(change.run(only(loaded, "quality.duplication-registry")))
        assert finding.status == "unanswerable"


# -- the record (DN-4) ------------------------------------------------------------------------


class TestTheRecord:
    def test_a_cochange_finding_has_no_fingerprint_and_no_line(self, loaded):
        change = Change(tree={"app/new.py": b"x = 1\n"}, lines=touched("app/new.py"))
        (finding,) = counted(change.run(only(loaded, "tests.module-has-test")))
        assert finding.fingerprint is None
        record = finding.record("abcdefabcdef")
        assert record["fingerprint"] is None
        assert record["citations"] == [{"path": "app/new.py", "lines": None}]
        assert record["kind"] == "cochange"
        assert record["fix"] == finding.fix

    def test_the_evaluation_counts_cochange_rules(self, loaded):
        change = Change(tree={"app/m.py": b"x = 1\n"}, lines=touched("app/m.py"))
        evaluation = change.run(loaded.rules, registries=loaded.registries)
        assert evaluation.rules == 4
        assert evaluation.scanned == 1

    def test_the_same_change_gives_the_same_findings_in_the_same_order(self, loaded):
        def once():
            change = Change(
                tree={"app/b.py": b"x\n", "app/a.py": b"y\n", "app/twin_a.py": b"z\n"},
                lines=touched("app/b.py", "app/a.py", "app/twin_a.py"),
            )
            return change.run(loaded.rules, registries=loaded.registries).findings

        first = once()
        assert first == once()
        assert [f.path for f in first if f.rule == "tests.module-has-test"] == [
            "app/a.py", "app/b.py", "app/twin_a.py",
        ]


# -- the rule, over a real repository ------------------------------------------------------


PYTHON = {"name": "demo", "ecosystems": ["python"]}


def sh(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, REGISTRY_REL, REGISTRY)
    write(root, "app/m.py", "x = 1\n")
    write(root, "tests/test_m.py", "def test(): pass\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    return root


def clean_of(root: Path, *, base="master", files=(), **manifest_extra):
    manifest = Manifest.load(root)
    if manifest_extra:
        body = yaml.safe_load((root / MANIFEST_RELPATH).read_text(encoding="utf-8"))
        body.update(manifest_extra)
        (root / MANIFEST_RELPATH).write_text(yaml.safe_dump(body), encoding="utf-8")
        manifest = Manifest.load(root)
    ctx = RuleContext(
        manifest=manifest, cwd=root, session={"files": list(files), "base": base}
    )
    (result,) = evaluate_rules([RULE], ctx)
    return result


class TestTheRule:
    def test_a_code_change_with_no_paired_test_fails(self, repo):
        write(repo, "app/m.py", "x = 2\n")
        sh(repo, "commit", "-qam", "change")
        result = clean_of(repo)
        assert not result.verdict.ok
        assert "tests.module-has-test app/m.py" in result.verdict.message
        (record,) = [r for r in result.verdict.findings if r["rule"] == "tests.module-has-test"]
        assert record["fingerprint"] is None
        assert any("tests/test_m.py" in fix for fix in result.verdict.fixes)

    def test_the_paired_test_in_the_change_passes(self, repo):
        write(repo, "app/m.py", "x = 2\n")
        write(repo, "tests/test_m.py", "def test(): assert True\n")
        sh(repo, "commit", "-qam", "change")
        result = clean_of(repo)
        assert result.verdict.ok, result.verdict.message
        assert "4 mechanical guideline(s)" in result.verdict.message

    def test_an_uncommitted_paired_test_counts(self, repo):
        write(repo, "app/m.py", "x = 2\n")
        sh(repo, "commit", "-qam", "change")
        write(repo, "tests/test_m.py", "def test(): assert True\n")
        assert clean_of(repo).verdict.ok

    def test_a_declared_but_untouched_test_does_not_count(self, repo):
        write(repo, "app/m.py", "x = 2\n")
        sh(repo, "commit", "-qam", "change")
        result = clean_of(repo, files=["tests/test_m.py"])
        assert not result.verdict.ok

    def test_a_comment_only_change_owes_no_test(self, repo):
        write(repo, "app/m.py", "x = 1  # the reason\n")
        sh(repo, "commit", "-qam", "comment")
        assert clean_of(repo).verdict.ok

    def test_a_new_module_warns_for_its_docs_inside_a_pass(self, repo):
        write(repo, "app/new.py", "y = 1\n")
        write(repo, "tests/test_new.py", "def test(): pass\n")
        sh(repo, "add", "-A")
        sh(repo, "commit", "-qm", "new")
        result = clean_of(repo)
        assert result.verdict.ok
        assert "docs.new-module-documented app/new.py" in result.verdict.detail

    def test_an_open_registry_entry_blocks_with_its_fix(self, repo):
        write(repo, "app/twin_a.py", "x = 1  # twin\n")
        write(repo, "tests/test_twin_a.py", "t = 1\n")
        write(repo, "docs/a.md", "a\n")
        sh(repo, "add", "-A")
        sh(repo, "commit", "-qm", "twin")
        result = clean_of(repo)
        assert not result.verdict.ok
        assert "quality.duplication-registry app/twin_a.py" in result.verdict.message
        assert "stop and scope a refactor session" in result.verdict.fixes

    def test_striking_the_file_from_the_registry_in_the_change_clears_it(self, repo):
        write(repo, "app/twin_a.py", "x = 1\n")
        write(repo, "tests/test_twin_a.py", "t = 1\n")
        write(repo, "docs/a.md", "a\n")
        write(repo, REGISTRY_REL, REGISTRY.replace("app/twin_a.py, ", ""))
        sh(repo, "add", "-A")
        sh(repo, "commit", "-qm", "resolved")
        assert clean_of(repo).verdict.ok, clean_of(repo).verdict.message

    def test_no_base_makes_an_executable_trigger_unanswerable(self, repo):
        write(repo, "app/m.py", "x = 2\n")
        result = clean_of(repo, base="", files=["app/m.py"])
        assert not result.verdict.ok
        assert "tests.module-has-test app/m.py: unanswerable" in result.verdict.message

    def test_it_blocks_where_the_profile_blocks(self, repo):
        write(repo, "app/m.py", "x = 2\n")
        sh(repo, "commit", "-qam", "change")
        result = clean_of(repo, enforcement={"profile": "strict"})
        assert result.blocks

    def test_a_warn_cochange_never_fails_the_rule(self, repo):
        write(repo, RULESET_REL, RULESET.replace("severity: blocker", "severity: warn").replace(
            "contained: blocker", "contained: warn"
        ))
        write(repo, "app/m.py", "x = 2\n")
        sh(repo, "commit", "-qam", "change")
        result = clean_of(repo)
        assert result.verdict.ok
        assert "tests.module-has-test app/m.py" in result.verdict.detail
