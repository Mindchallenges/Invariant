---
id: IDEA_012
type: idea
title: Session Oracle v1 — a machine-wide monitor of every running session
status: proposed
residue: []
history: []
priority: high
effort: L
tier: '2'
area: oracle (new), hosts/claude_code, cli/main
---

**The want.** One place, for the whole machine, that knows every running
session in every invariant project and tells you when one needs you. Today
several sessions run at once across projects, often driven remotely, and
nothing says that one is waiting on a permission prompt, blocked by a gate,
stuck, finished, or failing. That state is split across per-project stores
(see [state_model.md](../architecture/state_model.md)), and no hook sees a
permission prompt. The shape is a machine-wide, read-only **Session Oracle**:
an always-on background watcher (launchd), a user-level project index, and a
live terminal dashboard. It lists every session, shows its state, recommends
the exact next command for each, and raises a macOS notification when a
session needs you.

**Settled in the interview (2026-10-22):**

* **Scope.** Every invariant project on the machine. A project registers
  itself on `init` and on its first session start, and can be removed.
* **Rows.** Invariant sessions with their Claude Code host sessions nested
  under them, plus host sessions that have no invariant session. Live, parked
  and stranded sessions are all shown.
* **"Oracle"** means it recommends the exact next command (argv and cwd) for
  each session, not just its state.
* **Alerts in v1:** waiting on you (a permission prompt or a question); a gate
  is blocked; stuck (no activity for 20 minutes while a turn is running);
  finished and awaiting merge; an error or a failed test run.
* **Shown but no alert:** stranded sessions, leaked worktrees and processes,
  parked sessions, and an idle "your turn".
* **Powers.** Every action needs confirmation first: one key, then a confirm
  modal that shows the exact command. It never answers or approves a
  permission prompt for you.
* **Primary surface.** The live dashboard: a Textual TUI that ships as an
  optional extra (`invariant[oracle]`), with macOS notifications as the v1
  alert channel.

**Acceptance:**

* With 3 projects and 5 sessions open, the dashboard shows all of them within
  5 seconds.
* A session waiting on a permission prompt turns red and you get a macOS
  notification within 30 seconds. Answering the prompt clears the row and
  re-arms the alert.
* A project added with `invariant init`, or by starting its first session,
  shows up with no manual step. `oracle projects remove` takes it out.
* The same rising edge never alerts twice. `invariant oracle list --json`
  matches the dashboard.
* No scan writes to any project's state root. One broken project shows as an
  error row and does not hide the others.
* An action runs only after you confirm the exact command it will run.

**Architecture sketch** (from the approved plan, 2026-10-22; summarised):

* A new `oracle/` package that nothing in `core/` or `domains/` imports.
  Modules: home (user-level state dir, `$INVARIANT_HOME` override); a project
  index keyed by state root; a per-project attention sidecar under each state
  root (one writer, atomic replace); a read-only scan over the inert store;
  pure `classify` and `recommend` functions with an injected clock; a gate
  cache that evaluates only on a fingerprint change while the session is
  idle; a 5-second watcher tick with a snapshot and a rising-edge de-dup
  ledger; a notifier seam (osascript); a launchd installer seam; and the TUI.
* Hook changes in the Claude Code host adapter (see
  [overview.md](../architecture/overview.md), `hosts/`): a seventh hook,
  `Notification`, plus sidecar writes on PreToolUse(AskUserQuestion),
  PostToolUse, UserPromptSubmit, Stop and SessionEnd. These writes are
  guarded and can never change a hook verdict.
* CLI (see [cli.md](../architecture/cli.md)): `invariant oracle list | watch
  | tui | projects list|add|remove|prune | install | uninstall | notify-test`.
* Docs: a new ADR ("a machine-wide, read-only oracle": user-level state, the
  attention sidecar, the first stored pid, gate alert = a recorded refusal,
  confirmed subprocess actions, the first daemon, the first optional
  dependency); a new architecture page for the oracle; updates to
  overview, state_model and cli.
* Build order, one landable TDD increment each: (1) home + project index +
  auto-registration; (2) attention sidecar + Notification hook; (3) scan,
  signals, recommend, `oracle list`; (4) watcher, de-dup ledger, notifier,
  `watch --once`, `notify-test`; (5) gate cache; (6) launchd
  install/uninstall; (7) TUI behind the `[oracle]` extra.

**Related:**

* BUG_040 is a **known gap**, documented and not fixed here. A host session
  rooted in another repository is mis-attributed, so it shows as an orphan
  host row.
* IDEA_007 is a sibling. The host-binding history decides how a resumed
  session's earlier hosts nest.
* IDEA_010 is a sibling. A host-layer write refusal is one more "blocked"
  cause.
* Follow-ons, each filed as its own idea: IDEA_013 (phone push), IDEA_014
  (status line and prompt-line injection), IDEA_015 (a hosted web
  dashboard), IDEA_016 (a Claude artifact dashboard), IDEA_017 (extended
  oracle actions), IDEA_018 (alerting on an idle "your turn").

**Risks / open questions:**

* A host that crashed without SessionEnd could stay "waiting" forever. The
  plan answers this with a pid liveness check and a process-name guard, plus
  a fallback to amber after 6 hours.
* launchd runs with a minimal environment, and an editable install can move.
  The plist uses an absolute interpreter and an explicit PATH, and `doctor`
  checks that the interpreter still exists.
* The `notification_type` field may drift between Claude Code versions. The
  plan falls back to matching the message text.
* Whether the host pid is present in the hook environment is unconfirmed.
  Increment 2 checks it against a real hook first, and falls back to walking
  the parent pid if it is missing.

**Why now / why this tier:**

High value: it touches every session, every day, and it is the only thing
that would catch a session silently waiting on you. L effort: it is the first
daemon, the first user-level state and the first optional dependency, plus a
new hook. That makes it T2 (strategic), not T1.
