"""Judgement verdicts: the record and its submission (IDEA_031 A4 increment 1).

ADR-012 D5 and the A4 rulings DN-1 to DN-12. A judgement guideline is
answered by a cited verdict, submitted through the CLI. `core/verdicts` holds
the record, the parsing of a submission and the pure checks behind D5's
predicates; `core.rules.plan_verdicts` makes every read and asks it, so the
gate A4.3 builds can ask the same plan. A verdict is refused at submission,
naming the predicate that failed and its fix, and nothing is recorded:

- P1, the rule exists, is a judgement guideline, the ruleset in the tree is
  the one the pipeline pinned (DN-6), the change meets its `applies_to`
  through a changed file present in the tree and not path-excepted (DN-3),
  the state is a review-role state, and the rule is owed there (DN-2);
- P2, a `pass` or `fail` rests on evidence of a kind the rule allows;
- P3, each citation is a real range of a touched UTF-8 text file (DN-4,
  DN-5), and for `scope: diff` one of them meets a line the change added;
- P4, each test is a selector a recorded run named, green for a pass (DN-8);
- P5, an `na` names a claim condition and carries no evidence;
- P6, the note is capped and privacy-scanned.

A bulk submission with any invalid entry is refused whole, every problem
named per entry (DN-11).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write

from invariant.core import guidelines, verdicts
from invariant.core.git import AddedLines
from invariant.core.manifest import Manifest
from invariant.core.rules import RuleContext, VerdictRefused, all_rules, plan_verdicts
from invariant.core.verdicts import Cited, Facts, Problem, Submitted
from invariant.domains.guidelines import GuidelinesDomain
from invariant.domains.pipeline import PipelineDomain
from invariant.domains.session import SessionDomain

REGISTERED = frozenset(spec.id for spec in all_rules())

RULESET = """\
version: 1
sections:
  - id: data
    title: Data safety
  - id: style
    title: Style
rules:
  - id: data.single-open
    section: data
    title: Only one store is open at a time
    statement: No code path holds two store handles at once.
    severity: blocker
    phases: [design, review]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: citation
      scope: diff
      not_applicable_when:
        - id: no-code
          mechanical: {no_executable_change: true}
        - id: read-only
          claim: The change only reads through an already-open store.
    exceptions:
      - path: app/legacy.py
        reason: retired next release
  - id: data.tested
    section: data
    title: The store's invariant is tested
    statement: The single-open invariant is backed by a test.
    severity: blocker
    phases: [review]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: test
      scope: file
  - id: data.either
    section: data
    title: Either kind of evidence
    statement: Cite the code or name the test.
    severity: warn
    phases: [landing]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: either
      scope: file
  - id: data.both
    section: data
    title: Both kinds of evidence
    statement: Cite the code and name the test.
    severity: blocker
    phases: [always]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: both
      scope: file
  - id: data.design-only
    section: data
    title: A design-time rule
    statement: Seen while designing only.
    severity: warn
    phases: [design]
    applies_to: ["app/*.py"]
    check:
      kind: judgement
      evidence: citation
      scope: file
  - id: style.no-print
    section: style
    title: No print calls
    statement: Code never prints.
    severity: blocker
    phases: [review]
    applies_to: ["app/*.py"]
    check:
      kind: pattern
      engine: regex
      in: any
      match: 'print\\('
