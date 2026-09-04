---
id: IDEA_005
type: idea
title: StatsDomain.resolve does not know that a session name carries its id
status: done
residue:
- 'Shipped: the identifier rung, the three name rungs, and a refusal that lists candidates
  rather than taking the first hit. NOT shipped, and filed as IDEA_007: joining the
  earlier hosts on Session.hosts. That join is what makes several records share one
  name, which this resolver refuses with no selector able to separate them — so IDEA_007
  must ship a per-host selector with it.'
history:
- from: proposed
  to: accepted
  reason: increment 5 of the session-outlives-host series takes it
  forced: false
- from: accepted
  to: in_progress
  reason: null
  forced: false
- from: in_progress
  to: done
  reason: the three strict-xfail assertions pass unmarked and unmodified; resolve
    climbs resolve_name's ladder through name_rank
  forced: false
area: domains/stats
severity: low
priority: medium
---

**The want.** `invariant stats show <bare-task-name>` and `invariant stats
archive <bare-task-name>` again, now that a session name is an address.

**Why.** `SessionDomain.start` appends the session id to the task slug, so a
record's `session_name` is `add-widget-abcd1234` rather than `add-widget`.
`StatsDomain.resolve` matches the selector against `host_session_id`,
`session_id` and `session_name` by **equality**, so the bare task name — the
one thing a person actually remembers — no longer selects anything, and the
refusal it produces names three spellings of which the reader has none to hand.

**The shape of the fix.** `domains/session` already exports the predicate:
`name_matches(session_name, session_id, query)` answers the name half of
`resolve_name`'s ladder (full name, bare slug, name prefix) without needing a
live set to scan — which is the case here, because a telemetry record outlives
the session that produced it. `domains/stats -> domains/session` is an arrow
`docs/architecture/overview.md` already draws. Note that `name_matches`
flattens the ladder's precedence, so a selector matching several records still
has to be disambiguated by the caller rather than resolved to the first hit.

**Owed by.** Three tests are wrapped in a strict `xfail` naming this item:
`tests/test_stats_domain.py::TestArchiving::test_a_record_can_be_archived_by_its_invariant_session_name`,
`tests/test_cli_stats.py::TestShow::test_one_session_by_name`, and
`tests/test_cli_stats.py::TestArchive::test_archiving_promotes_a_record`.
Delete the markers with the fix; the assertions must pass unchanged.
