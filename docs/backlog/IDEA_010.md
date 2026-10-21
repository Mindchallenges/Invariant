---
id: IDEA_010
type: idea
title: A dispatched agent can ask, before doing its work, whether its session can
  write the work-item store
status: proposed
residue: []
history: []
priority: medium
effort: M
area: cli/main, data/agents
---

**The want.** An agent whose job ends in a write to the work-item store
(`backlog-curator`, `bug-reproducer`) can ask, as its FIRST step, whether its
session is able to make that write, and bail in seconds if not. Today a refusal
surfaces only at the first write, after the agent has read the backlog,
interviewed, scored and drafted, so each occurrence costs a whole agent run
(BUG_032). The shape is a cheap pre-flight on the CLI
([cli.md](../architecture/cli.md)), e.g. `invariant context --can-write items`
or a `doctor` row, which the agent definitions under `agents/`
([overview](../architecture/overview.md)) call before Phase 1.

**Why it is not just invariant's own rules.** BUG_032's 2600-call matrix showed
invariant itself allows these writes, so the refusal reported there most likely
came from the HOST's own permission layer. A pre-flight that consults only
invariant's rules would answer "yes" in exactly the case that motivated it. The
answer must cover both layers, or say plainly which one it could not see.

**Acceptance:**

* An agent that cannot write the work-item store learns so from one command
  that returns in seconds, before it reads the backlog or asks the user
  anything.
* The answer distinguishes "invariant refuses (rule named)", "host refuses",
  "both allow" and "invariant allows; host unknown" — it never reports "allowed"
  for a layer it did not actually probe.
* The probe leaves no trace: no item file, no history row, no session state
  change, no residue in the store after it runs (the BUG_059 lesson).
* `backlog-curator` and `bug-reproducer` run the pre-flight first and, on a
  refusal, bail with a report naming the refusing layer instead of starting
  their interview or repro.
* In a session where the write is allowed, the pre-flight adds no visible
  noise to the agent's normal run.

**Design question for the builder.** How to probe the host layer honestly
without performing a real write. Candidates: an inert probe write inside the
store (created and removed, reusing the `InertStore` mechanism from BUG_059
where it fits); a dry-run hook payload; or, when neither can tell, reporting
"invariant allows; host unknown" rather than guessing.

**Risks / open questions:**

* A probe write the host permits may itself prompt the user for permission —
  which is a refusal-in-waiting the pre-flight should surface, but must not
  turn into an extra prompt on every healthy run.
* The host's permission decision can differ by tool (Edit vs Bash-driven CLI
  write); the probe must exercise the same path the agent's real write uses,
  or it answers a different question.
* "Host unknown" must not decay into a silent pass; agents need a defined
  behaviour for it (proceed with a warning, most likely).

**Related:** BUG_032 (origin — its entry asks for exactly this pre-flight);
BUG_059 (`InertStore`, a reusable no-trace probe mechanism).

**Why now:** each occurrence loses a full agent run, and it retires the
folklore "dispatch filing agents from a non-isolated session".
