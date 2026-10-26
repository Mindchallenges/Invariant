# ADR-009 — The oracle page becomes a mobile control panel, and the oracle gains destructive powers

- **Status:** Accepted, partly implemented. The user approved the mobile
  mockup on 2026-10-23. IDEA_027 builds it in increments (D14): D2–D6, D8,
  D11 and D12 shipped with increment 3, D10 with increment 4 (its two iOS
  checks were verified on 2026-10-25), and D9 with increment 5. Increment 6
  shipped D1's first destructive action, close host, with D7's guards and
  D15. Increment 7's clean-up actions remain; the two facts they waited for,
  BUG_078 and IDEA_024, have shipped. D16, the oracle starting a new Claude
  window (IDEA_030), was added on 2026-10-26; its CLI core and shared
  launcher shipped with IDEA_030 increment 1, and the panel's sheet and the
  TUI key follow. IDEA_029's transcript decision is D17.
  The design of record (screens, components, tokens, wording) is
  [oracle-panel.md](../architecture/oracle-panel.md).
- **Date:** 2026-10-23
- **Supersedes:** nothing. It amends ADR-007 in one place: the oracle is no
  longer read-only as a whole. Its scan, watcher and gate cache stay
  read-only (ADR-007 D11, D16), and a confirmed action may now change or
  delete things (D1). ADR-008's transport, pairing and tailnet-only rules
  are unchanged.

## Context

IDEA_023 put the oracle's dashboard on the phone (ADR-008) and IDEA_026 served
it over HTTPS. On 2026-10-23, after using it on an iPhone, the user said: "the
UI is really not mobile friendly and unclear. It has to be a nice mobile
friendly control panel." The page was the terminal table rendered as HTML.
Engine words ("isolated", "early · bugfix:fixing", "pid unknown") and full
host UUIDs were on its surface. Absolute paths and argv were its main content
and wrapped mid-word. The header's "2 need you" led nowhere, the only control
was one small "run..." per card, and a failed action dumped raw shell output.

The user settled the scope with IDEA_027 the same day: attention-first order
with a summary strip, plain words, a session detail view, copy command, a
home-screen app and plain failure reasons. IDEA_027 also folds in IDEA_017
(clean up and unstick: abandon a stranded session, kill a leaked process,
tear down an orphaned worktree, nudge a stuck agent) and IDEA_025 (sort
orphan hosts, close a leftover host). Those bodies stay the spec of record
for their actions. Their actions signal processes and delete worktrees and
branches, which ADR-007's read-only premise does not allow. This record holds
that change and the panel's design decisions.

The user then approved a static mockup of six screens: overview, session
detail, a safe confirm, a destructive confirm, a failed result, and
leftovers. The mockup is a private claude.ai artifact, linked below. It was
drawn from scratch files that do not outlive the session that made them, so
its exact values are copied into
[oracle-panel.md](../architecture/oracle-panel.md), which is the design of
record.

<!-- invariant: allow-privacy -->
Visual reference: <https://claude.ai/artifact/M8PBU7JKuUxbYmV6MLViKK>.

## Decisions recorded

### D1 — The oracle may change things, but only through a confirmed action

ADR-007 named the oracle read-only, and ADR-008 D5 kept the page read-only
until its D8 added the TUI's confirm-first steps. Those steps already ran
`invariant` commands that change state (merge, review, teardown). The panel
adds actions whose whole purpose is to destroy: closing a host (SIGTERM to a
`claude` process), killing a leaked background process, abandoning a
session, and removing a worktree and its branch.

The amendment is narrow. Everything that reads stays read-only: the scan,
the signals, the recommendations, the watcher and the gate cache write
nothing in any project (ADR-007 D11, D16). The only path that may change or
delete anything is a confirmed action, and a confirmed action is still
ADR-007 D18's: derived by the server from a fresh scan (ADR-008 D9), never
from a client's argv, confirmed with a single-use nonce (ADR-008 D10), run
as an `invariant` command with no shell, and written to the audit log
(ADR-008 D13).

