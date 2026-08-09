import subprocess
from pathlib import Path

import pytest
import yaml

from invariant.core.errors import GateFailure, StateError, TransitionError, UsageError
from invariant.core.items import (
    JsonBackend,
    MarkdownBackend,
    SphinxNeedsBackend,
    backend_for,
)
from invariant.core.manifest import MANIFEST_RELPATH, Manifest
from invariant.domains.pipeline import PipelineDomain, describe_run, summarise
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

    def test_an_agent_run_is_recorded_as_evidence(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        pipelines.record_agent_run(session.id, "feature-architect")
        assert pipelines.current(session.id).evidence["agents_run"] == [
            "feature-architect"
        ]

    def test_the_same_agent_twice_is_recorded_once(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        pipelines.record_agent_run(session.id, "feature-architect")
        pipelines.record_agent_run(session.id, "feature-architect")
        assert pipelines.current(session.id).evidence["agents_run"] == [
            "feature-architect"
        ]

    def test_an_agent_run_survives_a_reload(self, m):
        """Evidence that is not persisted is not evidence."""
        _, pipelines, session = self.setup_pipeline(m)
        pipelines.record_agent_run(session.id, "compliance-reviewer")
        fresh = PipelineDomain(m)
        assert "compliance-reviewer" in fresh.current(session.id).evidence["agents_run"]

    def test_the_advance_gate_reads_that_evidence(self, m):
        _, pipelines, session = self.setup_pipeline(m)
        pipelines.record_docs_read(
            session.id, str(m.root / "docs" / "architecture" / "overview.md")
        )
        pipelines.record_agent_run(session.id, "feature-architect")
        verdicts = pipelines.evaluate_gates(
            session.id, session=session.to_dict(), cwd=m.root
        )
        design = [v for v in verdicts if v.rule_id == "agents.design_agent_ran"]
        assert design and design[0].verdict.ok

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


class TestAttributionByEditTarget:
    """Who is editing is answered by where the edit lands.

    The host runs hooks from the project root and resets the working directory
    between tool calls, so with several sessions live, cwd identifies nobody.
    An Edit call carries an absolute target path, and that path is inside
    exactly one session's worktree — a fact, not a heuristic.
    """

    def two_sessions(self, m):
        domain = SessionDomain(m)
        ids = []
        for name in ("alpha", "beta"):
            session = domain.start(name)
            domain.isolate(session.id, execute=True)
            ids.append(session.id)
        return domain, domain.get(ids[0]), domain.get(ids[1])

    def test_a_target_inside_a_worktree_names_its_session(self, m):
        domain, a, b = self.two_sessions(m)
        target = Path(a.worktree) / "src" / "thing.swift"
        assert domain.current(m.root, strict=False, target=target).id == a.id

    def test_the_other_worktree_names_the_other_session(self, m):
        domain, a, b = self.two_sessions(m)
        target = Path(b.worktree) / "src" / "thing.swift"
        assert domain.current(m.root, strict=False, target=target).id == b.id

    def test_without_a_target_the_same_call_is_ambiguous(self, m):
        """The blocked case, stated as a test.

        Two live sessions, cwd in the main checkout, no host identifier: this
        is precisely what an Edit call looked like, and it resolved to nobody.
        """
        domain, _, _ = self.two_sessions(m)
        assert domain.current(m.root, strict=False) is None

    def test_a_target_in_the_main_checkout_does_not_invent_a_session(self, m):
        domain, _, _ = self.two_sessions(m)
        target = m.root / "src" / "thing.swift"
        assert domain.current(m.root, strict=False, target=target) is None

    def test_an_explicit_id_still_wins(self, m, monkeypatch):
        domain, a, b = self.two_sessions(m)
        monkeypatch.setenv("INVARIANT_SESSION_ID", b.id)
        target = Path(a.worktree) / "f.swift"
        assert domain.current(m.root, strict=False, target=target).id == b.id

    def test_the_host_identifier_still_outranks_the_target(self, m):
        """Who is calling beats where the bytes land.

        Kept in that order so that a session writing into a peer's worktree is
        still attributed to the writer — which is what makes that a detectable
        violation rather than a silent reattribution.
        """
        domain, a, b = self.two_sessions(m)
        domain.adopt_host_session(b.id, "host-aaaa-bbbb-" + b.id)
        target = Path(a.worktree) / "f.swift"
        resolved = domain.current(
            m.root, strict=False, target=target, host_session_id="host-aaaa-bbbb-" + b.id
        )
        assert resolved.id == b.id

    def test_resolution_reports_how_it_decided(self, m):
        domain, a, _ = self.two_sessions(m)
        assert domain.resolve(m.root, target=Path(a.worktree) / "f").how == "target"
        assert domain.resolve(m.root).how == "ambiguous"

    def test_a_dead_session_does_not_claim_its_old_worktree(self, m):
        domain, a, b = self.two_sessions(m)
        a.state = "abandoned"
        domain._save(a)
        target = Path(a.worktree) / "f.swift"
        assert domain.resolve(m.root, target=target).session != a

    def test_an_unclaimed_target_falls_through_rather_than_stopping(self, m):
        """A target nobody owns is not an answer, so resolution continues.

        This is what keeps an edit to the main checkout attributable: the path
        belongs to no worktree, resolution falls through to the registered
        session, and `session.no_edit_in_main_checkout` — whose job it actually
        is — gets a session to report against instead of abstaining.
        """
        domain = SessionDomain(m)
        session = domain.start("solo")
        domain.isolate(session.id, execute=True)
        resolution = domain.resolve(m.root, target=m.root / "src" / "f.swift")
        assert resolution.session.id == session.id
        assert resolution.how == "origin"



class TestTeardownRemovesTheBranch:
    """A session ends by leaving nothing behind but the merged work."""

    def merged_session(self, m):
        domain = SessionDomain(m)
        session = domain.start("feature")
        domain.isolate(session.id, execute=True)
        session = domain.get(session.id)
        # Land the branch on the base so a safe delete will accept it.
        subprocess.run(
            ["git", "merge", "--ff-only", session.branch],
            cwd=str(m.root), check=True, capture_output=True,
        )
        # Teardown is only legal from `merged`; the transitions themselves are
        # covered elsewhere, and driving them here would test them twice while
        # obscuring what these cases are actually about.
        session.state = "merged"
        domain._save(session)
        return domain, session

    def branches(self, m):
        out = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            cwd=str(m.root), capture_output=True, text=True,
        ).stdout
        return set(out.split())

    def test_the_branch_is_gone_afterwards(self, m):
        domain, session = self.merged_session(m)
        assert session.branch in self.branches(m)
        domain.teardown(session.id)
        assert session.branch not in self.branches(m)

    def test_the_worktree_is_gone_afterwards(self, m):
        domain, session = self.merged_session(m)
        domain.teardown(session.id)
        assert not Path(session.worktree).exists()

    def test_keep_branch_leaves_it(self, m):
        domain, session = self.merged_session(m)
        domain.teardown(session.id, remove_branch=False)
        assert session.branch in self.branches(m)

    def test_an_unmerged_branch_is_refused_rather_than_forced(self, m):
        """The refusal is the signal, not an obstacle to route around.

        Reaching teardown is supposed to mean the work is merged. A branch that
        will not delete safely is evidence the merge did not do what it looked
        like it did — and forcing it would destroy the only copy and report
        success.
        """
        domain = SessionDomain(m)
        session = domain.start("unmerged")
        domain.isolate(session.id, execute=True)
        session = domain.get(session.id)
        worktree = Path(session.worktree)
        (worktree / "new.txt").write_text("work\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=str(worktree), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-qm", "unmerged work"],
            cwd=str(worktree), check=True, capture_output=True,
        )

        session.state = "merged"  # claimed, but the branch says otherwise
        domain._save(session)

        with pytest.raises(StateError, match="not merged"):
            domain.teardown(session.id)
        assert session.branch in self.branches(m)

    def test_a_session_that_never_isolated_cannot_be_torn_down(self, m):
        """It has nothing to tear down; the honest ending is `abandoned`.

        Written the other way round first, which was wrong: teardown means
        "the merged work is in and the scaffolding is gone", and a session
        that never had scaffolding never got there.
        """
        domain = SessionDomain(m)
        session = domain.start("never-isolated")
        with pytest.raises(TransitionError):
            domain.teardown(session.id)



