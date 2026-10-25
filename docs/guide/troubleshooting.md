# Troubleshooting

Organised by what you see. Start with `invariant doctor` — one line per
check, exit 1 if any fails — and `invariant context`, which prints exactly
what the agent is being told.

## Installation and hooks

### Nothing happens in Claude Code — no greeting, no gates

The shims are silent by design when they cannot find the CLI: a missing tool
must not break every tool call in a project. Check, in order:

1. `invariant doctor` → `hook shims actually run`. The shim looks for
   `invariant` on PATH, then `~/.local/bin/invariant`, then
   `<project>/.claude/invariant/bin/invariant`. Hooks run in a non-login shell,
   so a PATH set only in `.zshrc`'s interactive section is not seen; `uv tool`
   and `pipx` both install into `~/.local/bin`, which the shim tries directly.
2. You started a new Claude Code session after `init`. Hooks are read when a
   session starts.
3. `.claude/settings.json` still carries the six `invariant-*.sh` entries
   (`doctor` → `hooks wired in settings`). `invariant hooks install` rewrites
   the shims and the wiring without touching the manifest.

### `doctor` says `architecture docs exist: FAIL`

The docs-first gate reads `paths.docs` (default `docs/architecture`) and the
directory is missing. `invariant init` scaffolds a starter `overview.md` there
when the directory is absent; on a project that already had a `docs/` tree of
another shape, either create the directory and a first document, or point
`paths.docs` at where the architecture documents actually live. Setting
`enforcement.rules.docs.read_architecture: warn` is the third option, and it
is a policy change, not a repair.

### `invariant: no .claude/invariant.yaml found in this directory or any parent`

You are outside a project that adopted invariant. The manifest is found by
walking upward from the working directory, or from `CLAUDE_PROJECT_DIR` when a
hook supplies it.

### Hook output carries `invariantDegraded`

A hook fired where no manifest could be resolved. It exits zero and never
denies — but says enforcement is off instead of returning an empty verdict that
would read as a pass. Usually: a Claude Code session rooted outside the
project.

## Sessions

### `worktree creation failed: fatal: invalid reference: <branch>`

The session's base branch does not exist. The base is the branch checked out
when `session start` ran; pass `--base <branch>` to choose another. Older
releases hard-coded `master`, which failed on every repository whose default
branch is `main` — upgrade, or pass `--base main`.

### `[BLOCK] session.isolated_before_edit` / `session.no_edit_in_main_checkout`

An edit was attempted before `invariant session isolate --execute`, or in the
main checkout while a session owns a worktree. Edit in the worktree. Files
under `session.exempt_paths` (the manifest, hooks, the memory index) are
excepted; so is the work-item store.

### `this host session already owns session <id>`

One Claude Code session, one live invariant session. Finish the first
(`invariant session teardown --id <id>` from the main checkout) or end it
deliberately (`invariant session transition abandoned --id <id> --force
--reason '<why>'`).

### `session start` refused: the task already has a session parked here

A previous Claude Code session left this task with commits on its branch and
an open plan. `invariant session resume <name>` picks it up with its pipeline,
file set and evidence intact. `--force` on `start` begins a genuinely separate
session.

### `session merge` refused: must run from the main checkout

`session.ff_merge_target`. A fast-forward acts on whatever HEAD the calling
tree has checked out; from a worktree it would merge the wrong thing. `cd` to
the main checkout and run it there. The same applies to `session teardown`,
which also refuses to remove the worktree you are standing in while Claude
Code is still bound to it.

### `session.branch_is_current` warns after every commit

The base moved under the session. `invariant session sync` rebases the session
branch onto it from inside the worktree; a conflict is reported and the rebase
aborted, never resolved for you.

### `session.work_would_be_stranded` fires at the end of a turn

There are commits on an unmerged branch and nothing says they are parked on
purpose. Either land them (review, merge) or record what is still owed with
`invariant session plan add "<deliverable>"`; an open plan is the signal.

### The registry is wrong — a session lists a worktree that is gone

```bash
invariant session reap --dry-run      # abandon sessions whose worktree AND branch are gone
invariant state repair --dry-run      # reconcile the registry with session files and disk
invariant state show
```

## Pipelines and gates

### `[BLOCK] docs.read_architecture: no architecture doc has been read in this pipeline`

