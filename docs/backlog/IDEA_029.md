---
id: IDEA_029
type: idea
title: Oracle panel shows a session's last message and hands it off
status: proposed
residue: []
history: []
priority: high
effort: M
tier: '2'
area: oracle/panel, oracle/scan, oracle/actions, hosts/claude_code_telemetry
---

**The want.** The oracle's control panel shows what each session last said,
and lets the user act on it from the phone. On 2026-10-25 the user said the
panel works but "I cannot close the orphan sessions, and I also like to see
their last messages in the Oracle. For example, if they are asking me to do
some things or they claim something, it is open. So I can decide to just
close them or to ask you to take a follow-up action or create a bug or an
idea in the backlog." Today a card and its detail view
([oracle-panel.md](../architecture/oracle-panel.md), screens 1, 2 and 6)
say what state a session is in, never what its Claude window last asked or
claimed, so the user has to walk to the Mac to read it before deciding
anything.

The proposed shape has two parts.

1. **Read.** For every session row and every orphan host row, the panel
   shows the last assistant text from the host's transcript. A card shows a
   short excerpt, a few lines at most. The detail view shows the full text,
   with a "Show all" toggle for a long message. The user's last prompt is
   shown above it as context when it comes from the same read at no extra
   cost.
2. **Hand off.** The detail view offers three actions on that message: "Ask
   Claude to follow up", "File a bug" and "File an idea". On confirm, the
   panel opens a new Claude window on the Mac, rooted in that session's
   project. The window starts with a prepared prompt that quotes the
   message and names the session id, the project and the action (follow
   up; file a bug through backlog-curator; file an idea through
   backlog-curator). Nothing is ever typed into the existing session. The
   launch reuses the existing seam: `oracle/actions`' launch line and the
   CLI's `_handoff_launcher` ([oracle.md](../architecture/oracle.md), the
   `terminal` action kind).

Close stays where it is, in IDEA_027 increment 6 (IDEA_025's scope v1).
This entry adds only the reading and the hand-off, so that per row the user
picks close or a hand-off. It is filed as its own entry, linked to IDEA_027
as its umbrella, because it is new scope the IDEA_027 increment plan does
not list (call it increment 6b in that plan when it is scheduled).

**Constraints that stay:** the oracle never answers or approves a
permission prompt. Every action is confirmed first, and the confirm sheet
shows the exact command and the full prepared prompt before anything runs
(ADR-009 D1, D6). Tailscale only, paired devices only (ADR-008). Scans stay
read-only. The oracle imports nothing from `hosts/`: the transcript read
goes through the `HostProbe` seam (ADR-007 D11), not into `oracle/`.

**New exposure, so ADR-009 needs an amendment (a new D15).** Transcript
text on a phone is content the oracle has never served. The decision must
settle that:

* the text is served only to a paired device, on the same token rules as
  the rest of the page;
* nothing is persisted beyond the snapshot: no transcript text in any
  oracle file, log or record other than the in-memory or snapshot read;
* there is a length cap on what is read and served;
* the text is HTML-escaped and rendered as plain text, never as markup
  (no markdown rendering, no links made live). The CSP is unchanged.

**Acceptance:**

* A session row and an orphan host row whose transcript has an assistant
  entry show an excerpt of its last assistant text on the card, a few lines
  at most; a row with none shows no excerpt and no placeholder text.
* The detail view shows the full last assistant text up to the cap; a long
  message is collapsed behind "Show all". Where available, the user's last
  prompt is shown above it, labelled as the prompt.
* Text containing markup or script (for example `<script>` or a markdown
  link) displays literally as characters and executes nothing; the page
  still loads nothing from another origin.
* Choosing "Ask Claude to follow up", "File a bug" or "File an idea" shows
  a confirm sheet with the exact launch command and the full prepared
  prompt; nothing happens unless confirmed. On confirm, a new Claude window
  opens on the Mac in that session's project with that prompt, and the
  existing session receives no input.
* An unpaired device gets no transcript text from any route, and no
  transcript text is written to disk by the oracle.
* A row whose project attribution is not known to be right (BUG_040, a
  host rooted in another repository) offers no hand-off.

**Architecture sketch:**

* `hosts/claude_code_telemetry` / `hosts/claude_code_launch`: the probe
  gains a capped tail read returning the last assistant text and the last
  user prompt. This is the same reader BUG_078's fix adds for the last
  timestamped user/assistant entry.
* `oracle/scan`: carries the text into the snapshot through `HostProbe`.
* `oracle/panel`: the excerpt and the full text as plain strings, plus the
  three hand-off rows in the detail's "More actions".
* `oracle/webpage`: escaped plain-text rendering and the "Show all" toggle,
  under the nonce CSP.
* `oracle/actions` / `oracle/webactions`: three new hand-off kinds on the
  `terminal` launch path, each building its prepared prompt.
* ADR-009 D15; oracle.md, oracle-panel.md and the oracle-web guide updated.

**Depends on:** BUG_078 lands first or together, since its fix is the same
transcript tail read. IDEA_024 (every live host's pid) and BUG_040 (a
misattributed cross-root host) gate the hand-off: a row whose project is
wrong must never be offered a hand-off under that project.

**Related:** IDEA_027 (umbrella; close host is its increment 6), IDEA_025
(dropped, folded into IDEA_027; the orphan classes this reads for),
IDEA_013 (push, which could later carry the same excerpt).

**Risks / open questions:**

* Transcript text may hold secrets or personal data the user typed. Paired
  devices and the cap limit exposure; whether to redact anything (for
  example through the privacy patterns) is for D15 to settle.
* The transcript JSONL format is Claude Code internals. The read must
  tolerate an unknown or partial last line and fall back to no excerpt,
  never to a wrong one.
* A last assistant entry may be tool use with no text. Whether to walk back
  to the newest entry that has text, and how far, is for the plan.
* A prepared prompt that quotes the message passes transcript text into a
  new session; it must be passed as data, never through a shell string.

**Why now / why this tier:**

The user drives sessions from the phone daily and named this as the
missing half of deciding what to do with an open or orphaned session. High
value; M effort (one probe read, one ADR decision, view and three action
kinds on an existing launch seam), so tier 2 at high priority.
