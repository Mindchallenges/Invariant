# CLI surface

The CLI is the whole API. Hooks are shims that call it, agents are instructed
to call it, and a human debugging a wedged state calls the same commands.
Nothing reaches the engine another way.

## Conventions

- Every command accepts `--json` and prints a machine-readable result.
- Exit codes: `0` clean, `1` a gate failed or a transition is illegal, `2`
  usage error, `3` state store problem. A gate failure and a crash are never
  the same code, because a shim must be able to tell them apart.
- Every command that mutates state is idempotent where that is meaningful, and
  says so in `--help` where it is not.
- No command ever writes to the repository outside the state root and **the
  stores the manifest declares** (`items.path`, `paths.memory`,
  `paths.reports`).

  This was once a flat list of three exceptions. ADR-002 D4 generalised it
  rather than adding a fourth, and the reason is worth stating: an enumeration
  grows on every feature, and the second exception is always easier to justify
  than the first. Expressed this way, a feature that needs a writable location
  adds a manifest key — which is visible in `config show`, reviewable in the
  manifest diff, and exempt from session isolation by the same mechanism that
  already exempts the backlog — instead of quietly widening a guarantee.

## Setup

```
invariant init [--ecosystem <id>] [--host claude_code] [--profile standard]
    Write a manifest, install shims, wire host settings, create the state root,
    and add the state root to .gitignore. Refuses to clobber an existing
    manifest without --force; prints a diff of what it would change with
    --dry-run.

invariant doctor
    Check the installation: manifest valid, shims present and executable, host
    settings wired, state root writable, adapter commands resolvable, memory
    index current. One line per check, exit 1 if any fails.

invariant assets list [--kind agent|command|skill]
    Every host asset: kind, whether the host can load it, and whether this
    library or the project owns it.

invariant assets install [--kind ...] [--dry-run]
    Reinstall library-shipped assets and nothing else. Not a flag on `init`:
    `init --force` rewrites the manifest, so routing a repair through it would
    trade a broken asset for a discarded enforcement configuration.

invariant agents list
    Every library agent: installed or not, whether the host can load it, and
    which pipeline role it answers for.

invariant agents install [--dry-run]
    Reinstall the library agents and nothing else. Deliberately not a flag on
    `init`: `init --force` rewrites the manifest, so routing an agent repair
    through it would trade a broken agent file for a discarded enforcement
    configuration.

invariant config show [--provenance]
    Print resolved configuration and where each value came from.
```

## Session

```
invariant session start <name> [--task <slug>]
invariant session isolate            Print the worktree and branch to create,
                                     or create them with --execute.
invariant session status [--json]
invariant session transition <state> [--reason <text>]
invariant session files add <path>...    Declare this session's file set.
invariant session review              Run the review gates.
invariant session merge               Validate the merge is being run from the
                                      right checkout, then perform it.
invariant session push                Publish the base branch. Refused unless
                                      session.push is enabled (it is off by
                                      default); the barrier then allows one.
invariant pipeline coverage
    Run commands.coverage, parse it with the adapter, and record per-file
    numbers as pipeline evidence. This is what coverage.meets_floor reads.

    Where an ecosystem measures and reports with separate programs, set
    commands.coverage_report too: the suite's exit status is then read from
    the first command and the numbers from the second, and neither can be
    mistaken for the other. Joining them into one command is REFUSED — a
    shell reports only the LAST command's status, so a red suite followed by
    a clean read exits 0 and a green suite followed by a failed read exits
    non-zero. Whatever the first leg needs in its environment the second
    needs too: a `VAR=value cmd` prefix applies to that one command alone.

    A changed file with no row in that evidence normally BLOCKS: an absent row
    is what "never measured" looks like. Two absences are set aside instead,
    each on positive evidence — a file git reports the change deleted, and a
    file the ecosystem's adapter reports carries no executable lines. Neither
    could ever earn a row, so demanding one is a gate no test can clear, and a
    gate no test can clear is one people route around. Anything else,
    including every "cannot tell", keeps blocking, and every file set aside is
    NAMED in the verdict — a pass that quietly narrowed its own scope is the
    vacuous green the gate exists to prevent.

invariant pipeline ack <rule> --reason "<why>"
    Record a deliberate decision to proceed against an ADVISORY gate. It then
    reports as skipped, carrying that reason, for the rest of the pipeline.
    Refused for blocking gates: satisfy those, or record an override on the
    transition with `advance --force --reason`.

invariant session adopt <host-id> [--id <id>] [--force]
    Bind a host session to an invariant session. Normally automatic; needed by
    hand only to claim a session registered before the binding existed.

invariant session reap [--dry-run]
    Abandon sessions whose worktree AND branch are both gone. A missing
    worktree alone is not enough: its branch may still hold unmerged commits.

invariant session teardown [--keep-worktree] [--keep-branch] [--force]
    Remove the worktree, delete the branch, deregister.

    REFUSED when the session's host binding is still live AND this command is
    running inside the worktree it would remove. Removing an occupied worktree
    wedges its host: the host's own isolation then refuses every shell call and
    orders the caller back into the deleted path, and the repair commands sit
    behind that same refusal, so there is no in-band recovery. The refusal
    names the way out — step to the main checkout and run it from there. A
    binding spent by SessionEnd, or none at all, tears down from anywhere.
    --force is for the case where the operator knows the host has moved.

    Run it from the MAIN CHECKOUT. That is the documented route, it is what
    the refusal asks for, and git's own calls run from there too — removing
    the worktree a later git call is standing in fails after the removal has
    already happened.
invariant session list                All registered sessions on this machine.
```

