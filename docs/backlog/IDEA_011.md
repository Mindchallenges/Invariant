---
id: IDEA_011
type: idea
title: A file can declare its coverage ceiling, distinct from a ratcheted shortfall
status: proposed
residue: []
history: []
priority: medium
effort: M
area: core/rules, core/manifest
---

**The want.** A project can declare, per path and in a reviewable diff, that a
file's coverage CEILING sits below the project floor, for a reason that is a
property of the ecosystem rather than of any change: e.g. SwiftUI view bodies
and `Button` action closures, which no test runner in that ecosystem executes.
The shape is an explicit key in `methodology.gates` (see
[manifest.md](../architecture/manifest.md)), beside `non_source`,
`coverage_exclude` and `coverage_baseline`, and a `coverage.meets_floor`
verdict that names a held file as "held to its declared ceiling", distinct
from a ratcheted shortfall.

**Why not `coverage_baseline`.** The 2026-10-21 repro of BUG_054 showed
`coverage_baseline` can already hold such a file: at a global floor of 90, a
file pinned to 14 passes at 14.83%, blocks at 10%, and its neighbours still
bind at 90. So this is about meaning and reviewability, not capability.
`coverage_baseline` is documented and worded as "pre-existing shortfall,
ratcheted", i.e. debt to pay down. The consuming project's baseline-file header
forbids seeding it with a permanent ceiling, and the verdict cannot tell a
measured ceiling from an ordinary gap. A ceiling is a standing claim about
the file, not debt, and a reviewer should see it as one.

**Evidence.** BUG_054's symptom table: four view files in the consuming project
at 0-15% under a floor of 90, so a copy-only change that authored only 100%
covered lines could not land. The same project's ratchet record shows a
complete extraction refactor of a sibling view topping out near 89.

**Acceptance:**

* A project can name one exact path and a ceiling number for it. That file
  passes at or above its ceiling and blocks below it, while every other
  touched file is still held to the global floor.
* The verdict for a file held by a ceiling says so, in wording distinct from a
  `coverage_baseline` hold. A reader can tell "declared ceiling" from
  "ratcheted shortfall" without opening the manifest.
* A ceiling never widens to a directory: a glob or a directory path is refused
  at manifest load with a message naming the key.
* A ceiling entry with no stated reason is refused (if the builder settles on
  a required reason; see below).
* A file with no ceiling entry behaves exactly as today. Neither
  `coverage_baseline` nor `coverage_exclude` changes meaning.

**Design questions for the builder:**

* Does a ceiling ratchet, so that it may only go up once the file measures
  higher, the way `coverage_baseline` tightens?
* How does it order with `non_source`, `coverage_exclude` and
  `coverage_baseline` in `core/rules.py::_coverage_meets_floor`? BUG_054's fix
  sketch says ordering is the whole of the safety: it is asked LAST, and only of
  what the floor already rejected.
* Must each entry carry a reason (the ecosystem property that caps it)?
* Should a path that is in both `coverage_baseline` and the ceiling key be
  refused as a double statement?
* Path spelling goes through BUG_071's `project_relative` normaliser, so `x`
  and `./x` are one entry.

**Related:** BUG_054 (origin; closed not_reproducible 2026-10-21, the engine
already holds a per-path number); BUG_026 (the coverage levers); BUG_071 (path
normaliser).

**Why now / why this tier:**

Medium value: it unblocks view-heavy consuming projects honestly, without
abusing a debt ledger. M effort: one manifest key, one rule branch whose
ordering must be proven, and verdict wording.
