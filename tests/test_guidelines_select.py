"""Selection and `guidelines show --phase` (IDEA_031 A5 increment 1, ADR-012 D7).

`core/guidelines.select(ruleset, roles, paths)` is pure. A state's roles map
to the rule phases it sees (the A5 rulings D1 and D2): a state carrying none
of the selection's roles is a build state and sees design and always rules;
a design state sees the same; a review state sees review, landing and always
rules; a landing or abandon state sees nothing; a state with several roles
sees the union. Paths narrow it through `applies_to`, and a path exception
sets a file aside, the one reading `meets` gives. No paths means no filter.

`invariant guidelines show --phase R [--files ...] [--touched]` prints the
full text of the selected rules with the checklist's own per-rule writer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_cli import payload, run
from test_domains import git_project
from test_guidelines_domain import RULESET_REL, write

from invariant.core import findings, guidelines
from invariant.core.machines import load_pipeline_machine
from invariant.core.manifest import Manifest
from invariant.core.rules import RenderRefused, RuleContext, all_rules
from invariant.domains.guidelines import GuidelinesDomain, SelectionRefused

REGISTERED = frozenset(spec.id for spec in all_rules())

RULESET = """\
version: 1
sections:
  - id: core
    title: Core
rules:
  - id: core.design
    section: core
    title: Design is settled first
    statement: >
      Name the owner of every behaviour.

      Then reuse it.
    severity: blocker
    phases: [design]
    applies_to: ["app/**"]
    check: {kind: judgement, evidence: citation, scope: diff}
  - id: core.review
    section: core
    title: Reviewed against the diff
    statement: Every changed line is read.
    severity: warn
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: judgement, evidence: citation, scope: diff}
    exceptions:
      - path: app/legacy.py
        reason: retired next release
  - id: core.floor
    section: core
    title: Touched files meet the floor
    statement: Every touched source file meets the floor.
    severity: warn
    phases: [landing]
    applies_to: ["**"]
    check: {kind: engine, rule: coverage.meets_floor}
  - id: core.always
    section: core
    title: Never skip a test
    statement: A skipped test hides a failure.
    severity: blocker
    phases: [always]
    applies_to: ["tests/**"]
    check: {kind: judgement, evidence: citation, scope: diff}
  - id: core.both
    section: core
    title: Docs move with structure
    statement: A structural change updates the docs.
    severity: warn
    phases: [design, review]
    applies_to: ["docs/**"]
    check: {kind: judgement, evidence: citation, scope: diff}
