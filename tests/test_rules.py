import subprocess
from pathlib import Path

import pytest

from invariant.core.manifest import Level
from invariant.core.rules import (
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
        assert "merges it into itself" in result.verdict.detail

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
        result = only(evaluate(["coverage.meets_floor"], ctx_factory(pipeline=pipeline)))
        assert "1 file(s) below" in result.verdict.message
        assert "a.py" in result.verdict.detail

    def test_all_above_floor_passes(self, ctx_factory):
        pipeline = {"evidence": {"coverage": {"a.py": 95.0}}}
        assert only(evaluate(["coverage.meets_floor"], ctx_factory(pipeline=pipeline))).verdict.ok

    def test_no_recorded_coverage_fails(self, ctx_factory):
        assert not only(evaluate(["coverage.meets_floor"], ctx_factory(pipeline={}))).verdict.ok


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
