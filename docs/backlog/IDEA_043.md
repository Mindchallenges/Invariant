---
id: IDEA_043
type: idea
title: init assesses the architecture docs and offers to reuse, relocate or write
  them
status: proposed
residue: []
history: []
priority: high
effort: L
tier: '2'
area: cli/main, core/rules, domains, core/manifest
---

**Context:** reported by the user on 2026-11-06. They initialised invariant on a project whose `paths.docs` directory already existed. As designed (cli.md, `init`), init leaves an existing directory alone and writes the starter `overview.md` only when the directory is absent. So nothing happened. Nothing tells the user whether the docs that are there are enough for the docs-first gate (`docs.read_architecture`) and the design agents to work from. Nothing offers to put invariant's docs somewhere else, and nothing offers to write the missing docs. The user expected init to notice the existing directory and offer a choice, and in general to check that the architecture docs are comprehensive enough, and offer to create them if not.

Scope is invariant's architecture docs, the ones `paths.docs` names and the docs-first gate reads. It is not the project's own README or docs site, except where the user points invariant at existing documentation to adopt (below).

**Proposal (interview of 2026-11-06):**

* **A deterministic docs assessment the engine owns.** No model judgement in the check itself, so it is model-agnostic and gives the same verdict on every run. It checks, per area: the docs exist under the resolved docs location, carry the sections the starter defines (layers, component responsibilities, compliance checklist), and name each top-level source area the ecosystem adapter reports. Links between docs also resolve. The result is a list of gaps by area, not a single pass or fail.
* **It runs in three places:** at init, as a `doctor` row, and as its own command (for example `invariant docs assess`). The doctor row is informational by default, so an undocumented project does not turn doctor red. Its level is configurable like any other rule.
* **An existing directory gets a choice at init.** When `paths.docs` already exists, init offers to reuse it as invariant's docs, or to configure another location for invariant's docs, including an ADR-014 store (`records.docs`). Init only offers and never moves anything on its own.
* **Gaps get an offer, never a forced write.** Documenting a whole project at the start can be costly, so the user chooses:
  * **All at once:** a docs session, run through the normal pipeline, that writes every missing area.
  * **Incremental:** the gaps are recorded, and each area is documented when a session first touches it. The assessment then shrinks over time.
  * **Not now:** nothing is written. The gap list stays visible in doctor and in the assess command.
* **Adopting existing documentation.** The user can point invariant at documentation that already exists. Configuration then decides between two modes:
  * **In place:** invariant reads and updates that documentation where it lives.
  * **Copy:** invariant copies it into its own docs location, the repo path or the store, and edits the copy from then on.
* **Where created docs go:** wherever `paths.docs` / `records.docs` resolves. That is the repo by default, and the store when ADR-014 configures one. No separate setting.
* **Safe defaults keep today's behaviour.** A non-interactive init (`--dry-run`, `--json`, or no terminal) does exactly what it does now, and reports the assessment as text. Every offer is skipped there.

**Acceptance:**

* `init` on a project whose `paths.docs` exists and holds unrelated content asks whether to reuse it or to configure another location. Choosing a store results in `records.docs` pointing at it, and nothing in the existing directory changes.
* `init` on a project with no docs and with docs that cover half its source areas reports the gaps by area. In each case it offers all-at-once, incremental and not-now, and writes nothing unless one of the first two is chosen.
* `invariant docs assess` and the doctor row give the same gap list on two runs over an unchanged tree, and the list shrinks when a missing area's doc is added.
* With the incremental path chosen, a session touching an undocumented area is asked to document that area. An area already documented is not asked again.
* Pointing invariant at existing documentation in copy mode leaves the original untouched and makes the copy the one the docs-first gate reads. In in-place mode, the gate reads and the agents update the original.
* A non-interactive `init` on any of the above writes the same files as today and prompts for nothing.

**Architecture sketch:**