class TestHostBindingIsOneToOne:
    """A host session and an invariant session are the same unit of work.

    They used to be independent namespaces, which meant an ambiguity report
    could offer six candidates to a caller for whom five were provably wrong.
    """

    def test_a_host_may_not_own_two_sessions(self, m):
        domain = SessionDomain(m)
        domain.start("first", host_session_id="host-aaaa-1111")
        with pytest.raises(UsageError, match="already owns session"):
            domain.start("second", host_session_id="host-aaaa-1111")

    def test_the_refusal_names_the_session_and_how_to_end_it(self, m):
        domain = SessionDomain(m)
        first = domain.start("first", host_session_id="host-aaaa-1111")
        with pytest.raises(UsageError) as exc:
            domain.start("second", host_session_id="host-aaaa-1111")
        assert first.id in str(exc.value)
        assert "teardown" in str(exc.value)

    def test_a_finished_session_frees_the_host(self, m):
        """1:1 is about LIVE sessions; a host outlives the work it did."""
        domain = SessionDomain(m)
        first = domain.start("first", host_session_id="host-aaaa-1111")
        first.state = "torn_down"
        domain._save(first)
        assert domain.start("second", host_session_id="host-aaaa-1111")

    def test_rebinding_to_another_host_is_refused(self, m):
        domain = SessionDomain(m)
        session = domain.start("work", host_session_id="host-aaaa-1111")
        with pytest.raises(UsageError, match="already bound"):
            domain.adopt_host_session(session.id, "host-bbbb-2222")

    def test_rebinding_is_possible_deliberately(self, m):
        domain = SessionDomain(m)
        session = domain.start("work", host_session_id="host-aaaa-1111")
        updated = domain.adopt_host_session(session.id, "host-bbbb-2222", force=True)
        assert updated.host_session_id == "host-bbbb-2222"

    def test_adopting_is_refused_when_the_host_already_owns_one(self, m):
        domain = SessionDomain(m)
        domain.start("owned", host_session_id="host-aaaa-1111")
        other = domain.start("unowned")
        with pytest.raises(UsageError, match="already owns"):
            domain.adopt_host_session(other.id, "host-aaaa-1111")

    def test_readopting_the_same_pair_is_not_an_error(self, m):
        domain = SessionDomain(m)
        session = domain.start("work", host_session_id="host-aaaa-1111")
        assert domain.adopt_host_session(session.id, "host-aaaa-1111").id == session.id

    def test_the_short_form_only_claims_an_unbound_session(self, m):
        """Matching by derived id is a fallback, not an override.

        A session already answering to another host that happens to share the
        derived short form is a naming coincidence, not a match.
        """
        domain = SessionDomain(m)
        session = domain.start("x", session_id="abcd1234")
        domain.adopt_host_session(session.id, "host-zzzz-0000abcd1234", force=True)
        assert domain.by_host_session("other-host-0000abcd1234") is None


