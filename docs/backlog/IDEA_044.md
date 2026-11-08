---
id: IDEA_044
type: idea
title: invariant init runs an onboarding questionnaire that shows every default and
  lets the user configure it
status: proposed
residue: []
history: []
priority: high
effort: L
tier: '2'
area: cli/main, core/manifest, hosts
---

**Asked by the user, 2026-11-07:** "design an onboarding for invariant init. So we take user to a questionnaire, show the defaults, and user can give inputs and configure everything."

**What:** an interactive onboarding for `invariant init`. It walks the user through the configuration as a questionnaire: ecosystem and commands, profile, lane, coverage floor, agents, guidelines ruleset or checklist, docs location and onboarding (IDEA_043), records locations and store (ADR-014), and privacy scope. Each question shows the resolved default and its provenance, and the user accepts it or enters a value. The answers are written at the level the user picks (user, store or repo; ADR-014 D3).

**Safe default:** a non-interactive `init` (`--dry-run`, `--json`, `--yes`, or no terminal) behaves exactly as today. The questionnaire is skipped, and the defaults it would have shown are printed.

**Open questions for the design interview:** which questions are asked by default and which are behind an advanced step; whether the questionnaire can be re-run later (for example `invariant configure`) to change answers; host-neutral rendering (terminal prompts first); and validation, where every answer goes through the same manifest validation as a hand-written value, refused at entry with the reason.

**Related:** [ADR-017](../adr/ADR-017-init-asks-the-configuration-as-a-questionnaire-and-configure-asks-it-again.md) (the design, Accepted 2026-11-08; the rulings of the interview and of acceptance are recorded there), IDEA_043 (its docs offers become one step of this questionnaire), IDEA_040 / ADR-014 (levels, `store init`), IDEA_041 / ADR-015 (commands derived from CI could pre-fill answers), IDEA_042.
