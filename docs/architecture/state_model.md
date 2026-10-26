# State model

The five domains, their state machines, and the storage layout. This is the
specification the engine is implemented against.

Three of the five are genuine finite state machines. `memory` and `stats` are
not — each is a store with invariants over it and operations that repair them
— and they belong here for the same reason the machines do: what they hold
drifts silently, and drift is undetectable without a script that checks it.

## Declarative FSM format

A state machine is a YAML file. The same schema serves methodologies, session
lifecycle, and work-item lifecycles, so there is one loader and one validator.

```yaml
id: tdd
kind: pipeline
description: Test-driven development with a docs-first design phase.

initial: designing
terminal: [committed, abandoned]

states:
  designing:
    description: Understand the change against the architecture docs.
    roles: [design]          # the docs-first read gate attaches here
    next: [red, abandoned]
  red:
    description: A failing test that fails on the reported symptom.
    next: [green, abandoned]
  green:
    description: Minimum change that makes the failing test pass.
    entry_gates: [test.new_test_fails]
    next: [refactor, reviewing, abandoned]
  refactor:
    description: Clean up under a green suite.
    entry_gates: [test.all_pass]
    next: [reviewing, red, abandoned]
  reviewing:
    description: Review, coverage, docs, and privacy before committing.
    roles: [review]
    entry_gates: [test.all_pass]
    next: [committed, red, refactor, abandoned]
  committed:
    description: Work is committed locally. Pushing is a session-level event.
    roles: [landing]         # docs.updated and the agent-role gates attach here
    entry_gates: [test.all_pass, coverage.meets_floor, privacy.clean]
  abandoned:
    roles: [abandon]         # reaching it is ungated
```

Every state, edge, gate and role above is quoted from the shipped `tdd.yaml`;
only the descriptions are abridged, and the three `#` notes are added here. The
structure is the part a reader checks against the engine, so the structure is
the part that is verbatim. Note what it does **not** contain: no
`docs.updated`, no `agents.*`. Those attach centrally
from the `design` and `landing` roles, so forking a methodology cannot silently
drop them — a rule the suite pins, and a rule an inline example that declared
them would teach people to break.

**A gate belongs to the outcome it protects, not to the state whose work
precedes it.** Almost every gate above is an ENTRY gate, and that is the
correction rather than a stylistic preference. Written as exit gates they fired
on every edge out of the state, and a state's edges do not all go the same
place: `reviewing` advertises four successors, two of which are retreats. A
review that discovered more TDD was owed was refused the retreat by
`coverage.meets_floor` and `agents.review_agent_ran` — gates that exist to
permit *shipping* — so the choice was to satisfy ship gates on a change the
reviewer had just judged unfinished, or to do the work in the wrong state. Both
routes ended at `--force` (BUG_037).

Read the machine as preconditions and it comes out right on its own: you may
not start making a test pass until it has failed (`green`), may not clean up
except under a green suite (`refactor`), may not land without coverage, docs
and privacy (`committed`). The retreats then carry what their *destination*
needs, which is usually nothing, and always something the session can actually
produce from where it stands.

Two roles exist to carry this. **`landing`** marks the state a finished change
lands in, and is where the gates a methodology cannot opt out of are injected —
a pipeline machine that declares a `review` state and no `landing` state is
refused at load, because a role nothing carries injects nothing and the
omission would otherwise be invisible. **`abandon`** marks a state whose meaning
is that the work is being dropped; an edge into one takes only that state's own
entry gates, since the exit gates of the state being left all exist to protect
an outcome and this is the edge with no outcome to protect. Holding it to them
made finishing the precondition of stopping.

The remaining exit gate is the docs-first read, and it stays one for the reason
below: a pipeline starts in its design state, so as an entry gate there it
could never fail.

Fields:

- **`initial`** — the state a fresh instance starts in.
- **`terminal`** — states from which no transition is required; reaching one
  completes the machine.
- **`next`** — the whitelist of legal successors. A transition to anything not
  listed is rejected by the engine regardless of enforcement level; this is
  structural validity, not policy.
- **`entry_gates`** / **`exit_gates`** — rule ids that must return clean before
  the state is entered or left. Whether a failing gate *blocks* or merely
  *warns* is policy, resolved from the manifest at evaluation time. Prefer
  entry gates: see the note above on which of the two a given obligation
  actually is.
- **`red_phase`** — optional; which of this machine's states does
  proof-of-red. Only a machine declaring `test.new_test_fails` needs it, and
  only when that state is not literally called `red`.

`red_phase` exists because a rule cannot carry a state name that only one
machine has. `test.new_test_fails` selects its evidence by the phase a run was
recorded in, was written against `tdd` — which has a `red` state — and is
declared by `bugfix`, which runs `triaging → reproducing → fixing` and has no
legal transition to any state called `red`. Its own exit gate was therefore
satisfiable only by recording a run under a phase name the machine cannot
produce, which writes into durable evidence that the run happened in a state
the pipeline was never in (BUG_030). Renaming `reproducing` was rejected: the
state is named for what it proves, and a machine's vocabulary is not a
string comparison's to set. The rule now asks the machine, and a new
methodology answers the question by existing.

A gate names a rule id. The rule's implementation lives in `core/rules`, which
is the escape hatch for logic YAML cannot express (D5).

## Domain: session

Governs isolation and the push barrier. This is the domain with real
cross-process concurrency — several sessions may run against one checkout.

```mermaid
stateDiagram-v2
    [*] --> registered
    registered --> isolated: isolate (worktree + branch)
    isolated --> working: first edit (an edit tool lands in its own worktree)
    working --> working: commit (local, granular)
    working --> reviewed: review gate passes
    reviewed --> merged: rebase + fast-forward merge
    merged --> pushed: push (once, at teardown)
    pushed --> torn_down: remove worktree
    torn_down --> [*]
    registered --> abandoned
    isolated --> abandoned
    working --> abandoned
    abandoned --> [*]
```

Invariants this domain enforces:

| Rule id | Meaning | Default |
|---|---|---|
| `session.isolated_before_edit` | No file edit while in `registered` — isolate first | block |
| `session.no_edit_in_main_checkout` | Edits land in the session worktree, never the shared checkout | block |
| `session.push_enabled` | Publishing is a session step at all (`session.push`, off by default) | block |
| `session.push_barrier` | At most one push per session, and only from `merged` | block |
| `session.ff_merge_target` | A fast-forward merge must be run from the main checkout, not inside the worktree | block |
| `session.own_file_set_only` | Commits touch only files this session declared | warn |
| `session.deliverables_complete` | A session lands only once the deliverables it recorded are done | block |
| `session.branch_is_current` | A session branch has not fallen behind the base it will merge into | warn |
| `session.named_after_isolating` | Session has a task-derived name and color | warn |
| `session.teardown_complete` | Worktree removed after push | warn |

### The one edge a hook walks: the first edit

`isolated --> working` is fired by the work itself, not by a command. Every
other edge is walked by a verb somebody runs; this one was drawn as automatic
from the start and, until BUG_062, nothing fired it — so a session sat in
`isolated` through every commit it made, `session review` refused it from
there, and every session that ever merged had either had `session transition
working` typed by hand or been forced past the edge. The decision (2026-10-20)
was to implement the trigger rather than redraw the edge as manual, because a
state that records nothing about progress is an instrument that reads the
same whatever happened.

What fires it, exactly: the **PostToolUse of an edit tool** (`Edit`, `Write`,
`MultiEdit`, `NotebookEdit`) whose destination path lies inside a session's
own worktree. The host adapter reports only *where the bytes landed and who it
is*; `SessionDomain.advance_on_edit` makes every judgement, and advances only
when all of these hold:

| Condition | Why |
|---|---|
| The writer is **identified** — `Resolution.proven`, so not the `origin` guess | An edit aimed at the shared checkout resolves to "the only session left standing", which names a session without identifying one (the BUG_028 distinction) |
| That session is in **`isolated`** | `working --> working` is the commit loop, not an edit counter, and a row per edit would stamp the heartbeat the next transition's `elapsed` is measured from; an edit after `reviewed` must not quietly retreat the session |
| The path is inside **that session's own** worktree | An edit into a peer's worktree is nobody's first edit: the writer did not edit its own tree, and the owner did not edit at all |
| The path is **not exempt** — `session.exempt_paths`, the work-item store, the report store | The exemption exists because writing those is not work on the branch, so writing one is not the session starting its work. Asked through `core.rules.is_exempt_path`, the isolation rules' own helper |

Three properties are decisions rather than consequences:

- **Only a completed call.** PostToolUse fires after the tool has run, so an
  edit refused at pre-tool-use — or one a rule is still deciding — advances
  nothing; that is the same reason `_bind_by_successful_edit` lives there.
  Pre-tool-use stays a question that does not change the world.
- **A shell write does not fire it.** A command line names no destination a
  hook can trust, and recovering one by parsing it is a guess about where
  bytes landed — the reason BUG_027 fixed `privacy.clean` by reading staged
  content rather than by widening a list of command shapes. A session that
  edits only through the shell still reaches `working` by
  `invariant session transition working`, which remains the generic route.
