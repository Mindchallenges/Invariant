"""Mechanical N/A conditions, answered by the engine (IDEA_031 A4 increment 2).

ADR-012 D5 item 5 and the check-kind amendment's DN-A12. A judgement rule may
declare `not_applicable_when` conditions. A `claim` is a sentence the agent
picks by id. A `mechanical` condition is a test the engine evaluates itself:

- `no_added_match: {in, match}`, no line the change adds in the rule's files
  matches the regex, through the mask its `in` names;
- `no_executable_change: true`, no rule file's `executable_text` changed.

The rule's files are DN-3's: the changed files present in the tree, under
`applies_to` and not path-excepted. `core/findings.na_answer` decides,
purely, over what `History` and the reads hand it, and answers true, false
or unanswerable. An `na` naming a mechanical condition holds only when the
engine finds it true (P5); false or unanswerable refuses it, naming why.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write

from invariant.core import findings, guidelines, verdicts
from invariant.core.findings import History, NaAnswer, na_answer
from invariant.core.git import AddedLines
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, all_rules, plan_verdicts

REGISTERED = frozenset(spec.id for spec in all_rules())

RULESET = """\
version: 1
sections:
  - id: data
    title: Data safety
rules:
  - id: data.single-open
    section: data
    title: Only one store is open at a time
    statement: No code path holds two store handles at once.
    severity: blocker
    phases: [review]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: citation
      scope: diff
      not_applicable_when:
        - id: no-handle-code
          mechanical: {no_added_match: {in: code, match: '\\bopen_store\\b'}}
        - id: no-code
          mechanical: {no_executable_change: true}
        - id: any-handle
          mechanical: {no_added_match: {in: any, match: 'open_store'}}
        - id: read-only
          claim: The change only reads through an already-open store.
    exceptions:
      - path: app/legacy.py
        reason: retired next release
"""

RULESET_OBJ = guidelines.parse(RULESET, registered=REGISTERED)
RULE = RULESET_OBJ.by_id("data.single-open")


def condition(condition_id):
    return next(c for c in RULE.check.not_applicable_when if c.id == condition_id)


def code_mask(rel: str, text: str, keep: str) -> str | None:
    """A toy masker for `#` comments, `.py` only."""
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
    """The tree, the base and git's account, as the evaluator sees them."""

    def __init__(self, tree=None, base=None, lines=None):
        self.tree = dict(tree or {})
        self.base = dict(base or {})
        self.lines = lines

    def history(self, *, existed=None, before=None, executable=reduce):
        return History(
            existed=existed or (lambda rel: rel in self.base),
            before=before or (lambda rel: self.base.get(rel)),
            lines=lambda: self.lines,
            executable=executable,
        )

    def answer(self, condition_id, *, history=None, mask=code_mask, files=None, **kw):
        return na_answer(
            RULE,
            condition(condition_id),
            sorted(self.tree) if files is None else files,
            read=lambda rel: self.tree.get(rel),
            mask=mask,
            history=history if history is not None else self.history(**kw),
        )


# -- no_added_match ----------------------------------------------------------------------