"""

RULESET_OBJ = guidelines.parse(RULESET, registered=REGISTERED)
DIGEST = RULESET_OBJ.digest
TREE = {
    "app/m.py": b"one\ntwo\nthree\n",
    "app/n.py": b"alpha\nbeta",
    "app/legacy.py": b"old\n",
    "docs/notes.md": b"prose\n",
    "app/blob.py": b"\xff\xfe\x00",
}
ADDED = {
    "app/m.py": AddedLines(frozenset({2})),
    "app/n.py": AddedLines(whole=True),
    "app/legacy.py": AddedLines(frozenset({1})),
    "docs/notes.md": AddedLines(frozenset({1})),
}


def rule(rule_id):
    return RULESET_OBJ.by_id(rule_id)


def green(*selectors, passed=True, read_as_red=False):
    return {"selectors": list(selectors), "passed": passed, "read_as_red": read_as_red}


def facts(**overrides) -> Facts:
    values = {
        "tree_digest": DIGEST,
        "pin": DIGEST,
        "state": "reviewing",
        "roles": ("review",),
        "touched": tuple(sorted(TREE)),
        "read": TREE.get,
        "added": lambda: ADDED,
        "test_runs": (green("tests/test_m.py::test_open"),),
        "note_max_chars": 280,
        "scan": lambda text: (),
    }
    values.update(overrides)
    return Facts(**values)


def sub(rule_id="data.single-open", status="pass", **fields) -> Submitted:
    return Submitted(rule=rule_id, status=status, **fields)


def cite(path="app/m.py", first=2, last=2, blob=None) -> Cited:
    return Cited(path=path, first=first, last=last, blob=blob)


def predicates(checked) -> list[str]:
    return [p.predicate for p in checked.problems]


def checked(submitted, f=None):
    return verdicts.check(submitted, rule(submitted.rule), f or facts(), ids=RULESET_OBJ_IDS)


RULESET_OBJ_IDS = tuple(r.id for r in RULESET_OBJ.rules)


# -- the record --------------------------------------------------------------------


class TestTheRecord:
    def test_an_accepted_verdict_is_recorded_in_d5s_shape(self):
        result = checked(sub(citations=(cite(),), note="closes before it opens"))
        assert result.problems == ()
        assert result.record.record() == {
            "rule": "data.single-open",
            "status": "pass",
            "citations": [
                {"path": "app/m.py", "lines": [2, 2],
                 "blob": guidelines.digest_of(TREE["app/m.py"])},
            ],
            "tests": [],
            "na_condition": None,
            "note": "closes before it opens",
            "ruleset": DIGEST,
            "state": "reviewing",
            "surface": "cli",
        }

    def test_the_blob_is_twelve_hex_digits_of_the_cited_files_bytes(self):
        (citation,) = checked(sub(citations=(cite(),))).record.citations
        assert citation.blob == guidelines.digest_of(TREE["app/m.py"])
        assert len(citation.blob) == 12

    def test_the_agent_is_an_extension_point_written_only_when_set(self):
        """DN-12: A4.8 stamps the acting agent; the record carries no key until then."""
        record = checked(sub(citations=(cite(),))).record
        assert record.agent is None and "agent" not in record.record()
        stamped = verdicts.VerdictRecord(**{**record.__dict__, "agent": "reviewer"})
        assert stamped.record()["agent"] == "reviewer"

    def test_the_record_carries_no_clock_time(self):
        keys = set(checked(sub(citations=(cite(),))).record.record())
        assert not keys & {"at", "time", "when", "submitted", "recorded"}

    def test_the_problem_describes_its_predicate_and_fix(self):
        problem = Problem("P3", "a message", "a fix")
        assert problem.describe() == "P3: a message; fix: a fix"


# -- parsing a submission ----------------------------------------------------------------


class TestParsing:
    def test_a_full_object_parses(self):
        submitted, problems = verdicts.parse_entry({
            "rule": "data.both", "status": "fail",
            "citations": [{"path": "app/m.py", "lines": [1, 3], "blob": "abcdefabcdef"}],
            "tests": ["t::a"], "na_condition": None, "note": "n", "ruleset": DIGEST,
        })
        assert problems == ()
        assert submitted == Submitted(
            rule="data.both", status="fail",
            citations=(Cited("app/m.py", 1, 3, "abcdefabcdef"),),
            tests=("t::a",), na_condition=None, note="n", ruleset=DIGEST,
        )

    def test_the_least_object_parses(self):
        submitted, problems = verdicts.parse_entry({"rule": "r", "status": "na"})
        assert problems == () and submitted == Submitted(rule="r", status="na")

    @pytest.mark.parametrize(
        ("raw", "needle"),
        [
            ("text", "is not an object"),
            ({"status": "pass"}, "names no rule"),
            ({"rule": "", "status": "pass"}, "names no rule"),
            ({"rule": "a b", "status": "pass"}, "names no rule"),
            ({"rule": "r"}, "status must be one of pass, fail, na"),
            ({"rule": "r", "status": "ok"}, "status must be one of pass, fail, na"),
            ({"rule": "r", "status": "pass", "state": "x"}, "unknown key state"),
            ({"rule": "r", "status": "pass", "surface": "x"}, "unknown key surface"),
            ({"rule": "r", "status": "pass", "citations": "app/m.py"}, "citations must be a list"),
            ({"rule": "r", "status": "pass", "citations": ["app/m.py"]}, "citation 1 is not an object"),
            ({"rule": "r", "status": "pass", "citations": [{"lines": [1, 1]}]}, "citation 1 names no path"),
            ({"rule": "r", "status": "pass", "citations": [{"path": "a", "lines": [1]}]}, "citation 1's lines must be two integers"),
            ({"rule": "r", "status": "pass", "citations": [{"path": "a", "lines": [1, "2"]}]}, "citation 1's lines must be two integers"),
            ({"rule": "r", "status": "pass", "citations": [{"path": "a", "lines": [True, 2]}]}, "citation 1's lines must be two integers"),
            ({"rule": "r", "status": "pass", "citations": [{"path": "a", "lines": [1, 2], "x": 1}]}, "citation 1 has unknown key x"),
            ({"rule": "r", "status": "pass", "citations": [{"path": "a", "lines": [1, 2], "blob": 3}]}, "citation 1's blob must be a string"),
            ({"rule": "r", "status": "pass", "tests": "t"}, "tests must be a list of selectors"),
            ({"rule": "r", "status": "pass", "tests": None}, "tests must be a list of selectors"),
            ({"rule": "r", "status": "pass", "citations": None}, "citations must be a list"),
            ({"rule": "r", "status": "pass", "tests": [""]}, "tests must be a list of selectors"),
            ({"rule": "r", "status": "na", "na_condition": ""}, "na_condition must be null or a condition id"),
            ({"rule": "r", "status": "pass", "note": 3}, "note must be null or a string"),
            ({"rule": "r", "status": "pass", "ruleset": 3}, "ruleset must be null or a digest"),
        ],
    )
    def test_a_malformed_entry_is_refused_naming_why(self, raw, needle):
        submitted, problems = verdicts.parse_entry(raw)
        assert submitted is None
        assert any(needle in p.message for p in problems), problems
        assert {p.predicate for p in problems} == {"shape"}

    def test_every_shape_problem_is_named(self):
        _, problems = verdicts.parse_entry({"rule": "", "status": "ok", "x": 1})
        assert len(problems) == 3

    def test_a_submission_is_a_list(self):
        parsed = verdicts.parse_submission({"rule": "r"})
        assert parsed.entries == ()
        assert [p.message for p in parsed.problems] == [
            "a submission is a JSON list of verdict objects"
        ]

    def test_an_empty_submission_is_refused(self):
        parsed = verdicts.parse_submission([])
        assert [p.message for p in parsed.problems] == ["the submission holds no verdict"]

    def test_each_entry_is_parsed_and_numbered_from_one(self):
        parsed = verdicts.parse_submission([{"rule": "a", "status": "pass"}, "x"])
        assert [e.index for e in parsed.entries] == [1, 2]
        assert parsed.entries[0].submitted is not None
        assert parsed.entries[1].submitted is None and parsed.entries[1].problems


# -- P1, the rule exists and applies ------------------------------------------------------


class TestP1:
    def test_an_unknown_rule_is_refused_with_the_nearest_id(self):
        result = checked(sub("data.single-opn", citations=(cite(),)))
        assert predicates(result) == ["P1"]
        assert "no guideline 'data.single-opn'" in result.problems[0].message
        assert "data.single-open" in result.problems[0].fix

    def test_a_mechanical_guideline_takes_no_verdict(self):
        result = checked(sub("style.no-print", citations=(cite(),)))
        assert predicates(result) == ["P1"]
        assert "pattern guideline" in result.problems[0].message
        assert "invariant check guidelines.mechanical_clean" in result.problems[0].fix

    def test_a_ruleset_edited_since_the_pin_is_refused_naming_both(self):
        """DN-6."""
        result = checked(sub(citations=(cite(),)), facts(tree_digest="0123456789ab"))
        assert "P1" in predicates(result)
        message = next(p.message for p in result.problems if p.predicate == "P1")
        assert "0123456789ab" in message and DIGEST in message
        assert result.record is None

    @pytest.mark.parametrize("pin", [None])
    def test_no_pin_or_a_null_digest_compares_against_the_tree(self, pin):
        """DN-6: with nothing pinned, the tree's digest is the one recorded."""
        result = checked(sub(citations=(cite(),)), facts(pin=pin))
        assert result.problems == ()
        assert result.record.ruleset == DIGEST

    def test_a_submitted_ruleset_must_be_the_trees(self):
        result = checked(sub(citations=(cite(),), ruleset="0123456789ab"))
        assert predicates(result) == ["P1"]
        assert "0123456789ab" in result.problems[0].message

    def test_a_submitted_ruleset_equal_to_the_trees_is_accepted(self):
        assert checked(sub(citations=(cite(),), ruleset=DIGEST)).problems == ()

    def test_a_state_without_the_review_role_is_refused(self):
        """DN-2: verdicts are accepted only in a review-role state."""
        result = checked(sub(citations=(cite(),)), facts(state="green", roles=()))
        assert predicates(result) == ["P1"]
        assert "'green'" in result.problems[0].message
        assert "review" in result.problems[0].fix

    def test_a_landing_state_is_not_a_review_state(self):
        result = checked(sub(citations=(cite(),)), facts(state="committed", roles=("landing",)))
        assert predicates(result) == ["P1"]

    def test_roles_that_cannot_be_read_are_refused(self):
        result = checked(
            sub(citations=(cite(),)), facts(roles=(), roles_problem="machine 'x' is gone")
        )
        assert predicates(result) == ["P1"]
        assert "machine 'x' is gone" in result.problems[0].message

    def test_a_rule_whose_phases_owe_no_verdict_is_refused(self):
        result = checked(sub("data.design-only", citations=(cite(),)))
        assert predicates(result) == ["P1"]
        assert "design" in result.problems[0].message

    @pytest.mark.parametrize("rule_id", ["data.single-open", "data.either", "data.both"])
    def test_review_landing_and_always_are_owed(self, rule_id):
        submitted = sub(rule_id, citations=(cite(),), tests=("tests/test_m.py::test_open",))
        if rule_id == "data.single-open":
            submitted = sub(rule_id, citations=(cite(),))
        assert "P1" not in predicates(checked(submitted))

    def test_a_change_that_touches_nothing_under_applies_to_is_refused(self):
        """DN-3."""
        result = checked(
            sub(citations=(cite("docs/notes.md", 1, 1),)), facts(touched=("docs/notes.md",))
        )
        assert "P1" in predicates(result)
        assert "applies_to" in next(p.message for p in result.problems if p.predicate == "P1")

    def test_a_path_excepted_file_does_not_meet_applies_to(self):
        """DN-3: only a file under applies_to and not path-excepted counts."""
        result = checked(
            sub(citations=(cite("app/legacy.py", 1, 1),)), facts(touched=("app/legacy.py",))
        )
        assert "P1" in predicates(result)

    def test_a_deleted_file_does_not_meet_applies_to(self):
        """DN-3: the touched set holds only files present in the tree."""
        result = checked(
            sub(citations=(cite("docs/notes.md", 1, 1),)),
            facts(touched=("docs/notes.md",), read=lambda rel: TREE.get(rel)),
        )
        assert "P1" in predicates(result)

    def test_a_change_that_cannot_be_established_is_refused(self):
        result = checked(sub(citations=(cite(),)), facts(touched=None))
        assert "P1" in predicates(result)
        assert "could not be established" in result.problems[0].message


