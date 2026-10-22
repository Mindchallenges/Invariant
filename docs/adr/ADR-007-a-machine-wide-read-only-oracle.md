# ADR-007 — A machine-wide, read-only oracle

- **Status:** Proposed. D1–D4 are implemented by increment 1 of IDEA_012,
  D5–D10 by increment 2, and D11–D12 by increment 3. The later decisions are listed under "Still to
  decide", and each is recorded here when its increment lands. The status
  becomes Accepted when the oracle ships.
- **Date:** 2026-10-22
- **Supersedes:** nothing. This record adds the first state this library keeps
  outside any project.

## Context

Several sessions run at once, across several projects, and are often driven
remotely. None of them can say that it is waiting on a permission prompt, is
blocked by a gate, is stuck, or has finished. All of that state is split across
per-project stores, and each store is keyed by its own repository's git common
directory (state_model.md, "Storage layout"). By construction, no project can
list another project's sessions. The user approved a plan on 2026-10-22 for a
machine-wide, read-only Session Oracle (IDEA_012). This record holds the
architectural decisions that plan implies.

Increment 1 already crosses a boundary this library had never crossed before:
it writes state that belongs to no project. That alone justifies opening the
record now, instead of waiting for the daemon or the optional dependency.

## Decisions recorded

### D1 — The oracle is a layer of its own, which the engine never imports

The new package is `src/invariant/oracle/`. It sits above `domains/` and below
the CLI. Nothing in `core/`, `domains/` or `adapters/` may import it, and a
test enforces this with an import scan.

The alternative was a sixth domain under `domains/`. A domain binds a state
machine or a store to one project's state root. The oracle reads many state
roots and owns none of them, so the domain shape does not fit what the oracle
is. Keeping it out of the engine's import graph also means that a project
which never runs the oracle carries no part of it.

The SessionStart registration is called from the CLI's `hook` command, not
from `hosts/claude_code`. The host adapter gains no import, and the overview's
"hosts reach the engine only through the CLI" sentence is left as it is.

### D2 — User-level state lives in one directory: `$INVARIANT_HOME`, else `~/.invariant`

The list of projects on the machine cannot live inside any one project, so it
lives in a directory per user. An environment variable overrides it, for two
reasons: an operator can relocate it, and the test suite can point every test
at a temporary directory. That fixture is an autouse fixture and is mandatory,
because otherwise every test that runs `init` would register a temporary
project in the developer's real index.

The cli.md guarantee is that no command writes to a repository outside the
state root and the stores the manifest declares. That guarantee still holds:
this directory is outside every repository. The guarantee now names the
directory explicitly, so the exception is visible and not merely technically
true.

### D3 — The project index is keyed by the state root and stores no clock time

`projects.json` holds `{key, root, name, via}`, and `key` is
`Manifest.state_root`. The project root would be the wrong key: a repository's
worktrees, and several manifests in one repository, would each list the same
sessions again. `root` records the main checkout's spelling, so that tearing
down a worktree does not make the project look gone.

The index stores no timestamp, which follows the rule the stats records
already obey. Unknown keys are kept, because entries are mutated in place (the
BUG_061 lesson). A newer `version` is refused rather than rewritten.

### D4 — A project registers itself, and registering can never break the command that does it

There are three routes into the index: `init`, the first SessionStart in a
project, and `oracle projects add`. The two automatic routes run only after
their own command has done its real work, and they differ in how loudly they
fail:

- **`init`** reports a failed registration and still exits 0. By that point
  the project is initialised, and a failure exit would say it was not.
- **The SessionStart hook** swallows a failure completely. Its response is
  computed first and cannot change. The hook reads the index first and takes
  the lock only for a project the index has never seen, so a known project
  costs one read and writes nothing. That keeps the hook within state_model.md's
  rule that SessionStart must not write per start. The hook skips registration
  entirely for `doctor`'s probe, because the probe writes nothing anywhere
  (BUG_059).

