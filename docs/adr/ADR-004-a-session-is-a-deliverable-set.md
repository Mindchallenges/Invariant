# ADR-004 — A session is a deliverable set, not a sitting

- **Status:** Accepted
- **Date:** 2026-09-05
- **Supersedes:** nothing. Refines the scope of ADR-001 D19 and adds a clause to
  ADR-001 D18; both rows are amended in place.

## Context

ADR-001 D19 binds a host session and an invariant session 1:1, automatically,
"enforced both ways, because a silent rebind hands a pipeline and a file set to
an owner that never did the work". That reasoning is correct and it survives
unchanged. What it never said is *over what interval* the 1:1 holds, and until
now nothing made the question worth asking: a session was started, worked and
merged inside one agent-host session, so the two lifetimes coincided and the
scope was invisible.

Real work does not fit that shape. A change of any size runs across several
host sessions, and under strict 1:1 the second sitting has exactly one legal
move: start a *second* invariant session. The plan then forks. Two branches
hold half the work each, two file sets are declared, and the merge — the one
irreversible step in the session machine — either happens twice or, more often,
happens once on the half somebody remembered.

The distinction that resolves it is already recorded on the session document
and was not being read. `SessionEnd` sets `host_ended`, so a binding is in one
of three conditions:

| Binding | `host_session_id` | `host_ended` | May be replaced |
|---|---|---|---|
| **Live** | set | false | only with `--force` — this is the hand-over D19 forbids |
| **Spent** | set | true | yes: `session resume`, or `session adopt` |
| **None** | unset | — | yes |

D19's harm requires an incumbent. A spent binding has none: the host that owned
it is gone, and there is nobody on the other end to have a pipeline, an
evidence trail or a declared file set taken away from them. Claiming a spent
binding is a **continuation**, not a hand-over.

Leaving the ADR alone was the one option not available. The docs would state a
rule the code no longer follows, in the repository whose thesis is that
unchecked prose goes stale silently — and D16's lesson is sharper than that: a
rule the codebase does not keep is worse than a weaker one it does, because a
reader enforcing it would "correct" the working fix by reverting it (BUG_022).

## Decision

**A session is the deliverable set it recorded, not the sitting it was started
in.** It may be picked up by any number of host sessions over its lifetime, one
at a time, and it merges once — when the deliverables are done.

Two mechanisms carry it. A **plan**: an ordered checklist of what the session
owes, stored on the session record. And **resume**: a named command that binds
a new host session to a spent binding, refusing a live one.

This ADR is separate from ADR-001 rather than another row in it for the reason
ADR-002 and ADR-003 are separate. D19's rows are one-line decisions with a
rejected alternative each; a paragraph-sized position does not fit a table
cell, and growing ADR-001 until it does is how a decision record stops being
readable.

## Decisions recorded

### D1 — 1:1 at a time, N over a lifetime

**Rejected: keep strict 1:1 and start a fresh invariant session each sitting.**

That alternative is not a smaller feature, it is the defect. A plan spread over
four sittings becomes four sessions, four branches and four declared file sets,
each holding a quarter of the work, and nothing in the model says which of them
is the one that lands. The failure is silent at the moment it happens — the
second `session start` succeeds — and expensive later, when a merge takes one
branch and leaves the rest.

So the refusal in `adopt_host_session` is narrowed by exactly one clause,
`and not session.host_ended`, rather than removed. It now names the condition it
was always protecting: the refusal fires on a **live** binding and says so, and
it points at `session resume` for the other case. Every other guard around it is
untouched, including the reciprocal one — a host session that already owns a
live session may not take a second, which is the half of D19 this change does
not touch at all.

The N is recorded rather than inferred. `hosts` carries every host session bound
before the current one, oldest first, and `resumed_count` says how many times
the session was picked up. `_bind_host` seeds `hosts` from the scalar the first
time the binding moves, so a record written before the field existed acquires
its own history without a migration.