A destructive action is a user-initiated hard delete: the person asked for
it, on a confirm that said what it deletes. The oracle never deletes on a
signal of its own, and no watcher tick acts.

### D2 — Attention-ordered groups, with a summary strip that jumps to them

The first screen answers "what needs me" before "what exists". Rows are
grouped, and a row joins the first group whose rule holds:

1. **Needs you**: a host waiting on a permission prompt or a question, or a
   finished session (ready for review or ready to merge).
2. **Problems**: failed tests, a gate refusal, a stuck turn, a broken hook, a
   closed host the session still needs, a session whose folder is gone, an
   unreadable project.
3. **Working**: a turn is running and nothing above holds.
4. **Idle**: every other live session (waiting for your next message,
   paused, merged and waiting to be cleaned up).

Leftovers are not a group on this screen. These are the orphan hosts and
leaked worktrees that raise no alert, the rows ADR-008 D5 folds. They get
their own screen, reached from the strip and from one summary card at the
bottom. A leftover that raises an alert stays in its group, as it stays
unfolded today.

A strip of count tiles heads the page: Need you, Problems, Working,
Leftovers. Each tile is a link. The first three scroll to their group, and
the last opens the leftovers screen. Within a group, higher severity comes
first, then the longest-waiting. The grouping, the order and the counts are
built server-side, in a new pure module `oracle/panel` that `web.view`
calls, so the script renders and never classifies.

### D3 — Plain words from one table; engine names only under "Technical details"

Every word on a card or a confirm comes from one mapping table in the view
model, in `oracle/panel`. The table is keyed by the engine's facts: the signal kind and its
cause, the waiting kind, the session stage, the pipeline's methodology and
phase, the recommendation's command, and the row kind. "waiting: permission"
becomes "Waiting for your permission", `bugfix` becomes "bug fix", and
`invariant pipeline test` becomes the button "Re-run tests…". The table is
in [oracle-panel.md](../architecture/oracle-panel.md), "Wording", and it is
the only place a label is spelled.

- **Built server-side.** The view returns the words. The page script holds
  no vocabulary, so the page cannot drift from the view by an edit to one
  side.
- **Nothing unmapped reaches the surface.** A methodology's phase is project
  data, and a project may name its own. A fact the table does not know gets
  no phrase on the card, and it is never shown raw there. The suite checks
  that no default-view string contains an engine state name, an absolute
  path, an argv, a pid or a host UUID.
- **Engine names live under "Technical details"** in the detail view: the
  session id, the state, the lane and phase, the worktree path, the host id
  and the pid.
- **A label never hides a fact a decision needs.** The detail view is one
  tap from every card and every confirm, and the confirm shows the command
  itself (D6).

### D4 — A session has a detail view

