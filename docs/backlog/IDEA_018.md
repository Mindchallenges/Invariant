---
id: IDEA_018
type: idea
title: Session Oracle can alert on an idle your-turn session
status: proposed
residue: []
history: []
priority: low
effort: XS
tier: '6'
area: oracle
---

**The want.** The Session Oracle (IDEA_012) can ALERT when a session has gone
idle on "your turn": the host finished its turn and is waiting for your next
prompt. In v1 that condition is shown on the dashboard but never alerts,
because it fires after every turn and would drown out the real signals. This
idea makes it a separate, opt-in alert with its own delay.

**Acceptance:**

* With the alert enabled, a session idle on "your turn" for longer than a
  configured delay raises one notification. With it disabled (the default),
  the v1 behaviour is unchanged.
* The alert does not fire for a session already alerting under a stronger
  condition (waiting, blocked, finished, error).
* The alert re-arms only after a new turn starts.

**Risks / open questions:**

* Noise. The right default delay is unknown, and it may need a per-project
  override.

**Why now / why this tier:**

Follow-on to IDEA_012. Low value: polish on an existing signal. XS effort, one
classifier branch and a threshold. T6.