# -- P2, evidence of an allowed kind ---------------------------------------------------------


class TestP2:
    def test_citation_evidence_needs_a_citation(self):
        result = checked(sub())
        assert predicates(result) == ["P2"]
        assert "citation" in result.problems[0].message

    def test_citation_evidence_takes_no_test(self):
        result = checked(sub(citations=(cite(),), tests=("tests/test_m.py::test_open",)))
        assert predicates(result) == ["P2"]

    def test_test_evidence_needs_a_test(self):
        assert predicates(checked(sub("data.tested"))) == ["P2"]

    def test_test_evidence_takes_no_citation(self):
        result = checked(
            sub("data.tested", citations=(cite(),), tests=("tests/test_m.py::test_open",))
        )
        assert predicates(result) == ["P2"]

    def test_test_evidence_alone_is_accepted(self):
        assert checked(sub("data.tested", tests=("tests/test_m.py::test_open",))).problems == ()

    @pytest.mark.parametrize(
        "evidence",
        [
            {"citations": (cite(),)},
            {"tests": ("tests/test_m.py::test_open",)},
            {"citations": (cite(),), "tests": ("tests/test_m.py::test_open",)},
        ],
    )
    def test_either_takes_either_or_both(self, evidence):
        assert checked(sub("data.either", **evidence)).problems == ()

    def test_either_needs_one(self):
        assert predicates(checked(sub("data.either"))) == ["P2"]

    @pytest.mark.parametrize(
        "evidence", [{"citations": (cite(),)}, {"tests": ("tests/test_m.py::test_open",)}, {}]
    )
    def test_both_needs_both(self, evidence):
        assert predicates(checked(sub("data.both", **evidence))) == ["P2"]

    def test_both_with_both_is_accepted(self):
        result = checked(
            sub("data.both", status="fail", citations=(cite(),),
                tests=("tests/test_m.py::test_open",))
        )
        assert result.problems == ()

    def test_a_fail_needs_evidence_too(self):
        assert predicates(checked(sub(status="fail"))) == ["P2"]


