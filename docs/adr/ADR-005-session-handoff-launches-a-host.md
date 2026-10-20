# ADR-005 — A session hands off by recording a note and launching its successor

- **Status:** Accepted
- **Date:** 2026-10-19
- **Supersedes:** nothing. Builds on ADR-004 (a session outlives its host) and
  ADR-003 D4 (what `hosts/` own).

## Context

ADR-004 made a session outlive the host session that started it, and named the
continuation: `session resume`. What it left to prose is the moment between two
host sessions. The ending session writes a terse keyword line — what was done,
what is next, what carries over — and a human pastes it into a new terminal,
types `claude`, and remembers to name the session. In a consuming project the
step happens after merge and teardown, so by then no live session exists to
hold the line, and the only record of what comes next is a chat message.

That is the shape this library exists to remove: a step that is remembered or
not, with nothing recording which. The maintainer settled what the command
does by questionnaire — the ending session passes the note, invariant
privacy-scans and records it, derives the new session's name from the note's
`next:` item, and opens a new macOS Terminal window in the main checkout
running `claude --name <slug> --remote-control <slug> "<note>"`. This record
holds the decisions that implementing it required and that the questionnaire
did not reach.

It is also the first command that starts a host process. Every earlier contact
with a host is a hook it calls, a file it reads, or a transcript it wrote.

## Decision

`invariant session handoff --note "<line>" [--name <slug>] [--dry-run]` vets
the note, settles the name, refuses what cannot launch, records the handoff on
the session record, and only then launches. Nothing is written on any refusal.

## Decisions recorded

### D1 — The note is vetted by the existing privacy scanner and recorded on the session record

The note is scanned with `PrivacyScanner.from_manifest`, the project's own
patterns and allow list, and a finding REFUSES with the findings redacted. The
exemption marker is refused outright: in a document it marks a place a reviewer
can see, and in a one-line note it would exempt the whole note from the only
scan it gets. A note is one line and bounded.

It is recorded as `Session.handoffs`, a list on the per-session document,
for the reasons ADR-004 D3 gives for the plan: per-session, small, read beside
the state it qualifies. No new store, and the round-trip contract (BUG_061)
carries the field across builds that predate it.

*Rejected:* a project-level handoff file. A second store for one list, outside
the session it describes, and the first thing to drift from it.

### D2 — A handoff is an event, not an edge; every state may hand off

A handoff changes nothing the session machine governs — no worktree, branch or
gate — so it is legal in every state, `torn_down` and `abandoned` included,
and the machine is unchanged. It is recorded the way `resume` is: a standstill
row in `history` carrying `handoff: <name>`. A finished session is saved and
not re-registered, because the registry indexes sessions in flight.

Which session hands off, most reliable first: an explicit `--id` or
`INVARIANT_SESSION_ID`; a session the call PROVES (the attribution routes other
than `origin`); otherwise the most recently moved session registered at or
above the working directory, in any state, preferring one bound to this host
now or earlier. The last is a guess, so the command prints which session it
recorded on.

*Rejected:* a `handed_off` state. It would need an edge out of every state,
including the terminal ones, to model something that is not a position in the
lifecycle.

### D3 — Launching lives in a third Claude Code host module, behind a seam

`hosts/claude_code_launch` builds a `LaunchPlan` — the `claude` argv, the
`cd <main checkout> && …` shell line, and the AppleScript that types it into a
new Terminal window — and `TerminalLauncher` runs `osascript` with the script
as one argument. The CLI reaches the launcher through one function,
`_handoff_launcher`, which the suite replaces for every test, so no test opens
a window.

It is a sibling of `claude_code` and `claude_code_telemetry` rather than part
of either because it is a third relationship with the same host: `claude_code`
DRIVES a running host, the telemetry module READS BACK what one wrote, and this
STARTS one. The domain stays host-agnostic: it vets, names and records, and
never learns that a terminal exists.

