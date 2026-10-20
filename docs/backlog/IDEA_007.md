---
id: IDEA_007
type: idea
title: The stats join reads only the current host binding, so a resumed session's
  earlier records claim nothing is known
status: proposed
residue:
- 'DECISION 2026-10-20 (user-approved): The identity-only join — join earlier hosts
  for session_id/session_name only, states = None with a new DECLINED_* code naming
  the real cause, shipped with a per-host selector for StatsDomain.resolve. No per-host
  arithmetic split.'
history: []
area: domains/stats
severity: low
priority: medium
---

**The want.** A stats record from a host session that drove a session EARLIER
should be joined to that session, and its per-state breakdown should be that
host's share rather than the session's whole spend.

**Why it is not just a mapping.** `StatsDomain._bindings` builds
`{session.host_session_id: session}` from the scalar CURRENT binding, so after
a resume every earlier host's record joins to nothing and renders
`DECLINED_UNJOINED` — which BUG_024 fixed into meaning "nothing is known about
this host session". Here something IS known: `Session.hosts` records every
earlier host, oldest first. A stated wrong cause is the failure BUG_024 warns
is harder to catch than an absence, so the reason code is wrong today.

**The naive fix is worse than the bug, and must not be applied.** Mapping every
id in `hosts` to the session makes `_join` call `_breakdown(session.id)` for
each of N records, and `_breakdown` sums across ALL of that session's
pipelines. Each of the N records would then carry the session's full per-state
spend, so the reported total multiplies by N. The record TOTALS are safe —
`record.stats` is folded from that host's own transcript — the damage is
confined to `states`.

**What a real fix needs, and does not have.** A per-host filter keyed on the
`usage_host` recorded on every history row and pipeline entry is the obvious
shape, and the data is not sufficient for it as recorded:

1. **`elapsed` has no host.** A row records seconds spent in the state just
   left; nothing says which host session was driving. Splitting durations by
   `usage_host` would attribute a duration by a key that describes a different
   field.
2. **No per-host baseline.** `breakdown` subtracts consecutive watermarks with
   the PIPELINE's `usage_at_start` as the first earlier reading. A per-host
   slice's first row has no earlier reading of its own counter, so every
   resumed host loses its first state's spend, or the fix invents a baseline
   nothing records.
3. **The boundary row belongs to the wrong host.** `session resume` run from a
   shell exporting no host id cannot see the new host — the rebind happens
   afterwards, in the host adapter's PostToolUse — so the `resumed` row's
   `usage_host` is the OLD host and its `host` extra is null. Filtering on
   `usage_host` assigns that row's interval to the previous host. Nothing is
   mis-measured today, because the following transition is declined by
   `_incommensurable`'s host check exactly as a rebind should be; what is
   missing is an owner for the reading, which is precisely what this fix
   needs.
4. **A second axis on `states_partial`.** Today it means "some pipelines
   predate per-state attribution". A per-host split adds "some rows belong to
   no host", and one flag cannot carry both.

**A third shape, cheaper than either, worth costing first.** Join the earlier
hosts for IDENTITY only — `session_id` and `session_name`, so the record stops
claiming nothing is known — and set `states = None` with a new `DECLINED_*`
code naming the real cause: the per-state attribution is recorded against the
session, and this record is one of several host sessions that drove it. That
fixes the wrong cause without inventing arithmetic. It is not free: it makes
several records share one name, which `StatsDomain.resolve` refuses as
ambiguous with no selector that separates them (see the refusal it already
prints), so it needs a per-host selector shipped with it, and the report's
session table would show N rows under one name.

**Deferred from IDEA_005 deliberately.** That increment taught `resolve` the
name ladder. This is the telemetry arithmetic, which is a different seam, and
folding it in would have smuggled a redesign of what a per-state figure MEANS
into a change about how a record is addressed.