### D2 — Resume is explicit and named; `by_host_session` still refuses an ended host

**Rejected: let an ended host implicitly reclaim its own session** — drop the
`host_ended` filter in `by_host_session`, and a host that comes back resolves to
the session it left.

Rejected on two counts, and the second is the one that would have been found in
production rather than in review.

Picking up unfinished work brings a pipeline state, a declared file set and a
branch along with it. That is a decision with consequences, so it is a command
somebody runs — not something that happens because a hook resolved a lookup.
What *is* automatic is the refusal in the other direction: `session start`
refuses a task name whose resumable session is sitting right there, names the
address and its progress, and gives the command that picks it up. A warning
printed above a successful `start` would be prose-as-control-flow — it does not
stop the fork, and by the time anyone reads it there are two branches.

The mechanical count is that `by_host_session` is what every hook resolves
through, so relaxing it there is not a local change. The tempting variant —
vacate the binding and let the ordinary unbound path pick the session up — has a
chain in which every link is invisible from the line that would cause it: a
vacated session rejoins `attributable()`; a *peer* host that owns nothing and
had one origin candidate now has two; `resolve` answers `ambiguous`;
`RuleContext` renders that as `session=None`; and `pre-tool-use` **denies** the
peer's edit. Resuming one session must not be able to refuse another session's
tool call.

`resume` and `adopt` therefore write the binding through one function,
`_bind_host`, so they cannot disagree about what binding *means*. Two of the
three things it does are easy to omit and expensive to omit: it clears
`host_ended`, without which the resumed session resolves to nobody for every
subsequent hook in its new host, and it keeps the outgoing id.

### D3 — The plan is a field on the session record

**Rejected: a `plans/<sid>.json` store.**

The plan is per-session, it is read on the same turn as the state it qualifies,
and it is small. A second document would buy one thing — writes independent of
the session record — and the session record is already written atomically under
the same lock, so there is nothing to buy. What it would cost is a second thing
to keep consistent and a second thing to lose, and the storage layout stays one
per-session JSON file plus the locked registry.

It stays small by construction. `plan_remove` is a **hard delete** of the entry
rather than a `dropped` flag on it, so the field is bounded by what is actually
owed rather than by everything ever considered. What is retired is the
*number*, not the row: `plan_add` takes `max(existing) + 1`, so `plan done 3`
means the same thing after a removal as it did before one. The cost is gaps in
the sequence, which are cheap and visible; the alternative costs a command that
acts on the wrong deliverable and looks like it worked.

Being a field is what makes the layer split necessary, and the split is the
reason this row is a decision rather than a storage detail. The mutations
belong to `domains/session`, which owns the store, the locking and the
refusals. The *questions* do not: a rule has to ask how far along the plan is,
rules live in `core/rules`, and `core/` may not import `domains/`. So the
reading lives in `core/plan` with the domain above it, on the exact precedent of
`core/stats.breakdown` doing the arithmetic that `domains/stats` renders.

### D4 — The next step is derived from the plan, never stored beside it

**Rejected: a free-text `next_step` field on the session.**

It is the same fact written twice, and the copy that goes stale is the one
people read. A derived answer — the first open entry of the checklist — cannot
disagree with the checklist.

Five call sites ask for it — the closeout refusal, the resume line, the turn
projection, the greeting's offer and `session plan show` itself — and each
derives it from `core.plan.progress`. Five hand-built renderings would be five
chances to print a next step, or a progress count, that disagrees with the
numbers `session plan done <n>` takes, so the arithmetic is computed once and
every caller reads the answer. This is ADR-002 D3's
argument applied to a checklist rather than to a transition log: two records of
one fact are a single-source-of-truth violation from the moment they disagree.

The two renderers above that arithmetic are separate on purpose and neither is a
flag on the other. `render_lines` answers a question somebody asked and lists
every entry against the number the commands take; `summary_lines` is injected
into every turn, is capped at two lines however long the plan is, and renders
**nothing at all** for an empty plan — the opposite of the direct answer, and
deliberate, because a permanent line reporting the absence of a checklist is
what teaches a reader to skip the whole block.

