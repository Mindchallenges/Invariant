---
id: IDEA_031
type: idea
title: invariant owns the format, lifecycle and enforcement of coding guidelines
status: proposed
residue: []
history: []
priority: high
effort: XL
tier: '2'
area: domains, adapters, hosts, manifest, docs/adr
---

**The want.** invariant owns the format, the lifecycle and the enforcement of
a project's coding guidelines, including the guideline parts of its
`CLAUDE.md`. The project keeps owning the content. Today a consuming project
(Heap) carries its compliance rules as prose: a 36-rule list plus a
Boy-Scout rule, enforced by hand-edited agent copies and by whichever model
happens to read them. Nothing checks that a rule was applied, a verdict
cannot be audited against the diff, and editing a rule is ungated.

**Standing goal: invariant is model-agnostic.** Determinism comes from the
engine (state machines, mechanical checks, schema-validated evidence), never
from pinning a model. Gates judge evidence, not which model produced it. This
extends [ADR-001](../adr/ADR-001-scripted-state-over-prose-rules.md)
(scripted state over prose rules) to the guidelines themselves, and
[ADR-003](../adr/ADR-003-agent-role-gate-placement.md) (where an agent-role
gate may sit) to per-rule verdicts.

**Deliverables (the numbering carries the build order):**

* **A0 — ADR (docs lane).** The guidelines domain and the model-agnostic
  contract: ruleset file format and location; three check kinds, pattern,
  boundary (import layering) and judgement; a verdict schema with
  citations; capability tiers as hints only; a host-neutral agent format.
* **A1 — Conformance harness.** Fixed scenarios with known verdicts (a seeded
  violation, a missing test, a docs-first breach, a vague verdict), runnable
  through any host and model. The same verdict is expected on every run.
* **A2 — Ruleset core.** Loader, `doctor` validation, a generated rst
  checklist and managed, marker-delimited `CLAUDE.md` sections, and a drift
  rule for a hand-edited generated file.
* **A3 — Mechanical checkers.** Pattern and boundary checks per ecosystem
  adapter (regex first), with a baseline file and a ratchet modelled on the
  coverage ratchet and on [IDEA_028](IDEA_028.md).
* **A4 — Verdict contract.** A per-rule verdict schema from the design and
  review agents; citations validated against the diff; the gate blocks on a
  missing or vague verdict; the ruleset's version hash recorded in evidence.
* **A5 — Phase-scoped rule injection** through the host adapter.
* **A6 — Host-neutral agent definitions** rendered per host adapter. Project
  overlays replace edited agent copies, and `doctor` reports drift.
  Background: [BUG_051](BUG_051.md), [BUG_021](BUG_021.md).
* **A7 — Capability tiers in the manifest.** Model and host recorded in
  evidence for audit only, with a test that no gate reads them.
* **A8 — Ruleset edits gated** through the docs lane, plus an ADR when the
  edit is large.

**Heap adoption (pointer only).** B1 classify Heap's compliance rules (36 +
Boy-Scout) into the three kinds; B2 Heap ruleset, generated checklist and
managed `CLAUDE.md`; B3 baseline, then warn to block; B4 library agents plus
an overlay, retire or thin-wrap Heap's precommit and review-and-commit agents,
keep testflight-publisher local; B5 the conformance suite against Heap with
two or more models. The full B entries go in Heap's own backlog, filed from a
host session rooted in Heap, not from here.

**Order.** The [BUG_040](BUG_040.md) fix (its ADR first) and A0 come first.
Then A1 and B1 in parallel; A2, then A3, then A4. B2 and B3 after A2 and A3.
B4 after A6. B5 last. A5, A7 and A8 anywhere after A4. Sequencing set by the
user on 2026-10-26: [IDEA_030](IDEA_030.md) is finished before any of this
starts, and [IDEA_032](IDEA_032.md) (scheduled sweep, T1) goes ahead of the
BUG_040 fix and A0.

**Acceptance:**

* A project declares its rules in one ruleset file; `doctor` rejects a
  malformed ruleset, and a hand-edit to a generated checklist or a managed
  `CLAUDE.md` section is reported as drift.
* A pattern or boundary violation added on top of the baseline blocks the
  gate; an existing violation recorded in the baseline does not, and fixing
  one shrinks the baseline for good.
* A review that leaves a judgement rule without a verdict, or cites a line
  the diff does not contain, cannot leave review.
* The conformance scenarios return the same verdicts across every run and
  across at least two models and hosts.
* Nothing in a gate's outcome changes when only the recorded model or host
  changes.

**Architecture sketch:**

* A new guidelines domain beside the existing domains
  ([overview.md](../architecture/overview.md), "Component responsibilities").
* Pattern and boundary checkers under the ecosystem adapters, and agent
  rendering and rule injection under `hosts/`.
* Ruleset location, check levels and capability tiers in the manifest
  ([manifest.md](../architecture/manifest.md)).

**Risks / open questions:**

* Blocked by [BUG_040](BUG_040.md): a cross-repository host records no
  agent-ran evidence, so A4's verdict gate would wedge the same way in any
  consuming project driven from another root.
* How strict "vague verdict" is, mechanically, without the engine itself
  judging prose. A0 has to settle it.
* Boundary checks per ecosystem may outgrow regex quickly; A3 starts there and
  the ADR should say where it stops.
* The size of the umbrella: each A item is likely its own track and may later
  be split into its own entry.

**Why now / why this tier:**

High value (every session in every consuming project goes through the
guidelines; it turns prose rules into gated evidence), XL effort as a
multi-phase programme with an ADR first. T2 Strategic by the user's call.