class TestAttributionCandidates:
    """Who could plausibly be calling. Every exclusion removes a wrong answer."""

    def test_a_stranded_session_stops_competing(self, m):
        import shutil

        domain = SessionDomain(m)
        session = domain.start("gone")
        domain.isolate(session.id, execute=True)
        shutil.rmtree(domain.get(session.id).worktree)

        assert domain.get(session.id).is_stranded
        assert session.id not in [s.id for s in domain.attributable()]

    def test_a_session_that_never_isolated_is_not_stranded(self, m):
        """The deliberate no-worktree case: a soak owning no code.

        The binding is host-to-session, never host-to-worktree, so having no
        worktree must not read as having lost one.
        """
        domain = SessionDomain(m)
        session = domain.start("device-soak")
        assert not session.is_stranded
        assert session.id in [s.id for s in domain.attributable()]

    def test_a_session_whose_host_ended_stops_competing(self, m):
        domain = SessionDomain(m)
        session = domain.start("over", host_session_id="host-aaaa-1111")
        domain.end_host_session("host-aaaa-1111")
        assert session.id not in [s.id for s in domain.attributable()]

    def test_ending_a_host_does_not_destroy_the_work(self, m):
        """A closed terminal is not a reason to abandon unmerged work."""
        domain = SessionDomain(m)
        session = domain.start("over", host_session_id="host-aaaa-1111")
        domain.isolate(session.id, execute=True)
        domain.end_host_session("host-aaaa-1111")

        after = domain.get(session.id)
        assert after.state == "isolated"
        assert Path(after.worktree).exists()
        assert after.host_ended

    def test_a_session_bound_elsewhere_stops_competing(self, m):
        domain = SessionDomain(m)
        theirs = domain.start("theirs", host_session_id="host-bbbb-2222")
        mine = domain.start("mine")
        visible = [s.id for s in domain.attributable("host-aaaa-1111")]
        assert theirs.id not in visible
        assert mine.id in visible

    def test_an_ended_host_cannot_reclaim_its_old_session(self, m):
        """A new host reusing the record would inherit somebody else's work."""
        domain = SessionDomain(m)
        domain.start("over", host_session_id="host-aaaa-1111")
        domain.end_host_session("host-aaaa-1111")
        assert domain.by_host_session("host-aaaa-1111") is None


