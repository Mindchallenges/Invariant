"""The declarative state-machine loader and validator.

One schema and one loader serve every machine in the library — methodologies,
the session lifecycle, and work-item lifecycles — so that a project-authored
machine is exactly as capable as a shipped one.

The separation that matters here: this module decides what is *structurally*
possible, never what is *currently permitted*. An edge that a machine does not
declare cannot be taken at any enforcement level. A gate that fails is a
separate question, resolved by policy, and lives in ``core.rules``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import ManifestError, TransitionError, UsageError


@dataclass(frozen=True)
class State:
    """One node. Gates name rule ids; roles let gates be injected by kind."""

    id: str
    description: str = ""
    entry_gates: tuple[str, ...] = ()
    exit_gates: tuple[str, ...] = ()
    next: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, state_id: str, raw: Mapping[str, Any] | None) -> "State":
        raw = raw or {}
        if not isinstance(raw, Mapping):
            raise UsageError(f"state {state_id!r} must be a mapping")
        return cls(
            id=state_id,
            description=str(raw.get("description", "")),
            entry_gates=tuple(raw.get("entry_gates", ()) or ()),
            exit_gates=tuple(raw.get("exit_gates", ()) or ()),
            next=tuple(raw.get("next", ()) or ()),
            roles=tuple(raw.get("roles", ()) or ()),
        )


@dataclass(frozen=True)
class Machine:
    """A validated state machine."""

    id: str
    kind: str
    initial: str
    states: Mapping[str, State]
    terminal: frozenset[str] = field(default_factory=frozenset)
    description: str = ""
    #: The state whose job is proof-of-red, for a machine that declares
    #: ``test.new_test_fails``. ``None`` means the machine has not said, and
    #: the rule falls back to the state literally called ``red``.
    #:
    #: The rule used to carry that name as a constant. It was written against
    #: ``tdd``, which has a ``red`` state, and declared by ``bugfix``, which
    #: runs ``triaging -> reproducing -> fixing`` and has no such state and no
    #: legal transition to one — so its own exit gate was satisfiable only by
    #: labelling a run with a state the pipeline had never been in (BUG_030).
    #: Renaming ``reproducing`` to ``red`` was rejected: the state is named for
    #: what it proves, and renaming a state to satisfy a string comparison is
    #: the shape this library files bugs against.
    red_phase: str | None = None

    # -- construction --------------------------------------------------

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Machine":
        if not isinstance(raw, Mapping):
            raise UsageError("machine definition must be a mapping")
        for required in ("id", "initial", "states"):
            if required not in raw:
                raise UsageError(f"machine is missing required key {required!r}")

        raw_states = raw["states"]
        if not isinstance(raw_states, Mapping) or not raw_states:
            raise UsageError(f"machine {raw['id']!r} declares no states")

        machine = cls(
            id=str(raw["id"]),
            kind=str(raw.get("kind", "generic")),
            description=str(raw.get("description", "")),
            initial=str(raw["initial"]),
            terminal=frozenset(raw.get("terminal", ()) or ()),
            red_phase=(str(raw["red_phase"]) if raw.get("red_phase") else None),
            states={
                state_id: State.from_dict(state_id, body)
                for state_id, body in raw_states.items()
            },
        )
        problems = machine.validate()
        if problems:
            raise UsageError(
                f"machine {machine.id!r} is invalid:\n  - "
                + "\n  - ".join(problems)
            )
        return machine

    @classmethod
    def load(cls, path: Path) -> "Machine":
        path = Path(path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ManifestError(f"no machine file at {path}") from exc
        except yaml.YAMLError as exc:
            raise ManifestError(f"{path} is not valid YAML: {exc}") from exc
        return cls.from_dict(raw)

    # -- validation ----------------------------------------------------

    def validate(self) -> list[str]:
        """Return every structural problem. Empty list means sound."""
        problems: list[str] = []
        known = set(self.states)

        if self.initial not in known:
            problems.append(f"initial state {self.initial!r} is not defined")

        for terminal in sorted(self.terminal - known):
            problems.append(f"terminal state {terminal!r} is not defined")

        if self.red_phase is not None and self.red_phase not in known:
            problems.append(f"red_phase {self.red_phase!r} is not defined")

        for state in self.states.values():
            for target in state.next:
                if target not in known:
                    problems.append(
                        f"state {state.id!r} lists unknown successor {target!r}"
                    )
            if not state.next and state.id not in self.terminal:
                problems.append(
                    f"state {state.id!r} is a dead end but is not marked terminal"
                )

        if self.initial in known:
            unreachable = known - self._reachable_from(self.initial)
            for state_id in sorted(unreachable):
                problems.append(
                    f"state {state_id!r} is unreachable from {self.initial!r}"
                )

        return problems

    def _reachable_from(self, start: str) -> set[str]:
        seen = {start}
        stack = [start]
        while stack:
            for target in self.states[stack.pop()].next:
                if target in self.states and target not in seen:
                    seen.add(target)
                    stack.append(target)
        return seen

    # -- queries -------------------------------------------------------

    def state(self, state_id: str) -> State:
        try:
            return self.states[state_id]
        except KeyError:
            raise UsageError(
                f"machine {self.id!r} has no state {state_id!r}; known states: "
                + ", ".join(sorted(self.states))
            ) from None

    def is_terminal(self, state_id: str) -> bool:
        return state_id in self.terminal

    def next_states(self, state_id: str) -> tuple[str, ...]:
        return self.state(state_id).next

    def can(self, source: str, target: str) -> bool:
        return target in self.state(source).next

    def require_edge(self, source: str, target: str) -> None:
        """Raise unless the edge exists. Structural, not policy."""
        if not self.can(source, target):
            legal = ", ".join(self.next_states(source)) or "(none — terminal)"
            raise TransitionError(
                f"{source!r} -> {target!r} is not a transition {self.id!r} "
                f"declares. Legal from {source!r}: {legal}"
            )

    def states_with_role(self, role: str) -> tuple[str, ...]:
        return tuple(s.id for s in self.states.values() if role in s.roles)

    # -- gate injection ------------------------------------------------

    def inject_gates(
        self, by_role: Mapping[str, Mapping[str, list[str]]]
    ) -> "Machine":
        """Return a copy with extra gates attached to states by role.

        This is how a cross-cutting concern that every methodology must carry
        — the docs-first pair, for instance — is applied without each
        methodology file having to remember it. Roles rather than state names
        keep the injection working against machines this library has never
        seen.
        """
        states = dict(self.states)
        for role, gates in by_role.items():
            entry = tuple(gates.get("entry", ()) or ())
            exit_ = tuple(gates.get("exit", ()) or ())
            for state_id in self.states_with_role(role):
                current = states[state_id]
                states[state_id] = replace(
                    current,
                    entry_gates=_dedupe(current.entry_gates + entry),
                    exit_gates=_dedupe(current.exit_gates + exit_),
                )
        return replace(self, states=states)


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    """Order-preserving dedupe — gate order is the order they are reported in."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return tuple(out)
