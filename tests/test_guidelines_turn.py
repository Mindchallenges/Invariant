"""The turn layer (IDEA_031 A5 increment 2, ADR-012 ruling 8, D7, the A5 rulings).

`core/rules.turn_guidelines(manifest, context)` decides what the turn
projection says about the guidelines and returns it as plain data, a
`core/guidelines.TurnIndex`. It reads the manifest's ruleset key first and,
with none, asks for no context and reads nothing. Otherwise it reads the
pipeline's current state roles from the pinned machine, the file scope
(`touched_paths`'s union, the A5 ruling D3), the ruleset from the tree in
hand and the pin, and asks `core/guidelines.select`.

`core/guidelines.render_turn_index` is the pure renderer (D8): rule ids and
titles under one header, the always rules first and always listed (D12),
capped at `guidelines.injection.max_lines` with one overflow line naming the
command that reproduces it. No verdict standings (D10).

The host's `build_context` appends the lines, so `UserPromptSubmit`, the
`SessionStart` greeting and `invariant context` say the same thing.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from test_guidelines_domain import RULESET_REL, write
from test_guidelines_select import REGISTERED, RULESET

from invariant.core import guidelines
from invariant.core.rules import RuleContext, turn_guidelines
from invariant.hosts import claude_code

DIGEST = guidelines.digest_of(RULESET.encode("utf-8"))
BODY = {"version": 1, "project": {"name": "demo"}, "guidelines": {"ruleset": RULESET_REL}}


def ids(index) -> list[str]:
    return [rule.id for rule in index.rules]


@pytest.fixture
def tree(tmp_path):
    write(tmp_path, RULESET_REL, RULESET)
    return tmp_path


def project_manifest(make_manifest, root, body=BODY):
    return make_manifest(body, root)


def pipeline(state, *, pin=DIGEST, methodology="tdd"):
    record = {"state": state, "methodology": methodology}
    if pin is not False:
        record["ruleset"] = {"path": RULESET_REL, "digest": pin, "error": None}
    return record


def turn(manifest, root, *, state="designing", files=(), pin=DIGEST, no_pipeline=False):
    ctx = RuleContext(
        manifest=manifest, cwd=root,
        session={"id": "s1", "files": list(files), "base": "", "worktree": str(root)},
        pipeline=None if no_pipeline else pipeline(state, pin=pin),
    )
    return turn_guidelines(manifest, lambda: ctx)


# -- what is selected -----------------------------------------------------------------


class TestWhatTheTurnSees:
    def test_no_ruleset_reads_nothing_and_never_builds_a_context(self, make_manifest, tmp_path):
        manifest = make_manifest({"version": 1, "project": {"name": "demo"}}, tmp_path)

        def never():
            raise AssertionError("the context was built with no ruleset configured")

        assert turn_guidelines(manifest, never) is None

    def test_a_design_state_sees_design_and_always_rules(self, make_manifest, tree):
        index = turn(project_manifest(make_manifest, tree), tree, state="designing")
        assert index.phases == ("design", "always")
        assert ids(index) == ["core.always", "core.design", "core.both"]
        assert index.show == "invariant guidelines show --phase design --touched"
        assert (index.source, index.digest, index.total) == (RULESET_REL, DIGEST, 5)
        assert index.problem is None and index.pin is None

    @pytest.mark.parametrize("state", ["red", "green", "refactor"])
    def test_a_build_state_sees_what_design_sees(self, make_manifest, tree, state):
        index = turn(project_manifest(make_manifest, tree), tree, state=state)
        assert index.phases == ("design", "always")
        assert ids(index) == ["core.always", "core.design", "core.both"]
        assert index.show == "invariant guidelines show --phase design --touched"

    def test_a_review_state_sees_review_landing_and_always_rules(self, make_manifest, tree):
        index = turn(project_manifest(make_manifest, tree), tree, state="reviewing")
        assert index.phases == ("review", "landing", "always")
        assert ids(index) == ["core.always", "core.review", "core.floor", "core.both"]
        assert index.show == "invariant guidelines show --phase review --touched"

    @pytest.mark.parametrize("state", ["committed", "abandoned"])
    def test_landing_and_abandon_inject_nothing(self, make_manifest, tree, state):
        assert turn(project_manifest(make_manifest, tree), tree, state=state) is None

    def test_no_pipeline_means_only_always_rules(self, make_manifest, tree):
        index = turn(project_manifest(make_manifest, tree), tree, no_pipeline=True)
        assert index.phases == ("always",)
        assert ids(index) == ["core.always"]
        assert index.show == "invariant guidelines show"
        assert index.files == () and index.scope_unknown is False

    def test_the_file_scope_narrows_role_rules_but_never_always_rules(
        self, make_manifest, tree
    ):
        index = turn(project_manifest(make_manifest, tree), tree, files=("docs/a.md",))
        assert index.files == ("docs/a.md",)
        assert ids(index) == ["core.always", "core.both"]
        assert index.scope_unknown is False

    def test_an_unestablished_changed_set_is_the_whole_role_index_and_says_so(
        self, make_manifest, tree
    ):
        index = turn(project_manifest(make_manifest, tree), tree)
        assert index.files == ()
        assert index.scope_unknown is True
        assert ids(index) == ["core.always", "core.design", "core.both"]


# -- what stands in for the index -----------------------------------------------------


class TestProblems:
    def test_a_ruleset_that_does_not_load_is_one_line_naming_the_gate(
        self, make_manifest, tmp_path
    ):
        index = turn(project_manifest(make_manifest, tmp_path), tmp_path)
        assert index.rules == ()
        assert "invariant check guidelines.ruleset_valid" in index.problem
        assert "does not exist" in index.problem

    def test_a_tree_digest_that_differs_from_the_pin_says_so(self, make_manifest, tree):
        index = turn(project_manifest(make_manifest, tree), tree, pin="0" * 12)
        assert index.pin == "0" * 12
        assert ids(index) == ["core.always", "core.design", "core.both"]

    @pytest.mark.parametrize("pin", [None, False])
    def test_a_null_or_absent_pin_compares_against_the_tree(self, make_manifest, tree, pin):
        assert turn(project_manifest(make_manifest, tree), tree, pin=pin).pin is None

    def test_an_exception_inside_the_projection_is_one_line(self, make_manifest, tree):
        manifest = project_manifest(make_manifest, tree)

        def explode():
            raise RuntimeError("boom")

        index = turn_guidelines(manifest, explode)
        assert index.rules == ()
        assert "RuntimeError" in index.problem
        assert "could not be computed" in index.problem

    def test_an_unloadable_machine_is_one_line(self, make_manifest, tree):
        manifest = project_manifest(make_manifest, tree)
        ctx = RuleContext(
            manifest=manifest, cwd=tree, session={"id": "s1"},
            pipeline=pipeline("designing", methodology="no-such-machine"),
        )
        index = turn_guidelines(manifest, lambda: ctx)
        assert index.rules == () and index.problem


# -- the renderer -----------------------------------------------------------------


def index_of(**overrides) -> guidelines.TurnIndex:
    ruleset = guidelines.parse(RULESET, registered=REGISTERED)
    values = {
        "source": RULESET_REL, "digest": ruleset.digest, "total": len(ruleset.rules),
        "phases": ("design", "always"),
        "rules": tuple(ruleset.by_id(i) for i in ("core.always", "core.design", "core.both")),
        "show": "invariant guidelines show --phase design --touched",
    }
    values.update(overrides)
    return guidelines.TurnIndex(**values)


class TestRenderTurnIndex:
    def test_ids_and_titles_under_one_header(self):
        lines = guidelines.render_turn_index(index_of(), 40)
        assert lines == [
            (
                f"guidelines for design, always from {RULESET_REL} (ruleset {DIGEST}): "
                "3 of 5 rules, with no file filter"
            ),
            "- core.always (always): Never skip a test",
            "- core.design: Design is settled first",
            "- core.both: Docs move with structure",
        ]

    def test_the_header_counts_the_files(self):
        one = guidelines.render_turn_index(index_of(files=("app/m.py",)), 40)[0]
        two = guidelines.render_turn_index(index_of(files=("a", "b")), 40)[0]
        assert one.endswith("3 of 5 rules, over 1 file")
        assert two.endswith("3 of 5 rules, over 2 files")

    def test_an_unestablished_scope_is_named_in_the_header(self):
        header = guidelines.render_turn_index(index_of(scope_unknown=True), 40)[0]
        assert header.endswith(
            "with no file filter, since the changed files could not be established"
        )

    def test_the_cap_keeps_one_overflow_line_naming_the_command(self):
        lines = guidelines.render_turn_index(index_of(), 3)
        assert len(lines) == 3
        assert lines[1] == "- core.always (always): Never skip a test"
        assert lines[2] == "- 2 more: invariant guidelines show --phase design --touched"

    def test_exactly_at_the_cap_nothing_is_cut(self):
        lines = guidelines.render_turn_index(index_of(), 4)
        assert len(lines) == 4 and "more:" not in lines[-1]

    def test_a_pin_that_differs_is_one_line_kept_above_the_rules(self):
        lines = guidelines.render_turn_index(index_of(pin="0" * 12), 4)
        assert lines[1] == (
            f"- the ruleset in the tree ({DIGEST}) is not the one this pipeline "
            f"pinned ({'0' * 12}): a verdict is refused until they agree"
        )
        assert lines[2] == "- core.always (always): Never skip a test"
        assert lines[3] == "- 2 more: invariant guidelines show --phase design --touched"

    def test_a_problem_is_one_line(self):
        index = guidelines.TurnIndex(problem="the ruleset does not load")
        assert guidelines.render_turn_index(index, 40) == [
            "guidelines: the ruleset does not load"
        ]

    def test_nothing_selected_and_nothing_to_say_is_silent(self):
        assert guidelines.render_turn_index(index_of(rules=()), 40) == []

    def test_nothing_selected_under_a_moved_pin_still_says_so(self):
        lines = guidelines.render_turn_index(index_of(rules=(), pin="0" * 12), 40)
        assert len(lines) == 2 and "pinned" in lines[1]

    def test_it_never_carries_a_statement_or_a_standing(self):
        text = "\n".join(guidelines.render_turn_index(index_of(), 40))
        assert "A skipped test hides a failure" not in text
        assert "verdict" not in text.replace("a verdict is refused", "")


# -- through the host --------------------------------------------------------------

HOST_ID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def world(tmp_path, monkeypatch):
    from invariant.cli.main import Context
    from invariant.core.manifest import MANIFEST_RELPATH

    monkeypatch.delenv("INVARIANT_SESSION_ID", raising=False)
    monkeypatch.delenv("INVARIANT_HOST_SESSION_ID", raising=False)
    (tmp_path / MANIFEST_RELPATH).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / MANIFEST_RELPATH).write_text(
        yaml.safe_dump({**BODY, "session": {"worktree_dir": ".claude/worktrees"}}),
        encoding="utf-8",
    )
    write(tmp_path, RULESET_REL, RULESET)
    write(tmp_path, "app/m.py", "x = 1\n")

    def git(*args):
        subprocess.run(["git", *args], cwd=str(tmp_path), check=True, capture_output=True)

    git("init", "-q", "-b", "master")
    git("config", "user.email", "dev@example.invalid")
    git("config", "user.name", "Dev")
    git("add", "-A")
    git("commit", "-qm", "init")

    ctx = Context(cwd=tmp_path, host_session_id=HOST_ID)
    session = ctx.sessions.start("turn-layer")
    ctx.sessions.adopt_host_session(session.id, HOST_ID)
    ctx.sessions.isolate(session.id, execute=True)
    session = Context(cwd=tmp_path).sessions.get(session.id)
    Context(cwd=tmp_path).pipelines.start(
        session.id, task="turn", session=session.to_dict(), cwd=Path(session.worktree)
    )
    return tmp_path, session


def hook_text(root, event):
    from invariant.cli.main import Context

    response = claude_code.ClaudeCodeHost(root).handle(
        event, json.dumps({"session_id": HOST_ID}),
        Context(cwd=root, host_session_id=HOST_ID),
    )
    return response["hookSpecificOutput"]["additionalContext"]


class TestThroughTheHost:
    def test_the_prompt_hook_carries_the_turn_index(self, world):
        root, _ = world
        lines = hook_text(root, "user-prompt-submit").splitlines()
        header = next(i for i, line in enumerate(lines) if line.startswith("  guidelines for"))
        assert lines[header].startswith(
            f"  guidelines for design, always from {RULESET_REL} (ruleset {DIGEST})"
        )
        assert lines[header + 1] == "  - core.always (always): Never skip a test"

    def test_the_hook_says_exactly_what_invariant_context_says(
        self, world, capsys, monkeypatch
    ):
        from test_cli import payload, run

        root, session = world
        text = hook_text(root, "user-prompt-submit")
        monkeypatch.chdir(root)
        capsys.readouterr()
        assert run("context", "--id", session.id, "--json") == 0
        assert payload(capsys)["context"] == text
        assert "guidelines for design, always" in text

    def test_the_greeting_carries_the_same_lines(self, world):
        root, _ = world
        prompt = hook_text(root, "user-prompt-submit").splitlines()
        greeting = hook_text(root, "session-start").splitlines()
        turn_lines = [
            line for line in prompt if line.startswith(("  guidelines for", "  - core."))
        ]
        assert turn_lines and all(line in greeting for line in turn_lines)

    def test_the_cap_is_the_manifests(self, world):
        root, _ = world
        from invariant.core.manifest import MANIFEST_RELPATH

        path = root / MANIFEST_RELPATH
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        body["guidelines"]["injection"] = {"max_lines": 2}
        path.write_text(yaml.safe_dump(body), encoding="utf-8")
        # Committed, so the main checkout's working tree adds nothing to the scope.
        subprocess.run(["git", "commit", "-qam", "cap"], cwd=str(root), check=True)
        lines = hook_text(root, "user-prompt-submit").splitlines()
        assert "  - 3 more: invariant guidelines show --phase design --touched" in lines

    def test_an_exception_in_the_hosts_own_step_is_silence(self, world, monkeypatch):
        root, _ = world

        def explode(index, max_lines):
            raise RuntimeError("boom")

        monkeypatch.setattr(claude_code, "render_turn_index", explode)
        text = hook_text(root, "user-prompt-submit")
        assert "guidelines for" not in text
        assert "pipeline tdd: designing" in text

    def test_silence_from_the_engine_adds_no_line(self, world, monkeypatch):
        root, _ = world
        monkeypatch.setattr(claude_code, "turn_guidelines", lambda manifest, context: None)
        text = hook_text(root, "user-prompt-submit")
        assert "guidelines for" not in text
        assert "pipeline tdd: designing" in text

    def test_a_context_with_no_manifest_adds_no_line(self, world):
        from types import SimpleNamespace

        _, session = world
        assert claude_code._turn_index_lines(SimpleNamespace(cwd=None), session, None) == []