- **Never forced, never raised.** The edge goes through `_advance` like any
  other, so a project whose session machine gates it, or lacks it, keeps the
  session where it was; and the hook absorbs a store failure rather than fail
  a tool call that has already run (BUG_001's posture).

No migration. A record already sitting in `isolated` — with commits, as every
session before this fix did — advances on its next qualifying edit, and until
then reads as it always did. Two hooks racing on a session's first two edits
can each write the row; per-session documents are written by their owner, so
the worst case is a duplicated `isolated -> working` row, not a wrong state.

### What the record carries beyond the state

Four fields exist so that a session can outlive the agent-host session that
started it. None of them needs a migration, in either direction: every field
has a default, so a document written before they existed loads with an empty
plan, no recorded hosts and a zero resume count and behaves exactly as it did
— and a document carrying them is written back INTACT by a build that has
never heard of them, which is the half that had to be added (BUG_061, and the
round-trip contract under Storage layout below).

| Field | Holds | Read by |
|---|---|---|
| `plan` | The deliverable set this session owes, in the order it was recorded: `{"n", "title", "done", "done_at"}` per entry | `core/plan`, through four derived properties on the record |
| `plan_touched` | When the plan last MOVED | the resumable ordering, and `session.work_would_be_stranded`'s staleness check |
| `resumed_count` | How many host sessions picked this one up | the report |
| `hosts` | Every host session bound to it BEFORE the current one, oldest first | provenance |
| `handoffs` | What it told the NEXT host session: `{"note", "name", "at", "state"}` per `session handoff`, plus `"consumed": {"at", "how", "session", "host"}` once the note was claimed or dismissed | the greeting and `invariant context`, when nothing is bound |

`handoffs` is the fifth, added with `session handoff` (ADR-005) and under the
same no-migration contract. Its `consumed` key (IDEA_008) is under it too, one
level down: an entry without the key is unconsumed, so every entry written
before it existed reads exactly as it did.

Three properties of that set are decisions rather than shape.

**A deliverable's number is never reused.** `plan_add` takes `max(existing) + 1`
rather than `len + 1`, so `plan done 3` means the same thing after a
`plan remove` as it did before one. The cost is gaps in the sequence, which are
cheap and visible; the alternative costs a command that acts on the wrong entry
and looks like it worked.

**`plan_reopen` and `plan_remove` exist from day one.** `session files add`
shipped without a `remove`, and the result was a declared set with no way back
— a path nobody could delete blocking the review boundary until the missing
command was written (BUG_057). A `done` that cannot be undone is that defect one
field over, against a gate that blocks a turn.

**`plan_touched` is deliberately not `heartbeat`.** The heartbeat anchors the
per-transition `elapsed` telemetry below, so stamping it on a checklist edit
would shorten the next transition's recorded duration by however long the edit
happened to be after the last one: a plan edit silently rewriting the record of
what a *state* cost.

There is a second reason, and it is what makes the field load-bearing rather
than merely tidy. `session.work_would_be_stranded` refuses on a **cold** plan,
and a `block` must be clearable by the commands its own fixes name. `plan_done`,
`plan_remove` and `plan_reopen` each stamp `plan_touched`; had the plan reused
`heartbeat`, the one way to clear that gate would have been to stamp the anchor
of the `elapsed` measurement — a gate satisfiable only by corrupting a number.
The staleness reading is `max(plan_touched, heartbeat)`, so a session moving
through its lifecycle counts as alive without touching its checklist, and **both
clocks at zero reads as fresh**: every record written before the field existed
carries `0.0`, and failing on missing data would fire a blocking gate on all of
them at once, at `stop`, the surface with the least room to be wrong.

### The two gates the plan arms, and why they are duals

The plan exists so a session can outlive its host. That model breaks both
closeout questions unless they are asked as a pair:

| Gate | Fires at | Asks |
|---|---|---|
| `session.work_would_be_stranded` | `stop`, every turn | are these commits abandoned, or parked? |
| `session.deliverables_complete` | entering `reviewed` and `merged` | did the session finish what it said it was for? |

**`work_would_be_stranded` abstains exactly where `deliverables_complete` has
something to hold.** An open plan that moved recently is `clean` at the closeout
— the work is parked against a recorded promise, and under the multi-host model
that is the *resting* state at the end of every increment, not a defect. The
same open plan is a refusal at the landing boundary, because merging is the
claim that the session is over. Cold is the one case the closeout still refuses
on: the threshold is `session.plan_stale_days`, drawing the line between "paused
over a weekend" and "abandoned". Reversed, the two would fight — a gate firing
on every parked turn is a gate that teaches people to ignore it, and a merge
that lands a half-done checklist is the thing the checklist was written to stop.

An **empty** plan is `na` at both, deliberately, and `na` rather than `clean`:
an empty plan is not "the plan is finished", it is "no plan was made". `clean`
would be a vacuous green on a gate guarding the merge, and `fail` would make a
checklist mandatory for every one-line chore — a gate legitimate work must
override routinely is what teaches people to reach for `--force`. The skip names
`invariant session plan add`, the command that would arm it, rather than "not
applicable here" (BUG_027).

`session.deliverables_complete` is an **entry** gate on both `reviewed` and
`merged`. Both, because `reviewed` can be re-entered from `working` and the
merge is the irreversible step, so guarding only the reviewable step guards
nothing once the work moves on from it — the same argument `pipeline.complete`
carries in the machine file. *Entry* rather than exit, so the
`reviewed -> working` retreat stays walkable: a session that discovers more is
owed must be able to go back and do it without first satisfying the gate that
says it is all done (BUG_037).

`session.branch_is_current` is the third, and the one that is `warn` in every
profile. The model has always said "rebase onto the local base after every
commit"; nothing owned it, so a branch discovered it was behind at the
fast-forward merge — the one step where the fix costs a rebase of work already
declared done. It stays advisory because a stale branch costs a rebase and never
data, and a `block` whose trigger is *somebody else's commit* is one the person
holding it cannot prevent. It reads `commits_ahead(root, base, branch)`: the
same helper as the closeout rule with the arguments the other way round, which
is the whole rule.

The fixes all three name — `invariant session plan add|done|remove|show` and
`invariant session sync` — are the CLI surface over the plan; the rules landed
with the domain that answers them and the commands followed one increment
later. A fix naming a command that does not exist yet was a forward reference
on purpose: the alternative was a gate that refuses and suggests nothing.

The reference is closed, and what keeps it closed is a test rather than a
memory. A `fixes` line is a string literal, so a renamed subcommand breaks it
silently and the only reader who finds out is somebody already stopped at a
blocking gate. `tests/test_cli.py` reads every `invariant …` command named in
`core/rules` and in `domains/session` and puts it through the real argument
parser, so a fix that cannot be run is a failing test.

The plan is a **field, not a file**. It is per-session, it is read on the same
turn as the state it qualifies, and it is small, so a second document under
"Storage layout" would buy a second thing to keep consistent and a second thing
to lose. That section is unchanged by this: the layout is per-session JSON plus
the locked registry, and the plan is part of the per-session JSON.

`session.ff_merge_target` deserves its comment: merging a branch while standing
inside that branch's own worktree merges it into itself and the follow-up push
pushes an unchanged target. Both commands exit zero. The failure is invisible
without a check, which is exactly the class of problem this library exists for.

Both it and `session.own_file_set_only` ask git a question, and "git could not
answer" is not an answer: the underlying helpers (`in_linked_worktree`,
`staged_files`) return `None` on an unreadable repository and the rules FAIL on
it, in the same do-not-relax convention as `branch_files` and `unpushed_files`.
They used to default to `False`/`[]`, which read as "running from the main
checkout" and "0 staged file(s), all declared" — a clean pass exactly when
nothing had been checked (AUD-07).

**The machine's gates hold on every route.** `SessionDomain._advance` — the
one function every session transition passes through, whether it was reached
by `session review`, `session merge`, `session isolate`, or the generic
`session transition` — evaluates the exit gates of the state being left and
the entry gates of the state being entered, exactly as `PipelineDomain.advance`
always has. The gates used to be read by two hardcoded CLI call sites only, so
`session transition reviewed` then `merged` walked the lifecycle with zero
gates, `session.named_after_isolating` was a gate no code path evaluated, and
`session.push_enabled` lived a second life as a hardcoded `if` inside the push
command (AUD-05). Three consequences of the one-route design:

- **An entry gate runs while the record still reads the previous state.** A
  rule attached as an entry gate must not abstain on the source state —
  `session.named_after_isolating` abstained on `registered` and so could never
  fire on the only edge it guards (AUD-14).
- **A forced transition past a blocking gate records the override** — which
  gates were forced, and why — on the session record, next to the structural
  overrides. Silent is not an option the API has.
- **Commands with an irreversible step ask first.** `session merge` and
  `session push` evaluate the same gate list through `SessionDomain.
  pending_gates` *before* touching git, because refusing the transition after
  the merge or push has run would leave git and the record disagreeing. The
  pre-flight and the transition read one list from the machine, so they cannot
  drift.

The rules that also fire from the pre-tool-use hook on raw git commands
(`session.ff_merge_target`, `session.push_barrier`) are told which question
they are being asked: a transition *entering* `merged` or `pushed` carries the
`merging` / `pushing` context, so those gates judge the machine transition
rather than parsing an empty command string and abstaining.

### The two override lists

The session record carries two, and the difference between them is tense.

| Field | Holds | Written by |
|---|---|---|
| `overrides` | overrides that have been **spent** | a forced transition, a structural edge walked deliberately, a one-shot token consumed at a hook |
| `pending_overrides` | grants nothing has spent **yet** | `invariant override <rule> --reason` |
| `standdown` | enforcement held for an **interval** | `invariant standdown --reason` — see below |

`overrides` is the audit trail and has always existed. `pending_overrides` is
the half that was missing, and it exists because of where the gates that stop
an agent actually live. Every gate the CLI evaluates could already be passed
with `--force --reason`; twelve rules are evaluated by *hooks*, and a blocking
verdict there was final — no flag, no marker, no prompt, no recorded exception
(BUG_049). A hook fires on a tool call, which carries no argument the operator
can add and no environment a running host will re-read, so the appeal has to be
something written down **before** the call and found by it.

```mermaid
stateDiagram-v2
    [*] --> granted: invariant override <rule> --reason
    granted --> granted: a call that rule did not stop
    granted --> spent: the call it was granted for
    granted --> withdrawn: invariant override <rule> --clear
    spent --> [*]
    withdrawn --> [*]
```

The self-loop is the property worth stating: a grant is consumed only where the
verdict **actually stops the call**. A token burnt by a rule that was passing
anyway would be gone at the moment the operator needed it. Consumption happens
in the host adapter, at the delivery surface, and never inside
`core.rules.evaluate` — a rule is a question about the world, and asking it must
not change the world.

Spending one appends to `overrides` with the rule, the reason and the surface
that spent it, so the compliance record shows a gate passed deliberately rather
than a gate that quietly did not fire. An unspent grant prints in
`session status`, because a held override is enforcement the operator turned off
and may well have forgotten.

### Standing enforcement down

`Session.standdown` is the third of these fields and the only one that is a
state rather than a list. It answers the want the other two cannot: *stop
asking for the next while*.

None of the routes that looked like this were it. `loose` is an observation
mode and still blocks `session.no_edit_in_main_checkout` and `privacy.clean`,
correctly. `INVARIANT_DISABLE` takes exact ids with no wildcard and no `all`,
and an environment variable does not reach a hook mid-session anyway — the hook
subprocess inherits what the host was launched with. What was left was editing
committed project policy for the duration of one task, which either lands in a
commit by accident or quietly becomes the project's real policy (IDEA_003).

Three properties are what make it a control rather than a hole:

- **Expiry is derived, not stored.** `core.rules.standdown_in_force` recomputes
  on every evaluation whether the hold still applies, from the pipeline the
  scope names. Nothing has to run for it to lapse, which matters because an
  expiry that depends on a later command running is an expiry that does not
  happen. The default scope is the shortest useful one — the current pipeline,
  or, where none is running yet, the next one to start: a hold thrown for work
  outside a pipeline waits rather than expiring, because expiring on `pipeline
  start` would undo it before anything ran under it (BUG_052).
- **`privacy.clean` is held out unless named.** It is the one gate whose
  failure cannot be undone after the fact; nothing un-writes an identifier out
  of a published history. Folding it in costs `--include privacy.clean`, a
  separate sentence the operator wrote.
- **It is loud on every turn.** `build_context` puts it above the lifecycle
  lines, and each held rule reports `stood down — <reason>` rather than
  vanishing — so a held gate reads as a `[skip:BLOCK]` on the default verdict
  stream rather than as a pass.

### Host binding

A host session and an invariant session are **1:1**. The binding is what makes
attribution answerable at all: without it, several sessions running against one
repository are indistinguishable from the shared checkout.

It is made automatically. `session start` runs through a shell, and a shell
tool call carries no hook payload, so the CLI cannot see the host identifier —
the host adapter records it in `PostToolUse`, which has the identifier and
brackets the exact call that registered the session, reading the new session's
id out of that command's own output rather than guessing.

Two commands claim a session that way, and **exactly** two: `session start` and
`session resume`. Both run through a shell, so the CLI can see the host
identifier for neither; everything else that names a session is a query. The
narrowness is load-bearing rather than tidy. The id is read as `session <id> (`
anywhere in the tool response, and `session plan show` prints a header spelled
`plan for session ab12cd34 (add-widget) — 2/5 done`, so a pattern widened to
"any `invariant session` command" would bind this host to whatever session a
read-only query happened to name — quite possibly one the caller was only
looking at. `session status`, `session list`, `session sync` and `context` all
print the same sequence.

**The claim binds; it does not re-resume.** By the time `PostToolUse` sees a
resume's output, `SessionDomain.resume` has already incremented
`resumed_count` and written the standstill row, so the hook calls
`adopt_host_session` for both verbs rather than routing the resume back through
the domain method of the same name. A second call there would count one pickup
twice and leave two `resumed` rows describing one event. `adopt_host_session`
is sufficient, and it is sufficient because of the narrowing above: its refusal
names a **live** binding only, so a spent one goes straight through to
`_bind_host` — which is where `host_ended` is cleared and the outgoing id is
kept. What the hook owes is the half the CLI could not do, which is *which*
host session claimed it, and nothing more.

The one other thing that call carries is the handoff claim. A host launched by
`session handoff` inherits `INVARIANT_HANDOFF`, and the same PostToolUse that
binds its first `start` or `resume` passes that token to
`SessionDomain.claim_handoff` (see "A note leaves the offer three ways"). It is
here for the reason the binding is: this is the first moment a write is both
allowed and bracketed by the act it records.

Enforced in both directions. A host session that already owns a live session
cannot start a second; a session bound to a **live** host is not rebound to
another without `--force`. Rebinding silently would hand a pipeline, its
evidence and its declared file set to an owner that never did the work. A
binding whose host has *ended* is spent rather than live, and replacing that one
is a different operation — see "A session outlives its host" below.

The binding is host-to-session and **never** host-to-worktree. Registering
without isolating is legitimate — a device soak owns no code — so a session
with no worktree is fully bound and fully valid.

Three kinds of session stop competing for attribution, because none of them was
ever a real answer:

| Excluded | Why it was never the caller |
|---|---|
| **Stranded** — worktree recorded, gone from disk | Cannot be worked in. Left in, it pads every ambiguity report forever. |
| **Host ended** — `SessionEnd` fired | Whoever is asking, it is not them. |
| **Bound elsewhere** | Under 1:1 that is a definite no, not an unknown. |

