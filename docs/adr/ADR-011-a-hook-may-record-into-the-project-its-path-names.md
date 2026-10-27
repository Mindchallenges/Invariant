# ADR-011 — A hook may record into the project the call's path names

- **Status:** Accepted
- **Date:** 2026-10-27
- **Supersedes:** nothing. Amends `docs/architecture/state_model.md`,
  "Attribution", whose closing sentence read "Attribution is within one
  project by construction." Settles the "ADR-worthy" question in BUG_010's
  residue and the prerequisite in BUG_009's. Fixes BUG_040.

## Context

A hook's manifest and state store come from the host's project directory
(`CLAUDE_PROJECT_DIR`, AUD-04). Every attribution lookup then runs against
that project's session registry. A host rooted in project A that drives a
session in project B therefore searches A's registry for B's worktree, finds
nothing, and records nothing (BUG_040). The block-level `agents.*_ran` gates
and `docs.read_architecture` in B cannot be satisfied by any work B's track
can do. Before f1396f9 the dispatch was worse than dropped: it was recorded
into A's pipeline by A's host binding, which greens a gate in A on work done
in B (BUG_010, wide half).

The payload already identifies B. A `Read`, `Edit` or `Write` carries an
absolute `file_path`. A dispatch's brief names the worktree it delegates into
(f1396f9). Nothing used either one to choose the project before looking up the
session.

The user ruled on 2026-10-20 (recorded on BUG_040): allow cross-root
recording, constrained exactly as the entry's sketch says. Re-root only for a
path that matches a registered live worktree, and only when every match lands
on one session. Otherwise fail loudly.

This record holds the decisions the ruling did not reach.

## Decisions

### D1 — A hook may resolve a second manifest and write to a second state root

