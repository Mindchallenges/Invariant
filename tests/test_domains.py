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
from invariant.core.manifest import MANIFEST_RELPATH, Level, Manifest
from invariant.core.rules import Evaluated, Verdict
from invariant.domains.pipeline import PipelineDomain, describe_run, summarise
from invariant.domains.session import Session, SessionDomain
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

    def test_worktree_dir_places_the_worktree_inside_the_repository(self, tmp_path):
        """Some hosts trust exactly one location and confirm every other.

        The default deliberately sits beside the repository; a project that
        needs the worktree somewhere specific must be able to say so without
        the location being smuggled into the name via worktree_prefix.
        """
        m = git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"name": "demo"},
                "session": {"worktree_dir": ".claude/worktrees"},
            },
        )
        domain = SessionDomain(m)
        plan = domain.isolation_plan(domain.start("x", session_id="deadbeef"))
        assert Path(plan["worktree"]) == m.root / ".claude" / "worktrees" / "proj-wt-deadbeef"

    def test_an_absolute_worktree_dir_is_used_as_given(self, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        m = git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"name": "demo"},
                "session": {"worktree_dir": str(elsewhere)},
            },
        )
        domain = SessionDomain(m)
        plan = domain.isolation_plan(domain.start("x", session_id="deadbeef"))
        assert Path(plan["worktree"]).parent == elsewhere

    def test_isolate_creates_a_configured_directory_that_does_not_exist_yet(self, tmp_path):
        """The first session of a fresh checkout must not fail on a missing dir.

        No code here creates it: `git worktree add` makes the leading
        directories itself. Asserted rather than assumed, because the opposite
        would break every project that configures a directory, and only on the
        very first session — the least convenient moment to discover it.
        """
        m = git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"name": "demo"},
                "session": {"worktree_dir": ".claude/worktrees"},
            },
        )
        domain = SessionDomain(m)
        session = domain.start("x")
        assert not (m.root / ".claude" / "worktrees").exists()
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


class TestSessionGatesOnTheGenericTransitionPath:
    """AUD-05: ``_advance`` validated only the edge.

    The entry and exit gates in ``session.yaml`` were read by exactly two
    hardcoded call sites (``session review``, ``session merge``), so the
    generic ``session transition`` walked the whole lifecycle with zero gates
    — and ``session.named_after_isolating`` was a gate no code path evaluated
    at all (AUD-14). Every route now runs through the machine's gates inside
    ``_advance``, the same way ``PipelineDomain.advance`` always has.
    """

    def strict_project(self, tmp_path):
        return git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"name": "demo"},
                "enforcement": {"profile": "strict"},
            },
        )

    def test_entry_gates_gate_the_generic_transition(self, tmp_path):
        """The known positive: legal edge, no pipeline, must not advance."""
        m = self.strict_project(tmp_path)
        domain = SessionDomain(m)
        session = domain.start("x")
        domain.isolate(session.id, execute=True)
        domain.transition(session.id, "working")

        with pytest.raises(GateFailure) as exc:
            domain.transition(session.id, "reviewed")
        assert any(
            v.rule_id == "pipeline.complete" and v.blocks for v in exc.value.verdicts
        )
        assert domain.get(session.id).state == "working"

    def test_a_forced_transition_records_the_gate_override(self, tmp_path):
        m = self.strict_project(tmp_path)
        domain = SessionDomain(m)
        session = domain.start("x")
        domain.isolate(session.id, execute=True)
        domain.transition(session.id, "working")

        domain.transition(session.id, "reviewed", reason="fixture", force=True)
        updated = domain.get(session.id)
        assert updated.state == "reviewed"
        override = updated.overrides[-1]
        assert "pipeline.complete" in override.get("gates", [])
        assert override["reason"] == "fixture"

    def test_a_session_named_after_its_branch_is_refused_isolation(self, tmp_path):
        """``session.named_after_isolating`` is live, with a failing input.

        The record is written the way earlier tooling wrote it — named after
        its branch rather than its task — which ``start`` itself can no longer
        produce. The gate exists for exactly these records, and until AUD-05
        no code path ever evaluated it (AUD-14).
        """
        m = self.strict_project(tmp_path)
        domain = SessionDomain(m)
        session = domain.start("placeholder")
        session.name = "session/deadbeef"
        domain._save(session)

        with pytest.raises(GateFailure) as exc:
            domain.transition(session.id, "isolated")
        assert any(
            v.rule_id == "session.named_after_isolating" and v.blocks
            for v in exc.value.verdicts
        )
        assert domain.get(session.id).state == "registered"

    def test_push_enabled_is_read_from_the_machine(self, m):
        """`pushed`'s entry gate holds on the generic path, not only in the CLI.

        The standard profile blocks on ``session.push_enabled`` and this
        project has publishing off, so walking ``transition pushed`` by hand
        must refuse — it used to succeed, because the setting lived in a
        hardcoded ``if`` inside ``cmd_session_push`` that the generic
        transition never passed through (AUD-05).
        """
        domain = SessionDomain(m)
        session = domain.start("x")
        session.state = "merged"
        domain._save(session)

        with pytest.raises(GateFailure) as exc:
            domain.transition(session.id, "pushed")
        assert any(
            v.rule_id == "session.push_enabled" and v.blocks
            for v in exc.value.verdicts
        )
        assert domain.get(session.id).state == "merged"


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
        # The agent-role gates live on the review state's exit, because a
        # dispatch is recorded when the agent RETURNS (ADR-003 D1).
        pipeline = pipelines.current(session.id)
        pipeline.state = "reviewing"
        pipelines._save(session.id, pipeline)
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


