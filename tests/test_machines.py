import pytest
import yaml

from invariant.core.errors import ManifestError
from invariant.core.machines import (
    AGENT_ROLE_GATES,
    DOCS_FIRST_GATES,
    MACHINE_DIR,
    load_item_machine,
    load_machine,
    load_pipeline_machine,
    load_session_machine,
    shipped_ids,
)
from invariant.core.rules import all_rules


class TestShippedMachines:
    def test_every_shipped_machine_is_structurally_sound(self):
        # Machine.from_dict validates on load, so this asserts the whole set
        # parses, has a reachable graph, and has no dead ends.
        for machine_id in shipped_ids():
            assert load_machine(machine_id).id == machine_id

    def test_the_expected_set_is_present(self):
        assert set(shipped_ids()) >= {
            "session", "tdd", "bugfix", "spec-first", "chore", "none", "idea", "bug",
        }

    def test_every_gate_names_a_registered_rule(self):
        """A gate naming a rule that does not exist would pass forever."""
        registered = {spec.id for spec in all_rules()}
        for machine_id in shipped_ids():
            machine = load_machine(machine_id)
            for state in machine.states.values():
                for gate in (*state.entry_gates, *state.exit_gates):
                    assert gate in registered, f"{machine_id}.{state.id} -> {gate}"

    def test_kinds_are_what_the_loaders_expect(self):
        assert load_machine("session").kind == "session"
        assert load_machine("tdd").kind == "pipeline"
        assert load_machine("bug").kind == "item"


class TestDocsFirstInjection:
    def test_every_pipeline_with_a_landing_state_gets_the_update_gate(
        self, make_manifest
    ):
        """It rides `landing`, not `review`, and as an ENTRY gate.

        On the review state's exit it fired on every edge out of review, so a
        review that found the change unfinished was asked to update the
        architecture docs as the price of going back to finish it (BUG_037).
        On the landing state's entry it fires at the same moment for the same
        edge, and on no other.
        """
        for methodology in ("tdd", "bugfix", "spec-first", "chore", "docs"):
            m = make_manifest({"methodology": {"pipeline": methodology}})
            machine = load_pipeline_machine(m)
            landing = machine.states_with_role("landing")
            assert landing, methodology
            assert "docs.updated" in machine.state(landing[0]).entry_gates, methodology

    def test_every_pipeline_with_a_design_state_gets_the_read_gate(self, make_manifest):
        for methodology in ("tdd", "bugfix", "spec-first"):
            m = make_manifest({"methodology": {"pipeline": methodology}})
            machine = load_pipeline_machine(m)
            exit_ = {g for s in machine.states.values() for g in s.exit_gates}
            assert "docs.read_architecture" in exit_, methodology

    def test_the_read_gate_can_actually_fail(self, make_manifest):
        """It guards leaving the design state, not entering it.

        A pipeline starts in its design state. An entry gate there would be
        evaluated before any work happened and could never fail, which is a
        gate that looks present and enforces nothing.
        """
        m = make_manifest({"methodology": {"pipeline": "tdd"}})
        machine = load_pipeline_machine(m)
        design = machine.states_with_role("design")[0]
        assert design == machine.initial
        assert "docs.read_architecture" in machine.state(design).exit_gates
        assert "docs.read_architecture" not in machine.state(design).entry_gates

    def test_chore_is_exempt_from_the_read_gate_but_not_the_update_gate(
        self, make_manifest
    ):
        """A methodology that declares no design state is asserting there is none.

        Forcing an architecture read onto a formatter pass would make `chore`
        useless, which pushes people to `none` and drops every gate — a strictly
        worse outcome than exempting the read. The review gate still catches a
        chore that turns out to change structure after all.
        """
        m = make_manifest({"methodology": {"pipeline": "chore"}})
        machine = load_pipeline_machine(m)
        assert machine.states_with_role("design") == ()
        entry = {g for s in machine.states.values() for g in s.entry_gates}
        exit_ = {g for s in machine.states.values() for g in s.exit_gates}
        assert "docs.read_architecture" not in entry
        assert "docs.read_architecture" not in exit_
        assert "docs.updated" in entry

    def test_the_shipped_files_do_not_declare_them_themselves(self):
        # Docs-first is core policy, applied centrally. If a methodology file
        # declared it, a project forking that file could silently drop it.
        for machine_id in ("tdd", "bugfix", "chore"):
            raw = yaml.safe_load((MACHINE_DIR / f"{machine_id}.yaml").read_text())
            text = str(raw)
            assert "docs.read_architecture" not in text

    def test_injection_targets_roles_not_names(self):
        assert set(DOCS_FIRST_GATES) == {"design", "landing"}

    def test_a_review_state_without_a_landing_state_is_refused(self, make_manifest, tmp_path):
        """The omission this injection could otherwise make silently.

        A role nothing carries injects nothing. That is correct for `design` —
        `chore` declares none deliberately — and unacceptable for `landing`,
        whose gates are the ones ADR-001 D12 says a methodology cannot opt out
        of. Forgetting the role would drop them with nothing to report it.
        """
        machine_dir = tmp_path / "machines"
        machine_dir.mkdir()
        (machine_dir / "forgetful.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "forgetful",
                    "kind": "pipeline",
                    "initial": "doing",
                    "terminal": ["done"],
                    "states": {
                        "doing": {"next": ["checking"]},
                        "checking": {"roles": ["review"], "next": ["done"]},
                        "done": {},
                    },
                }
            ),
            encoding="utf-8",
        )
        m = make_manifest(
            {
                "methodology": {
                    "pipeline": "forgetful",
                    "extra_machines": [str(machine_dir)],
                }
            }
        )
        with pytest.raises(ManifestError, match="no 'landing' state"):
            load_pipeline_machine(m)

    def test_a_machine_without_the_roles_is_unaffected(self, make_manifest):
        m = make_manifest({"methodology": {"pipeline": "none"}})
        machine = load_pipeline_machine(m)
        assert machine.states["working"].entry_gates == ()


