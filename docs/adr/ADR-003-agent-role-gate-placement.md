# ADR-003 — Where an agent-role gate may sit, and what `hosts/` may hand upward

- **Status:** Accepted
- **Date:** 2026-08-14
- **Supersedes:** nothing. Corrects one placement decision recorded in
  `core/machines.py` and one dependency rule recorded in
  `docs/architecture/overview.md`.

## Context

`agents.design_agent_ran` is an exit gate on the state carrying the `design`
role, injected by `AGENT_ROLE_GATES`. This project sets it to `block`. The
agent it names — `feature-architect` — could not clear it. Every pipeline the
design agent drove itself left a forced override behind, and the reason
recorded in one of them (`67ca8b19`) is the whole defect in a sentence:
"feature-architect IS the running agent".

BUG_021 attributed this to two mechanisms: a timing race, and a claim that
dispatch evidence never reaches a worktree session's pipeline at all because a
`Task` payload carries no path. The second mechanism does not exist. A probe
repository settled it by execution:

| # | Experiment | Result |
|---|---|---|
| A | Design agent advances `designing→red` from inside its worktree | `[BLOCK] agents.design_agent_ran: no design agent ran in this pipeline` |
| B | `Task` PostToolUse fires from the **main checkout** on return | Recorded into the **worktree session's** pipeline |
| C | `agents_run` after B | `['feature-architect']` |
| D | Advance `designing→red` after the Task returned | `pipeline -> red (2 passed)` |

B and C are the important pair. The dispatch is attributed correctly across
roots, with no path in the payload, because `Manifest.state_root` resolves to
the git *common* directory — which every linked worktree of a repository shares
— and because the host session identifier in the payload resolves the session
directly. BUG_010's residue is therefore **not** a precondition for this fix,
and the entry's instruction to settle it first is withdrawn.

What remains is one fact with one consequence. `Task` PostToolUse fires when a
subagent **returns**. A gate that requires a subagent to have run therefore
cannot be cleared by that subagent while it is still running. The shipped
`feature-architect.md` instructs the design agent to run `invariant pipeline
start` and to advance the pipeline itself, so the design agent is structurally
the actor for its own gate, and the gate is structurally unsatisfiable for it.

Experiment D is the other half: once the dispatch has landed, the gate passes
with no code change at all. Nothing is broken in the recording. The gate is
merely standing one event too early for the only actor that has to pass it.

BUG_021 also reports that the gate is "not wired as an `exit_gate` anywhere in
`tdd.yaml`", and infers two enforcement sites with two meanings. That is a
misreading of the injection: `tdd.yaml`'s `designing` state declares
`roles: [design]`, and `load_pipeline_machine` injects the gate onto every
state carrying that role. There is one site and one meaning. No decision is
owed on that half.

## Decision

**Move the agent-role gates to the exit of the state carrying the `review`
role, and place both of them there.**

```python
AGENT_ROLE_GATES = {
    "review": {"exit": ["agents.design_agent_ran", "agents.review_agent_ran"]},
}
```

## Decisions recorded

### D1 — An agent-role gate sits where the agent it names has provably returned

A gate that reads "an agent ran" must be evaluated at a point the agent's
return has already passed. For a subagent whose dispatch is recorded on
return, the earliest such point is the next boundary driven by a *different*
actor.

`reviewing → committed` is that boundary. It is advanced by
`compliance-reviewer`, which the parent dispatches only after the design agent
has returned — so the design agent's dispatch is on record by then. The four
pipelines that recorded cleanly (`374f3afb`, `5155a7cc`, `6a063212`,
`8ec4f3ed`) are the evidence, and experiment D is the mechanism.

This inverts the placement argument in `machines.py:26`, which put the
docs-first read gate on the design state's exit because an entry gate there
"would be evaluated before any work had happened and would never be able to
fail". That argument is correct and still governs `docs.read_architecture`,
which the design agent satisfies by *reading* — an act it completes while
running. It does not transfer to a gate the agent satisfies by *returning*,
which is an act it cannot complete while running. The distinction is whether
the evidence is produced during the state or at the boundary of the actor's
own lifetime.

### D2 — The gate guards landing, not leaving design, and that is accepted

The cost is real and is recorded here rather than argued away: a pipeline may
now run `designing → red → green → refactor` with no design agent, and only be
caught on the way out of `reviewing`.

Accepted for three reasons.

The gate's purpose is to keep unreviewed-by-procedure work from **landing**,
not to sequence the author's morning. `reviewing → committed` is where work
lands, so the gate still stands between the failure it names and the harm that
failure causes.

