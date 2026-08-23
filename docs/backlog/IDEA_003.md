---
id: IDEA_003
type: idea
title: A stand-down switch — one temporary, reversible way to hold every gate
status: proposed
residue: []
history: []
area: core/rules
severity: medium
priority: high
slug: stand-down-switch
---

**The want.** One switch that holds every gate, reversibly, without editing
project policy. Today the smallest expression of "stand down while I do
something unusual" is either a manifest edit the operator has to remember to
revert, or `INVARIANT_DISABLE` with all twenty-four rule ids spelled out by
hand.

**Why the existing routes do not cover it.**

* The `loose` profile is not off. It still blocks
  `session.no_edit_in_main_checkout` and `privacy.clean`, deliberately and
  correctly — it is an observation mode, not a stand-down.
* `INVARIANT_DISABLE` takes a comma-separated list of exact ids. There is no
  wildcard, no `all`, and a mistyped id is silently ignored
  ([BUG_050](BUG_050.md)).
* An environment variable does not reach a hook mid-session anyway: the hook
  subprocess inherits what the host was launched with.
* Editing `enforcement.rules` works and is permanent by construction. A
  temporary policy change that lives in the project's committed configuration
  is the wrong shape — it either gets committed by accident or gets forgotten
  and quietly becomes the project's real policy.

**Shape.** `invariant standdown --reason '<why>' [--until <event>]`, recorded on
the session record where `overrides` already lives, so it is visible in
`session status` and in the compliance report rather than being a silent hole.
Every rule reports `na("stood down — <reason>")` while it holds. It ends at the
end of the pipeline or the session, whichever the operator names, or at
`invariant standdown --clear`; the default should be the SHORTEST scope that is
useful, because a stand-down nobody remembers to lift is indistinguishable from
having uninstalled the tool.

`privacy.clean` deserves an explicit decision rather than a default: it is the
one gate whose failure cannot be undone after the fact, and this library has
already argued that case twice (both the `loose` profile and the README's
opinions section keep it blocking). Either hold it out of the stand-down, or
require it to be named — `--include privacy.clean` — so that going past it is
a separate sentence the operator wrote.

**Why this belongs to the founding position, not to convenience.** A tool that
cannot be told to stand down for one task gets uninstalled during that task, and
what returns afterwards is usually nothing. The README already argues the
adjacent half: *"a gate that fights legitimate exceptions gets the whole tool
switched off"*. A stand-down is the supported version of the thing an operator
would otherwise improvise, and improvised it leaves no record, while this
leaves one.

**Effort.** Small-to-moderate. The delivery seam exists (`Level.OFF` is already
honoured in `core/rules.evaluate` before the rule body runs) and the storage
seam exists (`Session.overrides`). The work is the scope/expiry semantics and
making the state loud in `build_context`, so that no turn passes without the
agent and the operator being told enforcement is currently held.

**Related.** [BUG_049](BUG_049.md) is the per-call case; this is the
per-interval one. They share a seam and could be built together, but they are
not the same want: one is "let this call through", the other is "stop asking
for the next while".

**Provenance.** Raised by a review of the implementation against the project's
founding position — the operator overrides temporarily or permanently, and only
the permanent half is currently expressible. Reported 2026-08-20.
