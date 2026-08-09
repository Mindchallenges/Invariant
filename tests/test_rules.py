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
