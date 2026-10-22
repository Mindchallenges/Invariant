---
id: IDEA_015
type: idea
title: Session Oracle dashboard as a hosted website
status: proposed
residue: []
history: []
priority: medium
effort: L
tier: '3'
area: oracle
---

**The want.** You can see the Session Oracle (IDEA_012) dashboard from any
browser, including on a phone, as a hosted website (e.g. on Vercel). Its sibling is IDEA_016. The v1
dashboard is a local TUI. The shape is an opt-in publisher that pushes a
redacted snapshot to a hosted endpoint, plus a read-only web page that
renders the same rows, states and recommended commands.

**Acceptance:**

* With publishing on, the hosted page shows the same sessions and states as
  `invariant oracle list --json`, within one watcher tick plus the publish
  latency.
* The page is not readable without authentication.
* The published snapshot holds no paths that embed a username, no transcript
  or prompt text, and no host identifiers beyond opaque session ids. A
  privacy check runs on every publish.
* Publishing is off by default. Turning it off stops all outbound traffic.
* The web page is read-only in this idea. Acting from the web is out of scope.

**Risks / open questions:**

* This is the first time invariant sends state off the machine. It needs its
  own ADR covering the trust boundary, authentication, retention, and what
  is redacted.
* Hosting choice and cost.

**Why now / why this tier:**

Follow-on to IDEA_012. Medium value: remote visibility. L effort because of
the trust boundary, not the rendering. T3 (scope expansion: a new external
system).
