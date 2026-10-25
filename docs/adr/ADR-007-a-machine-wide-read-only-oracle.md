# ADR-007 — A machine-wide, read-only oracle

- **Status:** Accepted. The oracle shipped with increment 7 of IDEA_012, the
  last of its v1. D1–D4 are implemented by increment 1, D5–D10 by increment
  2, D11–D12 by increment 3, D13–D15 by increment 4, D16 by increment 5 (its
  read-only git amended by increment 6), D17 by increment 6, and D18–D19 by
  increment 7. Nothing is left to decide for v1; the follow-ons are their own
  ideas (IDEA_013 to IDEA_018, and IDEA_023, the web dashboard over
  Tailscale, whose listener and pairing ADR-008 records).
- **Amended by:** ADR-008 (a network listener, and a second confirm surface)
  and ADR-009 (2026-10-23), which ends the oracle's read-only premise: the
  scan, watcher and gate cache stay read-only, and a confirmed action may now
  change or delete things, such as closing a host or removing a worktree.
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
ruling, D14.

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

IDEA_024 (2026-10-25) extends the probe, not the rule: `host_processes()`
reads Claude Code's per-process records and `process_facts(pid)` asks `ps`
for a pid's name, start and terminal, both implemented in
`hosts/claude_code_launch`. The scan only joins them, and reads a probe
without them, or one whose read raises, as a source not used.

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

### D13 — The notifier is a seam, and its quoting is injected from the host

The watcher hands a `Notification` to a `Notifier` (`oracle/notify`) and knows
nothing about delivery. A phone push (IDEA_013) would be a second
implementation, not a change to the watcher. The CLI chooses the notifier
through one seam, `_oracle_notifier`. The suite replaces that seam with a
recorder for every test, and a guard in `conftest.py` refuses `osascript` to
any test not marked as evaluating a script that does nothing.

v1's channel is `osascript -e 'display notification …'`, and that needs text
turned into AppleScript string literals. That rule already has an owner,
`hosts/claude_code_launch.applescript_string`, which `session handoff` relies
on. There were three ways to reach it:

- **Import it from `oracle/notify`.** Rejected. This is the `oracle → hosts`
  arrow D11 rules out, and `tests/test_oracle_scan.py` scans for it.
- **Copy the escaping into the oracle.** Rejected. It would be two
  definitions of one rule, and they would drift.
- **Move it to a neutral module below both.** This was the honest long-term
  home, because AppleScript quoting is macOS knowledge and not Claude Code
  knowledge. It was not taken here, because it moves a function the handoff
  path and its tests own, for a seam this one injection already serves.

So `OsascriptNotifier(quote=…)` takes the quoting function as an argument,
and the CLI passes `applescript_string`. This is D11's shape again: the oracle
names what it needs, a host module supplies it, and the CLI joins the two. If
a second caller outside `hosts/` ever needs the quoting, the move becomes the
right answer.

### D14 — Clock times in the watcher's files: user-level, derived, uncommitted

The snapshot carries `generated`, and the assessed `now` that `oracle list
--json` already prints. The ledger carries a `since` per alert. These are the
second exception to "Storing what was measured, not when", and they rest on
D9's argument with one more reason. They live under the oracle home, which is
outside every repository, so they are never committed, rendered or screened.
The files are derived on every tick, so a time in them records only when this
machine last looked. `generated` and `since` use the sidecar's spelling: UTC,
to the second, ISO 8601.

### D15 — One notification per rising edge, through a ledger that forgets

"The same rising edge never alerts twice" is kept by a ledger of signal keys,
not by remembering notifications. A key the watcher has seen does not notify
again. A key it no longer sees is dropped, so a condition that clears and
returns is a new edge. A new occurrence of the same kind of condition already
has a new discriminator (increment 3), so it is a new key. There is no repeat
timer in v1. A reminder for an alert left unanswered would be a separate
decision.

Three refinements keep the rule honest:

- **`notified: false` is owed, not spent.** An edge seen by `--no-notify`, or
  whose delivery failed, is delivered by the next tick that can deliver it.
  A dry look therefore never swallows an alert the daemon owes.
- **A project that could not be read keeps its keys.** "Cannot read" is not
  "cleared". Otherwise one broken manifest would re-alert every live
  condition in its project once it was fixed.
- **The read-modify-write is locked**, so the daemon and a `watch --once`
  cannot both announce one edge.

The ledger is derived. One that cannot be read, or has another version or
shape, is replaced, not refused, which is the opposite of the project
index's rule (D3). The index is a source of truth, and the ledger costs at most
one repeat of each live alert to rebuild.

### D16 — Gates are evaluated off the tick, for an idle session, and only ever as detail

D8 settled that an alert is a refusal somebody met. Increment 5 adds the
other half: an evaluation of what is owed, for the recommendation. Four
choices shape it.