class TestReapStranded:
    def test_a_worktree_and_branch_both_gone_is_reaped(self, m):
        import shutil

        domain = SessionDomain(m)
        session = domain.start("dead")
        domain.isolate(session.id, execute=True)
        session = domain.get(session.id)
        shutil.rmtree(session.worktree)
        subprocess.run(
            ["git", "worktree", "prune"], cwd=str(m.root), check=True, capture_output=True
        )
        subprocess.run(
            ["git", "branch", "-D", session.branch],
            cwd=str(m.root), check=True, capture_output=True,
        )

        assert [s.id for s in domain.reap_stranded()] == [session.id]
        assert domain.get(session.id).state == "abandoned"

    def test_a_surviving_branch_is_not_reaped(self, m):
        """The narrow condition, and why it is narrow.

        A worktree can be removed while its branch still holds unmerged
        commits. That work is recoverable, so a missing directory alone must
        not be enough to write the session off.
        """
        import shutil

        domain = SessionDomain(m)
        session = domain.start("recoverable")
        domain.isolate(session.id, execute=True)
        shutil.rmtree(domain.get(session.id).worktree)

        assert domain.reap_stranded() == []
        assert domain.get(session.id).state == "isolated"

    def test_dry_run_changes_nothing(self, m):
        import shutil

        domain = SessionDomain(m)
        session = domain.start("dead")
        domain.isolate(session.id, execute=True)
        session = domain.get(session.id)
        shutil.rmtree(session.worktree)
        subprocess.run(["git", "worktree", "prune"], cwd=str(m.root), capture_output=True)
        subprocess.run(
            ["git", "branch", "-D", session.branch], cwd=str(m.root), capture_output=True
        )

        assert [s.id for s in domain.reap_stranded(dry_run=True)] == [session.id]
        assert domain.get(session.id).state == "isolated"

    def test_a_live_session_is_never_reaped(self, m):
        domain = SessionDomain(m)
        session = domain.start("alive")
        domain.isolate(session.id, execute=True)
        assert domain.reap_stranded() == []