Quoting is two layers with one owner each: `shlex` for the shell, and
AppleScript's two string escapes for the literal. The suite proves the
composition with a real shell handing a fake `claude` the note byte for byte,
and with `osascript` reading the literal back unchanged.

`claude` is resolved from PATH and its absolute path is typed, since the new
window's login shell builds its own PATH. A non-macOS host is refused with the
command printed to run by hand; another `host.kind` is refused; `--dry-run`
prints the plan and needs neither macOS nor `claude`.

*Rejected:* passing the note through a temporary file. It leaves the note on
disk with a lifetime nobody owns, and the two quoting layers are provable
without it.

### D4 — The name is derived from the `next:` item, deterministically

The text after `next:` up to the first separator (`·`, `•`, `|`, `;`, an
arrow, or the next `label:`), with `BUG_` / `IDEA_` type prefixes dropped,
lowercased, split into alphanumeric words, kept to six words and 48
characters, and cut at a word. `--name` overrides and is only normalised. No
`next:` item and no `--name` is a refusal: a session named after nothing is one
nobody can find again.

### D5 — The latest fresh handoff is surfaced where nothing is bound

`SessionStart` and `invariant context` show the newest handoff recorded here
when no session is bound — the turn a handed-off session starts on — as the
backstop for a prompt argument that did not arrive. Fresh means younger than
`session.plan_stale_days`, derived on every read, so nothing has to run for an
old note to stop being offered. Amended by D6: a CONSUMED note is skipped, and
the newest open one answers instead.

### D6 — A launched session claims exactly the note its launcher named (amendment, IDEA_008)

*Added after acceptance, from the follow-up the Consequences below named; the
claim signal, the dismiss form and the storage were settled by the maintainer
on IDEA_008 and are recorded here rather than in a new ADR because they extend
D3 and D5 without reversing either.*

The launcher prefixes `claude` in the typed line with
`INVARIANT_HANDOFF=<session-id>:<at>`, naming the note it just recorded. A
hook subprocess inherits the environment its host was started with, so on the
PostToolUse that binds that host's first `session start` or `session resume`
the Claude Code adapter reads the token and the session domain marks that one
note consumed — on the binding path, never at `SessionStart`, which must not
write. The first claim wins; later ones with the same token do nothing.

`session handoff --dismiss` retires the note the greeting is showing, recorded
as `dismissed` rather than `claimed`. Staleness by `session.plan_stale_days`
is unchanged.

The marker is a `consumed` key on the handoffs entry — when, how, and the
consuming invariant and host session — so an entry without it reads as
unconsumed and the round-trip contract needs nothing new. It is written onto
the handing-off session's record as a standstill row, the shape D2 gave the
handoff itself: saved, not re-registered, no gate.

*Rejected:* consuming every note recorded before a start or resume ("any start
after `at`"). It silences a note on behalf of whatever unrelated session
happens to start next in the project, and it needs no evidence that the
starting session had anything to do with the note. A note launched by hand
carries no token and leaves only by dismissal or staleness.

*Rejected:* reading the new host's session name instead of a token. Hooks are
not told it, and two hosts launched from two handoffs would be told apart only
by a name somebody may have overridden.

## Consequences

- A host process is started by this library for the first time. It is macOS
  only, and says so rather than degrading.
- The handoff note reaches the new host's transcript, which this library does
  not own. That is why the scan refuses rather than warns.
- A recorded note was offered, while fresh, to every unbound greeting here —
  including one that already acted on it. That follow-up is D6: the launched
  session claims its note, and `--dismiss` retires one nobody will act on.
- The handoff launcher now writes an environment variable the Claude Code
  hook adapter reads, so two modules of one host share a constant. A note
  whose host was started some other way is never claimed automatically.
- The two override routes are unchanged: a handoff passes no gate and forces
  none.

## Notes

The documented flags are `--name/-n`, `--remote-control/--rc [name]` and a
positional initial prompt. Claude Code has no flag for the working directory
or a new window, which is the whole reason the launch is this library's.
