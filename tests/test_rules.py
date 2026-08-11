import subprocess
from pathlib import Path

import pytest

from invariant.core.manifest import Level
from invariant.core.rules import (
    _coverage_roots,
    _relative_to_any,
    _same_file,
    RuleContext,
    Verdict,
    all_rules,
    evaluate,
    evaluate_event,
    rules_for_event,
)


def write_manifest(root: Path, body: dict) -> Path:
    import yaml

    from invariant.core.manifest import MANIFEST_RELPATH

    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body), encoding="utf-8")
    return path


def run(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A real repository with a main checkout and one linked worktree."""
    main = tmp_path / "main"
    main.mkdir()
    run(["git", "init", "-q", "-b", "master"], main)
    run(["git", "config", "user.email", "test@example.invalid"], main)
    run(["git", "config", "user.name", "Test"], main)
    write_manifest(main, {"version": 1, "project": {"name": "demo"}})
    (main / "docs" / "architecture").mkdir(parents=True)
    (main / "docs" / "architecture" / "overview.md").write_text(
        "# Overview\n\n```mermaid\ngraph TD\n  A-->B\n```\n", encoding="utf-8"
    )
    run(["git", "add", "-A"], main)
    run(["git", "commit", "-qm", "init"], main)

    wt = tmp_path / "wt"
    run(["git", "worktree", "add", "-q", str(wt), "-b", "session/x"], main)
    return main, wt


@pytest.fixture
def ctx_factory(repo):
    main, wt = repo
    from invariant.core.manifest import Manifest

    def build(cwd=None, session=None, pipeline=None, payload=None, extra=None):
        cwd = cwd or main
        return RuleContext(
            manifest=Manifest.load(main),
            cwd=Path(cwd),
            session=session,
            pipeline=pipeline,
            payload=payload or {},
            extra=extra or {},
        )

    build.main = main
    build.wt = wt
    return build


def edit_payload(path, content="hello"):
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": content}}


def bash_payload(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def only(results):
    assert len(results) == 1
    return results[0]


# -- registry ----------------------------------------------------------


class TestRegistry:
    def test_every_rule_has_a_summary(self):
        for spec in all_rules():
            assert spec.summary.strip()

    def test_every_profile_rule_id_is_registered(self):
        """A profile naming a rule that does not exist would pass forever."""
        import yaml

        from invariant.core.manifest import DATA_DIR

        registered = {spec.id for spec in all_rules()}
        for profile in (DATA_DIR / "profiles").glob("*.yaml"):
            raw = yaml.safe_load(profile.read_text(encoding="utf-8"))
            for rule_id in raw.get("rules", {}):
                assert rule_id in registered, f"{profile.name} names unknown {rule_id}"

    def test_unknown_rule_id_fails_loudly(self, ctx_factory):
        result = only(evaluate(["no.such.rule"], ctx_factory()))
        assert not result.verdict.ok
        assert "no rule registered" in result.verdict.message

    def test_a_raising_rule_reports_itself_as_the_bug(self, ctx_factory, monkeypatch):
        from invariant.core import rules as rules_mod

        spec = rules_mod.get_rule("privacy.clean")

        def boom(_ctx):
            raise RuntimeError("kaboom")

        monkeypatch.setitem(
            rules_mod._REGISTRY, "privacy.clean", type(spec)(spec.id, spec.summary, spec.events, boom)
        )
        result = only(evaluate(["privacy.clean"], ctx_factory()))
        assert not result.verdict.ok
        assert "bug in the rule" in result.verdict.detail

    def test_off_short_circuits_without_running_the_rule(self, ctx_factory, tmp_path):
        write_manifest(
            ctx_factory.main, {"enforcement": {"rules": {"privacy.clean": "off"}}}
        )
        ctx = ctx_factory(payload=edit_payload(ctx_factory.main / "docs/architecture/x.md",
                                               "mail a@b.com"))
        result = only(evaluate(["privacy.clean"], ctx))
        assert result.level is Level.OFF and result.verdict.skipped

    def test_events_bind_rules(self):
        ids = {spec.id for spec in rules_for_event("pre-tool-use")}
        assert "session.no_edit_in_main_checkout" in ids
        assert "coverage.meets_floor" not in ids


class TestEvaluatedSemantics:
    def test_skipped_is_not_a_pass(self):
        assert Verdict.na().ok and Verdict.na().skipped

    def test_a_skipped_verdict_never_blocks(self, ctx_factory):
        ctx = ctx_factory(payload=bash_payload("ls"))
        for result in evaluate_event("pre-tool-use", ctx):
            if result.verdict.skipped:
                assert not result.blocks and not result.warns


# -- session rules -----------------------------------------------------


class TestIsolationRules:
    def test_edit_in_main_checkout_is_refused(self, ctx_factory):
        ctx = ctx_factory(cwd=ctx_factory.main, payload=edit_payload(ctx_factory.main / "src.py"))
        result = only(evaluate(["session.no_edit_in_main_checkout"], ctx))
        assert not result.verdict.ok
        assert result.level is Level.BLOCK and result.blocks

    def test_edit_in_a_linked_worktree_is_allowed(self, ctx_factory):
        ctx = ctx_factory(cwd=ctx_factory.wt, payload=edit_payload(ctx_factory.wt / "src.py"))
        result = only(evaluate(["session.no_edit_in_main_checkout"], ctx))
        assert result.verdict.ok

    def test_exempt_paths_are_allowed_in_the_main_checkout(self, ctx_factory):
        # Config and knowledge files are data every session edits; routing them
        # through a branch produces conflicts with no review value.
        ctx = ctx_factory(
            cwd=ctx_factory.main,
            payload=edit_payload(ctx_factory.main / ".claude" / "invariant.yaml"),
        )
        result = only(evaluate(["session.no_edit_in_main_checkout"], ctx))
        assert result.verdict.skipped

    def test_non_edit_tools_are_not_checked(self, ctx_factory):
        ctx = ctx_factory(payload=bash_payload("ls"))
        assert only(evaluate(["session.no_edit_in_main_checkout"], ctx)).verdict.skipped

    def test_no_session_blocks_the_edit(self, ctx_factory):
        ctx = ctx_factory(cwd=ctx_factory.wt, payload=edit_payload(ctx_factory.wt / "a.py"))
        result = only(evaluate(["session.isolated_before_edit"], ctx))
        assert not result.verdict.ok and "not attributable" in result.verdict.message

    def test_registered_but_not_isolated_blocks(self, ctx_factory):
        ctx = ctx_factory(
            cwd=ctx_factory.wt,
            session={"state": "registered"},
            payload=edit_payload(ctx_factory.wt / "a.py"),
        )
        result = only(evaluate(["session.isolated_before_edit"], ctx))
        assert "not isolated" in result.verdict.message

    def test_isolated_session_passes(self, ctx_factory):
        ctx = ctx_factory(
            cwd=ctx_factory.wt,
            session={"state": "working"},
            payload=edit_payload(ctx_factory.wt / "a.py"),
        )
        assert only(evaluate(["session.isolated_before_edit"], ctx)).verdict.ok


class TestPushBarrier:
    """The barrier only exists where publishing is a session step."""

    @pytest.fixture(autouse=True)
    def publishing_on(self, ctx_factory):
        write_manifest(
            ctx_factory.main,
            {"version": 1, "project": {"name": "demo"}, "session": {"push": True}},
        )

    def test_second_push_is_refused(self, ctx_factory):
        ctx = ctx_factory(
            session={"state": "merged", "push_count": 1},
            payload=bash_payload("git push origin master"),
        )
        result = only(evaluate(["session.push_barrier"], ctx))
        assert "already pushed" in result.verdict.message

    def test_push_before_merge_is_refused(self, ctx_factory):
        ctx = ctx_factory(
            session={"state": "working", "push_count": 0},
            payload=bash_payload("git push"),
        )
        assert "barrier opens after the merge" in only(
            evaluate(["session.push_barrier"], ctx)
        ).verdict.message

    def test_first_push_after_merge_passes(self, ctx_factory):
        ctx = ctx_factory(
            session={"state": "merged", "push_count": 0},
            payload=bash_payload("git push"),
        )
        assert only(evaluate(["session.push_barrier"], ctx)).verdict.ok

    def test_a_non_push_command_is_skipped(self, ctx_factory):
        ctx = ctx_factory(session={"state": "working"}, payload=bash_payload("git status"))
        assert only(evaluate(["session.push_barrier"], ctx)).verdict.skipped


class TestMergeTarget:
    def test_merging_from_inside_the_worktree_is_refused(self, ctx_factory):
        ctx = ctx_factory(cwd=ctx_factory.wt, payload=bash_payload("git merge --ff-only session/x"))
        result = only(evaluate(["session.ff_merge_target"], ctx))
        assert not result.verdict.ok
        assert "into itself" in result.verdict.detail

    def test_merging_from_the_main_checkout_passes(self, ctx_factory):
        ctx = ctx_factory(cwd=ctx_factory.main, payload=bash_payload("git merge --ff-only session/x"))
        assert only(evaluate(["session.ff_merge_target"], ctx)).verdict.ok


class TestFileSet:
    def test_stray_staged_file_is_reported(self, ctx_factory):
        wt = ctx_factory.wt
        (wt / "mine.py").write_text("x", encoding="utf-8")
        (wt / "theirs.py").write_text("y", encoding="utf-8")
        run(["git", "add", "mine.py", "theirs.py"], wt)
        ctx = ctx_factory(
            cwd=wt,
            session={"state": "working", "files": ["mine.py"]},
            payload=bash_payload("git commit -m x"),
        )
        result = only(evaluate(["session.own_file_set_only"], ctx))
        assert "theirs.py" in result.verdict.detail

    def test_no_declared_set_means_no_opinion(self, ctx_factory):
        ctx = ctx_factory(
            session={"state": "working", "files": []},
            payload=bash_payload("git commit -m x"),
        )
        assert only(evaluate(["session.own_file_set_only"], ctx)).verdict.skipped


# -- docs rules --------------------------------------------------------


class TestDocsRules:
    def test_unread_docs_fail_the_gate(self, ctx_factory):
        ctx = ctx_factory(pipeline={"evidence": {"docs_read": []}})
        result = only(evaluate(["docs.read_architecture"], ctx))
        assert "no architecture doc has been read" in result.verdict.message

    def test_reading_a_doc_satisfies_the_gate(self, ctx_factory):
        doc = ctx_factory.main / "docs" / "architecture" / "overview.md"
        ctx = ctx_factory(pipeline={"evidence": {"docs_read": [str(doc)]}})
        assert only(evaluate(["docs.read_architecture"], ctx)).verdict.ok

    def test_reading_an_unrelated_file_does_not(self, ctx_factory):
        ctx = ctx_factory(pipeline={"evidence": {"docs_read": [str(ctx_factory.main / "README.md")]}})
        assert not only(evaluate(["docs.read_architecture"], ctx)).verdict.ok

    def test_source_change_without_a_doc_change_fails(self, ctx_factory):
        wt = ctx_factory.wt
        (wt / "app.py").write_text("code", encoding="utf-8")
        run(["git", "add", "app.py"], wt)
        ctx = ctx_factory(cwd=wt)
        result = only(evaluate(["docs.updated"], ctx))
        assert "source changed but no architecture doc did" in result.verdict.message
        assert any("--force" in fix for fix in result.verdict.fixes)

    def test_source_change_with_a_doc_change_passes(self, ctx_factory):
        wt = ctx_factory.wt
        (wt / "app.py").write_text("code", encoding="utf-8")
        (wt / "docs" / "architecture" / "overview.md").write_text(
            "# Overview\n\n```mermaid\ngraph TD\n  A-->C\n```\n", encoding="utf-8"
        )
        run(["git", "add", "-A"], wt)
        assert only(evaluate(["docs.updated"], ctx_factory(cwd=wt))).verdict.ok

    def test_docs_only_change_passes(self, ctx_factory):
        wt = ctx_factory.wt
        (wt / "docs" / "architecture" / "overview.md").write_text("# New\n", encoding="utf-8")
        run(["git", "add", "-A"], wt)
        assert only(evaluate(["docs.updated"], ctx_factory(cwd=wt))).verdict.ok

    def test_missing_docs_directory_is_a_failure_not_a_skip(self, ctx_factory, tmp_path):
        # Docs-first is core policy: an absent docs tree is told to exist.
        import shutil

        shutil.rmtree(ctx_factory.main / "docs")
        result = only(evaluate(["docs.read_architecture"], ctx_factory()))
        assert not result.verdict.ok and "no architecture docs" in result.verdict.message

    def test_changed_doc_without_a_diagram_is_reported(self, ctx_factory):
        wt = ctx_factory.wt
        (wt / "docs" / "architecture" / "overview.md").write_text("# Prose only\n", encoding="utf-8")
        run(["git", "add", "-A"], wt)
        result = only(evaluate(["docs.diagrams_current"], ctx_factory(cwd=wt)))
        assert "no diagram" in result.verdict.message

    def test_changed_doc_with_a_diagram_passes(self, ctx_factory):
        wt = ctx_factory.wt
        (wt / "docs" / "architecture" / "overview.md").write_text(
            "# X\n\n```mermaid\ngraph TD\n  A-->D\n```\n", encoding="utf-8"
        )
        run(["git", "add", "-A"], wt)
        assert only(evaluate(["docs.diagrams_current"], ctx_factory(cwd=wt))).verdict.ok


class TestDocsGatesOnACommittedTree:
    """Repro for BUG_012 — see docs/backlog/BUG_012.md.

    Every test above dirties the working tree or the index, which is the state
    work is in *while it happens*. `docs.updated` is an exit gate on the review
    state (`docs/architecture/state_model.md` line 43), so it runs at the end of
    a phase, when the work is committed by construction and `git diff` reports
    nothing. Both rules read `git.changed_files` alone and bail on the empty
    set, so the gate the engine cannot be opted out of returns N/A — which is
    indistinguishable from a pass in the gate listing.

    `coverage.meets_floor` already survives this state by unioning three
    sources; `TestCoverageRule.test_committed_work_is_still_in_scope` is the
    same fixture shape asserted against the rule that gets it right.
    """

    def commit_source_only(self, wt):
        """A source change that lives ONLY in the branch's commits."""
        (wt / "app.py").write_text("def go():\n    return 1\n", encoding="utf-8")
        run(["git", "add", "app.py"], wt)
        run(["git", "commit", "-qm", "add a module and no doc"], wt)

    def test_the_fixture_leaves_a_genuinely_committed_tree(self, ctx_factory):
        """Guard for the two repros below, and deliberately not xfail.

        A strict xfail counts a failure in setup as the expected failure, so a
        repro that quietly dirtied the tree would report red for the wrong
        reason and prove nothing. This asserts the precondition separately: the
        working tree and index are empty, and the change is visible only
        through the branch's commits.
        """
        from invariant.core import git

        wt = ctx_factory.wt
        self.commit_source_only(wt)
        assert git.changed_files(wt) == []
        assert git.branch_files(wt, "master") == [Path("app.py")]

    def test_docs_updated_reads_a_source_only_commit(self, ctx_factory):
        """The blocking gate must judge committed work, not decline to look."""
        wt = ctx_factory.wt
        self.commit_source_only(wt)
        session = {"worktree": str(wt), "base": "master", "files": []}
        result = only(evaluate(["docs.updated"], ctx_factory(cwd=wt, session=session)))
        assert not result.verdict.skipped, result.verdict.message
        assert "source changed but no architecture doc did" in result.verdict.message

    def test_diagrams_current_reads_a_committed_doc_change(self, ctx_factory):
        wt = ctx_factory.wt
        (wt / "docs" / "architecture" / "overview.md").write_text(
            "# Prose only\n", encoding="utf-8"
        )
        run(["git", "add", "-A"], wt)
        run(["git", "commit", "-qm", "drop the diagram"], wt)
        session = {"worktree": str(wt), "base": "master", "files": []}
        result = only(
            evaluate(["docs.diagrams_current"], ctx_factory(cwd=wt, session=session))
        )
        assert not result.verdict.skipped, result.verdict.message
        assert "no diagram" in result.verdict.message

    def test_docs_updated_reads_the_declared_set_when_no_base_is_configured(
        self, ctx_factory
    ):
        """The declared file set is not belt-and-braces; it is the last carrier.

        `git.branch_files` returns `None` on an empty base before it attempts a
        ref at all (`src/invariant/core/git.py:137-138`), and the two readers of
        that field disagree about the fallback — `rules.py:586` reads `master`,
        `rules.py:1013` reads `""`. With the base absent and the work committed,
        the session's declared set is the only thing still holding an answer, so
        a two-source repair leaves this red.
        """
        from invariant.core import git

        wt = ctx_factory.wt
        self.commit_source_only(wt)
        session = {"worktree": str(wt), "base": "", "files": ["app.py"]}
        assert git.branch_files(wt, "") is None
        result = only(evaluate(["docs.updated"], ctx_factory(cwd=wt, session=session)))
        assert not result.verdict.skipped, result.verdict.message
        assert "source changed but no architecture doc did" in result.verdict.message


class TestTheChangedSetIsAnsweredOrRefused:
    """The other half of the BUG_012 repair: what an EMPTY set is allowed to mean.

    Widening the input set fixes the committed-tree case, and on its own it
    would leave a second inert path behind — the union is also empty when no
    source could answer, and reporting that as "nothing changed" is the same
    defect one step along. `coverage.meets_floor` already refuses there; these
    assert the two docs gates now refuse in the same shape, and that the
    refusal stays narrow enough not to swallow the genuine empty answer.
    """

    def clean_committed_repo(self, wt):
        run(["git", "commit", "-q", "--allow-empty", "-m", "no file changes"], wt)

    def test_docs_updated_refuses_an_unanswerable_set_rather_than_skipping(
        self, ctx_factory
    ):
        """An unknown base makes `branch_files` answer `None`.

        With the working tree clean and nothing declared, no source can say
        what changed. `docs.updated` is `block`, and a skip there reads as a
        pass — so the one honest verdict is a failure that says why.
        """
        wt = ctx_factory.wt
        session = {"worktree": str(wt), "base": "no-such-base", "files": []}
        result = only(evaluate(["docs.updated"], ctx_factory(cwd=wt, session=session)))
        assert not result.verdict.ok and not result.verdict.skipped
        assert "changed set could not be established" in result.verdict.message
        assert "no-such-base" in result.verdict.detail

    def test_diagrams_current_refuses_an_unanswerable_set_too(self, ctx_factory):
        wt = ctx_factory.wt
        session = {"worktree": str(wt), "base": "no-such-base", "files": []}
        result = only(
            evaluate(["docs.diagrams_current"], ctx_factory(cwd=wt, session=session))
        )
        assert not result.verdict.ok and not result.verdict.skipped
        assert "changed set could not be established" in result.verdict.message

    def test_a_missing_base_says_so_rather_than_naming_an_empty_one(self, ctx_factory):
        wt = ctx_factory.wt
        session = {"worktree": str(wt), "base": "", "files": []}
        result = only(evaluate(["docs.updated"], ctx_factory(cwd=wt, session=session)))
        assert "no base recorded" in result.verdict.detail

    def test_a_genuinely_empty_change_still_skips(self, ctx_factory):
        """The refusal must not swallow the real "nothing changed".

        Here the branch's commits ARE readable and report no paths, so the
        empty set is an answer rather than an absence of one. Failing here
        would block every gate evaluation that runs before any work.
        """
        wt = ctx_factory.wt
        self.clean_committed_repo(wt)
        session = {"worktree": str(wt), "base": "master", "files": []}
        result = only(evaluate(["docs.updated"], ctx_factory(cwd=wt, session=session)))
        assert result.verdict.skipped and result.verdict.message == "nothing changed"

    def test_a_source_only_commit_leaves_diagrams_current_with_nothing_to_say(
        self, ctx_factory
    ):
        """`no doc changed` survives, because it is a real answer.

        The changed set is non-empty and holds no doc. That is the case the
        rule was written for, and it must stay a skip now that an empty set
        can also mean "could not tell".
        """
        wt = ctx_factory.wt
        (wt / "app.py").write_text("x = 1\n", encoding="utf-8")
        run(["git", "add", "-A"], wt)
        run(["git", "commit", "-qm", "source only"], wt)
        session = {"worktree": str(wt), "base": "master", "files": []}
        result = only(
            evaluate(["docs.diagrams_current"], ctx_factory(cwd=wt, session=session))
        )
        assert result.verdict.skipped and "no doc changed" in result.verdict.message

    def test_a_committed_doc_change_alongside_source_passes(self, ctx_factory):
        """The positive committed case, which the repro does not cover.

        A gate that only ever fails on committed work would be as useless as
        one that only ever skips.
        """
        wt = ctx_factory.wt
        (wt / "app.py").write_text("x = 1\n", encoding="utf-8")
        (wt / "docs" / "architecture" / "overview.md").write_text(
            "# Overview\n\n```mermaid\ngraph TD\n  A-->E\n```\n", encoding="utf-8"
        )
        run(["git", "add", "-A"], wt)
        run(["git", "commit", "-qm", "source and doc together"], wt)
        session = {"worktree": str(wt), "base": "master", "files": []}
        ctx = ctx_factory(cwd=wt, session=session)
        updated = only(evaluate(["docs.updated"], ctx))
        assert updated.verdict.ok and not updated.verdict.skipped
        diagrams = only(evaluate(["docs.diagrams_current"], ctx))
        assert diagrams.verdict.ok and not diagrams.verdict.skipped

    def test_the_declared_set_alone_answers_when_git_cannot(self, ctx_factory):
        """The unset-base case, asserted on the helper rather than through a rule.

        No session record with an absent base has ever been observed; the path
        is reachable by construction, which is why it is constructed here.
        """
        from invariant.core.rules import _changed_set

        wt = ctx_factory.wt
        ctx = ctx_factory(
            cwd=wt, session={"worktree": str(wt), "base": "", "files": ["app.py"]}
        )
        changed = _changed_set(ctx)
        assert changed.answerable and changed.paths == (Path("app.py"),)

    def test_the_three_sources_are_unioned_without_duplicates(self, ctx_factory):
        """One path named by two sources is one path, not two.

        Left duplicated, every count in every verdict built on this set reads
        high, which is a small lie of exactly the kind these gates exist to
        catch.
        """
        from invariant.core.rules import _changed_set

        wt = ctx_factory.wt
        (wt / "app.py").write_text("x = 1\n", encoding="utf-8")
        run(["git", "add", "-A"], wt)
        run(["git", "commit", "-qm", "committed"], wt)
        (wt / "later.py").write_text("y = 2\n", encoding="utf-8")
        run(["git", "add", "-A"], wt)
        ctx = ctx_factory(
            cwd=wt,
            session={"worktree": str(wt), "base": "master", "files": ["app.py"]},
        )
        changed = _changed_set(ctx)
        assert changed.paths == (Path("app.py"), Path("later.py"))
        assert changed.base == "master" and changed.answerable


# -- test + coverage rules ---------------------------------------------


class TestTestRules:
    def test_no_run_recorded_fails(self, ctx_factory):
        assert not only(evaluate(["test.suite_runs"], ctx_factory(pipeline={}))).verdict.ok

    def test_zero_executed_is_caught_despite_a_pass(self, ctx_factory):
        pipeline = {"evidence": {"test_runs": [{"passed": True, "executed": 0}]}}
        result = only(evaluate(["test.suite_runs"], ctx_factory(pipeline=pipeline)))
        assert "executed zero tests" in result.verdict.message

    def test_green_run_passes(self, ctx_factory):
        pipeline = {"evidence": {"test_runs": [{"passed": True, "executed": 12}]}}
        assert only(evaluate(["test.all_pass"], ctx_factory(pipeline=pipeline))).verdict.ok

    def test_a_pass_with_a_hazard_is_not_accepted(self, ctx_factory):
        pipeline = {
            "evidence": {"test_runs": [{"passed": True, "executed": 5, "hazards": ["h"]}]}
        }
        result = only(evaluate(["test.all_pass"], ctx_factory(pipeline=pipeline)))
        assert "untrustworthy" in result.verdict.message

    def test_missing_red_phase_fails(self, ctx_factory):
        pipeline = {"evidence": {"test_runs": [{"phase": "green", "passed": True}]}}
        result = only(evaluate(["test.new_test_fails"], ctx_factory(pipeline=pipeline)))
        assert "no failing run was recorded" in result.verdict.message

    def test_a_red_phase_that_passed_is_rejected(self, ctx_factory):
        pipeline = {"evidence": {"test_runs": [{"phase": "red", "passed": True}]}}
        result = only(evaluate(["test.new_test_fails"], ctx_factory(pipeline=pipeline)))
        assert "recorded in the red phase passed" in result.verdict.message

    def test_a_genuine_red_passes(self, ctx_factory):
        pipeline = {"evidence": {"test_runs": [{"phase": "red", "passed": False}]}}
        assert only(evaluate(["test.new_test_fails"], ctx_factory(pipeline=pipeline))).verdict.ok


class TestCoverageRule:
    def test_below_floor_is_reported(self, ctx_factory):
        pipeline = {"evidence": {"coverage": {"a.py": 40.0, "b.py": 95.0}}}
        session = {"files": ["a.py", "b.py"], "base": "master"}
        result = only(
            evaluate(["coverage.meets_floor"], ctx_factory(session=session, pipeline=pipeline))
        )
        assert "1 file(s) below" in result.verdict.message
        assert "a.py" in result.verdict.detail

    def test_all_above_floor_passes(self, ctx_factory):
        pipeline = {"evidence": {"coverage": {"a.py": 95.0}}}
        session = {"files": ["a.py"], "base": "master"}
        assert only(
            evaluate(["coverage.meets_floor"], ctx_factory(session=session, pipeline=pipeline))
        ).verdict.ok

    def test_no_recorded_coverage_fails(self, ctx_factory):
        assert not only(evaluate(["coverage.meets_floor"], ctx_factory(pipeline={}))).verdict.ok

    # -- scope -------------------------------------------------------
    #
    # The touched set is what makes this a "touched" gate rather than a
    # whole-project one. Every test below is about the set being computed
    # from the wrong facts, which does not look like a scope bug from the
    # outside: it looks like a coverage failure in a file nobody edited.

    def test_committed_work_is_still_in_scope(self, ctx_factory):
        """`git diff` reports nothing for committed work.

        The exit gate runs after the commit, so reading only the working tree
        and the index empties the touched set at exactly the state where the
        gate is asked its question.
        """
        main, wt = ctx_factory.main, ctx_factory.wt
        (wt / "a.py").write_text("x = 1\n", encoding="utf-8")
        run(["git", "add", "-A"], wt)
        run(["git", "commit", "-qm", "work"], wt)

        pipeline = {"evidence": {"coverage": {"a.py": 40.0, "b.py": 10.0}}}
        session = {"worktree": str(wt), "base": "master", "files": []}
        result = only(
            evaluate(
                ["coverage.meets_floor"],
                ctx_factory(cwd=wt, session=session, pipeline=pipeline),
            )
        )
        assert "1 file(s) below" in result.verdict.message
        assert "a.py" in result.verdict.detail
        # b.py was never touched by this session. Holding it to the floor is
        # the whole defect: it blocks on a file the author did not write.
        assert "b.py" not in result.verdict.detail

    def test_an_empty_touched_set_does_not_widen_to_the_whole_project(self, ctx_factory):
        """Nothing touched is not the same question as everything touched.

        Falling through to the full measured set turns a touched-scope floor
        into a project-wide one, and every legacy file below the floor then
        blocks a change that never went near it.
        """
        main, wt = ctx_factory.main, ctx_factory.wt
        pipeline = {"evidence": {"coverage": {"legacy.py": 12.0}}}
        session = {"worktree": str(wt), "base": "master", "files": []}
        result = only(
            evaluate(
                ["coverage.meets_floor"],
                ctx_factory(cwd=wt, session=session, pipeline=pipeline),
            )
        )
        assert result.verdict.ok and result.verdict.skipped
        assert "legacy.py" not in (result.verdict.detail or "")

    def test_a_docs_only_change_has_nothing_for_the_floor_to_answer(self, ctx_factory):
        main, wt = ctx_factory.main, ctx_factory.wt
        (wt / "docs" / "architecture" / "overview.md").write_text("# hi\n", encoding="utf-8")
        run(["git", "add", "-A"], wt)
        run(["git", "commit", "-qm", "docs"], wt)

        pipeline = {"evidence": {"coverage": {"legacy.py": 12.0}}}
        session = {"worktree": str(wt), "base": "master", "files": []}
        result = only(
            evaluate(
                ["coverage.meets_floor"],
                ctx_factory(cwd=wt, session=session, pipeline=pipeline),
            )
        )
        assert result.verdict.ok and result.verdict.skipped

    def test_an_undeterminable_scope_blocks_rather_than_relaxing(self, ctx_factory):
        """An unanswered question is not a negative answer.

        With no base to diff against, the touched set cannot be established.
        Reporting that as "nothing was touched" would skip the floor on real
        source work — the vacuous green this gate exists to prevent.
        """
        main, wt = ctx_factory.main, ctx_factory.wt
        pipeline = {"evidence": {"coverage": {"legacy.py": 12.0}}}
        session = {"worktree": str(wt), "base": "no-such-base", "files": []}
        result = only(
            evaluate(
                ["coverage.meets_floor"],
                ctx_factory(cwd=wt, session=session, pipeline=pipeline),
            )
        )
        assert not result.verdict.ok
        assert "touched set could not be established" in result.verdict.message
        # And it must not answer for the project it could not scope.
        assert "legacy.py" not in (result.verdict.detail or "")


# -- privacy rule ------------------------------------------------------


class TestPrivacyRule:
    def test_a_leak_in_a_doc_write_is_blocked(self, ctx_factory):
        doc = ctx_factory.main / "docs" / "architecture" / "new.md"
        ctx = ctx_factory(payload=edit_payload(doc, "contact a.person@example.com"))
        result = only(evaluate(["privacy.clean"], ctx))
        assert not result.verdict.ok and result.level is Level.BLOCK

    def test_findings_are_redacted_in_the_report(self, ctx_factory):
        doc = ctx_factory.main / "docs" / "architecture" / "new.md"
        ctx = ctx_factory(payload=edit_payload(doc, "contact a.person@example.com"))
        detail = only(evaluate(["privacy.clean"], ctx)).verdict.detail
        assert "a.person@example.com" not in detail

    def test_a_clean_doc_passes(self, ctx_factory):
        doc = ctx_factory.main / "docs" / "architecture" / "new.md"
        ctx = ctx_factory(payload=edit_payload(doc, "nothing sensitive here"))
        assert only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_a_leak_in_a_commit_message_is_caught(self, ctx_factory):
        ctx = ctx_factory(payload=bash_payload("git commit -m 'fix by a.person@example.com'"))
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_source_files_are_out_of_scope_by_default(self, ctx_factory):
        ctx = ctx_factory(payload=edit_payload(ctx_factory.main / "app.py", "EMAIL='a@b.com'"))
        assert only(evaluate(["privacy.clean"], ctx)).verdict.skipped


# -- work-item rules ---------------------------------------------------


class TestItemRules:
    def test_fixing_an_unreproduced_bug_is_refused(self, ctx_factory):
        item = {"type": "bug", "status": "filed", "history": []}
        ctx = ctx_factory(extra={"item": item, "target_status": "fixed"})
        result = only(evaluate(["item.bug_reproduced_before_fix"], ctx))
        assert "never been reproduced" in result.verdict.message

    def test_a_reproduced_bug_may_be_fixed(self, ctx_factory):
        item = {"type": "bug", "status": "reproduced", "history": [{"to": "reproduced"}]}
        ctx = ctx_factory(extra={"item": item, "target_status": "fixed"})
        assert only(evaluate(["item.bug_reproduced_before_fix"], ctx)).verdict.ok

    def test_ideas_are_not_subject_to_the_repro_rule(self, ctx_factory):
        ctx = ctx_factory(extra={"item": {"type": "idea"}, "target_status": "done"})
        assert only(evaluate(["item.bug_reproduced_before_fix"], ctx)).verdict.skipped

    def test_closing_with_outstanding_residue_is_reported(self, ctx_factory):
        item = {"type": "bug", "status": "verified", "residue": ["a device soak is owed"]}
        ctx = ctx_factory(extra={"item": item, "target_status": "closed"})
        result = only(evaluate(["item.residue_matches_status"], ctx))
        assert "still owed" in result.verdict.message

    def test_closing_clean_passes(self, ctx_factory):
        item = {"type": "bug", "status": "verified", "residue": []}
        ctx = ctx_factory(extra={"item": item, "target_status": "closed"})
        assert only(evaluate(["item.residue_matches_status"], ctx)).verdict.ok


def test_render_includes_level_and_fixes(ctx_factory):
    ctx = ctx_factory(cwd=ctx_factory.main, payload=edit_payload(ctx_factory.main / "a.py"))
    rendered = only(evaluate(["session.no_edit_in_main_checkout"], ctx)).render()
    assert "BLOCK" in rendered and "try:" in rendered


class TestExemptionAcrossWorktrees:
    """The exemption must survive the multi-worktree case it exists for.

    Patterns are written project-relative. If the target sits in a different
    worktree than the manifest was found in, resolving against a single base
    yields an absolute path, which matches no project-relative pattern — so the
    exemption silently stops applying exactly when several worktrees exist.
    """

    def test_exempt_in_the_manifest_root(self, ctx_factory):
        ctx = ctx_factory(
            cwd=ctx_factory.main,
            payload=edit_payload(ctx_factory.main / ".claude" / "memory" / "a.md"),
        )
        assert only(evaluate(["session.isolated_before_edit"], ctx)).verdict.skipped

    def test_exempt_when_the_target_is_in_another_worktree(self, ctx_factory):
        # cwd is the worktree; the target is the same config path in the main
        # checkout. This is the case that regressed.
        ctx = ctx_factory(
            cwd=ctx_factory.wt,
            payload=edit_payload(ctx_factory.main / ".claude" / "memory" / "a.md"),
        )
        for rule_id in ("session.isolated_before_edit",
                        "session.no_edit_in_main_checkout",
                        "pipeline.started_before_edit"):
            assert only(evaluate([rule_id], ctx)).verdict.skipped, rule_id

    def test_exempt_when_cwd_is_main_and_target_is_in_a_worktree(self, ctx_factory):
        ctx = ctx_factory(
            cwd=ctx_factory.main,
            payload=edit_payload(ctx_factory.wt / ".claude" / "memory" / "a.md"),
        )
        assert only(evaluate(["session.isolated_before_edit"], ctx)).verdict.skipped

    def test_a_source_file_is_still_not_exempt(self, ctx_factory):
        ctx = ctx_factory(
            cwd=ctx_factory.wt,
            payload=edit_payload(ctx_factory.main / "src" / "app.py"),
        )
        assert not only(evaluate(["session.no_edit_in_main_checkout"], ctx)).verdict.ok

    def test_no_patterns_means_nothing_is_exempt(self, ctx_factory):
        write_manifest(ctx_factory.main, {"session": {"exempt_paths": []}})
        ctx = ctx_factory(
            cwd=ctx_factory.main,
            payload=edit_payload(ctx_factory.main / ".claude" / "memory" / "a.md"),
        )
        assert not only(evaluate(["session.no_edit_in_main_checkout"], ctx)).verdict.ok


class TestAWorktreeUnderAnExemptDirectoryStillEnforces:
    """A worktree's LOCATION must not exempt the sources inside it.

    ``session.worktree_dir`` lets a project put worktrees inside the repository,
    which some hosts require before they will treat the directory as trusted.
    The default exemption covers ``.claude/*``, and fnmatch's ``*`` spans path
    separators, so every file in a worktree placed there spells as an exempt
    path when resolved against the main checkout. The isolation rule consults
    the exemption first, so the result is not an error but a skip: enforcement
    off, silently, in the multi-worktree case it exists for.
    """

    @pytest.fixture
    def nested(self, repo):
        main, _ = repo
        inner = main / ".claude" / "worktrees" / "wt-1"
        inner.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "worktree", "add", "-q", str(inner), "-b", "session/nested"], main)
        return main, inner

    @pytest.fixture
    def nested_ctx(self, nested):
        main, inner = nested
        from invariant.core.manifest import Manifest

        def build(payload=None, cwd=None):
            return RuleContext(
                manifest=Manifest.load(main),
                cwd=Path(cwd or main),
                session=None,
                pipeline=None,
                payload=payload or {},
                extra={},
            )

        build.main = main
        build.inner = inner
        return build

    def test_a_source_file_in_the_nested_worktree_is_not_exempt(self, nested_ctx):
        """The regression this guards: skipped here means unenforced."""
        ctx = nested_ctx(payload=edit_payload(nested_ctx.inner / "src" / "app.py"))
        verdict = only(evaluate(["session.no_edit_in_main_checkout"], ctx)).verdict
        assert not verdict.skipped, "the worktree's own location exempted its sources"
        assert verdict.ok, "an edit inside a linked worktree is what the rule allows"

    def test_the_main_checkouts_own_config_is_still_exempt(self, nested_ctx):
        ctx = nested_ctx(payload=edit_payload(nested_ctx.main / ".claude" / "settings.json"))
        assert only(evaluate(["session.isolated_before_edit"], ctx)).verdict.skipped

    def test_config_inside_the_nested_worktree_is_exempt_on_its_own_merit(self, nested_ctx):
        """Judged by its path WITHIN the worktree, not by where the worktree is.

        Distinct from the source-file case: this one is exempt for the reason
        the exemption exists, and would stay exempt at any worktree location.
        """
        target = nested_ctx.inner / ".claude" / "memory" / "a.md"
        ctx = nested_ctx(payload=edit_payload(target))
        assert only(evaluate(["session.isolated_before_edit"], ctx)).verdict.skipped

    def test_the_main_checkout_is_still_refused_from_inside_the_nested_worktree(
        self, nested_ctx
    ):
        """Nesting must not make the main checkout look like part of a worktree."""
        ctx = nested_ctx(
            cwd=nested_ctx.inner,
            payload=edit_payload(nested_ctx.main / "src" / "app.py"),
        )
        assert not only(evaluate(["session.no_edit_in_main_checkout"], ctx)).verdict.ok


