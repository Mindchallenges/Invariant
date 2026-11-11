"""Locating and assembling state machines.

Shipped machines live in ``data/machines``. A project may add its own via
``methodology.extra_machines``; a project machine with the same id shadows a
shipped one, so forking a methodology never means forking the library.

The docs-first gates are injected here rather than written into every
methodology file, and so is ``sweep.fresh`` beside each gate a sweep cadence
schedules, ``docs.area_documented`` beside the update gate on a project onboarding
its docs incrementally, and the ``guidelines.*`` landing gates on the landing state of a project
with a guidelines ruleset (``guidelines.baseline_only_shrinks`` only when it keeps a
baseline too), and ``agents.rendered_current`` there when the caller holds a host
adapter's agent install plan. ADR-001 D12 makes docs-first core policy, so a methodology
cannot opt out by omission — including a methodology this library has never
seen, which is why the injection is by state role rather than by state name.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ManifestError
from .fsm import Machine

MACHINE_DIR = Path(__file__).resolve().parent.parent / "data" / "machines"

#: The role a state carries when reaching it means the work is being DROPPED.
#: Read by the gate collector, which returns only such a state's own entry
#: gates: the exit gates of the state being left exist to protect an outcome,
#: and this is the edge with no outcome to protect (BUG_037). Named as a role
#: rather than matched on the id ``abandoned`` for the same reason the other
#: injections are: a machine this library has never seen labels its own states.
ABANDON_ROLE = "abandon"

#: The role of the state a pipeline LANDS in: the role the landing gates are
#: injected by, and the one whose entry records the landing's guidelines
#: decisions in pipeline evidence (ADR-012 DN-7, A4.6).
LANDING_ROLE = "landing"

#: Attached to every pipeline machine. Keyed by state role.
#:
#: The read gate is an EXIT gate on the design state, not an entry gate. A
#: pipeline starts *in* its design state, so an entry gate there would be
#: evaluated before any work had happened and would never be able to fail. The
#: check that means something is "you may not leave design until you have read
#: the docs", which is an exit condition. It is also the one gate here whose
#: subject really is *leaving*: every edge out of design goes forward or
#: abandons, so there is no retreat for it to hold hostage.
#:
#: The update gate is the opposite shape, and used to be written the same way.
#: It rode the ``review`` role's EXIT set, so it fired on the retreats a review
#: state declares as well as on the landing — and a review that discovers the
#: change is unfinished was then asked to update the architecture docs before
#: it could go back and finish it (BUG_037). It now rides ``landing`` as an
#: ENTRY gate. Same moment on the shipping edge, absent on every other.
DOCS_FIRST_GATES: dict[str, dict[str, list[str]]] = {
    "design": {"exit": ["docs.read_architecture"]},
    "landing": {
        "entry": [
            "docs.updated",
            "docs.diagrams_current",
            # The docs project's strict build and its traceability (ADR-018
            # D5, D6): on every lane, N/A where the change touches neither the
            # source directory nor docs.needs, and on a migrating project
            # whose docs are not yet sphinx (D10).
            "docs.builds_strict",
            "docs.needs_traceable",
            # The session's declared decisions are needs, the items it changed
            # name only held design ids, and the switch is not walked back
            # (ADR-018 D8, D9, D10). Each N/A where it has nothing to judge.
            "docs.decisions_recorded",
            "items.decisions_linked",
            "docs.mandate_kept",
        ]
    },
}

#: Attached to the landing state's ENTRY beside the update gate, and only on a
#: project whose ``docs.onboarding`` is ``incremental`` (ADR-016 D6). The rule
#: is the incremental path's: each source area the change touches that no
#: architecture doc names is owed. Conditional for the reason ``sweep.fresh``
#: is: under any other mode the rule is N/A, and ``pipeline gates`` never lists
#: a rule that cannot apply, so a project that chose nothing lands exactly as
#: before. Read from the manifest as it stands, never pinned: the mode is the
#: project's standing choice, not a property of one pipeline. It warns in
#: ``loose`` and ``standard`` and blocks in ``strict`` (ruling 1).
DOCS_ONBOARDING_GATES: dict[str, dict[str, list[str]]] = {
    "landing": {"entry": ["docs.area_documented"]},
}

#: Attached the same way — and BOTH hang off the landing state, including the
#: one that asks about the *design* agent. See ADR-003 D1, and D2's BUG_064
#: amendment for which machines receive each.
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
#: The landing edge is the earliest boundary driven by a DIFFERENT actor,
#: which is what makes the evidence available: the parent dispatches the review
#: agent only after the design agent has returned. Reached at the cost of the
#: gate guarding *landing* rather than *leaving design* — accepted in ADR-003
#: D2, because a gate that blocks at a moment nothing can satisfy is not a
#: stricter gate, it is a disarmed one.
#:
#: These were the ``review`` role's exit gates until BUG_037, which is the same
#: sentence with a different failure: an exit gate fires on every edge out of
#: the state, retreats included, so ``reviewing -> red`` was refused by
#: ``agents.review_agent_ran``. Moving them to the landing state's ENTRY is
#: what ADR-003 D2 already said in words — the gate guards LANDING — written so
#: that it is also what the machine does.
#:
#: These stay ADVISORY in every shipped profile, including ``strict``. The
#: engine can prove an agent *ran*; it cannot prove one was *needed*, and a
#: one-line change made inline is a legitimate exception rather than a
#: violation. ``strict`` already holds that line for its other heuristics, on
#: the grounds that a blocking heuristic is a false positive with authority.
#:
#: Keyed by the role a machine must DECLARE for the gates under it to be
#: injected (BUG_064); each value is an ordinary by-role injection. The design
#: gate requires a ``design`` state. A methodology with none — ``chore`` — is
#: asserting the work has no design content, so "did a design agent run" has
#: nothing to ask there, exactly as ``docs.read_architecture`` has nothing to
#: ask. Before ADR-003 moved it the design gate sat on the design state and was
#: absent from ``chore`` by construction; moving it to landing widened it to
#: every machine, which was never decided. The review gate requires only the
#: landing state it rides, so its reach is what it was. Either way a gate is on
#: the machine or not, and ``pipeline gates`` shows which.
AGENT_ROLE_GATES: dict[str, dict[str, dict[str, list[str]]]] = {
    "design": {"landing": {"entry": ["agents.design_agent_ran"]}},
    "landing": {"landing": {"entry": ["agents.review_agent_ran"]}},
}


#: Injected beside every gate a sweep cadence has scheduled (ADR-010 D6,
#: ruling 10). Keyed by GATE id rather than by role, because the concern
#: belongs to the gate: wherever a machine asks for ``coverage.meets_floor``
#: or ``test.all_pass`` and a cadence has moved that gate's whole-run claim to
#: the sweep, the same transition reports how old the sweep holding it is. No
#: cadence, no injection, so ``pipeline gates`` never lists a rule that cannot
#: apply. The rule only ever warns; the manifest refuses a stronger level.
FRESHNESS_GATE = "sweep.fresh"


#: Attached to the landing state's ENTRY, and only on a project that has
#: configured a guidelines ruleset (ADR-012 D3, D12, ruling 7). Conditional
#: for the reason ``sweep.fresh`` is: with no ruleset every ``guidelines.*``
#: rule is N/A, and a ``block``-level rule that cannot apply renders as
#: ``[skip:BLOCK]`` on every landing, which would be a change on every project
#: that has not opted in. With a ruleset, the landing is where a malformed one
#: stops a change, and where drift between the ruleset and what render wrote
#: from it is reported (``guidelines.generated_current``, warn in every
#: profile, ruling 7). ``guidelines.mechanical_clean`` holds new findings of
#: the pattern, boundary and cochange guidelines there (A3 increments 3 and 4); it warns in
#: ``loose`` and ``standard`` and blocks in ``strict``. So does
#: ``guidelines.verdicts_complete`` (A4 increment 3), which asks whether every
#: judgement guideline the change owes a verdict has one that still holds; it
#: rides the landing entry by role, as ``agents.review_agent_ran`` does (D5).
#: A running pipeline that pinned a ruleset at ``start`` keeps them whatever
#: the manifest says later (``guidelines=True``).
GUIDELINES_GATES: dict[str, dict[str, list[str]]] = {
    "landing": {
        "entry": [
            "guidelines.ruleset_valid",
            "guidelines.generated_current",
            "guidelines.mechanical_clean",
            "guidelines.verdicts_complete",
        ]
    },
}


#: Attached to the landing state's ENTRY beside ``GUIDELINES_GATES``, and only
#: when ``guidelines.baseline`` is configured as well (ADR-012 D6, D12, A3
#: increment 6). Conditional for the reason ``sweep.fresh`` is: with no
#: baseline the rule is N/A, and it blocks in every profile, so it would
#: render as ``[skip:BLOCK]`` on every landing of a project that keeps no
#: baseline. It compares the baseline in the change with the base branch's
#: committed copy and refuses growth DN-6 does not admit, and a stale entry
#: (DN-5). The baseline key is read from the manifest as it stands, never
#: pinned: clearing it leaves no baseline to honour, which shrinks the record
#: rather than widening it.
BASELINE_GATES: dict[str, dict[str, list[str]]] = {
    "landing": {"entry": ["guidelines.baseline_only_shrinks"]},
}


#: Attached to the landing state's ENTRY wherever ``host.kind`` names a host
#: adapter (ADR-012 D8, the A6 ruling 10). Which kinds have one is the host
#: layer's answer, and ``core/`` imports no host, so the caller says: the
#: pipeline domain injects it when the CLI handed it the adapter's install
#: plan (``host_agents=True``). Conditional for the reason ``sweep.fresh`` is:
#: with no adapter the rule is N/A, and ``pipeline gates`` never lists a rule
#: that cannot apply. It warns in every profile, strict included, and only
#: reports: nothing installs at the landing.
AGENT_CURRENCY_GATES: dict[str, dict[str, list[str]]] = {
    "landing": {"entry": ["agents.rendered_current"]},
}


#: Attached to the design state's EXIT, and only in a store project (ADR-014
#: D5, increment 4c-1): the docs are read, designed against and written under
#: one lock, and the units are released before implementation, so the
#: session's code work runs in parallel with other sessions' doc cycles. An
#: exit gate on design, as ``docs.read_architecture`` is, and by role, so
#: every lane with a design state carries it. Conditional for the reason
#: ``sweep.fresh`` is: an in-repo project has no docs lock, the rule is N/A
#: there, and it blocks in every profile, so it would render as
#: ``[skip:BLOCK]`` on every design state of invariant and Heap.
STORE_LOCK_GATES: dict[str, dict[str, list[str]]] = {
    "design": {"exit": ["store.docs_lock_released"]},
}


def shipped_ids() -> list[str]:
    return sorted(p.stem for p in MACHINE_DIR.glob("*.yaml"))


def pipeline_ids(manifest=None) -> list[str]:
    """Every lane a project may name in `methodology.pipeline`, sorted: the
    shipped machines and `methodology.extra_machines` of kind ``pipeline``.

    Ids are file stems, as ``find_machine_file`` finds them, so a project
    machine shadowing a shipped one is listed once. An extra path that does
    not exist offers nothing.
    """
    files = [*MACHINE_DIR.glob("*.yaml")]
    if manifest is not None:
        for candidate in manifest.get("methodology.extra_machines") or []:
            path = (manifest.root / candidate).resolve()
            if path.is_file():
                files.append(path)
            elif path.is_dir():
                files.extend(path.glob("*.yaml"))
    ids = {path.stem for path in files if Machine.load(path).kind == "pipeline"}
    return sorted(ids)


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


def load_pipeline_machine(
    manifest,
    machine_id: str | None = None,
    *,
    guidelines: bool | None = None,
    host_agents: bool = False,
) -> Machine:
    """A pipeline machine with the docs-first gates attached.

    ``machine_id`` names the machine explicitly — a RUNNING pipeline is pinned
    to the methodology recorded on it at ``start``, not to whatever the
    manifest says today (AUD-02/AUD-03). Without it, the manifest's configured
    methodology is the answer, which is right for everything that has no
    pipeline record to read.

    ``guidelines`` is the same pin for the ``guidelines.*`` landing gates:
    ``True`` for a pipeline that pinned a ruleset at ``start``, so clearing
    ``guidelines.ruleset`` mid-pipeline cannot drop them. ``None`` asks the
    manifest, which is right for a pipeline that pinned nothing and for a
    record that predates the pin.

    ``host_agents`` says the caller holds a host adapter's install plan for
    the library agents, so ``agents.rendered_current`` can be answered and
    rides the landing entry (``AGENT_CURRENCY_GATES``).
    """
    machine_id = machine_id or manifest.get("methodology.pipeline") or "tdd"
    machine = load_machine(machine_id, manifest)
    if machine.kind != "pipeline":
        raise ManifestError(
            f"machine {machine_id!r} is of kind {machine.kind!r}, not 'pipeline'"
        )
    _require_a_landing_state(machine)
    machine = machine.inject_gates(DOCS_FIRST_GATES)
    if manifest is not None and manifest.get("docs.onboarding") == "incremental":
        machine = machine.inject_gates(DOCS_ONBOARDING_GATES)
    if manifest is not None and getattr(manifest, "store", None) is not None:
        machine = machine.inject_gates(STORE_LOCK_GATES)
    for required_role, by_role in AGENT_ROLE_GATES.items():
        if machine.states_with_role(required_role):
            machine = machine.inject_gates(by_role)
    if guidelines is None:
        guidelines = manifest is not None and bool(manifest.get("guidelines.ruleset"))
    if guidelines:
        machine = machine.inject_gates(GUIDELINES_GATES)
        if manifest is not None and manifest.get("guidelines.baseline"):
            machine = machine.inject_gates(BASELINE_GATES)
    if host_agents:
        machine = machine.inject_gates(AGENT_CURRENCY_GATES)
    scheduled = manifest.scheduled_gates() if manifest is not None else frozenset()
    return machine.inject_beside(scheduled, FRESHNESS_GATE)


def _require_a_landing_state(machine: Machine) -> None:
    """A machine with a review state must say which state is the landing.

    Injection is by role, so a role nothing carries injects nothing — silently.
    That is fine for ``design``: ``chore`` declares no design state because it
    is asserting there is no design content, and neither the read gate nor the
    design-agent gate applying is the point (BUG_064). It is NOT fine for ``landing``, because the gates that ride it
    are the ones docs-first policy says a methodology cannot opt out of
    (ADR-001 D12). A machine that declares a review state and forgets the
    landing role would ship with `docs.updated` and both agent gates absent and
    nothing to say so — the omission invisible exactly where it matters, which
    is the failure ``test_every_registered_rule_is_graded_by_every_profile``
    exists to prevent one layer over.

    Asked only of machines that HAVE a review state: `none` is a single
    ungated state by design and is not being asked to grow one.
    """
    if not machine.states_with_role("review"):
        return
    if machine.states_with_role("landing"):
        return
    raise ManifestError(
        f"pipeline machine {machine.id!r} declares a 'review' state but no "
        f"'landing' state. The gates a methodology cannot opt out of — "
        f"docs.updated and the agent-role gates — are injected as the landing "
        f"state's ENTRY gates, so without one they would be absent and nothing "
        f"would report it. Add 'roles: [landing]' to the state a finished "
        f"change lands in."
    )


def load_session_machine(manifest=None) -> Machine:
    return load_machine("session", manifest)


def load_item_machine(item_type: str, manifest=None) -> Machine:
    machine = load_machine(item_type, manifest)
    if machine.kind != "item":
        raise ManifestError(
            f"machine {item_type!r} is of kind {machine.kind!r}, not 'item'"
        )
    return machine
