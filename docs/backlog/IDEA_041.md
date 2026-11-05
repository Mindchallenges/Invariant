---
id: IDEA_041
type: idea
title: 'Docs repositories get gates: a Sphinx adapter, commands derived from CI'
status: proposed
residue: []
history: []
priority: high
effort: L
tier: '2'
area: adapters, core/manifest, domains/sweep, core/rules, setup
---

**Context:** invariant's gates assume a code project. A documentation repository has no test suite or coverage in that sense: doctor fails on unset `commands.build` and `commands.test` (IDEA_040, "Engine work known so far"), and no adapter knows what a build or a test means for a Sphinx project. The first adopters are the user's four Sphinx and sphinx-needs documentation repositories, the same ones IDEA_040 moves records out of. Their CI already defines how each one is built and checked, but invariant has no way to read that and has to be told by hand.

**Proposal:** a docs ecosystem adapter, gate commands proposed from the repo's own CI and kept current, and a time budget that moves a heavy full run to the scheduled sweep. Rulings from the user interview of 2026-11-05; the design is settled in ADR-015 (being drafted, Proposed), alongside ADR-014 for IDEA_040's store.

* **A docs/Sphinx ecosystem adapter** (ADR-013's adapter contract). Build is a strict `sphinx-build` (warnings as errors). Test is a link check plus sphinx-needs validation. Coverage is not applicable, and the adapter says so explicitly, so the coverage gates report "not applicable" rather than failing or passing silently.
* **CI derivation at setup.** invariant reads the repo's CI definition (GitHub Actions, GitLab CI, a Jenkinsfile and so on), proposes build and test commands, and the user accepts them into config. Gates run only accepted commands, never a derived one the user has not seen.
* **An update command** reruns the derivation when the pipeline, setup or scripts change, and proposes the differences for acceptance. The user asked for this explicitly. The engine also detects drift between the CI inputs and what was last derived, and says so, so a stale derivation does not go unnoticed.
* **Heavy tests.** Derivation measures how long the full run takes and judges whether a full run is needed per change. Above a configurable budget, the session gate runs only change-related tests and the full suite moves to the scheduled sweep. This generalises ADR-010's split from coverage to any suite past the budget.

**Acceptance:**

* One of the four documentation repos, with the docs ecosystem declared, passes doctor with no hand-written build or test command, and a session through gates and merge runs the strict build, the link check and sphinx-needs validation. Coverage gates report "not applicable, by the adapter", not a pass or a failure.
* Setup on a repo with a CI definition proposes build and test commands drawn from it, and nothing is written to config or run by a gate until the user accepts. A rejected proposal leaves config unchanged.
* After a change to the CI definition or a script it calls, invariant reports that the derivation is stale. The update command shows the proposed differences and applies only what the user accepts.
* A suite whose measured full run exceeds the configured budget gates each session on change-related tests only and runs in full on the sweep; one under the budget keeps running in full per session. The budget and the measured time are both visible in config or status.
* A warning-producing Sphinx build fails the build gate; a broken link or an invalid need fails the test gate, naming what failed.

**Architecture sketch:**

* A new adapter under `adapters/` implementing ADR-013's contract: `default_commands`, `parse_test_output` for the link check and needs validation, and an explicit "not applicable" for coverage.
* A CI reader per CI format feeding a proposal; accepted commands land in the manifest's `commands.*` (or `commands.<eco>.*` when the docs ecosystem is not the primary).
* A record of what was last derived and from which inputs, so drift is a comparison, not a re-derivation.
* Change-related test selection for docs (pages, needs and links touched by the diff), and the budget wired into ADR-010's cadence split.

**Risks / open questions (for ADR-015):**

* How generic CI reading can be: CI files call scripts, matrices and reusable actions. The ADR should say what is read, what is only reported as "could not derive", and that a partial derivation is a proposal, never a silent default.
* What counts as a change-related test for docs: a link check is cheap per page, but sphinx-needs validation may need the whole project. Derivation has to judge this per repo.
* How "coverage not applicable" sits with the coverage gates and `sweep.no_full_run` without becoming a general bypass for code projects.
* Whether the docs ecosystem is ever a secondary one (`commands.<eco>.*`) or, in the first adopters, always the primary.
* Measuring the full run's duration needs a full run at setup; on a heavy suite that is itself slow.

**Why now / why this tier:** part of the same adoption as IDEA_040: the four repos cannot pass doctor or gate a session without it. High value; effort L because it spans a new adapter, CI reading across several formats with an accept-and-update flow, drift detection, and a generalisation of ADR-010. Tier 2 rather than 3 because it extends existing frameworks (adapters, the sweep) rather than redefining where records live.

**Related:** IDEA_040 (home store; same adopters, sibling ADR-014), ADR-015 (being drafted), ADR-010 (whole-suite evidence moves to a scheduled sweep), ADR-013 (each declared ecosystem runs through its own adapter), IDEA_032, IDEA_036 (whole-suite evidence per ecosystem), manifest.md ("Adapters", "Several ecosystems", "A sweep cadence"), BUG_002, BUG_064 (gates inapplicable to a docs-only change).