class TestPrivacyScopeAcrossWorktrees:
    """Scope must not depend on which worktree the caller is standing in."""

    def _memory_write(self, path):
        return edit_payload(path, "the crash fired at 14:05")

    def test_memory_in_the_manifest_root(self, ctx_factory):
        target = ctx_factory.main / ".claude" / "memory" / "note.md"
        ctx = ctx_factory(cwd=ctx_factory.main, payload=self._memory_write(target))
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_memory_in_the_main_checkout_seen_from_a_worktree(self, ctx_factory):
        # The regression: scope computed from the worktree excluded a file in
        # the main checkout, so the gate reported nothing to check.
        target = ctx_factory.main / ".claude" / "memory" / "note.md"
        ctx = ctx_factory(cwd=ctx_factory.wt, payload=self._memory_write(target))
        verdict = only(evaluate(["privacy.clean"], ctx)).verdict
        assert not verdict.ok and not verdict.skipped

    def test_memory_in_a_worktree_seen_from_main(self, ctx_factory):
        target = ctx_factory.wt / ".claude" / "memory" / "note.md"
        ctx = ctx_factory(cwd=ctx_factory.main, payload=self._memory_write(target))
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_a_source_file_stays_out_of_scope(self, ctx_factory):
        ctx = ctx_factory(
            cwd=ctx_factory.wt,
            payload=edit_payload(ctx_factory.main / "app.py", "EMAIL='a@b.com'"),
        )
        assert only(evaluate(["privacy.clean"], ctx)).verdict.skipped

    def test_scope_all_covers_source_too(self, ctx_factory):
        write_manifest(ctx_factory.main, {"privacy": {"scope": ["all"]}})
        ctx = ctx_factory(
            cwd=ctx_factory.wt,
            payload=edit_payload(ctx_factory.main / "app.py", "mail a@b.com"),
        )
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_empty_scope_checks_nothing(self, ctx_factory):
        write_manifest(ctx_factory.main, {"privacy": {"scope": []}})
        target = ctx_factory.main / ".claude" / "memory" / "note.md"
        ctx = ctx_factory(cwd=ctx_factory.main, payload=self._memory_write(target))
        assert only(evaluate(["privacy.clean"], ctx)).verdict.skipped


