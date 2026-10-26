---
id: IDEA_032
type: idea
title: Whole-suite and whole-scheme coverage move to a scheduled sweep
status: done
residue: []
history:
- from: proposed
  to: accepted
  reason: null
  forced: false
- from: accepted
  to: in_progress
  reason: null
  forced: false
- from: in_progress
  to: done
  reason: null
  forced: false
priority: high
effort: L
tier: '1'
area: core/rules, core/manifest, cli, domains/items
---

**The want.** A project can move whole-suite and whole-scheme coverage evidence
off the per-change path and onto a scheduled sweep, and the engine enforces the
split rather than prose. A consuming project (Heap) ruled on 2026-10-26: a
change never runs the whole test suite or whole-scheme coverage, in any case;
the full suite plus coverage runs once a week; every new red or floor drop
becomes a follow-up work item.

**Why.** In that project `coverage.meets_floor` is block with
`coverage_scope: touched`, but its coverage command cannot be narrowed with
`-only-testing`, because a scoped run is only a lower bound: one file measured
65% scoped against 97% whole-scheme (that project's own coverage-scope defect).
So every change that touches shared code is forced into a whole-scheme run of
45 minutes or more. `test.all_pass` is already warn there.
`methodology.gates.coverage_advisory_paths` ([IDEA_028](IDEA_028.md)) covers
only new code under declared subtrees, so it cannot answer this.

**Proposed shape.**

1. **Scoped per-change evidence.** A change's test evidence comes from its
   affected suites. `test.new_test_fails` (RED-first) still blocks, unchanged.
2. **Gates on a cadence.** The manifest can put `coverage.meets_floor` and
   `test.all_pass` on a schedule. Per change, `coverage.meets_floor` then
   reports the scoped figure as advisory, or is satisfied by a fresh sweep; it
   never blocks on a scoped lower bound. Once a cadence is configured, no
   per-change gate may require whole-suite or whole-scheme evidence, and
   manifest validation refuses a config that would.
3. **Full-run detection.** The engine detects a session running the full suite
   or the whole-scheme coverage command on a change, and refuses or warns
   (level decided in design).
4. **Sweep command** (name decided in design, e.g. `invariant sweep run`): runs
   the full suite plus coverage, diffs against the last recorded sweep, and
   files each new red or floor drop through `invariant item` with the commit
   range since the last sweep. An item still broken from an earlier sweep is
   not filed twice.
5. **Freshness rule.** When the last recorded sweep is older than the cadence
   (7 days) plus a grace margin, the engine warns. It does not block.
6. **Manifest surface.** A cadence value; which gates move to the schedule; an
   optional path-glob allowlist that still owes a pre-merge full run (the
   consuming project has not ruled on it; default empty). This generalises
   `coverage_advisory_paths` rather than adding a parallel mechanism; whether
   that key is kept or retired is a design question.

**v1 scope (user ruling 2026-10-26).** IN: cadence mode for
`coverage.meets_floor` and `test.all_pass` (items 1 to 3 and 6), the sweep
command with diff and auto-filing (4), the freshness rule (5). OUT, fast
follow: automatic bisect of a new red to its commit; until then the commit
range is always recorded on the filed item.

**Acceptance:**

* With a cadence configured, a change touching shared code lands on its
  scoped evidence alone; `coverage.meets_floor` shows the scoped figure as
  advisory and does not block, while a new test that does not fail first
  still blocks.
* A manifest that configures a cadence and also leaves a per-change gate
  requiring whole-suite or whole-scheme evidence is refused at load, naming
  the gate.
* A session that runs the full suite or whole-scheme coverage command on a
  change is refused or warned, by the configured level.
* The sweep command, run twice across a commit range that introduces one
  failing test and one floor drop, files exactly two items, each carrying the
  commit range; a third run with both still broken files nothing new.
* With the last sweep older than 7 days plus the grace margin, the engine
  warns on every per-change check and names the age; it never blocks for it.

**Architecture sketch:**

* Cadence and gate-scheduling rules in `core/` beside the existing coverage
  levers ([overview.md](../architecture/overview.md), "Component
  responsibilities"); the per-file advisory/held split already used by
  `coverage_advisory_paths` is the seam to generalise.
* A new sweep subcommand ([cli.md](../architecture/cli.md)) that files
  through the work-item domain.
* Cadence, scheduled gates and the allowlist in the manifest schema
  ([manifest.md](../architecture/manifest.md)), with `doctor` naming an
  active cadence.
* Sweep records stored as evidence so freshness and the diff have a baseline.

**Related:**

* [IDEA_028](IDEA_028.md) (done) is generalised and superseded by this.
* [IDEA_031](IDEA_031.md) sibling: shares the A3 ratchet idiom; no dependency.
* [IDEA_011](IDEA_011.md) sibling coverage lever.
* [BUG_040](BUG_040.md) sibling, not a blocker.
* Background on unsatisfiable coverage gates: [BUG_026](BUG_026.md),
  [BUG_036](BUG_036.md), [BUG_039](BUG_039.md), [BUG_085](BUG_085.md).

**Order.** User ruling 2026-10-26: this idea comes first on this host, then
[BUG_040](BUG_040.md) together with [IDEA_031](IDEA_031.md) A0.

**Risks / open questions:**

* Whether "this run is the whole suite" can be detected per ecosystem without
  false positives, and whether detection refuses or warns by default.
* The grace margin's length.
* Keep or retire `coverage_advisory_paths` once cadence mode lands.
* Whether the pre-merge full-run allowlist is wanted at all (no ruling yet).
* A flaky test in the sweep (e.g. [BUG_081](BUG_081.md)) would be filed as a
  new red; dedup by test identity has to tolerate that.

**Closing obligation.** When the engine part lands, record the landing commit
in this entry and send a short note back to the Heap session named
s-1-sync-foundations saying it has shipped.

**Landed 2026-10-26.** v1 merged to main at ac73b18, in three increments:
e417275 (cadence mode, manifest validation, held_paths, coverage_one and
--selector, sweep.no_full_run), 8fbf143 (invariant sweep run|status|show,
committed records under docs/sweeps, auto-filing with sweep_key and
sweep_range) and ac73b18 (sweep.fresh, the advisory verdict naming the last
sweep, agent guidance, docs). Design: ADR-010. Fast follow: automatic bisect.

**Why now / why this tier:**

High value: every change in the consuming project that touches shared code
currently pays a 45-minute-plus whole-scheme run. L effort (cross-layer: rules,
manifest, a new command, evidence store). T1 Ship next, priority high, by the
user's call.
