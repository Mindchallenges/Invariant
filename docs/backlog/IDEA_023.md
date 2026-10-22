---
id: IDEA_023
type: idea
title: Session Oracle web dashboard over Tailscale, with confirm-first actions
status: proposed
residue: []
history: []
priority: high
effort: L
tier: '3'
area: oracle, cli/main
---

**The want.** You can watch and steer the Session Oracle (IDEA_012) from your
phone while you are away from the Mac. Today the only dashboard is `invariant
oracle tui`, a terminal on the Mac itself, so a session that needs you while you
are away is invisible until you get back. The shape is a small web page served
from the Mac by the watcher, or by a sibling process under the same launchd
agent ([oracle.md](../architecture/oracle.md), "Shipped: the launchd agent").
You reach it from your phone over your private Tailscale network. It shows
everything the TUI shows and offers the same confirm-first actions (ADR-007
D18), confirmed on the page.

**Settled with the user (2026-10-22):**

* **Hosting.** Local plus Tailscale. The page is served from the Mac, and
  session data never leaves your devices. Nothing on the public internet can
  reach it. This is the route chosen over a hosted site (IDEA_015) for now.
* **Remote power.** View plus confirmed actions: the rows, states, signals and
  recommended commands of `oracle tui`, and the same action kinds the
  recommendations produce (`info`, `run`, `terminal`, each confirmed on the
  page). It NEVER answers a Claude Code permission prompt. Those are still
  answered in Claude Code itself, for example through Remote Control.
* **Auth.** The network is the gate: only devices on your tailnet can reach
  the page. On top of that, each device holds a token it stores on its first
  visit.

**Acceptance:**

* From your phone, over Tailscale, the page shows the same rows as `oracle
  tui`, and a change shows up within 5 s.
* A session waiting on you (a permission prompt or a question) is drawn red,
  as in the TUI.
* Acting on a row shows the exact command, its cwd and the variables it adds
  before anything runs. A confirmed action runs exactly once, even if the
  confirm is sent twice, and the page then shows its output and exit status.
* The page refuses the same steps the TUI refuses: a step on a row waiting
  on a prompt, a command that is not an `invariant` command, a cwd that does
  not exist, or a kind it does not know.
* A device without the token gets nothing: no rows and no actions. A device
  off the tailnet cannot connect at all.
* There is no path from the page that answers or approves a permission
  prompt.

**Architecture sketch:**

* The page reads the same `assess` document the TUI's `dashboard.load`
  reads, and acts through the same `actions` refusal and dispatch. It adds a
  transport, not new rules.
* A `terminal` step from the page opens a new host on the Mac through the
  handoff launcher, exactly as it does from the TUI.
* This is the first network listener invariant runs, and the first time an
  action can be confirmed from outside the Mac's own terminal. ADR-007 calls
  the oracle read-only, and D18 assumes the person is at the Mac. This idea
  needs its own ADR, or a new decision in ADR-007, for the listener, the
  token and the remote confirm.

**Related:**

* IDEA_012 (parent): the oracle, the snapshot, the watcher, the launchd agent
  and the confirm-first actions this builds on.
* IDEA_017 (sibling): extended actions (clean up, unstick). Once they ship,
  they should be offered from the page under the same confirm rules.
* IDEA_015 (alternative, not chosen for now): a hosted website. It sends a
  redacted snapshot off the machine and is read-only. This idea keeps the
  data on your devices and adds actions.
* IDEA_016 (alternative): a Claude artifact dashboard. It is also read-only
  and also off the machine.
* IDEA_013 (possibly overlapping): phone push. The page shows a waiting
  session only while it is open on the phone, so push may still be wanted.

**Risks / open questions:**

* **Bind address.** Should the server listen only on the Tailscale
  interface, or on all interfaces (the wildcard address) and rely on the
  firewall plus the token? Binding to the tailnet address alone is the safer
  default, but it must survive the interface coming up after the launchd
  agent starts.
* **Token issue and revoke.** How is a device's token issued (for example a
  one-time pairing code shown by an `oracle` command on the Mac), where is it
  kept on the Mac, and how is one device revoked without resetting the rest?
* **CSRF and replay on action POSTs.** An action must carry proof that it
  came from the page's own confirm (a per-render nonce, single use, with a
  short expiry), so that a replayed or forged request runs nothing.
* **Phone push.** Is IDEA_013-style push still wanted once the page exists?
  (Note: the brief named IDEA_014 here, but IDEA_014 is the status line;
  phone push is IDEA_013.)
* A `terminal` step opens a window on a Mac nobody is sitting at. That is the
  point, but its successor watch and its failure output must reach the page.

**Why now / why this tier:**

The user asked for remote management directly, and the sessions are already
driven remotely, so this is high value. It is L effort because of the trust
boundary (listener, token, remote confirm), not the rendering. It is Tier 3
because it adds a new external surface, a network listener, to the oracle.