A host session that owns nothing is therefore **not ambiguous** — it owns
nothing. It is told to start a session, or to claim an unowned one, rather than
asked to pick from a list in which none of the entries is it.

`SessionEnd` marks the binding spent; it does **not** abandon the session. The
session may hold unmerged work in a live worktree, and destroying that because
a terminal closed would be the library doing the one thing it exists to
prevent. `invariant session reap` abandons a stranded session only once its
branch is gone too — a worktree can be removed while its branch still holds
recoverable commits, so the narrow condition is the safe one.

#### A session outlives its host

A plan that needs several agent-host sessions runs on **one** invariant session
and merges once, at the end. Without that, the second Claude session starts a
second invariant session under the same task name, the plan forks, and two
branches hold half the work each. The binding is what has to give, and the
distinction that lets it give safely is between a binding that is **spent** and
one that is **live**.

| Binding | `host_session_id` | `host_ended` | May be replaced |
|---|---|---|---|
| Live | set | false | only with `--force` — this is the hand-over ADR-001 D19 forbids |
| Spent | set | true | yes: `session resume`, or `session adopt` |
| None | unset | — | yes |

`SessionEnd` marks a binding spent, and a spent binding has nobody on the other
end to have a pipeline, an evidence trail or a declared file set taken away from
them. That is the whole of the argument: the refusal in `adopt_host_session`
exists to stop work changing owner behind its owner's back, and there is no
owner. So the refusal is narrowed by one clause rather than removed, it now says
the binding is *live*, and it names `session resume` as the route for the other
case.

**The resumable set is the exact complement of the attributable one.**
`attributable()` answers "who could be calling right now", and excludes a
session whose host has ended because whoever is asking, it is not them.
`resumable()` answers "what did somebody put down here", and admits only those.
The two lists are disjoint by construction, which is why the predicate is
written from `live()` rather than by widening `attributable` with a flag:
widening it would make the hooks that decide whether an edit is allowed start
seeing candidates that are provably not the caller, which is the ambiguity the
1:1 binding exists to remove. Two further filters remove sessions that are not
really on offer — a **stranded** one has no worktree to work in, and one
registered elsewhere is somebody else's.

**Resume replaces the binding; it never vacates it.** The tempting shortcut is
to clear `host_session_id` and let the ordinary unbound path pick the session
up. It is a defect, and the chain is worth writing down because every link is
invisible from the line that would cause it: a vacated session rejoins
`attributable()`; a *peer* host that owns nothing and had one origin candidate
now has two; `resolve` answers `ambiguous`; `Context.rule_context` renders that
as `session=None`; and `pre-tool-use` **denies** an unattributed edit. Resuming
one session must not be able to refuse another session's tool call. For the same
reason the write clears `host_ended`: `by_host_session` filters on it, so a
session picked up with the flag still set resolves to nobody for every
subsequent hook in its new host.

**Resumption is never automatic.** A host session that finds a resumable
session has not thereby claimed it. Picking up unfinished work brings a pipeline
state, a declared file set and a branch along with it, so it is a decision
somebody makes and a command somebody runs. What *is* automatic is the refusal
in the other direction: `session start` refuses a task name whose resumable
session is sitting right there, names the address and its progress, and gives
the command that picks it up. A warning printed above a successful `start` would
be prose-as-control-flow — it does not stop the fork, and by the time anyone
reads it there are two branches. `--force` starts a genuinely separate session.

**The greeting names what it will not do automatically.** A session somebody
parked here is invisible unless it is offered, and the greeting listed only
sessions with **no** owner — so the one kind that can actually be picked up was
the single thing it could not mention, and finding it required already knowing
it was there. `SessionStart` now prints the resumable set **above** the unowned
one: a definite offer, carrying a name, a state and a progress count, above a
conditional hedged with "only if it is yours". The other order buries the
answer under the caveat. It is capped at three against six for the unowned
list, because each line carries a progress clause and runs about twice as wide
and the block is injected on every `SessionStart`; the overflow is named rather
than dropped, behind `invariant session list`. Nothing on that path writes —
`resumable()` is written from `live()` and sorts a list — and nothing may start
to, because a hook that wrote on every `SessionStart` would stamp heartbeats
and race every other host against one registry in order to say hello.

One write sits beside that path and does not contradict it, because it touches
neither this project's registry nor any session. The `hook` command registers
the project in the oracle's USER-level index ("User-level state" below) after
the greeting is computed: it reads first and takes that index's lock only for a
project the index has never seen, so it writes once per project, ever, and not
once per start. It is skipped for `doctor`'s probe, and a failure there is
swallowed whole — it can never change the greeting or mark the hook degraded.

**The turn projection reports the plan, not the checklist.** A session that
outlived a host says so once, with the count; then at most two lines from
`core.plan.summary_lines` — the next deliverable and the one after it — and
everything else behind `invariant session plan show`. It sits below the
stand-down banner, which keeps its place as the only line reporting an absence
of enforcement, and above `session may go to:`, which is machinery: the plan is
what the turn is *about*. Both halves are silent by default, so a session with
no plan that was never resumed projects exactly as it did before this existed.
An empty plan renders nothing at all rather than `(no deliverables recorded)`,
which is the opposite of what `plan show` does and deliberate: that answer is
right for a direct question and wrong for a line every project sees on every
turn, where a permanent report of an absent checklist is what teaches a reader
to skip the whole block.

Resume also refuses a **merged** or **pushed** session without `--force`: the
deliverable set already landed, and accumulating new commits onto a merged
branch puts them behind a spent `push_barrier`, which is finished work with no
route out of the session holding it.

**A name is an address.** `session start` appends the session id to the task
slug, so the name a human types identifies exactly one session. Uniqueness is
then structural rather than checked — ids are unique, therefore `slug-id` is —
and the rejected alternative, keeping bare slugs and refusing a duplicate,
refuses the case this feature exists for. Lookup is a ladder, first hit wins:
exact id, exact name, bare slug, unique name prefix. Every rung but the first is
scoped to live sessions; the first is not, so an id naming a torn-down session
is told it is torn down rather than told it does not exist.

**The resume is recorded in `history` as a standstill row** — same shape as a
transition, with `from` equal to `to`. It is not decoration. It is where the
usage counter changes identity, so the seam sits on a row that says it is a
seam; and it absorbs the dormant interval, because `elapsed` is wall time since
the last heartbeat and without this row the days between two host sessions land
on the next real transition and read as "this state took three days".

#### Handing off: an event, not an edge

`invariant session handoff --note "<line>"` is how a session hands its work to
the next host session: the note is privacy-scanned and recorded on
`handoffs`, and a new Claude Code session is launched in the main checkout with
the note as its first prompt (ADR-005). The machine above is unchanged by it,
and that is the decision rather than an omission.

A handoff changes nothing the machine governs — no worktree, branch or gate —
so it is legal in **every** state, terminal ones included. That matters,
because the documented place for it is after `merged` and `torn_down`, where no
edge exists to walk. It is recorded the way a resume is, as a standstill row in
`history` (`from` equal to `to`, carrying `handoff: <name>`), and it passes and
forces no gate. A finished session is saved and NOT re-registered: the registry
indexes sessions in flight, and teardown removed this one from it.

Which session a handoff belongs to, most reliable first:

1. An explicit `--id` or `INVARIANT_SESSION_ID`.
2. A session the call PROVES — any attribution route except `origin`.
3. Otherwise the most recently moved session registered at or above the
   working directory, in ANY state, preferring one bound to this host now or
   earlier (`handoff_candidate`). This is a guess, so the command prints which
   session it recorded on.

Where nothing is bound, the greeting and `invariant context` show the newest
**unconsumed** handoff recorded here (`latest_handoff`) while it is younger
than `session.plan_stale_days` — derived on every read, like the stand-down's
expiry, so nothing has to run for an old note to stop being offered.

##### A note leaves the offer three ways

Freshness was the only exit at first (ADR-005 D5), so a note went on being
offered — to every unbound greeting here, including the one in the very host
session launched to act on it — for most of its life after the work was already
in flight. Two exits were added beside it (IDEA_008, ADR-005 D6), and each
consumes **one** note:

```mermaid
stateDiagram-v2
    [*] --> offered: session handoff --note (recorded)
    offered --> claimed: first start / resume in the host its token names
    offered --> dismissed: session handoff --dismiss
    offered --> stale: older than session.plan_stale_days (derived, nothing writes)
    claimed --> [*]
    dismissed --> [*]
    stale --> [*]
```

- **Claimed — by the token the launcher set.** The launched line is
  `cd <main> && env -u … INVARIANT_HANDOFF=<session-id>:<at> /bin/sh <launch file>`,
  and the launch file `exec`s `claude` (BUG_075), so the variable reaches that
  one `claude` process and every hook it spawns. On the
  PostToolUse that brackets the host's first `session start` or
  `session resume` — the binding path under "Host binding" above —
  `claude_code` reads it and calls `SessionDomain.claim_handoff`, which marks
  EXACTLY that note. Not at `SessionStart`, which must not write. The first
  claim wins; the host keeps the variable for its whole life, and every later
  claim with it is a no-op.
- **Dismissed — by hand.** `session handoff --dismiss` marks the note the
  greeting is showing, recorded as `dismissed` rather than `claimed`, so the
  history says nobody acted on it.
- **Stale — unchanged.** Still derived on every read.

There is deliberately **no** "any start after `at` consumes" fallback. A note
launched by hand carries no token, and a time window would silence a note on
behalf of an unrelated session that merely started later in the same project
— which is the duplicated-work failure this exists to remove, inverted. Such a
note leaves by dismissal or staleness. Consumption is per note, so a handoff
recorded after the one a session claimed is still offered.

The marker lives on the entry — when, `how` (`claimed` or `dismissed`), the
consuming invariant session and host session — and the entry stays on
`handoffs`: the list is history, not a queue to pop. The write lands on the
HANDING-OFF session's record, which is usually torn down by then, so it takes
the handoff's own shape: a standstill row in `history` (carrying
`handoff_consumed: <name>` and `how`), saved and never re-registered, passing
and forcing no gate. A hook that cannot read the record, or a token that names
nothing, changes nothing and fails nothing — the note simply goes on being
offered.

#### How a session lands: two modes, asked rather than remembered

A session that outlives its host lands **once**, at the end of the plan, and
every increment before that ends at a commit. Which of the two a given run is
doing is a question with a recorded answer, and the reviewing agent asks the
engine for it — `invariant session plan show --json`, whose `planned` and
`complete` keys exist for exactly this consumer:

| `planned` | `complete` | Mode | What the run does |
|---|---|---|---|
| `false` | `false` | FINAL | commit, merge, sync, teardown — the behaviour that predates the plan |
| `true` | `false` | INCREMENT | commit and stop; the session stays open |
| `true` | `true` | FINAL | the run that closed the last deliverable is the run that finishes the session |

Three properties are decisions rather than shape.

**It is asked, not told.** Before this, the reviewing agent's Definition of Done
was commit → merge → teardown unconditionally, and holding a session open across
several increments meant pasting a paragraph into every invocation that said not
to merge. That is prose-as-control-flow — remembered or not, with nothing
recording which — in the library whose whole argument is that discipline
belongs in state a script owns. Five increments of this repository's own session
work rested on a human retyping it.

**The unplanned session lands, and that is why the two keys are separate.** A
session with no plan is *unplanned*, not *unfinished*, so it takes the
pre-existing behaviour. Collapsed into one flag the two cases are
indistinguishable, and every project that keeps no checklist would defer its
merge forever — which is the same failure `core.plan.progress` computes
`complete` once to avoid, one layer up.

**An engine that cannot answer falls back to FINAL.** Where the command fails or
no plan record exists, the run merges and says so. The opposite default sounds
safer and is not: it parks every run of every project silently, and unmerged
work on branches looks exactly like the tool working.

The state machine above is unchanged by this, and it is worth saying why the
absence of an edit is the right outcome. An INCREMENT run never enters
`reviewed`: it commits, rebases, marks its deliverables, and stops — which the
diagram already draws as `working --> working: commit (local, granular)`. The
loop was there for granular commits within one host session; a plan makes the
same loop span several. A mode that needed a new edge would be a mode the
session machine had never modelled.