class TestTheMethodologyIsPinnedToThePipeline:
    """`pipeline start --methodology` must not wedge the pipeline it starts.

    The override was persisted in the record and then ignored: every later
    domain instance loaded the manifest-default machine and asked it about a
    state it does not have, which wedged `status`, `gates` and `advance` — and,
    through the hook's fail-open path, turned all enforcement off (AUD-02).
    Pinning the machine to the RECORD also closes AUD-03: editing
    `methodology.pipeline` mid-pipeline no longer swaps the running pipeline's
    gate set, because the record answers, not the manifest.
    """

    def start_chore(self, m):
        sessions = SessionDomain(m)
        session = sessions.start("x")
        PipelineDomain(m).start(session.id, methodology="chore", task="t")
        return session

    def test_an_override_survives_a_reload(self, m):
        """The known positive: every one of these raised before the fix.

        The manifest is RE-loaded, as every later CLI invocation loads it —
        the old in-memory swap survived only within the process that ran
        `start`, which is exactly why the wedge escaped the domain tests.
        """
        session = self.start_chore(m)
        fresh = PipelineDomain(Manifest.load(m.root))
        pipeline = fresh.current(session.id)
        assert pipeline.state == "doing"
        fresh.evaluate_gates(session.id, cwd=m.root)  # must not raise
        advanced, _ = fresh.advance(session.id, "reviewing", cwd=m.root)
        assert advanced.state == "reviewing"

    def test_machine_for_resolves_the_persisted_methodology(self, m):
        session = self.start_chore(m)
        fresh = PipelineDomain(Manifest.load(m.root))
        assert fresh.machine_for(fresh.current(session.id)).id == "chore"

    def test_the_override_does_not_rewrite_the_shared_manifest(self, m):
        """The old in-memory swap mutated the manifest object it was handed."""
        self.start_chore(m)
        assert m.get("methodology.pipeline") == "tdd"

    def test_a_running_override_pipeline_still_refuses_a_second(self, m):
        session = self.start_chore(m)
        with pytest.raises(UsageError, match="already running"):
            PipelineDomain(Manifest.load(m.root)).start(session.id)

    def test_editing_the_manifest_mid_pipeline_does_not_swap_the_gate_set(self, m):
        """AUD-03: one exempt manifest line must not silently retune the gates."""
        sessions = SessionDomain(m)
        session = sessions.start("y")
        PipelineDomain(m).start(session.id, task="t")  # records tdd

        path = m.root / MANIFEST_RELPATH
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
        body.setdefault("methodology", {})["pipeline"] = "docs"
        path.write_text(yaml.safe_dump(body), encoding="utf-8")

        fresh = PipelineDomain(Manifest.load(m.root))
        pipeline = fresh.current(session.id)
        assert fresh.machine_for(pipeline).id == "tdd"
        verdicts = fresh.evaluate_gates(session.id, target="red", cwd=m.root)
        assert "docs.read_architecture" in {v.rule_id for v in verdicts}

    def test_a_pipeline_whose_machine_is_gone_can_be_replaced(self, tmp_path):
        """Losing the machine file must not wedge `start` forever.

        Every other operation on such a pipeline honestly refuses (the machine
        is unloadable), so starting a fresh pipeline — which archives the old
        record rather than discarding it — is the one route out.
        """
        machine_dir = tmp_path / "proj" / "machines"
        m = git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"name": "demo"},
                "methodology": {"extra_machines": ["machines"]},
            },
        )
        machine_dir.mkdir()
        (machine_dir / "local.yaml").write_text(
            "id: local\nkind: pipeline\ninitial: first\nterminal: [done]\n"
            "states:\n  first:\n    next: [done]\n  done: {}\n",
            encoding="utf-8",
        )
        sessions = SessionDomain(m)
        session = sessions.start("x")
        PipelineDomain(m).start(session.id, methodology="local")
        (machine_dir / "local.yaml").unlink()

        fresh = PipelineDomain(m)
        replacement = fresh.start(session.id, task="recovered")
        assert replacement.methodology == "tdd"
        assert fresh.history(session.id)[-1]["methodology"] == "local"


class TestSummarise:
    def test_counts_each_category_separately(self, m):
        _, pipelines, session = TestPipelineDomain().setup_pipeline(m)
        verdicts = pipelines.evaluate_gates(session.id, target="red", cwd=m.root)
        text = summarise(verdicts)
        assert "passed" in text

    def test_empty(self):
        assert summarise([]) == "no gates on this transition"

    def test_a_skipped_blocking_gate_is_counted_apart_from_a_skipped_warning(self):
        """BUG_020: "1 not applicable" hid which gate the project enforces.

        A warn-level gate going inert is advisory noise; a block-level one
        means a gate the project chose to enforce did not run. Collapsing both
        into one count makes the second unreportable.
        """
        text = summarise(
            [
                Evaluated("a", Level.BLOCK, Verdict.na()),
                Evaluated("b", Level.WARN, Verdict.na()),
            ]
        )
        assert "2 not applicable" in text
        assert "1 blocking" in text

    def test_only_warning_skips_do_not_claim_a_blocking_one(self):
        text = summarise([Evaluated("b", Level.WARN, Verdict.na())])
        assert "1 not applicable" in text
        assert "blocking" not in text


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



def branch_names(m) -> set[str]:
    """The branches that exist in the project's main checkout."""
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=str(m.root), capture_output=True, text=True,
    ).stdout
    return set(out.split())


