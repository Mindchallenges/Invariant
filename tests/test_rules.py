import subprocess
from pathlib import Path

import pytest

from invariant.core.manifest import Level
from invariant.core.rules import (
    _coverage_roots,
    _relative_to_any,
    _same_file,
    NORMALISED_PAYLOAD_KEYS,
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

    def test_every_registered_rule_is_graded_by_every_profile(self):
        """The other direction, which is the one that fails silently.

        A profile naming an unknown rule is caught above. A rule no profile
        names is not: `rule_level` answers `warn` for anything absent, so a
        new rule ships advisory in every profile — including `strict`, whose
        whole promise is that everything provable is enforced — and nothing
        says so. The omission is invisible precisely where it matters most.
        """
        import yaml

        from invariant.core.manifest import DATA_DIR

        registered = {spec.id for spec in all_rules()}
        for profile in (DATA_DIR / "profiles").glob("*.yaml"):
            raw = yaml.safe_load(profile.read_text(encoding="utf-8"))
            graded = set(raw.get("rules", {}))
            assert registered <= graded, (
                f"{profile.name} grades no level for "
                f"{sorted(registered - graded)}; absent means 'warn' silently"
            )

    def test_every_registered_rule_is_reachable(self):
        """A graded rule that nothing ever evaluates reports a level it has no
        way to apply.

        Two routes reach a rule: an event binding, or a gate naming it in some
        machine. A rule with neither is dead code wearing an enforcement
        level — `invariant rules list` shows it as `block`, the profiles argue
        about it, and it never once runs. That reads as a control being
        present, which is the failure mode this library exists to remove.
        """
        from invariant.core.machines import (
            AGENT_ROLE_GATES,
            DOCS_FIRST_GATES,
            load_machine,
            shipped_ids,
        )

        reachable = {
            gate
            for injected in (DOCS_FIRST_GATES, AGENT_ROLE_GATES)
            for spec in injected.values()
            for gates in spec.values()
            for gate in gates
        }
        for machine_id in shipped_ids():
            for state in load_machine(machine_id).states.values():
                reachable.update(state.entry_gates)
                reachable.update(state.exit_gates)

        orphans = [
            spec.id for spec in all_rules() if not spec.events and spec.id not in reachable
        ]
        assert not orphans, f"registered, graded, and never evaluated: {sorted(orphans)}"

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


class TestAGitFailureIsNotAnAnswer:
    """AUD-07: two helpers reported "could not ask git" as a clean pass.

    `staged_files` answered `[]` — read by its caller as "0 staged file(s),
    all declared" — and `in_linked_worktree` answered `False` — read as
    "running from the main checkout" — on an unreadable repository. The
    codebase's own convention (`branch_files`, `unpushed_files`,
    `deleted_files`) is that an unanswerable question returns `None` and no
    caller may relax on it.
    """

    def test_staged_files_abstains_outside_a_repository(self, tmp_path):
        from invariant.core import git

        assert git.staged_files(tmp_path) is None

    def test_in_linked_worktree_abstains_outside_a_repository(self, tmp_path):
        from invariant.core import git

        assert git.in_linked_worktree(tmp_path) is None

    def test_both_still_answer_inside_a_repository(self, ctx_factory):
        from invariant.core import git

        assert git.staged_files(ctx_factory.main) == []
        assert git.in_linked_worktree(ctx_factory.main) is False
        assert git.in_linked_worktree(ctx_factory.wt) is True

    def test_an_unreadable_staged_set_fails_the_file_gate(self, ctx_factory, tmp_path):
        norepo = tmp_path / "norepo"
        norepo.mkdir()
        ctx = ctx_factory(
            cwd=norepo,
            session={"state": "working", "files": ["mine.py"]},
            payload=bash_payload("git commit -m x"),
        )
        result = only(evaluate(["session.own_file_set_only"], ctx))
        assert not result.verdict.ok and not result.verdict.skipped
        assert "staged" in result.verdict.message

    def test_an_unanswerable_worktree_question_fails_the_merge_gate(
        self, ctx_factory, tmp_path
    ):
        """The gate guarding the self-merge whose failure mode is 'both
        commands exit zero' must not pass on 'git itself could not answer'."""
        norepo = tmp_path / "norepo2"
        norepo.mkdir()
        ctx = ctx_factory(cwd=norepo, extra={"merging": True})
        result = only(evaluate(["session.ff_merge_target"], ctx))
        assert not result.verdict.ok and not result.verdict.skipped

    def test_the_bash_merge_path_fails_the_same_way(self, ctx_factory, tmp_path):
        norepo = tmp_path / "norepo3"
        norepo.mkdir()
        ctx = ctx_factory(
            cwd=norepo, payload=bash_payload("git merge --ff-only session/x")
        )
        result = only(evaluate(["session.ff_merge_target"], ctx))
        assert not result.verdict.ok and not result.verdict.skipped


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
    # Both of these were written against `test.suite_runs`, which is retired:
    # it was graded by three profiles and evaluated by nothing. They are kept
    # pointed at `test.all_pass`, which is where the two questions are actually
    # asked — retiring a rule must not quietly retire its coverage.
    def test_no_run_recorded_fails(self, ctx_factory):
        assert not only(evaluate(["test.all_pass"], ctx_factory(pipeline={}))).verdict.ok

    def test_zero_executed_is_caught_despite_a_pass(self, ctx_factory):
        pipeline = {"evidence": {"test_runs": [{"passed": True, "executed": 0}]}}
        result = only(evaluate(["test.all_pass"], ctx_factory(pipeline=pipeline)))
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
        # The counts are what make it genuine. This case used to be spelled
        # `{"phase": "red", "passed": False}` and nothing else, which is
        # precisely the shape a compile error produces (BUG_029).
        pipeline = {
            "evidence": {
                "test_runs": [{"phase": "red", "passed": False, "executed": 4, "failed": 1}]
            }
        }
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


def multiedit_payload(path, *new_strings):
    return {
        "tool_name": "MultiEdit",
        "tool_input": {
            "file_path": str(path),
            "edits": [{"old_string": "x", "new_string": s} for s in new_strings],
        },
    }


class TestPrivacyPayloadShapes:
    """AUD-06: MultiEdit and NotebookEdit are EDIT_TOOLS, and their payloads
    were never scanned — the same data that blocks a Write passed through a
    MultiEdit untouched."""

    def test_a_multiedit_leak_is_caught(self, ctx_factory):
        doc = ctx_factory.main / "docs" / "architecture" / "new.md"
        ctx = ctx_factory(payload=multiedit_payload(doc, "mail a.person@example.com"))
        result = only(evaluate(["privacy.clean"], ctx))
        assert not result.verdict.ok and not result.verdict.skipped

    def test_every_edit_in_a_multiedit_is_scanned(self, ctx_factory):
        doc = ctx_factory.main / "docs" / "architecture" / "new.md"
        ctx = ctx_factory(
            payload=multiedit_payload(doc, "clean", "mail a.person@example.com")
        )
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_a_clean_multiedit_passes_rather_than_skips(self, ctx_factory):
        doc = ctx_factory.main / "docs" / "architecture" / "new.md"
        ctx = ctx_factory(payload=multiedit_payload(doc, "nothing sensitive"))
        verdict = only(evaluate(["privacy.clean"], ctx)).verdict
        assert verdict.ok and not verdict.skipped

    def test_a_notebookedit_leak_is_caught(self, ctx_factory):
        nb = ctx_factory.main / "docs" / "architecture" / "walkthrough.ipynb"
        ctx = ctx_factory(
            payload={
                "tool_name": "NotebookEdit",
                "tool_input": {
                    "notebook_path": str(nb),
                    "new_source": "mail a.person@example.com",
                },
            }
        )
        result = only(evaluate(["privacy.clean"], ctx))
        assert not result.verdict.ok and not result.verdict.skipped


class TestCommitMessageForms:
    """AUD-09: only exact ``-m`` forms were scanned — ``git commit -am`` and
    ``-F <file>`` carried a message past the gate unread."""

    def test_dash_am_is_scanned(self, ctx_factory):
        ctx = ctx_factory(
            payload=bash_payload("git commit -am 'mail a.person@example.com'")
        )
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_a_longer_cluster_ending_in_m_is_scanned(self, ctx_factory):
        ctx = ctx_factory(
            payload=bash_payload("git commit -sam 'mail a.person@example.com'")
        )
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_dash_capital_f_scans_the_file(self, ctx_factory):
        msg = ctx_factory.main / "commit-message.txt"
        msg.write_text("mail a.person@example.com\n", encoding="utf-8")
        ctx = ctx_factory(
            cwd=ctx_factory.main, payload=bash_payload(f"git commit -F {msg}")
        )
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_a_relative_dash_f_path_resolves_against_cwd(self, ctx_factory):
        (ctx_factory.main / "msg.txt").write_text(
            "mail a.person@example.com\n", encoding="utf-8"
        )
        ctx = ctx_factory(
            cwd=ctx_factory.main, payload=bash_payload("git commit -F msg.txt")
        )
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_an_unreadable_message_file_fails_closed(self, ctx_factory):
        """The codebase convention: an unanswerable question is not a pass."""
        ctx = ctx_factory(
            cwd=ctx_factory.main,
            payload=bash_payload("git commit -F does-not-exist.txt"),
        )
        result = only(evaluate(["privacy.clean"], ctx))
        assert not result.verdict.ok and not result.verdict.skipped
        assert "could not" in result.verdict.message

    def test_a_stdin_message_fails_closed(self, ctx_factory):
        """``-F -`` reads stdin, which a hook can never see."""
        ctx = ctx_factory(cwd=ctx_factory.main, payload=bash_payload("git commit -F -"))
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_a_clean_message_file_passes(self, ctx_factory):
        msg = ctx_factory.main / "clean.txt"
        msg.write_text("nothing sensitive\n", encoding="utf-8")
        ctx = ctx_factory(
            cwd=ctx_factory.main, payload=bash_payload(f"git commit -F {msg}")
        )
        verdict = only(evaluate(["privacy.clean"], ctx)).verdict
        assert verdict.ok and not verdict.skipped


class TestPrivacyScopeCoversTheSecondCopies:
    """AUD-15: the scope prefixes came from paths.docs and paths.memory only.

    The memory index is a concentrate of exactly the text the memory scope
    protects, and the committed telemetry report is the one rendering of the
    raw stats store that leaves the machine — both were unscanned.
    """

    def test_the_memory_index_is_in_scope(self, ctx_factory):
        target = ctx_factory.main / "MEMORY.md"
        ctx = ctx_factory(payload=edit_payload(target, "mail a.person@example.com"))
        result = only(evaluate(["privacy.clean"], ctx))
        assert not result.verdict.ok and not result.verdict.skipped

    def test_the_reports_store_is_in_scope_by_default(self, ctx_factory):
        target = ctx_factory.main / "docs" / "reports" / "telemetry.md"
        ctx = ctx_factory(payload=edit_payload(target, "mail a.person@example.com"))
        result = only(evaluate(["privacy.clean"], ctx))
        assert not result.verdict.ok and not result.verdict.skipped

    def test_a_literal_prefix_widens_the_scope(self, ctx_factory):
        """A scope entry that is not a keyword is a project-relative prefix.

        This is how a project whose ``paths.docs`` names one subtree declares
        that a wider one — ``docs/`` including its audits — is still a place
        where a leak becomes permanent.
        """
        write_manifest(ctx_factory.main, {"privacy": {"scope": ["docs/"]}})
        target = ctx_factory.main / "docs" / "audits" / "audit.md"
        ctx = ctx_factory(payload=edit_payload(target, "mail a.person@example.com"))
        assert not only(evaluate(["privacy.clean"], ctx)).verdict.ok

    def test_a_literal_prefix_does_not_reach_outside_itself(self, ctx_factory):
        write_manifest(ctx_factory.main, {"privacy": {"scope": ["docs/"]}})
        ctx = ctx_factory(
            payload=edit_payload(ctx_factory.main / "app.py", "mail a@b.com")
        )
        assert only(evaluate(["privacy.clean"], ctx)).verdict.skipped


class TestNamedAfterIsolating:
    """AUD-14: the one non-memory rule with no known-positive failing fixture.

    Structural until AUD-05: no code path evaluated it. Its own ``registered →
    not applicable`` branch was the second layer of deadness — an entry gate
    runs while the record still reads the PREVIOUS state, so abstaining on
    ``registered`` made the gate unable to fire on the only edge it guards.
    """

    def test_a_branch_derived_name_fails_at_the_isolation_gate(self, ctx_factory):
        # State is `registered` because that is what the record says while the
        # entry gate to `isolated` is being evaluated.
        ctx = ctx_factory(session={"state": "registered", "name": "session/deadbeef"})
        result = only(evaluate(["session.named_after_isolating"], ctx))
        assert not result.verdict.ok and not result.verdict.skipped

    def test_an_unnamed_record_fails(self, ctx_factory):
        ctx = ctx_factory(session={"state": "isolated", "name": ""})
        assert not only(evaluate(["session.named_after_isolating"], ctx)).verdict.ok

    def test_a_task_derived_name_passes(self, ctx_factory):
        ctx = ctx_factory(session={"state": "registered", "name": "fix-the-widget"})
        assert only(evaluate(["session.named_after_isolating"], ctx)).verdict.ok

    def test_no_session_still_abstains(self, ctx_factory):
        assert only(
            evaluate(["session.named_after_isolating"], ctx_factory())
        ).verdict.skipped


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


class TestMemoryRulesCanActuallyFail:
    """Four rules that had never once produced a failing verdict.

    Instrumenting the whole suite found them only ever skipping: bound to no
    event and named by no machine gate, they were unreachable, and every
    project that reads `invariant rules list` was being told memory hygiene
    was enforced at whatever level its profile named. The README's claim is
    that a gate is a function with a fixture proving it fails on a
    known-positive input; these four are that claim's counterexamples, so each
    gets both — a binding that reaches it, and an input it fails on.
    """

    def managed(self, ctx_factory, **payload):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo"},
                "paths": {"memory": "memory"},
                "memory": {"managed": True},
            },
        )
        return ctx_factory(payload=payload or {})

    def memo(self, root, name, body="Body text.", description="a fact"):
        path = root / "memory"
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{name}.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n"
            f"metadata:\n  type: project\n---\n\n{body}\n",
            encoding="utf-8",
        )

    def index(self, root, text):
        (root / "memory" / "MEMORY.md").write_text(text, encoding="utf-8")

    def test_an_unparseable_memory_fails_the_schema_rule(self, ctx_factory):
        (ctx_factory.main / "memory").mkdir()
        (ctx_factory.main / "memory" / "bad.md").write_text("just prose\n", encoding="utf-8")
        ctx = self.managed(ctx_factory)
        assert not only(evaluate(["memory.schema_valid"], ctx)).verdict.ok

    def test_a_memory_missing_from_the_index_fails_the_index_rule(self, ctx_factory):
        self.memo(ctx_factory.main, "alpha")
        self.index(ctx_factory.main, "# Memory\n")
        ctx = self.managed(ctx_factory)
        assert not only(evaluate(["memory.index_current"], ctx)).verdict.ok

    def test_a_link_to_nothing_fails_the_dead_link_rule(self, ctx_factory):
        self.memo(ctx_factory.main, "alpha", body="see [[ghost]]")
        self.index(ctx_factory.main, "- [Alpha](alpha.md)\n")
        ctx = self.managed(ctx_factory)
        assert not only(evaluate(["memory.no_dead_links"], ctx)).verdict.ok

    def test_two_memories_of_one_fact_fail_the_duplicate_rule(self, ctx_factory):
        text = "The deploy script must be run from the repository root every time."
        self.memo(ctx_factory.main, "alpha", body=text, description="deploy from root")
        self.memo(ctx_factory.main, "beta", body=text, description="deploy from root")
        self.index(ctx_factory.main, "- [A](alpha.md)\n- [B](beta.md)\n")
        ctx = self.managed(ctx_factory)
        assert not only(evaluate(["memory.no_duplicates"], ctx)).verdict.ok

    def test_an_unmanaged_project_still_hears_nothing(self, ctx_factory):
        """The short-circuit stays. Wiring the rules to an event must not turn
        memory hygiene into noise for the projects that do not use it."""
        for rule_id in (
            "memory.schema_valid",
            "memory.index_current",
            "memory.no_dead_links",
            "memory.no_duplicates",
        ):
            assert only(evaluate([rule_id], ctx_factory())).verdict.skipped

    def test_they_run_at_the_end_of_a_turn(self):
        bound = {spec.id for spec in rules_for_event("stop")}
        assert {
            "memory.schema_valid",
            "memory.index_current",
            "memory.no_dead_links",
            "memory.no_duplicates",
        } <= bound

    def test_they_do_not_ask_twice_in_one_turn(self, ctx_factory):
        (ctx_factory.main / "memory").mkdir()
        (ctx_factory.main / "memory" / "bad.md").write_text("prose\n", encoding="utf-8")
        ctx = self.managed(ctx_factory, stop_hook_active=True)
        assert only(evaluate(["memory.schema_valid"], ctx)).verdict.skipped


