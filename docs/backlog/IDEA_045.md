---
id: IDEA_045
type: idea
title: Architecture and design live only in a strict-building Sphinx-needs project,
  in every managed project
status: proposed
residue: []
history: []
priority: high
effort: XL
tier: '1'
area: domains/docs, adapters (the ADR-015 sphinx adapter), core/items, cli/main, core/rules
---

**Asked by the user, 2026-11-08:** "I want the architecture of the projects with the documentation first rule that we are enforcing in the projects managed by invariant to be done on Sphinx needs. And this is a must. Also, the documentation must stay buildable. And invariant does not create or let Claude create design related documents in anywhere else out of the Sphinx needs documentation. For any projects, the reason behind is I want to leverage from the ID structure of Sphinx needs and having the more traceable documentation that we can remember our decisions in future." Also: "This has been already enforced on the heap project and worked really well." And: "all other invariants development or sessions must finish their jobs and wait for this decision to be implemented. This is very important here."

**Problem:** today the docs-first rule only says that architecture docs exist and are updated. Where design lives, and in what shape, is left open. Decisions are scattered across Markdown ADR files, backlog item bodies, plans and agent reports. Nothing gives a decision a stable ID, nothing links it to the element it shapes, and nothing checks that the docs still build. A decision made months ago can only be found by searching. The Heap project already keeps its architecture as sphinx-needs (rST, typed element needs, typed links, items as needs) and it has worked well. The user wants that to be the rule for every project invariant manages.

**What:** every invariant-managed project keeps its architecture and design as a buildable Sphinx project with sphinx-needs, and invariant refuses design content anywhere else. This is a rule, not an option. It amends ADR-016 (the Markdown starter, and docs offers that are "offered, never forced") for the docs format, and builds on ADR-015's sphinx adapter and the existing sphinx_needs items backend.

**Rulings (user interview, three rounds, 2026-11-08; decided):**

