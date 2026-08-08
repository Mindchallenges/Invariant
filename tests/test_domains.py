import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.core.errors import GateFailure, TransitionError, UsageError
from invariant.core.items import (
    JsonBackend,
    MarkdownBackend,
    SphinxNeedsBackend,
    backend_for,
)
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.domains.pipeline import PipelineDomain, summarise
from invariant.domains.session import SessionDomain
from invariant.domains.workitem import WorkItemDomain


def git_project(root: Path, manifest: dict | None = None) -> Manifest:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=root, check=True)
    (root / "docs" / "architecture").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "architecture" / "overview.md").write_text(
        "# Overview\n\n```mermaid\ngraph TD\n  A-->B\n```\n", encoding="utf-8"
    )
    path = root / MANIFEST_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(manifest or {"version": 1, "project": {"name": "demo"}}),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return Manifest.load(root)


@pytest.fixture
def m(tmp_path):
    return git_project(tmp_path / "proj")


# -- session -----------------------------------------------------------


class TestSessionDomain:
    def test_start_registers(self, m):
        domain = SessionDomain(m)
        session = domain.start("add-widget")
        assert session.state == "registered"
        assert domain.get(session.id).name == "add-widget"

    def test_name_is_slugified(self, m):
        assert SessionDomain(m).start("Add The Widget").name == "add-the-widget"

    def test_an_unnameable_session_is_refused(self, m):
        with pytest.raises(UsageError, match="at least one alphanumeric"):
            SessionDomain(m).start("!!!")

    def test_duplicate_id_is_refused(self, m):
        domain = SessionDomain(m)
        domain.start("one", session_id="abc")
        with pytest.raises(UsageError, match="already exists"):
            domain.start("two", session_id="abc")

    def test_state_root_is_shared_across_worktrees(self, m, tmp_path):
        """The property a per-worktree state directory would break."""
        domain = SessionDomain(m)
        session = domain.start("shared")
        domain.isolate(session.id, execute=True)

        worktree = Path(domain.get(session.id).worktree)
        from_worktree = Manifest.load(worktree)
        assert from_worktree.state_root == m.state_root
        assert SessionDomain(from_worktree).get(session.id) is not None

    def test_a_registered_session_is_findable_before_it_isolates(self, m):
        domain = SessionDomain(m)
        session = domain.start("pre-isolation")
        # Without the origin fallback this returns None and the session can
        # never be isolated, because isolate needs to find it first.
        assert domain.current(cwd=m.root).id == session.id

    def test_current_prefers_the_worktree_match(self, m):
        domain = SessionDomain(m)
        session = domain.start("iso")
        domain.isolate(session.id, execute=True)
        worktree = Path(domain.get(session.id).worktree)
        assert domain.current(cwd=worktree).id == session.id

    def test_ambiguous_origin_lists_the_candidates(self, m):
        domain = SessionDomain(m)
        domain.start("one")
        domain.start("two")
        with pytest.raises(UsageError, match="INVARIANT_SESSION_ID"):
            domain.current(cwd=m.root)

    def test_torn_down_sessions_are_not_current(self, m):
        domain = SessionDomain(m)
        session = domain.start("gone")
        session.state = "torn_down"
        domain._save(session)
        assert domain.current(cwd=m.root) is None

    def test_isolation_plan_is_derived_from_the_id(self, m):
        domain = SessionDomain(m)
        session = domain.start("x", session_id="deadbeef")
        plan = domain.isolation_plan(session)
        assert plan["branch"] == "session/deadbeef"
        assert "deadbeef" in plan["worktree"]
        assert not Path(plan["worktree"]).is_relative_to(m.root)

    def test_isolate_without_execute_only_plans(self, m):
        domain = SessionDomain(m)
        session = domain.start("x")
        plan = domain.isolate(session.id, execute=False)
        assert "git worktree add" in plan["command"]
        assert domain.get(session.id).state == "registered"

    def test_isolate_creates_the_worktree_and_advances(self, m):
        domain = SessionDomain(m)
        session = domain.start("x")
        plan = domain.isolate(session.id, execute=True)
        assert Path(plan["worktree"]).is_dir()
        assert domain.get(session.id).state == "isolated"

    def test_illegal_transition_is_refused(self, m):
        domain = SessionDomain(m)
        session = domain.start("x")
        with pytest.raises(TransitionError, match="Legal from 'registered'"):
            domain.transition(session.id, "pushed")

    def test_force_records_an_override(self, m):
        domain = SessionDomain(m)
        session = domain.start("x")
        updated = domain.transition(session.id, "pushed", reason="recovery", force=True)
        assert updated.state == "pushed"
        assert updated.overrides[0]["reason"] == "recovery"

    def test_files_accumulate_without_duplicates(self, m):
        domain = SessionDomain(m)
        session = domain.start("x")
        domain.add_files(session.id, ["a.py", "b.py"])
        assert domain.add_files(session.id, ["a.py", "c.py"]).files == ["a.py", "b.py", "c.py"]

    def test_repair_marks_a_vanished_worktree_abandoned(self, m):
        import shutil

        domain = SessionDomain(m)
        session = domain.start("x")
        domain.isolate(session.id, execute=True)
        shutil.rmtree(domain.get(session.id).worktree)

        actions = domain.repair()
        assert any("abandoned" in a for a in actions)
        # The record survives: it is the evidence of what happened.
        assert domain.get(session.id).state == "abandoned"

    def test_repair_dry_run_changes_nothing(self, m):
        import shutil

        domain = SessionDomain(m)
        session = domain.start("x")
        domain.isolate(session.id, execute=True)
        shutil.rmtree(domain.get(session.id).worktree)
        domain.repair(dry_run=True)
        assert domain.get(session.id).state == "isolated"


