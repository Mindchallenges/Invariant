---
id: IDEA_031
type: idea
title: invariant owns the format, lifecycle and enforcement of coding guidelines
status: in_progress
residue: []
history:
- from: proposed
  to: accepted
  reason: A0 landed as ADR-012 (7c5ed6d) with the user's 13 rulings; the user approved
    building the plan
  forced: false
  ruling: the user, 2026-10-27 (ADR-012 rulings)
  evidence: 7c5ed6d78ad62ba791721b83abaa7e4f0b837566
- from: accepted
  to: in_progress
  reason: 'First build increment landed: A1 increment 1 in dc48851'
  forced: false
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

**A0 landed** as [ADR-012](../adr/ADR-012-the-guidelines-domain-and-the-model-agnostic-contract.md)
in commit 7c5ed6d: all thirteen questions ruled, an always-pure ratchet with
no Boy-Scout switch. A1 to A8 are unbuilt. The item stays `proposed` until its
first build increment can cite a ruling commit for the ruled route to
`in_progress`.

A1 increment 1 landed (dc48851): engine-layer conformance harness, three
scenarios. A1 increments 2–6 are tracked here, not in a session.

* Increment 2: after A2 (ruleset fixtures, malformed-ruleset scenario). The
  malformed-ruleset scenario landed with A2 increment 1; the fixtures for the
  kinds A2 increment 2 validates are still owed.
* Increment 3: done, landed with A3: the seeded-pattern, missing-test,
  baselined, baseline-grown and fixed-not-pruned scenarios.
* Increment 4: the scenarios are done, landed with A4 increment 7:
  vague-verdict, judgement-fail, judgement-fail-standard, stale-verdict and
  engine-answers. The model/host permutation test still arrives with A7.
* Increment 5: the scripted CLI driver plus `invariant conformance run|show`,
  after A4.
* Increment 6: Claude Code under two or more models, after A4 and A6.

**A3 landed** on main on 2026-10-29 from session 864db3ee, in nine
increments; the final commit is 3d41370. What landed:

* lexical masks and a path-aware `executable_text`, which fixed
  [BUG_103](BUG_103.md);
* Python import references;
* the `guidelines.mechanical_clean` gate over pattern, boundary and cochange;
* the baseline file with `guidelines baseline --record/--prune`;
* `guidelines.baseline_only_shrinks`;
* override by guideline id;
* an edit-time advisory at PostToolUse;
* this repository's dogfood ruleset: two layering boundary rules, no
  baseline.

A follow-up chore (303c85b) raised `guidelines.mechanical_clean` to block in
this repository. A1 increment 3's conformance scenarios landed with A3.

User rulings: all 17 A3 design recommendations accepted, recorded in
[ADR-012](../adr/ADR-012-the-guidelines-domain-and-the-model-agnostic-contract.md)'s
as-built notes. The dogfood ruleset is two rules covering all four
hand-written layering scans. No `CLAUDE.md` for now; revisit with A5.

Known accepted gap: removing `guidelines.baseline` in one landing and
re-adding a grown one in a later landing is admitted. A8's gated ruleset
edits close it.

Follow-ups filed, both unscheduled: [BUG_104](BUG_104.md) (a
whitespace-named file is invisible to the gates) and
[IDEA_038](IDEA_038.md) (a node lexer).

Next: A4 (verdict contract), in design in session c181f22d.

**A5 closed** in session 59bb4d65, in four increments: phase-scoped
selection and `guidelines show --phase` (ed52faf), the turn layer
(991fe2a), the edit layer and `guidelines.injection.on_edit` (145e6a5), and
the always-layer warning with this closure (A5.4). All three layers of
ADR-012 ruling 8 are built: the managed `guidelines/index` block, the turn
index, and the statements an edit carries beside its findings. When a
ruleset has `always` rules and no instruction file carries the block,
`doctor` warns and `guidelines.generated_current` notes it, both naming
`invariant guidelines render --insert guidelines/index`.

User rulings: the twelve A5 rulings (D1 to D12), recorded in
[ADR-012](../adr/ADR-012-the-guidelines-domain-and-the-model-agnostic-contract.md)
under D7. Per D11 this repository still has no `CLAUDE.md`, and its ruleset
has no always rules; revisit when one is added. Subagents get only the edit
layer until A6 renders the agents (D9).

**A6 closed** in session 58e4b6c1, in five increments: one asset classifier
and one install plan, with [BUG_106](BUG_106.md) fixed (3b52b34, fd92bc6),
the neutral agent format and the Claude Code renderer (662984b), overlays and
the `agents.*` keys (e971cdf), the `agents.rendered_current` drift rule
(aa418f2), and the agent bodies onto the engine with this closure (A6.5).
The shipped agents are host-neutral sources rendered per host and amended by
overlays rather than edited copies. `feature-architect` reads `invariant
guidelines show --phase design --touched` before it plans, and
`compliance-reviewer` answers the guidelines through `guidelines show --phase
review --touched`, `check guidelines.mechanical_clean`, `guidelines verdicts
show` and one `guidelines verdict` per owed rule, pasting the standings into
its report. Both fall back to the checklist on the engine's no-ruleset line.
A test parses every command a shipped body names.

User rulings: the fourteen A6 rulings, recorded in
[ADR-012](../adr/ADR-012-the-guidelines-domain-and-the-model-agnostic-contract.md)
under D8. Ruling 14 holds: no gate on the design read. Ruling 13's migration
helper (draft an overlay from an edited copy) is to be filed as its own idea.

A6 unblocks Heap B4 (the downstream library agents and their overlays) and
A1 increment 6 (Claude Code under two or more models). This repository's own
installed agents are reinstalled after the merge.