class TestOutOfProjectPaths:
    """One project's hooks must not govern edits to a different repository."""

    def test_an_edit_in_another_repository_is_not_governed(self, ctx_factory, tmp_path):
        other = tmp_path / "unrelated-repo"
        (other / "src").mkdir(parents=True)
        target = other / "src" / "thing.py"
        target.write_text("x", encoding="utf-8")
        ctx = ctx_factory(cwd=ctx_factory.main, payload=edit_payload(target))
        for rule_id in ("session.isolated_before_edit", "pipeline.started_before_edit"):
            verdict = only(evaluate([rule_id], ctx)).verdict
            assert verdict.skipped, f"{rule_id}: {verdict.message}"

    def test_an_edit_in_this_project_is_still_governed(self, ctx_factory):
        ctx = ctx_factory(
            cwd=ctx_factory.main, payload=edit_payload(ctx_factory.main / "app.py")
        )
        assert not only(evaluate(["session.isolated_before_edit"], ctx)).verdict.ok

    def test_an_edit_in_a_sibling_worktree_is_still_governed(self, ctx_factory):
        ctx = ctx_factory(
            cwd=ctx_factory.main, payload=edit_payload(ctx_factory.wt / "app.py")
        )
        assert not only(evaluate(["session.isolated_before_edit"], ctx)).verdict.ok