### D5 — `session.deliverables_complete` guards the landing at `block`, with two recorded routes past it

**Rejected: guard only `reviewed`; ship it `warn`.**

It is an **entry** gate on both `reviewed` and `merged`. Both, because
`reviewed` can be re-entered from `working` and the merge is the irreversible
step — a gate that guards only the reviewable step guards nothing once the work
moves on from it, which is the same argument `pipeline.complete` already carries
on the same two states. *Entry* rather than exit, so the `reviewed → working`
retreat stays walkable: a session that discovers more is owed must be able to go
back and do it without first satisfying the gate that says it is all done
(BUG_037).

`warn` was rejected on what the failure costs. The gate stands in front of a
merge that lands a half-finished checklist and a teardown that removes the
worktree and deletes the branch behind it; by the time anybody reads the
warning, the thing the warning was about has been done and the advisory line has
scrolled away.

A blocking gate is only permitted here because ADR-001 D25 requires a route
past, and there are three. `plan done <n>` and `plan remove <n>` are the two
**recorded** ones, and they are named first: each writes the decision into the
checklist itself, where the next reader finds it, while `merge --force` records
only that somebody went past a gate. Between the two, `plan done` leads, because
the commonest cause of this refusal is work that is genuinely finished and
unticked — frequency, not ceremony.

### D6 — An empty plan is `na`, not `clean` and not `fail`

**Rejected: both of the other two.**

`clean` is the vacuous green, on a gate guarding the merge. An empty plan is not
"the plan is finished", it is "no plan was made", and those are different facts.
`fail` makes a checklist mandatory for every session including a one-line chore
— and a gate that legitimate work must override routinely is what teaches people
to reach for `--force` on the gates that matter (ADR-001 D17, BUG_002).

The skip names `invariant session plan add`, the command that would **arm** the
gate, rather than saying "not applicable here" — which tells a reader that a
gate went inert and nothing about how to make it mean something (BUG_027).

The distinction is computed once, in `core.plan.Progress.complete`, and that is
part of the decision rather than tidiness. The obvious inline spelling is
`done == total`, which is `True` for an empty list; written at three call sites
it would be written that way at one of them, and every unplanned session would
quietly report itself finished at the boundary this gate exists to hold.

### D7 — Names are `slug-id`, unique by construction; existing names are not migrated

**Rejected: enforce uniqueness by refusing a collision; rename old sessions.**

Refusing a duplicate refuses the case this feature exists for. Two sessions on
one long-running task, started weeks apart, is exactly the shape a deliverable
set produces, and a uniqueness check would make the second one impossible to
name. Appending the session id to the task slug makes uniqueness **structural** —
ids are unique, therefore `slug-id` is — so there is no collision to check for
and no check to get wrong. `qualified_name` is idempotent, which is load-bearing
rather than tidy: the name people copy back out of a greeting already carries the
id, and re-qualifying it must not double the suffix.

Renaming existing sessions was rejected because a name is an *address*. It has
been written into transcripts, into notes, into whatever the operator wrote
down, and a migration would invalidate all of those to buy a uniformity nothing
needs — the lookup ladder's first name rung is an exact match on whatever is
stored, so an unqualified legacy name stays addressable by the thing a human
would type. This repository's own long-running session carries an unqualified
name and is addressed by it throughout.

The ladder itself is written down once. `resolve_name` climbs exact id, exact
name, bare slug, unique name prefix — and `domains/stats` climbs the same three
name rungs over telemetry records, so the precedence lives in `name_rank` rather
than in two places that would drift while both kept answering plausibly.

### D8 — `work_would_be_stranded` answers `clean` on a fresh open plan, and fails again on a cold one

**Rejected: leave it blocking and expect `--force` every turn.**