class TestAgentRoleGates:
    """The agent gates ride the same role injection as docs-first."""

    def test_both_are_attached_to_the_tdd_pipeline(self, make_manifest):
        machine = load_pipeline_machine(make_manifest({}))
        entry = {g for s in machine.states.values() for g in s.entry_gates}
        assert {"agents.design_agent_ran", "agents.review_agent_ran"} <= entry

    def test_they_are_the_landing_states_entry_gates(self, make_manifest):
        """Entry gates on the LANDING state, which is not the same relaxation
        as an entry gate on the state the agent works in.

        The original reasoning holds and is why they are not on the design
        state: a pipeline starts there, so an entry gate there could never
        fail. What changed is which state carries them. `agents.*_ran` turns on
        evidence produced when an agent RETURNS, so the earliest boundary a
        different actor drives is the landing edge — and writing them as the
        landing state's entry condition is ADR-003 D2's own sentence, "the gate
        guards landing", made true of the machine rather than only of the
        prose. As the review state's EXIT set they also fired on every retreat
        out of review, which is BUG_037.
        """
        machine = load_pipeline_machine(make_manifest({}))
        landing = machine.states_with_role("landing")[0]
        assert {"agents.design_agent_ran", "agents.review_agent_ran"} <= set(
            machine.state(landing).entry_gates
        )
        exit_ = {g for s in machine.states.values() for g in s.exit_gates}
        assert not {g for g in exit_ if g.startswith("agents.")}

    def test_injection_targets_roles_not_names(self):
        assert set(AGENT_ROLE_GATES) == {"landing"}

    def test_the_design_gate_does_not_sit_on_the_design_state(self, make_manifest):
        """ADR-003 D1 — a gate an agent clears by RETURNING cannot guard the
        state that agent is running in.

        `Task` PostToolUse fires when a subagent returns, so a design agent
        driving its own pipeline can never have its own dispatch on record
        before it advances out of the design state. The gate was structurally
        unsatisfiable for the one actor it names, and three pipelines on this
        machine carry a forced override of exactly it (BUG_021).
        """
        machine = load_pipeline_machine(make_manifest({}))
        design = machine.states_with_role("design")[0]
        assert "agents.design_agent_ran" not in machine.state(design).exit_gates
        assert "agents.design_agent_ran" not in machine.state(design).entry_gates

    def test_both_agent_gates_guard_the_landing_edge(self, make_manifest):
        """The earliest boundary driven by a DIFFERENT actor.

        `reviewing -> committed` is advanced by the review agent, which the
        parent dispatches only after the design agent returned — so the design
        agent's dispatch is on record by then (ADR-003 D1). What this pins is
        that EDGE, evaluated the way a transition evaluates it, rather than the
        state the gates happen to hang off: the gates moved from the review
        state's exit set to the landing state's entry set (BUG_037) and the
        edge's obligation is unchanged, which is the whole claim of that move.
        """
        from invariant.domains.pipeline import PipelineDomain

        machine = load_pipeline_machine(make_manifest({}))
        review = machine.states_with_role("review")[0]
        landing = machine.states_with_role("landing")[0]
        gates = set(PipelineDomain.gates_for(None, review, landing, machine))
        assert {"agents.design_agent_ran", "agents.review_agent_ran"} <= gates

    def test_the_docs_read_gate_stays_on_the_design_state(self, make_manifest):
        """ADR-003 D1's limit. The design agent satisfies docs-first by
        READING, which it completes while running, so `machines.py`'s original
        placement argument still governs that gate and only that gate."""
        machine = load_pipeline_machine(make_manifest({}))
        design = machine.states_with_role("design")[0]
        assert "docs.read_architecture" in machine.state(design).exit_gates

    def test_every_shipped_methodology_with_a_design_role_has_a_review_role(self):
        """ADR-003 D2 rests on this. A design role with no review role would
        lose the gate outright rather than merely have it move."""
        for machine_id in shipped_ids():
            machine = load_machine(machine_id)
            if machine.kind != "pipeline":
                continue
            if machine.states_with_role("design"):
                assert machine.states_with_role("review"), machine_id

    def test_a_roleless_machine_is_unaffected(self, make_manifest):
        machine = load_pipeline_machine(make_manifest({"methodology": {"pipeline": "none"}}))
        gates = {
            g
            for s in machine.states.values()
            for g in s.entry_gates + s.exit_gates
        }
        assert not {g for g in gates if g.startswith("agents.")}

    def test_no_methodology_file_declares_them(self):
        # Same reason as docs-first: applied centrally, so forking a
        # methodology file cannot silently drop them.
        for machine_id in ("tdd", "bugfix", "chore"):
            raw = str(yaml.safe_load((MACHINE_DIR / f"{machine_id}.yaml").read_text()))
            assert "agents." not in raw

    def test_they_advise_in_every_shipped_profile(self):
        """A blocking heuristic is a false positive with authority.

        Pinned as a test because the tempting change — "strict should block
        this" — makes every inline one-line fix require an override, and a
        profile that gets overridden routinely teaches people to reach for
        --force on the gates that do matter.
        """
        from invariant.core.manifest import DATA_DIR

        for profile in ("standard", "strict", "loose"):
            path = DATA_DIR / "profiles" / f"{profile}.yaml"
            rules = yaml.safe_load(path.read_text())["rules"]
            assert rules["agents.design_agent_ran"] == "warn", profile
            assert rules["agents.review_agent_ran"] == "warn", profile