class TestAcknowledgingAnAdvisoryGate:
    """A warn that repeats forever cannot distinguish two very different states.

    "Considered and declined" and "never read" produce identical output, which
    is the real defect — more than the repetition is. An acknowledgement costs
    an explicit act with a stated reason and leaves that reason in the record.
    """

    def pipeline_at(self, m):
        sessions = SessionDomain(m)
        session = sessions.start("x")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id, task="a task")
        return pipelines, session

    def verdict_for(self, pipelines, session, m, rule_id):
        verdicts = pipelines.evaluate_gates(session.id, cwd=m.root)
        for evaluated in verdicts:
            if evaluated.rule_id == rule_id:
                return evaluated
        raise AssertionError(f"{rule_id} was not among the gates here")

    def test_the_gate_warns_before_it_is_acknowledged(self, m):
        pipelines, session = self.pipeline_at(m)
        evaluated = self.verdict_for(
            pipelines, session, m, "agents.design_agent_ran"
        )
        assert not evaluated.verdict.ok

    def test_acknowledging_turns_it_into_a_recorded_skip(self, m):
        pipelines, session = self.pipeline_at(m)
        pipelines.acknowledge(
            session.id, "agents.design_agent_ran", "one-line fix; no design content"
        )
        evaluated = self.verdict_for(
            pipelines, session, m, "agents.design_agent_ran"
        )
        # Skipped, not passed. The gate did not become satisfied; it became
        # answered.
        assert evaluated.verdict.skipped
        assert "one-line fix" in evaluated.verdict.message

    def test_the_reason_is_kept_in_the_record(self, m):
        pipelines, session = self.pipeline_at(m)
        pipelines.acknowledge(session.id, "agents.design_agent_ran", "deliberate")
        evidence = pipelines.current(session.id).evidence
        assert evidence["acknowledged"]["agents.design_agent_ran"] == "deliberate"

    def test_it_survives_a_reload(self, m):
        pipelines, session = self.pipeline_at(m)
        pipelines.acknowledge(session.id, "agents.design_agent_ran", "deliberate")
        fresh = PipelineDomain(m)
        evaluated = self.verdict_for(fresh, session, m, "agents.design_agent_ran")
        assert evaluated.verdict.skipped

    def test_a_reason_is_required(self, m):
        pipelines, session = self.pipeline_at(m)
        with pytest.raises(UsageError, match="silencer"):
            pipelines.acknowledge(session.id, "agents.design_agent_ran", "   ")

    def test_a_blocking_gate_cannot_be_acknowledged(self, m):
        """Otherwise block and warn are the same level, spelled differently.

        A blocking gate is satisfied, or overridden on the transition itself
        where the override is recorded against that transition rather than
        standing for the rest of the run.
        """
        pipelines, session = self.pipeline_at(m)
        with pytest.raises(UsageError, match="blocks in this project"):
            pipelines.acknowledge(session.id, "docs.read_architecture", "later")

    def test_the_refusal_points_at_the_recorded_override_instead(self, m):
        pipelines, session = self.pipeline_at(m)
        with pytest.raises(UsageError) as exc:
            pipelines.acknowledge(session.id, "docs.read_architecture", "later")
        assert "--force --reason" in str(exc.value)

    def test_an_unknown_rule_is_refused(self, m):
        pipelines, session = self.pipeline_at(m)
        with pytest.raises(UsageError, match="no rule"):
            pipelines.acknowledge(session.id, "agents.no_such_rule", "why")

    def test_acknowledging_does_not_affect_other_gates(self, m):
        pipelines, session = self.pipeline_at(m)
        pipelines.acknowledge(session.id, "agents.design_agent_ran", "deliberate")
        other = self.verdict_for(pipelines, session, m, "docs.read_architecture")
        assert not other.verdict.ok

    def test_it_is_scoped_to_one_pipeline(self, m):
        """It must not quietly become project policy."""
        pipelines, session = self.pipeline_at(m)
        pipelines.acknowledge(session.id, "agents.design_agent_ran", "deliberate")

        sessions = SessionDomain(m)
        other = sessions.start("second")
        pipelines.start(other.id, task="another task")
        evaluated = self.verdict_for(pipelines, other, m, "agents.design_agent_ran")
        assert not evaluated.verdict.ok

    def test_a_passing_gate_is_unaffected_by_an_acknowledgement(self, m):
        """It rewrites failures only — never manufactures a skip over a pass."""
        pipelines, session = self.pipeline_at(m)
        pipelines.record_agent_run(session.id, "feature-architect")
        pipelines.acknowledge(session.id, "agents.design_agent_ran", "belt and braces")
        evaluated = self.verdict_for(pipelines, session, m, "agents.design_agent_ran")
        assert evaluated.verdict.ok and not evaluated.verdict.skipped