A tap on a card opens the session. The view shows its project, lane and
title, and its state in plain words. Under that come "What is going on" (a
sentence or two composed from the same table), the suggested next step with
its button and a copy button, "More actions" (resume, and from increment 7
the clean-up actions), "Recent" (the session's own dated events) and the
closed "Technical details".

The detail view is part of the same page, drawn from the state document the
page already polls, so it refreshes with everything else and needs no new
route. "Recent" lists only events with a recorded time. A fact without one
is left out, never given an invented time.

### D5 — Copy a command

The suggested step and every confirm carry a Copy button. It puts the exact
command on the clipboard as one shell line: `shlex.join` of the argv, with
any pinned variable as a leading `NAME=value`. It is for running the step in
a terminal of the person's choosing instead of the page's runner. Copying
runs nothing and spends no nonce. The clipboard API needs a secure context,
so over plain HTTP (ADR-008 D21) the button selects the text instead.

### D6 — The confirm is a bottom sheet that leads with what it does

A confirm slides up from the bottom, over the dimmed screen it came from. It
holds, in order:

- a title that is the question ("Re-run the tests?") and one plain sentence
  saying what happens and what it does not touch;
- the command, in monospace, with Copy, and the place it runs in plain words
  ("In Heap › session folder 4b7b00c1");
- the exact directory and environment behind a closed "Exact folder and
  environment" toggle, with the host-marker note in plain words ("Runs as
  you on the Mac, without the marks of any Claude window");
- full-width Run and Cancel buttons, 52 px tall. The Run button is named for
  the deed ("Run tests", "Merge"), never "Run it" or "OK";
- the nonce's expiry, counting down in minutes and seconds ("This confirm
  expires in …"). An expired confirm disables Run and offers to look again.

The server side is unchanged: ADR-008 D8–D10's derived step, nonce, origin
check and one action per row.

### D7 — A destructive action says what it deletes and checks again when you press it

A destructive confirm differs from D6's in four ways:

- Under the title it lists, on a red panel headed "This deletes", every
  thing that will be gone, counted from a fresh read: the folder, the branch
  and its commits not on main, the uncommitted files, or the host and its
  unsaved turn.
- A "Checked just now" panel says what was verified (no Claude window is
  using it) and that it will be verified again.
- Cancel comes first, and the red button is named for the deed ("Remove
  folder and branch", "Close window").
- The exact command sits behind a toggle.

The guards are IDEA_017's and IDEA_025's acceptance. They ship with the
action, never after it:

- **Re-checked at confirm time.** The precondition is evaluated when the
  sheet opens and again when the button is pressed, inside the action. If it
  no longer holds, nothing runs and the result says why. That covers a
  session that came alive, and a pid that is gone, is no longer `claude`, or
  carries another Claude session id. Close host reuses the handoff closer's
  kill-time `ps -p` check (`HostCloser` / `DetachedCloser`, ADR-005), which
  already guards against a reused pid.
- **The same CLI path.** Each action is an `invariant` command, an existing
  one or a new subcommand (for example `invariant oracle close-host`), so the
  engine's own gates and refusals apply. The oracle passes no flag that
  overrides a refusal: no `--force` on `session teardown` or `session
  transition`. A command that refuses is a failed action (D9), not a prompt
  to retry harder.
- **Never on a misattributed row.** A host attributed across repositories
  (BUG_040) is never offered a destructive action. An unmanaged host is
  "unmanaged here", and it is offered close host only when the evidence ties
  it to this project's root.
- **Needs every live pid.** Close host and the process kill target a pid, so
  they wait for IDEA_024. Without it, most leftovers read "process id not
  recorded" and offer nothing destructive. IDEA_024 has shipped: a host row
  carries its pid, the record's start token to compare again at kill time,
  its terminal (`tty`), and `pid_source`; a `conflict` row has no pid and is
  offered nothing destructive.

### D8 — A row waiting on a permission prompt has no run button

A host waiting on a permission prompt or a question shows no action button
at all. Its card says "Answer it in Claude on your Mac or phone. This panel
never answers prompts." The rule is ADR-007 D18's and is enforced where it
is today: `recommend` offers nothing to run on such a row, and `refusal`
refuses one as the second line of defence. The panel adds no route that
could reach a prompt.

### D9 — A failed action says one plain reason, and never guesses

The result sheet leads with what happened ("The tests did not start"), the
step and how long it ran. Then comes a "Why" panel with one sentence, then a
closed "Show full output" toggle holding the output's tail (the existing
64 KiB). A summariser beside the job output in `oracle/webactions` writes
the sentence:

- It names a cause only when the output carries one in a form invariant
  itself writes (its own refusal and error lines), and words it from the
  wording table.
- Otherwise it says "Failed, exit N", plus the last meaningful line of the
  output: the last line that is neither blank nor a traceback frame.
- It never infers a cause the output does not state. A reason that names
  the wrong cause is worse than none.

As shipped in increment 5, invariant's own line is the CLI's error line,
`invariant: <message>` at the start of a line; the table words the two
discovery failures and quotes any other message as "invariant said: …".
The fallback also skips progress bars and rules, and reads "Failed. <line>"
when there is no exit status (a timeout, a launch failure). The details are
in [oracle-panel.md](../architecture/oracle-panel.md), "As built in
increment 5".

### D10 — The page installs as a home-screen app, under the same CSP and token

The page gains a web app manifest (name, standalone display, the dark
background and theme colour, an icon), an icon, `viewport-fit=cover` with
`env(safe-area-inset-*)` padding, and the iOS standalone meta tags. The icon
is a PNG, which the iOS home screen requires. It is produced by the oracle
itself or shipped in the package, and never fetched.

- **Same origin, same token.** The manifest and icon are routes of the same
  server, so the CSP gains only `manifest-src 'self'` and `img-src 'self'`,
  and nothing loads from another origin. The manifest link carries
  `crossorigin="use-credentials"`, because a browser otherwise fetches a
  manifest without cookies and would get the 401.
- **Unverified: the icon fetch.** Whether iOS fetches the icon with the
  cookie must be checked on a device in increment 4. If it does not, serving
  those two data-free routes without a token would amend ADR-008 D4, and
  needs the user's agreement first. It is not decided here.
- **Unverified: the iOS cookie store.** A home-screen web app on iOS may
  keep a cookie store apart from Safari's, so an install can open as "not
  paired". This is checked in increment 4 too. The "not paired" page
  therefore links to the pairing form, where a code from `oracle web pair`
  can be typed in the app itself, and the guide says so.
- **Verified on an iPhone (2026-10-25).** The user added the panel to the home
  screen: the four-tile icon appeared, so iOS sends the pairing cookie when
  it fetches the touch icon, and the home-screen app opened on the panel
  without asking to pair again, so it shares Safari's pairing. ADR-008 D4
  needs no amendment. The paragraph below is the record as shipped, before
  the check.
- **As shipped in increment 4, both checks were still open.** The build could
  not run them on a device. The manifest and the icons are served under the
  token, as D4 of ADR-008 requires, and the "not paired" page links the
  pairing form. If iOS fetches the touch icon without the cookie, the home
  screen shows a fallback icon and the app still works; whether to serve the
  icon and manifest without the token remains the user's decision, recorded
  in [oracle-panel.md](../architecture/oracle-panel.md), "Open questions".

### D11 — Dark first, from system colours and system fonts, with no external assets

The palette is dark-first: near-black grounds, raised card surfaces, and
four tinted accents. Amber is for needs you, red for problems and
destruction, blue for working and the primary action, and grey for idle and
leftovers. Text is the system UI font (`-apple-system, system-ui`), and
monospace is `ui-monospace, Menlo`. There is no web font, image, icon font
or CDN, and the few icons are inline SVG in the page. The page states
`color-scheme: dark`. A light palette may follow from the same tokens, but
the mockup was approved dark and the build ships dark. The exact tokens are
in [oracle-panel.md](../architecture/oracle-panel.md), "Tokens".

### D12 — Touch targets of 44 px; long values wrap at separators or scroll

Every control's hit area is at least 44 by 44 CSS px. That includes the
small "Copy", "Close…" and "Remove…" pills the mockup draws at 32 and 36 px:
their visible pill may stay small, but the tappable area is padded to 44.

A long value (a path, a branch, a command) never breaks mid-word. It sits in
a single-line horizontal scroller, or wraps only at `/`, `-`, `_`, `.` and
spaces, with a `<wbr>` after each separator and `overflow-wrap: normal`. On
a 390 px wide screen the first view needs no horizontal scrolling.

### D13 — Push stays out

Alerts pushed to the phone remain IDEA_013. This record's home-screen app is
the precondition for iOS web push, one of IDEA_013's provider options, and
nothing here chooses a provider.

### D14 — The panel ships in increments, and the destructive ones wait for true facts

IDEA_027's increments 1 (the plan) and 2 (the mockup) are done. The rest:

| # | Increment | Waits for |
|---|---|---|
| 3 | Plain-language panel: groups, strip, wording table, detail view, copy, button wording, no mid-word wrap (D2–D6, D8, D11, D12) | nothing |
| 4 | Home-screen app: manifest, icon, standalone, safe areas, the two iOS checks (D10) | 3 |
| 5 | Failure reasons: the summariser and the result sheet (D9) | 3 |
| 6 | Orphan classes and close host (IDEA_025 scope v1, D7, D15), **shipped** | IDEA_024, BUG_078 (both shipped) |
| 7 | Clean-up and unstick actions (IDEA_017's list, D7) | 6 |

Increments 3 to 5 change only the view, the page and a summariser, and run
nothing new. Increments 6 and 7 add the destructive powers, and their inputs
must be true first. IDEA_024 gives every live host its pid, so close host
has a target and "process id not recorded" goes. BUG_078 makes "idle 2
days" a fact rather than a transcript's modification time. A destructive
action decided on a false idle time is the failure D7 exists to prevent.

### D15 — Close host ends Claude and then closes its Terminal tab (increment 6)

*Added 2026-10-25 with increment 6. The user approved the mockup's Leftovers
screen, with its "Close…" pills and destructive confirm, and then said: "My
aim from what I said is to be able to close the Claude Code session and its
terminal window properly from Oracle."*

**This departs from ADR-005 D7's rejection of closing the Terminal window
through AppleScript, for one case only: a confirmed oracle close of a
leftover host.** D7 rejected it for the handoff closer, where the host ends
itself 20 seconds after launching its successor: closing the window there
would end the shell the person may still use, it does nothing over Remote
Control, and nothing knew which window the host ran in. A leftover close is
different on each count. The person asked for that window to be gone, on a
confirm that names it; the host's terminal is now known, from `ps` and
Claude Code's per-process record (IDEA_024); and the tab is closed only when
nothing but its shell is left, so no shell with anything in it is ended. A
window over Remote Control, in an editor or in another terminal app is left
open and the result says so. The handoff closer is unchanged: it still
signals the process and never touches a window.

Who is offered Close: an orphan host of IDEA_025's leftover or unmanaged
class, and only when all of these hold. It is alive; its pid comes from
Claude Code's per-process record (`pid_source` `record` or `both`) with a
start token; no other source disputes it (no `pid_conflict`); its record's
`cwd` is this project's root, a folder under it or one of its worktrees, so
the evidence ties it to this project; and no live session in any registered
project lists it, the BUG_040 case (D7). An unmanaged host is offered
Attach instead only when the match is unique both ways: it is the only live
orphan host in the project that the project's one adoptable session could
take, and that session is the only one it could take (*amended 2026-10-25*:
six idle hosts were each offered Attach into one session, which none of them
could take, and it hid their Close). A row carries one step, so a host
offered Attach does not also show Close; that waits for the panel's second
action. A conflict row, a gone row, an active
one and a row of unknown liveness are offered nothing, and the panel says
"Its process ids disagree" on a conflict row rather than "process id not
recorded".

*Amended 2026-10-25 (BUG_087)*: "active" and "idle" are read from one
mid-turn answer, `signals.turn_state`, which the signals and the gate cache
share. When Claude Code's per-process record verified the host, its
`status` outranks the attention turn: `busy` is active, `idle` lets the host
become leftover or unmanaged once it has been quiet past
`orphan_idle_minutes`, and `waiting` is never closeable: the row is active
and raises the existing `waiting` alert, so the panel shows it under Needs
you as waiting, with no button. Without a record status, a `running`
attention turn counts as mid-turn only while the host was heard from within
`orphan_idle_minutes`, because a missed Stop left six idle hosts `active`
for up to 44 hours. The guards above are unchanged.

The confirm is D7's destructive sheet. Under "This ends" it names the
project, the Claude session id (its first 8 characters), the pid and the
idle time (true since BUG_078); the sentence is "Ends Claude in this window,
then closes the Terminal tab", with the `claude --resume` note; "Checked just
now" says no invariant session uses the window and that it is checked again
on Close window; the exact command sits behind its toggle, and the nonce
counts down.

The action is a new subcommand the `run` kind calls with its argv as data:
`invariant oracle close-host <host-id> --pid N --start TOKEN [--tty
ttysNNN]`. At the moment it runs it checks everything again and refuses with
a plain reason, having signalled nothing: Claude Code's record for the pid
must still exist, name this host session and carry this start; `kill 0`
must find the pid; `ps` must say it is `claude`, started at this start, on
this tty; and a fresh scan must find no live session listing the host. The
server re-derives the step from a fresh scan before it runs anything, as for
every action (D1), so an orphan that became active since the confirm is
refused there.

Ending Claude is one SIGTERM, then a wait of up to 10 seconds for the exit.
It never escalates to SIGKILL: a Claude that does not exit is reported as
"Claude did not exit; nothing else was done", and the window is left. Only
once Claude has gone, and only when its parent chain shows Terminal.app,
does an AppleScript find the tab whose tty is `/dev/<tty>`. The tty is the
script's argument (`on run argv`), never text in it. The tab is closed when
it is not busy and only a login shell is left in it, with its window when it
was the only tab; a busy tab, no matching tab, or a Terminal that is not
running is left as it is ("Claude ended; the window was left open because
…"). The script never names Terminal in a `tell` unless it is running, so it
cannot start it. The handoff closer's kill-time `ps -p` check is reused as
its primitives (`process_facts`, `is_claude_process_name`), not the detached
closer itself, because a close waits for the exit and reports it.

macOS asks for Automation permission the first time a process controls
Terminal. From the web agent under launchd that is a prompt on the Mac, or
error -1743; either reads "Claude ended; macOS has not allowed the oracle to
control Terminal", with where to grant it (System Settings > Privacy &
Security > Automation), and the `osascript` is bounded by a 30-second timeout
so an unanswered prompt cannot hold the job.

The result sheet is increment 5's: a refusal is "Why: invariant said: nothing
was done: …", and a close that ran says what happened under "What happened",
from the last line the command wrote.

Every host fact and effect is a seam (`oracle/closehost.HostSeams`): the
record reader, `ps`, `kill 0`, the signal, the parent chain and the
AppleScript runner. The oracle imports nothing from `hosts/`; the CLI's
`_oracle_host_seams` joins Claude Code's. The suite replaces it everywhere,
and a `conftest.py` guard refuses `os.kill` and `os.killpg` with any signal
but 0 unless the pid is a child the test spawned, beside the `osascript`
guard.

*Rejected:* closing the window whatever runs in it. It would end a shell, or
a command in it, that nothing asked to end. *Rejected:* SIGKILL after the
grace. A Claude that will not exit on SIGTERM may be writing; forcing it is a
decision the person makes at the Mac. *Rejected:* a separate action kind
that runs the close in the server. The subcommand keeps the close on the
same path as every other action, with its refusals in one place (D7).

Out of scope here: the leftover folders' "Remove…" (increment 7), IDEA_029's
last message, and IDEA_017's actions. IDEA_025's one summary line per
project in `oracle list` and the TUI is not built in this increment.

### D16 — The oracle may start a new Claude window, confirmed, in a registered project (IDEA_030)

*Added 2026-10-26. The user asked that the "Oracle can start a new session
with a remote control enabled on any of the projects that I want", and
settled the shape on IDEA_030 the same day.*

Until now every confirmed action ran a step in a session that exists, or
closed a window that exists. Starting a new `claude` process on the Mac at
a phone's request is a new power, and it is bounded as follows:

- **Confirm-first.** The start is an action like D1's: the panel's sheet and
  the TUI's modal show the exact command, the full first prompt and the two
  facts below before anything runs, and the server derives the start from
  its own fresh read (ADR-008 D9, D10, D13). The CLI command,
  `invariant oracle start-session`, is what the confirm runs.
- **Registered projects only.** The project is named by its key, root or
  name in the oracle's project index (`oracle/index`), and nothing else is
  launchable: no free path, and an entry whose root no longer carries a
  manifest is refused. The window starts in the entry's recorded root, the
  project's main checkout.
- **Remote Control on, and named.** The command line is always `claude
  --name <name> --remote-control <name> [prompt]`. The name is
  `<project>-<label>`, slugged; the label defaults to the time of the start
  (`MMDD-HHMM`, local). A name a live Claude window holds, in Claude Code's
  per-process records or in the process table, gets `-2`, `-3`, the user's
  choice over refusing it. `LaunchPlan` refuses an argv without the pair
  (ADR-005 D3).
- **Claude's default permission mode only.** There is no mode picker. The
  command never carries `--dangerously-skip-permissions`,
  `--allow-dangerously-skip-permissions` or `--permission-mode`:
  `LaunchPlan` refuses an argv that does, and a first prompt that starts
  with `-`, which `claude` would read as a flag, is refused before a plan
  is built. Every permission prompt of the new window reaches the person
  through Remote Control, and the oracle answers none (D8).
- **The prompt is data.** It is one positional argument, quoted by `shlex`
  into the private launch file the handoff already writes (ADR-005 D3,
  BUG_075), and never part of a shell string or the typed line.
- **No invariant session is pre-created.** Claude starts one itself when the
  work begins, as in any window. The launch also unsets an inherited
  `INVARIANT_HANDOFF`, so the new window claims no note it was not launched
  for (ADR-005 D6).
- **Facts that inform, not block.** The confirm shows how many Claude
  windows the scan knows to be alive in the project and whether its main
  checkout has uncommitted changes. Neither stops the start: the new session
  isolates before it edits, and a second window is the person's choice. The
  checkout is read with `GIT_OPTIONAL_LOCKS=0` (ADR-007 D16).
- **One launcher.** `session handoff`, the dashboard's terminal step and the
  start all open their window through one CLI function over
  `hosts/claude_code_launch`'s plan, launcher and successor watch; a start
  counts once a successor is seen alive. IDEA_029's hand-off will reuse it
  with a prepared prompt.

**What a stolen paired device token can now do.** A device token is still
reached only over Tailscale (ADR-008 D1), and its holder could already run
any recommended step and close a leftover window. With D16 it can also open
new Claude windows on the Mac. That is bounded three ways: only in the
registered projects, never in a path it names; only through the confirm,
which spends a single-use nonce bound to the device (ADR-008 D10); and only
in Claude's default permission mode, so every tool call that needs a
permission stops at a prompt the person sees in Remote Control, and the
oracle answers none. A start is written to the audit log like every action
(ADR-008 D13), and `oracle web revoke` ends the token. What it can do is
start a window and give it a first prompt; it cannot make that window act
without the person's permission where Claude asks for one.

*Rejected:* a free path or a folder picker. It would let a token start
Claude anywhere the user can write. *Rejected:* a permission-mode choice on
the sheet. A phone confirm is the wrong place to waive every later prompt.
*Rejected:* refusing a name in use. The user chose the suffix, and a second
start in the same minute is ordinary. *Rejected:* creating the invariant
session first. It would bind a session to a window that may never start,
and Claude starts the session in its own turn.

## Alternatives rejected

- **Interpret the state document in the page script.** It would put the
  vocabulary and the grouping in JavaScript that the suite tests only
  through a JS engine, and let the page and the TUI word one row two ways.
  The view model owns it (D2, D3).
- **Show engine names with a glossary.** A phone has no hover, and the user
  asked for plain words, not explained jargon.
- **Keep the per-project layout.** It answers "what exists" first. The user
  asked to see what needs them first (D2).
- **A centred dialog for the confirm.** On a phone it puts the buttons out
  of thumb reach and hides the screen it came from. A bottom sheet keeps
  both (D6).
- **Summarise every failure by pattern-matching its output.** The motivating
  failure, a target project's destination discovery that could not choose
  among booted simulators, was the target's gap, and a pattern written for
  it would guess on the next one. D9 names a cause only when invariant wrote
  it.
- **Destructive actions before IDEA_024 and BUG_078.** See D14.
- **Run destructive steps with `--force` so they do not stall.** It would
  give the oracle a bypass that the person at a terminal has to ask for
  themselves. D7 keeps the engine's refusals.
- **A native app, or a hosted copy.** IDEA_015 and an App Store app are out
  of scope. A home-screen web app needs no new transport (D10).