- **The domain's own evaluation, over an inert store.** The cache calls
  `PipelineDomain.evaluate_gates_by_destination`, the call `pipeline gates`
  makes, with the session record and the worktree as `cwd`, so the oracle
  cannot tell a person something the command it recommends would contradict.
  Both domains are built on an `InertStore`. The rules are written to read,
  and the inert store makes that a guarantee rather than a convention, which
  is the BUG_059 argument.
- **Never while a turn runs.** The rules ask git questions in the session's
  worktree. A host mid-turn is using it, and a watcher's `git diff` must not
  meet the host's own git commands there. So a session is evaluated when its
  turn has ended, or, with no turn known or its host gone, after two quiet
  minutes. The alternative, evaluating every tick, was rejected for the same
  reason and for cost.
- **Its git takes no optional lock** (added by increment 6). An idle turn is
  not a promise: the person can type the next prompt while an evaluation
  runs. A plain `git status` or `git diff` may refresh the index stat cache
  as a side effect, and that takes `index.lock`, so the host's next `git add`
  could fail on the oracle's lock. Every git call the evaluation makes
  therefore runs with `GIT_OPTIONAL_LOCKS=0`, which git defines for exactly
  this, a background reader. The scope is
  `core/git.without_optional_locks()`, a context variable `_run` and
  `_run_bytes` read, entered by `evaluate_row` in the worker thread. Two
  alternatives were rejected. Setting it in `os.environ` would be
  process-wide and racy across the worker threads, and would reach the
  scan's and every other git in the process. Setting it in `core/git._run`
  for every call would change the engine's own commands, which run in their
  own turn in their own worktree, where refreshing the stat cache is the
  ordinary, wanted behaviour and speeds the next `git status`; nothing there
  was wrong. The scan needs no scope: its only git call is `worktree list`,
  which takes no optional lock.
- **Once per fingerprint, off the tick.** The fingerprint is the session's
  state and heartbeat, the pipeline's state and transition count, and the
  pipeline record's write time. Evaluation runs in daemon threads, at most two
  at once, with a ten-second timeout. The watcher's tick never waits for them,
  so a slow rule cannot stretch the five-second budget the latency acceptance
  rests on; `oracle list` and `watch --once`, which are one-shots, wait up to
  the timeout. A timeout or an error keeps the last answer, flagged, and the
  same fingerprint is not tried again. Daemon threads, not a pool, because an
  interpreter exit must not wait on a hung `git`.
- **Detail, never an alert, and never stored.** `recommend` receives the
  report as data and `classify` never sees it, so the ledger and the
  notifications are identical with or without it. The cache is memory only;
  the answers appear in the snapshot as part of the assessed document, which
  is derived and rewritten every tick (D14). Persisting them would make a
  forecast look like a record.

### D17 — The first daemon is a user LaunchAgent that runs the CLI

The watcher is useful only while it runs, so increment 6 makes it the first
long-lived process this library starts. On macOS that is a LaunchAgent in
the person's GUI domain, `~/Library/LaunchAgents/dev.invariant.oracle.plist`,
loaded with `launchctl bootstrap gui/<uid>`. A GUI-domain agent is what can
post notifications as the logged-in user; a LaunchDaemon cannot.

- **It runs the CLI, not a new entry point.** The plist's program is
  `<python> -m invariant oracle watch --interval N`, so the daemon is exactly
  the command a person can run by hand, with the same seams and the same
  failure handling. `oracle/launchd` imports nothing else from the oracle.
- **launchd's environment is minimal, so the plist is explicit.** launchd
  starts an agent with a bare `PATH` and no shell profile. The interpreter is
  absolute: `sys.executable` as it is, not resolved, because a virtual
  environment's `python` is a symlink that finds its environment from its own
  path. `-m invariant` needs no `PATH` at all. `PATH` is set to the
  directories `git` and `osascript` are found in at install time, then
  `/usr/bin:/bin:/usr/sbin:/sbin`, because the scan and the gate cache run
  git and the notifier runs `osascript`. `INVARIANT_HOME` is pinned only when
  it was set, so an unset one keeps meaning `~/.invariant` rather than
  freezing a path. Both output streams go to `<oracle home>/oracle/watcher.log`,
  whose directory `install` creates because launchd does not.
- **An install can move.** An editable install, or a reinstalled tool, leaves
  the plist naming an interpreter that is gone, and launchd then fails at every
  login without a word. `doctor` adds a row when the plist exists, and `oracle
  status` reports it; both read the plist only. The remedy is to run `install`
  again, which replaces the agent.
- **Replacing a loaded agent is bootout, wait, bootstrap.** launchd refuses
  to bootstrap a label that is still loaded, and `bootout` returns before the
  old process has gone, so the installer polls `launchctl print` for up to
  five seconds before it bootstraps. `--dry-run` runs only that `print`, so
  the calls it shows are exact, and changes nothing.