class TestAgentRoleGates:
    """Did an agent actually fire — read from evidence, not from intent."""

    def test_na_when_the_role_has_no_agent_configured(self, ctx_factory):
        write_manifest(ctx_factory.main, {
            "version": 1,
            "project": {"name": "demo"},
            "agents": {"roles": {"design": []}},
        })
        ctx = ctx_factory(pipeline={"evidence": {}})
        verdict = evaluate(["agents.design_agent_ran"], ctx)[0].verdict
        # Skipped, NOT passed. A project with no design agent has nothing
        # checked here, and saying "clean" would claim otherwise.
        assert verdict.skipped
        assert verdict.ok

    def test_fails_when_no_agent_ran(self, ctx_factory):
        ctx = ctx_factory(pipeline={"evidence": {"agents_run": []}})
        assert not evaluate(["agents.design_agent_ran"], ctx)[0].verdict.ok

    def test_passes_when_the_configured_agent_ran(self, ctx_factory):
        ctx = ctx_factory(pipeline={"evidence": {"agents_run": ["feature-architect"]}})
        verdict = evaluate(["agents.design_agent_ran"], ctx)[0].verdict
        assert verdict.ok and not verdict.skipped

    def test_a_different_agent_does_not_satisfy_the_role(self, ctx_factory):
        """An agent ran, but not one that does this job."""
        ctx = ctx_factory(pipeline={"evidence": {"agents_run": ["backlog-curator"]}})
        verdict = evaluate(["agents.design_agent_ran"], ctx)[0].verdict
        assert not verdict.ok
        assert "backlog-curator" in verdict.detail  # names what did run

    def test_the_review_role_is_independent_of_the_design_role(self, ctx_factory):
        ctx = ctx_factory(pipeline={"evidence": {"agents_run": ["feature-architect"]}})
        assert evaluate(["agents.design_agent_ran"], ctx)[0].verdict.ok
        assert not evaluate(["agents.review_agent_ran"], ctx)[0].verdict.ok

    def test_a_project_may_name_its_own_agent_for_a_role(self, ctx_factory):
        write_manifest(ctx_factory.main, {
            "version": 1,
            "project": {"name": "demo"},
            "agents": {"roles": {"review": ["house-reviewer"]}},
        })
        ctx = ctx_factory(pipeline={"evidence": {"agents_run": ["house-reviewer"]}})
        assert evaluate(["agents.review_agent_ran"], ctx)[0].verdict.ok

    def test_fails_outside_a_pipeline_rather_than_passing(self, ctx_factory):
        ctx = ctx_factory(pipeline=None)
        assert not evaluate(["agents.design_agent_ran"], ctx)[0].verdict.ok

    def test_the_fix_names_the_agents_that_would_satisfy_it(self, ctx_factory):
        ctx = ctx_factory(pipeline={"evidence": {}})
        verdict = evaluate(["agents.design_agent_ran"], ctx)[0].verdict
        assert any("feature-architect" in fix for fix in verdict.fixes)



