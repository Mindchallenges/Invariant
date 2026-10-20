---
id: IDEA_009
type: idea
title: session handoff ends the old Claude Code session once the successor has launched
status: in_progress
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
area: hosts/claude_code_launch,cli/main
priority: high
effort: S
---

**The want.** After `invariant session handoff` has successfully launched the
successor host session, the old Claude Code session ends itself. Once the
successor is running the old session is not needed any more, but today it
stays open indefinitely: on 2026-10-20 two such sessions had been sitting idle
for about 10h after their handoffs. The launch path is
[`claude_code_launch`](../architecture/overview.md) (ADR-005, D3 and D6), driven
by the CLI's `session handoff` command ([cli.md](../architecture/cli.md)).

**Why it matters.** Every handoff leaves one orphaned host session behind. It
holds a terminal window, a host process and its bindings, and it invites the
user to type into a session whose work has moved on, which can lead to the
same work being started twice.

**Acceptance:**

* After a successful launch, the handoff output is printed in full, and a few
  seconds later the launching Claude Code process receives SIGTERM and exits.
  The terminal window stays open at a shell prompt.
* `--dry-run`, a refused handoff, and a failed launch never end the old
  session, so the user is never left with no session.
* `invariant session handoff --keep-open` launches the successor and leaves
  the old session running.
* If `CLAUDE_PID` is unset, or does not name a claude process, the handoff
  still succeeds, says that it is leaving the session open and why, and sends
  no signal.
* No long-lived background process is left behind: the delay is a short,
  bounded, detached sleep-then-kill (or an equivalent bounded mechanism) that
  is gone within seconds of the handoff.
* Ending the session works in any terminal and over Remote Control, because it
  signals the process rather than closing the window.

**Decisions (user, 2026-10-20):**

1. **Only after a successful launch.** Never on `--dry-run`, a refusal, or a
   failed launch.
2. **Mechanism:** after a short delay, so that the handoff output reaches the
   user first, send SIGTERM to the launching Claude Code process. Claude Code
   exposes `CLAUDE_PID` to its tool processes. If it is absent or not a
   claude process, say so and leave the session open.
3. **Opt-out:** `--keep-open`.
4. **Bounded delay:** no long-lived background process. Use a short detached
   sleep-then-kill or an equivalent.

**Risks / open questions:**

* Identifying "a claude process" from a PID has to be portable (macOS and
  Linux) and must not signal an unrelated process after PID reuse. The check
  runs immediately before the signal is sent.
* SIGTERM to the host may race the PostToolUse hook that records the handoff
  command. The delay has to cover the hook's write, or the record has to land
  before the kill is scheduled.

**Sequencing:** after BUG_068, which touches the same module
(`claude_code_launch`, the environment the successor is launched with).

**Why now:** every handoff leaks a session today, and it is a small change
(S) inside one module plus one CLI flag.