- **launchctl is a seam, and the suite refuses the real one.** The installer
  takes the program as an injected runner; the CLI reaches it through
  `_oracle_installer`, which the suite replaces with a recording installer for
  every test. The `conftest.py` guard that refused `osascript` (D13) now
  refuses `launchctl` too, with no marker that admits it. It raises a
  `BaseException`, so a broad `except Exception` in the code under test — the
  watcher's notify path has one on purpose — cannot swallow the refusal; and
  it reads the program from a bare string or path, an `executable=` override
  and, best effort, a shell command string, not only an argument list.

The alternatives were a cron entry or a login item. Cron restarts nothing that
exits and runs without the GUI session; a login item is a GUI application
concern. launchd's `KeepAlive` is the restart policy the watcher wants.

### D18 — Every action is confirmed first, and runs as a subprocess or a new host

The dashboard can act on a row, and the person's hard rule is that nothing it
does is a surprise. So an action is never one key. The key opens a modal that
shows the exact `argv`, the `cwd`, and each variable the step adds to the
environment, with Cancel focused; only `y` or the Run button runs it. A step
of kind `info` opens the same kind of modal with its text and no action.

- **A `run` step is a subprocess of the dashboard, with no shell and no
  stdin.** Its environment is the dashboard's own, minus Claude Code's host
  markers and minus `INVARIANT_SESSION_ID`, plus the step's own `env`. The
  markers go for the reason they go from a handoff's successor (BUG_068,
  BUG_069): a dashboard started from a host's tool shell must not make a
  command look like that host's child. The ambient session id goes for the
  reason `domains/pipeline` strips it from the suite it spawns (BUG_035): a
  step that needs a session names it in `env`, and an inherited id would
  point the command at whichever session ran the dashboard. The output is
  shown when the command ends, and the table refreshes.
- **A `terminal` step opens a new host session through the handoff's
  launcher.** Resuming belongs in a host, not in a subprocess, so the step
  becomes a `session handoff` launch plan: `claude --name <session>
  --remote-control <session>`, in the step's `cwd`, with a first prompt that
  asks the new session to run the step's command. It goes through
  `_handoff_launcher` and counts only once the successor is seen alive, as a
  handoff does (BUG_075). Unlike a handoff, it records no note and ends no
  host. The modal shows the exact line the window will run.
- **The host facts are injected.** The marker list
  (`without_host_markers`) and the launcher are `hosts/claude_code_launch`'s.
  The oracle may not import `hosts/`, so `oracle/actions` takes both as
  arguments and the CLI joins them, D11's shape again.
- **The oracle never answers a prompt.** A row waiting on a permission prompt
  or a question recommends nothing to run (increment 3), and `oracle/actions`
  refuses a runnable step on such a row as a second line of defence. It also
  refuses an `argv` that is not an `invariant` command and a `cwd` that does
  not exist. The snapshot is a file anybody with the user's rights can write,
  and a file is not a reason to run a program.
- **The suite never runs one.** The runner is reached through
  `_oracle_runner` and the launcher through `_handoff_launcher`. Both are
  replaced for every test by recorders, and the `osascript` guard stays the
  backstop.

The alternatives were to run in a shell, which would turn a recommendation's
quoting into a second language, and to type a resume into the person's
current terminal, which the dashboard does not own.

### D19 — The first optional dependency: Textual, as `invariant[oracle]`

The dashboard is a terminal UI, and the standard library has none worth
building on (`curses` has no widgets, no modal and no test harness). Textual
does, and its `App.run_test()` drives an app headless with a pilot, which is
what lets the suite test that cancel runs nothing.

- **An extra, not a dependency.** `[project.optional-dependencies] oracle =
  ["textual>=8.0"]`. The core install stays PyYAML-only. Every other part of
  the oracle — the index, the scan, the watcher, the notifier, the
  LaunchAgent — uses only the standard library, so only the dashboard needs
  the extra.
- **Imported only by the command.** `oracle/tui` is imported inside
  `cmd_oracle_tui` and nowhere else. A missing Textual is a usage error that
  names the extra, never a traceback. Any other import failure is not
  disguised as one. What the dashboard decides lives in modules that do not
  import Textual (`oracle/dashboard`, `oracle/actions`), so the logic is
  tested without a terminal and `tui.py` is only drawing.
- **Pinned for the suite.** Textual is also in the `dev` dependency group,
  so the suite always runs the dashboard's tests instead of skipping them,
  and `uv.lock` pins the version, for the reason ruff and Sphinx are pinned
  there: a floating toolkit would move what the tests measure.

The alternative was `rich` alone, with a redraw loop. It has no input
handling or modal, so the confirm-first rule would have been hand-built on
raw key reads, and that is the part that must not be wrong.