class TestBUG037ABackwardEdgeIsNotGatedByTheShipGates:
    """A gate belongs to the edge it protects, not to the state it hangs off.

    `reviewing` advertised four successors and refused two of them, because its
    exit gates — the ones that exist to permit SHIPPING — were evaluated on
    every edge out of it including the retreats. A review that discovers more
    work is owed then had to choose between satisfying ship gates on a change
    the reviewer had just judged unfinished, and doing that work in the wrong
    state. Both routes ended at `--force`, which is the outcome this library
    treats as the defect rather than as the escape.
    """

    LANDING = {
        "tdd": ("reviewing", "committed"),
        "bugfix": ("verifying", "committed"),
        "spec-first": ("reviewing", "committed"),
        "chore": ("reviewing", "committed"),
        "docs": ("reviewing", "committed"),
    }

    #: What each review state may retreat TO, and exactly what that edge owes.
    #:
    #: Spelled per edge rather than as "retreats are ungated", because that
    #: broader claim is not the principle and would be wrong. The principle is
    #: that a gate belongs to the destination it protects — so spec-first's
    #: `reviewing -> implementing` legitimately carries `docs.updated`, which
    #: is not a ship gate there but implementing's own precondition: this
    #: methodology's premise is that a written spec exists before code follows
    #: it. It is satisfiable on that edge (the spec was written to get to
    #: review at all), which is the property that separates a real precondition
    #: from the wedge BUG_037 is about.
    RETREATS = {
        "tdd": {"red": set(), "refactor": {"test.all_pass"}},
        "bugfix": {"fixing": {"test.new_test_fails"}},
        "spec-first": {"implementing": {"docs.updated"}},
        "chore": {"doing": set()},
        "docs": {"drafting": set()},
    }

    #: The gates that exist to permit LANDING. None of these may fire on a
    #: retreat, whatever else does.
    SHIP_GATES = {
        "coverage.meets_floor",
        "docs.diagrams_current",
        "agents.design_agent_ran",
        "agents.review_agent_ran",
        "privacy.clean",
    }

    def gates(self, make_manifest, methodology, source, target):
        from invariant.domains.pipeline import PipelineDomain

        machine = load_pipeline_machine(make_manifest({}), methodology)
        return PipelineDomain.gates_for(None, source, target, machine)

    @pytest.mark.parametrize("methodology", sorted(LANDING))
    def test_the_ship_gates_still_guard_the_landing_edge(
        self, make_manifest, methodology
    ):
        """The half that must not be lost. Moving a gate off the retreat is
        only correct if it still fires on the edge it was written for."""
        review, landing = self.LANDING[methodology]
        gates = set(self.gates(make_manifest, methodology, review, landing))
        assert "privacy.clean" in gates, methodology
        assert "docs.updated" in gates, methodology
        assert "agents.review_agent_ran" in gates, methodology

    @pytest.mark.parametrize("methodology", sorted(RETREATS))
    def test_a_retreat_from_review_carries_no_ship_gate(
        self, make_manifest, methodology
    ):
        review, _ = self.LANDING[methodology]
        for target in self.RETREATS[methodology]:
            gates = set(self.gates(make_manifest, methodology, review, target))
            assert not gates & self.SHIP_GATES, (
                f"{methodology}: {review} -> {target} owes {sorted(gates)}"
            )

    @pytest.mark.parametrize("methodology", sorted(RETREATS))
    def test_a_retreat_owes_exactly_its_destinations_preconditions(
        self, make_manifest, methodology
    ):
        """The other half, and the one that keeps this from being a relaxation.

        Asserting only that ship gates are absent would pass on a machine that
        had lost every gate. Each retreat's whole gate set is pinned, so a
        precondition dropped by accident shows here rather than at the next
        session that skips it.
        """
        review, _ = self.LANDING[methodology]
        for target, expected in self.RETREATS[methodology].items():
            gates = set(self.gates(make_manifest, methodology, review, target))
            assert gates == expected, f"{methodology}: {review} -> {target}"

    @pytest.mark.parametrize("methodology", sorted(LANDING))
    def test_abandoning_is_never_gated(self, make_manifest, methodology):
        """Abandoning is the deliberate way out. Holding it to the gates that
        permit landing means the only way to stop is to finish first, and the
        route people actually take from there is `--force`.

        Asserted from EVERY state, not only the review one. The review state's
        exit set emptied as a side effect of moving the ship gates, which would
        make this pass for the wrong reason; the design state still has an exit
        gate — the injected docs-first read — and `triaging -> abandoned` was
        refused by it until the abandon role was honoured.
        """
        machine = load_pipeline_machine(make_manifest({}), methodology)
        for state in machine.states:
            if "abandoned" not in machine.next_states(state):
                continue
            assert not self.gates(make_manifest, methodology, state, "abandoned"), (
                f"{methodology}: {state} -> abandoned"
            )

    #: Edges that carried no gate BEFORE this change and must still carry none.
    #: Moving a gate to the destination it protects can silently make a
    #: previously free edge expensive — `chore`'s `doing` had no exit set at
    #: all, so putting `test.all_pass` on `reviewing`'s entry would have
    #: demanded a test run to START reviewing a dependency bump, which is how
    #: `chore` becomes useless and people reach for `none`. Caught by the suite
    #: rather than by review; pinned so the next rearrangement is caught too.
    #: Spelled as the exact expected set, not as "empty", because `drafting`
    #: carries the injected docs-first read gate and always has — the claim is
    #: that these edges owe what they owed, not that they owe nothing.
    ALREADY_FREE = [
        ("chore", "doing", "reviewing", set()),
        ("docs", "drafting", "reviewing", {"docs.read_architecture"}),
        ("bugfix", "reproducing", "not_reproducible", set()),
    ]

    @pytest.mark.parametrize("methodology,source,target,expected", ALREADY_FREE)
    def test_an_edge_that_was_never_gated_did_not_become_gated(
        self, make_manifest, methodology, source, target, expected
    ):
        assert set(self.gates(make_manifest, methodology, source, target)) == expected

    def test_BUG_037_a_no_repro_outcome_is_not_gated_by_a_failing_test(
        self, make_manifest
    ):
        """Found while fixing this, in the same shape and worth its own claim.

        `bugfix` gated `reproducing -> not_reproducible` with
        `test.new_test_fails`, because that was `reproducing`'s exit gate. The
        destination IS "no failing test could be produced", so the gate was
        unsatisfiable by definition on the one edge that means it — and the
        machine's own description calls that outcome "a real outcome, not a
        failure of the attempt".
        """
        assert not self.gates(
            make_manifest, "bugfix", "reproducing", "not_reproducible"
        )
        assert "test.new_test_fails" in self.gates(
            make_manifest, "bugfix", "reproducing", "fixing"
        )


