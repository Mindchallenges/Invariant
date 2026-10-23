---
id: IDEA_025
type: idea
title: Session Oracle sorts orphan hosts and closes a leftover on confirm
status: dropped
residue: []
history:
- from: proposed
  to: dropped
  reason: 'Folded into IDEA_027 (2026-10-23): its scope v1 is built there unchanged
    as the orphan-host and close-host increment'
  forced: false
priority: high
effort: M
tier: '2'
area: oracle, cli/main, hosts/claude_code_launch
---

**Dropped 2026-10-23:** folded into IDEA_027 (the mobile-first control panel), which builds this scope v1 unchanged as its orphan-host and close-host increment. The body below stays the spec of record.

**The want.** The oracle tells you which orphan hosts are leftovers you can
close, and lets you close one safely, instead of listing every orphan as the
same undifferentiated `orphan host` row. On 2026-10-22 this Mac showed 15
orphan rows across two projects. Joined with Claude Code's per-process
records, they were three different things: 3 had no process at all (old
transcripts only); 9 were live Claude Code hosts whose invariant session was
already `torn_down`, idle from 1.4 h to 10 days, with up to 16 days of
uptime; and 3 were live hosts that never had an invariant session. The user
closed 7 of the leftover hosts by hand that day, after checking each one.
That checking is what this idea does for you.

**Settled with the user (2026-10-22):**

* Build it after IDEA_023 (the web dashboard).
* Everything here is show-only: no orphan class raises an alert. An alert
  for a long-idle leftover host is an open question below.
* Closing a host is confirm-first, like every oracle action (ADR-007 D18).

**Scope v1:**

* **Classify orphans** into four classes:
  * *gone*: no process. Hidden 24 h after its last activity.
  * *leftover host*: alive, its session ended, idle. Recommended action:
    close host.
  * *unmanaged host*: alive, never had an invariant session, idle.
    Recommended: start a session here, or close host.
  * *recently active unmanaged*: alive, no session, active recently. Shown
    only, with no action.
* **Close host**, a new confirm-first action. Before sending SIGTERM it
  verifies, at confirm time, that the pid is still a `claude` process and that
  its Claude session id matches the row's. It reuses the handoff closer
  (`HostCloser` / `DetachedCloser`, overview.md and ADR-005), whose kill-time
  `ps -p` check already guards against a reused pid. The confirm modal says
  that `claude --resume` can reopen the conversation.
* **Collapse orphans** into one expandable summary line per project in `oracle
  list`, the TUI and the IDEA_023 web page, e.g. "12 orphan hosts: 9
  leftover, 3 unmanaged". Expanding it shows the rows.

Out of scope: closing a host whose session is live, any action on a *gone*
row, and any alert.

**Acceptance:**

* Each orphan row is labelled gone, leftover, unmanaged or recently active
  unmanaged, and the label matches the host's real process and session
  state.
* A gone row is not shown once 24 h have passed since its last activity.
* Close host on a leftover row shows the pid, the session id and the
  `claude --resume` note, and does nothing unless confirmed. Once confirmed,
  that host's process exits, and nothing else is signalled.
* If at confirm time the pid is gone, is not `claude`, or carries a
  different session id, close host refuses and says why.
* Orphans show as one summary line per project by default in `oracle list`,
  the TUI and the web page, and can be expanded to the individual rows.
* No orphan class raises an alert or a notification.

**Architecture sketch:**

* Classification sits in `oracle/scan` and `oracle/signals`, from facts the
  probe provides (IDEA_024's per-process records, BUG_078's real last
  activity). `oracle/recommend` gains the leftover and unmanaged rows.
* The action layer runs only `invariant` commands (oracle.md, "Confirm-first
  actions"), so close host needs either a new `invariant` subcommand that the
  `run` kind calls (e.g. `invariant oracle close-host`) or a new action kind.
  The subcommand keeps the existing refusal rules intact.
* ADR-007 calls the oracle read-only. Sending a signal to a host is a new
  power and needs a decision recorded in ADR-007, or folded into IDEA_023's
  ADR.

**Relation to IDEA_017 (decided: kept separate).** IDEA_017 is the general
clean-up and unstick action set: stranded sessions, leaked background
processes, orphaned worktrees, stuck agents. This idea is narrower and
ready to build now: the orphan host classes, the summary line, and one
action, close host. It is the first IDEA_017 action to ship. Its
confirm-time process check is the pattern IDEA_017's process kill should
reuse. IDEA_017 keeps the rest of its list.

**Depends on:** IDEA_024 (liveness for every live host; without it most
leftovers read `alive = null`) and BUG_078 (last activity from the
transcript's last real entry; without it no orphan ever reads idle).

**Related:** IDEA_012 (parent), IDEA_017 (sibling, above), IDEA_023 (the web
page shows the summary line and offers close host under its confirm rules),
BUG_040 (a host rooted in another repository must never be offered close
host on a misattributed row).

**Risks / open questions:**

* **Alert for a long-idle leftover?** Optionally raise a low alert for a
  leftover host idle for more than 24 h. Left open; v1 is show-only.
* Closing the wrong host loses an in-flight turn. The pid, process name and
  session id check at confirm time is the guard; it must be tested against a
  reused pid and a mismatched session id.
* An unmanaged host may belong to a different project (BUG_040). The class
  should be "unmanaged here", not "unmanaged anywhere".

**Leftovers found on 2026-10-22, for the user to decide (nothing was
removed):** these are invariant sessions and worktrees in the other
registered project, not hosts, so they belong to IDEA_017's clean-up list
rather than this idea's scope.

* Session 4b7b00c1: isolated for 6 weeks. Its branch is 2 commits ahead of
  master (one is a failing repro test), 297 behind, and its worktree has 13
  uncommitted files.
* Abandoned session 0c269de4: its leftover worktree holds 1 unmerged commit,
  "Ruling recorded - the browser always stays in Debug builds".
* Abandoned session 292ad87d: its leftover worktree holds no commits and 1
  uncommitted edit to ADR-071.

**Why now / why this tier:**

Leftover hosts pile up on every machine that runs the oracle, and the user
closed seven by hand in one sitting. Medium-high value. M effort, mostly the
classification and the safety checks. Tier 2, because the user approved it
next after IDEA_023 and it is the first destructive action to ship.