## Pipeline

```
invariant pipeline start [--methodology <id>] [--task <text>]
invariant pipeline status [--json]
invariant pipeline advance <state> [--force]
    Validates the transition against the FSM and runs the gates. --force
    records an override with a required --reason rather than bypassing
    silently; the override is visible in status and in the session record.
invariant pipeline gates [--state <id>]
    List the gates for a state and their current verdicts, without advancing.
```

`pipeline gates` is deliberately separate from `advance`. Asking "what is owed
here" must not have the side effect of moving.

## Work items

```
invariant item new <type> [--title <t>] [--field k=v]...
invariant item list [--type <t>] [--status <s>] [--open]
invariant item show <id>
invariant item transition <id> <status> [--residue <text>]
invariant item residue <id> [--add <text> | --clear <text>]
```

## Memory

```
invariant memory check           Validate schema, links, index currency, dupes,
                                 privacy. Exit 1 on any failure.
invariant memory write <name> --type <t> --description <d> [--body-file <f>]
invariant memory index           Regenerate the index from the directory.
invariant memory dedup           Report candidate duplicate pairs.
invariant memory link-check      Report dead [[links]].
```

## Stats

What a session took and what it cost. Measurement is **scripted** — parsing
and arithmetic, no model in the loop — because a measurement that varies
between runs is not a measurement. See ADR-002.

```
invariant stats refresh [--force]
    Rebuild the raw store from the host's own transcripts. A record whose
    source watermark has not moved is left alone; --force re-parses anyway.
    The JOIN to an invariant session is re-run either way, because a session
    can be named, bound or advanced without its transcript changing.

invariant stats report [--write]
    Render the report. Without --write it goes to stdout and nothing is
    written. With it, the artifact lands in paths.reports, carrying a
    fingerprint of the store it was rendered from — which is what `stats
    check` compares against, and why stdout-only was rejected: the staleness
    contract needs an anchor.

invariant stats show <session>
    One session in full, including its per-state breakdown. A session with no
    breakdown says so explicitly and says WHY: recorded before per-state
    attribution existed, or no pipeline state left yet, or no pipeline at all.
    Never a zero and never a blank: a blank invites the reader to conclude the
    work was free, and a cause that is not the real one invites them to stop
    asking. A state whose two watermarks cannot be subtracted — the counter
    changed under a rebind, the reading went backwards, or it did not move —
    renders as unattributable, with the reason, and never as a figure.

invariant stats archive <session>
    Promote a record to permanent. The raw store is a cache only while the
    host's transcripts survive; once they are pruned it is the only copy, and
    this is where the user decides what outlives the host's retention.

invariant stats check
    Staleness against the source, host sessions not yet ingested, records that
    are now the only copy and are not archived, models missing from the price
    table, and whether the rendered report matches the store. Exit 1 on any.
    Problems are named by the invariant session's name or counted, never by a
    host session identifier: this output gets pasted into commits, and the
    repair is the same single command whichever record is affected.
```

Two properties of the output are not stylistic:

- **Every currency figure is a floor**, labelled "excludes subagent spend". A
  subagent's own loop is billed and never written down, so the honest answer
  is "at least this much". The agents view reports launches, declared model
  and frequency, and no cost at all — estimating the missing share would be
  modelling stacked on modelling, producing an invented number in the same
  units as a measured one.
- **Tokens are measured, cost is modelled.** A model missing from the price
  table prices `None`, never zero, and a session containing one reports no
  total rather than a partial one. A total that silently omits a model's spend
  is worse than no total, because it looks like a total.

Deliberately absent: any gate that regenerates the report at teardown.
Teardown is the step that must never acquire new ways to refuse.

## Rules and gates

```
invariant check [<rule-id>...]   Evaluate rules; default is every rule bound
                                 to no particular event.
invariant rules list [--level]   Every rule, its bound event, resolved level.
invariant privacy scan <path>... Report personal-data matches with offsets.
```

## State maintenance

```
invariant state show [--session <sid>]
invariant state repair [--dry-run]
    Reconcile the registry against session files and the worktrees actually on
    disk. Marks entries with no live worktree as abandoned rather than deleting
    them.
invariant state gc [--older-than <days>]
```

## Host bridge

```
invariant hook <event>
    Read the host's payload on stdin, evaluate the rules bound to <event>, and
    write the host's expected response on stdout. Events for Claude Code:
    session-start, user-prompt-submit, pre-tool-use, post-tool-use, session-end.

invariant context
    Print the state projection a host would inject: current state, legal
    transitions, pending gates, outstanding residue. Useful standalone for
    debugging what an agent is actually being told.
```

`invariant context` existing as a plain command is the point of the design.
What the model gets injected is not a static rulebook someone hopes is being
followed — it is this command's output, reproducible on demand, derived from
the recorded state.
