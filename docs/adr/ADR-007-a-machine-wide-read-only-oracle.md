# ADR-007 — A machine-wide, read-only oracle

- **Status:** Proposed. D1–D4 are implemented by increment 1 of IDEA_012. The
  later decisions are listed under "Still to decide", and each is recorded
  here when its increment lands. The status becomes Accepted when the oracle
  ships.
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

## Still to decide (later increments of IDEA_012)

These are recorded so the record is complete. They are not decided yet.

- The per-project **attention sidecar** under `<state_root>/attention/`. It
  would be the first per-host file in a state root written by more than one
  hook event, and it would store the first **pid**.
- **"Gate blocked" means a recorded refusal**, never a fresh evaluation by the
  watcher.
- **Actions are confirmed first**, run as subprocesses with the host markers
  stripped, and never answer a permission prompt.
- **The first daemon** (launchd) and **the first optional dependency**
  (Textual, as `invariant[oracle]`).
- **How clock times are scoped** in the watcher's snapshot and de-dup ledger.
  Both are user-level, not committed, and outside the privacy scope.
- Whether the hook's attention writes need a `hosts → oracle` arrow, or go
  through the CLI as the registration in D1 does.