class TestNoAddedMatch:
    def test_no_added_line_matching_holds(self):
        change = Change({"app/m.py": b"x = 1\ny = 2\n"}, lines={"app/m.py": AddedLines(frozenset({2}))})
        answer = change.answer("no-handle-code")
        assert answer.holds is True
        assert answer.files == ("app/m.py",)

    def test_an_added_line_matching_is_false_naming_the_line(self):
        change = Change(
            {"app/m.py": b"x = 1\nh = open_store()\n"},
            lines={"app/m.py": AddedLines(frozenset({2}))},
        )
        answer = change.answer("no-handle-code")
        assert answer.holds is False
        assert "app/m.py:2" in answer.reason

    def test_a_match_on_a_line_the_change_did_not_add_holds(self):
        change = Change(
            {"app/m.py": b"h = open_store()\ny = 2\n"},
            lines={"app/m.py": AddedLines(frozenset({2}))},
        )
        assert change.answer("no-handle-code").holds is True

    def test_a_match_inside_a_comment_is_outside_code(self):
        change = Change(
            {"app/m.py": b"y = 2  # open_store later\n"},
            lines={"app/m.py": AddedLines(frozenset({1}))},
        )
        assert change.answer("no-handle-code").holds is True
        assert change.answer("any-handle").holds is False

    def test_an_untracked_file_is_added_whole(self):
        change = Change(
            {"app/new.py": b"a = 1\nb = open_store()\n"},
            lines={"app/new.py": AddedLines(whole=True)},
        )
        answer = change.answer("no-handle-code")
        assert answer.holds is False and "app/new.py:2" in answer.reason

    def test_every_matching_line_is_named(self):
        change = Change(
            {"app/m.py": b"open_store()\nopen_store()\n", "app/n.py": b"open_store()\n"},
            lines={"app/m.py": AddedLines(whole=True), "app/n.py": AddedLines(whole=True)},
        )
        answer = change.answer("no-handle-code")
        assert all(where in answer.reason for where in ("app/m.py:1", "app/m.py:2", "app/n.py:1"))

    def test_a_file_git_shows_unchanged_holds(self):
        change = Change({"app/m.py": b"open_store()\n"}, lines={})
        assert change.answer("no-handle-code").holds is True

    def test_lines_git_cannot_give_are_unanswerable(self):
        change = Change({"app/m.py": b"x = 1\n"}, lines=None)
        answer = change.answer("no-handle-code")
        assert answer.holds is None
        assert "could not be read from git" in answer.reason

    def test_no_history_is_unanswerable(self):
        change = Change({"app/m.py": b"x = 1\n"})
        answer = na_answer(
            RULE, condition("no-handle-code"), ["app/m.py"],
            read=change.tree.get, mask=code_mask, history=None,
        )
        assert answer.holds is None
        assert "was not read" in answer.reason

    def test_no_mask_is_unanswerable_never_a_pass(self):
        change = Change({"app/m.py": b"x = 1\n"}, lines={"app/m.py": AddedLines(whole=True)})
        answer = change.answer("no-handle-code", mask=lambda rel, text, keep: None)
        assert answer.holds is None
        assert "mask code" in answer.reason

    def test_a_mask_that_moves_lines_is_unanswerable(self):
        change = Change({"app/m.py": b"x = 1\ny = 2\n"}, lines={"app/m.py": AddedLines(whole=True)})
        answer = change.answer("no-handle-code", mask=lambda rel, text, keep: "x")
        assert answer.holds is None
        assert "did not keep the file's lines" in answer.reason

    def test_in_any_asks_no_mask(self):
        change = Change({"app/m.py": b"x = 1\n"}, lines={"app/m.py": AddedLines(whole=True)})
        assert change.answer("any-handle", mask=lambda rel, text, keep: None).holds is True

    def test_a_file_that_cannot_be_read_is_unanswerable(self):
        change = Change({"app/m.py": None}, lines={"app/m.py": AddedLines(whole=True)})
        answer = change.answer("no-handle-code")
        assert answer.holds is None
        assert "app/m.py: the file could not be read" in answer.reason

    def test_a_file_that_is_not_text_has_no_line_to_match_and_is_named(self):
        change = Change({"app/m.py": b"\xff\xfe\x00"}, lines={"app/m.py": AddedLines(whole=True)})
        answer = change.answer("no-handle-code")
        assert answer.holds is True
        assert answer.not_text == ("app/m.py",)

    def test_a_match_beside_an_unanswerable_file_is_false(self):
        """A false answer is definite: what another file holds cannot change it."""
        change = Change(
            {"app/m.py": b"open_store()\n", "app/n.py": None},
            lines={"app/m.py": AddedLines(whole=True), "app/n.py": AddedLines(whole=True)},
        )
        answer = change.answer("no-handle-code")
        assert answer.holds is False
        assert "app/n.py" in answer.reason


# -- no_executable_change -----------------------------------------------------------------------


