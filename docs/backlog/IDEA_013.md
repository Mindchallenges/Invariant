---
id: IDEA_013
type: idea
title: Session Oracle alerts reach your phone as a push notification
status: proposed
residue: []
history: []
priority: medium
effort: S
tier: '5'
area: oracle
---

**The want.** When the Session Oracle (IDEA_012) raises an alert, you also
get it on your phone, so that a session waiting on you while you are away
from the Mac does not sit idle for hours. Sessions are often driven remotely,
and that is exactly when a macOS notification goes unseen. The shape is a
second implementation behind v1's notifier seam, fed by the same rising-edge
de-dup ledger, so v1's alert rules and de-dup carry over unchanged.

**Acceptance:**

* An alert that would raise a macOS notification also reaches the phone
  within 30 seconds, and only once per rising edge.
* Push is opt-in and off by default. `invariant oracle notify-test` exercises
  it.
* The pushed text carries the project, the session and the condition. It
  carries no transcript content, no paths and no prompt text.
* A failing push provider is reported in `oracle watch` output and does not
  delay the other notifiers.

**Risks / open questions:**

* Which provider to use (a self-hosted relay, a push service, or the host's
  own mobile notifications). Every option sends data off the machine, so the
  payload must stay minimal and the credential must stay out of the
  manifest.

**Why now / why this tier:**

Follow-on to IDEA_012. Medium value: it matters most for remote driving. S
effort behind an existing seam. T5.