class TestSessionMachine:
    def test_the_push_barrier_is_after_the_merge(self):
        machine = load_session_machine()
        assert not machine.can("working", "pushed")
        assert not machine.can("reviewed", "pushed")
        assert machine.can("merged", "pushed")

    def test_teardown_follows_a_merge_or_a_push(self):
        """Publishing is a detour, so the merge is the normal end.

        `merged -> torn_down` is the edge that makes a session finishable
        without touching a remote. `pushed -> torn_down` stays for projects
        that opted in.
        """
        machine = load_session_machine()
        assert machine.can("merged", "torn_down")
        assert machine.can("pushed", "torn_down")
        assert not machine.can("working", "torn_down")
        assert not machine.can("reviewed", "torn_down")

    def test_publishing_is_held_by_a_gate_not_by_the_graph(self):
        """Enabling it must be one manifest line, never a forked machine."""
        machine = load_session_machine()
        assert machine.can("merged", "pushed")
        assert "session.push_enabled" in machine.state("pushed").entry_gates

    def test_abandon_is_reachable_from_every_working_state(self):
        machine = load_session_machine()
        for state in ("registered", "isolated", "working", "reviewed", "merged"):
            assert machine.can(state, "abandoned"), state

    def test_the_landing_boundary_carries_the_deliverables_gate(self):
        """Both states, and the repetition is the point.

        `reviewed` can be re-entered from `working`, and the merge is the
        irreversible step, so a gate that only guarded the reviewable step
        would guard nothing once the work moved on from it. The file already
        carries that argument for `pipeline.complete`; this is the same shape
        one question over — did the session finish what it said it was for.

        Entry gates, so the `reviewed -> working` retreat stays walkable: a
        session that discovers more deliverables are owed must be able to go
        back and do them without first satisfying the gate that says they are
        all done (BUG_037).
        """
        machine = load_session_machine()
        for state in ("reviewed", "merged"):
            assert "session.deliverables_complete" in machine.state(state).entry_gates
            assert "session.deliverables_complete" not in (
                machine.state(state).exit_gates
            )
        assert machine.can("reviewed", "working")