# -- P3, each citation is real ------------------------------------------------------------


class TestP3:
    @pytest.mark.parametrize(
        ("path", "canonical"),
        [("./app/m.py", "app/m.py"), ("app//m.py", "app/m.py"), ("app/x/../m.py", "app/m.py")],
    )
    def test_a_path_not_spelled_project_relative_is_refused(self, path, canonical):
        result = checked(sub(citations=(cite(path),)))
        assert "P3" in predicates(result)
        assert canonical in next(p.fix for p in result.problems if p.predicate == "P3")

    @pytest.mark.parametrize("path", ["/abs/app/m.py", "../m.py", ".", " app/m.py"])
    def test_a_path_outside_the_project_or_badly_spelled_is_refused(self, path):
        assert "P3" in predicates(checked(sub(citations=(cite(path),))))

    def test_a_file_outside_the_touched_set_is_refused(self):
        """DN-4: a citation points into a touched file."""
        result = checked(sub(citations=(cite("app/other.py", 1, 1),)))
        assert "P3" in predicates(result)
        assert "touched" in result.problems[0].message

    def test_any_touched_file_may_be_cited_under_applies_to_or_not(self):
        """DN-5."""
        result = checked(sub(citations=(cite("docs/notes.md", 1, 1),)))
        assert result.problems == ()

    def test_a_file_that_cannot_be_read_is_refused(self):
        result = checked(sub(citations=(cite(),)), facts(read=lambda rel: None))
        assert "P3" in predicates(result)
        assert "could not be read" in result.problems[0].message

    def test_a_file_that_is_not_utf8_text_is_refused(self):
        result = checked(sub(citations=(cite("app/blob.py", 1, 1),)))
        assert predicates(result) == ["P3"]
        assert "UTF-8" in result.problems[0].message

    @pytest.mark.parametrize(
        ("first", "last"), [(0, 1), (3, 2), (1, 4), (-1, 1), (4, 4)]
    )
    def test_a_range_outside_the_file_is_refused(self, first, last):
        result = checked(sub(citations=(cite("app/m.py", first, last),)))
        assert "P3" in predicates(result)

    def test_a_last_line_without_a_line_feed_counts(self):
        assert checked(sub(citations=(cite("app/n.py", 2, 2),))).problems == ()

    def test_the_whole_file_is_a_valid_range(self):
        assert checked(sub(citations=(cite("app/m.py", 1, 3),))).problems == ()

    def test_an_empty_file_has_no_line_to_cite(self):
        tree = {**TREE, "app/empty.py": b""}
        result = checked(
            sub(citations=(cite("app/empty.py", 1, 1), cite())),
            facts(read=tree.get, touched=tuple(sorted(tree))),
        )
        assert "P3" in predicates(result)

    def test_a_submitted_blob_must_be_the_trees(self):
        result = checked(sub(citations=(cite(blob="0123456789ab"),)))
        assert predicates(result) == ["P3"]
        assert "0123456789ab" in result.problems[0].message

    def test_a_submitted_blob_equal_to_the_trees_is_accepted(self):
        blob = guidelines.digest_of(TREE["app/m.py"])
        assert checked(sub(citations=(cite(blob=blob),))).problems == ()

    def test_scope_diff_needs_a_range_meeting_an_added_line(self):
        result = checked(sub(citations=(cite("app/m.py", 1, 1), cite("app/m.py", 3, 3))))
        assert predicates(result) == ["P3"]
        assert "added" in result.problems[0].message

    def test_scope_diff_is_met_by_one_range_of_several(self):
        result = checked(sub(citations=(cite("app/m.py", 1, 1), cite("app/m.py", 1, 2))))
        assert result.problems == ()

    def test_an_untracked_file_is_added_whole(self):
        assert checked(sub(citations=(cite("app/n.py", 1, 1),))).problems == ()

    def test_a_file_git_shows_no_added_line_for_does_not_meet_the_diff(self):
        added = {"app/m.py": AddedLines()}
        result = checked(sub(citations=(cite(),)), facts(added=lambda: added))
        assert predicates(result) == ["P3"]

    def test_lines_git_cannot_give_make_the_verdict_unanswerable(self):
        result = checked(sub(citations=(cite(),)), facts(added=lambda: None))
        assert predicates(result) == ["P3"]
        assert "unanswerable" in result.problems[0].message

    def test_scope_file_asks_no_added_line(self):
        def boom():
            raise AssertionError("scope file must not ask git")

        result = checked(sub("data.either", citations=(cite("app/m.py", 1, 1),)),
                         facts(added=boom))
        assert result.problems == ()

    def test_an_invalid_range_does_not_meet_the_diff(self):
        result = checked(sub(citations=(cite("app/m.py", 2, 9), cite("app/m.py", 1, 1))))
        assert predicates(result).count("P3") == 2

    def test_no_valid_citation_asks_no_diff(self):
        def boom():
            raise AssertionError("nothing valid to meet the diff with")

        result = checked(sub(citations=(cite("app/m.py", 2, 9),)), facts(added=boom))
        assert predicates(result) == ["P3"]

    def test_every_citation_problem_is_named(self):
        result = checked(sub(citations=(cite("./app/m.py"), cite("app/x.py", 1, 1))))
        assert len([p for p in result.problems if p.predicate == "P3"]) >= 2