Read something under `paths.docs` *in this pipeline*, with Claude Code's Read
tool — the `PostToolUse` hook is what records it, so a `cat` in a shell is not
evidence. A read that happened before `pipeline start`, or in another
session's pipeline, does not count: the pipeline is the unit the evidence
belongs to. Driving the pipeline from a bare terminal, where no hook fires,
pass the gate explicitly: `invariant pipeline advance red --force --reason
"<what you read>"`. If the read *did* happen in Claude Code and the gate still
blocks, the hook could not attribute it — usually several sessions are live
and the file was opened through the main checkout's path rather than the
worktree's; the hook says so in that case.

### `[BLOCK] docs.updated: a structural change updates the architecture docs`

The diff adds or removes a component or dependency and nothing under
`paths.docs` changed. Update the diagram. If the change is genuinely not
structural, the methodology was the wrong one: a `chore` or `docs` pipeline
does not carry this gate the same way.

### `test.new_test_fails`: no red was recorded

`green` requires a recorded failing run. `invariant pipeline test` in the `red`
state records it; a suite run through the shell directly leaves no evidence.

### `pipeline test` exits 127, or every run is red

Command not found. The adapter's default (`pytest`, `npm test`) is not on PATH
in the hook's shell. Set `commands.test` (and `test_one`, `coverage`) in the
manifest to the invocation that works — `uv run pytest`, `npx vitest` — and
check with `invariant config show --provenance`.

### `coverage.meets_floor` blocks on a file that has no coverage row

An absent row is what "never measured" looks like, so it blocks. Four cases
are set aside automatically and named in the verdict — deleted files, files
outside the directories the reporter measures, the test suite itself, files
with no executable lines. Anything else: declare it `non_source` if it is not
code, `coverage_exclude` if it is measured but should not be held, or fix the
coverage configuration so it produces a row.

### `[BLOCK] privacy.clean` — findings in a document or commit message

The scanner found a home path, an email, an IP, a token, a clock time or a
link. Findings are redacted by default; `invariant privacy scan <path>` shows
them with offsets. Then:

- a value that is legitimately public (your repository URL) → `privacy.allow`
- one place that is a false positive → the marker `invariant: allow-privacy`
  on a comment line before the block
- a pattern that is wrong for this project → drop it from `privacy.patterns`

For a commit message, write it to a file and commit with `-F <file>`; a
heredoc that fails the scan takes the commit down with it.

### I need past a gate right now

Each route records itself on the session, with your reason:

```bash
invariant pipeline advance <state> --force --reason "<why>"   # one transition
invariant override <rule> --reason "<why>" [--calls N]        # one hook-level rule, N calls
invariant pipeline ack <rule> --reason "<why>"                # one advisory gate, rest of pipeline
invariant standdown --reason "<why>" [--until session]        # everything but privacy.clean
invariant standdown --clear
```

A rule you keep overriding is a bug report against the default: change
`enforcement.rules` in the manifest instead.

## Agents

### `doctor` says an agent is *edited locally*

You changed a shipped agent. That is allowed and it is yours; the row tells you
`agents install --overwrite-local` is the way to take the shipped version back,
and plain `agents install` will skip it and say so.

### `agents.design_agent_ran` warns although the agent ran

The dispatch was recorded against a different project. A pipeline must be
driven from a Claude Code session rooted in the project that owns it — the
hooks fire against the manifest of the directory the host was started in.

### The reviewer stopped at `INCREMENT COMMITTED` and the branch is not merged

That is a success. The session's plan still has open entries, so the engine
kept the session open on purpose: the commit is on the branch, every worktree
sees it, and the next increment continues there. Mark finished deliverables
(`invariant session plan done <n>`) and the next review run will land it.

## Memory

### `memory.index_current` / `memory.no_dead_links` warn at every Stop

`invariant memory check` lists the problems; `invariant memory index`
regenerates the index from the directory. A project with its own memory
convention sets `memory.managed: false`.

## The Session Oracle

### A leftover Claude window has no "Close…", or closing it left the tab open

A window is offered Close only when it is idle (not working, not waiting for
you, not used in the last hour), Claude Code's own record names its process
and nobody disputes it, it was started in this project, and no live session in
any project lists it. Anything less is left alone on purpose. If Claude ended but the tab
stayed open, macOS has not yet allowed the oracle to control Terminal; allow
it under System Settings > Privacy & Security > Automation. The full table of
messages is in [The oracle on your phone, Troubleshooting](oracle-web.md#troubleshooting).

### The phone page will not load, or says "not paired"

Pairing, Tailscale, certificates and the Local Network prompt each have a row
in the same [Troubleshooting](oracle-web.md#troubleshooting) table, and
`invariant oracle status` says whether the agents are loaded and the snapshot
is fresh.