The closeout rule refuses when a pipeline says the work is done and the branch
holds commits the base does not. Under the model this ADR records, that
combination is the **resting** state at the end of every increment: a plan
spanning four host sessions parks with work on its branch three times before it
merges once. A gate firing on all of those is one that gets ignored — which is
the failure the rule's own narrowing to a terminal pipeline was written to
avoid, reappearing one model change later. Expecting `--force` on every parked
turn is worse than that, because it trains the override on the gates that do
matter.

What separates parked from abandoned is whether the plan is being **worked**, so
that is the question the rule asks: an open plan that moved recently is
`clean` — parked against a promise somebody recorded — and the verdict names the
next deliverable.

`clean` and **not** `na`, deliberately, and this is where D6's polarity inverts
for a reason. `na` would claim the rule had nothing to say about a session it
had just examined; the question was asked and answered. D6's empty plan gets
`na` because there is genuinely nothing to answer *about*. Drawing the
distinction consistently in both directions is what keeps a skip from reading as
a pass anywhere in the file.

Cold is where the refusal returns. The threshold is
`session.plan_stale_days`, read from the manifest rather than fixed here,
because where the line falls between "paused over a weekend" and "abandoned" is
a property of how a project works. The reading is
`max(plan_touched, heartbeat)`, so a session moving through its lifecycle counts
as alive whether or not anyone edited the checklist on the way. And **both
clocks at zero read as fresh**: every record written before the field existed
carries `0.0`, and failing on missing data would fire a blocking gate on all of
them simultaneously, at `stop`, the surface with the least room to be wrong. The
two mistakes do not cost the same — a false "cold" ends a turn, a false "parked"
costs one turn's reminder.

The two gates are duals and would fight if either were moved. The same open plan
is `clean` at the closeout, because parking is the resting state, and a refusal
at the landing boundary, because merging is the claim that the session is over.

### D9 — The reviewer asks the engine which mode it is in

**Rejected: tell it in the invocation prompt.**

A session that is mid-plan must commit and stop; only the run that closes the
last deliverable may merge and tear the session down. Getting that wrong in the
merging direction ends a session that was deliberately unfinished, with its
worktree removed and its branch deleted.

For six increments of the branch this ADR records, that instruction was carried
by a paragraph pasted by hand into each invocation of the review agent. It
worked every time. It is also, precisely, the failure ADR-001 exists to
eliminate — occurring inside the library that eliminates it, in the one document
set that is supposed to know better. All three of ADR-001's failure modes apply
without adjustment: non-compliance would have been **silent**, because nothing
records whether the paragraph was read; the instruction sits at the top of a
long prompt and is exactly what **context pressure** summarizes away; and "did
the reviewer know it was mid-plan?" is **unanswerable** from anything but the
transcript, because the control was a sentence that was read rather than a thing
that ran. That is evidence rather than rhetoric, and it is the strongest
argument available for this row: the alternative was tried, at length, by the
authors of the position it violates.

So the mode is a question put to the engine — `invariant session plan show
--json` — and three properties of the answer are decisions in their own right.

**`planned` and `complete` are separate keys**, and neither is derivable from
the other by a consumer holding one. Collapsed into a single flag, a session
that never recorded a plan is indistinguishable from one that finished its plan,
and a reviewer switching on it either defers the merge forever on every project
that keeps no checklist, or passes every unplanned session as finished.

**The unanswerable case reads FINAL, not INCREMENT.** Failing closed to
INCREMENT sounds safer and is not: an engine that could not answer would then
silently park every run of every project, and the symptom — work sitting
unmerged on branches — looks exactly like the tool working. A wrong FINAL is
loud and recoverable; a wrong INCREMENT is quiet.

**The reading is provisional until the deliverables this run finished are
marked**, and is then re-read. The run that closes the last deliverable is the
run that finishes the session, so deciding once, up front, would strand every
plan's final merge one run late.

## Consequences