What the two modes do change is which gates a run meets. An increment never
reaches `session.deliverables_complete` — an entry gate on `reviewed` and on
`merged` — because it never attempts either transition, which is the gate
working rather than being avoided: it refuses a landing, and no landing is being
claimed. `session.work_would_be_stranded` is the one that stays live between
increments, and it abstains on an open plan that moved recently, so an increment
that marked a deliverable done has clocked the very touch that keeps the closeout
quiet. The pair documented above as duals is what makes a parked session legal
and a half-done merge illegal at the same time.

#### Teardown will not remove the ground under the caller

Teardown's other two preconditions are git questions — a clean tree, and a
safe `git branch -d`. Neither asks whether anything *outside* git is standing
in the directory, and git will remove one that is occupied (BUG_009). When the
occupant is an agent host the result is not an inconvenience: that host's own
worktree isolation refuses every subsequent shell call and orders the caller
back into the path just deleted, and the commands that would repair it sit
behind the same refusal. The deadlock is self-sealing, so this is a **refusal**
rather than a warning — there is no in-band recovery to scroll past it into.

The precondition is a conjunction, and each half is what keeps the other
honest:

| Half | Alone it would | Why it is measurable |
|---|---|---|
| The binding is **live** — `host_session_id` set, `host_ended` false | refuse every healthy teardown: a session ending the documented way is bound to the very host session running the teardown, and `SessionEnd` has not fired because that host is still alive | it is the model above, read from the record before anything is removed |
| The worktree is **occupied** — the caller is standing inside it | refuse a cleanup that only costs one `cd`, because with the binding already spent there is nobody left to strand | the caller's own working directory, which under a 1:1 binding identifies that session's host |

Occupancy is measured from the **caller's** working directory because that is
the only evidence there is. The binding is a scalar with no notion of where
its host stands (BUG_010), so a *different* host wedged in this worktree stays
invisible; closing that needs the binding to record a location, not a wider
predicate here. `--force` remains for the deliberate case, in the same shape
as every other override in this domain.

The reads must happen **before** the removal, which is a real constraint
rather than a stylistic one: teardown reads `host_session_id` afterwards
anyway — twice for the transition's telemetry watermark, once when the record
is serialised — and a check satisfied by those reads would be a precondition
that ran after the thing it was guarding.

### Attribution

Which session a tool call belongs to, most reliable first:

1. An explicit `INVARIANT_SESSION_ID` / `--id`.
2. The host's session identifier, when the caller supplies one.
3. The session whose worktree contains **the file the call names** — the
   destination of a write, or the path a read was aimed at.
4. The session whose worktree contains the working directory.
5. The single session registered at or above the working directory.

Steps 2 and 3 exist because step 4 cannot be relied on: a host runs hooks from
the project root and may reset the working directory between tool calls, so
with several sessions live, every one of them looks identical from the shared
checkout — attribution fails precisely when the isolation rules need it.

Step 3 is what keeps an edit attributable when step 2 is unavailable, which is
the common case rather than a rare one: a session registered before the host
identifier was recorded has none, and the documented fallback —
`INVARIANT_SESSION_ID` — is an environment variable that an edit tool call has
no way to carry. A destination path depends on neither.

The order matters. `target` sits *below* the host identifier so that who is
writing beats where the bytes land: a session writing into a peer's worktree
stays attributed to the writer, which is what makes that a detectable
violation instead of a silent reattribution. And a target that belongs to no
worktree is not an answer — resolution falls through, so an edit aimed at the
shared checkout still reaches `session.no_edit_in_main_checkout` with a session
to report against.

When step 3 succeeds and the session has no host identifier recorded, the
mapping is **learned**: the next call from that host session resolves by
identifier alone, including calls that carry no path at all. Only step 3 may
teach it. Learning from a weaker signal would bind the wrong pair permanently.
Learning is reserved to a call that *wrote* — a read is evidence of where the
reader was looking, not of who owns the worktree.

**A resolution is not automatically an identification.** Steps 1–4 name the
session from something the call itself carried; step 5 names the only session
left standing. Both return a session, and only the first four are evidence.
Writing evidence on step 5 is how a *foreign* session's agent came to sit in a
pipeline that never dispatched it: a curator run in the shared checkout, under
a host bound to nothing, resolved to whichever session happened to be the sole
attributable candidate — and the pipeline that received it would then clear a
block-level `agents.*_ran` gate on work that never happened in it (BUG_028).

So the evidence writers ask `Resolution.proven`, not `resolution.session is not
None`. Enforcement and display still use step 5: telling somebody where they
probably are is a fair use of a guess, and recording a fact against them is
not.

**What may not be recorded may still have to be said.** The `proven` rule
decides what gets written; it does not decide what may be left unsaid. A
session has two copies of every file — the shared checkout's and its
worktree's — and only the worktree spelling identifies the reader, so a docs
read aimed at the shared checkout while several sessions are live resolves at
step 5 or lower and is discarded. Discarding it is correct. Its **silence** was
not: `docs.read_architecture` is block-level, and a discarded read and an
absent one produced the same verdict and the same remedy line, so the advice
the gate gave back was the action that had just been thrown away (BUG_046). An
unproven read of an architecture doc now reports the drop in the turn it
happened, on the same reasoning as an unrecordable dispatch — and with the same
restraint, since `Read` fires on everything an agent opens: only a read that
would have been evidence speaks, and only when a governed session exists to
hear it. An ungoverned turn stays silent.

**A proof of identity is not a proof of place**, and an actor row needs both.
Step 2 deliberately outranks step 3 — who is acting beats where the bytes land
— so it answers identically for an architect writing inside its own worktree
and for a curator filing a bug in the shared checkout, because a subagent
shares its parent's host identifier. Both are `host`, both are proven, and only
one of them worked in the pipeline. A *dispatch* row can live with that: at
dispatch time there is no better evidence, and the row only claims the request
resolved here. An *actor* row claims the agent worked here, so for the host
route — and only for it — the location is asked for explicitly rather than
inferred. A session with no worktree passes on the binding alone, having no
location to contradict.

Where the location contradicts the binding, the location wins and the run is
recorded against the session it landed in. That is step 2 inverted, for the
same reason the dispatch route already inverts it when a brief names a
worktree: the host is not acting there, it is delegating into it, and the run
belongs to the delegate. One binding holds one session, so a parent driving two
tracks cannot represent the second — and refusing outright would leave that
track unable to record any agent that never names a path, which is the original
wedge from the other end. The inversion is safe because it applies only when
the host names an ACTOR, and a main-loop turn never does: a session reaching
into a peer's worktree by hand stays attributed to the reacher, and stays a
detectable crossing. When a *dispatch* cannot be recorded — the route was a guess, or the
session has no pipeline — the hook says so in the turn where it happened,
because the alternative is the caller discovering it at a blocking gate at the
end of the phase, when nothing it can do will satisfy it.

**One order, every recorded call.** Enforcement resolved through this order and
recording did not: a docs read and an agent dispatch used steps 2 and 4 only,
so a `Read` naming a file inside a session's worktree was not attributed to
that session while several were live — the payload carried the answer and the
recording pulled the same key out one line later purely to store it (BUG_010,
narrow half). Both now resolve here. A call carrying no path, which is every
agent dispatch, simply finds nothing at step 3 and continues; that is the
order working, not an exception to it. What this does **not** reach is a call
whose path lies in a *different repository*: the state root is per-repository
(see "Storage layout"), so a session in another project is not in the registry
being searched at all. Attribution is within one project by construction.

## Domain: pipeline

One task's progress through the configured methodology. Scoped to a session;
a session runs pipelines one at a time.

The methodology is **pinned to the pipeline at `start`**: the record carries
the machine id, and every later operation — `status`, `gates`, `advance`, and
the rules that read the record (`pipeline.complete`,
`session.work_would_be_stranded`) — resolves the machine from the record,
falling back to the manifest only for records that predate the pin. This is
what makes `pipeline start --methodology <id>` safe (the override used to
live only in the memory of the `start` call, wedging every later invocation —
AUD-02) and what makes the gate set un-swappable by a mid-pipeline edit to
`methodology.pipeline`, which is an isolation-exempt manifest line (AUD-03).
A pinned machine that can no longer be loaded fails the gates that need it
rather than skipping; the one permitted escape is `pipeline start`, which
archives the unusable record rather than discarding it.

Shipped methodologies:

| id | For | Shape |
|---|---|---|
| `tdd` | Default. Test-first development | designing → red → green → refactor → reviewing → committed |
| `spec-first` | Design-heavy work where the spec is the deliverable | designing → specified → implementing → reviewing → committed |
| `bugfix` | Proving a reported defect is real before fixing it | triaging → reproducing → fixing → verifying → committed, or reproducing → reproduced (`red_phase: reproducing`) |
| `chore` | Mechanical work with no design content | doing → reviewing → committed |
| `docs` | Prose, records, backlog entries — nothing to test or diagram | drafting → reviewing → committed |
| `none` | Escape hatch — a single state, no gates | working |

`docs-first` is not in this list because it is not a methodology (D12). It is a
pair of gates the engine injects into whichever methodology is configured:
`docs.read_architecture` leaving the design state, `docs.updated` entering the
landing state. A methodology cannot opt out of it; a project can only retune
the gates' level, and even that is discouraged.

Details that are load-bearing rather than incidental:

- **The read gate is an exit gate, not an entry gate.** A pipeline starts *in*
  its design state, so an entry gate there would be evaluated before any work
  had happened and could never fail — a gate that looks present and enforces
  nothing. The check that means something is "you may not leave design until
  you have read the docs."
- **A fresh project has something to read.** The read gate blocks on an empty
  `paths.docs`, and `init` used to leave a new project exactly there: `doctor`
  red on the directory, the first `advance` blocked, nothing to read and
  nothing saying what to write. `init` now scaffolds a starter `overview.md`
  when the directory is absent, and the gate stays at `block`. The fix for a
  gate nobody can satisfy is to make it satisfiable, not to lower it.
- **Injection is by role, not by state name.** A methodology this library has
  never seen still receives the gates, provided it labels its states. A
  methodology that declares *no* design state — `chore` — is asserting there is
  no design content, and is exempt from the read gate and from the
  design-agent gate (BUG_064, below) but not from the update gate or the
  review-agent gate. That exemption is deliberate: forcing an architecture read onto a
  formatter pass makes `chore` useless and pushes people to `none`, which drops
  every gate. A narrower rule that gets used beats a broader one that gets
  bypassed.
- **A document has as many spellings as the repository has trees, and the read
  gate accepts all of them.** An isolated session holds two copies of
  `docs/architecture/overview.md`, and the two steps a read must survive used
  to prefer opposite ones: recording keeps only the spelling that identified
  the reader, which is the worktree's, while matching resolved its docs root
  from whichever tree the CLI was invoked in. Run from the shared checkout the
  two accepted sets were disjoint — a `block` gate closed against every read
  the track could perform (BUG_046). Matching now reduces to the docs
  directory of *every* working tree of this repository, so the verdict no
  longer depends on where the question was asked from. It stops there: another
  checkout's `docs/architecture` is not this project's, however alike the paths
  look.
- **An exit gate is asked its question after the commit.** Anything evaluated
  on leaving a state runs when that state's work is, by construction, already
  committed, so a gate that asks "what changed" by reading the working tree
  and the index alone sees nothing and returns *not applicable* — every time,
  in exactly the state it was written for. Three sources answer between them:
  the working tree and index (work in progress), the branch's own commits
  (work already committed in this phase), and the session's declared file set
  (the only one left when there is no base to diff against). All three, or the
  gate is inert where it matters most. This is `core/rules._changed_set`, read
  by `docs.updated`, `docs.diagrams_current` and `coverage.meets_floor`; it is
  one function rather than three because it was three, and two of them were
  wrong for as long as they existed (BUG_012).
- **An empty answer and an unanswerable question are different verdicts.**
  When no source could speak — the commits unreadable, nothing declared — the
  set is empty for a reason that is not "nothing changed". Those gates fail
  and say so instead of skipping, because a skip on a `block`-level gate reads
  as a pass, and an unanswered question is not a negative answer.
