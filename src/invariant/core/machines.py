"""Locating and assembling state machines.

Shipped machines live in ``data/machines``. A project may add its own via
``methodology.extra_machines``; a project machine with the same id shadows a
shipped one, so forking a methodology never means forking the library.

The docs-first gates are injected here rather than written into every
methodology file. ADR-001 D12 makes docs-first core policy, so a methodology
cannot opt out by omission — including a methodology this library has never
seen, which is why the injection is by state role rather than by state name.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ManifestError
from .fsm import Machine

MACHINE_DIR = Path(__file__).resolve().parent.parent / "data" / "machines"

#: Attached to every pipeline machine. Keyed by state role.
#:
#: The read gate is an EXIT gate on the design state, not an entry gate. A
#: pipeline starts *in* its design state, so an entry gate there would be
#: evaluated before any work had happened and would never be able to fail. The
#: check that means something is "you may not leave design until you have read
#: the docs", which is an exit condition.
DOCS_FIRST_GATES: dict[str, dict[str, list[str]]] = {
    "design": {"exit": ["docs.read_architecture"]},
    "review": {"exit": ["docs.updated", "docs.diagrams_current"]},
}

#: Attached the same way, and exit gates for the same reason — but BOTH hang
#: off the ``review`` role, including the one that asks about the *design*
#: agent. See ADR-003 D1.
#:
#: The docs-first note above turns on evidence the agent produces WHILE it
#: runs: a design agent satisfies ``docs.read_architecture`` by reading, and
#: the read is recorded the moment it happens. These gates turn on evidence
#: produced when the agent RETURNS — ``Task`` PostToolUse fires on return, so a
#: design agent that drives its own pipeline can never have its own dispatch on
#: record before it advances out of the design state. Placed there, the gate was
#: unsatisfiable for the one actor it names, and the only way past it was
#: ``--force`` (BUG_021).
#:
#: The review state's exit is the earliest boundary driven by a DIFFERENT
#: actor, which is what makes the evidence available: the parent dispatches the
#: review agent only after the design agent has returned. Reached at the cost
#: of the gate guarding *landing* rather than *leaving design* — accepted in
#: ADR-003 D2, because a gate that blocks at a moment nothing can satisfy is
#: not a stricter gate, it is a disarmed one.
#:
#: These stay ADVISORY in every shipped profile, including ``strict``. The
#: engine can prove an agent *ran*; it cannot prove one was *needed*, and a
#: one-line change made inline is a legitimate exception rather than a
#: violation. ``strict`` already holds that line for its other heuristics, on
#: the grounds that a blocking heuristic is a false positive with authority.
AGENT_ROLE_GATES: dict[str, dict[str, list[str]]] = {
    "review": {"exit": ["agents.design_agent_ran", "agents.review_agent_ran"]},
}


def shipped_ids() -> list[str]:
    return sorted(p.stem for p in MACHINE_DIR.glob("*.yaml"))


def find_machine_file(machine_id: str, manifest=None) -> Path:
    if manifest is not None:
        for candidate in manifest.get("methodology.extra_machines") or []:
            path = (manifest.root / candidate).resolve()
            if path.is_file() and path.stem == machine_id:
                return path
            if path.is_dir() and (path / f"{machine_id}.yaml").is_file():
                return path / f"{machine_id}.yaml"

    shipped = MACHINE_DIR / f"{machine_id}.yaml"
    if shipped.is_file():
        return shipped

    raise ManifestError(
        f"no state machine named {machine_id!r}; shipped machines are: "
        + ", ".join(shipped_ids())
        + ". Add a project machine with methodology.extra_machines."
    )


def load_machine(machine_id: str, manifest=None) -> Machine:
    return Machine.load(find_machine_file(machine_id, manifest))


def load_pipeline_machine(manifest, machine_id: str | None = None) -> Machine:
    """A pipeline machine with the docs-first gates attached.

    ``machine_id`` names the machine explicitly — a RUNNING pipeline is pinned
    to the methodology recorded on it at ``start``, not to whatever the
    manifest says today (AUD-02/AUD-03). Without it, the manifest's configured
    methodology is the answer, which is right for everything that has no
    pipeline record to read.
    """
    machine_id = machine_id or manifest.get("methodology.pipeline") or "tdd"
    machine = load_machine(machine_id, manifest)
    if machine.kind != "pipeline":
        raise ManifestError(
            f"machine {machine_id!r} is of kind {machine.kind!r}, not 'pipeline'"
        )
    return machine.inject_gates(DOCS_FIRST_GATES).inject_gates(AGENT_ROLE_GATES)


def load_session_machine(manifest=None) -> Machine:
    return load_machine("session", manifest)


def load_item_machine(item_type: str, manifest=None) -> Machine:
    machine = load_machine(item_type, manifest)
    if machine.kind != "item":
        raise ManifestError(
            f"machine {item_type!r} is of kind {machine.kind!r}, not 'item'"
        )
    return machine