# -- P4, each test is real --------------------------------------------------------------------


class TestP4:
    def test_a_selector_no_run_named_is_refused(self):
        result = checked(sub("data.tested", tests=("tests/test_m.py::test_other",)))
        assert predicates(result) == ["P4"]
        assert "invariant pipeline test --selector tests/test_m.py::test_other" in (
            result.problems[0].fix
        )

    def test_a_full_run_never_vouches_for_a_named_test(self):
        """DN-8."""
        result = checked(
            sub("data.tested", tests=("tests/test_m.py::test_open",)),
            facts(test_runs=(green(),)),
        )
        assert predicates(result) == ["P4"]

    def test_a_selector_must_match_exactly(self):
        result = checked(
            sub("data.tested", tests=("tests/test_m.py",)),
            facts(test_runs=(green("tests/test_m.py::test_open"),)),
        )
        assert predicates(result) == ["P4"]

    def test_a_pass_needs_the_latest_run_naming_it_green(self):
        runs = (green("t::a"), green("t::a", passed=False))
        result = checked(sub("data.tested", tests=("t::a",)), facts(test_runs=runs))
        assert predicates(result) == ["P4"]
        assert "not green" in result.problems[0].message

    def test_a_later_green_run_answers_an_earlier_red_one(self):
        runs = (green("t::a", passed=False), green("t::a", "t::b"))
        assert checked(sub("data.tested", tests=("t::a",)), facts(test_runs=runs)).problems == ()

    def test_a_run_read_as_red_is_not_green(self):
        runs = (green("t::a", read_as_red=True),)
        result = checked(sub("data.tested", tests=("t::a",)), facts(test_runs=runs))
        assert predicates(result) == ["P4"]

    def test_a_fail_needs_a_run_but_not_a_green_one(self):
        runs = (green("t::a", passed=False),)
        result = checked(sub("data.tested", status="fail", tests=("t::a",)), facts(test_runs=runs))
        assert result.problems == ()

    def test_a_malformed_run_record_names_nothing(self):
        runs = ({"selectors": "t::a", "passed": True}, "junk")
        result = checked(sub("data.tested", tests=("t::a",)), facts(test_runs=runs))
        assert predicates(result) == ["P4"]


# -- P5, na is chosen, not written -----------------------------------------------------------------