A gate that blocks at the wrong moment is not a stricter gate, it is a
disarmed one. Three of the nine pipelines on this machine carry a forced
override of exactly this gate. `--force` is not selective; the operator who
learns it here uses it on `privacy.clean` next. Per BUG_002, an unsatisfiable
blocking gate teaches the override, and the override is the thing that has to
be kept expensive.

Every shipped methodology that declares a `design` role also declares a
`review` role — verified across all five (`tdd`, `bugfix`, `chore`, `docs`,
`spec-first`). No shipped methodology loses the gate by this move. A
project-authored machine with a design state and no review state does lose it,
and that is the documented consequence of choosing a methodology with no
review boundary.

### D3 — `agents.design_agent_ran` stays a distinct rule from `agents.review_agent_ran`

Both now hang off the same state's exit, so collapsing them into one rule that
checks both roles would be a smaller table. Rejected: they answer about
different roles, fail with different remedies, and a project retunes them
independently through `enforcement.rules`. Co-location is a fact about where
evidence is available, not about what is being asked.

### D4 — `hosts/` own hook transport; documented payload keys travel upward

The overview's rule — "Nothing above `hosts/` is aware of hook schemas" — is
false as written, and experiment B is what makes it load-bearing rather than
merely stale. Attribution across roots works *because* `Context.session_for_payload`
reads `tool_input.file_path` and `core/rules.RuleContext` reads `tool_name`,
`tool_input` and `target_path`. A reader enforcing the sentence as written
would read `faa4de0` as a layering violation and could "correct" a shipped fix
by reverting it.

The rule is restated as BUG_022 proposed:

> `hosts/` own hook *transport* — payload parsing, response shape, settings
> wiring. A normalised payload dict travels upward and `cli/` and `core/rules`
> read documented keys from it (`tool_name`, `tool_input.file_path`); no layer
> above `hosts/` knows the hook *protocol*.

The alternative — moving the payload reads back below the seam — was rejected
on cost: it re-opens `faa4de0`, and the layering it would restore is one the
codebase has already, deliberately and correctly, departed from. What the
restatement buys is a named contract: a host adapter for a different agent host
now has an explicit normalisation obligation rather than an implied one.

## Consequences

`core/machines.py` carries one table entry instead of two, and its docstring
gains the design-state exception rather than losing the docs-first argument.

The shipped `feature-architect.md` no longer needs the parent to take over the
pipeline boundary. The agent keeps driving its own pipeline, and the gate it
must pass now sits after it returns. This is what makes the fix a mechanism
rather than a convention: nothing has to be remembered by a human or an agent
for the gate to be satisfiable.

`invariant pipeline gates` reports different owings per state, so a reader who
has memorised which state owes what will find the agent gates listed later.
The three pipelines carrying forced overrides of this gate keep them; an
override is a record of what happened and is not rewritten by a later decision.

A methodology with a design role and no review role silently loses the design
gate. Accepted under D2, and the gate-naming test in `test_machines.py` does
not catch it because it asserts over shipped machines only.

## Alternatives considered

**Record the dispatch at request time (PreToolUse) rather than on return.**
This is the only option that keeps the gate on the design state's exit while
still being mechanical: `_pre_tool_use` writes `agents_dispatched` at session
scope, and `pipeline start` seeds a new pipeline's evidence from dispatches
made while no pipeline was running. Rejected on two counts. It dilutes the
evidence — "dispatched" counts an agent that died on its first tool call, and
`rules.py:738` says these gates exist to read "what happened, not what was
intended". And it turns a read-only evaluation hook into a writer, so a
**denied** tool call could record evidence for the gate it was denied by,
which needs a guard that is easy to omit and invisible when omitted.

**Leave the gate where it is and change the protocol so the parent owns the
pipeline boundary.** Zero code; experiment D already passes. Rejected because
it makes a *blocking* gate's satisfiability rest on a convention nothing
enforces. The next parent that forgets is wedged exactly as before, and reaches
for `--force` exactly as before — the BUG_002 shape this entry was filed
against.

**Have the gate abstain when the actor advancing is the agent it names.**
Cheapest of all, and rejected outright: it requires the agent to identify
itself, which is self-attestation, and a gate satisfied by assertion is the
thing this rule family exists to replace.

## Notes

The historical anomaly in BUG_021 — `feature-architect` absent from
`67ca8b19`'s `agents_run` entirely, rather than merely late — is **not**
explained by this ADR and could not be reproduced. Experiment B records the
dispatch reliably under the same shape. Recorded as unexplained residue on the
entry rather than folded into a cause it does not support, per the standard
BUG_013 set: a rendered cause must be entailed by the evidence behind it.
