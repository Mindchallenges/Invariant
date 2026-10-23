---
id: IDEA_027
type: idea
title: Session Oracle web page becomes a mobile-first control panel
status: proposed
residue: []
history: []
priority: high
effort: XL
tier: '3'
area: oracle, cli/main
---

**The want.** The Session Oracle web page (IDEA_023, over HTTPS by IDEA_026)
should be a clear, mobile-first control panel for the phone. Today it is a
terminal listing rendered as HTML. On 2026-10-23 the user said, after using
it on an iPhone: "the UI is really not mobile friendly and unclear. It has
to be a nice mobile friendly control panel." What they saw on the phone:

* Internal jargon on the surface: "(isolated)", "early · bugfix:fixing",
  "pid unknown", full host UUIDs, and the confirm's environment note ("on
  top of this environment, with Claude Code's host markers and
  INVARIANT_SESSION_ID removed").
* Raw absolute paths and argv are the main content, and they wrap mid-word.
* The header's "2 need you" does not take you to them. There is no
  attention-first order and no summary.
* The only control is one small "run..." per card. There is no session
  detail view and no other action.
* A failed action dumps raw shell output (in one case a whole destination
  discovery script) instead of a plain reason.
* There is no home-screen app (manifest, icon), no safe-area insets, and
  the button casing is inconsistent ("Run it" / "close").

The proposed shape is a redesign of the page's view and markup
(`oracle/web`, `oracle/webpage`, `oracle/webactions`; see
[oracle.md](../architecture/oracle.md), "Shipped: the web page"). It also
takes in the scope of IDEA_017 (clean-up and unstick actions) and IDEA_025
(orphan host classes and close host), so that the panel has real controls.
Both entries are dropped as folded into this one, and their bodies stay the
spec of record for those actions.

**Settled with the user (2026-10-23):**

* **Process.** Plan first, then design: a mobile mockup the user approves
  before any build. Then implement in increments. Everything is documented
  in a new ADR, in oracle.md and in the guide,
  [The oracle on your phone](../guide/oracle-web.md).
* **First screen.** A summary strip, e.g. "2 need you · 3 working · 1
  stale", where each count jumps to its group. Cards follow in the order
  needs-you, then stuck or orphaned, then working, then idle. Each card
  shows the project name, a short session label, the state in plain words
  ("Waiting for your answer · 12 min") and one primary button. Paths, argv,
  pids and host ids appear only in the detail view.
* **Controls.** All four are in scope: (a) a session detail view, (b)
  IDEA_017's actions, (c) IDEA_025's close a leftover host, and (d) copy a
  command to the clipboard. (b) and (c) are folded in here.
* **Home-screen app.** In scope: a web manifest, an icon, standalone
  display and safe-area insets.
* **Push alerts.** Out of scope. They stay in IDEA_013 as a follow-on.
  This item's home-screen app is the precondition for iOS web push, one of
  IDEA_013's provider options.
* **A failed action.** One plain line of reason, plus a "show full output"
  toggle.

**Constraints that stay:** Tailscale only, paired device token, and every
action confirmed first (ADR-007 D18, ADR-008 D8 to D13). The oracle never
answers or approves a permission prompt, and never runs a step on a row
waiting on one. Auto-refresh stays, with "updated N s ago" and a stale flag
(ADR-008 D6, D18). Assets stay inline under the nonce CSP, with no CDN and
no other origin.

**Scope v1, in increments (order to be confirmed by the plan):**

1. **Plan.** The increments and the decisions the ADR must record, above
   all that the oracle gains destructive powers (signal a host, kill a
   process, tear down a worktree), which amends ADR-007's read-only premise.
2. **Design.** A static mobile mockup of the first screen, a card, the
   detail view, the confirm sheet and a failed action, drawn from the
   project's existing look. The user approves it before step 3.
3. **Plain-language panel.** The summary strip, attention-first ordering,
   plain-word states and labels, the detail view, copy command, consistent
   button wording, and no mid-word wrapping.