- **"What changed" has one answer; "is it structural" deliberately does not
  share coverage's.** `docs.updated` used to call everything outside
  `paths.docs` source, so a backlog record, a release plan or a `LICENSE`
  demanded an architecture-doc edit nobody could honestly write, and the chore
  lane's first non-code change needed `--force` (BUG_064). It now holds out
  prose (`.md`, `.rst`, `.txt`), extensionless repository metadata by whole
  name (`LICENSE`, `NOTICE`, `CODEOWNERS`, …) and the project's `non_source`
  globs, naming every `non_source` match in its verdict. It does NOT borrow
  the coverage gate's full unmeasured set: that set answers "could a run
  measure this", and configuration, markup and stylesheets are unmeasurable
  without being unstructural — a machine YAML in `data/machines/`, an API
  contract or an entry-point table changes a diagram with no code beside it,
  and still reads as source here.

- **A gate on a sweep cadence stays where the machine puts it.** Scheduling
  `coverage.meets_floor` or `test.all_pass` (`methodology.gates.schedule`,
  ADR-010) changes what the gate demands per change, not which states carry
  it: the floor turns advisory outside `held_paths`, and the suite gate judges
  the change's scoped run. Every recorded run says whether it was `full` or
  `scoped`, which is what lets a held path demand a full one.

- **`sweep.fresh` rides beside a scheduled gate, by gate id.** With a cadence,
  `core/machines` adds it to every entry or exit set naming a scheduled gate
  (`Machine.inject_beside`), so the transition that once demanded whole-run
  evidence reports how old the sweep holding it is. By id rather than by role,
  because the concern belongs to the gate: a project machine that put
  `test.all_pass` on an exit set gets it there. No cadence injects nothing, so
  `pipeline gates` never lists a rule that cannot apply. It warns and never
  blocks; the manifest refuses it at `block` or `ask` (ADR-010 D3 item 5, D6).

A note on `docs`, because the reasoning generalises. A documentation-only
change run through `tdd` blocks on three gates it cannot satisfy in principle —
no implementation means no test could have failed first, no code means nothing
to measure, and a change adding no component cannot honestly update a diagram —
and none can be acknowledged, because they block. `advance --force` was the
only route, and a tool that teaches `--force` as routine has spent the thing it
exists for.

Applicability is **declared, not inferred**. A caller choosing a methodology is
making a claim that can be wrong and can be reviewed; a rule reading the diff's
file extensions would be guessing, and would guess wrong on the change that adds
a doc and a schema together. Privacy still blocks and the docs are still read
and updated — only the diagram gate steps aside, and only for a pipeline that
has declared there is no structure to draw.

### A proof is an outcome: `bugfix`'s `reproduced`

```mermaid
stateDiagram-v2
    [*] --> triaging
    triaging --> reproducing
    reproducing --> fixing: test.new_test_fails
    reproducing --> reproduced: test.new_test_fails, privacy.clean
    reproducing --> not_reproducible
    fixing --> verifying: test.all_pass
    verifying --> committed: landing gates
    verifying --> fixing
    reproduced --> [*]
    not_reproducible --> [*]
    committed --> [*]
```

(`abandoned` is reachable from every non-terminal state and omitted.)

`reproducing` has stopping authority, and the `bug-reproducer` role uses it:
it proves the bug, commits the red test wrapped in a strict expected-failure
marker, and never fixes. Until BUG_066 the only terminals reachable from there
without fixing were `not_reproducible` and `abandoned` — both false about a
proof that fired — so `pipeline.complete` refused the session and its proof
could not reach the base branch until its fix did. `reproduced` is that
outcome: terminal, reached from `reproducing` only, priced at the recorded
red run and a clean privacy scan. It deliberately does not carry the
`landing` role, whose docs and agent gates ask for a diagram a test-only
commit cannot honestly change and for a design and review this outcome does
not claim (ADR-006 D2). The fixer starts a fresh pipeline later and records
red against the proof, which is then on the base branch (D3).

The proof also has to read as red. A strict expected failure that fails exits
clean, so on exit status alone the reproducer's run recorded as a pass. The
recorder behind `pipeline test` now reads it as a counted failure — in the
machine's `red_phase` only, for a run that named a `--selector`, when the
adapter confirms every expected-failure marker in the selected files is
strict. The record keeps its real `exit_code` and says `read_as_red: true`.
Every other phase records the exit status, because in a green run a strict
expected failure still failing is the tripwire holding, not a failure. Only
the Python adapter can confirm strictness, from a literal `strict=True` in the
source, since pytest's output prints strict and non-strict expected failures
identically; the other adapters answer "cannot tell", and in those ecosystems
the red run is recorded with the wrapper off (ADR-006 D4–D6).

### Agent-role gates

A second pair rides the same injection, and **both hang on the landing
state's entry** — `agents.review_agent_ran` and, despite its name,
`agents.design_agent_ran`. Neither guards the state it is named after, and
that is two decisions rather than one: where the design gate can be satisfied
at all (ADR-003 D2), and which EDGES a gate ought to fire on (BUG_037). The
second bullet below argues both, including the history — these were the review
state's exit gates until BUG_037 moved them, and the sentence you are reading
said so for longer than it was true. They read the subagent invocations the
host recorded as pipeline evidence, keyed by `agents.roles.<role>` in the
manifest.

They exist because installing an agent and using one are different events, and
only the second changes any outcome. A project can carry four correctly
installed agents and never dispatch one.

Six properties, each deliberate:

- **The design gate applies only where a design state exists.** Both gates
  hang on landing, but a machine receives `agents.design_agent_ran` only if it
  declares a `design`-role state; `agents.review_agent_ran` needs only the
  landing state it rides. `chore` declares no design state, so it carries the
  review-agent gate alone — the same boundary the read gate has always had.
  When the design gate lived on the design state that boundary held by
  construction; moving it to landing silently widened it to every machine, and
  a `LICENSE`-only chore needed `--force` to land (BUG_064, ADR-003 D2
  amendment). The decision is made at injection, not by the rule answering
  "not applicable", so `invariant pipeline gates` does not list the gate on a
  machine that cannot owe it. Shipped result: `tdd`, `bugfix`, `spec-first`
  and `docs` carry both; `chore` carries the review-agent gate; `none` carries
  neither.

- **Two events, not one: the dispatch and the actor.** A dispatch is observed
  from the *parent's* tool call. An agent that starts its own session and
  pipeline has neither at that moment, so the dispatch has no destination and
  no better lookup invents one — the agent that *created* a pipeline could not
  be evidence for it (BUG_028). The actor can be observed instead: Claude Code
  stamps the acting agent's type onto every hook payload, including the ones
  the subagent's own tool calls raise, so a `feature-architect` that runs
  `pipeline start` and then writes an ADR records itself in the pipeline it
  made. Both land in `agents_run`; `agents_run_how` keeps them apart, because
  `dispatch` proves somebody asked for the agent and `actor` proves the agent
  worked here, and only the second answers "did the design happen in this
  pipeline". A host that cannot name the actor omits the key and loses that
  route only.

- **The design gate does not guard the design state.** It cannot: the host
  records a dispatch when the subagent *returns*, so a design agent driving its
  own pipeline can never have its own dispatch on record before it advances out
  of designing. Placed there the gate was unsatisfiable for the one actor it
  names, and `--force` was the only route past it. The landing edge is the
  earliest boundary a *different* actor drives, which is what makes the
  evidence available. The cost — the gate guards landing rather than leaving
  design — is accepted in ADR-003 D2. Contrast `docs.read_architecture`, which
  stays on the design state's exit because a read is recorded the moment it
  happens, while the agent is still running.

  The actor route weakens this premise and is recorded as doing so, rather
  than quietly acted on: an actor reading IS available while the agent is still
  running, exactly like a docs read, so the reason the gate sits at the review
  boundary no longer holds for a design agent that drives its own pipeline. It
  still holds for one dispatched into a pipeline it does not act in. Moving the
  gate is an ADR-003 D2 decision and has not been made here; what has changed
  is that it is now a choice rather than a constraint.

  Where these gates HANG has since changed, and it is not that decision. They
  were the review state's exit gates and are now the landing state's entry
  gates. On `reviewing → committed` — the edge ADR-003 D2 is about — the moment
  and the obligation are identical; what stopped is their firing on
  `reviewing → red` and `reviewing → refactor`, which are not landing anything
  (BUG_037). D2's own sentence is "the gate guards landing rather than leaving
  design"; this is that sentence made true of the machine as well as of the
  prose.

- **Advisory in every shipped profile, including `strict`.** The engine can
  prove an agent *ran*; it cannot prove one was *needed*. A one-line fix made
  inline is a legitimate exception, and a gate that legitimate work has to
  override routinely is what teaches people to reach for `--force` on the gates
  that do matter.
- **An unconfigured role skips rather than passes.** A project with no review
  agent should see the gate report "not applicable", not report green. Those
  are different facts and `Verdict.skipped` keeps them apart.
- **A wrong agent does not satisfy a role.** The failure names what did run, so
  "I dispatched the curator" reads as the miss it is rather than as silence.

#### Warned before the dispatch, not only after it