Removal is always manual (`remove`, `prune`), and it touches only the index.
Deregistering automatically on uninstall or teardown was rejected: those
commands' "never deletes the operator's data" and "teardown must not acquire
new ways to refuse" postures would each have to absorb a new failure mode for
bookkeeping that `prune` already handles.

### D5 — Each host session keeps an attention sidecar in its project's state root

`<state_root>/attention/<host-id>.json` records what one host session is
waiting on: `waiting` (a permission prompt, a question, or idle), `blocked` (the
rule ids of a refusal and where it happened), `turn` and `turn_since`, `ended`,
`pid` and `degraded`. It lives in the project's state root and not in the
oracle home, because it is a fact about one project's host and the hook that
writes it already holds that project's store. It does not live on the session
record either. A host can exist with no invariant session at all, and the
session record is written under the registry's discipline by the domain that
owns it. A file written on every turn has no business there.

It is the first per-host file in a state root that more than one hook event
writes. It still has a single writer: its own host, whose hooks run one at a
time. So it is replaced atomically and takes no lock, which is the argument
state_model.md already makes for `sessions/<sid>.json`. The one other writer
is a CLI gate refusal, and that writes the record of the host bound to the
refused session, which is the process tree that ran the command. Unknown keys
are kept (BUG_061). A record that cannot be read is replaced by the next write
instead of wedging the host, because it is derived from events and the next
event derives it again.

### D6 — The host translates; the CLI writes. No `hosts → oracle` arrow

`hosts/claude_code` decides what an event means for the person's attention.
That is protocol knowledge: which `notification_type` is a permission prompt,
which tool asks a question, which closeout rules refused the turn. The host
leaves the answer on `ClaudeCodeHost.attention` as a host-agnostic
`AttentionSignal`, and `invariant hook` in the CLI applies it to
`oracle/attention`. This is the D1 shape again, and the overview's
`NORMALISED_PAYLOAD_KEYS` shape: the host hands a normalised fact upward, and
the layer above decides what to do with it. A second host adapter would emit
the same signals and inherit the sidecar unchanged.

The alternative was for the host adapter to import the oracle and write the
sidecar itself. It was rejected because it puts the oracle's record format into
the one module a second host would have to copy, and because it would add an
arrow the overview says hosts do not draw. A test scans `hosts/` for oracle
imports.

### D7 — The first stored pid: `CLAUDE_PID`, else a walk up the parent chain

The sidecar stores the host's pid so that the oracle can tell a waiting host
from a crashed one. It is the first process id this library persists. Before
choosing, we checked a real hook environment on 2026-10-22 by capturing the
environment of `invariant hook` processes while Claude Code 2.1.291 ran them.
The hooks of a terminal-launched host carried `CLAUDE_PID`, and their parent
was the `claude` process itself. The tool shells of another host on the same
machine, an IDE-attached one, did not export it. So the variable is used when
present, and the fallback is not hypothetical.

`CLAUDE_PID` wins whenever it is present, because it costs nothing, and it is
right even when a resumed host kept its session id under a new process.
Without it, a pid already recorded stands. Only a host with no recorded pid
walks the parent chain with `ps`, from the hook's parent up to the first
process whose name is `claude` (`is_claude_process_name`). The walk is bounded
to eight hops and runs once per host. `SessionEnd` clears the pid. A pid is a
claim that can go stale, so the reader must check it, and increment 3 does:
the pid must be alive and still named `claude`. The CLI refusal path records
no pid, because the command may have been run by somebody other than the
bound host.

### D8 — "Gate blocked" is a recorded refusal, from one of two surfaces

`blocked` is written only where a refusal was actually met: the Stop hook's
closeout (`surface: stop`) and a gated CLI command (`pipeline advance`,
`session transition`, `review`, `merge`; `surface: cli`). The watcher will
never evaluate gates to find out. Evaluating is the gate cache's job, and it
only adds detail to a recommendation (increment 5).