def merged_session(m, *, name="feature", host_session_id=None, host_ended=False):
    """An isolated session whose branch is already on the base.

    Teardown is only legal from ``merged`` and its safe branch delete refuses
    an unmerged branch, so both are arranged here: a case about a *different*
    precondition must not be masked by one of these two.
    """
    domain = SessionDomain(m)
    session = domain.start(name)
    domain.isolate(session.id, execute=True)
    session = domain.get(session.id)
    subprocess.run(
        ["git", "merge", "--ff-only", session.branch],
        cwd=str(m.root), check=True, capture_output=True,
    )
    # Set rather than adopted: `adopt_host_session` is one-to-one and would
    # refuse the second session in a test that wants two, and `host_ended` is
    # not reachable through it at all.
    session.host_session_id = host_session_id
    session.host_ended = host_ended
    # The transitions themselves are covered elsewhere, and driving them here
    # would test them twice while obscuring what these cases are about.
    session.state = "merged"
    domain._save(session)
    return domain, session


class TestTeardownRemovesTheBranch:
    """A session ends by leaving nothing behind but the merged work."""

    def test_the_branch_is_gone_afterwards(self, m):
        domain, session = merged_session(m)
        assert session.branch in branch_names(m)
        domain.teardown(session.id)
        assert session.branch not in branch_names(m)

    def test_the_worktree_is_gone_afterwards(self, m):
        domain, session = merged_session(m)
        domain.teardown(session.id)
        assert not Path(session.worktree).exists()

    def test_keep_branch_leaves_it(self, m):
        domain, session = merged_session(m)
        domain.teardown(session.id, remove_branch=False)
        assert session.branch in branch_names(m)

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
        assert session.branch in branch_names(m)

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


