---
id: IDEA_024
type: idea
title: Session Oracle knows every live host's pid from Claude Code's records
status: done
residue: []
history:
- from: proposed
  to: accepted
  reason: Shipped in 32b104a
  forced: false
- from: accepted
  to: in_progress
  reason: Implemented in 32b104a
  forced: false
- from: in_progress
  to: done
  reason: Shipped in 32b104a
  forced: false
priority: high
effort: S
tier: '2'
area: oracle/scan, hosts/claude_code_launch
---

**The want.** The oracle knows whether every running Claude Code host is
alive, not only the ones that have written an attention record. Today a
host's liveness comes from the pid in its attention record, and a host that
has not written one since the Notification hook was installed has no pid, so
it reads `alive = null` (oracle.md, "Host liveness"). On 2026-10-22 that was
most of the orphan hosts on this Mac, some with up to 16 days of uptime. With
liveness unknown, the oracle cannot offer adopt (it requires a known-alive
host), cannot tell a leftover host from a closed one, and cannot safely offer
any action that signals a process.

Claude Code keeps a per-process record, `~/.claude/sessions/<pid>.json`,
carrying the pid, the Claude session id, the cwd, the session name and its
start time. Joining it with the oracle's host ids gives an authoritative
pid-to-session map for every live host, whatever hooks it ran. It is
undocumented Claude Code internals, so it must be optional: read it when it
is there and parses, and fall back to today's behaviour when it is not.

**Acceptance:**

* A live Claude Code host that never wrote an attention record shows as
  alive, with its pid, in `oracle list`, the TUI and the snapshot.
* A host whose per-process record names a pid that is gone, or a pid that is
  no longer a `claude` process, shows as not alive.
* With the per-process records missing, unreadable, or in an unexpected
  shape, the oracle behaves exactly as it does today (attention-record pid,
  else `null`), and `invariant doctor` or the scan says the source was not
  used. Nothing crashes.
* When both sources name a pid for one host, the attention record and the
  per-process record agree, or the row says they disagree; the oracle never
  silently picks one.
* The oracle still imports nothing from `hosts/`, and no scan writes
  anything.

**Architecture sketch:**

* Extend `HostProbe` (ADR-007 D11) with a host-process read, for example
  `host_processes()` returning `{session_id: (pid, name, cwd, started)}`.
  `hosts/claude_code_launch.ClaudeCodeProbe` implements it; the scan only
  joins. The suite injects a fake probe as it does today.
* The pid check keeps today's guard: `os.kill(pid, 0)` plus the
  `is_host_process` name check, cached as now.
* The record's start time and name are kept on the row, so the dashboard can
  show uptime and the name the host was launched with.

**Related:**

* IDEA_025 (orphan management) depends on this: its classification and its
  close-host action need trustworthy liveness.
* IDEA_017's own risk note says the liveness check must be trustworthy
  before any destructive action ships. This is that check for hosts.
* BUG_078 (last activity from transcript mtime) is the sibling read. Both go
  through the same probe and are cheapest built together.
* BUG_040: a host rooted in another repository is still attributed by its
  hooks' project; this idea gives it a pid, not a different project.

**Risks / open questions:**

* The file format is not a Claude Code contract and can change in any
  release. The fallback must be the tested default, and a shape change must
  read as "source not used", never as "host gone".
* Is the cwd in the record good enough to attribute a host with no attention
  record to a project, or does attribution stay transcript-based?

**Why now / why this tier:**

It makes the oracle's host rows true for every live host, and it unblocks
IDEA_025, which the user approved to build after IDEA_023. S effort, one
probe method plus its fallback. Tier 2: medium value on its own, with a strong
unlock.