class TestItemHistoryDrift:
    """The item store is exempt from isolation, so a hand edit never asks the
    machine.

    That exemption is deliberate and correct — a backlog entry is data, every
    session touches it, and routing it through a branch produces conflicts with
    no review value. But it means the FSM's load-bearing invariant is
    structural only for callers who go through the CLI. Seven bugs in this
    repository's own backlog show `status: fixed` with `history: []`: the
    `filed -> reproduced -> fixed` edge the machine will not let you skip was
    skipped, by writing the file. Nothing noticed for months.

    So the machine cannot be the only reader. This rule re-derives the status
    from the recorded history at turn end and reports the ones that disagree —
    drift detection over a store the gate cannot sit in front of.
    """

    def backlog(self, root):
        from invariant.core.items import MarkdownBackend

        path = root / "docs" / "backlog"
        path.mkdir(parents=True, exist_ok=True)
        return MarkdownBackend(path)

    def save(self, root, **kwargs):
        from invariant.core.items import Item

        kwargs.setdefault("type", "bug")
        kwargs.setdefault("title", "a defect")
        self.backlog(root).save(Item(**kwargs))

    def check(self, ctx_factory, **payload):
        ctx = ctx_factory(payload=payload or {})
        return only(evaluate(["item.history_matches_status"], ctx))

    def test_a_status_no_history_explains_is_reported(self, ctx_factory):
        # The BUG_002..BUG_008 shape exactly: fixed, with nothing recorded.
        self.save(ctx_factory.main, id="BUG_900", status="fixed", history=[])
        result = self.check(ctx_factory)
        assert not result.verdict.ok
        assert "BUG_900" in result.verdict.detail

    def test_a_history_that_walks_the_machine_passes(self, ctx_factory):
        self.save(
            ctx_factory.main,
            id="BUG_901",
            status="fixed",
            history=[
                {"from": "filed", "to": "reproduced"},
                {"from": "reproduced", "to": "fixed"},
            ],
        )
        assert self.check(ctx_factory).verdict.ok

    def test_a_fresh_item_sitting_in_its_initial_state_passes(self, ctx_factory):
        self.save(ctx_factory.main, id="BUG_902", status="filed", history=[])
        assert self.check(ctx_factory).verdict.ok

    def test_history_that_ends_somewhere_else_than_the_status_is_reported(
        self, ctx_factory
    ):
        self.save(
            ctx_factory.main,
            id="BUG_903",
            status="closed",
            history=[{"from": "filed", "to": "reproduced"}],
        )
        result = self.check(ctx_factory)
        assert not result.verdict.ok
        assert "reproduced" in result.verdict.detail

    def test_an_illegal_edge_in_the_history_is_reported(self, ctx_factory):
        self.save(
            ctx_factory.main,
            id="BUG_904",
            status="fixed",
            history=[{"from": "filed", "to": "fixed"}],
        )
        result = self.check(ctx_factory)
        assert not result.verdict.ok
        assert "filed -> fixed" in result.verdict.detail

    def test_a_recorded_override_is_not_drift(self, ctx_factory):
        """`--force` already demanded a written reason and recorded itself.

        An override is the machine being asked and answered, not bypassed. A
        drift check that flagged it would be reporting the audit trail working.
        """
        self.save(
            ctx_factory.main,
            id="BUG_905",
            status="fixed",
            history=[{"from": "filed", "to": "fixed", "forced": True, "reason": "x"}],
        )
        assert self.check(ctx_factory).verdict.ok

    def test_a_gap_in_the_chain_is_reported(self, ctx_factory):
        """The fourth drift kind: each entry is legal, the sequence is not.

        `filed -> reproduced` then `verified -> closed` is two edges the
        machine has, spliced together across a step that was never recorded.
        Checking edges one at a time would pass it; the walk is what catches
        it, so the walk needs its own fixture.
        """
        self.save(
            ctx_factory.main,
            id="BUG_907",
            status="closed",
            history=[
                {"from": "filed", "to": "reproduced"},
                {"from": "verified", "to": "closed"},
            ],
        )
        result = self.check(ctx_factory)
        assert not result.verdict.ok
        assert "resumes at 'verified' after 'reproduced'" in result.verdict.detail

    def test_a_history_entry_naming_no_target_is_stepped_over(self, ctx_factory):
        """Hand-edited files are the whole reason this rule exists, so a
        malformed entry must not decide the verdict for the item around it."""
        self.save(
            ctx_factory.main,
            id="BUG_908",
            status="reproduced",
            history=[{"reason": "typed by hand"}, {"from": "filed", "to": "reproduced"}],
        )
        assert self.check(ctx_factory).verdict.ok

    def test_every_item_is_walked_not_only_the_first(self, ctx_factory):
        """One machine is loaded per type, not per item — and the second item
        of that type still gets checked rather than riding the cache."""
        self.save(ctx_factory.main, id="BUG_909", status="filed", history=[])
        self.save(ctx_factory.main, id="BUG_910", status="fixed", history=[])
        result = self.check(ctx_factory)
        assert not result.verdict.ok
        assert "BUG_910" in result.verdict.detail
        assert "BUG_909" not in result.verdict.detail

    def test_an_empty_backlog_is_not_a_finding(self, ctx_factory):
        assert self.check(ctx_factory).verdict.skipped

    def test_an_item_of_an_unknown_type_is_left_alone(self, ctx_factory):
        """A project may store other kinds in the same backlog. This rule can
        only speak for the types it has a machine for."""
        self.save(ctx_factory.main, id="RFC_001", type="rfc", status="whatever")
        assert self.check(ctx_factory).verdict.skipped

    def test_it_does_not_ask_twice_in_one_turn(self, ctx_factory):
        self.save(ctx_factory.main, id="BUG_906", status="fixed", history=[])
        assert self.check(ctx_factory, stop_hook_active=True).verdict.skipped

    def test_it_runs_at_the_end_of_a_turn(self):
        assert "item.history_matches_status" in {
            spec.id for spec in rules_for_event("stop")
        }


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



