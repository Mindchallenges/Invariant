---
id: IDEA_037
type: idea
title: The oracle closes background-work count-downs from the host transcript
status: proposed
residue: []
history: []
priority: medium
effort: M
tier: '5'
area: oracle/scan, oracle/signals, oracle/attention, hosts/claude_code
---

**Context:** [BUG_094](BUG_094.md)'s fix (session 980e0254, not yet merged)
adds a per-host background ledger to the oracle attention record. It counts
up when Claude Code launches an async Agent or a background Bash command
(PostToolUse) and counts down when a UserPromptSubmit starts with the
task-notification tag. Measured on local transcripts, about 56% of task
notifications (334 of 594) arrive while a turn is already running, as
queued-command attachments, and fire no UserPromptSubmit, so the ledger
misses roughly half its count-downs. The user therefore ruled that the
ledger lapses after `stuck_minutes` (20) without host activity. The
accepted cost: a hung background agent is never flagged `stuck`; it falls
back to idle after 20 minutes of silence. Hung background commands are
still flagged through Claude Code's per-process status "shell".

**Proposal:** the oracle scan closes ledger entries from the host's
transcript. A new `HostProbe` method, used by `oracle/scan`, checks the
ledger's open ids against the task-notification text in the tail of the
host's main transcript and closes the entries it finds there. Once
count-downs are reliable, the ledger can lapse at `orphan_idle_minutes`
instead of `stuck_minutes`, and a background agent silent past the stuck
threshold is flagged `stuck` like a mid-turn host.

**Acceptance:**

* A background agent whose task notification arrives mid-turn (queued, no
  UserPromptSubmit) no longer counts as in flight on the next scan.
* A host whose only remaining work is a background agent silent for more
  than 20 minutes is flagged `stuck`, not idle.
* A background agent that finishes normally never produces a `stuck` flag.
* A scan with no probe, or a transcript that cannot be read, behaves as
  today (ledger lapse at `stuck_minutes`), never worse.

**Architecture sketch:**

* `HostProbe` (ADR-007 D11) gains a transcript-tail read for notification
  ids; the Claude Code host implements it, the scan only joins.
* `oracle/scan` and `oracle/signals` (docs/architecture/oracle.md, "the scan,
  the signals and the recommendations", "Mid-turn").
* The attention ledger from BUG_094 (`oracle/attention`).

**Risks / open questions:**

* New probe contract: needs an ADR amending ADR-007's probe/scan contract
  (D11).
* How far back the tail read must go, and its cost per scan across many
  hosts.
* The task-notification text format is Claude Code's, not ours; a format
  change silently reverts to today's lapse behaviour.
* Depends on BUG_094's ledger landing first.

**Why now / why this tier:**

Closes the one cost the user accepted in BUG_094's fix; medium value
(hung background agents are a real but occasional case), M effort with an
ADR, so tier 5.

**Related:** [BUG_094](BUG_094.md) (parent), [BUG_096](BUG_096.md)
(subagent transcripts, same session), ADR-007.
