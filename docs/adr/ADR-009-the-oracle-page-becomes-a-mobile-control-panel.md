# ADR-009 — The oracle page becomes a mobile control panel, and the oracle gains destructive powers

- **Status:** Accepted, partly implemented. The user approved the mobile
  mockup on 2026-10-23. IDEA_027 builds it in increments (D14): D2–D6, D8,
  D11 and D12 shipped with increment 3, and D10 with increment 4, whose two
  iOS checks remain open (D10); D9 lands with increment 5, and D1's
  destructive actions with D7's guards in increments 6 and 7, after IDEA_024
  and BUG_078. The design of record (screens, components,
  tokens, wording) is [oracle-panel.md](../architecture/oracle-panel.md).
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
  recorded" and offer nothing destructive.

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
- **As shipped in increment 4, both checks are still open.** The build could
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
| 6 | Orphan classes and close host (IDEA_025 scope v1, D7) | IDEA_024, BUG_078 |
| 7 | Clean-up and unstick actions (IDEA_017's list, D7) | 6 |

Increments 3 to 5 change only the view, the page and a summariser, and run
nothing new. Increments 6 and 7 add the destructive powers, and their inputs
must be true first. IDEA_024 gives every live host its pid, so close host
has a target and "process id not recorded" goes. BUG_078 makes "idle 2
days" a fact rather than a transcript's modification time. A destructive
action decided on a false idle time is the failure D7 exists to prevent.

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