class TestTouchedTestFilesAreNotHeldToTheFloor:
    """BUG_019 — a Python session touching its own tests got an unsatisfiable gate.

    `[tool.coverage.run] source = [...]` means test files are never measured,
    so no row can ever exist for them, and the gate demanded one on essentially
    every session in this repository:

        2 changed source file(s) have no coverage row — tests/test_adapters.py, ...

    The fix is NOT `carries_executable_code`, which BUG_019's sketch named: a
    test file is full of executable lines, and answering `False` there would
    assert the dangerous direction the base docstring forbids. The honest fact
    is a different one — a test file is what measures, not what is measured.
    """

    def ctx_with(self, ctx_factory, coverage, changed=()):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
                "methodology": {"gates": {"coverage_min": 90, "coverage_scope": "touched"}},
            },
        )
        for rel in changed:
            path = ctx_factory.main / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x = 1\n", encoding="utf-8")
        return ctx_factory(
            session={"id": "s1", "files": list(changed)},
            pipeline={"evidence": {"coverage": coverage}},
        )

    def verdict(self, ctx):
        return only(evaluate(["coverage.meets_floor"], ctx)).verdict

    def test_a_touched_test_file_with_no_row_is_set_aside(self, ctx_factory):
        ctx = self.ctx_with(
            ctx_factory,
            {str(ctx_factory.main / "src" / "app.py"): 95.0},
            changed=["src/app.py", "tests/test_app.py"],
        )
        verdict = self.verdict(ctx)
        assert verdict.ok, verdict.message
        assert "no coverage row" not in verdict.message

    def test_the_file_set_aside_is_named(self, ctx_factory):
        """Never silently. Every file the gate declines to answer for is
        reported, or the pass is one the reader cannot audit."""
        ctx = self.ctx_with(
            ctx_factory,
            {str(ctx_factory.main / "src" / "app.py"): 95.0},
            changed=["src/app.py", "tests/test_app.py"],
        )
        assert "tests/test_app.py" in self.verdict(ctx).detail

    def test_a_production_file_with_no_row_still_blocks(self, ctx_factory):
        """The relaxation must not widen by one inch. This is the case the
        gate exists for."""
        ctx = self.ctx_with(
            ctx_factory,
            {str(ctx_factory.main / "src" / "app.py"): 95.0},
            changed=["src/app.py", "src/untested.py"],
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "src/untested.py" in verdict.detail

    def test_a_measured_test_file_is_still_held_to_the_floor(self, ctx_factory):
        """A project that DOES measure its tests keeps the answer it earned.
        Setting a file aside is for an absent row, never for a low one."""
        ctx = self.ctx_with(
            ctx_factory,
            {
                str(ctx_factory.main / "src" / "app.py"): 95.0,
                str(ctx_factory.main / "tests" / "test_app.py"): 10.0,
            },
            changed=["src/app.py", "tests/test_app.py"],
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "tests/test_app.py" in verdict.detail

    def test_a_project_with_no_python_adapter_gets_no_relaxation(self, ctx_factory):
        """The rule keeps the policy, the adapter supplies the fact. With no
        adapter that knows the language, "cannot tell" relaxes nothing."""
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo"},
                "methodology": {"gates": {"coverage_min": 90, "coverage_scope": "touched"}},
            },
        )
        for rel in ("src/app.py", "tests/test_app.py"):
            path = ctx_factory.main / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("x = 1\n", encoding="utf-8")
        ctx = ctx_factory(
            session={"id": "s1", "files": ["src/app.py", "tests/test_app.py"]},
            pipeline={
                "evidence": {"coverage": {str(ctx_factory.main / "src" / "app.py"): 95.0}}
            },
        )
        assert not self.verdict(ctx).ok


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
        """Still "skip" and never "ok" — a gate that did not apply has not
        vouched for anything. The level now rides along on a BLOCKING skip
        (BUG_020): "an advisory gate did not apply" and "a gate this project
        enforces did not apply" are not the same news, and the marker was the
        only place that distinction could reach a reader."""
        assert (
            self.evaluated(Verdict.na("not applicable"), Level.WARN)
            .render()
            .startswith("[skip]")
        )
        assert (
            self.evaluated(Verdict.na("not applicable"))
            .render()
            .startswith("[skip:BLOCK]")
        )

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

    def test_an_unloadable_machine_fails_rather_than_going_silent(self, tmp_path):
        """AUD-08: a configuration error must FAIL the gate, not step aside.

        With the pipeline machine unloadable, the terminal set came back empty,
        "committed" was not in it, and the rule skipped as "work is still in
        flight" — the closeout gate silently off on a mistyped manifest.
        `bug` is a shipped machine of the wrong KIND, so it passes manifest
        validation and still cannot be loaded as a pipeline.
        """
        from invariant.core.manifest import Manifest

        write_manifest(
            tmp_path,
            {"version": 1, "project": {"name": "demo"},
             "methodology": {"pipeline": "bug"}},
        )
        ctx = RuleContext(
            manifest=Manifest.load(tmp_path),
            cwd=tmp_path,
            session={"state": "working", "branch": "session/x", "base": "master"},
            pipeline={"state": "committed"},
        )
        verdict = only(evaluate(["session.work_would_be_stranded"], ctx)).verdict
        assert not verdict.ok and not verdict.skipped

    def test_the_persisted_methodology_answers_not_the_manifest(self, ctx_factory):
        """A pipeline is pinned to the machine it started under (AUD-02/03).

        Under `none` the single state `working` IS terminal, so this work is
        done and stranded. Reading the manifest's `tdd` instead called it
        in-flight and went silent.
        """
        ctx = ctx_factory(
            session=self.committed_ahead(ctx_factory),
            pipeline={"state": "working", "methodology": "none"},
        )
        verdict = only(evaluate(["session.work_would_be_stranded"], ctx)).verdict
        assert not verdict.ok and not verdict.skipped
        assert "commit(s)" in verdict.message


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

    def test_an_unloadable_machine_fails_the_gate_not_skips_it(self, tmp_path):
        """AUD-08: "the gate that makes every other pipeline gate mandatory"
        yielded a non-blocking skip on a mistyped `methodology.pipeline`.

        A configuration error is not a reason to step aside — a skip on a
        block-level gate reads as a pass. `bug` is shipped but of kind `item`,
        so it survives manifest validation and still cannot load as a pipeline.
        """
        from invariant.core.manifest import Manifest

        write_manifest(
            tmp_path,
            {"version": 1, "project": {"name": "demo"},
             "methodology": {"pipeline": "bug"}},
        )
        ctx = RuleContext(
            manifest=Manifest.load(tmp_path),
            cwd=tmp_path,
            session={"state": "working"},
            pipeline={"state": "red"},
        )
        verdict = only(evaluate(["pipeline.complete"], ctx)).verdict
        assert not verdict.ok and not verdict.skipped

    def test_the_pipelines_own_methodology_is_what_completes_it(self, ctx_factory):
        """A `none` pipeline parked in `working` is complete — by ITS machine.

        Judging it by the manifest's `tdd` instead reported a finished
        methodology as unfinished, which is the AUD-02 pin read from the other
        side.
        """
        ctx = ctx_factory(
            session={"state": "working"},
            pipeline={"state": "working", "methodology": "none"},
        )
        verdict = only(evaluate(["pipeline.complete"], ctx)).verdict
        assert verdict.ok and not verdict.skipped

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

    PORTS = "Heap/Library/LibrarySwapPorts.swift"
    CONSTANTS = (
        "import Foundation\n"
        "\n"
        "protocol LibrarySwitchableMediaStore: AnyObject {\n"
        "    func prepareForLibrarySwitch() async\n"
        "}\n"
        "\n"
        "extension Notification.Name {\n"
        '    static let activeLibraryDidChange = Notification.Name("activeLibraryDidChange")\n'
        "}\n"
        "\n"
        "extension Notification {\n"
        '    static let blobRelativePathKey = "blobRelativePath"\n'
        "}\n"
    )

    def test_a_file_of_type_level_constants_no_longer_blocks(self, ctx_factory):
        """The second reported shape, and the one the block-level gate refused
        a merge over: bare protocols and two extensions of `static let`
        constants, absent from all ten targets of the report that named it."""
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, self.PORTS, self.CONSTANTS)
        ctx = self.ctx_for(
            ctx_factory, self.measured_row(ctx_factory), [self.MEASURED, self.PORTS]
        )
        verdict = self.verdict(ctx)
        assert verdict.ok, verdict.message
        assert "LibrarySwapPorts.swift" in (verdict.detail or "")

    def test_one_real_initialiser_in_that_file_still_blocks(self, ctx_factory):
        """The one-bit control, at the layer the consumer sees. One right-hand
        side that is a call rather than a constant, and the demand comes
        straight back — which is what says the relaxation reads the file
        rather than its shape."""
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(
            ctx_factory,
            self.PORTS,
            self.CONSTANTS.replace(
                'static let blobRelativePathKey = "blobRelativePath"',
                "static let blobRelativePathKey = resolveKey()",
            ),
        )
        ctx = self.ctx_for(
            ctx_factory, self.measured_row(ctx_factory), [self.MEASURED, self.PORTS]
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "no coverage row" in verdict.message
        assert "LibrarySwapPorts.swift" in verdict.detail

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

    def test_a_touched_property_list_is_set_aside_and_named(self, ctx_factory):
        """The reported case, at the layer the consuming project sees it.

        A session whose load-bearing edit was `Info.plist` was handed "1
        changed source file(s) have no coverage row" by a block-level gate.
        `xccov` measures compiled Swift/ObjC/C, so no test anyone could write
        would ever produce that row, and the gate's own suggestion — drop the
        file from the session's declared set — asks the author to hide the
        change in order to describe it.
        """
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, "Heap/Info.plist", "<plist><dict/></plist>\n")
        ctx = self.ctx_for(
            ctx_factory,
            self.measured_row(ctx_factory),
            [self.MEASURED, "Heap/Info.plist"],
        )
        verdict = self.verdict(ctx)
        assert verdict.ok, verdict.message
        # Passing is only half of it. A file that vanished from the verdict
        # instead of being named would be a worse outcome than the block: an
        # exclusion nobody can see is indistinguishable from a gate that did
        # not run.
        assert "Info.plist" in (verdict.detail or "")
        assert "no executable lines to measure" in (verdict.detail or "")

    def test_a_touched_objective_c_file_still_blocks(self, ctx_factory):
        """The one-bit control for the relaxation above.

        `xccov` measures Objective-C, so an absent row for a `.m` file is the
        thing this gate exists to catch. If the resource allowlist ever grows
        far enough to swallow a compiled language, this is where it shows.
        """
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(
            ctx_factory,
            "Heap/Intake/Legacy.m",
            "@implementation Legacy\n- (void)work { compute(); }\n@end\n",
        )
        ctx = self.ctx_for(
            ctx_factory,
            self.measured_row(ctx_factory),
            [self.MEASURED, "Heap/Intake/Legacy.m"],
        )
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "no coverage row" in verdict.message
        assert "Legacy.m" in verdict.detail

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