class TestTeardownAndALiveHostBinding:
    """Teardown's preconditions used to ask git and never who was standing there.

    BUG_009. The binding is now read before anything is removed, and removing
    an *occupied* worktree — one the caller is standing in — while the binding
    that would be stranded is still live is refused outright.

    The refusal is a conjunction, and both halves are load-bearing:

    * **The binding is live.** A spent binding (`SessionEnd` fired) or none at
      all leaves nobody to wedge; a caller in a deleted directory then recovers
      with a `cd`, which is an inconvenience rather than a deadlock.
    * **The caller is standing in it.** This is the only evidence of occupancy
      the library has — the binding is a scalar with no notion of where its
      host stands (BUG_010) — and without this half the check refuses every
      healthy teardown, because a session torn down normally is bound to the
      live host session that is doing the tearing down.
    """

    def test_teardown_does_not_consult_a_live_host_binding(self, m):
        """Repro for BUG_009 — see docs/backlog/BUG_009.md.

        The reported symptom is a WEDGED host session, which is environment
        state and not a return value: the host's own worktree isolation then
        refuses every shell call and orders the caller back into a directory
        that no longer exists, and the repair commands sit behind that same
        refusal. That deadlock is not expressible here and this test does not
        attempt it. What is expressible is the missing precondition underneath
        it, which is what `docs/architecture/state_model.md` "Host binding"
        makes checkable: `SessionEnd` marks the binding spent, so a session
        carrying a `host_session_id` with `host_ended` still False is a
        binding that is, by the documented model, LIVE.

        The entry's contract: "Removing a directory another system holds a
        live reference to should not be a silent success."

        Deliberately NOT asserting the suspected cause's shape, and
        deliberately NOT asserting any particular reaction. Whether teardown
        should REFUSE or merely WARN is an open design decision, and a test
        that hardcoded one would foreclose the other. So this pins only that
        teardown never CONSULTS the binding at all, by measuring reads of the
        two binding fields that happen while the worktree is still on disk —
        that is, in time to be a precondition.

        The ordering discriminator is load-bearing rather than decorative.
        Teardown does read `host_session_id` afterwards, twice for the
        transition's telemetry watermark and once more when the record is
        serialised, so a naive "was it read at all" spy reports a consultation
        that never happened. Reads after the directory is already gone cannot
        be a precondition on removing it.
        """
        domain = SessionDomain(m)
        session = domain.start("host-occupied")
        domain.isolate(session.id, execute=True)
        session = domain.get(session.id)
        # Land the branch on the base so the safe `git branch -d` accepts it:
        # an unmerged branch is refused by a precondition that DOES exist, and
        # that refusal would mask the absence of this one.
        subprocess.run(
            ["git", "merge", "--ff-only", session.branch],
            cwd=str(m.root), check=True, capture_output=True,
        )
        domain.adopt_host_session(session.id, "host-0000-1111-2222")
        session = domain.get(session.id)
        session.state = "merged"  # teardown is only legal from `merged`
        domain._save(session)

        session = domain.get(session.id)
        assert session.host_session_id == "host-0000-1111-2222"
        assert session.host_ended is False, "SessionEnd never fired: the binding is live"
        worktree = Path(session.worktree)
        assert worktree.is_dir()

        # Record every read of the binding, tagged with whether the directory
        # still existed at that moment. `require()` rebuilds the Session from
        # the store, so the spy has to sit on the class, not on this instance.
        reads: list[tuple[str, bool]] = []
        unspied = Session.__getattribute__

        def spy(self, name):
            if name in {"host_session_id", "host_ended"}:
                try:
                    reads.append((name, worktree.exists()))
                except OSError:
                    reads.append((name, False))
            return unspied(self, name)

        Session.__getattribute__ = spy
        try:
            refusal = None
            try:
                domain.teardown(session.id)
            except StateError as exc:
                refusal = str(exc)
        finally:
            Session.__getattribute__ = unspied

        consulted = [name for name, still_there in reads if still_there]
        destroyed = not worktree.exists()

        # One condition, satisfiable by either fix. A refusal leaves the
        # worktree standing and passes on `destroyed`; a warning removes it but
        # has to read the binding first and passes on `consulted`. Today
        # neither holds: the directory is gone and nothing ever asked.
        assert not (destroyed and not consulted), (
            "teardown removed the worktree of a session whose host binding is "
            f"still live, without ever consulting it. Reads of the binding "
            f"while the directory still existed: {consulted}; reads after it "
            f"was already gone (telemetry and serialisation, too late to be a "
            f"precondition): {[n for n, s in reads if not s]}. Refusal raised: "
            f"{refusal!r}."
        )

    # -- the wedge itself: occupied, and the binding still live -------------

    def test_removing_an_occupied_worktree_is_refused(self, m):
        """The one case with no in-band recovery, so it is a refusal.

        Standing in the directory *is* the occupancy evidence: under the 1:1
        binding, a caller inside a session's worktree whose binding is live is
        that session's host.
        """
        domain, session = merged_session(m, host_session_id="host-aaaa-1111")
        worktree = Path(session.worktree)

        with pytest.raises(UsageError, match="worktree it would remove"):
            domain.teardown(session.id, cwd=worktree)

        # Nothing was removed and nothing was recorded: a refusal that had
        # already deleted half of what it refused would be the phantom-session
        # class arrived at from the other end.
        assert worktree.is_dir()
        assert session.branch in branch_names(m)
        assert domain.get(session.id).state == "merged"

    def test_the_refusal_names_the_step_that_clears_it(self, m):
        """A refusal nobody can act on costs the same wedged session.

        The caller cannot read the code from inside the deadlock it is warning
        about, so the message carries the way out rather than a diagnosis.
        """
        domain, session = merged_session(m, host_session_id="host-aaaa-1111")
        with pytest.raises(UsageError) as exc:
            domain.teardown(session.id, cwd=Path(session.worktree))
        message = str(exc.value)

        assert session.id in message
        assert session.worktree in message
        # The step out, spelled as a command rather than described.
        assert f"invariant session teardown --id {session.id}" in message
        assert f"cd {m.root}" in message
        # And the deliberate override, for a caller who knows better.
        assert "--force" in message

    def test_a_deeper_directory_inside_the_worktree_counts_as_standing_in_it(self, m):
        """Occupancy is the subtree, not the one path.

        A caller is normally several directories down inside the worktree, and
        removing it strands them exactly as thoroughly as removing the root.
        """
        domain, session = merged_session(m, host_session_id="host-aaaa-1111")
        deeper = Path(session.worktree) / "docs" / "architecture"
        assert deeper.is_dir()
        with pytest.raises(UsageError, match="worktree it would remove"):
            domain.teardown(session.id, cwd=deeper)

    # -- the companion negatives: everything else must still end cleanly ----

    def test_a_live_binding_tears_down_from_the_main_checkout(self, m):
        """The NORMAL ending, and the case a liveness-only check would break.

        A session torn down the documented way is bound to the very host
        session running the teardown, and `SessionEnd` has not fired because
        that host is still alive. So a live binding cannot on its own be the
        refusal's trigger: it is the resting state of every healthy teardown.
        """
        domain, session = merged_session(m, host_session_id="host-aaaa-1111")

        domain.teardown(session.id, cwd=m.root)

        assert not Path(session.worktree).exists()
        assert session.branch not in branch_names(m)
        assert domain.get(session.id).state == "torn_down"

    def test_a_spent_binding_tears_down_from_inside_the_worktree(self, m):
        """`SessionEnd` fired: there is no host left to strand.

        The caller's own shell is left in a deleted directory, which one `cd`
        undoes. That is not the deadlock this refusal exists for, and refusing
        it would block the cleanup of exactly the sessions most likely to need
        it.
        """
        domain, session = merged_session(
            m, host_session_id="host-aaaa-1111", host_ended=True
        )
        domain.teardown(session.id, cwd=Path(session.worktree))
        assert not Path(session.worktree).exists()
        assert domain.get(session.id).state == "torn_down"

    def test_an_unbound_session_tears_down_from_inside_the_worktree(self, m):
        """No binding at all — a session registered by hand, or before hooks."""
        domain, session = merged_session(m)
        assert session.host_session_id is None
        domain.teardown(session.id, cwd=Path(session.worktree))
        assert not Path(session.worktree).exists()
        assert domain.get(session.id).state == "torn_down"

    def test_keeping_the_worktree_is_never_refused(self, m):
        """The refusal guards a removal; `--keep-worktree` performs none.

        The branch is kept too, because git will not delete one that a live
        worktree still has checked out — an existing refusal that would mask
        the absence of this one.
        """
        domain, session = merged_session(m, host_session_id="host-aaaa-1111")
        domain.teardown(
            session.id,
            remove_worktree=False,
            remove_branch=False,
            cwd=Path(session.worktree),
        )
        assert Path(session.worktree).is_dir()
        assert domain.get(session.id).state == "torn_down"

    def test_force_tears_down_an_occupied_worktree(self, m):
        """The deliberate case: the operator knows the host has already moved.

        Same shape as every other override here — a refusal that cannot be
        overridden at all gets routed around outside the tool.

        Asserted to completion rather than to the removal, because forcing is
        the only way to reach the case where git's own working directory is
        the directory being removed: the branch delete used to run from inside
        it and fail with an unrelated ENOENT, leaving the worktree gone, the
        branch alive and no transition recorded.
        """
        domain, session = merged_session(m, host_session_id="host-aaaa-1111")
        domain.teardown(session.id, cwd=Path(session.worktree), force=True)
        assert not Path(session.worktree).exists()
        assert session.branch not in branch_names(m)
        assert domain.get(session.id).state == "torn_down"

    def test_an_unreadable_working_directory_does_not_block_the_cleanup(
        self, m, monkeypatch
    ):
        """The aftermath of the wedge must not be locked behind the guard.

        A shell whose directory has already been deleted cannot answer where
        it is standing. Nothing can be inside a path that cannot be named, and
        failing closed here would refuse the teardown of exactly the sessions
        that got into this state.
        """
        domain, session = merged_session(m, host_session_id="host-aaaa-1111")

        def gone():
            raise FileNotFoundError(2, "No such file or directory")

        monkeypatch.setattr(Path, "cwd", staticmethod(gone))

        domain.teardown(session.id)
        assert domain.get(session.id).state == "torn_down"

    def test_a_sibling_worktree_is_not_the_one_being_removed(self, m):
        """Standing in *a* worktree is not standing in *this* one.

        Concurrent sessions are the normal case, and a peer tearing down its
        own finished session from inside its own live worktree touches nothing
        the caller is standing in.
        """
        domain, session = merged_session(m, name="doomed", host_session_id="host-a")
        _, neighbour = merged_session(m, name="bystander", host_session_id="host-b")

        domain.teardown(session.id, cwd=Path(neighbour.worktree))

        assert not Path(session.worktree).exists()
        assert Path(neighbour.worktree).is_dir()


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

    def at_review(self, pipelines, session):
        """Park the pipeline where the agent-role gates live (ADR-003 D1).

        Set directly rather than advanced through, because advancing there is
        what the gates under test would themselves have to permit.
        """
        pipeline = pipelines.current(session.id)
        pipeline.state = "reviewing"
        pipelines._save(session.id, pipeline)
        return pipelines, session

    def verdict_for(self, pipelines, session, m, rule_id):
        verdicts = pipelines.evaluate_gates(session.id, cwd=m.root)
        for evaluated in verdicts:
            if evaluated.rule_id == rule_id:
                return evaluated
        raise AssertionError(f"{rule_id} was not among the gates here")

    def test_the_gate_warns_before_it_is_acknowledged(self, m):
        pipelines, session = self.at_review(*self.pipeline_at(m))
        evaluated = self.verdict_for(
            pipelines, session, m, "agents.design_agent_ran"
        )
        assert not evaluated.verdict.ok

    def test_acknowledging_turns_it_into_a_recorded_skip(self, m):
        pipelines, session = self.at_review(*self.pipeline_at(m))
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
        pipelines, session = self.at_review(*self.pipeline_at(m))
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
        pipelines, session = self.at_review(*self.pipeline_at(m))
        pipelines.acknowledge(session.id, "agents.design_agent_ran", "deliberate")

        sessions = SessionDomain(m)
        other = sessions.start("second")
        pipelines.start(other.id, task="another task")
        self.at_review(pipelines, other)
        evaluated = self.verdict_for(pipelines, other, m, "agents.design_agent_ran")
        assert not evaluated.verdict.ok

    def test_a_passing_gate_is_unaffected_by_an_acknowledgement(self, m):
        """It rewrites failures only — never manufactures a skip over a pass."""
        pipelines, session = self.at_review(*self.pipeline_at(m))
        pipelines.record_agent_run(session.id, "feature-architect")
        pipelines.acknowledge(session.id, "agents.design_agent_ran", "belt and braces")
        evaluated = self.verdict_for(pipelines, session, m, "agents.design_agent_ran")
        assert evaluated.verdict.ok and not evaluated.verdict.skipped