class TestP5:
    def test_a_claim_condition_is_accepted(self):
        result = checked(sub(status="na", na_condition="read-only"))
        assert result.problems == ()
        assert result.record.na_condition == "read-only"
        assert result.record.citations == () and result.record.tests == ()

    def test_na_names_a_condition(self):
        result = checked(sub(status="na"))
        assert predicates(result) == ["P5"]
        assert "read-only" in result.problems[0].fix

    def test_an_unknown_condition_is_refused_naming_the_claims(self):
        result = checked(sub(status="na", na_condition="nope"))
        assert predicates(result) == ["P5"]
        assert "read-only" in result.problems[0].fix

    def test_a_mechanical_condition_is_the_engines_to_answer(self):
        result = checked(sub(status="na", na_condition="no-code"))
        assert predicates(result) == ["P5"]
        assert "mechanical" in result.problems[0].message

    def test_a_rule_with_no_conditions_offers_no_na(self):
        result = checked(sub("data.tested", status="na", na_condition="read-only"))
        assert predicates(result) == ["P5"]
        assert "pass or fail" in result.problems[0].fix

    def test_an_na_carries_no_citations_and_no_tests(self):
        result = checked(
            sub(status="na", na_condition="read-only", citations=(cite(),),
                tests=("tests/test_m.py::test_open",))
        )
        assert predicates(result) == ["P5"]

    def test_a_pass_names_no_condition(self):
        result = checked(sub(citations=(cite(),), na_condition="read-only"))
        assert predicates(result) == ["P5"]


# -- P6, the note is bounded and inert ---------------------------------------------------------------


class TestP6:
    def test_a_note_at_the_cap_is_accepted(self):
        result = checked(sub(citations=(cite(),), note="x" * 10), facts(note_max_chars=10))
        assert result.problems == ()

    def test_a_note_past_the_cap_is_refused(self):
        result = checked(sub(citations=(cite(),), note="x" * 11), facts(note_max_chars=10))
        assert predicates(result) == ["P6"]
        assert "11" in result.problems[0].message and "10" in result.problems[0].message

    def test_a_privacy_finding_refuses(self):
        result = checked(
            sub(citations=(cite(),), note="see it"),
            facts(scan=lambda text: ["1 emails a***@b"]),
        )
        assert predicates(result) == ["P6"]
        assert "a***@b" in result.problems[0].message

    def test_no_note_is_not_scanned(self):
        def boom(text):
            raise AssertionError("no note, nothing to scan")

        assert checked(sub(citations=(cite(),)), facts(scan=boom)).problems == ()


# -- bulk submission (DN-11) ----------------------------------------------------------------------------


class TestCheckAll:
    def test_every_entry_is_checked_and_every_problem_named(self):
        parsed = verdicts.parse_submission([
            {"rule": "data.single-open", "status": "pass",
             "citations": [{"path": "app/m.py", "lines": [2, 2]}]},
            {"rule": "data.tested", "status": "pass"},
            {"rule": "nope", "status": "pass"},
            "junk",
        ])
        results = verdicts.check_all(parsed, RULESET_OBJ, facts())
        assert [r.index for r in results] == [1, 2, 3, 4]
        assert results[0].problems == () and results[0].record is not None
        assert predicates(results[1]) == ["P2"]
        assert predicates(results[2]) == ["P1"]
        assert predicates(results[3]) == ["shape"]

    def test_one_rule_twice_in_one_submission_is_refused(self):
        entry = {"rule": "data.single-open", "status": "pass",
                 "citations": [{"path": "app/m.py", "lines": [2, 2]}]}
        results = verdicts.check_all(
            verdicts.parse_submission([entry, entry]), RULESET_OBJ, facts()
        )
        assert results[0].problems == ()
        assert predicates(results[1]) == ["shape"]
        assert "entry 1" in results[1].problems[0].message


# -- plan_verdicts, over a real repository ------------------------------------------------------------


PYTHON = {"name": "demo", "ecosystems": ["python"]}
SELECTOR = "tests/test_m.py::test_open"


def sh(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "one\ntwo\nthree\n")
    write(root, "app/legacy.py", "old\n")
    git_project(root, {"version": 1, "project": PYTHON, "guidelines": {"ruleset": RULESET_REL}})
    sh(root, "checkout", "-q", "-b", "work")
    return root


def pipeline(state="reviewing", *, digest=DIGEST, runs=(), methodology="tdd", pin=True):
    return {
        "methodology": methodology,
        "state": state,
        "ruleset": (
            {"path": RULESET_REL, "digest": digest, "error": None} if pin else None
        ),
        "evidence": {"test_runs": list(runs)},
    }


def context(root, *, base="master", files=(), **kw) -> RuleContext:
    return RuleContext(
        manifest=Manifest.load(root), cwd=root,
        session={"id": "s", "base": base, "files": list(files)},
        pipeline=kw.pop("pipeline", pipeline()), **kw,
    )


ONE = [{"rule": "data.single-open", "status": "pass",
        "citations": [{"path": "app/m.py", "lines": [2, 2]}], "note": "checked"}]


