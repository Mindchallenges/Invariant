---
id: IDEA_008
type: idea
title: A handoff note is offered to every unbound greeting until it goes stale, even
  after a session acted on it
status: accepted
residue: []
history:
- from: proposed
  to: accepted
  reason: null
  forced: false
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
  `session resume` in this project marks the handoff its launcher token names
  consumed; the next unbound greeting and `invariant context` no longer show
  it.
* A consumed handoff stays on `Session.handoffs` with when it was consumed and
  by which session/host — the record is history, not a queue to pop.
* A handoff recorded AFTER the consuming start is still offered (consumption is
  per note, not "any start silences all handoffs").
* An unconsumed note still expires by `session.plan_stale_days` exactly as
  today.
* `invariant session handoff --dismiss` dismisses the latest fresh note
  without starting a session (for a handoff the user decided not to act on);
  it is recorded as dismissed, not claimed.

**Decisions (user, 2026-10-20):**

1. **Claim signal: a launcher env token.** The handoff launcher prefixes
   `claude` in the `cd <main> && …` line it types with an environment token,
   e.g. `INVARIANT_HANDOFF=<session-id>:<at>`. Hook subprocesses inherit the
   environment the host was started with, so the first `session start` /
   `session resume` in that host reads the token and consumes EXACTLY that
   note, on the PostToolUse binding path. This keeps the write out of
   SessionStart (which state_model.md says must not write), does not depend
   on the host exposing its session name, and is safe with parallel sessions.
   A note launched by hand (no token) is cleared only by dismiss or by
   staleness. There is deliberately NO "any start/resume after `at`"
   fallback.
2. **Which note is consumed:** the one the token names — never "every fresh
   note".
3. **Dismiss:** `invariant session handoff --dismiss` marks the latest fresh
   note consumed and records it as dismissed. Same command, not a new
   subcommand.
4. **Storage:** the consumed marker lives on the `handoffs` entry — timestamp,
   consuming session/host, and how (`claimed` or `dismissed`). An entry
   without the key reads as unconsumed, preserving the BUG_061 no-migration /
   round-trip contract. The write to the old (possibly torn-down) session's
   record is a standstill-style event, not an edge, like the handoff itself.

**Risks / open questions:**

* None open — the four questions (claim signal, which note, storage contract,
  where the write happens) are resolved by the decisions above.

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