A dispatch made while the session has no pipeline is recorded nowhere, because
there is no pipeline for it to be evidence in. The PostToolUse notice ("was
NOT recorded as evidence: session … has no pipeline running") says so, but
only after the agent has done its work. For a gated role the cost is a full
re-dispatch: a pipeline started later finds its `agents.*_ran` gate unmet.
`agents.gated_dispatch_has_pipeline` says it at PreToolUse instead, when
starting the pipeline first is still cheap (IDEA_020).

```mermaid
sequenceDiagram
    participant H as Claude Code
    participant R as rules
    participant P as pipeline
    H->>R: PreToolUse (Agent/Task, subagent_type)
    R->>R: gated_roles_of(agent) over agents.roles
    alt gated role, session bound, no pipeline
        R-->>H: [warn] … will NOT count as evidence — try: pipeline start --methodology <m>
    else otherwise
        R-->>H: silent
    end
    Note over H: the dispatch runs either way
    H->>P: PostToolUse: record_agent_run (unchanged)
```

- **A rule, not a branch in the host.** Bound to `pre-tool-use` at `warn` in
  every shipped profile, so `_pre_tool_use` delivers it through the same
  `v.warns` path as any other advisory, and a project can retune it in its
  manifest. A hard-coded host branch would have been one fewer contract key,
  but nobody could turn it down, it could not be acknowledged or stood down,
  and it would sit beside the gates it describes instead of in the same
  registry. Never blocking is a decision, not a default: the dispatch is
  legitimate, and the engine cannot tell that the agent will not start the
  pipeline itself.
- **One reading of the role mapping.** The rule asks `gated_roles_of`, which
  reads `agents.roles.<role>` through the helper the two gates read, and
  `GATED_ROLES` names which role each gate reads. An agent in an ungated role
  (the backlog curator) gets nothing. A role whose gate is `off` gets nothing
  either, because that gate asks for no evidence.
- **One answer to "which agent is this dispatch".** `AGENT_TOOLS` and
  `dispatched_agent` live in `core/rules`, and the PostToolUse recording uses
  the same two. Reading `tool_input.subagent_type` from a rule widened the
  normalised payload contract (see `overview.md`, `hosts/`).
- **Same conditions as the PostToolUse notice.** No session bound to the call
  means no warning: nobody is working under the gates. "No pipeline" is what
  `record_agent_run` calls `NO_PIPELINE`: no current pipeline record. A
  dispatch whose brief names another live session's worktree is judged
  against that session, because the rule context resolves through
  `resolution_for_payload`. A dispatch from a host rooted in another
  repository resolves no session and so stays silent; BUG_040 is that gap, and
  its fix will reach this rule through the same resolution with no change here.
- **Quiet under a stand-down, like any other rule.** The open question was
  whether an advisory should still speak. It does not. A stand-down that holds
  now also holds over the pipeline started next, so the gate the warning is
  about would be stood down too.
- **Session-recorded runs are not carried over.** Evidence is per-pipeline on
  purpose. The warning makes the loss visible early; it does not change it.

## Domain: work item

Two item types with distinct lifecycles.

```mermaid
stateDiagram-v2
    direction LR
    state "idea" as I {
        [*] --> proposed
        proposed --> accepted
        proposed --> dropped
        accepted --> in_progress
        in_progress --> done
    }
    state "bug" as B {
        [*] --> filed
        filed --> reproduced: red test, or a ruling (red with the fix reverted)
        filed --> not_reproducible
        filed --> wont_fix
        reproduced --> fixed: item.bug_reproduced_before_fix
        reproduced --> not_reproducible
        reproduced --> wont_fix
        fixed --> verified
        fixed --> reproduced
        verified --> closed: item.residue_matches_status
        verified --> reproduced
        verified --> wont_fix
    }
```

The `filed → reproduced` edge is load-bearing: a bug may not enter `fixed`
without having passed through `reproduced`, because a fix for an unreproduced
defect proves nothing. The repro-proving agent owns that transition and is the
only actor allowed to make it.

### An already-fixed bug closes through a ruling, not a new edge

`bug-reproducer` has a fourth verdict besides "red", "blind" and "device
only": ALREADY-FIXED — the test passes on the base branch, goes red with a
named commit reverted, and that commit is the fix. The only engine route from
`filed` used to be `not_reproducible`, which records "we tried and it did not
fire" about a defect that was real and is fixed, so a consuming project
hand-edited the status instead and kept no record of the ruling at all
(IDEA_019).

`invariant item transition <id> fixed --ruling "<who, date>" --evidence
<commit>` records it, and **the bug machine is unchanged**. Two shapes were
weighed:

| | A `filed → fixed` edge, legal with a ruling | One ruled command recording `filed → reproduced → fixed` |
|---|---|---|
| The invariant above | Broken in its literal form; the gate would have to learn an exception | Kept: `fixed` is still reached only through `reproduced` |
| `item.bug_reproduced_before_fix`, `item.history_matches_status` | Both need a second accepted shape | Both read the history as they always have |
| What the reproduction row claims | — | A reproduction on the pre-fix tree, which is exactly what a test red with the fix reverted is |

The second is chosen because the invariant's reason is satisfied rather than
waived: the reason "a fix for an unreproduced defect proves nothing" is about
evidence, and a test that goes red with the fix reverted IS that evidence. The
row does not pretend it was walked: it carries `intermediate: true` and
`item show` prints "recorded on the ruling, not walked".

What the command does, in order:

1. `--ruling` and `--evidence` come together or not at all; either alone is
   refused, naming the one that is missing. The evidence is resolved to a full
   hash through `core/git.resolve_commit`, and a name that is no commit here is
   refused — a ruling resting on a string nothing can check is not provenance.
2. Where the machine has no direct edge, the route is the **one** state both
   edges reach (`_one_step_between`), read from the machine rather than named
   in code. None, or two, and the edge is refused as before — `filed →
   verified` is two unwalked states, and a ruling vouches for one. Without a
   ruling, the refusal names the ruled route instead of leaving `--force` as
   the only visible way past it.
3. Each step is gated as if it were walked — the exit gates of the state left
   and the entry gates of the state entered, with the item as the previous
   step left it — and **nothing is written unless every step passes**. Then
   one save writes both rows.
4. Every row the command writes carries `ruling` and `evidence`; a ruling on
   an edge that does exist (`reproduced → fixed`) annotates that one row.

Who may rule is recorded, not enforced. The ruling text names the person and
the date, and `bug-reproducer` still never runs the command itself: it reports
ALREADY-FIXED with the commit and names this command, and the user runs it. A
verdict without the revert-red check — the test passed, the commit was found
by `git log` — is not a reproduction, and its route stays `not_reproducible`.

**Body text.** A transition writes status and history and never rewrites the
body, on every backend — pinned by a round-trip test per backend on this
path, including the `sphinx_needs` fixture BUG_065 was fixed against. On
`sphinx_needs` the ruling itself is not yet stored: that backend persists no
history (BUG_073), so there the status is right and the provenance survives
only in version control until that lands.

**Retirement is deferred.** Moving a closed entry into a backend-declared
closed store in the same transition is the other half of IDEA_019. Its
manifest key now exists: `items.closed_path`, unset by default so the markdown
backend grows no archive, landed with BUG_074 (c), and today it is READ — id
minting and `item new`'s explicit-id check both count the ids retired there
(manifest.md, "The ids a store has spent, and the closed store"). Nothing
writes it. Retiring on close stays deferred because none of what would make it
safe exists yet: a move touches two files, so it is not one atomic outcome
without a recovery story for a write that lands in one and not the other; the
closed file has its own layout, and placing an entry there is the placement
question BUG_074 (b) answered for the open store only; and the only backend
that wants a closed store today is the one that cannot yet keep the ruling
(BUG_073).

A separate field records *residue* — what remains owed after the status says
done, such as a device soak that has not been run. Status and residue are
distinct on purpose: a status of `fixed` with an unfulfilled residue is a
normal, honest state, and collapsing the two produces documents that claim
more than was proven. `item.residue_matches_status` guards the closing state
on **entry** in both machines — `bug.closed` and `idea.done`. Entry rather
than exit because both are terminal: nothing ever leaves them, so an exit gate
there is never evaluated and an entry could close owing three legs with the
gate named to prevent exactly that staying silent.

### The store the gates cannot sit in front of

Work items are data, and `items.exempt` holds them out of session isolation —
deliberately: every session touches the backlog, and routing it through a
branch produces conflicts with no review value. The cost is that the FSM's
invariants bind only callers who go through the CLI. Writing the file directly
never asks the machine, so it is never refused, and nothing records that
anything was skipped. Seven bugs in this library's own backlog reached `fixed`
with an empty history exactly that way.

`item.history_matches_status` covers the gap from the other side. It is bound
to the `stop` event rather than to any transition — there is no transition to
attach it to — and at turn end it re-derives each item's status by walking its
recorded history through that type's machine, reporting the ones that
disagree: a status no history explains, a history that ends somewhere else, an
edge the machine does not have. A `forced` entry is not drift; an override
went through the machine, was refused, and recorded a written reason, so
flagging it would report the audit trail working.

This is the same shape as the memory domain's index check below — not "you may
not do this", but "this store no longer says what it means".

## Domain: memory

Not an FSM. A store with invariants and repair operations.

| Invariant | Check | Repair |
|---|---|---|
| Every file parses | Valid frontmatter, known `type` | report only |
| The index matches the directory | Every file has an index line; every line resolves | regenerate index |
| No dead links | Every `[[link]]` resolves, or is flagged as intentional-forward | report only |
| No duplicates | No two files cover the same fact | report candidates |
| No personal data | Privacy patterns find nothing | report with offsets |

The index is a **generated artifact** (D-memory, per interview): regenerating
it is a command, not an editing task. A hand-maintained index over hundreds of
files drifts, and the drift is silent.

The first four rows are the four `memory.*` rules, and all four are bound to
the `stop` event and to nothing else. They are not pipeline gates on purpose:
memory hygiene is a property of a knowledge store every session shares, not of
the change under review, so holding a merge hostage to a stale index in a file
the branch never touched would be a gate answering about someone else's work.
Turn end is when the drift is worth naming and cheap to fix. They short-circuit
to "not applicable" where `memory.managed` is false, so a project that does not
use the domain hears nothing.

Before they carried that binding they were reachable by no code path at all —
graded by all three profiles, evaluated by none of them, which is an
enforcement level reported for a control that never ran.

## Domain: stats

Not an FSM either. A store with invariants and repair operations, added by
ADR-002.

| Invariant | Check | Repair |
|---|---|---|
| Every record parses | Readable JSON with a known shape | report only |
| Records match their source | Stored watermark equals the source's | `stats refresh` |
| Nothing is silently lost | A record whose transcript is gone is archived | `stats archive` |
| Every model is priced | No model outside the price table | report only |
| The report matches the store | Fingerprint in the artifact equals the store's | `stats report --write` |

### Selecting a record

A record is addressed by an identifier — the host session's or the invariant
session's — or by the invariant session's name, climbing the same ladder
`SessionDomain.resolve_name` climbs: full name, bare task slug, name prefix.
It has to be the same ladder because it is the same question, and the
precedence is written down once, in `domains/session.name_rank`, which both
callers read. The predicate beside it answers only *yes*, so a bare-slug match
and a prefix match come back indistinguishable; a caller choosing between
records needs the rung, not the answer.

Two properties are decisions rather than shape.

**A selector that matches several records refuses.** The rungs get narrower, so
a tie inside one rung is a genuine tie, and the wider rung below it must not be
allowed to break it. Taking the first hit would archive or render one session's
telemetry under another's name — quietly, since every figure in the output
would be internally consistent. The refusal lists the candidates by full name,
which carries the id and so cannot be ambiguous.

**The join is scanned, not indexed.** Records outlive the sessions that
produced them, and a name is read off the record rather than off a live
session, which is exactly why the ladder is available here at all.

One ambiguity has no remedy and says so instead of offering one: several
records carrying ONE name are one invariant session measured across several
host sessions, and no selector this domain accepts separates them. It is
unreachable today — `_bindings` joins the session's CURRENT binding only, so
each session claims one record — and it becomes reachable the moment the
earlier hosts recorded on `Session.hosts` are joined too. That join is filed
as IDEA_007 rather than taken here: mapping every id in `hosts` to the session
would hand `_breakdown` the same session id N times and report the session's
whole spend against each of N records.

### What a transition records

Each recorded pipeline and session transition carries three fields beyond
`{from, to, reason}`:

- **`elapsed`** — seconds spent in the state just left.
- **`usage`** — the cumulative token counts observed for that host session at
  that moment: a **watermark**.
- **`usage_host`** — the host session that watermark was counted against. The
  machine's baseline carries the same pair (`usage_at_start`,
  `usage_at_start_host`).

Per-state duration is `elapsed` directly. Per-state spend is the difference
between consecutive watermarks, with the machine's own starting watermark
(`usage_at_start`) as the earlier reading for the first state.

A difference is only taken between two readings of the **same** counter. A
forced rebind moves the binding mid-run, so the readings either side of it
count two different transcripts; subtracting them is well-formed and measures
nothing, and where the new transcript's total is the higher it comes out
positive and looks exactly like a measurement. Recording which counter each
reading came from is what makes that visible rather than plausible. Two
*equal* readings are declined for the same reason in the other direction: they
say nothing was written to the transcript in between, which is what a whole
subagent run looks like from the parent's side, and never that the state was
free.

Neither is a clock time, so the privacy rule is satisfied by construction
rather than by scrubbing. The anchor `elapsed` is measured against is
*derived*, not stored — a pipeline's is `started` plus every `elapsed` since,
and a session's is the heartbeat it already stamped — so no second timestamp
was added to make this work.

This is a better design than the timestamp it replaces and not only a
compliant one. Attributing spend by comparing a response's timestamp against a
transition's has a boundary problem: a response spanning a transition must be
assigned to one side arbitrarily, and the arbitrariness is invisible in the
output. A watermark has no boundary; a subtraction of two counts is exact.

The cost is a **permanent asymmetry**. Every transition recorded before this
existed has neither field and can never acquire one, because the information
was not merely unstored — it was never observed. One entry missing `elapsed`
makes the whole machine's breakdown unavailable rather than partial, and the
report says "no breakdown available" explicitly. Never a zero, and never a
blank: a blank invites the reader to conclude the work was free.

That sentence is reserved for that case. A machine that has not left a state
yet, and a session that has never started a pipeline, also have no breakdown —
and neither of them lost anything, so neither is told it predates the
instrumentation. The report says which of the three it is, because a stated
wrong cause is harder to catch than an absence: the reader has been given a
reason and stops looking for one.

### Storing what was measured, not when

A stats record holds durations and a bare date and no timestamp of any kind.
The host's transcript timestamps are read at the boundary, turned into a wall
duration, an active duration and a date, and discarded. The cache key is a
content fingerprint rather than a modification time, for the same reason: an
mtime is a stored clock time, and it answers the wrong question anyway — the
cache asks whether the source changed, not when.

## Domain: sweep

Not an FSM either (IDEA_032 increment 3, ADR-010 D4, D7, D9). A sweep is one
command, `invariant sweep run`, that holds the gates a cadence moved off the
per-change path: it runs the whole suite once and files what is new since the
last sweep.

```mermaid
sequenceDiagram
    participant S as sweep run
    participant G as git
    participant P as pipeline seam
    participant A as adapter
    participant W as workitem
    S->>G: main checkout? a branch? no tracked change?
    S->>G: last committed record on this branch (ls-tree, show at HEAD)
    S->>P: one run: commands.coverage, else commands.test
    S->>A: failing_test_ids
    S->>S: core/sweep diff: new, still broken, recovered, floor drops
    loop each new red, at most 20
        S->>P: commands.test_one alone: red again, or green (flaky)
    end
    S->>W: open items carrying sweep_key (non-terminal states)
    S->>W: new bug per unclaimed key, with sweep_key and sweep_range
    S->>S: write the record, privacy-scan it, the message and the items
    S->>G: commit --only the record and the filed items
```

