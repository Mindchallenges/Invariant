---
id: IDEA_020
type: idea
title: Warn before a gated-role agent is dispatched in a session with no pipeline
  running
status: done
residue: []
history:
- from: proposed
  to: accepted
  reason: user approved the ordered fix queue (BUG_075, IDEA_019, BUG_074, IDEA_020)
  forced: false
- from: accepted
  to: in_progress
  reason: work started in session 7be8db18
  forced: false
- from: in_progress
  to: done
  reason: shipped in b5612ce
  forced: false
priority: medium
effort: S
tier: '5'
area: hosts/claude_code, core/rules
---

**The want.** An agent in a gated role (`agents.roles` design or review) is
sometimes dispatched in a session with no pipeline running. Today the only
notice arrives at PostToolUse ("was NOT recorded as evidence: session has no
pipeline running"), after the agent has already done its work. A pipeline
started later then finds its `agents.*_ran` gate unmet, and the whole dispatch
has to be repeated. The want is a warning at PreToolUse, before the dispatch,
so the operator can start the pipeline first or dispatch knowingly. See
[state_model.md](../architecture/state_model.md), "Agent-role gates".

**Decided (2026-10-20):** a warning only, never a block. Agent runs recorded
against the session are NOT carried over to a later pipeline: evidence is
per-pipeline on purpose, and this idea does not reopen that.

**Acceptance:**

* Dispatching an agent whose role is design or review, in a session with no
  running pipeline, prints a warning before the agent runs. The warning names
  the agent, its role, and that its run will not count as evidence for a
  later pipeline.
* The dispatch still proceeds. The warning does not block, ask, or need an
  override.
* An agent in a non-gated role (e.g. the backlog curator) gets no warning.
* With a pipeline running, a gated-role dispatch gets no warning. The
  existing PostToolUse recording is unchanged.
* The PostToolUse "NOT recorded as evidence" notice still fires as today.

**Risks / open questions:**

* The role lookup at PreToolUse must read the same `agents.roles` mapping the
  gates use, or the warning and the gate can disagree.
* Under a stand-down, should the warning stay quiet like other enforcement,
  or still speak because it is advisory?

**Related:** [BUG_040](BUG_040.md) (a dispatch from a host session rooted in
another repository is recorded nowhere: a different reason a run fails to
count).

**Why now / why this tier:**

Medium value: it saves a wasted full design or review dispatch, which is the
most expensive step in a pipeline. S effort: one PreToolUse branch in
`hosts/claude_code.py` `_pre_tool_use`, reusing the role lookup in
`core/rules`. Tier 5.