class TestBugMachine:
    def test_fixed_is_unreachable_without_a_reproduction(self):
        machine = load_item_machine("bug")
        assert not machine.can("filed", "fixed")
        assert machine.can("reproduced", "fixed")

    def test_declining_a_bug_is_distinct_from_failing_to_reproduce(self):
        # A decision must never be recorded as a failed investigation.
        machine = load_item_machine("bug")
        assert machine.can("filed", "wont_fix")
        assert machine.can("filed", "not_reproducible")
        assert "wont_fix" in machine.terminal
        assert "not_reproducible" in machine.terminal

    def test_a_verified_bug_can_be_reopened(self):
        assert load_item_machine("bug").can("verified", "reproduced")

    def test_closing_carries_the_residue_gate(self):
        machine = load_item_machine("bug")
        assert "item.residue_matches_status" in machine.state("closed").entry_gates


class TestIdeaMachine:
    def test_the_residue_gate_guards_entry_to_done_not_exit_from_it(self):
        """An exit gate on a terminal state is a gate that can never fire.

        `done` is terminal, so nothing ever leaves it and its exit gates are
        never evaluated — an idea could close owing three unresolved legs and
        the gate named to prevent exactly that would stay silent. The bug
        machine already gets this right on `closed`; this is the same fix.
        """
        machine = load_item_machine("idea")
        assert "item.residue_matches_status" in machine.state("done").entry_gates
        assert not machine.state("done").exit_gates

    def test_no_terminal_state_of_any_item_machine_carries_an_exit_gate(self):
        for item_type in ("idea", "bug"):
            machine = load_item_machine(item_type)
            for state_id in machine.terminal:
                assert not machine.state(state_id).exit_gates, f"{item_type}.{state_id}"


