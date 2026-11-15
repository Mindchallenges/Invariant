"""The edit layer (IDEA_031 A5 increment 3, ADR-012 ruling 8, D7, the A5 rulings D4 to D9).

After an edit lands, `PostToolUse` carries two things in one response: what
`guidelines.mechanical_clean` would count as new in the file (A3.8, the
findings), then the statements of the judgement rules that apply to it in
the current phase (D4). Nothing remembers what an earlier edit said, so
every matching edit carries them (D5). Both share one cap and one overflow
line (D6). `guidelines.injection.on_edit`, default true, turns the
statements off (D7).

`core/rules.edit_guidelines` decides the statements and returns plain data;
`core/guidelines.render_edit` writes both halves and `render_advisory`, moved
down beside it, is the findings half alone (D8). The host only wraps them.
Nothing at an edit is ever a deny (ruling 9).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_guidelines_domain import RULESET_REL, write
from test_guidelines_select import RULESET

from invariant.core import guidelines
from invariant.core.guidelines import (
    AdvisedFinding,
    EditAdvisory,
    EditGuidelines,
    render_advisory,
    render_edit,
)
from invariant.core.rules import RuleContext, edit_advisory, edit_guidelines
from invariant.hosts import claude_code
from invariant.hosts.claude_code import ClaudeCodeHost

BODY = {"version": 1, "project": {"name": "demo"}, "guidelines": {"ruleset": RULESET_REL}}
MORE = "invariant check guidelines.mechanical_clean"


def ids(guidance) -> list[str]:
    return [rule.id for rule in guidance.rules]


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, RULESET)
    write(tmp_path, "app/m.py", "x = 1\n")
    write(tmp_path, "app/legacy.py", "x = 1\n")
    write(tmp_path, "tests/test_m.py", "def test(): pass\n")
    write(tmp_path, "docs/a.md", "# a\n")
    return tmp_path


def at_edit(manifest, root, target, *, state="designing", no_pipeline=False, cwd=None):
    ctx = RuleContext(
        manifest=manifest, cwd=cwd or root,
        session={"id": "s1", "files": [], "base": "", "worktree": str(root)},
        pipeline=None if no_pipeline else {"state": state, "methodology": "tdd"},
    )
    return edit_guidelines(manifest, Path(target), lambda: ctx)


def never():
    raise AssertionError("the context was built when the edit layer had nothing to say")


# -- what is selected (D1, D2, D4) ---------------------------------------------------


class TestWhatAnEditCarries:
    def test_a_design_state_carries_the_design_judgement_rules_of_the_file(
        self, make_manifest, tree
    ):
        guidance = at_edit(make_manifest(BODY, tree), tree, tree / "app/m.py")
        assert isinstance(guidance, EditGuidelines)
        assert guidance.path == "app/m.py"
        assert guidance.phases == ("design", "always")
        assert ids(guidance) == ["core.design"]
        assert guidance.show == "invariant guidelines show --phase design --files app/m.py"
        assert guidance.problem is None

    @pytest.mark.parametrize("state", ["red", "green", "refactor"])
    def test_a_build_state_carries_what_design_carries(self, make_manifest, tree, state):
        guidance = at_edit(make_manifest(BODY, tree), tree, tree / "app/m.py", state=state)
        assert guidance.phases == ("design", "always")
        assert ids(guidance) == ["core.design"]
        assert guidance.show.startswith("invariant guidelines show --phase design ")

    def test_a_review_state_carries_judgement_rules_only(self, make_manifest, tree):
        """core.floor is a landing rule over every file, but an engine rule (D4)."""
        guidance = at_edit(make_manifest(BODY, tree), tree, tree / "app/m.py", state="reviewing")
        assert guidance.phases == ("review", "landing", "always")
        assert ids(guidance) == ["core.review"]
        assert guidance.show == "invariant guidelines show --phase review --files app/m.py"

    def test_an_always_rule_reaches_its_files(self, make_manifest, tree):
        guidance = at_edit(make_manifest(BODY, tree), tree, tree / "tests/test_m.py")
        assert ids(guidance) == ["core.always"]

    def test_a_path_exception_sets_the_file_aside(self, make_manifest, tree):
        manifest = make_manifest(BODY, tree)
        assert at_edit(manifest, tree, tree / "app/legacy.py", state="reviewing") is None

    def test_a_file_no_judgement_rule_covers_is_silent(self, make_manifest, tree):
        write(tree, "README.md", "x\n")
        assert at_edit(make_manifest(BODY, tree), tree, tree / "README.md") is None

    @pytest.mark.parametrize("state", ["committed", "abandoned"])
    def test_landing_and_abandon_carry_nothing(self, make_manifest, tree, state):
        assert at_edit(make_manifest(BODY, tree), tree, tree / "app/m.py", state=state) is None

    def test_no_pipeline_carries_the_always_rules_alone(self, make_manifest, tree):
        manifest = make_manifest(BODY, tree)
        guidance = at_edit(manifest, tree, tree / "tests/test_m.py", no_pipeline=True)
        assert guidance.phases == ("always",)
        assert ids(guidance) == ["core.always"]
        assert guidance.show == (
            "invariant guidelines show --phase design --files tests/test_m.py"
        )
        assert at_edit(manifest, tree, tree / "app/m.py", no_pipeline=True) is None

    def test_every_matching_edit_carries_them_again(self, make_manifest, tree):
        """D5: stateless. Nothing remembers the first edit."""
        manifest = make_manifest(BODY, tree)
        first = at_edit(manifest, tree, tree / "app/m.py")
        second = at_edit(manifest, tree, tree / "app/m.py")
        assert first == second and ids(second) == ["core.design"]

    def test_a_relative_target_is_read_against_the_working_directory(
        self, make_manifest, tree
    ):
        guidance = at_edit(make_manifest(BODY, tree), tree, Path("app/m.py"), cwd=tree)
        assert guidance.path == "app/m.py"

    def test_a_path_with_a_space_is_quoted_in_the_command(self, make_manifest, tree):
        write(tree, "app/my m.py", "x = 1\n")
        guidance = at_edit(make_manifest(BODY, tree), tree, tree / "app/my m.py")
        assert guidance.show == (
            "invariant guidelines show --phase design --files 'app/my m.py'"
        )


# -- silence and cost ----------------------------------------------------------------


class TestSilence:
    def test_no_ruleset_never_builds_a_context(self, make_manifest, tmp_path):
        manifest = make_manifest({"version": 1, "project": {"name": "demo"}}, tmp_path)
        assert edit_guidelines(manifest, tmp_path / "app/m.py", never) is None

    def test_on_edit_false_never_builds_a_context(self, make_manifest, tree):
        body = {**BODY, "guidelines": {"ruleset": RULESET_REL, "injection": {"on_edit": False}}}
        assert edit_guidelines(make_manifest(body, tree), tree / "app/m.py", never) is None

    def test_on_edit_false_leaves_the_findings_alone(self, make_manifest, tree):
        """The switch is the statements', never the A3.8 findings'."""
        from test_guidelines_findings import RULESET as FINDINGS

        write(tree, RULESET_REL, FINDINGS)
        write(tree, "app/m.py", "print(1)\n")
        body = {
            "version": 1, "project": {"name": "demo", "ecosystems": ["python"]},
            "guidelines": {"ruleset": RULESET_REL, "injection": {"on_edit": False}},
        }
        manifest = make_manifest(body, tree)
        ctx = RuleContext(manifest=manifest, cwd=tree, session={"worktree": str(tree)})
        advisory = edit_advisory(manifest, tree / "app/m.py", lambda: ctx)
        assert [f.rule for f in advisory.findings] == ["style.no-print"]

    def test_an_edit_outside_the_sessions_tree_is_silent(
        self, make_manifest, tree, tmp_path_factory
    ):
        elsewhere = tmp_path_factory.mktemp("elsewhere")
        write(elsewhere, "app/m.py", "x = 1\n")
        assert at_edit(make_manifest(BODY, tree), tree, elsewhere / "app/m.py") is None

    def test_a_path_that_is_no_file_is_silent(self, make_manifest, tree):
        assert at_edit(make_manifest(BODY, tree), tree, tree / "app/gone.py") is None


# -- what stands in for the statements ---------------------------------------------


class TestProblems:
    def test_a_ruleset_that_does_not_load_is_the_advisorys_own_line(
        self, make_manifest, tmp_path
    ):
        """One line, worded as the findings half words it, so the two merge."""
        write(tmp_path, "app/m.py", "x = 1\n")
        manifest = make_manifest(BODY, tmp_path)
        guidance = at_edit(manifest, tmp_path, tmp_path / "app/m.py")
        assert guidance.rules == ()
        assert "invariant check guidelines.ruleset_valid" in guidance.problem
        ctx = RuleContext(manifest=manifest, cwd=tmp_path, session={"worktree": str(tmp_path)})
        advisory = edit_advisory(manifest, tmp_path / "app/m.py", lambda: ctx)
        assert guidance.problem == advisory.problem
        assert render_edit(advisory, guidance, 40) == [f"guidelines advisory: {advisory.problem}"]

    def test_an_exception_inside_is_one_line(self, make_manifest, tree):
        def broken():
            raise RuntimeError("boom")

        guidance = edit_guidelines(make_manifest(BODY, tree), tree / "app/m.py", broken)
        assert guidance.path == "m.py"
        assert "RuntimeError" in guidance.problem
        assert "invariant guidelines show" in guidance.problem


# -- the renderer (D6, D8) ------------------------------------------------------------


def found(n):
    return AdvisedFinding(
        rule="style.no-print", severity="blocker", path="app/m.py", line=n,
        text=f"print({n})", target=None, fix=f"fix {n}",
    )


def rules_named(*names):
    ruleset = guidelines.parse(RULESET, registered=frozenset({"coverage.meets_floor"}))
    return tuple(ruleset.by_id(name) for name in names)


SHOW = "invariant guidelines show --phase design --files app/m.py"


def guidance_of(*names, problem=None):
    return EditGuidelines(
        path="app/m.py", phases=("design", "always"), rules=rules_named(*names),
        show=SHOW, problem=problem,
    )


class TestRenderEdit:
    def test_nothing_is_silence(self):
        assert render_edit(None, None, 40) == []
        assert render_edit(EditAdvisory("app/m.py"), guidance_of(), 40) == []

    @pytest.mark.parametrize("cap", [2, 3, 4, 40])
    @pytest.mark.parametrize("count", [0, 1, 3, 10])
    def test_findings_alone_render_as_the_advisory_does(self, cap, count):
        advisory = EditAdvisory(
            "app/m.py", findings=tuple(found(n) for n in range(1, count + 1)),
            problem="half" if count % 2 == 0 else None,
        )
        assert render_edit(advisory, None, cap) == render_advisory(advisory, cap)

    def test_statements_alone_have_a_header_and_one_line_each(self):
        lines = render_edit(None, guidance_of("core.design", "core.both"), 40)
        assert lines == [
            "guidelines advisory: 2 judgement rule(s) for app/m.py (phases design, always)",
            "- core.design (blocker): Name the owner of every behaviour. Then reuse it.",
            "- core.both (warn): A structural change updates the docs.",
        ]

    def test_findings_come_first_then_statements(self):
        advisory = EditAdvisory("app/m.py", findings=(found(1),))
        lines = render_edit(advisory, guidance_of("core.design"), 40)
        assert lines == [
            render_advisory(advisory, 40)[0],
            "- style.no-print (blocker) app/m.py:1: print(1); fix: fix 1",
            "guidelines advisory: 1 judgement rule(s) for app/m.py (phases design, always)",
            "- core.design (blocker): Name the owner of every behaviour. Then reuse it.",
        ]

    def test_a_problem_alone_beside_statements_is_kept(self):
        advisory = EditAdvisory("app/m.py", problem="app/m.py could not be checked")
        lines = render_edit(advisory, guidance_of("core.design"), 40)
        assert lines[0] == "guidelines advisory: app/m.py could not be checked"
        assert lines[2].startswith("- core.design")

    def test_a_statements_problem_alone_is_one_line(self):
        lines = render_edit(None, guidance_of(problem="it broke"), 40)
        assert lines == ["guidelines advisory: it broke"]

    def test_exactly_at_the_cap_nothing_is_cut(self):
        advisory = EditAdvisory("app/m.py", findings=(found(1),))
        lines = render_edit(advisory, guidance_of("core.design", "core.both"), 5)
        assert len(lines) == 5 and "more:" not in lines[-1]

    def test_a_cut_in_the_statements_names_the_show_command(self):
        advisory = EditAdvisory("app/m.py", findings=(found(1),))
        guidance = guidance_of("core.design", "core.both", "core.review")
        lines = render_edit(advisory, guidance, 5)
        assert lines[-2].startswith("- core.design")
        assert lines[-1] == f"- 2 more: {SHOW}"
        assert len(lines) == 5

    def test_one_shared_cap_names_both_commands_when_both_are_cut(self):
        advisory = EditAdvisory("app/m.py", findings=tuple(found(n) for n in range(1, 4)))
        lines = render_edit(advisory, guidance_of("core.design", "core.both"), 3)
        assert lines == [
            render_advisory(advisory, 40)[0],
            "- style.no-print (blocker) app/m.py:1: print(1); fix: fix 1",
            f"- 4 more: {MORE}; {SHOW}",
        ]

    def test_a_header_left_with_nothing_under_it_is_dropped(self):
        advisory = EditAdvisory("app/m.py", findings=(found(1), found(2)))
        lines = render_edit(advisory, guidance_of("core.design", "core.both"), 5)
        assert lines[-1] == f"- 2 more: {SHOW}"
        assert not any("judgement rule(s)" in line for line in lines)
        assert len(lines) == 4

    def test_the_smallest_cap_keeps_the_first_header(self):
        advisory = EditAdvisory("app/m.py", findings=(found(1),))
        lines = render_edit(advisory, guidance_of("core.design"), 2)
        assert lines == [render_advisory(advisory, 40)[0], f"- 2 more: {MORE}; {SHOW}"]

    def test_only_statements_past_a_cap_of_two(self):
        lines = render_edit(None, guidance_of("core.design", "core.both"), 2)
        assert lines == [
            "guidelines advisory: 2 judgement rule(s) for app/m.py (phases design, always)",
            f"- 2 more: {SHOW}",
        ]


# -- the host only wraps (D8, ruling 9) -----------------------------------------------


class Values:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, dotted, default=None):
        return self.values.get(dotted, default)


class CountingCtx:
    """Only what `_edit_advisory` reads: a manifest and the rule context builder."""

    def __init__(self, values=None):
        self.manifest = Values(values)
        self.built = 0

    def rule_context(self, payload=None):
        self.built += 1
        return object()


def edit(target):
    return {"tool_name": "Edit", "tool_input": {"file_path": str(target)}}


def context_lines(response):
    assert "permissionDecision" not in json.dumps(response)
    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "PostToolUse"
    return output["additionalContext"].split("\n")


class TestTheHostWraps:
    def patch(self, monkeypatch, advisory, guidance):
        def advise(manifest, target, context):
            context()
            return advisory

        def guide(manifest, target, context):
            context()
            return guidance

        monkeypatch.setattr(claude_code, "edit_advisory", advise)
        monkeypatch.setattr(claude_code, "edit_guidelines", guide)

    def test_one_response_with_findings_then_statements(self, monkeypatch):
        advisory = EditAdvisory("app/m.py", findings=(found(1),))
        guidance = guidance_of("core.design")
        self.patch(monkeypatch, advisory, guidance)
        ctx = CountingCtx()
        lines = context_lines(ClaudeCodeHost._edit_advisory(edit("/proj/app/m.py"), ctx))
        assert lines == render_edit(advisory, guidance, 40)
        assert ctx.built == 1

    def test_the_manifests_cap_is_shared(self, monkeypatch):
        advisory = EditAdvisory("app/m.py", findings=(found(1),))
        self.patch(monkeypatch, advisory, guidance_of("core.design", "core.both"))
        ctx = CountingCtx({"guidelines.injection.max_lines": 3})
        lines = context_lines(ClaudeCodeHost._edit_advisory(edit("/proj/app/m.py"), ctx))
        assert len(lines) == 3 and lines[-1] == f"- 2 more: {SHOW}"

    def test_statements_alone_are_carried(self, monkeypatch):
        self.patch(monkeypatch, None, guidance_of("core.design"))
        lines = context_lines(
            ClaudeCodeHost._edit_advisory(edit("/proj/app/m.py"), CountingCtx())
        )
        assert lines[1].startswith("- core.design (blocker)")

    def test_a_failure_in_the_hosts_step_is_silent(self, monkeypatch):
        def explode(manifest, target, context):
            raise RuntimeError("boom")

        monkeypatch.setattr(claude_code, "edit_advisory", lambda *a: None)
        monkeypatch.setattr(claude_code, "edit_guidelines", explode)
        assert ClaudeCodeHost._edit_advisory(edit("/proj/app/m.py"), CountingCtx()) == {}


# -- through the hook's entry, against a real repository -------------------------------

#: A pattern rule the findings half advises, and an always judgement rule the
#: statements half carries, over the same file.
REAL = """\
version: 1
sections:
  - id: style
    title: Style
