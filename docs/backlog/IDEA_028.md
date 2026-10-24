---
id: IDEA_028
type: idea
title: The coverage floor can be advisory for declared subtrees during a phase
status: done
residue: []
history:
- from: proposed
  to: accepted
  reason: null
  forced: false
- from: accepted
  to: in_progress
  reason: null
  forced: false
- from: in_progress
  to: done
  reason: null
  forced: false
priority: high
effort: S
area: core/rules, core/manifest, cli doctor
---

**The want.** A project can declare, in a reviewable manifest diff, subtrees where the coverage floor is ADVISORY: methodology.gates.coverage_advisory_paths, a list of globs matched exactly as coverage_exclude and non_source are. A touched file under one is reported with its figure, or as not measured, and never fails coverage.meets_floor; every other file keeps the blocking floor unchanged.

**Why.** A consuming project ruled that one development phase lands new, flag-off code under two subtrees on scoped test suites, with one full regression plus coverage pass owed at the phase gate. Its coverage.meets_floor is block and its only coverage lane is a full-suite run, so a milestone touching only the new code could not land without that run or a force. Dropping the gate from the methodology cannot tell shared code from new code.

**Acceptance:**

* A change touching only advisory paths passes with no coverage run, and the message names the count, the key, and that a coverage run is still owed before the list is removed.
* A mixed change still blocks on the held files exactly as before: no-record, no-row, trust, floor and ratchet.
* An advisory file below the floor is reported and flagged, never failed; no figure is invented.
* invariant doctor names a configured list as an ACTIVE EXCEPTION.
* The key is accepted by validation and listed by config show --provenance; a bare string for any of the three glob lists is refused at load.

**Shipped with:** the session that adds the key (docs: architecture/overview.md coverage levers, architecture/manifest.md schema, guide/configuration.md).
