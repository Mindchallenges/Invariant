---
id: IDEA_034
type: idea
title: A sweep can opt in to filing its baseline's reds, grouped and capped
status: proposed
residue: []
history: []
priority: low
effort: M
area: domains/sweep
---

ADR-010 ruling 7 makes the first sweep on a branch a baseline that records
and files nothing, and that stays the default. Its consequence: a project
that adopts the sweep with a large pre-existing red set never sees those reds
as items — only reds that are new after the baseline are filed. Observed
2026-10-27 on a downstream iOS project whose baseline held 845 failing
tests; that set is now triaged by hand, outside the backlog the sweep keeps
for everything after it. The proposal: an opt-in — a manifest setting under
the sweep's schedule, or a `sweep run` flag — that files the baseline's reds,
grouped and capped so hundreds of reds do not flood the backlog, and keyed
by `sweep_key` so later sweeps never double-file them.

**Acceptance:**

* With the opt-in off (the default), a baseline sweep files nothing,
  exactly as today.
* With it on, a baseline sweep with N failing tests files a bounded number of
  items: one per test class or suite (each listing its failing tests), or one
  umbrella item with a checklist — never one item per test once N is large.
* Every failing test in the baseline is claimed by exactly one filed item's
  `sweep_key` set, so the next sweep reports it as still broken and files
  nothing new for it.
* Re-running a baseline sweep with the opt-in on, against open items that
  already claim those keys, files nothing.
* The sweep record's `filed` list names every item the baseline filed.

**Architecture sketch:**

* `domains/sweep` — a baseline branch of the filing step that groups failing
  ids (by class/suite from the test identity, else one umbrella) and caps the
  item count; reuses the existing dedup against open items.
* `core/sweep` — the grouping and cap as a pure function beside the diff.
* Manifest / CLI — one documented key (docs/architecture/manifest.md) or one
  flag (docs/architecture/cli.md); an ADR-010 amendment recording that ruling
  7 gained an opt-in.

**Risks / open questions:**

* `sweep_key` today is one key per item (`test:<id>`, `flaky:<id>`,
  `suite:red`); a grouped item must claim many keys. Decide whether an item
  carries a key list, or the group gets its own key (`class:<name>`) and the
  dedup learns that it claims its members.
* Grouping depends on the adapter exposing a class/suite from a test id;
  where `failing` is null (adapter cannot read identities) only the umbrella
  form is possible.
* Cap shape: a fixed limit with the overflow folded into the umbrella, like
  ruling 8's `suite:red` past the re-run cap.
* Setting or flag: a flag is one-shot and fits a one-time baseline; a setting
  would also apply to a new branch's baseline.

**Why now / why this tier:**

Parked by the user's call: the default stays, the pain is one-time per
adopting project and has a manual workaround, and the dedup-shape question
should be settled before anyone builds it.

**Related.** [IDEA_032](IDEA_032.md) (the sweep, done) and ADR-010
(docs/adr/ADR-010-whole-suite-evidence-moves-to-a-scheduled-sweep.md),
rulings 7 and 8; the sweep's filing and dedup in docs/architecture/state_model.md,
"Domain: sweep".