class CoverageFixture:
    """Shared setup only — a mixin, so its cases are not inherited and re-run."""

    def pipeline_with(self, m, script, report=None):
        """A project whose coverage command(s) are scripts we control.

        `report=None` is the single-command ecosystem: one program both runs
        the suite and prints the numbers, so `commands.coverage_report` is
        explicitly null and the command speaks for itself. The adapter default
        would otherwise supply one, since this fixture claims an ecosystem.
        """
        path = m.root / "fake-coverage.sh"
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        commands = {"coverage": f"sh {path}", "coverage_report": None}
        if report is not None:
            report_path = m.root / "fake-report.sh"
            report_path.write_text(report, encoding="utf-8")
            report_path.chmod(0o755)
            commands["coverage_report"] = f"sh {report_path}"
        (m.root / MANIFEST_RELPATH).write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "project": {"name": "demo", "ecosystems": ["swift"]},
                    "commands": commands,
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


class TestCoverageFromARedSuite(CoverageFixture):
    """Whether a measurement can be trusted depends on whether the suite RAN.

    Not on whether it passed. Those are different claims, and in a repository
    whose main branch is not green the first is the steady state.
    """

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

        # Per session AND per kind: a test run and a coverage run must not
        # share a bundle, or one would clear the other's mid-flight.
        bundle = (
            Path(pipelines.store.root) / "coverage" / f"{session.id}.coverage.xcresult"
        )
        bundle.mkdir(parents=True, exist_ok=True)
        (bundle / "marker").write_text("stale", encoding="utf-8")

        pipelines.run_coverage(session.id, cwd=m.root)
        assert not (bundle / "marker").exists()