class TestPushBarrierAndExemptPaths:
    """Config and memory commit straight to the shared branch by design.

    Such a commit can never acquire a session, so a barrier that demands one
    enforces nothing — it makes those commits permanently unpushable. That was
    not hypothetical: it blocked a real push of already-committed memory.
    """

    PUSH = {"tool_name": "Bash", "tool_input": {"command": "git " + "push origin master"}}

    @pytest.fixture(autouse=True)
    def publishing_on(self, ctx_factory):
        write_manifest(
            ctx_factory.main,
            {"version": 1, "project": {"name": "demo"}, "session": {"push": True}},
        )

    def push_ctx(self, ctx_factory, cwd, session=None):
        return ctx_factory(cwd=cwd, session=session, payload=self.PUSH)

    def with_upstream(self, main, tmp_path, files):
        """A repo with a real upstream, ahead by one commit touching `files`."""
        remote = tmp_path / "remote.git"
        run(["git", "init", "-q", "--bare", str(remote)], main)
        run(["git", "remote", "add", "origin", str(remote)], main)
        run(["git", "push", "-q", "-u", "origin", "master"], main)
        for rel in files:
            path = main / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x\n", encoding="utf-8")
        if files:
            run(["git", "add", "-A"], main)
            run(["git", "commit", "-qm", "ahead"], main)

    def verdict(self, ctx_factory, tmp_path, files, session=None):
        main = ctx_factory.main
        self.with_upstream(main, tmp_path, files)
        ctx = self.push_ctx(ctx_factory, main, session=session)
        return evaluate(["session.push_barrier"], ctx)[0].verdict

    def test_a_push_of_only_exempt_paths_is_allowed(self, ctx_factory, tmp_path):
        verdict = self.verdict(ctx_factory, tmp_path, [".claude/memory/a-note.md"])
        # Skipped, not passed: the barrier did not apply, rather than being met.
        assert verdict.skipped and verdict.ok

    def test_one_non_exempt_path_reinstates_the_barrier(self, ctx_factory, tmp_path):
        verdict = self.verdict(
            ctx_factory, tmp_path, [".claude/memory/a-note.md", "src/app.py"]
        )
        assert not verdict.ok

    def test_no_upstream_does_not_open_the_gate(self, ctx_factory):
        """An unanswerable question is not a negative answer."""
        ctx = self.push_ctx(ctx_factory, ctx_factory.main)
        assert not evaluate(["session.push_barrier"], ctx)[0].verdict.ok

    def test_nothing_ahead_does_not_open_the_gate(self, ctx_factory, tmp_path):
        assert not self.verdict(ctx_factory, tmp_path, []).ok

    def test_a_registered_session_is_still_held_to_the_barrier(
        self, ctx_factory, tmp_path
    ):
        """The relaxation is for sessionless pushes only."""
        verdict = self.verdict(
            ctx_factory,
            tmp_path,
            [".claude/memory/a-note.md"],
            session={"id": "s1", "state": "working", "push_count": 0},
        )
        assert not verdict.ok



class TestPublishingIsOptIn:
    """Off by default: a session ends at the merge, not at a remote.

    The work is on the base branch and every worktree already sees it — they
    share one object database. Publishing costs a CI run and is a decision
    about the project rather than about whichever session finished last.
    """

    PUSH = {"tool_name": "Bash", "tool_input": {"command": "git " + "push origin master"}}

    def test_the_barrier_abstains_when_publishing_is_off(self, ctx_factory):
        ctx = ctx_factory(session={"state": "merged", "push_count": 3}, payload=self.PUSH)
        verdict = only(evaluate(["session.push_barrier"], ctx)).verdict
        # Skipped, not passed. There is no session push for this to be the
        # fourth of, so the barrier has nothing to say — which is a different
        # statement from "this push is fine".
        assert verdict.skipped

    def test_the_barrier_returns_when_publishing_is_enabled(self, ctx_factory):
        write_manifest(
            ctx_factory.main,
            {"version": 1, "project": {"name": "demo"}, "session": {"push": True}},
        )
        ctx = ctx_factory(session={"state": "merged", "push_count": 3}, payload=self.PUSH)
        assert not only(evaluate(["session.push_barrier"], ctx)).verdict.ok

    def test_the_pushed_state_is_refused_by_default(self, ctx_factory):
        ctx = ctx_factory(session={"state": "merged"})
        verdict = only(evaluate(["session.push_enabled"], ctx)).verdict
        assert not verdict.ok
        assert "session.push: true" in " ".join(verdict.fixes)

    def test_the_pushed_state_opens_when_enabled(self, ctx_factory):
        write_manifest(
            ctx_factory.main,
            {"version": 1, "project": {"name": "demo"}, "session": {"push": True}},
        )
        assert only(evaluate(["session.push_enabled"], ctx_factory())).verdict.ok

    def test_teardown_is_owed_from_merged_not_only_from_pushed(self, ctx_factory):
        """The common ending must be the one that is checked.

        Publishing is the detour now, so a rule that only looked at `pushed`
        would exempt every session that took the normal route.
        """
        ctx = ctx_factory(session={"state": "merged", "worktree": str(ctx_factory.wt)})
        assert not only(evaluate(["session.teardown_complete"], ctx)).verdict.ok

    def test_teardown_is_satisfied_once_the_worktree_is_gone(self, ctx_factory):
        ctx = ctx_factory(session={"state": "merged", "worktree": "/nonexistent/wt"})
        verdict = only(evaluate(["session.teardown_complete"], ctx)).verdict
        assert verdict.ok and not verdict.skipped