rules:
  - id: style.no-print
    section: style
    title: No print calls in the application
    statement: The application never prints.
    severity: blocker
    phases: [review]
    applies_to: ["app/**"]
    check: {kind: pattern, engine: regex, in: code, match: '\\bprint\\('}
  - id: style.owner
    section: style
    title: One owner per behaviour
    statement: Every behaviour has one documented owner.
    severity: warn
    phases: [always]
    applies_to: ["app/**"]
    check: {kind: judgement, evidence: citation, scope: diff}
"""

HOST_ID = "host-a53-edit-layer"


@pytest.fixture
def world(tmp_path, monkeypatch):
    from invariant.cli.main import Context
    from invariant.core.manifest import MANIFEST_RELPATH

    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    (tmp_path / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / MANIFEST_RELPATH).write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
                "session": {"worktree_dir": ".claude/worktrees"},
                "guidelines": {"ruleset": RULESET_REL},
            }
        ),
        encoding="utf-8",
    )
    write(tmp_path, RULESET_REL, REAL)
    write(tmp_path, "app/m.py", "x = 1\n")

    def git(*args):
        subprocess.run(["git", *args], cwd=str(tmp_path), check=True, capture_output=True)

    git("init", "-q", "-b", "master")
    git("config", "user.email", "dev@example.invalid")
    git("config", "user.name", "Dev")
    git("add", "-A")
    git("commit", "-qm", "init")

    ctx = Context(cwd=tmp_path, host_session_id=HOST_ID)
    session = ctx.sessions.start("edit-layer")
    ctx.sessions.adopt_host_session(session.id, HOST_ID)
    ctx.sessions.isolate(session.id, execute=True)
    worktree = Path(Context(cwd=tmp_path).sessions.get(session.id).worktree)
    return tmp_path, worktree


def hook(root, payload):
    from invariant.cli.main import Context

    return ClaudeCodeHost(root).handle(
        "post-tool-use",
        json.dumps({"session_id": HOST_ID, **payload}),
        Context(cwd=root, host_session_id=HOST_ID),
    )


class TestAgainstARealRepository:
    def test_a_worktree_edit_carries_findings_then_the_statement(self, world):
        root, worktree = world
        target = worktree / "app" / "m.py"
        target.write_text("x = 1\nprint(x)\n", encoding="utf-8")
        lines = context_lines(hook(root, edit(target)))
        assert lines[0].startswith("guidelines advisory: 1 new finding(s) in app/m.py")
        assert lines[1].startswith("- style.no-print (blocker) app/m.py:2")
        assert lines[2] == (
            "guidelines advisory: 1 judgement rule(s) for app/m.py (phases always)"
        )
        assert lines[3] == "- style.owner (warn): Every behaviour has one documented owner."

    def test_a_clean_edit_still_carries_the_statement(self, world):
        root, worktree = world
        target = worktree / "app" / "m.py"
        target.write_text("x = 2\n", encoding="utf-8")
        lines = context_lines(hook(root, edit(target)))
        assert lines == [
            "guidelines advisory: 1 judgement rule(s) for app/m.py (phases always)",
            "- style.owner (warn): Every behaviour has one documented owner.",
        ]

    def test_a_main_checkout_edit_is_silent(self, world):
        root, _ = world
        target = root / "app" / "m.py"
        target.write_text("print(1)\n", encoding="utf-8")
        assert hook(root, edit(target)) == {}