# -- pipeline ----------------------------------------------------------


class TestPipelineDomain:
    def setup_pipeline(self, m):
        sessions = SessionDomain(m)
        session = sessions.start("x")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id, task="a task")
        return sessions, pipelines, session

    def test_start_enters_the_initial_state(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        assert pipelines.current(session.id).state == "designing"

    def test_a_second_pipeline_is_refused_while_one_runs(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        with pytest.raises(UsageError, match="already running"):
            pipelines.start(session.id)

    def test_advance_is_blocked_by_a_failing_gate(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        with pytest.raises(GateFailure, match="1 gate"):
            pipelines.advance(session.id, "red", cwd=m.root)

    def test_recorded_evidence_satisfies_the_gate(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        pipelines.record_docs_read(
            session.id, str(m.root / "docs" / "architecture" / "overview.md")
        )
        pipeline, _ = pipelines.advance(session.id, "red", cwd=m.root)
        assert pipeline.state == "red"

    def test_force_without_a_reason_is_refused(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        with pytest.raises(UsageError, match="--force requires --reason"):
            pipelines.advance(session.id, "red", force=True, cwd=m.root)

    def test_force_with_a_reason_records_the_override(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        pipeline, _ = pipelines.advance(
            session.id, "red", force=True, reason="no structural change", cwd=m.root
        )
        assert pipeline.overrides[0]["reason"] == "no structural change"
        assert "docs.read_architecture" in pipeline.overrides[0]["gates"]

    def test_a_structurally_illegal_edge_needs_force_too(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        with pytest.raises(TransitionError):
            pipelines.advance(session.id, "committed", cwd=m.root)

    def test_gates_query_does_not_advance(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        pipelines.evaluate_gates(session.id, target="red", cwd=m.root)
        assert pipelines.current(session.id).state == "designing"

    def test_run_tests_records_what_happened(self, tmp_path):
        m = git_project(
            tmp_path / "proj",
            {"version": 1, "commands": {"test": "echo '2 passed in 0.1s'"},
             "project": {"ecosystems": ["python"]}},
        )
        sessions = SessionDomain(m)
        session = sessions.start("x")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id)
        record = pipelines.run_tests(session.id, phase="red", cwd=m.root)
        assert record["passed"] is True and record["phase"] == "red"
        assert pipelines.current(session.id).evidence["test_runs"][0]["phase"] == "red"

    def test_a_failing_command_is_recorded_as_failing(self, tmp_path):
        m = git_project(
            tmp_path / "proj",
            {"version": 1, "commands": {"test": "exit 1"},
             "project": {"ecosystems": ["python"]}},
        )
        sessions = SessionDomain(m)
        session = sessions.start("x")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id)
        assert pipelines.run_tests(session.id, phase="red", cwd=m.root)["passed"] is False

    def test_docs_read_outside_a_pipeline_is_ignored_not_fatal(self, m):
        sessions = SessionDomain(m)
        session = sessions.start("x")
        PipelineDomain(m).record_docs_read(session.id, "anything.md")  # no raise


class TestSummarise:
    def test_counts_each_category_separately(self, m):
        _, pipelines, session = TestPipelineDomain().setup_pipeline(m)
        verdicts = pipelines.evaluate_gates(session.id, target="red", cwd=m.root)
        text = summarise(verdicts)
        assert "passed" in text

    def test_empty(self):
        assert summarise([]) == "no gates on this transition"


# -- work items --------------------------------------------------------


class TestWorkItemDomain:
    def test_new_item_starts_in_the_initial_state(self, m):
        domain = WorkItemDomain(m)
        assert domain.new("bug", "A defect").status == "filed"
        assert domain.new("idea", "A want").status == "proposed"

    def test_ids_increment_per_type(self, m):
        domain = WorkItemDomain(m)
        assert domain.new("bug", "one").id == "BUG_001"
        assert domain.new("bug", "two").id == "BUG_002"
        assert domain.new("idea", "three").id == "IDEA_001"

    def test_id_prefix_is_applied(self, tmp_path):
        m = git_project(tmp_path / "proj", {"version": 1, "items": {"id_prefix": "X"}})
        assert WorkItemDomain(m).new("bug", "one").id == "XBUG_001"

    def test_a_titleless_item_is_refused(self, m):
        with pytest.raises(UsageError, match="needs a title"):
            WorkItemDomain(m).new("bug", "   ")

    def test_unknown_type(self, m):
        with pytest.raises(UsageError, match="known types"):
            WorkItemDomain(m).new("epic", "x")

    def test_fixing_an_unreproduced_bug_is_blocked(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        with pytest.raises(TransitionError):
            domain.transition(bug.id, "fixed", cwd=m.root)

    def test_the_reproduced_route_is_open(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        domain.transition(bug.id, "reproduced", cwd=m.root)
        item, _ = domain.transition(bug.id, "fixed", cwd=m.root)
        assert item.status == "fixed"

    def test_declining_is_distinct_from_failing_to_reproduce(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        item, _ = domain.transition(bug.id, "wont_fix", reason="by design", cwd=m.root)
        assert item.status == "wont_fix"

    def test_closing_with_residue_warns_but_does_not_block(self, m):
        """Residue matching advises even under `strict`, by design.

        It compares free text against a status, which is a judgement call. A
        blocking heuristic is a false positive with authority, so this one
        reports and lets the human decide.
        """
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        for target in ("reproduced", "fixed", "verified"):
            domain.transition(bug.id, target, cwd=m.root)
        domain.add_residue(bug.id, "a live-environment leg is owed")
        item, verdicts = domain.transition(bug.id, "closed", cwd=m.root)
        assert item.status == "closed"
        residue_verdict = next(
            v for v in verdicts if v.rule_id == "item.residue_matches_status"
        )
        assert residue_verdict.warns and "still owed" in residue_verdict.verdict.message

    def test_a_project_can_promote_the_residue_rule_to_blocking(self, tmp_path):
        """The per-rule override is what makes a shipped default negotiable."""
        m = git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "enforcement": {"rules": {"item.residue_matches_status": "block"}},
            },
        )
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        for target in ("reproduced", "fixed", "verified"):
            domain.transition(bug.id, target, cwd=m.root)
        domain.add_residue(bug.id, "a leg is owed")
        with pytest.raises(GateFailure):
            domain.transition(bug.id, "closed", cwd=m.root)

    def test_clearing_the_residue_unblocks_the_close(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "A defect")
        for target in ("reproduced", "fixed", "verified"):
            domain.transition(bug.id, target, cwd=m.root)
        domain.add_residue(bug.id, "a leg is owed")
        domain.clear_residue(bug.id, "a leg is owed")
        item, _ = domain.transition(bug.id, "closed", cwd=m.root)
        assert item.status == "closed"

    def test_clearing_absent_residue_reports_what_is_there(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "x")
        domain.add_residue(bug.id, "real thing")
        with pytest.raises(UsageError, match="real thing"):
            domain.clear_residue(bug.id, "imaginary thing")

    def test_history_records_each_transition(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "x")
        domain.transition(bug.id, "reproduced", reason="red test", cwd=m.root)
        assert domain.get(bug.id).history[-1]["reason"] == "red test"

    def test_open_filter_excludes_terminal_states(self, m):
        domain = WorkItemDomain(m)
        a = domain.new("bug", "open one")
        b = domain.new("bug", "declined one")
        domain.transition(b.id, "wont_fix", cwd=m.root)
        open_ids = {i.id for i in domain.list(open_only=True)}
        assert a.id in open_ids and b.id not in open_ids

    def test_open_table_marks_owed_work(self, m):
        domain = WorkItemDomain(m)
        bug = domain.new("bug", "x")
        domain.add_residue(bug.id, "a leg is owed")
        assert "+1 owed" in domain.open_table()

    def test_open_table_when_empty(self, m):
        assert WorkItemDomain(m).open_table() == "No open items."


# -- backends ----------------------------------------------------------


class TestBackends:
    def roundtrip(self, backend):
        from invariant.core.items import Item

        item = Item(
            id="BUG_001",
            type="bug",
            title="A defect",
            status="filed",
            fields={"severity": "high", "batch": "P1"},
            residue=["a leg is owed"],
            body="Steps to reproduce.",
        )
        backend.save(item)
        loaded = backend.get("BUG_001")
        assert loaded is not None
        assert loaded.title == "A defect"
        assert loaded.status == "filed"
        assert loaded.fields["severity"] == "high"
        assert loaded.residue == ["a leg is owed"]
        assert "Steps to reproduce" in loaded.body
        return loaded

    def test_markdown_roundtrip(self, tmp_path):
        self.roundtrip(MarkdownBackend(tmp_path / "backlog"))

    def test_json_roundtrip(self, tmp_path):
        self.roundtrip(JsonBackend(tmp_path / "backlog.json"))

    def test_sphinx_needs_roundtrip(self, tmp_path):
        self.roundtrip(SphinxNeedsBackend(tmp_path / "backlog.rst"))

    def test_sphinx_needs_renders_directives(self, tmp_path):
        backend = SphinxNeedsBackend(tmp_path / "backlog.rst")
        self.roundtrip(backend)
        text = (tmp_path / "backlog.rst").read_text(encoding="utf-8")
        assert ".. bug:: A defect" in text
        assert ":id: BUG_001" in text

    def test_sphinx_needs_updates_in_place(self, tmp_path):
        backend = SphinxNeedsBackend(tmp_path / "backlog.rst")
        item = self.roundtrip(backend)
        item.status = "reproduced"
        backend.save(item)
        text = (tmp_path / "backlog.rst").read_text(encoding="utf-8")
        assert text.count(":id: BUG_001") == 1
        assert ":status: reproduced" in text

    def test_sphinx_needs_preserves_surrounding_prose(self, tmp_path):
        path = tmp_path / "backlog.rst"
        path.write_text("Backlog\n=======\n\nIntro prose.\n", encoding="utf-8")
        self.roundtrip(SphinxNeedsBackend(path))
        assert "Intro prose." in path.read_text(encoding="utf-8")

    def test_null_backend_refuses_writes_with_a_reason(self, tmp_path):
        m = git_project(tmp_path / "proj", {"version": 1, "items": {"backend": "none"}})
        with pytest.raises(UsageError, match="disabled"):
            WorkItemDomain(m).new("bug", "x")

    def test_backend_selection_from_the_manifest(self, tmp_path):
        m = git_project(tmp_path / "proj", {"version": 1, "items": {"backend": "json"}})
        assert isinstance(backend_for(m), JsonBackend)

    def test_markdown_skips_unparseable_files(self, tmp_path):
        backend = MarkdownBackend(tmp_path / "backlog")
        self.roundtrip(backend)
        (tmp_path / "backlog" / "junk.md").write_text("no frontmatter", encoding="utf-8")
        assert len(backend.load_all()) == 1


class TestHostSessionAttribution:
    """Attribution must not depend on the working directory.

    A host may reset cwd to the project root between tool calls. When it does,
    every concurrent session looks identical from the main checkout — so
    cwd-based attribution fails precisely when more than one session is live,
    which is the case the isolation rules exist for.
    """

    def three_sessions(self, m):
        domain = SessionDomain(m)
        made = []
        for name, host in (
            ("alpha", "aaaaaaaa-1111-2222-3333-4444cc9185d7"),
            ("beta", "bbbbbbbb-1111-2222-3333-44440615b555"),
            ("gamma", "cccccccc-1111-2222-3333-44441654f8b0"),
        ):
            made.append(domain.start(name, host_session_id=host))
        return domain, made

    def test_cwd_alone_is_ambiguous_with_three_sessions(self, m):
        domain, _ = self.three_sessions(m)
        with pytest.raises(UsageError, match="INVARIANT_SESSION_ID"):
            domain.current(cwd=m.root)

    def test_the_host_identifier_resolves_it(self, m):
        domain, made = self.three_sessions(m)
        found = domain.current(
            cwd=m.root, host_session_id="bbbbbbbb-1111-2222-3333-44440615b555"
        )
        assert found is not None and found.name == "beta"

    def test_resolution_works_from_any_directory(self, m, tmp_path):
        domain, _ = self.three_sessions(m)
        for where in (m.root, tmp_path, Path("/")):
            found = domain.current(
                cwd=where,
                strict=False,
                host_session_id="cccccccc-1111-2222-3333-44441654f8b0",
            )
            assert found is not None and found.name == "gamma", where

    def test_the_short_id_is_the_LAST_eight_alphanumerics(self, m):
        # This project's own tooling has always derived worktree names that
        # way. Taking the first eight would match nothing.
        from invariant.domains.session import host_slug

        assert host_slug("d3e22c15-f3c9-47da-b1e0-955ecc9185d7") == "cc9185d7"
        assert host_slug("") == ""
        assert host_slug(None) == ""

    def test_a_session_registered_before_the_field_existed_still_matches(self, m):
        """No migration: the derived short form is the fallback."""
        domain = SessionDomain(m)
        legacy = domain.start("legacy", session_id="cc9185d7")
        assert legacy.host_session_id is None
        found = domain.current(
            cwd=m.root, host_session_id="d3e22c15-f3c9-47da-b1e0-955ecc9185d7"
        )
        assert found is not None and found.id == "cc9185d7"

    def test_an_unknown_host_id_does_not_match_something_else(self, m):
        domain, _ = self.three_sessions(m)
        assert domain.by_host_session("zzzzzzzz-0000-0000-0000-000000000000") is None

    def test_a_torn_down_session_is_never_matched(self, m):
        domain, made = self.three_sessions(m)
        session = made[0]
        session.state = "torn_down"
        domain._save(session)
        assert domain.by_host_session("aaaaaaaa-1111-2222-3333-4444cc9185d7") is None

    def test_explicit_env_still_outranks_the_host_id(self, m, monkeypatch):
        domain, made = self.three_sessions(m)
        monkeypatch.setenv("INVARIANT_SESSION_ID", made[0].id)
        found = domain.current(
            cwd=m.root, host_session_id="bbbbbbbb-1111-2222-3333-44440615b555"
        )
        assert found.id == made[0].id

    def test_start_derives_the_id_from_the_host_when_not_given(self, m):
        domain = SessionDomain(m)
        session = domain.start("derived", host_session_id="xxxx-1111-2222-abcd1234")
        assert session.id == "abcd1234"

    def test_adopt_binds_a_host_id_after_the_fact(self, m):
        domain = SessionDomain(m)
        session = domain.start("later", session_id="deadbeef")
        domain.adopt_host_session("deadbeef", "ffffffff-0000-0000-0000-000000009999")
        found = domain.current(
            cwd=m.root, host_session_id="ffffffff-0000-0000-0000-000000009999"
        )
        assert found is not None and found.id == "deadbeef"