| Invariant | How |
|---|---|
| The commit range is true of a committed tree | Refused outside the main checkout, on a detached HEAD, or with a tracked change; checked before anything runs. The sweep's own writes come after the check, so they never count against it |
| A key is filed once | Dedup against OPEN items (the machine's non-terminal states, so `fixed` and `verified` count); `test:<id>` and `flaky:<id>` claim each other, so a hand-filed item is claimed by adding `sweep_key` to its frontmatter |
| A flake is not a new red | Each new red is re-run alone; green alone is `flaky:<id>`; a re-run that executed nothing is not proof of green |
| A systemic red is one item | More than 20 new reds files one `suite:red` and no per-test items |
| The first sweep files nothing | No record on the branch: the run is recorded as the baseline |
| A baseline is evidence | Records are read from git at `HEAD`, never the working tree; an uncommitted record is never a baseline, and an unreadable one is named and skipped |
| Two sweeps cannot double-file | One lock under the state root, taken without waiting; a second sweep is refused |
| Nothing measured, nothing recorded | A run that executed no tests, or a coverage run with no rows, raises before any write |

A floor drop is a HELD file below the floor now that was not below it, or did
not exist, at the last sweep. Both sides are judged by one policy,
`core.rules.sweep_below_floor` — the floor's own `non_source`,
`coverage_exclude` and `coverage_baseline` ratchet — so a sweep and a change
cannot disagree about which file is held. When the last sweep ran no coverage,
files under the floor are reported and not filed: there is no previous figure
to have dropped from. Recovered keys are reported and never closed; closing a
bug goes through its own lifecycle.

### Freshness

`sweep.fresh` (ADR-010 D6) reads the same committed records, at the session's
BASE branch rather than `HEAD` — a session's own branch never carries a sweep,
and a worktree cut before the latest sweep still sees it on its base — or at
the checked-out branch when no session is bound. The last record is the
highest `seq` on that branch; its age is today's UTC date minus its bare
`ran_on`, in whole days. No record, a branch git cannot read, or a date that
cannot be parsed warns; an age past `cadence_days + grace_days` warns naming
the age and commit; otherwise the verdict is clean and still names the age.
Nothing here is stored: the answer is recomputed from git on every
evaluation, so a clone that has not pulled the base sees an older sweep and
warns, which is the right answer for evidence it does not hold.

### The record

One JSON file per sweep, `<paths.sweeps>/<date>-<first 12 hex of head>.json`,
committed by the sweep itself with the items it filed. Version 1 fields:
`version`, `seq` (one more than the last on the branch), `ran_on` (bare UTC
date), `branch`, `head` and `previous` (full hashes; `previous` null on a
baseline), `command` (the manifest key, `coverage` or `test`, never the
resolved command, which names a bundle under the state root), `executed`,
`failed`, `passed`, `failing` (test identities, null where the adapter cannot
read them; flaky ones excluded), `flaky`, `files` (`{project-relative path:
pct}`; rows outside the project dropped), `floor`, `hazards` (ids), `filed`
(`[{key, item}]`), `still_broken` (`[{key, item, status}]`) and `recovered`
(keys). `passed` is beside `failed` because an adapter that counts nothing
reports `None` for a green run and a red one alike.

No clock time, host, session or absolute path is in it, so it is
privacy-clean by construction, and checked anyway: the engine scans the record,
the commit message and the filed items with the project's own patterns before
committing, because its commit is a subprocess no hook sees. A finding leaves
all three uncommitted and exits 1; the record is untracked, so it blocks
nothing and is never read as a baseline.

## Storage layout

```
<git-common-dir>/invariant/state/
  registry.json           cross-session index; the only locked file
  sessions/<sid>.json     one file per session
  pipelines/<sid>.json    current + historical pipelines for a session
  stats/<host-id>.json    one telemetry record per host session; derived and
                          rebuildable, except where `stats archive` has
                          promoted it to permanent
  attention/<host-id>.json  one per host session: what it is waiting on, its
                          pid, and how its last turn ended. Written by that
                          host's hooks; read by the oracle (ADR-007 D5)
  sweeps/<kind>.xcresult  the sweep's per-run result bundles, cleared before
                          each use; never committed (ADR-010)
  locks/registry.lock     advisory lock file
  locks/sweep.lock        held for the whole of a `sweep run`, so two sweeps
                          cannot file at once
```

The sweep's records are the one exception to "runtime state is never
committed", and they are not runtime state: they live in the declared store
`paths.sweeps` (default `docs/sweeps`), are committed, and are read back from
git rather than from disk (Domain: sweep, above).

**Why the git common directory.** State has to be shared by every worktree of a
repository. It cannot live under the project root, because each linked worktree
*has* its own project root — a session created from the main checkout would
then be invisible from the worktree it had just created, at exactly the moment
it needs to be addressable. The git common directory is the one location every
worktree resolves identically, and git already ignores it, so nothing has to be
excluded by hand. `paths.state` overrides this for a project outside git or one
that wants state somewhere specific.

**Finding the current session.** An explicit `INVARIANT_SESSION_ID` wins.
Otherwise the session whose worktree contains the working directory does, with
the deepest match winning so a nested worktree beats its parent. Failing that,
a session is matched by where it was registered — without which a session that
has not isolated yet, having no worktree, would be unreachable by the command
that isolates it. Ambiguity there is reported with the candidates listed rather
than guessed at.

**Concurrency.** Per-session files are written only by their owning session, so
they need atomicity but not locking: write to a temporary file in the same
directory, then `os.replace`, which is atomic on a single filesystem. The
registry is written by every session, so it is guarded by an advisory lock
(`flock`) held across a read-modify-write. Lock acquisition has a timeout; a
timeout is reported as a real error rather than waited out forever, because a
wedged lock must be visible.

**The round trip, in both directions.** A record is a dataclass, and the two
builds at either end of a document need not be the same one. Forward: unknown
keys never reach the constructor and every field has a default, so a document
written before a field existed loads with that field at its default. That is
what makes a new field cost no migration, and it was designed and tested from
the start. Backward: the keys a build does not declare are KEPT on the record
and written back at the top level, so a build older than a field does not
delete it. That half was not designed, and its absence was live — the editable
install on `PATH` points at the main checkout, so while a feature branch adds
fields every hook subprocess writes the record through a dataclass that lacks
them, and a session's whole deliverable plan could be erased by a command that
reported success (BUG_061).

Both per-session documents carry the contract — `sessions/<sid>.json` and
`pipelines/<sid>.json`, from one implementation in `core/store`, because two
copies of one rule is two chances for a build to honour half of it. The stats
records deliberately do not: they are derived and rebuildable, so a key lost
there is recovered by re-deriving it, and a session document has no such
second source.

Three properties of the preserved set, since a reader will ask:

- **Preserved keys travel at the top level, not in a container.** They are
  written back where the build that owns them will look, which is where they
  were. Nesting them under a key of their own would be the same deletion one
  indirection along, because no other build's loader reads that key. The
  attribute they are held in — `_extra` — is reserved in the format and never
  written: a leading underscore where every real field is public says
  "structure, not data" to somebody reading the file by hand, and the loader
  holds the name out of its own field match, so a document that carries a key
  spelled that way round-trips as ordinary data rather than being expanded
  into the record.
- **A declared field always wins a collision.** The live attribute is what the
  running code means; a preserved value is by definition one that build could
  not interpret. The other polarity would discard the value the code had just
  computed, which is this defect inverted and harder to see.
- **It rescues nothing already on disk.** A build that predates the contract
  has neither the fields nor the preservation, so this stops the NEXT
  asymmetry rather than the one currently installed. The alternative — a
  version stamp on the record and a refusal to write on downgrade — has the
  same limitation and a worse failure: the older writer is usually a hook
  subprocess, which cannot usefully refuse, since a hook that errors turns
  every tool call in the repository into a failure and one that proceeds has
  lost the data anyway.

The registry is untouched by all of this, and deliberately so. Its per-session
entry is a PROJECTION of four attributes — name, state, worktree, branch —
rebuilt from the record on every write rather than round-tripped, so there is
nothing there for an older build to drop.

**Recovery.** Everything is JSON, hand-inspectable, and hand-fixable.
`invariant state show` prints it; `invariant state repair` reconciles the
registry against the session files and against the actual worktrees on disk,
which is what a stale registry entry from a crashed session needs.

**Staleness.** A session file whose worktree no longer exists is stale. The
registry records a heartbeat updated on each transition; `repair` treats an
entry with no live worktree as abandoned rather than deleting it outright, so
that evidence of what happened survives the cleanup.

## Attention and host liveness

The session record says where a session is in its lifecycle. It cannot say
whether its host is sitting on a permission prompt right now, whether the last
turn ended on a refusal, or whether the host is still alive. Those facts belong
to a HOST session, which may have no invariant session at all. They change on
every turn. The oracle reads them from a sidecar per host (IDEA_012, ADR-007
D5):

```json
{"host_session_id": "<id>", "pid": 4242,
 "waiting": {"kind": "permission|question|idle", "message": "<raw>", "since": "<iso>"},
 "blocked": {"rules": ["<rule id>"], "surface": "stop|cli", "since": "<iso>"},
 "turn": "running|ended", "turn_since": "<iso>",
 "ended": false,
 "degraded": {"event": "<hook>", "error": "<type: message>", "since": "<iso>"}}
```

Every field may be `null` or absent. A record nobody has written means nothing
is known about that host.

| Property | Why |
|---|---|
| **One writer, atomic replace, no lock** | A file is written by its own host's hooks, which run one at a time, so this is the per-session-file argument above. The other writer is a refused CLI command, which writes the record of the host bound to the refused session, and that host is the caller. |
| **Read-first where it fires constantly** | `PostToolUse` runs after every tool call. It reads, and writes only when something was waiting. `SessionStart` writes nothing, so the rule that a hook must not write on every start still holds. |
| **Clock times, machine-local** | `since` and `turn_since` are UTC wall-clock times, to the second. They are the exception to "Storing what was measured, not when", and they are scoped by where they live: under the state root, which is never committed or rendered (ADR-007 D9). |
| **Unknown keys survive; an unreadable record is replaced** | The BUG_061 contract, as for the session records. The difference is that this record is derived from events, so a corrupt one is overwritten by the next event instead of wedging every hook. |
| **A write never changes a verdict** | One guarded helper in the CLI applies every write after the response is computed and swallows any failure. `doctor`'s probe writes nothing (ADR-007 D10). |

**Host liveness.** `pid` is the first process id this library stores, and a
pid is a claim that can go stale. The hook records `CLAUDE_PID` when Claude
Code exports it to its hooks. Without it, a pid already on the record stands,
and a host with none walks the parent chain to the first process named
`claude`. `SessionEnd` clears the pid. The reader, which is the oracle's scan,
treats a host as alive only if the pid exists and `ps` still names it
`claude`. Existence is checked on every scan; the name is kept for 60 seconds
and forgotten the moment the pid is seen dead. Both run at read time, because
the pid can be reused after the host exits. A host that crashed without
`SessionEnd` therefore stops reading as "waiting" once its process is gone. A
record with no pid has unknown liveness, which no signal reads as gone. The
scan reads the record through an `InertStore` and never writes it (see
[oracle.md](oracle.md)).

**A second source for the pid** (IDEA_024). Claude Code keeps its own
per-process record, `<claude home>/sessions/<pid>.json`, for every running
process. It is Claude Code's state, not this library's: the oracle reads it,
never writes it, stores nothing from it, and opens only the `<digits>.json`
files, never the `.key` files beside them. A host with no attention pid takes
the record's, which is checked as above plus one more test, the record's
process start against the live process's, against a reused pid. When the two
sources name different pids the row says so and its liveness is unknown. A
missing or unexpected record format leaves this section's rules exactly as
they are, and the scan reports the source as not used ([oracle.md](oracle.md),
"Host liveness").

**Two surfaces of "blocked".** The Stop hook records the closeout's blocking
rule ids, and a gated CLI command (`pipeline advance`, `session transition`,
`review`, `merge`) records the rules that refused it. A clean Stop clears only
a Stop block. The oracle reports a block once the turn has ended, so a CLI
refusal must outlive the Stop of the turn it happened in. The next prompt clears
both, and a CLI transition that succeeds clears a CLI block (ADR-007 D8).

## User-level state

Everything above is per repository. The Session Oracle (IDEA_012, ADR-007)
needs one thing no repository can hold — the list of every invariant project on
the machine — so it keeps a directory per USER:

```
$INVARIANT_HOME, else ~/.invariant/
  projects.json         the project index
  locks/projects.lock   advisory lock for its read-modify-write
  locks/alerts.lock     advisory lock for the ledger's read-modify-write
  oracle/               the watcher's derived state; safe to delete
    snapshot.json       the last tick's assessed view of every project
    alerts.json         the rising-edge de-dup ledger
    watcher.log         the launchd agent's stdout and stderr
    web.json            the web page loop's report: waiting or serving, where
    web.log             the web page agent's stdout and stderr
  web/                  the web page's records; NOT derived (0700)
    devices.json        token hashes and pending pairing codes (0600)
    actions.log         what the page ran: a start and an end line each (0600)
    tls/                the page's certificate (0700)
      oracle.pem        Tailscale's certificate chain, then its key (0600)
  locks/devices.lock    advisory lock for its read-modify-write
```

```json
{"version": 1,
 "projects": [{"key": "<state root>", "root": "<main checkout>",
               "name": "<project.name>", "via": "init|session-start|manual"}]}
```

| Property | Why |
|---|---|
| **Keyed by `Manifest.state_root`** | A project is the place its sessions are recorded. Every linked worktree resolves the same git common directory, so keying by it collapses a repository's worktrees — and several manifests sharing one repository — to one entry. Keyed by root, each would list the same sessions again. A project that sets a relative `paths.state` has a state root per checkout and is, honestly, one entry per checkout. |
| **`root` is the main checkout's spelling** | A worktree is torn down; the main checkout is where a person would `cd`. A registration made from inside a worktree records the same relative position in the main checkout when a manifest is there, else the root as found. |
| **No clock times** | An entry says what a project is and how it arrived, never when — the same rule as "Storing what was measured, not when". |
| **Locked writes, read-first registration** | Several hosts can start in several projects at once, so a write is a read-modify-write under `JsonStore.locked`. The automatic route reads first and locks only when the project is missing; a race lost inside the lock keeps the first registration. |
| **Unknown keys survive** | Entries and the document are mutated in place, never rebuilt, so a field a later build adds is written back by this one (the BUG_061 lesson, applied to a store that is not per-session). |
| **A newer `version` is refused, not rewritten** | Rewriting would drop whatever the new version means, in the one file every project's registration goes through. |

Three routes add an entry: `init` (`via: init`), the first `SessionStart` in a
project (`via: session-start`), and `invariant oracle projects add`
(`via: manual`). An existing entry is never rewritten by a later route, so
`via` records how the project first arrived. `remove` and `prune` change only
this index — nothing under any project is touched — and `prune` drops an entry
only when its `root` no longer carries a manifest.

`doctor`'s probe writes nothing here either: the registration is skipped
wherever the hook's store is the inert one (BUG_059). The test suite points
`INVARIANT_HOME` at a temporary directory for every test, so no test can add to
the index on the machine running it.

### The watcher's derived state

`oracle watch` (IDEA_012 increment 4) keeps two files under `oracle/`. Neither
is a record of anything: each is derived from the projects' own state on every
tick.

```json
{"version": 1, "generated": "<iso>", "watcher_pid": 4242, "interval": 5.0,
 "home": "<oracle home>", "now": 1792640000.0,
 "projects": ["<each project entry of oracle list --json>"]}
```

```json
{"version": 1,
 "alerts": {"<project>|<subject>|<kind>|<discriminator>":
              {"since": "<iso>", "notified": true}}}
```

| Property | Why |
|---|---|
| **Derived, safe to delete** | The snapshot is rewritten every tick. Deleting the ledger costs at most one repeat of each live alert. A ledger that cannot be read, or has another version or shape, is replaced and not refused, because nothing but the watcher writes it and nothing is lost by starting it again. The index, which is a source of truth, refuses instead. |
| **Atomic replace** | A reader, such as the planned TUI, never sees half a snapshot. |
| **The ledger is locked** | The daemon and a `watch --once` run at the same time must not both announce one edge, so the read-modify-write takes `locks/alerts.lock`. The snapshot is not locked: the last writer wins, and either one is a whole tick. |
| **Clock times, machine-local** | `generated` and `since` are UTC to the second, the attention sidecar's spelling, and `now` is the assessed epoch `oracle list --json` already prints. They are the second exception to "Storing what was measured, not when". They are scoped by where they live: outside every repository, never committed or rendered, and outside what `privacy.clean` screens (ADR-007 D14). |
| **The log is output, not state** | `watcher.log` is where launchd sends the agent's two streams (`oracle install`, ADR-007 D17). Nothing reads it back; it holds the ticks with news and the failed ticks the loop survived. It is appended to, not rotated, and safe to delete. The agent's plist lives outside the home, in `~/Library/LaunchAgents`, because launchd reads it there. |
| **`notified: false` is owed, not spent** | An edge seen with no notifier, or whose delivery failed, is delivered by the next tick that can deliver it. A key that is no longer seen is dropped, so the condition returning is a new edge. A project that could not be read keeps its keys. |

### The web page loop's report

`oracle web --wait`, the mode the web page's LaunchAgent runs (IDEA_023
increment 3, ADR-008 D16, D17), writes `<oracle home>/oracle/web.json` each
time its state changes:

