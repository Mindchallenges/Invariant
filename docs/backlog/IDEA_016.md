---
id: IDEA_016
type: idea
title: Session Oracle dashboard as a Claude artifact
status: proposed
residue: []
history: []
priority: medium
effort: M
tier: '3'
area: oracle
---

**The want.** You can see the Session Oracle (IDEA_012) state as a Claude
artifact dashboard: a live document in the Claude web app that renders the
oracle's rows and alerts. You can read it alongside a chat, and from any
device signed into the account. This is an alternative to the hosted website
(IDEA_015), not a replacement for it.

**Acceptance:**

* An opt-in command publishes or refreshes the artifact from the watcher's
  snapshot. The artifact shows every session's project, state, alert and
  recommended command.
* The artifact refreshes on a change in the snapshot, not on every tick.
* The published content follows the same redaction rules as the hosted
  dashboard. No transcript or prompt text, and no paths that embed a
  username.
* The artifact is read-only. Acting from it is out of scope.

**Risks / open questions:**

* What API or tool surface exists for updating an artifact from a background
  process, rather than from inside a chat turn. This may need a spike first.
* Data leaves the machine. It shares the trust-boundary ADR with the hosted
  dashboard (IDEA_015).

**Why now / why this tier:**

Follow-on to IDEA_012. Medium value. M effort, with unknowns in the publish
surface. T3 (scope expansion: a new external system).
