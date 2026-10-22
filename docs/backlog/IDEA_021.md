---
id: IDEA_021
type: idea
title: 'The project plan is the default source of session state: plan next, and plan-derived
  handoff notes'
status: proposed
residue: []
history: []
priority: high
effort: L
tier: '3'
area: cli/main, core/manifest, hosts/claude_code
---

**The want.** The project's plan becomes the default source of session state,
so a handoff note no longer has to carry "what is next". Today the note is the
only place that state lives, which makes notes long (see
[BUG_075](BUG_075.md)) and lets them drift from the plan they restate. There
is no `plan` command today. Maintainer direction, 2026-10-20.

**Proposed shape.**

1. A plan-derived next item, e.g. `invariant plan next`. A manifest setting
   names the plan file and how to read item status, so the rule is not
   specific to one project. The reporting project's rule: the next item is
   the first non-DONE item in the lowest phase.
2. Session start and hook context show the derived next item by default.
3. `session handoff` without `--note` derives the note from the plan: the
   next item plus the live worktrees.
4. Optional: a handoff check that flags note items that are tracked in
   neither the backlog nor the plan.

**Acceptance:**

* With a plan declared in the manifest, `invariant plan next` prints the
  first not-done item of the lowest phase. With every item done it says so.
  With no plan declared it exits with a message naming the manifest key.
* The SessionStart greeting (and `invariant context`) shows that next item
  when a plan is declared, and nothing new when none is.
* `session handoff` with no `--note` records a note naming the plan's next
  item and each live worktree. An explicit `--note` still wins.
* A plan whose status cannot be read (unknown status word, missing file) is
  reported as unreadable, never as "nothing next".
* (Optional) The handoff check names any note line that matches no backlog
  item and no plan item.

**Architecture sketch:**

* `core/manifest`: a plan key (file path, status-reading rule).
* A plan reader behind a small protocol, so the status rule is configuration,
  not code per project.
* `cli` (a new `plan` group; see [cli.md](../architecture/cli.md)) and
  `session handoff`; `hosts/claude_code` greeting context.

**Risks / open questions:**

* How general can "how to read item status" be? A regex over lines, a
  sphinx_needs-style directive option, a markdown checkbox? Start with one
  shape and say which.
* Is "lowest phase" a general concept, or should the ordering rule itself be
  configured?
* The derived note must pass the same privacy screening as a typed one.
* A derived note keeps the typed line short only once BUG_075's temp-file fix
  lands. The two compose and neither blocks the other.

**Related:** [BUG_075](BUG_075.md) (long handoff notes fail to launch; this
idea shortens them, BUG_075 makes any length safe);
[IDEA_008](IDEA_008.md) (handoff note lifecycle).

**Why now / why this tier:**

Maintainer direction, and it removes the class of long, drifting notes behind
BUG_075. L effort: a new command group, a manifest key, greeting and handoff
integration. Tier 3: a new concept (the plan as state) across cli, manifest
and the host.
