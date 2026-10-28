---
id: IDEA_035
type: idea
title: Launched windows end with Claude, and invariant flags hosts in a polluted environment
status: done
residue: []
history:
- from: proposed
  to: accepted
  reason: built in session 675189e4
  forced: false
- from: accepted
  to: in_progress
  reason: built in session 675189e4
  forced: false
- from: in_progress
  to: done
  reason: shipped in e3baef8 (session 675189e4)
  forced: false
priority: medium
effort: M
tier: '5'
area: hosts/claude_code_launch, cli/main, oracle/scan, oracle/signals
---

**Shipped 2026-10-28** (commit e3baef8).

Two leftovers of how Claude windows get started cost the user every day.
First, a window invariant launches (`session handoff`, `oracle
start-session`, the dashboard's terminal step) types `cd <main> && env -u
… /bin/sh <launch file>` into a Terminal `do script` login shell, so when
Claude exits the shell remains and the window stays at a prompt (BUG_100's
leftover, for every launched window). Second, a host started under a
polluted environment, from a Terminal.app that was itself started from a
Claude tool shell before BUG_068 and BUG_069's scrub fix, inherits
`COLORTERM=truecolor` and `CLAUDE_CODE_CHILD_SESSION=1`. Observed on such
hosts: Terminal.app, which has no 24-bit colour, renders large white or
inverse blocks; Claude says "Transcript saving is off"; Claude Code writes
no per-process record, so the panel cannot offer Close (BUG_099) and
activity and transcript tracking degrade (BUG_096). Nothing in invariant
tells the user, and the cure is simple: quit Terminal.app fully and
relaunch it from the Dock. The proposal: (a) make the typed launch line
`exec env -u … /bin/sh <launch file>`, so the tab's shell is replaced and
the window ends when Claude exits; (b) a `doctor` row and/or an oracle
signal that names a host running with an inherited `COLORTERM=truecolor`
or `CLAUDE_CODE_CHILD_SESSION`, with that remedy.

**Acceptance:**

* A window opened by `session handoff`, `oracle start-session` or the
  dashboard's terminal step closes (or reports itself finished, per the
  user's Terminal profile) when its Claude exits; no shell prompt remains.
* A launch that fails before Claude starts still leaves the by-hand line
  usable, and the existing successor watch still reports the failure.
* On a machine with a Claude host whose own environment carries
  `CLAUDE_CODE_CHILD_SESSION` or `COLORTERM=truecolor` inherited from its
  terminal, `invariant doctor` prints a `warn` row naming the host and the
  remedy (quit Terminal.app fully, relaunch from the Dock); with no such
  host the row passes. The row never sets the exit code.
* The oracle shows the same finding on that host's row (an info-level
  signal or a reason line on its card), so the user sees it from the phone.
* Variables Claude Code sets for its own tool shells never trip the check:
  `doctor` run from a clean host's tool shell reports clean.

**Architecture sketch:**

* `hosts/claude_code_launch` — the typed line (`plan_launch`, docs/architecture/oracle.md "Start a session
  (IDEA_030)"; the line cli.md shows under `session handoff`), now
  `exec`-ed.
* `doctor` (docs/architecture/cli.md "Setup") — one warn row.
* `oracle/scan` / `oracle/signals` — read the host process's environment
  once per verified pid (with the 60 s fact cache) and carry a flag the
  panel words.

**Risks / open questions:**

* The colour cause is inference, not proven read-only: the correlation
  between `COLORTERM=truecolor` under Terminal.app and the white blocks was
  exact across the open windows, but no controlled toggle was run.
* Reading another process's environment on macOS (`ps eww`) may be limited
  to the user's own processes and may be truncated; the check must judge
  the `claude` process's own environment, never `doctor`'s, since Claude
  sets markers on its tool shells.
* `exec` changes what a user sees when Claude crashes at start: the window
  vanishes with the error. The launch file may need to pause on a
  non-zero exit.
* Whether a Terminal profile set to "don't close" keeps a finished window;
  that is the user's setting and fine to leave.

**Why now / why this tier:**

Medium value (every launched window, and the hidden cause of BUG_099 and
part of BUG_096), small to medium effort in two known places; T5, scheduled
by the user after the current oracle batch.

**Related:** BUG_098 (Close leaves the tab open), BUG_099 (no record, no
Close), BUG_100 (handoff leaves a prompt), BUG_092, BUG_093, BUG_094,
BUG_095, BUG_096 (current oracle batch), BUG_068 and BUG_069 (launcher
environment scrub, fixed: the source of the polluted Terminal.app),
IDEA_027 (control panel), IDEA_030 (start a session, the launcher).