class TestTheSuiteStatusAndTheReadStatusAreNotTheSameNumber(CoverageFixture):
    """A shell reports the LAST command's status, so a chain cannot say both.

    These two legs were one command joined by `;` and the conflation lied in
    both directions: a red suite followed by a clean read exited 0, and a green
    suite followed by a failed read exited non-zero. The second one is what
    actually happened — `xcrun` answers 72 for "utility not found", the
    `DEVELOPER_DIR=` prefix repairing the toolchain applied to the first
    command alone and never crossed the semicolon, and the resulting empty
    measurement was twice attributed to the suite and once to an imagined race
    before anyone looked at the reporting tool.
    """

    RED_RUN = """
echo "Test Suite 'All tests' failed at 2026-08-09."
echo "\t Executed 6 tests, with 4 failures (0 unexpected) in 6.5 (6.5) seconds"
exit 65
"""
    GREEN_RUN = """
echo "Test Suite 'All tests' passed at 2026-08-09."
echo "\t Executed 6 tests, with 0 failures (0 unexpected) in 6.5 (6.5) seconds"
exit 0
"""
    CLEAN_REPORT = """
echo "    /repo/Sources/App/Store.swift    100.00% (55/55)"
exit 0
"""
    UNREACHABLE_REPORT = """
echo "xcrun: error: unable to find utility \\"xccov\\"" >&2
exit 72
"""

    def test_a_red_suite_is_not_laundered_by_a_clean_read(self, m):
        """Chained, this exited 0 and the suite was recorded as passing."""
        pipelines, session = self.pipeline_with(m, self.RED_RUN, self.CLEAN_REPORT)
        result = pipelines.run_coverage(session.id, cwd=m.root)
        assert result["files"] == 1
        assert result["suite_passed"] is False
        assert result["failed"] == 4

    def test_the_two_statuses_are_recorded_apart(self, m):
        pipelines, session = self.pipeline_with(m, self.RED_RUN, self.CLEAN_REPORT)
        pipelines.run_coverage(session.id, cwd=m.root)
        run = pipelines.current(session.id).evidence["coverage_run"]
        assert run["exit_code"] == 65
        assert run["report_exit_code"] == 0

    def test_an_unreadable_report_does_not_accuse_the_suite(self, m):
        """The real incident. The suite was green; only the reader was missing."""
        pipelines, session = self.pipeline_with(
            m, self.GREEN_RUN, self.UNREACHABLE_REPORT
        )
        with pytest.raises(UsageError) as raised:
            pipelines.run_coverage(session.id, cwd=m.root)
        message = str(raised.value)
        assert "Only the reporting leg came back empty" in message
        assert "executed=6 failed=0" in message
        assert "fake-report.sh" in message

    def test_a_manifest_that_still_chains_the_legs_is_refused(self, m):
        """Adopting the split without splitting must not look like adopting it.

        Left alone, such a manifest runs the reporter twice AND keeps the
        first command's status meaning the reporter's — the defect intact
        behind a configuration that appears migrated.
        """
        pipelines, session = self.pipeline_with(m, self.GREEN_RUN, self.CLEAN_REPORT)
        chained = yaml.safe_load(
            (m.root / MANIFEST_RELPATH).read_text(encoding="utf-8")
        )
        commands = chained["commands"]
        commands["coverage"] = f"{commands['coverage']} ; {commands['coverage_report']}"
        (m.root / MANIFEST_RELPATH).write_text(
            yaml.safe_dump(chained), encoding="utf-8"
        )
        fresh = Manifest.load(m.root)
        with pytest.raises(UsageError, match="still runs the reporting tool"):
            PipelineDomain(fresh).run_coverage(session.id, cwd=m.root)

    def test_a_command_that_merely_chains_something_else_is_allowed(self, m):
        """Detection is by containment, not by the presence of a `;`."""
        pipelines, session = self.pipeline_with(m, self.RED_RUN, self.CLEAN_REPORT)
        chained = yaml.safe_load(
            (m.root / MANIFEST_RELPATH).read_text(encoding="utf-8")
        )
        chained["commands"]["coverage"] = (
            f"echo preparing ; {chained['commands']['coverage']}"
        )
        (m.root / MANIFEST_RELPATH).write_text(
            yaml.safe_dump(chained), encoding="utf-8"
        )
        fresh = Manifest.load(m.root)
        result = PipelineDomain(fresh).run_coverage(session.id, cwd=m.root)
        assert result["suite_passed"] is False
        assert result["files"] == 1


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


class TestDescribeRunNamesItsSource:
    def test_a_console_fallback_says_so(self):
        """A scraped count and a bundle read render the same otherwise, and
        they are not equally trustworthy."""
        line = describe_run(
            {"executed": 34, "failed": 0, "failed_tests": None, "source": "console"}
        )
        assert "scraped from console" in line

    def test_a_bundle_read_is_not_annotated(self):
        line = describe_run(
            {"executed": 13950, "failed": 625, "failed_tests": 625,
             "source": "result_bundle"}
        )
        assert "scraped" not in line

    def test_the_device_is_named_when_known(self):
        line = describe_run(
            {"executed": 10, "failed": 0, "source": "result_bundle",
             "device": "Heap-wt-mine (ABC)"}
        )
        assert "on Heap-wt-mine (ABC)" in line

    def test_skipped_tests_are_reported(self):
        line = describe_run(
            {"executed": 100, "failed": 0, "skipped": 2, "source": "result_bundle"}
        )
        assert "2 skipped" in line


