---
id: IDEA_022
type: idea
title: Retire a closed item into items.closed_path on its closing transition
status: proposed
residue: []
history: []
priority: medium
effort: L
tier: '5'
area: core/items, domains/workitem, cli/main
---

**The want.** When an item reaches a closing status, `invariant item
transition` moves the entry out of the open store and into the closed store
that `items.closed_path` declares, in the same transition. This is the
deferred half of [IDEA_019](IDEA_019.md) (done, cf9f356).
[BUG_074](BUG_074.md) (fixed, 07ee5c7) made the key real, but only as
something to read: id minting and `item new`'s explicit-id check count the ids
retired there, and nothing writes it. Today a project with a closed store has
to retire an entry by hand, outside the FSM. The record of the move then sits
in the one store the gates cannot see (state_model.md, "The store the gates
cannot sit in front of"). The read side has the same gap: `item list`,
`item show` and `item transition` read only the open store, even when
`closed_path` is set, so a retired entry cannot be listed, shown or looked up
through the CLI.

**Proposed shape.** A closing transition on a backend with `closed_path` set
writes the entry, with its whole body, history and residue unchanged, into the
closed store and removes it from the open store. The entry ends up in exactly
one of the two stores. With `closed_path` unset, behaviour is unchanged: the
markdown backend grows no archive. The read verbs consult both stores.

**Acceptance:**

* With `closed_path` set, transitioning an item to a closing status leaves it
  in the closed store and not in the open store. Its body, history and residue
  are byte-identical to what the open store held, plus the new history row.
* A write that lands in one file and not the other is either impossible or
  detected and recoverable: no run leaves an entry in both stores, or in
  neither, without a command that names the state and the way out.
* `item list` (with closed statuses), `item show <id>` and `item transition`
  find an entry that lives in the closed store.
* With `closed_path` unset, every existing test and every markdown backlog
  behaves exactly as before.

**Architecture sketch:**

* `core/items`: a backend-level retire (write to closed, remove from open)
  and a read path that covers both stores.
* `domains/workitem`: `WorkItemDomain.transition` calls retire on entering a
  closing status, after the gates pass.
* `core/manifest`: `items.closed_path` already exists (manifest.md, "The ids a
  store has spent, and the closed store"). No new key is expected.

**Risks / open questions:** Recorded by feature-architect in
[state_model.md](../architecture/state_model.md) under "Retirement is
deferred".

* (a) **Atomicity.** The move touches two files. It needs to be one atomic
  outcome, or it needs a recovery path for a write that lands in one file and
  not the other, e.g. write closed first, then remove open, with a check that
  detects an id present in both stores.
* (b) **Placement in the closed file.** The closed store has its own layout,
  so where an entry goes in it is unresolved. The `--section` / `--after`
  placement BUG_074 (b) added for the open store may be reusable here.
* (c) **Prerequisite: [BUG_073](BUG_073.md).** The only backend that wants a
  closed store today is sphinx_needs, and it does not persist item history.
  Until BUG_073 is fixed, retiring an entry there would carry its ruling and
  history into the archive as nothing.

**Why now / why this tier:**

It closes the gap IDEA_019 and BUG_074 left open, but only sectioned backends
with a closed store need it, and it waits on BUG_073. That makes it a Tier 5
item at medium priority, not Tier 2.