"""


def ruleset() -> guidelines.Ruleset:
    return guidelines.parse(RULESET, registered=REGISTERED)


def ids(rules) -> list[str]:
    return [r.id for r in rules]


# -- which phases a state's roles see (the A5 rulings D1 and D2) ---------------------


class TestPhasesFor:
    @pytest.mark.parametrize(
        ("roles", "phases"),
        [
            ((), ("design", "always")),
            (("design",), ("design", "always")),
            (("review",), ("review", "landing", "always")),
            (("landing",), ()),
            (("abandon",), ()),
            (("design", "review"), ("design", "review", "landing", "always")),
            (("review", "landing"), ("review", "landing", "always")),
            (("landing", "abandon"), ()),
            # A role the selection does not know carries no phases of its
            # own, so a state with only such roles is a build state.
            (("spec",), ("design", "always")),
            (("spec", "review"), ("review", "landing", "always")),
        ],
    )
    def test_roles_map_to_phases(self, roles, phases):
        assert guidelines.phases_for(roles) == phases

    @pytest.mark.parametrize(
        ("methodology", "state", "phases"),
        [
            ("tdd", "designing", ("design", "always")),
            ("tdd", "red", ("design", "always")),
            ("tdd", "green", ("design", "always")),
            ("tdd", "refactor", ("design", "always")),
            ("tdd", "reviewing", ("review", "landing", "always")),
            ("tdd", "committed", ()),
            ("tdd", "abandoned", ()),
            ("chore", "doing", ("design", "always")),
            ("chore", "reviewing", ("review", "landing", "always")),
        ],
    )
    def test_shipped_states(self, make_manifest, methodology, state, phases):
        manifest = make_manifest({"version": 1, "project": {"name": "demo"}})
        machine = load_pipeline_machine(manifest, methodology)
        assert guidelines.phases_for(machine.state(state).roles) == phases


# -- select(ruleset, roles, paths) ---------------------------------------------------


class TestSelect:
    def test_a_build_state_sees_design_and_always_rules_in_declared_order(self):
        assert ids(guidelines.select(ruleset(), (), ())) == [
            "core.design", "core.always", "core.both",
        ]

    def test_a_review_state_sees_review_landing_and_always_rules(self):
        assert ids(guidelines.select(ruleset(), ("review",), ())) == [
            "core.review", "core.floor", "core.always", "core.both",
        ]

    @pytest.mark.parametrize("role", ["landing", "abandon"])
    def test_a_landing_or_abandon_state_selects_nothing(self, role):
        assert guidelines.select(ruleset(), (role,), ()) == ()

    def test_several_roles_get_the_union(self):
        assert ids(guidelines.select(ruleset(), ("design", "review"), ())) == [
            "core.design", "core.review", "core.floor", "core.always", "core.both",
        ]

    def test_paths_narrow_by_applies_to(self):
        assert ids(guidelines.select(ruleset(), ("review",), ("app/m.py",))) == [
            "core.review", "core.floor",
        ]

    def test_any_path_meeting_a_rule_selects_it(self):
        assert ids(guidelines.select(ruleset(), (), ("tests/t.py", "docs/a.md"))) == [
            "core.always", "core.both",
        ]

    def test_a_path_exception_sets_a_file_aside(self):
        assert ids(guidelines.select(ruleset(), ("review",), ("app/legacy.py",))) == [
            "core.floor",
        ]

    def test_a_path_no_rule_names_selects_nothing(self):
        assert guidelines.select(ruleset(), ("design",), ("other/x.py",)) == ()

    def test_it_is_pure(self):
        rs = ruleset()
        first = guidelines.select(rs, ("review",), ("app/m.py",))
        assert guidelines.select(rs, ("review",), ("app/m.py",)) == first


class TestMeetsHasOneOwner:
    """`meets` moved down beside the ruleset so `select` can ask it; the
    findings and the verdicts read the same function."""

    def test_findings_reads_the_guidelines_predicate(self):
        assert findings.meets is guidelines.meets
        assert findings.matches is guidelines.matches

    def test_meets(self):
        rule = ruleset().by_id("core.review")
        assert guidelines.meets(rule, "app/m.py")
        assert not guidelines.meets(rule, "app/legacy.py")
        assert not guidelines.meets(rule, "docs/m.py")

    def test_path_exception_ignores_a_fingerprinted_exception(self):
        text = RULESET.replace(
            "reason: retired next release",
            "reason: retired next release\n        fingerprint: 3f2a9c1b7e44",
        )
        rule = guidelines.parse(text, registered=REGISTERED).by_id("core.review")
        assert guidelines.path_exception(rule, "app/legacy.py") is None
        assert guidelines.meets(rule, "app/legacy.py")

    def test_a_glob_naming_nothing_in_the_project_matches_nothing(self):
        assert not guidelines.matches("app/x.py", "/elsewhere/**")
        assert not guidelines.matches("app/x.py", ".")


# -- the rendered text ---------------------------------------------------------------


class TestRenderSelected:
    def test_each_rule_is_the_checklists_own_text(self):
        rs = ruleset()
        selected = guidelines.select(rs, ("review",), ())
        text = guidelines.render_selected(
            rs, selected, source=RULESET_REL, phases=("review", "landing", "always"), files=()
        )
        checklist = guidelines.render_checklist(rs, "markdown", source=RULESET_REL)
        for rule in selected:
            heading = f"### {rule.id}: {rule.title}"
            assert heading in text
            block = text.split(heading, 1)[1].split("\n### ", 1)[0]
            assert block in checklist
        assert "core.design" not in text

    def test_the_header_names_the_ruleset_the_phases_and_the_files(self):
        rs = ruleset()
        text = guidelines.render_selected(
            rs, rs.rules[:1], source=RULESET_REL, phases=("design", "always"),
            files=("app/m.py", "app/n.py"),
        )
        header = text.splitlines()[0]
        assert f"`{RULESET_REL}`" in header and rs.digest in header
        assert "design, always" in header
        assert "1 of 5 rules" in header and "2 files" in header

    def test_no_files_says_there_is_no_file_filter(self):
        rs = ruleset()
        text = guidelines.render_selected(rs, rs.rules, source=RULESET_REL,
                                          phases=("design",), files=())
        assert "no file filter" in text.splitlines()[0]

    def test_an_empty_selection_says_so(self):
        rs = ruleset()
        text = guidelines.render_selected(rs, (), source=RULESET_REL, phases=(), files=())
        assert "no phases" in text.splitlines()[0]
        assert text.rstrip("\n").endswith("No rule is selected.")


# -- the domain ----------------------------------------------------------------------


def domain(make_manifest, root, *, session=None, configured=True):
    body = {"version": 1, "project": {"name": "demo"}}
    if configured:
        body["guidelines"] = {"ruleset": RULESET_REL}
    return GuidelinesDomain(
        RuleContext(manifest=make_manifest(body, root), cwd=root, session=session)
    )


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, RULESET)
    return tmp_path


class TestTheDomain:
    def test_it_selects_and_renders(self, make_manifest, tree):
        shown = domain(make_manifest, tree).select("review", files=("app/m.py",))
        assert shown.rules == ("core.review", "core.floor")
        assert shown.phases == ("review", "landing", "always")
        assert shown.files == ("app/m.py",)
        assert shown.ruleset == guidelines.digest_of(RULESET.encode("utf-8"))
        assert "### core.review: Reviewed against the diff" in shown.text
        assert "core.design" not in shown.text

    def test_a_relative_file_is_read_against_the_working_directory(
        self, make_manifest, tree
    ):
        dom = GuidelinesDomain(RuleContext(
            manifest=make_manifest({"version": 1, "project": {"name": "demo"},
                                    "guidelines": {"ruleset": RULESET_REL}}, tree),
            cwd=tree / "app",
        ))
        assert dom.select("design", files=("m.py",)).files == ("app/m.py",)

    def test_an_absolute_file_inside_the_project_is_spelled_relative(
        self, make_manifest, tree
    ):
        shown = domain(make_manifest, tree).select("design", files=(str(tree / "app/m.py"),))
        assert shown.files == ("app/m.py",)

    def test_a_file_outside_the_project_is_refused(self, make_manifest, tree):
        with pytest.raises(SelectionRefused, match="names nothing inside the project") as caught:
            domain(make_manifest, tree).select("design", files=("/elsewhere/x.py",))
        assert caught.value.exit_code == 1

    def test_an_unknown_phase_is_refused_naming_the_phases(self, make_manifest, tree):
        with pytest.raises(SelectionRefused, match="unknown phase 'build'") as caught:
            domain(make_manifest, tree).select("build")
        assert "design, review, landing, abandon" in str(caught.value)

    def test_touched_with_no_session_is_refused(self, make_manifest, tree):
        with pytest.raises(SelectionRefused, match="no session is active here"):
            domain(make_manifest, tree).select("design", touched=True)

    def test_touched_with_a_session_without_an_id_is_refused(self, make_manifest, tree):
        with pytest.raises(SelectionRefused, match="no session is active here"):
            domain(make_manifest, tree, session={"files": []}).select("design", touched=True)

    def test_no_ruleset_shows_nothing(self, make_manifest, tmp_path):
        shown = domain(make_manifest, tmp_path, configured=False).select("design")
        assert shown.text is None and shown.rules == ()

    def test_a_ruleset_that_does_not_load_is_refused(self, make_manifest, tmp_path):
        with pytest.raises(RenderRefused, match="does not exist"):
            domain(make_manifest, tmp_path).select("design")

    def test_it_writes_nothing(self, make_manifest, tree):
        dom = domain(make_manifest, tree)
        before = sorted(p.relative_to(tree) for p in tree.rglob("*"))
        dom.select("review")
        assert sorted(p.relative_to(tree) for p in tree.rglob("*")) == before


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    write(root, RULESET_REL, RULESET)
    write(root, "app/m.py", "one\n")
    write(root, "tests/t.py", "one\n")
    git_project(root, {"version": 1, "project": {"name": "demo"},
                       "guidelines": {"ruleset": RULESET_REL}})
    return root


class TestTouched:
    """--touched: the session's declared plus changed files (the A5 ruling D3)."""

    def session_domain(self, root: Path, files=()):
        return GuidelinesDomain(RuleContext(
            manifest=Manifest.locate(root), cwd=root,
            session={"id": "s1", "files": list(files), "base": ""},
        ))

    def test_declared_and_changed_files_both_count(self, repo):
        write(repo, "tests/t.py", "two\n")
        shown = self.session_domain(repo, files=("docs/new.md",)).select(
            "design", touched=True
        )
        assert shown.files == ("docs/new.md", "tests/t.py")
        assert shown.rules == ("core.always", "core.both")

    def test_files_and_touched_are_the_union(self, repo):
        shown = self.session_domain(repo, files=("docs/new.md",)).select(
            "design", files=("app/m.py",), touched=True
        )
        assert shown.files == ("app/m.py", "docs/new.md")

    def test_nothing_touched_means_no_file_filter(self, repo):
        shown = self.session_domain(repo).select("design", touched=True)
        assert shown.files == ()
        assert shown.rules == ("core.design", "core.always", "core.both")