class TestPlan:
    def test_a_valid_verdict_on_a_committed_change_is_planned(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        sh(repo, "commit", "-qam", "change")
        plan = plan_verdicts(context(repo), ONE)
        assert plan.ok, plan.problems
        (record,) = plan.records
        assert record.rule == "data.single-open"
        assert record.citations[0].blob == guidelines.digest_of(b"one\nTWO\nthree\n")
        assert record.ruleset == DIGEST and record.state == "reviewing"
        assert plan.ruleset == DIGEST and plan.state == "reviewing"

    def test_an_uncommitted_change_counts(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        assert plan_verdicts(context(repo), ONE).ok

    def test_scope_diff_against_a_line_the_change_did_not_add(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        sh(repo, "commit", "-qam", "change")
        cited = [{**ONE[0], "citations": [{"path": "app/m.py", "lines": [1, 1]}]}]
        plan = plan_verdicts(context(repo), cited)
        assert not plan.ok
        assert any("P3" in line for line in plan.problems)

    def test_no_base_recorded_makes_scope_diff_unanswerable(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        plan = plan_verdicts(context(repo, base="", files=["app/m.py"]), ONE)
        assert not plan.ok
        assert any("unanswerable" in line for line in plan.problems)

    def test_a_base_git_cannot_find_makes_scope_diff_unanswerable(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        plan = plan_verdicts(context(repo, base="no-such-branch", files=["app/m.py"]), ONE)
        assert not plan.ok
        assert any("unanswerable" in line for line in plan.problems)

    def test_a_deleted_file_does_not_meet_applies_to(self, repo):
        """DN-3: the change deletes a file under applies_to and touches nothing else there."""
        sh(repo, "rm", "-q", "app/legacy.py")
        write(repo, "docs/notes.md", "prose\n")
        sh(repo, "add", "-A")
        sh(repo, "commit", "-qm", "drop")
        cited = [{**ONE[0], "citations": [{"path": "docs/notes.md", "lines": [1, 1]}]}]
        plan = plan_verdicts(context(repo), cited)
        assert any("P1" in line and "applies_to" in line for line in plan.problems)

    def test_the_test_runs_are_read_from_the_pipeline(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        sub_ = [{"rule": "data.tested", "status": "pass", "tests": [SELECTOR]}]
        assert not plan_verdicts(context(repo), sub_).ok
        ran = pipeline(runs=[green(SELECTOR)])
        assert plan_verdicts(context(repo, pipeline=ran), sub_).ok

    def test_a_ruleset_edited_mid_pipeline_is_refused_naming_both(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        edited = RULESET.replace("title: Style", "title: Styling")
        write(repo, RULESET_REL, edited)
        plan = plan_verdicts(context(repo), ONE)
        assert not plan.ok
        new = guidelines.digest_of(edited.encode("utf-8"))
        assert any(new in line and DIGEST in line for line in plan.problems)

    def test_a_null_pinned_digest_compares_against_the_tree(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        ran = pipeline(digest=None)
        assert plan_verdicts(context(repo, pipeline=ran), ONE).ok

    def test_no_pin_compares_against_the_tree(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        assert plan_verdicts(context(repo, pipeline=pipeline(pin=False)), ONE).ok

    def test_a_state_of_another_role_is_refused(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        plan = plan_verdicts(context(repo, pipeline=pipeline("green")), ONE)
        assert any("P1" in line and "'green'" in line for line in plan.problems)

    def test_a_methodology_that_does_not_load_is_refused(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        plan = plan_verdicts(context(repo, pipeline=pipeline(methodology="gone")), ONE)
        assert any("P1" in line and "gone" in line for line in plan.problems)

    def test_no_pipeline_is_refused(self, repo):
        plan = plan_verdicts(context(repo, pipeline=None), ONE)
        assert not plan.ok
        assert any("no pipeline is running" in line for line in plan.problems)

    def test_no_ruleset_is_refused(self, tmp_path):
        root = tmp_path / "bare"
        git_project(root, {"version": 1, "project": PYTHON})
        plan = plan_verdicts(context(root), ONE)
        assert any("no guidelines ruleset is configured" in line for line in plan.problems)

    def test_a_ruleset_that_does_not_load_is_refused(self, repo):
        write(repo, RULESET_REL, "version: 2\n")
        plan = plan_verdicts(context(repo), ONE)
        assert any("does not validate" in line for line in plan.problems)

    def test_a_changed_set_that_cannot_be_established_is_refused(self, repo):
        plan = plan_verdicts(context(repo, base="no-such-branch"), ONE)
        assert any("could not be established" in line for line in plan.problems)

    def test_the_note_is_privacy_scanned_whatever_privacy_scope_says(self, repo):
        body = (repo / ".claude" / "invariant.yaml").read_text(encoding="utf-8")
        (repo / ".claude" / "invariant.yaml").write_text(
            body + "privacy:\n  enabled: false\n  scope: []\n", encoding="utf-8"
        )
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        noted = [{**ONE[0], "note": "ask someone@example.com"}]
        plan = plan_verdicts(context(repo), noted)
        assert any("P6" in line and "emails" in line for line in plan.problems)

    def test_a_note_may_not_carry_the_exemption_marker(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        noted = [{**ONE[0], "note": "invariant: allow-privacy"}]
        plan = plan_verdicts(context(repo), noted)
        assert any("P6" in line and "exemption marker" in line for line in plan.problems)

    def test_the_cap_is_the_manifests(self, repo):
        body = (repo / ".claude" / "invariant.yaml").read_text(encoding="utf-8")
        (repo / ".claude" / "invariant.yaml").write_text(
            body.replace("guidelines:\n", "guidelines:\n  verdicts: {note_max_chars: 5}\n"),
            encoding="utf-8",
        )
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        plan = plan_verdicts(context(repo), ONE)
        assert any("P6" in line for line in plan.problems)

    def test_every_problem_is_named_per_entry(self, repo):
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        plan = plan_verdicts(context(repo), [ONE[0], {"rule": "data.tested", "status": "pass"}, 7])
        assert not plan.ok
        assert plan.records == ()
        assert any(line.startswith("entry 2 (data.tested): P2") for line in plan.problems)
        assert any(line.startswith("entry 3: shape") for line in plan.problems)
        assert not any(line.startswith("entry 1") for line in plan.problems)

    def test_the_refusal_names_every_problem_and_exits_one(self, repo):
        plan = plan_verdicts(context(repo, pipeline=None), ONE)
        refusal = plan.refusal()
        assert isinstance(refusal, VerdictRefused) and refusal.exit_code == 1
        assert "nothing was recorded" in str(refusal)


# -- the domain records what the plan accepted ------------------------------------------------------------


def started(root: Path, state="reviewing"):
    manifest = Manifest.load(root)
    session = SessionDomain(manifest).start("verdicts")
    pipelines = PipelineDomain(manifest)
    pipelines.start(session.id, task="t", cwd=root)
    record = pipelines.current(session.id)
    record.state = state
    pipelines._save(session.id, record)
    return manifest, session, pipelines


def domain_for(root: Path, session, pipelines, manifest) -> GuidelinesDomain:
    ctx = RuleContext(
        manifest=manifest, cwd=root,
        session={**session.to_dict(), "base": "master"},
        pipeline=pipelines.current(session.id).to_dict(),
    )
    return GuidelinesDomain(ctx, pipelines=pipelines)


class TestSubmit:
    def test_an_accepted_submission_is_appended_to_the_pipeline(self, repo):
        manifest, session, pipelines = started(repo)
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        result = domain_for(repo, session, pipelines, manifest).submit_verdicts(ONE)
        assert [r["rule"] for r in result.records] == ["data.single-open"]
        assert result.ruleset == DIGEST and result.state == "reviewing"
        stored = pipelines.current(session.id).evidence["verdicts"]
        assert stored == list(result.records)

    def test_submissions_append_and_never_replace(self, repo):
        manifest, session, pipelines = started(repo)
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        domain_for(repo, session, pipelines, manifest).submit_verdicts(ONE)
        domain_for(repo, session, pipelines, manifest).submit_verdicts(
            [{**ONE[0], "status": "fail"}]
        )
        stored = pipelines.current(session.id).evidence["verdicts"]
        assert [v["status"] for v in stored] == ["pass", "fail"]

    def test_a_refused_bulk_submission_records_nothing(self, repo):
        manifest, session, pipelines = started(repo)
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        with pytest.raises(VerdictRefused) as exc:
            domain_for(repo, session, pipelines, manifest).submit_verdicts(
                [ONE[0], {"rule": "data.tested", "status": "pass"}]
            )
        assert "entry 2 (data.tested): P2" in str(exc.value)
        assert "verdicts" not in pipelines.current(session.id).evidence

    def test_no_pipeline_domain_is_refused(self, repo):
        manifest, session, pipelines = started(repo)
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        ctx = domain_for(repo, session, pipelines, manifest).ctx
        with pytest.raises(VerdictRefused):
            GuidelinesDomain(ctx).submit_verdicts(ONE)

    def test_no_session_is_refused(self, repo):
        manifest, session, pipelines = started(repo)
        ctx = domain_for(repo, session, pipelines, manifest).ctx
        ctx.session = None
        with pytest.raises(VerdictRefused, match="no session"):
            GuidelinesDomain(ctx, pipelines=pipelines).submit_verdicts(ONE)

    def test_the_state_moving_under_the_plan_is_refused(self, repo):
        manifest, session, pipelines = started(repo)
        write(repo, "app/m.py", "one\nTWO\nthree\n")
        domain = domain_for(repo, session, pipelines, manifest)
        moved = pipelines.current(session.id)
        moved.state = "committed"
        pipelines._save(session.id, moved)
        with pytest.raises(VerdictRefused, match="moved"):
            domain.submit_verdicts(ONE)
        assert "verdicts" not in pipelines.current(session.id).evidence


class TestRecordVerdicts:
    def test_it_appends_and_counts(self, repo):
        _manifest, session, pipelines = started(repo)
        record = {"rule": "r", "status": "pass", "state": "reviewing"}
        assert pipelines.record_verdicts(session.id, [record]) == 1
        assert pipelines.record_verdicts(session.id, [record, record]) == 2
        assert len(pipelines.current(session.id).evidence["verdicts"]) == 3

    def test_it_needs_a_pipeline(self, tmp_path):
        root = tmp_path / "bare"
        manifest = git_project(root)
        session = SessionDomain(manifest).start("none")
        from invariant.core.errors import UsageError

        with pytest.raises(UsageError):
            PipelineDomain(manifest).record_verdicts(session.id, [{"state": "x"}])

    def test_it_refuses_a_record_from_another_state(self, repo):
        _manifest, session, pipelines = started(repo)
        with pytest.raises(VerdictRefused, match="moved"):
            pipelines.record_verdicts(session.id, [{"rule": "r", "state": "green"}])
        assert "verdicts" not in pipelines.current(session.id).evidence


def test_a_submission_round_trips_through_json(tmp_path):
    path = tmp_path / "v.json"
    path.write_text(json.dumps(ONE), encoding="utf-8")
    parsed = verdicts.parse_submission(json.loads(path.read_text(encoding="utf-8")))
    assert parsed.problems == () and parsed.entries[0].submitted.note == "checked"
