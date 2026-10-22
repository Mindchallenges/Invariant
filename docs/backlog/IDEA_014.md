---
id: IDEA_014
type: idea
title: Status line and prompt line show which other sessions need you
status: proposed
residue: []
history: []
priority: medium
effort: M
tier: '5'
area: oracle, hosts/claude_code
---

**The want.** Inside any one Claude Code session, you can see at a glance
that ANOTHER session needs you, without switching to the Session Oracle
dashboard (IDEA_012). The shape has two parts. (1) A status-line command
that prints a compact count read from the watcher's snapshot, e.g. "2
waiting, 1 blocked". (2) Prompt-line injection: the existing host hooks add
a one-line note to the context when other sessions are waiting, the same way
invariant already injects state (see
[overview.md](../architecture/overview.md), "Injecting state instead of
rules").

**Acceptance:**

* The status line shows the count of alerting sessions across all projects,
  with no more than one watcher tick of lag, and shows nothing when none are
  alerting.
* The status line never scans the projects itself. It reads only the
  snapshot, and degrades to a blank or "watcher off" display when no
  snapshot exists.
* The injected note names which sessions need you and the recommended
  command for each. It is never injected into the session it describes.
* Both parts are opt-in.

**Risks / open questions:**

* Injection costs context tokens on every turn. It may need rate limiting, or
  a rule that it fires only on a change.

**Why now / why this tier:**

Follow-on to IDEA_012. Medium value. S–M effort that reuses the snapshot and
the existing hooks. T5.
