---
id: IDEA_017
type: idea
title: Session Oracle can clean up and unstick sessions, each on confirm
status: proposed
residue: []
history: []
priority: medium
effort: M
tier: '5'
area: oracle, cli/main
---

**The want.** The Session Oracle (IDEA_012) can clean up and unstick
sessions, not just run their recommended next command. v1's actions run the
recommended command after a confirm. This idea adds:

* closing or abandoning a stranded session
* killing a leaked background process left behind after a hand-back
* tearing down an orphaned worktree
* nudging or re-dispatching a stuck agent

They are offered from the dashboard and from `invariant oracle` subcommands.

**Fixed by the interview (2026-10-22):** every action needs confirmation
first. It is one key, then a confirm modal that shows the exact command and
its cwd. The oracle never answers or approves a permission prompt for the
user, and nothing in this idea changes that.

**Acceptance:**

* Each action shows the exact command and what it will destroy, if anything,
  before it runs, and does nothing unless you confirm.
* A destructive action (a worktree teardown, a process kill) re-checks its
  precondition at confirm time, and refuses if the session became live in
  the meantime.
* Every action runs through the same CLI path a user would type, so the
  engine's gates still apply. The oracle gets no bypass.
* There is no path by which the oracle answers or approves a host permission
  prompt.

**Risks / open questions:**

* A process kill or teardown on a misclassified session destroys work. The
  liveness check (pid plus process-name guard) must be trustworthy before
  any destructive action ships.
* BUG_040's mis-attributed cross-root hosts must never be offered a
  destructive action.

**Why now / why this tier:**

Follow-on to IDEA_012. Medium value. M effort, mostly safety checks. T5.