```json
{"version": 1, "pid": 4242, "state": "waiting|serving",
 "addresses": ["<tailnet address>"], "port": 7341,
 "urls": ["https://<host>.<tailnet>.ts.net:7341/"], "since": "<iso>", "error": null,
 "scheme": "https|http",
 "certificate": {"name": "<host>.<tailnet>.ts.net", "not_after": 1800000000.0,
                 "expires": "<iso>", "error": null}}
```

`scheme` and `certificate` were added by IDEA_026 (ADR-008 D23), within
version 1: a reader that does not know them reads the rest as before.
`certificate` is the one being served (`name`, `not_after` and `expires` are
`null` over plain HTTP) and `error` is why the last request to Tailscale
failed, until one succeeds. Over plain HTTP `urls` lists each address with
`http://`, as before.

| Property | Why |
|---|---|
| **Derived, safe to delete** | It says what one running process is doing, nothing more, so it sits in `oracle/` beside the snapshot. The loop removes it when it stops; `oracle uninstall` removes it after the `bootout`, because launchd's SIGTERM ends the process first. A report left by a process that died says so: its `pid` is checked alive on every read. |
| **Written on a change, not on a timer** | `waiting` to `serving`, a new address set, a new bind error, a certificate renewed, lost or failing. `since` is when the state began, so a steady loop writes nothing. Its pid's liveness, not the file's age, says whether anything is behind it. |
| **`error` means waiting for a reason** | A bind that failed (a port in use) leaves `state: waiting` with the error. `doctor` fails on it; waiting for Tailscale with no error passes. |
| **Clock times, machine-local** | `since` is UTC to the second: the D14 exception again, under the home and never committed. |

`web.log` is the agent's output, like `watcher.log`: appended to, never read
back, safe to delete. It holds the state changes and never a request line.

### The page's certificate

`<oracle home>/web/tls/oracle.pem` is the HTTPS certificate the web page
serves (IDEA_026, ADR-008 D19): the chain Tailscale issued for the Mac's
MagicDNS name, followed by its private key, as PEM.

| Property | Why |
|---|---|
| **One file** | `ssl` loads the key from the certificate's own file, so a crash can never leave a certificate beside another one's key. |
| **Owner-only from creation** | `web/` and `web/tls/` are `0700`; the file is written through a temporary file created `0600` and exclusively, then renamed over it (`home.write_private`). The key is never readable by anyone else, not even briefly. |
| **Replaced only by a pair that loads** | A new one is written to `web/tls/candidate.pem`, loaded once, and renamed over `oracle.pem`; one whose key does not match, which names another host, or which has expired is removed and the kept one stays. |
| **Neither a record nor a report** | It is not derived from anything invariant holds, but deleting it costs only one request to Tailscale, which keeps its own copy. `oracle uninstall` leaves it, with the rest of `web/`. |
| **No clock time stored** | Its expiry is read from the certificate itself whenever it is needed. |

### The paired devices

`invariant oracle web` (IDEA_023, ADR-008) answers only a device that holds a
token, and `<oracle home>/web/devices.json` is where those devices are
recorded. It lives in `web/` rather than `oracle/` because it is not derived:
deleting it unpairs every device, and only `oracle web pair` and a phone can
make it again.

```json
{"version": 1,
 "devices":  [{"id": "<8 hex>", "name": "<--name>", "hash": "sha256:<hex>",
               "paired": "<iso>"}],
 "pairings": [{"hash": "sha256:<hex>", "name": "<--name>", "expires": 1792640300.0}],
 "failed": 0}
```

| Property | Why |
|---|---|
| **Hashes only** | A device's token (256 random bits) and a pairing code are stored as their SHA-256, so the file is not a credential. The token is shown to nobody; the code only to the person who ran `pair`. |
| **Single-use, short-lived codes** | A code is removed by the redeem that spends it, and expired ones are dropped on every write. `failed` counts wrong codes in a row; at ten every pending code is cancelled, and a good redeem resets it. |
| **Owner-only from creation** | `web/` is created `0700` (and narrowed first if an older build made it wider), and each write's temporary file is created `0600`, exclusively, before it is renamed over the file. Nothing is created with the process default and narrowed afterwards, so the file is never readable by anyone else, not even briefly (ADR-008 D14). |
| **Locked writes, refused shapes** | `issue`, `redeem` and `revoke` take `locks/devices.lock` through `HomeStore`. Like the index, a newer `version`, a non-object or a malformed list is refused rather than rewritten, and unknown keys survive. |
| **Fails closed** | `verify`, the one read on every request, answers "nobody" when the file cannot be read, so a broken store locks devices out rather than letting one in. |
| **Clock times, machine-local** | `paired` (UTC ISO, to the second) and `expires` (epoch seconds) are the D14 exception again: under the home, never committed or rendered. |

### The web actions' audit log

Each action confirmed on the web page (IDEA_023 increment 2, ADR-008 D13)
appends two lines to `<oracle home>/web/actions.log`, one when it starts and
one when it ends:

```json
{"at": "<iso>", "event": "start", "job": "<id>", "device": "<8 hex>",
 "device_name": "<--name>", "row": "<project key>|<subject>", "kind": "run",
 "argv": ["invariant", "session", "merge", "--id", "<session>"], "cwd": "<dir>"}
{"at": "<iso>", "event": "done", "job": "<id>", "device": "<8 hex>", "...": "as above",
 "exit": 0, "ok": true, "elapsed": 1.234}
```

| Property | Why |
|---|---|
| **A record, not derived** | Nothing else can say afterwards what was run from the phone, so the log sits in `web/` beside the paired devices, not in the watcher's `oracle/`. Only `oracle status` and `doctor` read it back, and only its last 8 KiB, for the last action (`AuditLog.last`, ADR-008 D17). It is appended to and never rewritten, rotated or locked: one process writes it, and each line is a single `O_APPEND` write. |
| **No record, no action** | The start line is written before the action runs. If it cannot be, the action is refused and nothing runs. A server killed mid-run therefore still leaves the start line. An end line that cannot be written is reported on the page's job. |
| **No secrets** | No token, nonce, environment or output: the device by id and name, the row by its key, and the command by its `argv` and `cwd`. `exit` is `null` for a terminal step, which has no exit status. |
| **A start is an action too** | A session started from the page (IDEA_030, ADR-009 D16) writes the same two lines with `kind` `start`, `row` `<project key>\|start`, and the `argv` and `cwd` of the `claude` line it ran in the main checkout, the first message included: it is part of what ran. `exit` is `null`. |
| **Owner-only from creation** | Opened `O_APPEND \| O_CREAT \| O_NOFOLLOW` with mode `0600` in a `0700` `web/`, so a symbolic link planted in its place is refused rather than followed. |
| **Clock times, machine-local** | `at` is UTC to the second: ADR-007's D14 exception again, under the home and never committed. |