1. Scope: every invariant-managed project, invariant itself included. A must, not a configurable option.
2. Source format: reStructuredText.
3. Buildable is a gate: a strict sphinx-build (`-W`, plus sphinx-needs `needs_warnings`) runs on every change that touches the docs.
4. Existing projects without sphinx-needs docs (invariant, dang, and Heap's gaps) are owed a migration session. Until it lands, design content written elsewhere warns. After it lands, it blocks.
5. Need types in v1: decisions (each ADR and each of its D-rulings), architecture elements, requirements/specs, bugs, ideas, guideline rules.
6. "Design" means architectural and software design plus UI decisions (screens, flows, design-system choices), all as needs. Visuals and mockups may live in design tools, but only linked from a need, never as the record of a decision.
7. Refused homes for design content: standalone ADR files outside the Sphinx project (for example `docs/adr/*.md`); plans and agent reports (drafts are allowed, but the session cannot merge until its decisions are written as needs); design text in backlog items (items keep problem and status, and rulings move to decision needs linked by ID). Memory files and external docs are not refused homes.
8. Need-type schema: an engine baseline (decision, requirement, element types, bug, idea, rule) with fixed prefixes the gates rely on, plus per-project extension types in configuration (like Heap's view/service/model types).
9. Traceability gate: the strict build plus configured required-link rules (for example, every decision links to an element it shapes, and every bug links to the element it affects).
10. Diagrams: PlantUML and Mermaid are both allowed, chosen case by case.

**Acceptance:**

* `invariant init` on a new project writes a Sphinx + sphinx-needs rST docs project with the baseline need types, and a strict build of it passes. No Markdown architecture starter is offered.
* A change that breaks the strict docs build (a Sphinx warning, a sphinx-needs warning, a dangling need link) cannot land.
* A decision need with no link to an element it shapes, or a bug need with no link to the element it affects, fails the traceability gate with the rule named.
* A session that writes design content to a refused home (a `docs/adr/*.md` file, a design ruling in a backlog item body, an undrafted plan or report decision) warns before its project's migration lands and blocks after it. A session holding draft decisions in a plan or report cannot merge until they exist as decision needs.
* invariant itself is migrated: its ADR-001..ADR-017 and their D-rulings, its architecture docs, its ideas, bugs and guideline rules are needs in a strict-building Sphinx project. Its config reads `docs.format: sphinx` and `items.backend: sphinx_needs`.
* A project can declare extension need types in configuration (for example view/service/model) and the gates accept them next to the baseline.

**Architecture sketch:**

* A new ADR, amending ADR-016 (docs format becomes Sphinx/rST, mandatory) and building on ADR-015 (sphinx adapter) and ADR-012 (guideline rules become needs).
* Docs domain: the baseline need-type schema with fixed prefixes, configured extension types, required-link rules, and the refused-homes check.
* The sphinx adapter (ADR-015, not built yet): a strict build command as a gate on docs-touching changes.
* The sphinx_needs items backend becomes load-bearing for every project: BUG_073 (history never persisted) and any BUG_006 residue must be closed first or as part of it.
* init and the ADR-017 questionnaire: the docs-format question goes away (it is fixed), and the starter becomes a Sphinx project.
* Migration: a per-project migration session (invariant, dang, and Heap's gaps: no decision/requirement/rule types, and a Makefile build without `-W`), with the warn-then-block switch.

**Reference implementation:** Heap's `docs/conf.py` (sphinx_needs + plantuml, rST, element types COMP_/VIEW_/SVC_/MOD_/UTIL_/IF_/DF_, IDEA_/BUG_ as needs, typed links uses/contains/presents/persists_to, ADRs as rST pages under `docs/architecture/adr`; `items.backend: sphinx_needs`, `docs.format: sphinx`).

**Risks / open questions:**

* Fixed prefixes for the baseline types versus projects that already use other prefixes (Heap uses COMP_ and others; is the element baseline one type or several?). For the ADR design interview.
* How the refused-homes check tells design text in a backlog item body apart from a problem statement. A mechanical check versus a judgement guideline.
* Migrating invariant's 17 ADRs, Markdown architecture docs and about 160 Markdown items to rST needs is large. A converter may be needed, and BUG_073 must be fixed before items move.
* The sphinx-build toolchain becomes a dependency of every managed project, including non-Python ones (dang is Flutter).
* The interaction with ADR-014 stores (records outside the repository) and IDEA_043's reuse/relocate offers.

**FREEZE — this item blocks all other open invariant work.** User ruling, 2026-11-08: every other invariant development item and session finishes its current job and waits until this is implemented. The live session 4b3af513 (ADR-017 increment 2) may finish its increment. Nothing new starts until this item is done. Blocks: all open IDEA_ and BUG_ items in this backlog, including IDEA_041, IDEA_043, IDEA_044 and IDEA_040.

**Related:** [ADR-018](../adr/ADR-018-architecture-and-design-live-only-in-a-strict-building-sphinx-needs-project.md) (the design, accepted 2026-11-09; rulings 1 to 10 of 2026-11-08 and 11 to 19 of 2026-11-09 are recorded there, with the increment plan), IDEA_041 / [ADR-015](../adr/ADR-015-docs-repositories-get-an-adapter-and-commands-derived-from-ci.md) (the sphinx adapter this builds on), IDEA_043 / [ADR-016](../adr/ADR-016-the-architecture-docs-are-assessed-and-init-offers-to-reuse-relocate-or-write-them.md) (amended: Markdown starter and "offers never forced" for the docs format), IDEA_044 / [ADR-017](../adr/ADR-017-init-asks-the-configuration-as-a-questionnaire-and-configure-asks-it-again.md) (the docs-format question becomes fixed), IDEA_040 / [ADR-014](../adr/ADR-014-a-project-s-records-can-live-in-a-store-outside-its-repository.md) (where the docs project and its needs live), [ADR-012](../adr/ADR-012-the-guidelines-domain-and-the-model-agnostic-contract.md) (guideline rules become needs), BUG_006 and BUG_073 (sphinx_needs backend defects that become load-bearing).

**Why now / why this tier:** the user ruled it a must for every project and froze all other invariant work until it lands. Highest value: every decision in every managed project gets a stable, linked, buildable home. The rubric alone would put an XL architectural item in Tier 3. It is Tier 1 on the user's explicit ruling.