class TestLoaderErrors:
    def test_unknown_machine_lists_the_shipped_ones(self):
        with pytest.raises(ManifestError, match="shipped machines are: .*tdd"):
            load_machine("nonexistent")

    def test_wrong_kind_for_pipeline(self, make_manifest):
        m = make_manifest({"methodology": {"pipeline": "bug"}})
        with pytest.raises(ManifestError, match="not 'pipeline'"):
            load_pipeline_machine(m)

    def test_wrong_kind_for_item(self):
        with pytest.raises(ManifestError, match="not 'item'"):
            load_item_machine("tdd")


class TestProjectMachines:
    def test_a_project_machine_shadows_a_shipped_one(self, tmp_path, make_manifest):
        custom = tmp_path / "machines"
        custom.mkdir()
        (custom / "tdd.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "tdd",
                    "kind": "pipeline",
                    "initial": "only",
                    "terminal": ["only"],
                    "states": {"only": {"roles": ["design"]}},
                }
            ),
            encoding="utf-8",
        )
        m = make_manifest(
            {"methodology": {"pipeline": "tdd", "extra_machines": ["machines"]}}
        )
        machine = load_pipeline_machine(m)
        assert set(machine.states) == {"only"}
        # Forking a methodology still cannot drop the core policy.
        assert "docs.read_architecture" in machine.state("only").exit_gates


class TestDocsMethodology:
    """A docs-only change had no honest route through `tdd`.

    Three gates blocked that could not be satisfied in principle — no
    implementation to fail a test first, no code to measure, no structure to
    diagram — and none could be acknowledged, because they block. The only way
    through was `advance --force`, and a tool that teaches --force as routine
    has spent the thing it exists for.
    """

    def machine(self, make_manifest):
        return load_pipeline_machine(make_manifest({"methodology": {"pipeline": "docs"}}))

    def test_it_is_shipped(self):
        assert "docs" in shipped_ids()

    def test_it_demands_neither_a_red_test_nor_coverage(self, make_manifest):
        gates = {
            g
            for s in self.machine(make_manifest).states.values()
            for g in s.entry_gates + s.exit_gates
        }
        assert "test.new_test_fails" not in gates
        assert "coverage.meets_floor" not in gates

    def test_privacy_still_blocks(self, make_manifest):
        """Not relaxed. A clock time in prose is exactly as bad as in code."""
        gates = {
            g
            for s in self.machine(make_manifest).states.values()
            for g in s.entry_gates + s.exit_gates
        }
        assert "privacy.clean" in gates

    def test_the_docs_are_still_read_and_updated(self, make_manifest):
        """It keeps the design and review roles, so docs-first still applies."""
        gates = {
            g
            for s in self.machine(make_manifest).states.values()
            for g in s.entry_gates + s.exit_gates
        }
        assert "docs.read_architecture" in gates
        assert "docs.updated" in gates

    def test_it_reaches_committed(self, make_manifest):
        machine = self.machine(make_manifest)
        assert machine.can("drafting", "reviewing")
        assert machine.can("reviewing", "committed")


class TestEveryMachineDeclaringProofOfRedNamesItsRedState:
    """BUG_030 — the gate was declared by two machines and written against
    one. A machine that names `test.new_test_fails` as a gate and has no
    state called `red` must say which of its own states is the red one, or
    it ships a gate nothing on it can satisfy."""

    def test_no_shipped_machine_declares_an_unsatisfiable_proof_of_red(self):
        for machine_id in shipped_ids():
            machine = load_machine(machine_id)
            declares = [
                state.id
                for state in machine.states.values()
                if "test.new_test_fails" in (*state.entry_gates, *state.exit_gates)
            ]
            if not declares:
                continue
            red = machine.red_phase or "red"
            assert red in machine.states, (
                f"{machine_id} declares test.new_test_fails from {declares} "
                f"but has no state {red!r} to record a failing run in"
            )

    def test_bugfix_names_reproducing(self):
        assert load_machine("bugfix").red_phase == "reproducing"