class TestNoExecutableChange:
    def test_a_comment_only_edit_holds(self):
        change = Change({"app/m.py": b"x = 1  # now explained\n"}, base={"app/m.py": b"x = 1\n"})
        assert change.answer("no-code").holds is True

    def test_a_code_edit_is_false_naming_the_file(self):
        change = Change({"app/m.py": b"x = 2\n"}, base={"app/m.py": b"x = 1\n"})
        answer = change.answer("no-code")
        assert answer.holds is False
        assert "app/m.py" in answer.reason

    def test_a_new_file_with_code_is_false(self):
        change = Change({"app/new.py": b"x = 1\n"})
        assert change.answer("no-code").holds is False

    def test_a_new_file_of_comments_only_holds(self):
        change = Change({"app/new.py": b"# a note\n"})
        assert change.answer("no-code").holds is True

    def test_whether_it_existed_unknown_is_unanswerable(self):
        change = Change({"app/m.py": b"x = 1\n"})
        answer = change.answer("no-code", existed=lambda rel: None)
        assert answer.holds is None
        assert "existed where the branch left its base" in answer.reason

    def test_a_base_blob_that_cannot_be_read_is_unanswerable(self):
        change = Change({"app/m.py": b"x = 1\n"}, base={"app/m.py": b"x = 1\n"})
        answer = change.answer("no-code", before=lambda rel: None)
        assert answer.holds is None
        assert "at its base" in answer.reason

    def test_no_reduction_is_unanswerable_never_a_pass(self):
        change = Change({"app/m.py": b"x = 1\n"}, base={"app/m.py": b"x = 1\n"})
        answer = change.answer("no-code", executable=lambda rel, text: None)
        assert answer.holds is None
        assert "executable text" in answer.reason

    def test_a_file_that_is_not_text_is_unanswerable(self):
        change = Change({"app/m.py": b"\xff\xfe"}, base={"app/m.py": b"x = 1\n"})
        answer = change.answer("no-code")
        assert answer.holds is None
        assert "not UTF-8" in answer.reason

    def test_no_history_is_unanswerable(self):
        answer = na_answer(
            RULE, condition("no-code"), ["app/m.py"],
            read=lambda rel: b"x = 1\n", mask=code_mask, history=None,
        )
        assert answer.holds is None


# -- the rule's files (DN-3) ---------------------------------------------------------------------


class TestTheRulesFiles:
    def test_only_files_under_applies_to_are_asked(self):
        change = Change(
            {"app/m.py": b"x = 1\n", "docs/notes.py": b"open_store()\n"},
            base={"app/m.py": b"x = 1\n"},
            lines={"docs/notes.py": AddedLines(whole=True)},
        )
        assert change.answer("no-handle-code").holds is True
        assert change.answer("no-handle-code").files == ("app/m.py",)

    def test_a_path_excepted_file_is_not_asked(self):
        change = Change(
            {"app/legacy.py": b"open_store()\n"},
            lines={"app/legacy.py": AddedLines(whole=True)},
        )
        answer = change.answer("no-handle-code")
        assert answer.holds is True and answer.files == ()

    def test_files_are_asked_in_sorted_order_once(self):
        change = Change({"app/b.py": b"x = 2\n", "app/a.py": b"x = 2\n"})
        answer = change.answer("no-code", files=["app/b.py", "app/a.py", "app/b.py"])
        assert answer.files == ("app/a.py", "app/b.py")
        assert answer.reason.index("app/a.py") < answer.reason.index("app/b.py")

    def test_meets_is_the_one_spelling_of_dn3(self):
        assert findings.meets(RULE, "app/m.py")
        assert not findings.meets(RULE, "app/legacy.py")
        assert not findings.meets(RULE, "docs/m.py")
        assert verdicts.meets is findings.meets


class TestTheAnswer:
    def test_a_claim_is_not_the_engines_to_answer(self):
        answer = na_answer(
            RULE, condition("read-only"), ["app/m.py"],
            read=lambda rel: b"", mask=code_mask, history=None,
        )
        assert answer.holds is None
        assert "claim" in answer.reason

    def test_it_names_its_rule_and_condition(self):
        answer = Change({"app/m.py": b"x = 1\n"}, lines={}).answer("no-handle-code")
        assert (answer.rule, answer.condition) == ("data.single-open", "no-handle-code")

    def test_describe_says_true_false_or_unanswerable(self):
        assert NaAnswer("r", "c", True).describe() == "c holds"
        assert NaAnswer("r", "c", False, "app/m.py:2").describe() == "c is false: app/m.py:2"
        assert NaAnswer("r", "c", None, "why").describe() == "c is unanswerable: why"


# -- P5 over a real repository, through plan_verdicts ---------------------------------------------


PYTHON = {"name": "demo", "ecosystems": ["python"]}