It may do so only through the route below. This answers BUG_010's residue
("whether a hook may resolve a second manifest and write to a second state
root at all") with a constrained yes. The route lives in the CLI's context
construction (`Context.for_payload`, called by `cmd_hook`), not in a host
adapter. That follows the rule BUG_010 recorded: `hosts/` reach the engine
only through the CLI.

`state_model.md`'s sentence "Attribution is within one project by
construction" is replaced. Attribution is now within the project the call's
path names. That is the host's project unless the path lies in another
project's live session worktree.

### D2 — The candidate project comes from the path, not from the oracle index

The project a path belongs to is found from the path itself:

1. Walk up to the nearest manifest (`Manifest.find_root`).
2. Load that manifest from its repository's main checkout (`git.main_checkout`).
   Loading it from the main checkout means a linked worktree's copy cannot
   move a relative `paths.state`.
3. Treat the project as **foreign** exactly when its state root differs from
   the host's.

The state root is the per-repository boundary ("Storage layout"), so the
state root is the comparison. Two manifests that share a state root (an
exported `INVARIANT_STATE_ROOT`, for example) already share one registry, and
the ordinary order already reaches it.

The owning session is looked up in that project's own registry, through
`SessionDomain` over an `InertStore`. The decision therefore writes nothing,
anywhere. A path in no invariant project is ungoverned. It is ignored, as it
was before.

The oracle's project index (`~/.invariant/projects.json`, `oracle/index.py`)
was the natural alternative, and it is rejected:

- **The index is incomplete for the very case this route exists for.** A
  project enters it at the first `SessionStart` of a host rooted there
  (`via: session-start`) or at `init`. A project whose work has only ever been
  driven from a host rooted elsewhere may never be registered at all.
- **It caches a fact the path already carries.** The nearest manifest above a
  path is the authoritative answer to "which project is this". A cache of it
  could only be stale, never more correct.
- **The dependency rule allows it, but only in one place, and that place is
  not needed.** `CLI --> OINDEX` is already drawn, because `cmd_hook`
  registers on `SessionStart`. So the read would be legal in `cli/` and
  nowhere below it. `core/`, `domains/` and `adapters/` must not import
  `oracle/`, and `tests/test_oracle_index.py` scans for that. `hosts/` must
  not import it either, and a test scans for that too. Taking the walk-up
  instead adds no arrow, and the engine keeps working the same whether or not
  the oracle is installed. That property is what ADR-007 bought.

### D3 — The constraint, as ruled

- **One path (`file_path`, `notebook_path`).** The path is re-rooted only if a
  live session in the foreign project has a worktree that contains it. Live
  means `by_worktree_containing`'s rule: not `torn_down`, not `abandoned`, and
  not stranded.
- **A brief (`tool_input.prompt`).** Every absolute path in the brief is
  checked against the host's registry and against the registry of whichever
  project that path lies in. The call is re-rooted only if every live-worktree
  match, the host project's included, lands on one session, and that session
  is foreign. A brief whose only match is a host-project session resolves
  exactly as before.
- A path inside the host project is the ordinary order's business, and that
  order is unchanged.

### D4 — Re-rooting is whole-call, and the working directory moves with it

The re-rooted context serves the whole tool call: PreToolUse enforcement and
PostToolUse recording. Re-rooting only the recording would let enforcement and
recording disagree about the owner. Making them agree by construction is the
principle BUG_010's narrow half was fixed on. B's rules therefore govern a
call that lands in B's live session worktree. Before this change, A's rules
evaluated it, and they mostly abstained because the path lay outside A.

The re-rooted context's working directory is the owner's worktree. That way
every git question a rule asks (`docs_roots`, `_is_outside_project`, branch
checks) is about the repository the call acts on, not about the host's.

The attention sidecar and the `SessionStart` registration stay with the
host's project. They describe the host, and the host lives where it is rooted.

### D5 — No host binding crosses roots

The re-rooted context carries **no host identifier**. The foreign registry
therefore resolves by path (step 3 of the order) and never learns a binding
(`_bind_by_successful_edit` needs one).

The reason is the session end. `SessionEnd` fires in the host's own project.
A binding learned in B could never be marked spent. B's session would stay
bound to a dead host, so it would be "bound elsewhere" to every later host,
and `adopt_host_session` would refuse its real owner.

The consequence is that step 2 does not operate across roots. Inside B, a
path in session S's worktree is attributed to S. B can still detect a
crossing between two of its own sessions only when the host is bound in B.

### D6 — Explicit identity disables the route

When `INVARIANT_SESSION_ID` is set (or `--id` is passed), the call stays in
the host's project. Step 1 outranks every path. The identity was supplied for
the project that reads it.

### D7 — "Fail loudly", for a hook

A hook never raises, never denies on this account, and always exits zero. A
hook that fails is a deny, and this library breaking must never be spelled
"the agent may not act". "Loudly" therefore means said in the turn that made
the call, not refused.

A call is **unplaced** when it names a path inside a foreign invariant project
and the constraint in D3 is not met. There are three ways that happens:

- No live worktree there contains the path. That covers the main checkout,
  and a torn-down or stranded session's worktree.
- The brief's matches span more than one session.
- That project's manifest cannot be loaded. In that case nobody can say
  whether the path was a match, so the call is unplaced even when another
  path the call names did land on one session.

For an unplaced call:

- **Nothing is recorded anywhere.** That includes the host's project.
  Recording it there by the host's binding is the BUG_010 failure that greens
  a gate in A on work done in B. The acting-agent row is skipped for the same
  reason.
- **The drop is surfaced at PostToolUse** as `additionalContext`. The context
  names the path, the project and the reason, and gives a remedy. It follows
  the restraint BUG_046 established for unrecorded reads. Only a call that
  would have been evidence speaks:
  - A dispatch naming an agent always speaks.
  - A `Read` speaks only if it would have been docs evidence in the foreign
    project (`is_architecture_doc` against that project's manifest).
  - An edit stays silent. An edit outside a live worktree is nobody's first
    edit, so nothing any gate waits for was lost.
- **PreToolUse is unchanged** for an unplaced call. It is evaluated in the
  host's project, as before.

### D8 — The host binding stays a scalar with no location (BUG_009's residue)

BUG_009's residue is the case of a *different* host standing in a worktree
that another host tears down. It said closing that "needs the binding to
record a location", and it named BUG_010's wide half as the prerequisite.

This ADR closes the wide half without giving the binding a location.
Attribution across roots is per call, by the path each call names (D3). The
binding gains no foreign entry either (D5). So the prerequisite BUG_009
waited on will not arrive by this route. Teardown still measures occupancy
from the caller alone. The residue is closed as an accepted limitation, with
`--force` remaining the deliberate override. Reopening it needs its own
decision to record where a host stands, which this ADR does not take.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| The oracle index as the project registry | D2: incomplete for the failing case, a cache of what the path already says, and an optional component on the hook's critical path. |
| Record into the host's pipeline, as before f1396f9 | It greens a block-level gate in A on work done in B. BUG_010 named this as the worse failure. |
| Re-root only the PostToolUse recording | Enforcement and recording would disagree on the owner (D4). |
| Re-root on any foreign path, taking the deepest or first match | Contrary to the ruling. Attributing to the wrong session is worse than not attributing. |
| Learn host bindings across roots | Nothing could ever spend them (D5). |
| A machine-wide host-session index, or a location on the binding | A new store and a new consistency obligation to buy BUG_009's cross-host case, which D8 declines. |
| The payload's `cwd` or `transcript_path` | Both name the host's project, as measured under BUG_010. They carry no signal about B. |
| Keep the workaround: drive a pipeline from a host rooted in its project | It stays good practice. But the manifest already says that if this wedges, the defect is in the recording and gets fixed there. |

## Consequences

- A host rooted in A can satisfy B's `agents.*_ran` and
  `docs.read_architecture` gates by working in B's live session worktree, or
  by naming that worktree in a brief.
- B's rules now govern edits that land in B's live session worktrees, whoever
  the host is.
- A call that reaches into another project and cannot be placed is recorded
  nowhere, and a dispatch or architecture read says so in its own turn.
- A brief that names a worktree in each of two projects is ambiguous by D3
  and records nowhere. Before this change it recorded into the host's
  project's session.
- Cost: a path outside the host project costs one walk up the directory tree,
  plus, the first time each foreign project is seen in the call, a manifest
  load and two git reads. A path inside the host project costs what it cost
  before.