class TestABinaryAssetHasNoExecutableLinesToMeasure:
    """A committed binary file was a question every set-aside branch refused.

    A snapshot-testing session committed a golden PNG. It is a changed file
    that is not docs, so it enters the touched set; no coverage reporter emits
    a row for an image; and every branch of `_explain_absent_rows` answers
    "cannot tell" for it — deletion says no, the file is present; the
    reporter's scope is usually unreadable for an asset directory; and both
    adapter hooks are keyed on a language suffix, so `is_test_file` and
    `carries_executable_code` return `None` for anything that is not source.
    The result is a block no work can clear: there is no test to write for a
    PNG, and the file set only ever ADDS, so the verdict's own advice to drop
    it was both unavailable and — since `_changed_set` unions git's answer
    with the declared one — ineffective.

    The answer is not a relaxation. A binary file positively carries no
    executable lines; that is a FACT about the bytes, true in every ecosystem,
    and it is the same kind of positive evidence the other branches demand.
    The dangerous direction stays shut: the fact is read from the CONTENT, so
    a file that decodes as text is untouched however it is named, and a file
    that cannot be read at all stays "cannot tell" and keeps blocking.
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

    MEASURED = "Heap/Intake/FileBytesIngester.swift"
    LIVE = "struct A { func f() { g() } }"
    GOLDEN = "HeapTests/Snapshots/StorageScreen/test_exemptBlock.1.png"
    #: A real PNG signature. The NUL bytes that follow it open the IHDR chunk
    #: of every PNG ever written, so a byte-level reader has its answer inside
    #: the first two dozen bytes.
    PNG = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\x00\x00\x01\x00\x08\x06"

    def write(self, ctx_factory, relpath, body):
        path = ctx_factory.wt / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")
        return path

    def measured_row(self, ctx_factory):
        return {str(ctx_factory.wt / self.MEASURED): 100.0}

    def test_a_changed_binary_asset_with_no_row_does_not_block(self, ctx_factory):
        """The reported wedge, reduced to its mechanism."""
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, self.GOLDEN, self.PNG)
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory, self.measured_row(ctx_factory), [self.MEASURED, self.GOLDEN]
            )
        )
        assert verdict.ok, verdict.message

    def test_the_binary_file_is_named_rather_than_silently_dropped(self, ctx_factory):
        """Set aside, never disappeared.

        A gate that narrows its own scope without saying which file it dropped
        is the vacuous green one layer down, and a reader cannot audit a
        decision that left no trace.
        """
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, self.GOLDEN, self.PNG)
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory, self.measured_row(ctx_factory), [self.MEASURED, self.GOLDEN]
            )
        )
        detail = verdict.detail or ""
        assert "test_exemptBlock.1.png" in detail
        assert "binary" in detail

    def test_a_binary_file_without_nul_bytes_is_still_recognised(self, ctx_factory):
        """NUL is the cheap tell, not the definition.

        A JPEG header carries none in its opening bytes, and a format that
        avoided them entirely would otherwise reopen the same wedge under a
        different extension. What decides is whether the bytes are text, which
        is asked directly.
        """
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        jpeg = "HeapTests/Snapshots/StorageScreen/test_exemptBlock.2.jpg"
        self.write(ctx_factory, jpeg, b"\xff\xd8\xff\xe0\x10JFIF\xc3\x28\xff\xd9")
        verdict = self.verdict(
            self.ctx_for(ctx_factory, self.measured_row(ctx_factory), [self.MEASURED, jpeg])
        )
        assert verdict.ok, verdict.message

    def test_a_text_source_file_with_no_row_still_blocks(self, ctx_factory):
        """The guard, and the reason this is an answer rather than a hole.

        The case the gate exists for — a live file the run never measured —
        must read exactly as it did before, so a path-spelling mismatch is
        still caught with the same sharpness.
        """
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(
            ctx_factory,
            "Heap/Intake/Unmeasured.swift",
            "struct Unmeasured {\n    func work() { compute() }\n}\n",
        )
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory,
                self.measured_row(ctx_factory),
                [self.MEASURED, "Heap/Intake/Unmeasured.swift"],
            )
        )
        assert not verdict.ok
        assert "no coverage row" in verdict.message
        assert "Unmeasured.swift" in verdict.detail

    def test_a_text_file_whose_last_character_straddles_the_sniff_boundary_blocks(
        self, ctx_factory
    ):
        """The widening this fix must not do, pinned at its likeliest entry.

        Deciding on a bounded prefix can cut a multi-byte character in half,
        and a decoder asked to treat that truncation as an error calls a live
        UTF-8 source file binary — passing unmeasured code silently, which is
        the one direction `_explain_absent_rows` refuses. The file below is
        ordinary Swift with executable lines, arranged so its final character
        begins exactly at the last byte read.
        """
        head = 'struct Wide {\n    func work() { compute("x") }\n}\n// '
        pad = "a" * (8191 - len(head.encode("utf-8")))
        body = head + pad + "é" + "a" * 40 + "\n"
        assert body.encode("utf-8")[8191] == 0xC3, "the sniff boundary must split a char"
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, "Heap/Intake/Wide.swift", body)
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory,
                self.measured_row(ctx_factory),
                [self.MEASURED, "Heap/Intake/Wide.swift"],
            )
        )
        assert not verdict.ok, verdict.detail
        assert "Wide.swift" in verdict.detail

    def test_a_file_that_cannot_be_read_still_blocks(self, ctx_factory):
        """A read that failed is "cannot tell", which relaxes nothing.

        Only a successful read proving the content is not text may set a file
        aside. An unreadable file is exactly the shape a wrong answer would
        take — nothing about it says there is no code inside.
        """
        import os

        self.write(ctx_factory, self.MEASURED, self.LIVE)
        sealed = self.write(
            ctx_factory,
            "Heap/Intake/Sealed.swift",
            "struct Sealed {\n    func work() { compute() }\n}\n",
        )
        os.chmod(sealed, 0o000)
        try:
            if os.access(sealed, os.R_OK):
                pytest.skip("this user can read a mode-000 file; the case cannot arise")
            verdict = self.verdict(
                self.ctx_for(
                    ctx_factory,
                    self.measured_row(ctx_factory),
                    [self.MEASURED, "Heap/Intake/Sealed.swift"],
                )
            )
        finally:
            os.chmod(sealed, 0o644)
        assert not verdict.ok
        assert "no coverage row" in verdict.message
        assert "Sealed.swift" in verdict.detail

    def test_the_existing_set_aside_reasons_are_unchanged(self, ctx_factory):
        """The controls that must read the same in both states.

        A declaration-only file and a test file are set aside on the evidence
        they always were, and the binary branch neither replaces their reason
        nor swallows their name.
        """
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, "Heap/Intake/Ports.swift", "protocol P { func a() }\n")
        self.write(
            ctx_factory,
            "HeapTests/IntakeTests.swift",
            "final class IntakeTests {\n    func testA() { assert(true) }\n}\n",
        )
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory,
                self.measured_row(ctx_factory),
                [self.MEASURED, "Heap/Intake/Ports.swift", "HeapTests/IntakeTests.swift"],
            )
        )
        assert verdict.ok, verdict.message
        detail = verdict.detail or ""
        assert "Ports.swift (no executable lines to measure)" in detail
        assert "IntakeTests.swift (a test file; not what a coverage floor measures)" in detail

    def test_the_verdict_no_longer_advises_an_action_that_does_not_exist(
        self, ctx_factory
    ):
        """`session files` exposes only `add`, and there is no remove verb.

        That absence is deliberate: removing a source file from the declared
        set would be a lever for dropping it out of the floor. The union in
        `_changed_set` means it would not even work — git re-supplies a file
        committed on the branch. Advice that is unavailable AND ineffective
        teaches a reader that the gate does not know what it is talking about.
        """
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(
            ctx_factory,
            "Heap/Intake/Unmeasured.swift",
            "struct Unmeasured {\n    func work() { compute() }\n}\n",
        )
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory,
                self.measured_row(ctx_factory),
                [self.MEASURED, "Heap/Intake/Unmeasured.swift"],
            )
        )
        assert not verdict.ok
        assert not any("session file set" in fix for fix in verdict.fixes)

    def test_an_empty_file_is_not_evidence_of_anything(self, ctx_factory):
        """Zero bytes are not a positive answer, and the branch says so.

        An empty asset is the shape a truncated write leaves behind. Nothing
        about it establishes that no code belongs there, and a rule that read
        emptiness as "binary" would set aside a file whose content is simply
        missing — evidence by absence, which is what every branch here
        refuses. It keeps blocking, and the block is what sends someone to
        look at a file that should not be empty.
        """
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, self.GOLDEN, b"")
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory, self.measured_row(ctx_factory), [self.MEASURED, self.GOLDEN]
            )
        )
        assert not verdict.ok
        assert "no coverage row" in verdict.message
        assert "test_exemptBlock.1.png" in verdict.detail

    def test_a_binary_only_change_is_na_before_any_coverage_run_exists(
        self, ctx_factory
    ):
        """The same fact, read by the guard that runs before any recording.

        `_nothing_a_run_could_measure` unlocks on exactly the evidence
        `_explain_absent_rows` collects, so a change that committed only a
        golden image must not be told to run coverage — the run would produce
        no row for it however long it took, and on a project whose suite is
        not green it banks a failing test entry to say so.
        """
        self.write(ctx_factory, self.GOLDEN, self.PNG)
        verdict = self.verdict(self.ctx_for(ctx_factory, {}, [self.GOLDEN]))
        assert verdict.ok and verdict.skipped, verdict.message
        assert "test_exemptBlock.1.png" in (verdict.detail or "")

    def test_a_source_file_alongside_it_still_demands_a_run(self, ctx_factory):
        """The control on that guard. One file that could have had a row keeps
        the ordinary "you forgot to run coverage" refusal reading as itself."""
        self.write(ctx_factory, self.MEASURED, self.LIVE)
        self.write(ctx_factory, self.GOLDEN, self.PNG)
        verdict = self.verdict(
            self.ctx_for(ctx_factory, {}, [self.MEASURED, self.GOLDEN])
        )
        assert not verdict.ok, verdict.message
        assert "no coverage was recorded" in verdict.message


class TestTheReportersScopeIsNotDemandedOfFilesOutsideIt:
    """Repro for BUG_019 — see the backlog entry.

    A Python session that touches its own test files was handed a
    `coverage.meets_floor` verdict no work could satisfy: `source_files` counts
    every `.py` as source, so `tests/test_x.py` enters the touched set, while
    `[tool.coverage.run] source = ["src/..."]` means the reporter is DEFINED
    never to emit a row for it. Three correct behaviours meeting badly, and the
    result is the shape BUG_002 was filed against — a blocking gate trained
    around rather than met.

    The resolution keeps the number's meaning rather than widening what is
    measured: the adapter supplies the reporter's configured scope and the rule
    stops demanding rows for files outside it. Files set aside are NAMED, as
    every other excusal in this gate is.
    """

    def ctx_for(self, ctx_factory, coverage, declared):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
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

    SRC = "src/app/mod.py"
    TEST = "tests/test_mod.py"
    LIVE = "def f():\n    return g()\n"

    def wire(self, ctx_factory, pyproject='[tool.coverage.run]\nsource = ["src/app"]\n'):
        for rel in (self.SRC, self.TEST):
            path = ctx_factory.wt / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.LIVE, encoding="utf-8")
        (ctx_factory.wt / "pyproject.toml").write_text(pyproject, encoding="utf-8")
        return {str(ctx_factory.wt / self.SRC): 100.0}

    def test_a_touched_test_file_outside_the_reporters_scope_does_not_block(
        self, ctx_factory
    ):
        """The reported case, and the whole of it."""
        measured = self.wire(ctx_factory)
        verdict = self.verdict(self.ctx_for(ctx_factory, measured, [self.SRC, self.TEST]))
        assert verdict.ok, verdict.message

    def test_the_file_set_aside_is_named(self, ctx_factory):
        """A gate that narrows its own scope silently is the vacuous green."""
        measured = self.wire(ctx_factory)
        verdict = self.verdict(self.ctx_for(ctx_factory, measured, [self.SRC, self.TEST]))
        assert "test_mod.py" in (verdict.detail or "")

    def test_a_source_file_inside_the_scope_with_no_row_still_blocks(
        self, ctx_factory
    ):
        """The guard, and the reason this is a narrowing rather than a silencing.

        A file the reporter IS scoped to measure, with no row, is exactly what
        the gate exists to catch, and must survive the fix untouched.
        """
        measured = self.wire(ctx_factory)
        other = ctx_factory.wt / "src/app/unmeasured.py"
        other.write_text(self.LIVE, encoding="utf-8")
        verdict = self.verdict(
            self.ctx_for(ctx_factory, measured, [self.SRC, "src/app/unmeasured.py"])
        )
        assert not verdict.ok
        assert "no coverage row" in verdict.message
        assert "unmeasured.py" in verdict.detail

    def test_an_unreadable_scope_leaves_the_block_in_place(self, ctx_factory):
        """`None` from the adapter is "cannot tell", which relaxes nothing.

        Without a resolvable `source`, a production file with no row keeps
        blocking — the pre-fix behaviour, retained wherever no positive fact
        is available. A touched TEST file no longer depends on this scope
        fact: `is_test_file` answers the conventional half of the question
        positively, so it is set aside even when the configured scope cannot
        be read (the complementary mechanism recorded on BUG_019).
        """
        measured = self.wire(ctx_factory, pyproject='[project]\nname = "x"\n')
        other = ctx_factory.wt / "src/app/unmeasured.py"
        other.write_text(self.LIVE, encoding="utf-8")
        verdict = self.verdict(
            self.ctx_for(ctx_factory, measured, [self.SRC, "src/app/unmeasured.py"])
        )
        assert not verdict.ok
        assert "no coverage row" in verdict.message
        assert "unmeasured.py" in verdict.detail

    def test_a_scope_emptied_by_the_narrowing_is_not_a_pass(self, ctx_factory):
        """A change touching ONLY test files has nothing left to hold to the
        floor. That is N/A — reporting "all 0 file(s) at or above 90%" would be
        a green earned by the scope emptying itself."""
        measured = self.wire(ctx_factory)
        verdict = self.verdict(self.ctx_for(ctx_factory, measured, [self.TEST]))
        assert verdict.ok and verdict.skipped
        assert "test_mod.py" in (verdict.detail or "")

    def test_a_test_only_change_is_na_before_any_coverage_run_exists(
        self, ctx_factory
    ):
        """Nothing to measure is an answer a RUN cannot change.

        The sibling above proves the scope empties correctly once a run has
        recorded something. It cannot see the ordering defect, because it
        hands the gate a recording. `coverage.meets_floor` returns
        `no coverage was recorded` from an early guard that fires roughly
        fifty lines BEFORE the scoping logic, so a change touching only test
        files can never reach the N/A that is already written for it.

        The demand that creates is not merely redundant, it is destructive.
        `invariant pipeline coverage` takes no selector, so the only way to
        satisfy the guard is a whole-scheme run; and a coverage run is
        recorded as a test run, so on a project whose suite is not green that
        run banks a FAILING `test_runs` entry and flips `test.all_pass` from
        green to red — to produce numbers this rule then discards, because
        every file it measured was excluded from the floor anyway.

        Only positive evidence unlocks this: `_explain_absent_rows` sets a
        file aside for being deleted, outside the reporter's scope, a test
        file, or free of executable lines. One real source file in the set
        lands in `unexplained` and the recording is demanded exactly as
        before — which the sibling test below pins.
        """
        self.wire(ctx_factory)
        ctx = self.ctx_for(ctx_factory, {}, [self.TEST])
        verdict = self.verdict(ctx)
        assert verdict.ok and verdict.skipped, verdict.message
        assert "test_mod.py" in (verdict.detail or "")

    def test_a_source_file_with_no_run_still_demands_one(self, ctx_factory):
        """The control. Relaxing the guard must not relax the common case.

        A touched file that COULD carry a row keeps the original refusal, so
        "I forgot to run coverage" still reads as itself rather than as a
        scope answer.
        """
        self.wire(ctx_factory)
        verdict = self.verdict(self.ctx_for(ctx_factory, {}, [self.SRC]))
        assert not verdict.ok, verdict.message


class TestPathsTheManifestHoldsOutOfTheFloor:
    """`methodology.gates.coverage_exclude` — the policy half of BUG_026.

    A consuming project measured five of its own test files at 63–90% against
    a floor of 90 and could express no exclusion: the floor value and the rule
    LEVEL are both global, narrowing the reporter converts a below-floor block
    into a no-row block, and the declared file set only ever ADDS. So the
    decision "test targets are not held to the production floor" had nowhere
    to live except a habit each author remembers.

    It excludes from the FLOOR, never from the MEASUREMENT: the row is still
    required, the number is still reported, and the verdict names every path
    it set aside together with what matched it.
    """

    def ctx_for(self, ctx_factory, declared, exclude, coverage):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
                "methodology": {
                    "gates": {
                        "coverage_min": 90,
                        "coverage_scope": "touched",
                        "coverage_exclude": exclude,
                    }
                },
            },
        )
        return ctx_factory(
            cwd=ctx_factory.main,
            session={
                "id": "s1",
                "files": declared,
                "worktree": str(ctx_factory.wt),
                "base": "",
            },
            pipeline={"evidence": {"coverage": coverage}},
        )

    def verdict(self, ctx):
        return only(evaluate(["coverage.meets_floor"], ctx)).verdict

    SRC = "src/app/mod.py"
    TEST = "tests/test_mod.py"
    LIVE = "def f():\n    return g()\n"

    def wire(self, ctx_factory):
        for rel in (self.SRC, self.TEST):
            path = ctx_factory.wt / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.LIVE, encoding="utf-8")
        return {
            str(ctx_factory.wt / self.SRC): 100.0,
            str(ctx_factory.wt / self.TEST): 63.0,
        }

    def test_an_excluded_path_below_the_floor_does_not_block(self, ctx_factory):
        """The reported case."""
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory, [self.SRC, self.TEST], ["tests/**"], self.wire(ctx_factory)
            )
        )
        assert verdict.ok, verdict.message

    def test_the_exclusion_names_the_path_the_number_and_the_pattern(
        self, ctx_factory
    ):
        """All three, because each answers a different question a reader has.

        The path says WHAT was set aside, the pattern says WHICH decision did
        it, and the number is what keeps a suite that quietly stopped
        executing visible instead of exempt-and-invisible.
        """
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory, [self.SRC, self.TEST], ["tests/**"], self.wire(ctx_factory)
            )
        )
        detail = verdict.detail or ""
        assert "test_mod.py" in detail
        assert "63%" in detail
        assert "tests/**" in detail

    def test_an_unexcluded_path_below_the_floor_still_blocks(self, ctx_factory):
        """The floor is unchanged for everything the manifest did not name."""
        verdict = self.verdict(
            self.ctx_for(
                ctx_factory, [self.SRC, self.TEST], ["nothing/**"], self.wire(ctx_factory)
            )
        )
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message

    def test_an_excluded_path_with_no_row_still_blocks(self, ctx_factory):
        """The guard that stops this being a silencing.

        Exclusion happens AFTER the no-row check, so suppressing a path from
        the reporter cannot buy a pass through this door. Without that
        ordering, `coverage_exclude` plus a narrowed report would hide a
        path-spelling mismatch — which is the exact failure the no-row guard
        was written for.

        Uses a production path rather than `self.TEST`: with the
        `Adapter.is_test_file` mechanism (BUG_019), a touched test file with
        no row is set aside on that positive fact alone, which would pass
        this test for the wrong reason and stop it isolating the
        `coverage_exclude` ordering it exists to guard.
        """
        excluded = "src/app/generated.py"
        path = ctx_factory.wt / excluded
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.LIVE, encoding="utf-8")
        measured = {str(ctx_factory.wt / self.SRC): 100.0}
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.SRC, excluded], [excluded], measured)
        )
        assert not verdict.ok
        assert "no coverage row" in verdict.message

    def test_no_exclude_configured_changes_nothing(self, ctx_factory):
        """Empty by default: an exclusion is a decision, never an inheritance."""
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.SRC, self.TEST], [], self.wire(ctx_factory))
        )
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message


class TestTheRecordedBaselineThatRatchetsTheFloor:
    """`methodology.gates.coverage_baseline` — a floor that legacy debt cannot veto.

    The floor is right for new code and wrong for old. A file that has sat at
    70% for months blocks a two-line, fully covered, net-negative edit — so the
    gate refuses precisely the change that improves it, and the only ways
    forward are to close a several-hundred-line gap that was never this
    change's, or to route around the gate. The motivating case moved a file
    from 69.17% to 70.99% while deleting more than it added, and was blocked.

    A ratchet, on the same idiom this project already uses for its layering
    baseline: a recorded per-file number the measurement may never fall below.
    Below the floor and at or above the record is a pre-existing shortfall and
    does not block; below the record is a REGRESSION and does, in its own
    words; and no record at all fails exactly as before, because the
    relaxation must rest on positive, reviewable evidence rather than on the
    absence of any.
    """

    SRC = "src/app/mod.py"
    LEGACY = "src/app/legacy.py"
    LIVE = "def f():\n    return g()\n"
    BASELINE = "scripts/coverage_baseline.txt"

    #: Distinguishes "no key" from "a key whose value is this".
    UNSET = object()

    def ctx_for(self, ctx_factory, declared, coverage, baseline=UNSET):
        gates = {"coverage_min": 90, "coverage_scope": "touched"}
        if baseline is not self.UNSET:
            gates["coverage_baseline"] = baseline
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["python"]},
                "methodology": {"gates": gates},
            },
        )
        return ctx_factory(
            cwd=ctx_factory.main,
            session={
                "id": "s1",
                "files": declared,
                "worktree": str(ctx_factory.wt),
                "base": "",
            },
            pipeline={"evidence": {"coverage": coverage}},
        )

    def verdict(self, ctx):
        return only(evaluate(["coverage.meets_floor"], ctx)).verdict

    def wire(self, ctx_factory, measured, baseline_text=None):
        """Source files on disk, a measurement, and optionally a baseline file."""
        out = {}
        for rel, pct in measured.items():
            path = ctx_factory.wt / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(self.LIVE, encoding="utf-8")
            out[str(path)] = pct
        if baseline_text is not None:
            recorded = ctx_factory.wt / self.BASELINE
            recorded.parent.mkdir(parents=True, exist_ok=True)
            recorded.write_text(baseline_text, encoding="utf-8")
        return out

    # -- the shortfall the record forgives ----------------------------

    def test_a_file_exactly_at_its_recorded_baseline_does_not_block(
        self, ctx_factory
    ):
        """The floor is missed, the record is met, and the change proceeds."""
        measured = self.wire(
            ctx_factory, {self.LEGACY: 70.99}, f"{self.LEGACY} 70.99\n"
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        assert verdict.ok, verdict.message

    def test_an_improvement_still_short_of_the_floor_does_not_block(
        self, ctx_factory
    ):
        """The reported case, and the whole point of the mechanism.

        A change that RAISED the number from 69.17 to 70.99 was blocked by a
        floor of 90. The ratchet is what makes an improvement legal before the
        gap is fully closed.
        """
        measured = self.wire(
            ctx_factory, {self.LEGACY: 70.99}, f"{self.LEGACY} 69.17\n"
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        assert verdict.ok, verdict.message

    def test_the_set_aside_names_the_file_and_both_numbers(self, ctx_factory):
        """A relaxation nobody can read in the verdict is how a gate goes inert.

        Three facts, each answering a different question: WHICH file was let
        past, what it MEASURES now, and what it was RECORDED at — the last
        being the only one that says the pass was earned against evidence
        rather than granted.
        """
        measured = self.wire(
            ctx_factory, {self.LEGACY: 70.99}, f"{self.LEGACY} 69.17\n"
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        detail = verdict.detail or ""
        assert "legacy.py" in detail
        assert "70.99%" in detail
        assert "69.17%" in detail
        assert "ratcheted" in detail

    def test_a_ratcheted_file_is_still_named_when_another_file_blocks(
        self, ctx_factory
    ):
        """The set-aside is reported whatever the verdict turns out to be.

        A pass that narrowed its own scope is reviewable only if the narrowing
        is written where the verdict is read, and that is no less true of a
        FAILING verdict — a reader deciding what to fix needs to know which
        below-floor file was already answered for.
        """
        measured = self.wire(
            ctx_factory,
            {self.LEGACY: 70.99, self.SRC: 40.0},
            f"{self.LEGACY} 69.17\n",
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY, self.SRC], measured, self.BASELINE)
        )
        assert not verdict.ok
        assert "mod.py" in (verdict.detail or "")
        assert "legacy.py" in (verdict.detail or "")
        assert "69.17%" in (verdict.detail or "")

    # -- the ratchet biting -------------------------------------------

    def test_a_drop_below_the_recorded_baseline_fails(self, ctx_factory):
        """The half that makes this a ratchet rather than an amnesty."""
        measured = self.wire(
            ctx_factory, {self.LEGACY: 65.0}, f"{self.LEGACY} 69.17\n"
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        assert not verdict.ok, verdict.message

    def test_the_regression_reads_as_a_regression_not_a_floor_miss(
        self, ctx_factory
    ):
        """Two different failures need two different sentences.

        "Below the floor" sends the author to write several hundred lines of
        test for a gap that predates them. "You lost 4.17 points against a
        number this project recorded" sends them to the four lines they just
        deleted. Same file, same floor, opposite next action.
        """
        measured = self.wire(
            ctx_factory, {self.LEGACY: 65.0}, f"{self.LEGACY} 69.17\n"
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        assert "below the 90% floor" not in verdict.message
        assert "baseline" in verdict.message
        detail = verdict.detail or ""
        assert "65%" in detail
        assert "69.17%" in detail

    # -- the fail-safe --------------------------------------------------

    def test_a_below_floor_file_with_no_entry_blocks_exactly_as_before(
        self, ctx_factory
    ):
        """No record means no acknowledged debt.

        The relaxation is granted on positive evidence only. An absent entry
        is the ordinary case — every file in the project has one — so reading
        it as permission would turn the ratchet into a global amnesty.
        """
        measured = self.wire(
            ctx_factory, {self.SRC: 50.0}, f"{self.LEGACY} 69.17\n"
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.SRC], measured, self.BASELINE)
        )
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message
        assert "mod.py" in (verdict.detail or "")

    def test_no_baseline_configured_leaves_the_gate_as_it_was(self, ctx_factory):
        """Absent key, feature entirely off, verdict unchanged."""
        measured = self.wire(ctx_factory, {self.LEGACY: 70.99}, None)
        verdict = self.verdict(self.ctx_for(ctx_factory, [self.LEGACY], measured))
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message
        assert "ratchet" not in (verdict.detail or "").lower()

    def test_a_null_baseline_is_the_same_as_no_baseline(self, ctx_factory):
        """An explicit null is the default written out, not an empty record."""
        measured = self.wire(ctx_factory, {self.LEGACY: 70.99}, None)
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, None)
        )
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message

    def test_a_file_above_the_floor_is_never_measured_against_the_record(
        self, ctx_factory
    ):
        """The record answers for shortfalls, and nothing else.

        Discriminating on purpose: the file measures 95 against a floor of 90
        and a STALE record of 100. Consulting the record for a file that
        already clears the floor would read that as a 5-point regression and
        block a change that is fine — the ratchet would have quietly become a
        second, higher floor nobody agreed to.
        """
        measured = self.wire(ctx_factory, {self.SRC: 95.0}, f"{self.SRC} 100\n")
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.SRC], measured, self.BASELINE)
        )
        assert verdict.ok, verdict.message

    # -- a record the gate cannot read ----------------------------------

    def test_a_malformed_line_is_an_error_not_a_skipped_entry(self, ctx_factory):
        """A baseline that silently drops entries is the grandfathering trap.

        The dropped entry is invisible in both directions: the debt it
        recorded stops being enforced, and the file it named stops being
        forgiven — and neither shows up anywhere. So an unparseable line
        fails the gate rather than reducing the record behind the reader's
        back.
        """
        measured = self.wire(
            ctx_factory,
            {self.LEGACY: 70.99},
            f"{self.LEGACY} 69.17 stale\n",
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        assert not verdict.ok, verdict.message
        assert "baseline" in verdict.message
        assert "ratcheted" not in (verdict.detail or "")

    def test_a_non_numeric_percentage_is_an_error(self, ctx_factory):
        """Same fact, the other half of the line."""
        measured = self.wire(
            ctx_factory, {self.LEGACY: 70.99}, f"{self.LEGACY} sixty\n"
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        assert not verdict.ok
        assert "baseline" in verdict.message

    def test_the_error_names_the_line_it_could_not_read(self, ctx_factory):
        """An error that does not say WHICH line is a hunt, not a report."""
        measured = self.wire(
            ctx_factory,
            {self.LEGACY: 70.99},
            f"# a comment\n\n{self.LEGACY} sixty\n",
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        detail = verdict.detail or ""
        assert "line 3" in detail
        assert "sixty" in detail

    def test_a_duplicate_entry_is_an_error(self, ctx_factory):
        """Two records for one file name no number the gate can enforce.

        Last-wins would silently pick one of them, and which one it picked
        would decide whether a regression passes.
        """
        measured = self.wire(
            ctx_factory,
            {self.LEGACY: 70.99},
            f"{self.LEGACY} 69.17\n{self.LEGACY} 40\n",
        )
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        assert not verdict.ok
        assert "baseline" in verdict.message

    def test_a_configured_baseline_that_does_not_exist_is_an_error(
        self, ctx_factory
    ):
        """An absent file is not an empty record.

        It is indistinguishable from a ratchet that was never wired, and the
        gate will not read a relaxation — or the absence of one — out of a
        file it could not find. Loud, because the manifest key and the file it
        names have to land in the same commit.
        """
        measured = self.wire(ctx_factory, {self.SRC: 100.0}, None)
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.SRC], measured, self.BASELINE)
        )
        assert not verdict.ok
        assert "baseline" in verdict.message

    # -- what the format tolerates --------------------------------------

    def test_comments_blank_lines_and_ragged_whitespace_are_accepted(
        self, ctx_factory
    ):
        """Liberal in the shape of a line, strict about one it cannot parse."""
        text = (
            "# The coverage ratchet. A number here may only fall.\n"
            "\n"
            "   \n"
            f"\t{self.LEGACY}   69.17  # IDEA_LEGACY_COVERAGE_FLOOR\n"
        )
        measured = self.wire(ctx_factory, {self.LEGACY: 70.99}, text)
        verdict = self.verdict(
            self.ctx_for(ctx_factory, [self.LEGACY], measured, self.BASELINE)
        )
        assert verdict.ok, verdict.message


class TestAChangeThatAlteredNoCodeIsNotHeldToTheFloor:
    """The fact half of BUG_026 — a comment-only diff.

    Coverage is a property of executable lines. A change that rewrote only
    comments moved no number and can move none, so the work the floor demands
    of it cannot affect the thing being demanded. It arrives on exactly the
    changes that deserve the least friction: a change that DELETES a symbol
    must rewrite every comment citing it, and each of those files then owes a
    coverage debt it did not create and cannot discharge.

    Positive evidence only. Any "cannot tell" — no base, an unreadable
    revision, an adapter with no opinion on the language — leaves the file in
    the floor.
    """

    SRC = "app/mod.swift"

    def repo(self, ctx_factory, before, after):
        """`before` on the base branch, `after` dirty in the session worktree.

        The real shape: the fixture's worktree is a linked one on its own
        branch, so the base revision is a commit the session did not make.
        """
        main, wt = ctx_factory.main, ctx_factory.wt
        landed = main / self.SRC
        landed.parent.mkdir(parents=True, exist_ok=True)
        landed.write_text(before, encoding="utf-8")
        run(["git", "add", "-A"], main)
        run(["git", "commit", "-qm", "base"], main)
        run(["git", "merge", "-q", "--ff-only", "master"], wt)

        path = wt / self.SRC
        path.write_text(after, encoding="utf-8")
        return {str(path): 30.0}

    def ctx_for(self, ctx_factory, coverage):
        write_manifest(
            ctx_factory.main,
            {
                "version": 1,
                "project": {"name": "demo", "ecosystems": ["swift"]},
                "methodology": {
                    "gates": {"coverage_min": 90, "coverage_scope": "touched"}
                },
            },
        )
        return ctx_factory(
            cwd=ctx_factory.wt,
            session={
                "id": "s1",
                "files": [self.SRC],
                "worktree": str(ctx_factory.wt),
                "base": "master",
            },
            pipeline={"evidence": {"coverage": coverage}},
        )

    def verdict(self, ctx):
        return only(evaluate(["coverage.meets_floor"], ctx)).verdict

    BEFORE = '// queued into `pending_imports`\nfunc go() {\n    run("a")\n}\n'

    def test_a_comment_only_edit_below_the_floor_does_not_block(self, ctx_factory):
        """The reported case: the only edit deletes a citation of a deleted type."""
        after = '// queued for review\nfunc go() {\n    run("a")\n}\n'
        measured = self.repo(ctx_factory, self.BEFORE, after)
        verdict = self.verdict(self.ctx_for(ctx_factory, measured))
        assert verdict.ok, verdict.message
        assert "altered no code" in (verdict.detail or "")

    def test_reindenting_is_not_a_code_change(self, ctx_factory):
        """Layout is not something a coverage run can see."""
        after = '// queued for review\nfunc go() {\n        run("a")\n}\n'
        measured = self.repo(ctx_factory, self.BEFORE, after)
        assert self.verdict(self.ctx_for(ctx_factory, measured)).ok

    def test_a_changed_statement_still_blocks(self, ctx_factory):
        """The control. One line of real code and the floor applies in full."""
        after = '// queued for review\nfunc go() {\n    run("a")\n    stop()\n}\n'
        measured = self.repo(ctx_factory, self.BEFORE, after)
        verdict = self.verdict(self.ctx_for(ctx_factory, measured))
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message

    def test_a_changed_string_literal_still_blocks(self, ctx_factory):
        """The sharp control, and the reason this cannot reuse the reader that
        answers `carries_executable_code`.

        That one blanks a literal's CONTENTS — right for "is there a statement
        here", fatal for "did the code change", because `"a"` and `"b"` reduce
        to the same text. A reduction that loses this calls a real edit
        cosmetic, which is the one mistake this answer must never make.
        """
        after = '// queued for review\nfunc go() {\n    run("b")\n}\n'
        measured = self.repo(ctx_factory, self.BEFORE, after)
        verdict = self.verdict(self.ctx_for(ctx_factory, measured))
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message

    def test_no_base_revision_leaves_the_block_in_place(self, ctx_factory):
        """"Cannot tell" relaxes nothing — there is no before to compare to."""
        after = '// queued for review\nfunc go() {\n    run("a")\n}\n'
        measured = self.repo(ctx_factory, self.BEFORE, after)
        ctx = self.ctx_for(ctx_factory, measured)
        ctx.session["base"] = ""
        verdict = self.verdict(ctx)
        assert not verdict.ok
        assert "below the 90% floor" in verdict.message


class TestTheNormalisedPayloadContract:
    """BUG_022 — the dependency rule the code no longer kept.

    `overview.md` stated "Nothing above `hosts/` is aware of hook schemas"
    while two sites above `hosts/` read payload keys, and BUG_010's fix
    (faa4de0) had come to DEPEND on that departure — the path a call names is
    what attributes its evidence to the right project. A reader enforcing the
    documented rule would have read the fix as a layering violation.

    The rule now names the keys instead of denying them. This pins the list, so
    widening the seam again is a deliberate edit to a stated contract rather
    than a quiet import.
    """

    def test_the_contract_names_exactly_the_keys_the_engine_reads(self):
        assert set(NORMALISED_PAYLOAD_KEYS) == {
            "tool_name",
            "tool_input",
            "tool_input.file_path",
            "tool_input.notebook_path",
            "stop_hook_active",
        }

    def test_the_rule_context_reads_the_declared_top_level_keys(self, ctx_factory):
        ctx = ctx_factory(
            payload={
                "tool_name": "Edit",
                "tool_input": {"file_path": "/tmp/x.py"},
            }
        )
        assert ctx.tool_name == "Edit"
        assert ctx.tool_input == {"file_path": "/tmp/x.py"}
        assert ctx.target_path == Path("/tmp/x.py")

    def test_the_notebook_spelling_of_the_target_is_declared_too(self, ctx_factory):
        """`target_path` is derived from either spelling, so both are part of
        what a host must normalise — a host filling only one leaves the
        attribution BUG_010 fixed working for some tool calls and not others."""
        ctx = ctx_factory(
            payload={"tool_name": "NotebookEdit", "tool_input": {"notebook_path": "/tmp/n.ipynb"}}
        )
        assert ctx.target_path == Path("/tmp/n.ipynb")

    def test_the_documented_rule_is_the_one_the_overview_states(self):
        """The doc and the constant are the two halves BUG_022 found apart."""
        overview = (
            Path(__file__).resolve().parents[1]
            / "docs"
            / "architecture"
            / "overview.md"
        ).read_text(encoding="utf-8")
        assert "NORMALISED_PAYLOAD_KEYS" in overview
        assert "Nothing above `hosts/` is aware of hook schemas." not in overview


class TestProofOfRedIsAProofAboutATest:
    """BUG_029 — `test.new_test_fails` keyed off the exit status alone.

    A compile error, a signing failure or a missing simulator all exit
    non-zero and execute nothing, and every one of them was reported as
    "a red run is on record". The gate's name is a claim about a TEST, and
    the same file one rule below already refuses a GREEN that executed
    nothing — the asymmetry was the defect.
    """

    def red(self, **record):
        return {"evidence": {"test_runs": [{"phase": "red", "passed": False, **record}]}}

    def test_an_unparseable_failure_count_is_not_proof(self, ctx_factory):
        """The observed run, verbatim: exit 65, nothing countable."""
        result = only(
            evaluate(
                ["test.new_test_fails"],
                ctx_factory(pipeline=self.red(exit_code=65, executed=1, failed=None)),
            )
        )
        assert not result.verdict.ok
        assert "failure count" in result.verdict.detail

    def test_a_run_that_executed_nothing_is_not_proof(self, ctx_factory):
        result = only(
            evaluate(
                ["test.new_test_fails"],
                ctx_factory(pipeline=self.red(exit_code=65, executed=0, failed=None)),
            )
        )
        assert not result.verdict.ok
        assert "executed no tests" in result.verdict.detail

    def test_a_non_zero_exit_with_zero_failures_is_not_proof(self, ctx_factory):
        """The compile-error shape: the tool failed, no test did."""
        result = only(
            evaluate(
                ["test.new_test_fails"],
                ctx_factory(pipeline=self.red(exit_code=65, executed=0, failed=0)),
            )
        )
        assert not result.verdict.ok

    def test_the_rejected_run_is_named(self, ctx_factory):
        """An inconclusive verdict that does not name the run sends the
        session hunting a second red rather than reading the build log."""
        pipeline = self.red(exit_code=65, executed=0, failed=None, command="swift test -x")
        result = only(evaluate(["test.new_test_fails"], ctx_factory(pipeline=pipeline)))
        assert "swift test -x" in result.verdict.detail

    def test_a_counted_failure_is_proof(self, ctx_factory):
        assert only(
            evaluate(
                ["test.new_test_fails"],
                ctx_factory(pipeline=self.red(exit_code=1, executed=5, failed=2)),
            )
        ).verdict.ok

    def test_an_adapter_that_cannot_count_tests_is_not_held_against_the_run(
        self, ctx_factory
    ):
        """`executed is None` means "this adapter does not count", which is
        the generic adapter's permanent state — the same distinction
        `TestResult.trustworthy` already draws between None and zero."""
        assert only(
            evaluate(
                ["test.new_test_fails"],
                ctx_factory(pipeline=self.red(exit_code=1, executed=None, failed=3)),
            )
        ).verdict.ok

    def test_one_conclusive_red_among_several_is_enough(self, ctx_factory):
        pipeline = {
            "evidence": {
                "test_runs": [
                    {"phase": "red", "passed": False, "executed": 0, "failed": None},
                    {"phase": "red", "passed": False, "executed": 4, "failed": 1},
                ]
            }
        }
        assert only(
            evaluate(["test.new_test_fails"], ctx_factory(pipeline=pipeline))
        ).verdict.ok


class TestProofOfRedReadsTheMachinesOwnRedState:
    """BUG_030 — the rule hardcoded the state name `red`.

    `bugfix` runs triaging -> reproducing -> fixing, and `reproducing` is the
    state that DECLARES this gate. `advance red` is not a legal transition
    from anywhere on that machine, so the gate its own machine names as an
    exit condition was unsatisfiable except by labelling a run with a state
    the pipeline was never in.
    """

    def test_the_bugfix_machines_red_state_satisfies_the_gate(self, ctx_factory):
        pipeline = {
            "methodology": "bugfix",
            "state": "reproducing",
            "evidence": {
                "test_runs": [
                    {"phase": "reproducing", "passed": False, "executed": 3, "failed": 1}
                ]
            },
        }
        assert only(
            evaluate(["test.new_test_fails"], ctx_factory(pipeline=pipeline))
        ).verdict.ok

    def test_the_advice_names_the_phase_this_machine_can_produce(self, ctx_factory):
        pipeline = {"methodology": "bugfix", "state": "reproducing", "evidence": {}}
        result = only(evaluate(["test.new_test_fails"], ctx_factory(pipeline=pipeline)))
        assert not result.verdict.ok
        assert "--phase reproducing" in " ".join(result.verdict.fixes)

    def test_tdd_still_reads_red(self, ctx_factory):
        pipeline = {
            "methodology": "tdd",
            "state": "red",
            "evidence": {
                "test_runs": [{"phase": "red", "passed": False, "executed": 3, "failed": 1}]
            },
        }
        assert only(
            evaluate(["test.new_test_fails"], ctx_factory(pipeline=pipeline))
        ).verdict.ok

    def test_a_machine_that_cannot_be_loaded_is_not_a_pass(self, ctx_factory):
        """An unanswerable question is not a pass — the AUD-08 convention."""
        pipeline = {
            "methodology": "no-such-methodology",
            "evidence": {
                "test_runs": [{"phase": "red", "passed": False, "executed": 3, "failed": 1}]
            },
        }
        assert not only(
            evaluate(["test.new_test_fails"], ctx_factory(pipeline=pipeline))
        ).verdict.ok