* A docs assessment in `domains/` (or the docs rules in `core/rules`) reading the resolved docs location and the adapter's source areas. It returns per-area gaps, and `doctor`, `init` and the new command all render the same result.
* `init`: the reuse-or-relocate offer when `paths.docs` exists, and the all-at-once / incremental / not-now offer. It writes `records.docs`, the adoption mode and the chosen path into the manifest.
* New settings, with names to settle in design: the adoption mode (in place or copy) with the source path, the onboarding path (all at once, incremental or off), and the assessment row's level. Defaults: off and informational, matching today.
* Incremental path: the recorded gap list, joined to `docs.read_architecture` / `docs.updated`, so a session touching a listed area owes that area's doc.
* `docs/architecture/cli.md` (`init`, `doctor`, a new docs command) and `manifest.md` (the new keys, `records.docs`).

**Risks / open questions:**

* What counts as "comprehensive" has to stay mechanical: required sections, every source area named, and links resolve. Anything beyond that is a judgement, and the user chose deterministic checks. The required sections and how areas are derived need to be configurable, because the starter's shape is not every project's.
* Incremental onboarding adds a new obligation to sessions. It must not make a session that touches an undocumented area unmergeable by default. The open question is whether it is a warn or a block, and who decides.
* In-place adoption of a project's own documentation can mean editing Sphinx or another non-markdown format (IDEA_041's adopters). `docs.format` and the docs lock (ADR-014 D5) have to cover that path.
* Copy mode forks the documentation. Whether invariant ever reports drift between the original and its copy is undecided.
* The all-at-once path is a large docs session. Its cost and its review lane (docs lane, chore lane) need placing in the methodology.
* Depends on ADR-014 / IDEA_040 for the store option. The reuse-or-relocate offer can ship before the store does, with only repo locations.

**Why now / why this tier:** every new adopter meets this on day one, and today the first experience is either silence (directory exists) or a starter template with no sense of what is missing. It also feeds the store adoption IDEA_040 and IDEA_041 are building. High value. Effort L: interactive init offers, a new assessment shared by three entry points, two adoption modes, and an incremental obligation wired into the docs gates. Tier 2 rather than 1 because of the size, and because it leans on ADR-014's store.

**Related:** [ADR-016](../adr/ADR-016-the-architecture-docs-are-assessed-and-init-offers-to-reuse-relocate-or-write-them.md) (the design, Proposed), IDEA_040 (home store; `records.docs`), IDEA_041 (docs repositories; in-place docs may be Sphinx), ADR-014 (records in a store, D5 docs lock), IDEA_004 (uninstall, the other end of adoption), BUG_002 and BUG_064 (gates inapplicable to docs-only changes), cli.md ("Setup": `init`, `doctor`), manifest.md, overview.md ("Where things live at rest").

**Design rulings (user, 2026-11-07, interview rounds 1 and 2 before the ADR):**

1. **Incremental path.** A session touching a source area with no architecture doc owes that area's doc through a new rule. It warns under `standard`, and config or the `strict` profile can raise it to block. A session is never unmergeable by default.
2. **Comprehensive, deterministically.** Three checks: required sections, every source area named, links resolve. Each check, the section list, and how areas are derived (the adapter's source roots, globs, or a declared list) are settings. The defaults are the starter `overview.md`'s shape.
3. **Copy drift.** At copy time, record the original's hashes. `doctor` and `docs assess` report originals changed since then, naming the files. Never re-sync unless the user re-runs the adoption.
4. **All at once.** `init` records a deliverable plan with one line per undocumented area. Each area is written and reviewed through the `docs` lane, so the cost stays visible and the work is resumable.
5. **Formats.** Section and link reading go through a per-format reader: markdown now, and rST (Sphinx) alongside ADR-015's sphinx adapter. Any other format is refused for in-place mode with a reason; copy mode still works for it.
6. **Config levels.** Every new key resolves at the user, store and repo levels, with provenance (ADR-014 D3).
7. **Increments.**
   1. The assessment, `invariant docs assess` and the doctor row, all read-only.
   2. `init`'s reuse-or-relocate and onboarding offers, with repo and store locations (the store through ADR-014's `store init` / `link`).
   3. The incremental rule.
   4. In-place and copy adoption, with drift reporting.
8. **Next step.** Draft the ADR from these rulings. Usage was nearly exhausted on 2026-11-07, so it was not drafted then.