# -- the command ---------------------------------------------------------------------


class TestTheCommand:
    @pytest.fixture(autouse=True)
    def _inside(self, project, monkeypatch):
        monkeypatch.chdir(project)

    def configure(self, root: Path) -> None:
        from conftest import write_manifest

        write_manifest(root, {"version": 1, "project": {"name": "demo"},
                              "guidelines": {"ruleset": RULESET_REL}})
        write(root, RULESET_REL, RULESET)

    def test_it_prints_the_selected_rules_only(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "show", "--phase", "review", "--files", "app/m.py") == 0
        out = capsys.readouterr().out
        assert "### core.review: Reviewed against the diff" in out
        assert "### core.floor:" in out
        assert "core.design" not in out and "core.always" not in out

    def test_files_take_several_paths_and_repeat(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "show", "--phase", "design", "--files", "app/m.py",
                   "tests/t.py", "--files", "docs/a.md", "--json") == 0
        doc = payload(capsys)
        assert doc["files"] == ["app/m.py", "docs/a.md", "tests/t.py"]

    def test_json_carries_the_ids_the_digest_and_the_files(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "show", "--phase", "review", "--files", "app/m.py",
                   "--json") == 0
        doc = payload(capsys)
        assert doc["phase"] == "review"
        assert doc["phases"] == ["review", "landing", "always"]
        assert doc["rules"] == ["core.review", "core.floor"]
        assert doc["ruleset"] == guidelines.digest_of(RULESET.encode("utf-8"))
        assert doc["files"] == ["app/m.py"]
        assert "### core.review" in doc["text"]

    def test_an_unknown_phase_exits_1(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "show", "--phase", "bogus") == 1
        assert "unknown phase 'bogus'" in capsys.readouterr().err

    def test_touched_with_no_session_exits_1(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "show", "--phase", "design", "--touched") == 1
        err = capsys.readouterr().err
        assert "no session is active here" in err and "invariant session start" in err

    @pytest.mark.parametrize("args", [("--files", "app/m.py"), ("--touched",)])
    def test_selecting_files_needs_a_phase(self, project, capsys, args):
        self.configure(project)
        assert run("guidelines", "show", *args) == 1
        assert "--phase" in capsys.readouterr().err

    def test_with_no_ruleset(self, project, capsys):
        assert run("guidelines", "show", "--phase", "design") == 0
        assert "no guidelines ruleset configured" in capsys.readouterr().out

    def test_plain_show_is_unchanged(self, project, capsys):
        self.configure(project)
        assert run("guidelines", "show") == 0
        out = capsys.readouterr().out
        assert out.startswith("# Coding guidelines")
        assert "core.design" in out and "core.floor" in out


class TestTouchedThroughTheCommand:
    def test_it_reads_the_session_resolved_from_the_working_directory(
        self, repo, monkeypatch, capsys
    ):
        monkeypatch.chdir(repo)
        assert run("session", "start", "select") == 0
        write(repo, "tests/t.py", "two\n")
        capsys.readouterr()
        assert run("guidelines", "show", "--phase", "design", "--touched", "--json") == 0
        doc = payload(capsys)
        assert "tests/t.py" in doc["files"]
        assert "core.always" in doc["rules"] and "core.design" not in doc["rules"]