class TestSphinxMultiLineOptions:
    """A wrapped option value used to fall into the body, and take every
    option after it along. Silent, and on data this library did not write."""

    def backend(self, tmp_path, text):
        from invariant.core.items import SphinxNeedsBackend

        path = tmp_path / "backlog.rst"
        path.write_text(text, encoding="utf-8")
        return SphinxNeedsBackend(path)

    WRAPPED = """
.. bug:: Drain treats every failure as transient
   :id: BUG_DRAIN
   :area: Frameworks/Intake,
          Frameworks/Database
   :severity: high
   :status: open

   The drain retries permanent failures forever.
"""

    def test_the_wrapped_value_stays_in_its_field(self, tmp_path):
        item = self.backend(tmp_path, self.WRAPPED).load_all()[0]
        assert item.fields["area"] == "Frameworks/Intake, Frameworks/Database"

    def test_options_after_a_wrapped_one_are_not_lost(self, tmp_path):
        """The compounding half: `option and not body` failed for all of them."""
        item = self.backend(tmp_path, self.WRAPPED).load_all()[0]
        assert item.fields["severity"] == "high"
        assert item.status == "open"

    def test_the_body_contains_only_the_body(self, tmp_path):
        item = self.backend(tmp_path, self.WRAPPED).load_all()[0]
        assert item.body == "The drain retries permanent failures forever."
        assert "severity" not in item.body
        assert "Frameworks/Database" not in item.body

    def test_a_single_line_option_block_still_parses(self, tmp_path):
        text = (
            ".. bug:: Simple\n   :id: BUG_X\n   :status: open\n\n   Body here.\n"
        )
        item = self.backend(tmp_path, text).load_all()[0]
        assert item.id == "BUG_X" and item.status == "open"
        assert item.body == "Body here."

    def test_an_entry_with_no_body_parses(self, tmp_path):
        text = ".. bug:: Bare\n   :id: BUG_Y\n   :status: open\n"
        item = self.backend(tmp_path, text).load_all()[0]
        assert item.id == "BUG_Y" and item.body == ""

    def test_a_colon_in_the_body_is_not_read_as_an_option(self, tmp_path):
        text = (
            ".. bug:: T\n   :id: BUG_Z\n\n   Note: this is prose, not a field.\n"
        )
        item = self.backend(tmp_path, text).load_all()[0]
        assert "Note: this is prose" in item.body
        assert "Note" not in item.fields


class TestSphinxInsertsBesideItsOwnKind:
    """Appending to the end of the file put an entry outside its section.

    A file the tool writes and a person must then repair is worse than one it
    declines to write, because the broken state is commit-shaped.
    """

    DOC = """Backlog
=======

Open bugs
---------

.. bug:: First
   :id: BUG_ONE
   :status: open

   One.

Closed
------

Nothing here yet.
"""

    def backend(self, tmp_path):
        from invariant.core.items import Item, SphinxNeedsBackend

        path = tmp_path / "backlog.rst"
        path.write_text(self.DOC, encoding="utf-8")
        return SphinxNeedsBackend(path), path, Item

    def test_a_new_entry_lands_after_the_last_of_its_type(self, tmp_path):
        backend, path, Item = self.backend(tmp_path)
        backend.save(Item(id="BUG_TWO", type="bug", title="Second", status="open"))
        text = path.read_text(encoding="utf-8")
        assert text.index("BUG_TWO") < text.index("Closed")

    def test_the_trailing_section_survives(self, tmp_path):
        backend, path, Item = self.backend(tmp_path)
        backend.save(Item(id="BUG_TWO", type="bug", title="Second", status="open"))
        text = path.read_text(encoding="utf-8")
        assert "Closed" in text and "Nothing here yet." in text

    def test_the_existing_entry_is_untouched(self, tmp_path):
        backend, path, Item = self.backend(tmp_path)
        backend.save(Item(id="BUG_TWO", type="bug", title="Second", status="open"))
        assert backend.get("BUG_ONE").title == "First"

    def test_both_entries_parse_back(self, tmp_path):
        backend, path, Item = self.backend(tmp_path)
        backend.save(Item(id="BUG_TWO", type="bug", title="Second", status="open"))
        assert {i.id for i in backend.load_all()} == {"BUG_ONE", "BUG_TWO"}

    def test_with_no_entry_of_that_type_it_appends(self, tmp_path):
        """No anchor, so nothing clever — it says so rather than guessing."""
        backend, path, Item = self.backend(tmp_path)
        backend.save(Item(id="IDEA_ONE", type="idea", title="An idea", status="open"))
        text = path.read_text(encoding="utf-8")
        assert text.index("IDEA_ONE") > text.index("Nothing here yet.")


class TestSlugIdStrategy:
    """A numeric id is unlinkable in a backlog whose entries are referenced by
    name — as :need: targets in architecture docs, say — and inconsistent with
    every neighbour."""

    def domain(self, tmp_path, strategy):
        m = git_project(
            tmp_path / "proj",
            {"version": 1, "items": {"backend": "markdown", "id_strategy": strategy}},
        )
        return WorkItemDomain(m)

    def test_numeric_is_still_the_default(self, tmp_path):
        m = git_project(tmp_path / "proj", {"version": 1})
        assert WorkItemDomain(m).new("bug", "Anything at all").id == "BUG_001"

    def test_slug_derives_from_the_title(self, tmp_path):
        item = self.domain(tmp_path, "slug").new(
            "bug", "Drain treats every failure as transient"
        )
        assert item.id == "BUG_DRAIN_TREATS_EVERY_FAILURE_AS_TRANSIENT"

    def test_punctuation_and_case_are_normalised(self, tmp_path):
        item = self.domain(tmp_path, "slug").new("bug", "xccov: it's 100.00% wrong!")
        assert item.id == "BUG_XCCOV_IT_S_100_00_WRONG"

    def test_a_colliding_slug_is_suffixed_rather_than_overwriting(self, tmp_path):
        domain = self.domain(tmp_path, "slug")
        first = domain.new("bug", "Same title")
        second = domain.new("bug", "Same title")
        assert first.id != second.id
        assert second.id.endswith("_2")

    def test_a_titleless_slug_falls_back_to_the_stem(self):
        """Tested on the helper: the domain refuses an empty title upstream,
        so this branch is unreachable through `new` and asserting it there
        would be asserting nothing."""
        from invariant.core.items import _slug_id

        assert _slug_id("BUG_", "", set()) == "BUG"

    def test_an_unknown_strategy_is_refused_by_name(self, tmp_path):
        with pytest.raises(UsageError, match="id_strategy"):
            self.domain(tmp_path, "hieroglyph").new("bug", "x")


