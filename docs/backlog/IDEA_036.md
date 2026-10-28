---
id: IDEA_036
type: idea
title: Whole-suite and sweep evidence for every declared ecosystem, not only the primary
status: proposed
residue: []
history: []
area: domains/pipeline,domains/sweep,core/rules
priority: medium
---

**Context:** ADR-013 (BUG_097's fix) routes a scoped `pipeline test` or
`pipeline coverage` to the declared ecosystem that owns its selectors, so a
second ecosystem's red and scoped runs can be recorded and read by their own
adapter. It deliberately leaves the WHOLE-suite surfaces with the primary
ecosystem (ADR-013 D6):

* `pipeline test` and `pipeline coverage` with no `--selector` run the
  primary's `commands.test` / `commands.coverage` only.
* `invariant sweep run` runs, re-runs and reads the primary's suite only
  (`domains/sweep.py`, the adapter from `manifest.primary_ecosystem`).
* `test.all_pass` reads the last recorded run, whichever ecosystem it was.

So in a `[python, node]` project nothing ever demands, or records, that the
node suite is green as a whole, and under a sweep cadence a node file's
advisory coverage row is "held by the sweep" that never measures it. Nothing
here is lowered by ADR-013 (before it, the second ecosystem could not be run
at all), but the gap is real.

**Proposal:** whole-suite evidence for every declared ecosystem. Open
questions: one record per ecosystem in `test_runs` and how `test.all_pass`
reads several (every declared ecosystem's last full run green, not the last
run alone); whether `pipeline test` with no selector runs each ecosystem in
turn or takes `--ecosystem`; how a sweep record keys findings per ecosystem
and which adapter re-runs a red alone. Node coverage stays unreadable until
[BUG_018](BUG_018.md).

**Related:** [BUG_097](BUG_097.md), ADR-013, ADR-010, [BUG_031](BUG_031.md).