**Good.** Work larger than one sitting has a home: one session, one branch, one
declared file set, one merge. The 1:1 binding keeps everything it was protecting
— attribution stays unambiguous, because `resumable()` and `attributable()` are
exact complements written from the same `live()` set and are disjoint by
construction. And a review agent's landing decision becomes an engine reading
with an artifact behind it, instead of a paragraph somebody remembered to paste.

**Costs.**

- **A session can now be forgotten.** That is the direct price of removing the
  host session's end as a forcing function. The mitigations are
  `session.plan_stale_days` and the cold branch of `work_would_be_stranded`,
  which refuses a plan nobody has moved in that many days and names the days it
  judged on, plus the `doctor` rows and the resumable set the greeting offers
  above the unowned one. State them as mitigations rather than a solution: the
  cold refusal fires at `stop`, in a host that is running, so a session nobody
  ever opens a host for is refused nothing. What the design guarantees is that
  the session is *offered* the next time somebody works in that repository, and
  that its branch and worktree are still there when it is.

- **Telemetry across N hosts is incomplete, and one reason code is now wrong.**
  `StatsDomain._bindings` builds its join from the scalar *current* binding, so
  after a resume every earlier host's record joins to nothing and renders
  `DECLINED_UNJOINED` — which BUG_024 fixed into meaning "nothing is known about
  this host session". Here something *is* known: `Session.hosts` records the
  earlier hosts. A stated wrong cause is the failure BUG_024 warns is harder to
  catch than an absence. IDEA_007 is filed with the four inputs a real per-host
  fix needs and does not have — `elapsed` carries no host, there is no per-host
  baseline, the boundary row's counter belongs to the outgoing host, and
  `states_partial` would have to carry two meanings at once — and it records
  that the naive mapping is *worse than the bug*, because it would multiply the
  reported per-state totals by the number of hosts. Filed, scoped, and not
  solved.

- **The additive-field approach costs backward compatibility, silently.**
  `Session.from_dict` filters unknown keys and `to_dict` emits only known ones.
  That is exactly what makes `plan`, `plan_touched`, `resumed_count` and `hosts`
  need no migration in the forward direction — an older document loads with an
  empty plan and behaves as it did. The other direction was never designed: an
  **older** build that reads a record and writes it back deletes every field it
  does not know. Measured, a `plan`, `plan_touched`, `resumed_count` and `hosts`
  all vanished from a resumed session and `host_ended` reverted, and nothing
  warned. It is reachable in normal use because a shim on `PATH` is routinely an
  older build than the process being diagnosed — which is the standing condition
  of this repository while a feature branch is unmerged (BUG_059). Recorded here
  as a known cost of the approach rather than as a defect of one command; the
  next increment fixes it by preserving unknown keys through the round trip, so
  a record written by an older build keeps the fields that build cannot see.

**Accepted risk.**

Every refusal in this ADR has a `--force` past it, and each one re-opens the
harm the refusal names: a forced rebind still hands a live session's pipeline
and file set to an owner that never did the work; a forced resume of a merged
session accumulates commits behind a spent push barrier, which is finished work
with no route out of the session holding it; a forced merge lands a half-done
checklist. All three are deliberate under ADR-001 D25 — the operator is the
final authority — and all three are recorded against the transition that spent
them. The alternative is a gate with no route past it, which D25 names as the
worse defect.

The second risk is what the plan *is*. It is the operator's own prose about
their own work, and `session.deliverables_complete` reads that list and asks
whether it is finished; nothing checks that the list describes the change. A
checklist can be satisfied by writing one vague line and ticking it. Accepted,
because the only alternative is inferring deliverables from a diff, which is
guessing — and this library's standing position is that an inferred applicability
is worse than a declared one that can be wrong and reviewed.

## Notes

The scope refinement is recorded in ADR-001 D19's own row as well as here, and
D18 gains a clause saying that "a session ends at the merge" fixes *where* a
session ends and not *when*. Both edits are in place rather than as a new row,
because a reader who finds D19 is a reader who needs its scope, and a
cross-reference they have to follow is a rule half-stated.