4. **Home-screen app.** Manifest, icon, standalone display, safe-area
   insets, all served under the existing CSP and token rules.
5. **Failure reasons.** A failed action leads with a one-line reason; the
   full output sits behind a toggle.
6. **Orphan hosts and close host** (IDEA_025's scope v1, unchanged): the
   four orphan classes, one summary line per project, and close host with
   its confirm-time pid, process-name and session-id check.
7. **Clean-up and unstick actions** (IDEA_017's list): close or abandon a
   stranded session, kill a leaked background process, tear down an
   orphaned worktree, nudge or re-dispatch a stuck agent. Each is
   confirm-first and each re-checks its precondition at confirm time.

Out of scope: push notifications (IDEA_013), a hosted copy (IDEA_015), and
any change to transport, pairing or the tailnet-only rule.

**Acceptance:**

* On a phone-width screen, the first view shows the summary strip and the
  needs-you cards without horizontal scrolling, and tapping a count scrolls
  to that group.
* No card or confirm in the default view shows an absolute path, an argv, a
  pid, a host UUID or an engine state name. Each is reachable from the
  session's detail view, where long values wrap at path separators rather
  than mid-word.
* Every action IDEA_017 and IDEA_025 list can be started from the panel. It
  shows what it will run and destroy, does nothing unless confirmed, and a
  destructive one refuses at confirm time if its precondition no longer
  holds.
* A failed action shows one plain sentence of reason. The full output
  appears only after tapping "show full output".
* Added to the home screen, the page opens standalone with its own icon and
  respects the safe areas. It still refuses an unpaired device and still
  loads nothing from another origin.
* The page still refreshes itself, says "updated N s ago", and flags stale
  rows. No path lets it answer a permission prompt.

**Architecture sketch:**

* `oracle/web` (the view model): plain-language labels, attention order and
  the summary counts, built server-side so the page renders rather than
  interprets.
* `oracle/webpage`: new markup and CSS for cards, the detail view and the
  confirm sheet, the manifest and icon routes, and the copy action, all
  under the nonce CSP.
* `oracle/webactions` and `oracle/actions`: new action kinds or `invariant`
  subcommands for close host and the IDEA_017 actions, plus a failure-reason
  summariser beside the job output.
* `oracle/scan` and `oracle/signals`: the orphan classes (IDEA_025).
* A new ADR (ADR-009) for the panel and the oracle's destructive powers.
  oracle.md and the oracle-web guide are updated per increment.

**Depends on:** IDEA_024 (every live host's pid, so "pid unknown" goes and
close host has a target), BUG_078 (real last activity, so "waiting 12 min"
and "idle" are true), BUG_040 (a misattributed cross-root host must never be
offered a destructive action).

**Related:** IDEA_023 and IDEA_026 (the baseline, done), IDEA_017 and
IDEA_025 (folded in here), IDEA_013 (push, the follow-on), IDEA_015
(hosted alternative, unaffected). Related note, not filed: on 2026-10-23 a
"re-run the suite" from the page failed because the target iOS project's
own destination discovery could not choose among several booted simulators
with no destination id set. That is a target-project gap, not the oracle's.
Here it is only the motivating case for plain failure reasons.

**Risks / open questions:**

* Destructive actions from a phone raise the cost of a misclassified row.
  The confirm-time checks from IDEA_025 and IDEA_017 must ship with the
  actions, never after them.
* Plain-language labels must not hide a fact the user needs to decide on a
  confirm. The detail view stays one tap away from every confirm.
* Summarising a failure in one line risks naming the wrong cause. The
  summariser should say "failed, exit N" plus the last meaningful line when
  it cannot do better, and never guess.
* iOS standalone mode keeps its own cookie store, so a home-screen install
  may need its own pairing. The plan should check this and the guide should
  say so.

**Why now / why this tier:**

The user drives sessions from their phone daily and asked for this
directly. It also folds in the two queued oracle action ideas. High value,
XL effort (a redesign, a home-screen app, a new ADR and seven increments),
so tier 3 at high priority.
