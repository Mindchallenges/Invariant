---
id: IDEA_019
type: idea
title: A maintainer-ruled closure edge for an already-fixed bug, carrying its provenance
status: proposed
residue: []
history: []
priority: high
effort: M
tier: '2'
area: data/machines/bug.yaml, core/items
---

**The want.** A bug can close as "already fixed" through the engine, with a
record of who ruled it and on what evidence. Today
`data/machines/bug.yaml` (the bug machine, see
[state_model.md](../architecture/state_model.md), "Domain: work item") allows only `filed -> reproduced |
not_reproducible | wont_fix`, and `fixed` is reachable only from
`reproduced`. Sometimes `bug-reproducer` returns ALREADY-FIXED: the test
passes on main, goes red with the fix reverted, and the fixing commit is
named. The only engine route from `filed` is then `not_reproducible`, which
records the wrong meaning ("we tried and it did not fire", not "it was real
and is fixed"). A consuming project hand-edited `:status:` instead, which
left no record of the ruling at all.

**Proposed shape.**

* An explicit edge that carries provenance. Either `invariant item transition
  <id> fixed --ruling "<who/date>" --evidence <commit>`, legal from `filed`
  only when both are given, or a dedicated `already_fixed` edge or state. The
  ruling and evidence go into the history row.
* Optional retirement in the same transition: when the backend declares a
  closed store, a closing transition moves the entry there verbatim, in one
  write.
* A stated guarantee, enforced by tests on every backend, that a transition
  never drops or rewrites body text.

**Acceptance:**

* A `filed` bug with a named fixing commit and a ruling can reach a closed
  "fixed" meaning in one engine command, and its history shows the ruling, the
  evidence commit and that it skipped `reproduced`.
* The same command without a ruling or without evidence is refused, and the
  message names what is missing. `filed -> fixed` stays illegal otherwise.
* `not_reproducible` is never the only route for an already-fixed verdict.
  The item list and `item show` tell the two apart.
* On a backend with a declared closed store, a closing transition with
  retirement leaves the entry in exactly one store, its body byte-identical.
* A transition round-trip test on each backend proves body text survives
  unchanged.

**Architecture sketch:**

* `data/machines/bug.yaml`: the new edge or state, and its entry gate
  (ruling and evidence present).
* `core/items`: history-row fields for ruling and evidence; a backend-declared
  closed store and an atomic move. This is the same declaration BUG_074 (c)
  needs, so agree the key once.
* `domains/workitem` and `cli` `item transition` (see
  [cli.md](../architecture/cli.md), "Work items"): the flags.

**Risks / open questions:**

* A red test with the fix reverted arguably IS a reproduction. An alternative
  is to record `reproduced` (evidence: revert-red) and then `fixed`
  (evidence: the commit) in one ruled command. That needs no new edge, but it
  writes a reproduction no-one ran on the shipped tree. The builder picks one
  and says why.
* Does the ruling need a human, or is a `bug-reproducer` verdict enough?
  Today only `bug-reproducer` may record `reproduced`.
* Is retirement a backend capability or a manifest key? It must not make the
  markdown backend (one file per item, no archive) grow an archive it does
  not want.
* Body-text survival was probed and held. The only rewriting seen was
  BUG_065's (a wrapped option reflowed, a blank line lost), now fixed.

**Related:** [BUG_065](BUG_065.md) (prerequisite: the transition must not
rewrite the entry before it can be trusted to move one);
[BUG_073](BUG_073.md) (sphinx_needs drops history, where the ruling would
live); [BUG_074](BUG_074.md) (the closed-store declaration);
[BUG_066](BUG_066.md) (an earlier lifecycle gap for repro-only sessions).

**Why now / why this tier:**

The reporting project's curator is hand-editing status today, which defeats
the lifecycle's audit trail. M effort: one machine edge, two history fields,
and a backend capability. Tier 2: medium value with a strong unlock (an
honest record for every ALREADY-FIXED verdict).