The two surfaces are cleared differently, and on purpose. A refusal at the CLI
happens in the middle of a turn, and the oracle reports a block once the turn
has ended. If a clean Stop cleared every block, the Stop that ends the turn
would erase the refusal it was meant to reveal. So a clean Stop clears only a
Stop block. The next `UserPromptSubmit` clears both, because the person has
spoken. A gated CLI transition that then succeeds clears a CLI block.

### D9 — Clock times in the sidecar are machine-local and never committed

`since` and `turn_since` are UTC wall-clock times, to the second. They are the
first timestamps this library stores outside the stats records' bare dates,
which store none on principle ("Storing what was measured, not when"). They
are allowed here because of where they live and what they are for. They live
under the state root, which is never committed, rendered or published, so they
are outside what `privacy.clean` screens. And "how long has this host been
waiting" is the oracle's whole question, so a duration alone cannot answer it.
The watcher's snapshot and its de-dup ledger are user-level and get their own
ruling when increment 4 lands.

### D10 — A sidecar write can never change a verdict, and the probe writes none

Every attention write goes through one guarded helper in the CLI. It runs
after the hook's response is computed and before it is printed, it neither
reads nor returns that response, and it swallows any failure. A sidecar that
cannot be written costs the oracle one event. It never costs the host a
verdict, an exit status or a degraded marker. The same helper records
`degraded` when the hook's own handler raises.

`doctor`'s probe writes nothing. The attention store is built on the hook's
own `JsonStore`, which is the inert store for a probe, and the CLI also skips
the write explicitly, so a probe does not walk the process table either
(BUG_059).

### D11 — Host facts reach the oracle through a probe the CLI injects

The scan needs two things only the agent host knows: which transcripts belong
to a project, and whether a live pid is still the host. Both already have
homes in `hosts/` (`ClaudeCodeTranscripts`; `is_claude_process_name`). The
oracle does not import them. `oracle/scan` defines the shape it reads,
`HostProbe` — `transcripts(root)` and `is_host_process(pid)` — and
`hosts/claude_code_launch.ClaudeCodeProbe` implements it without importing the
oracle. The CLI injects it through a seam (`_oracle_cache`), which the suite
replaces.

This is the shape telemetry already has: `core/stats` defines `SampleSource`,
the host implements it, and the CLI injects it into `domains/stats`. The
alternative was for `oracle/scan` to import the host modules directly. It was
rejected for D6's reason in the other direction: the oracle would carry Claude
Code knowledge that a second host adapter would have to fork, and an arrow from
the layer below the CLI into the layer above it would appear. A test scans
`oracle/` for `hosts` imports.

The pid-existence half of liveness (`os.kill(pid, 0)`) is not host knowledge
and stays in the oracle, so a scan with no probe still tells a dead pid from a
live one.

### D12 — Where a session is in its life is derived from its machine, never named

The signals need to know that a session awaits a merge, has merged, or is
still working. A project may shadow the session machine
(`methodology.extra_machines`), so matching the shipped state names would
misread exactly the projects that customised their lifecycle. The scan derives
a stage per state from the machine's structure instead: the merge is the state
the merge gate (`session.ff_merge_target`) guards; everything reachable after
it short of the end is "landed"; a state with an edge into the merge is
"reviewed"; one with an edge into that is "working"; terminal and abandon-role
states are "ended"; the rest are "early". It is the D12 of `core/machines` —
injection by role rather than by name — applied to a reader.

A machine with no merge gate has no reviewed, working or landed stage, so its
sessions raise no "finished" or "teardown pending" signal. That is the honest
answer for a lifecycle that never merges, and it is visible: the rows still
list and still alert on everything the host reports.

## Still to decide (later increments of IDEA_012)

These are recorded so the record is complete. They are not decided yet.

- **Actions are confirmed first**, run as subprocesses with the host markers
  stripped, and never answer a permission prompt.
- **The first daemon** (launchd) and **the first optional dependency**
  (Textual, as `invariant[oracle]`).
- **How clock times are scoped** in the watcher's snapshot and de-dup ledger.
  Both are user-level, not committed, and outside the privacy scope. D9
  settles this for the attention sidecar only.