class TestCoverageFromARedSuite:
    """Whether a measurement can be trusted depends on whether the suite RAN.

    Not on whether it passed. Those are different claims, and in a repository
    whose main branch is not green the first is the steady state.
    """

    def pipeline_with(self, m, script):
        """A project whose 'coverage' command is a script we control."""
        path = m.root / "fake-coverage.sh"
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        (m.root / MANIFEST_RELPATH).write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "project": {"name": "demo", "ecosystems": ["swift"]},
                    "commands": {"coverage": f"sh {path}"},
                }
            ),
            encoding="utf-8",
        )
        fresh = Manifest.load(m.root)
        sessions = SessionDomain(fresh)
        session = sessions.start("x")
        pipelines = PipelineDomain(fresh)
        pipelines.start(session.id, task="t")
        return pipelines, session

    RED_RUN_WITH_REPORT = """
echo "Test Suite 'All tests' failed at 2026-08-09."
echo "\t Executed 6 tests, with 4 failures (0 unexpected) in 6.5 (6.5) seconds"
echo "    /repo/Sources/App/Store.swift    100.00% (55/55)"
exit 65
"""

    def test_a_failing_suite_still_yields_a_measurement(self, m):
        pipelines, session = self.pipeline_with(m, self.RED_RUN_WITH_REPORT)
        result = pipelines.run_coverage(session.id, cwd=m.root)
        assert result["files"] == 1
        assert result["suite_passed"] is False

    def test_the_suite_state_is_recorded_with_the_numbers(self, m):
        """100% measured on a red run is not the same fact as on a green one."""
        pipelines, session = self.pipeline_with(m, self.RED_RUN_WITH_REPORT)
        pipelines.run_coverage(session.id, cwd=m.root)
        run = pipelines.current(session.id).evidence["coverage_run"]
        assert run["executed"] == 6
        assert run["failed"] == 4
        assert run["suite_passed"] is False

    def test_a_run_that_executed_nothing_is_refused(self, m):
        """A build failure produces a report describing the previous world."""
        script = """
echo "error: build failed"
echo "    /repo/Sources/App/Store.swift    100.00% (55/55)"
exit 65
"""
        pipelines, session = self.pipeline_with(m, script)
        with pytest.raises(UsageError, match="executed no tests"):
            pipelines.run_coverage(session.id, cwd=m.root)

    def test_nothing_is_recorded_when_the_run_is_refused(self, m):
        script = """
echo "error: build failed"
echo "    /repo/Sources/App/Store.swift    100.00% (55/55)"
exit 65
"""
        pipelines, session = self.pipeline_with(m, script)
        with pytest.raises(UsageError):
            pipelines.run_coverage(session.id, cwd=m.root)
        assert "coverage" not in pipelines.current(session.id).evidence

    def test_a_run_with_tests_but_no_report_is_still_refused(self, m):
        script = """
echo "Test Suite 'All tests' failed at 2026-08-09."
echo "\t Executed 6 tests, with 4 failures (0 unexpected) in 6.5 (6.5) seconds"
echo "xccov: error: could not read bundle"
exit 65
"""
        pipelines, session = self.pipeline_with(m, script)
        with pytest.raises(UsageError, match="no per-file measurements"):
            pipelines.run_coverage(session.id, cwd=m.root)

    def test_a_green_run_records_normally(self, m):
        script = """
echo "Test Suite 'All tests' passed at 2026-08-09."
echo "\t Executed 6 tests, with 0 failures (0 unexpected) in 6.5 (6.5) seconds"
echo "    /repo/Sources/App/Store.swift    92.00% (46/50)"
exit 0
"""
        pipelines, session = self.pipeline_with(m, script)
        result = pipelines.run_coverage(session.id, cwd=m.root)
        assert result["suite_passed"] is True
        assert result["coverage"]["/repo/Sources/App/Store.swift"] == 92.0

    def test_a_stale_bundle_cannot_be_reported_as_a_fresh_measurement(self, m):
        """The bundle is removed before each run, so a failed run cannot
        silently republish the previous run's numbers."""
        pipelines, session = self.pipeline_with(m, self.RED_RUN_WITH_REPORT)
        pipelines.run_coverage(session.id, cwd=m.root)

        bundle = Path(pipelines.store.root) / "coverage" / f"{session.id}.xcresult"
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "marker").write_text("stale", encoding="utf-8")

        pipelines.run_coverage(session.id, cwd=m.root)
        assert not (bundle / "marker").exists()


class TestDescribeRun:
    """Two numbers in one line must not be counted in different things.

    `executed=14 failed=6` sent a reader hunting for six failing tests among
    fourteen. There were two; the six were assertions, because one test tripped
    four asserts after its first failed.
    """

    def test_assertions_and_tests_are_named_apart(self):
        line = describe_run({"executed": 14, "failed": 6, "failed_tests": 2})
        assert "6 assertion(s) failed in 2 test(s)" in line
        assert "14 test(s) executed" in line

    def test_a_single_number_is_not_padded_with_a_second_unit(self):
        line = describe_run({"executed": 14, "failed": 2, "failed_tests": 2})
        assert "2 failure(s)" in line
        assert "assertion" not in line

    def test_a_green_run_says_no_failures(self):
        assert "no failures" in describe_run(
            {"executed": 14, "failed": 0, "failed_tests": None}
        )

    def test_an_unknown_count_says_unknown_not_zero(self):
        """`failed: None` means the run failed and the count could not be
        parsed; rendering it as a number would invent one."""
        line = describe_run({"executed": 13945, "failed": None, "failed_tests": None})
        assert "unknown" in line
        assert "0" not in line.split(",")[1]

    def test_a_missing_executed_count_is_marked_rather_than_guessed(self):
        assert "? test(s) executed" in describe_run(
            {"executed": None, "failed": 0, "failed_tests": None}
        )
