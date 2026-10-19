---
id: IDEA_008
type: idea
title: A handoff note is offered to every unbound greeting until it goes stale, even
  after a session acted on it
status: proposed
residue: []
history: []
area: domains/session, hosts/claude_code, cli/main
priority: medium
severity: medium
effort: S
---

**The want.** A handoff note stops being offered once a session has acted on
it. Today `invariant session handoff` records the note on `Session.handoffs`,
and while it is younger than `session.plan_stale_days` `latest_handoff`
surfaces it in EVERY unbound SessionStart greeting and every unbound
`invariant context` in the project — including the greeting of the very host
session that was launched to pick it up and has already started or resumed
the work. ADR-005 (Consequences) names this as a deliberate follow-up; D5 made
freshness the only expiry so that nothing had to run for an old note to stop
showing, and that is the half this idea adds a second exit to.

**Why it matters.** A note that keeps being offered after it was acted on is
not neutral: an unrelated session opened in the main checkout within the stale
window is told what to do next by a note whose work is already in flight in
another session, and may start it a second time. The handoff is the documented
last step after merge and teardown, so the window in which a stale-but-fresh
note is wrong is most of its life.

**Acceptance:**

* After a handoff, the launched host session's first `session start` or
  `session resume` in this project marks the newest handoff consumed; the next
  unbound greeting and `invariant context` no longer show it.
* A consumed handoff stays on `Session.handoffs` with when it was consumed and
  by which session/host — the record is history, not a queue to pop.
* A handoff recorded AFTER the consuming start is still offered (consumption is
  per note, not "any start silences all handoffs").
* An unconsumed note still expires by `session.plan_stale_days` exactly as
  today.
* There is an explicit way to dismiss a note without starting a session (for a
  handoff the user decided not to act on).

**Risks / open questions:**

* **What counts as "acted on".** The handed-off host's id is not known at
  handoff time — the launcher only knows the `--name` slug. Candidate claim
  signals, most to least specific: a start/resume whose host session name
  matches the recorded `name`; a start/resume by a host that saw the note in
  its greeting; any start/resume in this project after `at`. The last is cheap
  and wrong when two sessions are opened in parallel; the first depends on the
  host exposing its session name to the hook.
* **Which note a start consumes** when several fresh handoffs exist from
  different sessions — only `latest_handoff`, or every fresh one older than the
  start.
* **Storage contract.** Adding a consumed marker to a `handoffs` entry has to
  respect the no-migration / round-trip contract on `Session` fields (BUG_061):
  an older record without the key reads as unconsumed.
* **Where the write happens.** A start or resume binds a NEW session, while the
  note lives on the OLD (often torn-down, unregistered) session's record, so
  consumption writes to a finished session's file. That is a new kind of write
  to a terminal record and should be stated as a standstill-style event, not an
  edge, like the handoff itself.

**Affects:** the session domain's `latest_handoff` reader and the
`session start` / `session resume` binding path; the SessionStart greeting and
`invariant context` (docs/architecture/cli.md); the `handoffs` field and
"Handing off: an event, not an edge" in docs/architecture/state_model.md;
ADR-005 D5.

**Why now / priority:** follow-up named in ADR-005's Consequences from the
session that shipped `session handoff`; medium value (every unbound greeting in
the stale window, with a real risk of duplicated work), small-to-medium effort
(one field, one write on the binding path, one filter on the reader, plus the
claim-signal decision).