class TestSeveralSelectorsRunOrRefuse:
    """BUG_031 — a repeated `--selector` kept only the last one and reported
    a healthy green for a scope nobody asked for. A flag that is accepted and
    then discarded is the worst of the three outcomes, because the caller
    believes a scope was covered that was not.

    How several selectors are spelled in one command is ecosystem knowledge:
    pytest takes node ids positionally, xcodebuild needs a repeated
    `-only-testing:` flag the manifest template owns. So the adapter supplies
    the fact and the domain keeps the policy — run them, or refuse.
    """

    def project(self, tmp_path, ecosystem, test_one):
        return git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"ecosystems": [ecosystem]},
                "commands": {"test": "true", "test_one": test_one},
            },
        )

    def pipeline_for(self, m):
        session = SessionDomain(m).start("x")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id)
        return pipelines, session

    def test_both_selectors_reach_the_command(self, tmp_path):
        m = self.project(tmp_path, "python", "echo ran {selector}")
        pipelines, session = self.pipeline_for(m)
        record = pipelines.run_tests(session.id, phase="red", selectors=["A", "B"], cwd=m.root)
        assert "A" in record["command"] and "B" in record["command"]

    def test_a_single_selector_is_unchanged(self, tmp_path):
        m = self.project(tmp_path, "python", "echo ran {selector}")
        pipelines, session = self.pipeline_for(m)
        record = pipelines.run_tests(session.id, phase="red", selectors=["A"], cwd=m.root)
        assert record["command"] == "echo ran A"

    def test_an_ecosystem_that_cannot_join_them_refuses(self, tmp_path):
        """Refusal costs the caller one retry. A discard costs them the
        belief that the scope was covered."""
        m = self.project(tmp_path, "generic", "echo ran {selector}")
        pipelines, session = self.pipeline_for(m)
        with pytest.raises(UsageError, match="one selector at a time"):
            pipelines.run_tests(session.id, phase="red", selectors=["A", "B"], cwd=m.root)

    def test_the_refusal_names_both_selectors(self, tmp_path):
        m = self.project(tmp_path, "generic", "echo ran {selector}")
        pipelines, session = self.pipeline_for(m)
        with pytest.raises(UsageError) as exc:
            pipelines.run_tests(session.id, phase="red", selectors=["A", "B"], cwd=m.root)
        assert "A" in str(exc.value) and "B" in str(exc.value)

    def test_nothing_is_recorded_when_the_request_is_refused(self, tmp_path):
        """A refused request must not leave a run behind for `test.all_pass`
        to read as the current state of the suite."""
        m = self.project(tmp_path, "generic", "echo ran {selector}")
        pipelines, session = self.pipeline_for(m)
        with pytest.raises(UsageError):
            pipelines.run_tests(session.id, phase="red", selectors=["A", "B"], cwd=m.root)
        assert pipelines.current(session.id).evidence.get("test_runs") is None


class TestTheSuiteDoesNotInheritTheRecordersIdentity:
    """BUG_035 — `INVARIANT_SESSION_ID` was inherited by the spawned test
    process. In THIS repository, whose own suite exercises session creation,
    every test that starts a session then collided with the named live one
    and died on `session '<id>' already exists`: 25 false failures recorded
    as durable red evidence, against a bare run that was fully green.

    The documented way to name a run for recording purposes must not change
    what the suite it records measures.
    """

    def env_probe(self, tmp_path):
        return git_project(
            tmp_path / "proj",
            {
                "version": 1,
                "project": {"ecosystems": ["python"]},
                "commands": {
                    "test": "test -z \"$INVARIANT_SESSION_ID\"",
                    "coverage": "test -z \"$INVARIANT_SESSION_ID\"",
                },
            },
        )

    def test_the_test_command_does_not_see_it(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INVARIANT_SESSION_ID", "live-session")
        m = self.env_probe(tmp_path)
        session = SessionDomain(m).start("x")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id)
        record = pipelines.run_tests(session.id, phase="red", cwd=m.root)
        assert record["passed"], "the spawned suite inherited INVARIANT_SESSION_ID"

    def test_the_parents_own_environment_is_untouched(self, tmp_path, monkeypatch):
        """Stripping it for the child must not deregister the caller."""
        import os

        monkeypatch.setenv("INVARIANT_SESSION_ID", "live-session")
        m = self.env_probe(tmp_path)
        session = SessionDomain(m).start("x")
        pipelines = PipelineDomain(m)
        pipelines.start(session.id)
        pipelines.run_tests(session.id, phase="red", cwd=m.root)
        assert os.environ["INVARIANT_SESSION_ID"] == "live-session"
