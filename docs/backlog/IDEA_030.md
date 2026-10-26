---
id: IDEA_030
type: idea
title: Oracle starts a new Remote Control session in any registered project
status: done
residue: []
history:
- from: proposed
  to: accepted
  reason: null
  forced: false
- from: accepted
  to: in_progress
  reason: null
  forced: false
- from: in_progress
  to: done
  reason: null
  forced: false
priority: high
effort: M
tier: '2'
area: oracle/actions, oracle/panel, oracle/index, hosts/claude_code_launch, cli/main
---

**The want.** From the phone, start a new Claude window on the Mac, with
Remote Control on, in any project the oracle knows. On 2026-10-26 the user
asked that the "Oracle can start a new session with a remote control enabled
on any of the projects that I want". Today the panel
([oracle-panel.md](../architecture/oracle-panel.md)) can only act on a
session that already runs; starting work in another project means walking
to the Mac, opening Terminal and typing the `claude` line by hand.

The proposed shape is one launcher, a CLI command at its core, that the
page and the TUI both run:

```
invariant oracle start-session <project> [--prompt TEXT] [--name LABEL]
```

It opens a new Terminal window on the Mac that runs
`claude --name <name> --remote-control <name>` in the project's main
checkout, with the first prompt if one was given. Claude then starts the
invariant session itself, as it does today; the oracle pre-creates no
invariant session.

**Settled with the user (2026-10-26):**

* **Which projects.** Registered invariant projects only, the oracle's
  project index (`oracle/index`,
  [oracle.md](../architecture/oracle.md), "Shipped: home and project
  index"). A name outside that list is refused; no free path is accepted.
* **The name.** The Remote Control name is the project plus an editable
  label, by default the project and the time. Once the new window appears
  in a scan, the panel shows it under Working by that name, with a link to
  it.
* **Entry points on the phone.** A "New session" button in the overview's
  top bar opens a sheet with a project picker and an optional first-prompt
  field; a per-project shortcut opens the same sheet with that project
  preselected.
* **Permission mode.** Claude's default only. There is no mode picker and
  never a bypass flag. Every permission prompt reaches the user through
  Remote Control; the oracle never answers one.
* **Surfaces.** The CLI command (the testable core), the phone page, and a
  key in the Textual TUI ([oracle.md](../architecture/oracle.md), "Shipped:
  the dashboard") that opens the same confirm.
* **Guards that inform, not block.** The confirm sheet shows how many Claude
  windows are already open in that project and whether its main checkout
  has uncommitted changes. Neither stops the start.
* **The shared launcher.** This is built first as the one "open a new Claude
  window" path: project, optional prompt, Remote Control, confirm-first.
  IDEA_029's hand-off (follow up, file a bug, file an idea) then reuses it
  and only fills in the prompt.

**Constraints that stay:** every action is confirmed first, and the confirm
shows the exact command and the full first prompt before anything runs
(ADR-009 D1, D6). Tailscale only, paired devices only (ADR-008). Tests never
run osascript, launchctl, a real kill or tailscale, and never touch the real
`~/.claude`. The prompt is passed as data, never through a shell string.

**New power, so ADR-009 needs a new decision, D16.** The oracle starting a
new process on the Mac at a phone's request is new: until now its confirmed
actions run steps in a session that exists, or close one. D16 must record
that the start is confirm-first, limited to registered projects, always in
the main checkout, always with Remote Control on and Claude's default
permission mode, and reachable only over Tailscale from a paired device
(ADR-008). IDEA_029 had pencilled its transcript decision as the next
number; it moves to D17.

**Acceptance:**

* `invariant oracle start-session <project>` for a registered project opens
  one new Terminal window in that project's main checkout running
  `claude --name <name> --remote-control <name>`, the name being the project
  plus the label (default: project and time); with `--prompt`, Claude starts
  with that prompt. A project not in the index is refused with a plain
  reason and nothing is launched.
* On the phone, "New session" in the top bar and the per-project shortcut
  both open a sheet with the project picker (preselected from the shortcut),
  the editable label and an optional prompt. Nothing launches until the user
  confirms the sheet, which shows the exact command, the full prompt, the
  number of Claude windows already open in that project and whether its
  main checkout has uncommitted changes.
* After a confirmed start, the new window appears on the overview under
  Working by its Remote Control name, with a link to it, once a scan sees
  it.
* No path offers a permission-mode choice or passes a bypass flag, and the
  oracle answers no permission prompt in the new window.
* A key in the TUI opens the same confirm and runs the same command.
* An unpaired device cannot start a session from any route.

**Architecture sketch:**

* `hosts/claude_code_launch`: reuse `require_remote_control`, the
  `--name`/`--remote-control` pairing, the Terminal window launch and the
  CLI's `_handoff_launcher` seam; the new command is a second caller, not a
  second launcher.
* `oracle/index`: resolve a project name to its main checkout; the only
  source of launchable projects.
* `cli/main`: `invariant oracle start-session`.
* `oracle/webactions` and `oracle/panel`: a new confirm-first action kind
  carrying project, label and prompt; the confirm's open-window count and
  dirty-checkout facts.
* `oracle/webpage`: the "New session" button, the per-project shortcut and
  the sheet, under the nonce CSP.
* The TUI dashboard: one key binding to the same confirm.
* ADR-009 D16; oracle.md, oracle-panel.md and the oracle-web guide updated.

**Related:** IDEA_029 depends on this item (its hand-off reuses this
launcher and fills in only the prompt). IDEA_027 is the panel's umbrella
(increment 7 still open); this entry is new scope alongside it. ADR-005 is
where the handoff launcher it reuses was decided.

**Risks / open questions:**

* Remote Control name collisions: two starts in the same minute with the
  default label. The plan should decide whether the label gets a suffix or
  the confirm refuses a name already in use.
* Matching the new window to its row relies on the scan seeing the host and
  its name; until it does, the page needs a plain "starting" state rather
  than nothing, and a start that never appears needs a reason.
* A dirty main checkout is shown, not blocked; the new session isolates
  before editing, but the user should still see it on the confirm.
* Starting a process on the Mac from the phone widens what a stolen paired
  token can do. D16 should state that this is bounded by the registered
  list, the confirm, and Claude's own permission prompts.

**Why now / why this tier:**

The user drives sessions from the phone daily and asked for this directly,
and it is the shared launcher IDEA_029 waits on. High value; M effort (one
CLI command and one action kind on an existing launch seam, a sheet, a TUI
key and one ADR decision), so tier 2 at high priority.
