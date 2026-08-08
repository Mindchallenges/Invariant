import pytest
import yaml

from invariant.core.errors import TransitionError, UsageError
from invariant.core.fsm import Machine, State

SOUND = {
    "id": "demo",
    "kind": "pipeline",
    "initial": "a",
    "terminal": ["c"],
    "states": {
        "a": {"next": ["b"], "roles": ["design"], "entry_gates": ["g.one"]},
        "b": {"next": ["c"], "exit_gates": ["g.two"], "roles": ["review"]},
        "c": {"description": "done"},
    },
}


def machine(**overrides):
    raw = {**SOUND, **overrides}
    return Machine.from_dict(raw)


class TestValidation:
    def test_sound_machine_loads(self):
        m = machine()
        assert m.id == "demo"
        assert m.initial == "a"
        assert m.is_terminal("c")

    def test_missing_initial_is_rejected(self):
        with pytest.raises(UsageError, match="initial state 'zzz' is not defined"):
            machine(initial="zzz")

    def test_unknown_successor_is_rejected(self):
        states = {**SOUND["states"], "a": {"next": ["nowhere"]}}
        with pytest.raises(UsageError, match="unknown successor 'nowhere'"):
            machine(states=states)

    def test_dead_end_that_is_not_terminal_is_rejected(self):
        # 'c' has no successors and terminal is now empty -> a dead end.
        with pytest.raises(UsageError, match="dead end but is not marked terminal"):
            machine(terminal=[])

    def test_unreachable_state_is_rejected(self):
        states = {**SOUND["states"], "orphan": {"next": ["c"]}}
        with pytest.raises(UsageError, match="'orphan' is unreachable"):
            machine(states=states)

    def test_undefined_terminal_is_rejected(self):
        with pytest.raises(UsageError, match="terminal state 'ghost' is not defined"):
            machine(terminal=["c", "ghost"])

    def test_missing_required_key(self):
        with pytest.raises(UsageError, match="missing required key 'initial'"):
            Machine.from_dict({"id": "x", "states": {"a": {}}})

    def test_no_states_at_all(self):
        with pytest.raises(UsageError, match="declares no states"):
            Machine.from_dict({"id": "x", "initial": "a", "states": {}})


class TestQueries:
    def test_next_states(self):
        assert machine().next_states("a") == ("b",)

    def test_can(self):
        m = machine()
        assert m.can("a", "b")
        assert not m.can("a", "c")

    def test_unknown_state_names_the_known_ones(self):
        with pytest.raises(UsageError, match="known states: a, b, c"):
            machine().state("nope")

    def test_states_with_role(self):
        assert machine().states_with_role("review") == ("b",)


class TestRequireEdge:
    def test_legal_edge_passes(self):
        machine().require_edge("a", "b")

    def test_illegal_edge_raises_and_lists_the_legal_ones(self):
        with pytest.raises(TransitionError, match=r"Legal from 'a': b"):
            machine().require_edge("a", "c")

    def test_terminal_state_reports_no_legal_successors(self):
        with pytest.raises(TransitionError, match=r"\(none — terminal\)"):
            machine().require_edge("c", "a")

    def test_structural_rejection_is_not_a_gate_failure(self):
        # The distinction is load-bearing: policy can downgrade a gate, but it
        # can never conjure an edge the machine does not declare.
        with pytest.raises(TransitionError):
            machine().require_edge("a", "c")


class TestGateInjection:
    def test_injects_by_role_not_by_name(self):
        m = machine().inject_gates(
            {"design": {"entry": ["docs.read_architecture"]},
             "review": {"exit": ["docs.updated"]}}
        )
        assert "docs.read_architecture" in m.state("a").entry_gates
        assert "docs.updated" in m.state("b").exit_gates

    def test_preexisting_gates_survive_injection(self):
        m = machine().inject_gates({"design": {"entry": ["docs.read_architecture"]}})
        assert m.state("a").entry_gates == ("g.one", "docs.read_architecture")

    def test_injection_is_idempotent(self):
        by_role = {"design": {"entry": ["docs.read_architecture"]}}
        once = machine().inject_gates(by_role)
        twice = once.inject_gates(by_role)
        assert once.state("a").entry_gates == twice.state("a").entry_gates

    def test_unknown_role_is_a_no_op(self):
        m = machine().inject_gates({"nonexistent": {"entry": ["g.x"]}})
        assert m.state("a").entry_gates == ("g.one",)

    def test_original_machine_is_untouched(self):
        original = machine()
        original.inject_gates({"design": {"entry": ["g.new"]}})
        assert original.state("a").entry_gates == ("g.one",)


class TestLoad:
    def test_load_from_file(self, tmp_path):
        path = tmp_path / "m.yaml"
        path.write_text(yaml.safe_dump(SOUND), encoding="utf-8")
        assert Machine.load(path).id == "demo"

    def test_missing_file(self, tmp_path):
        from invariant.core.errors import ManifestError

        with pytest.raises(ManifestError, match="no machine file"):
            Machine.load(tmp_path / "absent.yaml")

    def test_bad_yaml(self, tmp_path):
        from invariant.core.errors import ManifestError

        path = tmp_path / "m.yaml"
        path.write_text("id: [unclosed\n", encoding="utf-8")
        with pytest.raises(ManifestError, match="not valid YAML"):
            Machine.load(path)


def test_state_from_dict_defaults():
    s = State.from_dict("x", None)
    assert s.id == "x" and s.next == () and s.roles == ()