class TestCoverageScopeAndPathSpelling:
    """The gate had three separate holes, and one fix would have hidden another.

    `record_coverage` had no callers, so the evidence could never exist;
    `coverage_scope: touched` was configured but never implemented; and the
    reporter emits absolute paths while git emits repo-relative ones. Wiring
    only the first would have produced a gate that passed vacuously on an empty
    intersection — quieter than blocking, and much worse.
    """

    def ctx_with(self, ctx_factory, coverage, changed=()):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo"},
                "methodology": {"gates": {"coverage_min": 90, "coverage_scope": "touched"}},
            },
        )
        # Declared on the session rather than left dirty in the tree: by the
        # time this gate runs the work is normally committed, and `git diff`
        # reports nothing for committed work.
        return ctx_factory(
            session={"id": "s1", "files": list(changed)},
            pipeline={"evidence": {"coverage": coverage}},
        )

    def verdict(self, ctx):
        return only(evaluate(["coverage.meets_floor"], ctx)).verdict

    def test_absolute_reporter_paths_match_relative_changed_files(self, ctx_factory):
        """The mismatch that would have made the gate pass on nothing."""
        absolute = str(ctx_factory.main / "src" / "app.swift")
        ctx = self.ctx_with(ctx_factory, {absolute: 95.0}, changed=["src/app.swift"])
        verdict = self.verdict(ctx)
        assert verdict.ok and not verdict.skipped

    def test_a_changed_file_below_the_floor_is_caught(self, ctx_factory):
        absolute = str(ctx_factory.main / "src" / "app.swift")
        ctx = self.ctx_with(ctx_factory, {absolute: 40.0}, changed=["src/app.swift"])
        assert not self.verdict(ctx).ok

    def test_an_untouched_file_below_the_floor_is_ignored(self, ctx_factory):
        """`coverage_scope: touched` means touched, and it was never applied."""
        ctx = self.ctx_with(
            ctx_factory,
            {
                str(ctx_factory.main / "src" / "app.swift"): 95.0,
                str(ctx_factory.main / "src" / "ancient.swift"): 10.0,
            },
            changed=["src/app.swift"],
        )
        assert self.verdict(ctx).ok

    def test_no_overlap_fails_rather_than_passing_vacuously(self, ctx_factory):
        ctx = self.ctx_with(
            ctx_factory,
            {"/somewhere/else/other.swift": 100.0},
            changed=["src/app.swift"],
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "none of it covers the changed files" in verdict.message

    def test_a_docs_only_change_does_not_trip_the_overlap_check(self, ctx_factory):
        """Nothing measures coverage of a markdown file."""
        ctx = self.ctx_with(
            ctx_factory,
            {str(ctx_factory.main / "src" / "app.swift"): 95.0},
            changed=["docs/architecture/overview.md"],
        )
        assert self.verdict(ctx).ok

    def test_missing_evidence_names_the_command_that_produces_it(self, ctx_factory):
        """The old advice named a command no code path ran."""
        ctx = self.ctx_with(ctx_factory, {})
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert any("invariant pipeline coverage" in fix for fix in verdict.fixes)



class TestCoverageAcrossTwoRoots:
    """An isolated session has two roots, and the gate saw only one.

    State lives in the MAIN checkout, because worktrees share one `.git`.
    Compilation happens in the session's WORKTREE, so every path the coverage
    tool emits carries the worktree prefix. Hooks run from the main checkout,
    so the rule was stripping a prefix the measured paths did not have — the
    relative form never appeared and the intersection was empty for a reason
    that had nothing to do with coverage.

    Invisible in a single-checkout repo, which is why it survived until a
    session actually isolated.
    """

    def ctx_for(self, ctx_factory, coverage, declared):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo"},
                "methodology": {"gates": {"coverage_min": 90, "coverage_scope": "touched"}},
            },
        )
        return ctx_factory(
            cwd=ctx_factory.main,  # the rule runs from the MAIN checkout
            session={"id": "s1", "files": declared, "worktree": str(ctx_factory.wt)},
            pipeline={"evidence": {"coverage": coverage}},
        )

    def verdict(self, ctx):
        return only(evaluate(["coverage.meets_floor"], ctx)).verdict

    def test_the_reported_failure_now_passes(self, ctx_factory):
        """End to end, as reported.

        Note this passes on EITHER mechanism — worktree-root stripping or
        suffix matching — so it does not isolate which. `TestCoverageRoots`
        below tests them apart; this one asserts the user-visible outcome.
        """
        measured = {
            str(ctx_factory.wt / "Heap" / "Intake" / "FileBytesIngester.swift"): 100.0,
        }
        ctx = self.ctx_for(
            ctx_factory, measured, ["Heap/Intake/FileBytesIngester.swift"]
        )
        verdict = self.verdict(ctx)
        assert verdict.ok and not verdict.skipped, verdict.message

    def test_the_main_checkout_root_still_works(self, ctx_factory):
        """A session that never isolated has one root, and must keep working."""
        measured = {str(ctx_factory.main / "src" / "app.swift"): 95.0}
        ctx = ctx_factory(
            cwd=ctx_factory.main,
            session={"id": "s1", "files": ["src/app.swift"]},
            pipeline={"evidence": {"coverage": measured}},
        )
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo"},
                "methodology": {"gates": {"coverage_min": 90, "coverage_scope": "touched"}},
            },
        )
        assert self.verdict(ctx).ok

    def test_a_root_nobody_anticipated_still_matches_by_suffix(self, ctx_factory):
        """Belt to the braces: a prefix from neither known root."""
        measured = {"/build/somewhere/else/Heap/Intake/FileBytesIngester.swift": 100.0}
        ctx = self.ctx_for(
            ctx_factory, measured, ["Heap/Intake/FileBytesIngester.swift"]
        )
        assert self.verdict(ctx).ok

    def test_a_partial_intersection_fails_rather_than_passing(self, ctx_factory):
        """The failure mode a lenient matcher would introduce.

        Suffix matching is loose enough that a bug in it yields PARTIAL
        overlaps rather than empty ones — and a partial overlap that answered
        only for the files it matched would report a pass for the ones it never
        found. That is the vacuous green one layer below the one already
        closed.
        """
        measured = {
            str(ctx_factory.wt / "Heap" / "Intake" / "FileBytesIngester.swift"): 100.0,
        }
        ctx = self.ctx_for(
            ctx_factory,
            measured,
            [
                "Heap/Intake/FileBytesIngester.swift",
                "Heap/Intake/IntakeDrainDisposition.swift",  # never measured
            ],
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "no coverage row" in verdict.message
        assert "IntakeDrainDisposition.swift" in verdict.detail

    def test_docs_in_the_declared_set_are_not_expected_to_have_coverage(
        self, ctx_factory
    ):
        """A session declares its docs too; nothing measures a .rst file."""
        measured = {
            str(ctx_factory.wt / "Heap" / "Intake" / "FileBytesIngester.swift"): 100.0,
        }
        ctx = self.ctx_for(
            ctx_factory,
            measured,
            [
                "Heap/Intake/FileBytesIngester.swift",
                "docs/architecture/intake_sources.rst",
                "docs/backlog.rst",
            ],
        )
        assert self.verdict(ctx).ok

    def test_a_matched_file_below_the_floor_still_fails(self, ctx_factory):
        measured = {
            str(ctx_factory.wt / "Heap" / "Intake" / "FileBytesIngester.swift"): 40.0,
        }
        ctx = self.ctx_for(
            ctx_factory, measured, ["Heap/Intake/FileBytesIngester.swift"]
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "below the" in verdict.message


class TestOverstatingHazardsBlock:
    """Bent low and bent high are not the same risk.

    Low blocks work that was fine: visible, and self-correcting, because being
    blocked is what sends someone to look. High passes work that was never
    covered, silently, and nobody looks. Same evidence, opposite handling.
    """

    def ctx_for(self, ctx_factory, run):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo"},
                "methodology": {"gates": {"coverage_min": 90, "coverage_scope": "all"}},
            },
        )
        return ctx_factory(
            session={"id": "s1"},
            pipeline={
                "evidence": {
                    "coverage": {"src/app.swift": 100.0},
                    "coverage_run": run,
                }
            },
        )

    def verdict(self, ctx):
        return only(evaluate(["coverage.meets_floor"], ctx)).verdict

    def test_an_understating_hazard_is_annotated_not_blocked(self, ctx_factory):
        ctx = self.ctx_for(
            ctx_factory,
            {"hazards": ["xcode.exit65_drops_suites"], "overstating_hazards": []},
        )
        verdict = self.verdict(ctx)
        assert verdict.ok
        assert "exit65_drops_suites" in verdict.message

    def test_an_overstating_hazard_blocks(self, ctx_factory):
        ctx = self.ctx_for(
            ctx_factory,
            {"hazards": ["some.unknown"], "overstating_hazards": ["some.unknown"]},
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "cannot be trusted upward" in verdict.message

    def test_a_failing_suite_is_recorded_in_a_passing_verdict(self, ctx_factory):
        ctx = self.ctx_for(
            ctx_factory,
            {"hazards": [], "overstating_hazards": [], "suite_passed": False, "failed": 104},
        )
        verdict = self.verdict(ctx)
        assert verdict.ok
        assert "104" in verdict.message



class TestCoverageRoots:
    """The two mechanisms, tested apart.

    The end-to-end case passes on either one, so an integration test cannot say
    which is working — and a test whose name claims a mechanism it does not
    exercise is the shape this whole day has been about.
    """

    def test_the_session_worktree_is_tried_first(self, ctx_factory):
        ctx = ctx_factory(session={"id": "s1", "worktree": str(ctx_factory.wt)})
        assert _coverage_roots(ctx)[0] == Path(ctx_factory.wt)

    def test_a_session_without_a_worktree_falls_back_to_the_repo(self, ctx_factory):
        ctx = ctx_factory(session={"id": "s1"})
        roots = _coverage_roots(ctx)
        assert roots and Path(ctx_factory.wt) not in roots

    def test_no_session_at_all_still_yields_roots(self, ctx_factory):
        assert _coverage_roots(ctx_factory(session=None))

    def test_a_worktree_path_relativises_against_the_worktree(self, ctx_factory):
        """The mechanism the reported bug needed, in isolation.

        The rule runs from the main checkout while the measured path carries
        the worktree prefix, so stripping the checkout removes nothing.
        """
        measured = ctx_factory.wt / "Heap" / "Intake" / "F.swift"
        assert (
            _relative_to_any(measured, [Path(ctx_factory.wt), Path(ctx_factory.main)])
            == "Heap/Intake/F.swift"
        )

    def test_the_main_checkout_root_is_used_when_it_is_the_match(self, ctx_factory):
        measured = ctx_factory.main / "src" / "app.swift"
        assert (
            _relative_to_any(measured, [Path(ctx_factory.wt), Path(ctx_factory.main)])
            == "src/app.swift"
        )

    def test_a_path_under_no_known_root_is_left_absolute(self, ctx_factory):
        """Left absolute rather than mangled, so suffix matching can still try."""
        out = _relative_to_any("/elsewhere/x/F.swift", [Path(ctx_factory.main)])
        assert out.startswith("/")

    def test_a_relative_path_is_returned_unchanged(self, ctx_factory):
        assert _relative_to_any("Heap/Intake/F.swift", [Path(ctx_factory.main)]) == (
            "Heap/Intake/F.swift"
        )


class TestSameFile:
    """Suffix matching, tested apart from the root stripping."""

    def test_identical_spellings_match(self):
        assert _same_file("a/b.swift", "a/b.swift")

    def test_a_longer_measured_path_matches(self):
        assert _same_file("Heap/Intake/F.swift", "/wt/Heap/Intake/F.swift")

    def test_a_longer_declared_path_matches(self):
        assert _same_file("/wt/Heap/Intake/F.swift", "Heap/Intake/F.swift")

    def test_a_partial_component_does_not_match(self):
        """`Ingester.swift` must not match `FileBytesIngester.swift`."""
        assert not _same_file("Ingester.swift", "/wt/Heap/FileBytesIngester.swift")

    def test_different_files_do_not_match(self):
        assert not _same_file("Heap/A.swift", "/wt/Heap/B.swift")


class TestVerdictRendering:
    """The marker must say what happened, not what the rule's level is.

    `[BLOCK] coverage.meets_floor: all 3 file(s) at or above 90%` is a PASS,
    and it was read as a failure by someone who then had to check `--json` to
    find `ok: true`. The level is a property of the rule; the verdict is what
    the reader came for.
    """

    def evaluated(self, verdict, level=Level.BLOCK):
        from invariant.core.rules import Evaluated

        return Evaluated("some.rule", level, verdict)

    def test_a_pass_leads_with_ok_even_at_block_level(self):
        line = self.evaluated(Verdict.clean("all 3 file(s) above 90%")).render()
        assert line.startswith("[ok]")
        assert "BLOCK" not in line

    def test_a_skip_says_skip(self):
        assert self.evaluated(Verdict.na("not applicable")).render().startswith("[skip]")

    def test_a_blocking_failure_still_leads_with_block(self):
        """On a failure the level IS the news — it decides whether work stops."""
        assert self.evaluated(Verdict.fail("nope")).render().startswith("[BLOCK]")

    def test_an_advisory_failure_leads_with_warn(self):
        line = self.evaluated(Verdict.fail("nope"), Level.WARN).render()
        assert line.startswith("[warn]")

    def test_detail_and_fixes_still_render(self):
        line = self.evaluated(
            Verdict.fail("nope", detail="because", fixes=("do this",))
        ).render()
        assert "because" in line and "try: do this" in line


class TestAllPassReadsTheRunThatHappened:
    """A green here must mean THIS run was green.

    It reported "suite is green and the pass is trustworthy" while a
    whole-suite run with 625 failures had just finished — because the coverage
    path ran the suite and recorded it somewhere the rule never looked, so the
    rule answered about an older, narrower, greener run.
    """

    def ctx_with(self, ctx_factory, runs):
        return ctx_factory(pipeline={"evidence": {"test_runs": runs}})

    def verdict(self, ctx):
        return only(evaluate(["test.all_pass"], ctx)).verdict

    def test_a_failing_run_is_red(self, ctx_factory):
        ctx = self.ctx_with(ctx_factory, [{"passed": False, "failed": 625, "executed": 13945}])
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "625" in verdict.message

    def test_an_unknown_failure_count_does_not_read_as_zero(self, ctx_factory):
        """`failed: None` means the run failed and the count could not be
        parsed. "0 failing" would be green prose on a red verdict."""
        ctx = self.ctx_with(
            ctx_factory, [{"passed": False, "failed": None, "executed": 13945}]
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "unknown" in verdict.message
        assert "0 failing" not in verdict.message

    def test_a_clean_exit_with_zero_executed_is_not_green(self, ctx_factory):
        ctx = self.ctx_with(ctx_factory, [{"passed": True, "failed": 0, "executed": 0}])
        assert not self.verdict(ctx).ok

    def test_a_later_red_run_overrides_an_earlier_green_one(self, ctx_factory):
        """The reported failure, as a rule-level test."""
        ctx = self.ctx_with(
            ctx_factory,
            [
                {"phase": "green", "passed": True, "failed": 0, "executed": 34},
                {"phase": "coverage", "passed": False, "failed": 625, "executed": 13945},
            ],
        )
        assert not self.verdict(ctx).ok

    def test_a_genuine_green_still_passes(self, ctx_factory):
        ctx = self.ctx_with(ctx_factory, [{"passed": True, "failed": 0, "executed": 34}])
        assert self.verdict(ctx).ok


class TestMergeDirection:
    """Merging a branch into ITSELF is the trap. The mainline into a feature
    branch is the ordinary way to pick up other people's work — and blocking it
    pushed conflict resolution to the final merge, where it is harder and has
    no tests around it.
    """

    def ctx_in_worktree(self, ctx_factory, command):
        return ctx_factory(cwd=ctx_factory.wt, payload=bash_payload(command))

    def verdict(self, ctx):
        return only(evaluate(["session.ff_merge_target"], ctx)).verdict

    def test_mainline_into_a_feature_branch_is_allowed(self, ctx_factory):
        """The reported false positive. The worktree is on session/x."""
        verdict = self.verdict(self.ctx_in_worktree(ctx_factory, "git merge master"))
        assert verdict.ok

    def test_merging_the_branch_into_itself_is_refused(self, ctx_factory):
        verdict = self.verdict(
            self.ctx_in_worktree(ctx_factory, "git merge session/x")
        )
        assert not verdict.ok
        assert "into itself" in verdict.message

    def test_its_own_remote_ref_counts_as_itself(self, ctx_factory):
        assert not self.verdict(
            self.ctx_in_worktree(ctx_factory, "git merge origin/session/x")
        ).ok

    def test_merge_base_is_not_a_merge(self, ctx_factory):
        """`\\bmerge\\b` also matches `merge-base`; that is a read, not a merge."""
        assert self.verdict(
            self.ctx_in_worktree(ctx_factory, "git merge-base HEAD master")
        ).skipped

    def test_log_with_no_merges_is_not_a_merge(self, ctx_factory):
        assert self.verdict(
            self.ctx_in_worktree(ctx_factory, "git log --no-merges")
        ).skipped

    def test_a_heredoc_that_merely_writes_the_word_is_not_a_merge(self, ctx_factory):
        assert self.verdict(
            self.ctx_in_worktree(ctx_factory, "cat > f <<'EOF'\ngit merge x\nEOF")
        ).skipped

    def test_from_the_main_checkout_nothing_is_refused(self, ctx_factory):
        ctx = ctx_factory(cwd=ctx_factory.main, payload=bash_payload("git merge session/x"))
        assert self.verdict(ctx).ok

    def test_the_session_merge_command_still_requires_the_main_checkout(
        self, ctx_factory
    ):
        """The other half of the rule, unchanged: landing a branch on the base."""
        ctx = ctx_factory(cwd=ctx_factory.wt, extra={"merging": True})
        assert not self.verdict(ctx).ok


class TestPushBarrierMatchesRealPushes:
    def test_a_heredoc_writing_the_words_is_not_a_push(self, ctx_factory):
        """It refused one of these while a test file was being written."""
        ctx = ctx_factory(
            payload=bash_payload("cat > f <<'EOF'\nrun git push origin master\nEOF")
        )
        assert only(evaluate(["session.push_barrier"], ctx)).verdict.skipped

    def test_a_real_push_is_still_matched(self, ctx_factory):
        write_manifest(
            ctx_factory.main,
            {"version": 1, "project": {"name": "demo"}, "session": {"push": True}},
        )
        ctx = ctx_factory(
            session={"state": "working", "push_count": 0},
            payload=bash_payload("git push origin master"),
        )
        assert not only(evaluate(["session.push_barrier"], ctx)).verdict.ok


class TestDiagramGateAbstainsForDocsPipelines:
    def ctx_for(self, ctx_factory, methodology):
        doc = ctx_factory.main / "docs" / "architecture" / "record.md"
        doc.write_text("# A device measurement record\n\nNo structure here.\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(ctx_factory.main), check=True,
                       capture_output=True)
        return ctx_factory(pipeline={"methodology": methodology, "evidence": {}})

    def verdict(self, ctx):
        return only(evaluate(["docs.diagrams_current"], ctx)).verdict

    def test_a_docs_pipeline_is_not_asked_for_a_diagram(self, ctx_factory):
        verdict = self.verdict(self.ctx_for(ctx_factory, "docs"))
        assert verdict.skipped
        assert "no structural change is being claimed" in verdict.message

    def test_a_tdd_pipeline_is_still_asked(self, ctx_factory):
        """The relaxation is scoped to a DECLARED documentation pipeline."""
        assert not self.verdict(self.ctx_for(ctx_factory, "tdd")).ok


class TestWorkItemPathsAreExempt:
    """The backlog is data every session touches, like config and memory.

    Routing it through a branch produces conflicts with no review value —
    which is the stated rationale for the exemptions that already existed. It
    is not in the default LIST because its path is configurable; the library
    knows where it put it.
    """

    def ctx_for(self, ctx_factory, target, extra=None):
        body = {"version": 1, "project": {"name": "demo"},
                "items": {"path": "docs/backlog"}}
        if extra:
            body["items"].update(extra)
        write_manifest(ctx_factory.main, body)
        return ctx_factory(payload=edit_payload(ctx_factory.main / target))

    def verdict(self, ctx):
        return only(evaluate(["session.isolated_before_edit"], ctx)).verdict

    def test_a_backlog_edit_in_the_shared_tree_is_exempt(self, ctx_factory):
        assert self.verdict(self.ctx_for(ctx_factory, "docs/backlog/BUG_1.md")).skipped

    def test_the_backlog_directory_itself_is_exempt(self, ctx_factory):
        assert self.verdict(self.ctx_for(ctx_factory, "docs/backlog")).skipped

    def test_source_beside_it_is_not_exempt(self, ctx_factory):
        """The exemption is the work-item store, not everything under docs."""
        assert not self.verdict(self.ctx_for(ctx_factory, "src/app.py")).skipped

    def test_a_project_can_turn_it_off(self, ctx_factory):
        verdict = self.verdict(
            self.ctx_for(ctx_factory, "docs/backlog/BUG_1.md", {"exempt": False})
        )
        assert not verdict.skipped


class TestAmbiguityAdviceIsHonestAboutHooks:
    def test_it_no_longer_implies_the_env_var_reaches_a_hook(self):
        """A hook runs as its own process, so a variable exported in front of a
        tool call never gets there. Advice the tool cannot honour is a dead end
        — the same shape as --id being rejected by the commands that printed it.
        """
        from invariant.domains.session import Session, ambiguity_message

        text = ambiguity_message(
            [Session(id="aaa", name="one"), Session(id="bbb", name="two")]
        )
        assert "--id" in text
        assert "never gets there" in text
        assert "host session bound to it" in text


class TestClosingTheSessionIsGated:
    """The Definition of Done had no enforcement point at all.

    Every other rule fires while work is happening — before a tool call, on
    entering a state. The closeout comes due when work *stops*, and nothing
    fired there: the pipeline's terminal state hands off to the session
    machine, and the session machine was never asked again. Two sessions were
    found parked with four commits stranded on branches nobody was looking at,
    and three merged branches that were never deleted.

    `session.teardown_complete` had existed for the tidiness half the whole
    time — registered, profiled `block` under strict, covered by tests, and
    bound to no event and no machine state. It could not fail because nothing
    ever ran it. That is the failure mode these tests exist to prevent, so the
    binding itself is asserted, not just the logic.
    """

    def committed_ahead(self, ctx_factory, commits=1):
        """A session branch holding work the base does not have."""
        for n in range(commits):
            (ctx_factory.wt / f"work{n}.txt").write_text("x", encoding="utf-8")
            run(["git", "add", "-A"], ctx_factory.wt)
            run(["git", "commit", "-qm", f"work {n}"], ctx_factory.wt)
        return {"state": "working", "branch": "session/x", "base": "master"}

    # -- the binding, not just the logic -------------------------------

    def test_both_closeout_rules_actually_run_on_stop(self):
        """A gate bound to no event is inert however good its logic is."""
        bound = {spec.id for spec in rules_for_event("stop")}
        assert "session.work_would_be_stranded" in bound
        assert "session.teardown_complete" in bound

    # -- the known positive --------------------------------------------

    def test_finished_work_on_an_unmerged_branch_is_a_finding(self, ctx_factory):
        ctx = ctx_factory(
            session=self.committed_ahead(ctx_factory, commits=2),
            pipeline={"state": "committed"},
        )
        verdict = only(evaluate(["session.work_would_be_stranded"], ctx)).verdict
        assert not verdict.ok and not verdict.skipped
        assert "2 commit(s)" in verdict.message
        assert any("session merge" in fix for fix in verdict.fixes)

    def test_a_merged_branch_leaves_nothing_stranded(self, ctx_factory):
        session = self.committed_ahead(ctx_factory)
        run(["git", "merge", "--ff-only", "session/x"], ctx_factory.main)
        ctx = ctx_factory(session=session, pipeline={"state": "committed"})
        verdict = only(evaluate(["session.work_would_be_stranded"], ctx)).verdict
        assert verdict.ok and not verdict.skipped

    # -- the narrowing that keeps it from firing every turn -------------

    def test_work_still_in_flight_is_not_a_finding(self, ctx_factory):
        """The half that makes this liveable.

        A session mid-work is ahead of its base constantly. Blocking on that
        alone would fire on every turn until it was ignored, which is how a
        blocking gate becomes an obstacle instead of a control.
        """
        ctx = ctx_factory(
            session=self.committed_ahead(ctx_factory),
            pipeline={"state": "green"},
        )
        assert only(evaluate(["session.work_would_be_stranded"], ctx)).verdict.skipped

    def test_no_pipeline_at_all_is_not_a_finding(self, ctx_factory):
        ctx = ctx_factory(session=self.committed_ahead(ctx_factory), pipeline=None)
        assert only(evaluate(["session.work_would_be_stranded"], ctx)).verdict.skipped

    def test_an_abandoned_pipeline_claims_nothing(self, ctx_factory):
        ctx = ctx_factory(
            session=self.committed_ahead(ctx_factory),
            pipeline={"state": "abandoned"},
        )
        assert only(evaluate(["session.work_would_be_stranded"], ctx)).verdict.skipped

    def test_a_finished_session_is_not_asked_again(self, ctx_factory):
        session = self.committed_ahead(ctx_factory)
        session["state"] = "torn_down"
        ctx = ctx_factory(session=session, pipeline={"state": "committed"})
        assert only(evaluate(["session.work_would_be_stranded"], ctx)).verdict.skipped

    def test_asking_twice_in_one_turn_is_a_loop(self, ctx_factory):
        """A gate that cannot be satisfied gets forced.

        The host returns the turn to the agent with the reason attached; if the
        rule fired again on the next stop the pair would never terminate, and
        the way out of a non-terminating gate is always to disable it.
        """
        ctx = ctx_factory(
            session=self.committed_ahead(ctx_factory),
            pipeline={"state": "committed"},
            payload={"stop_hook_active": True},
        )
        assert only(evaluate(["session.work_would_be_stranded"], ctx)).verdict.skipped

    def test_terminal_states_are_read_from_the_machine_not_copied(self, ctx_factory):
        """A copied list is the one nobody updates when a machine gains a state."""
        from invariant.core.machines import load_session_machine
        from invariant.core.rules import _terminal_states

        found = _terminal_states(load_session_machine, ctx_factory().manifest)
        assert found == load_session_machine(ctx_factory().manifest).terminal
        assert "torn_down" in found


class TestTheMethodologyMustActuallyRun:
    """`pipeline.started_before_edit` proved a pipeline exists, never that it moved.

    Starting one in `designing` satisfied that rule for the rest of the
    session. Two sessions were found `torn_down` with their pipeline still in
    `designing` — which skipped every gate the methodology owns, because all of
    them are EXIT gates on states nobody was required to pass through.

    One gate closes all of it: the machine's `next` lists already forbid
    skipping a state, so requiring the pipeline to be terminal before the work
    lands transitively requires every exit gate along the way.
    """

    def test_the_initial_state_is_not_good_enough(self, ctx_factory):
        """The exact shape found on disk."""
        ctx = ctx_factory(session={"state": "working"}, pipeline={"state": "designing"})
        verdict = only(evaluate(["pipeline.complete"], ctx)).verdict
        assert not verdict.ok and not verdict.skipped
        assert "designing" in verdict.message

    def test_a_finished_pipeline_passes(self, ctx_factory):
        ctx = ctx_factory(session={"state": "working"}, pipeline={"state": "committed"})
        verdict = only(evaluate(["pipeline.complete"], ctx)).verdict
        assert verdict.ok and not verdict.skipped

    def test_a_missing_pipeline_is_a_finding_not_a_skip(self, ctx_factory):
        """Otherwise deleting the pipeline is the way past the gate."""
        ctx = ctx_factory(session={"state": "working"}, pipeline=None)
        verdict = only(evaluate(["pipeline.complete"], ctx)).verdict
        assert not verdict.ok and not verdict.skipped

    def test_an_abandoned_pipeline_does_not_count_as_finished(self, ctx_factory):
        """`abandoned` is terminal, and terminal is not the property wanted.

        Reading "terminal" alone would let a called-off methodology land work,
        which is the opposite of what abandoning it meant.
        """
        ctx = ctx_factory(session={"state": "working"}, pipeline={"state": "abandoned"})
        verdict = only(evaluate(["pipeline.complete"], ctx)).verdict
        assert not verdict.ok and "abandoned" in verdict.message

    def test_the_message_names_where_to_go_next(self, ctx_factory):
        ctx = ctx_factory(session={"state": "working"}, pipeline={"state": "red"})
        verdict = only(evaluate(["pipeline.complete"], ctx)).verdict
        assert any("green" in fix for fix in verdict.fixes)

    def test_it_guards_both_the_reviewable_step_and_the_irreversible_one(self):
        """A gate only on `reviewed` guards nothing once the work moves on.

        `reviewed` can be re-entered from `working`, so a session may pass it,
        go back, and merge from a state the gate never saw.
        """
        from invariant.core.machines import load_session_machine

        machine = load_session_machine(None)
        assert "pipeline.complete" in machine.state("reviewed").entry_gates
        assert "pipeline.complete" in machine.state("merged").entry_gates


class TestFilesThatCannotHaveACoverageRow:
    """Two reasons a row is missing, and only one of them is a problem.

    The gate treated every absent row identically and blocked. That is right
    for a file the run never measured, and unsatisfiable for a file that has
    nothing to measure — a bare protocol, an enum of cases, a file the change
    DELETED. No test can be written to clear it, so the gate stops being a
    standard and becomes something to route around.

    The distinction is only ever drawn in the direction of MORE evidence: an
    adapter that cannot tell leaves the block exactly where it was.
    """

    def ctx_for(self, ctx_factory, coverage, declared, ecosystems=("swift",)):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": list(ecosystems)},
                "methodology": {
                    "gates": {"coverage_min": 90, "coverage_scope": "touched"}
                },
            },
        )
        return ctx_factory(
            cwd=ctx_factory.main,
            session={
                "id": "s1",
                "files": declared,
                "worktree": str(ctx_factory.wt),
                "base": "master",
            },
            pipeline={"evidence": {"coverage": coverage}},
        )

    def verdict(self, ctx):
        return only(evaluate(["coverage.meets_floor"], ctx)).verdict

    def write(self, ctx_factory, relpath, body):
        path = ctx_factory.wt / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        return path

    MEASURED = "Heap/Intake/FileBytesIngester.swift"
    PROTOCOL = "Heap/Intake/IntakeDrainRunner.swift"
    LIVE = "struct A { func f() { g() } }"

    def measured_row(self, ctx_factory):
        return {str(ctx_factory.wt / self.MEASURED): 100.0}

    def test_a_declaration_only_file_no_longer_blocks(self, ctx_factory):
        """The reported case: a bare protocol can never earn a row."""
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(
            ctx_factory,
            self.PROTOCOL,
            "protocol IntakeDrainRunner {\n"
            "    func pumpNow(batchLimit: Int) async\n"
            "    var currentPauseReason: String? { get async }\n"
            "}\n",
        )
        ctx = self.ctx_for(
            ctx_factory, self.measured_row(ctx_factory), [self.MEASURED, self.PROTOCOL]
        )
        verdict = self.verdict(ctx)
        assert verdict.ok, verdict.message

    def test_the_excluded_file_is_named_rather_than_silently_dropped(self, ctx_factory):
        """A pass that quietly narrowed its own scope is the vacuous green."""
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, self.PROTOCOL, "protocol P { func a() }\n")
        ctx = self.ctx_for(
            ctx_factory, self.measured_row(ctx_factory), [self.MEASURED, self.PROTOCOL]
        )
        verdict = self.verdict(ctx)
        assert verdict.ok, verdict.message
        assert "IntakeDrainRunner.swift" in (verdict.detail or "")

    def test_a_file_with_code_and_no_row_still_blocks(self, ctx_factory):
        """The guard. This is the case the gate exists for."""
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(
            ctx_factory,
            "Heap/Intake/Unmeasured.swift",
            "struct Unmeasured {\n    func work() { compute() }\n}\n",
        )
        ctx = self.ctx_for(
            ctx_factory,
            self.measured_row(ctx_factory),
            [self.MEASURED, "Heap/Intake/Unmeasured.swift"],
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "no coverage row" in verdict.message
        assert "Unmeasured.swift" in verdict.detail

    def test_an_adapter_that_cannot_tell_leaves_the_block_in_place(self, ctx_factory):
        """`None` is not a negative answer, and must not relax anything."""
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, self.PROTOCOL, "protocol P { func a() }\n")
        ctx = self.ctx_for(
            ctx_factory,
            self.measured_row(ctx_factory),
            [self.MEASURED, self.PROTOCOL],
            ecosystems=("generic",),  # knows no language, answers None
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "no coverage row" in verdict.message

    def test_a_file_the_change_deleted_is_not_owed_coverage(self, ctx_factory):
        """A renamed-away file has no lines left to cover.

        The reported case renamed a 456-line runner. The old path stayed in
        the branch diff, so the gate asked for coverage of a file that no
        longer existed, and no run could ever answer.
        """
        gone = "Heap/Intake/OldRunner.swift"
        self.write(ctx_factory, gone, "struct OldRunner { func f() { g() } }")
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        run(["git", "add", "-A"], ctx_factory.wt)
        run(["git", "commit", "-qm", "add both"], ctx_factory.wt)
        (ctx_factory.wt / gone).unlink()
        run(["git", "add", "-A"], ctx_factory.wt)
        run(["git", "commit", "-qm", "remove one"], ctx_factory.wt)

        ctx = self.ctx_for(
            ctx_factory, self.measured_row(ctx_factory), [self.MEASURED, gone]
        )
        verdict = self.verdict(ctx)
        assert verdict.ok, verdict.message

    def test_a_file_still_present_is_not_treated_as_deleted(self, ctx_factory):
        """The deletion check must read the diff, not the absence of a row."""
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(
            ctx_factory,
            "Heap/Intake/Present.swift",
            "struct Present {\n    func work() { compute() }\n}\n",
        )
        run(["git", "add", "-A"], ctx_factory.wt)
        run(["git", "commit", "-qm", "add both"], ctx_factory.wt)

        ctx = self.ctx_for(
            ctx_factory,
            self.measured_row(ctx_factory),
            [self.MEASURED, "Heap/Intake/Present.swift"],
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "Present.swift" in verdict.detail

    def test_a_scope_emptied_by_exclusion_reports_itself_as_empty(self, ctx_factory):
        """Nothing left to hold to the floor is N/A, not a pass.

        A change that only edits a protocol leaves the touched scope with no
        measurable file in it. Reporting "all 0 file(s) at or above 90%" would
        be a green earned by the scope having emptied itself.
        """
        self.write(ctx_factory, self.PROTOCOL, "protocol P { func a() }\n")
        ctx = self.ctx_for(ctx_factory, self.measured_row(ctx_factory), [self.PROTOCOL])
        verdict = self.verdict(ctx)
        assert verdict.ok and verdict.skipped
        assert "IntakeDrainRunner.swift" in verdict.detail

    def test_an_unanswerable_deletion_query_excuses_nothing(
        self, ctx_factory, tmp_path
    ):
        """`None` from git is "cannot tell", which relaxes nothing.

        A path that is merely absent is the shape of a misspelling as much as
        of a deletion, so the excusal needs git to SAY the change removed it.
        """
        from invariant.core import git

        outside = tmp_path / "not-a-repo"
        outside.mkdir()
        assert git.deleted_files(outside, "master") is None

        self.write(ctx_factory, self.MEASURED, self.LIVE)
        ctx = self.ctx_for(
            ctx_factory,
            self.measured_row(ctx_factory),
            [self.MEASURED, "Heap/Intake/Misspelt.swift"],
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "Misspelt.swift" in verdict.detail