def sh(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "x = 1\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    return root


def context(root, *, base="master", files=()) -> RuleContext:
    return RuleContext(
        manifest=Manifest.locate(root), cwd=root,
        session={"id": "s", "base": base, "files": list(files)},
        pipeline={
            "methodology": "tdd", "state": "reviewing",
            "ruleset": {"path": RULESET_REL, "digest": RULESET_OBJ.digest, "error": None},
            "evidence": {"test_runs": []},
        },
    )


def na(condition_id):
    return [{"rule": "data.single-open", "status": "na", "na_condition": condition_id}]


class TestThePlan:
    def test_a_comment_only_change_takes_na_on_no_code(self, repo):
        write(repo, "app/m.py", "x = 1  # explained\n")
        sh(repo, "commit", "-qam", "comment")
        plan = plan_verdicts(context(repo), na("no-code"))
        assert plan.ok, plan.problems
        (record,) = plan.records
        assert record.status == "na" and record.na_condition == "no-code"

    def test_a_code_change_refuses_na_on_no_code(self, repo):
        write(repo, "app/m.py", "x = 2\n")
        sh(repo, "commit", "-qam", "code")
        plan = plan_verdicts(context(repo), na("no-code"))
        assert not plan.ok
        (line,) = plan.problems
        assert "P5" in line and "false" in line and "app/m.py" in line

    def test_an_added_handle_refuses_na_on_no_handle_code(self, repo):
        write(repo, "app/m.py", "x = 1\nh = open_store()\n")
        plan = plan_verdicts(context(repo), na("no-handle-code"))
        assert not plan.ok
        assert any("P5" in line and "app/m.py:2" in line for line in plan.problems)

    def test_no_added_handle_takes_na_on_no_handle_code(self, repo):
        write(repo, "app/m.py", "x = 1\ny = 2\n")
        assert plan_verdicts(context(repo), na("no-handle-code")).ok

    def test_no_base_recorded_makes_it_unanswerable(self, repo):
        write(repo, "app/m.py", "x = 1  # explained\n")
        plan = plan_verdicts(context(repo, base="", files=["app/m.py"]), na("no-code"))
        assert not plan.ok
        assert any("P5" in line and "unanswerable" in line for line in plan.problems)

    def test_a_base_git_cannot_find_makes_it_unanswerable(self, repo):
        write(repo, "app/m.py", "x = 1\n")
        plan = plan_verdicts(
            context(repo, base="no-such-branch", files=["app/m.py"]), na("no-handle-code")
        )
        assert not plan.ok
        assert any("P5" in line and "unanswerable" in line for line in plan.problems)


# -- P5 over Facts ---------------------------------------------------------------------------------


def facts(**overrides) -> verdicts.Facts:
    values = {
        "tree_digest": RULESET_OBJ.digest, "pin": RULESET_OBJ.digest,
        "state": "reviewing", "roles": ("review",), "touched": ("app/m.py",),
        "read": lambda rel: b"x = 1\n", "added": dict,
    }
    values.update(overrides)
    return verdicts.Facts(**values)


def answering(holds, reason=""):
    seen = []

    def na(rule, cond, files):
        seen.append((rule.id, cond.id, tuple(files)))
        return NaAnswer(rule.id, cond.id, holds, reason)

    return na, seen


def check(condition_id, f):
    submitted = verdicts.Submitted(rule="data.single-open", status="na",
                                   na_condition=condition_id)
    return verdicts.check(submitted, RULE, f)


class TestP5:
    def test_a_condition_the_engine_finds_true_is_accepted(self):
        na, seen = answering(True)
        result = check("no-code", facts(na=na))
        assert result.problems == ()
        assert result.record.na_condition == "no-code"
        assert seen == [("data.single-open", "no-code", ("app/m.py",))]

    def test_a_condition_the_engine_finds_false_is_refused_naming_why(self):
        na, _ = answering(False, "app/m.py:2 adds a matching line")
        result = check("no-handle-code", facts(na=na))
        (problem,) = result.problems
        assert problem.predicate == "P5"
        assert "finds it false" in problem.message
        assert "app/m.py:2 adds a matching line" in problem.message
        assert "read-only" in problem.fix

    def test_an_unanswerable_condition_is_refused_naming_why(self):
        na, _ = answering(None, "no configured adapter can mask code in this file")
        (problem,) = check("no-handle-code", facts(na=na)).problems
        assert problem.predicate == "P5"
        assert "finds it unanswerable" in problem.message and "mask code" in problem.message

    def test_no_engine_answer_read_is_refused(self):
        (problem,) = check("no-code", facts()).problems
        assert problem.predicate == "P5" and "not read" in problem.message

    def test_a_changed_set_that_cannot_be_established_asks_no_answer(self):
        na, seen = answering(True)
        result = check("no-code", facts(na=na, touched=None))
        assert [p.predicate for p in result.problems] == ["P1", "P5"]
        assert seen == []

    def test_a_claim_is_never_put_to_the_engine(self):
        na, seen = answering(False)
        assert check("read-only", facts(na=na)).problems == ()
        assert seen == []
