# Authority audit — August 2026

An audit of whether the current implementation of `invariant` honours the
position the library is built on: **the engine exists to make an agent
deterministic, and it must never take authority away from the operator, who
stays free to override any rule — temporarily or permanently.** Conducted
2026-08-23 against the source tree at `a01077b`.

Sources: the README's opinions section, ADR-001, the four architecture
documents, the full source tree (`core/`, `domains/`, `hosts/`, `cli/`), the
shipped profiles and machines, and the manifest this repository runs against
itself. Two findings were verified by executing the code rather than reading
it; the rest are source-confirmed.

This audit is narrow on purpose. It asks one question — *who decides* — and
says nothing about whether the gates are correct, which is
[the mission audit](mission-audit-2026-08.md)'s subject.

## Resolution

All six recommendations were carried out in the session that followed this
audit, against `a01077b`. This section records what landed; the findings below
are left as written, because an audit rewritten to match its own fixes stops
being evidence of what was true.

| Finding | Landed as | Shape of the fix |
|---|---|---|
| AUTH-01 | `Level.ASK` + `invariant override` | `ask` delivers to the human at `pre-tool-use` and stops work everywhere else; a granted token passes one call at any hook and appends to `session.overrides`. `acknowledge`'s misdirecting refusal and the acknowledgement's blindness to hooks were fixed in the same change |
| AUTH-02 | refusal at manifest load | an `enforcement.rules` key naming no rule is refused with the nearest real id; `INVARIANT_DISABLE` warns and proceeds; `doctor` survives an unloadable manifest |
| AUTH-03 | `classify_installed` + `--overwrite-local` | the classifier is lifted out of `_stale_for` and read by every install; a locally edited asset is skipped and named; `--dry-run` enumerates |
| AUTH-04 | `invariant standdown` | every gate held for a derived interval, `privacy.clean` held out unless named, announced on every turn |
| AUTH-05 | `invariant uninstall` | removes only what it wrote, never the operator's data, and `doctor` stops offering the repair that would undo it |
| Rec. 5 | ADR-001 **D25** | the position stated as a decision, with the README's "You are the authority" table as its public face |

The scorecard above the fixes read four Pass, three Failed, two Missing. It now
reads Pass throughout, and D25 is the row a future feature is checked against —
the question being not "is this gate too strict" but "what does the operator
type to proceed anyway, and where does that decision get written down".

## Verdict

**The override machinery is complete on the surface reached through the CLI
and absent on the surface that actually stops the operator.**

Every gate evaluated at a transition can be passed deliberately with
`--force --reason`, and the decision is appended to the record rather than
swallowed. Structural FSM edges are forcible too, so no lifecycle claim is
actually a cage — including the `filed -> fixed` edge the README describes as
absent. Every rule level is reconfigurable, no profile pins anything, and a
broken hook fails **open** with a loud marker, on the explicit reasoning that
"this library breaking must never be spelled 'the agent may not act'". The
doctrine is written down: *"Every gate needs an escape hatch for the case it
gets wrong."*

What that doctrine does not cover is the delivery surface. Twelve rules are
evaluated by hooks rather than by transitions, and for those a blocking verdict
is final: no flag, no marker, no prompt, no recorded exception. Alongside it sit
two smaller defects with the same shape — an override that is silently
discarded, and an operator edit that is silently reverted — and two capabilities
whose absence leaves the "temporarily" half of the position unexpressible.

## Scorecard

| Property the position implies | Observed state | Grade |
|---|---|---|
| Any rule can be relaxed permanently, per project | True. Profiles pin nothing; `enforcement.rules` overrides every level | Pass |
| Any gate can be passed deliberately, once, with the decision recorded | True at every CLI transition; false at every hook (AUTH-01) | Failed |
| An override the operator writes either takes effect or says it did not | False — an unknown rule id is accepted in silence (AUTH-02) | Failed |
| The operator can stand the engine down for one task | Not expressible; the smallest unit is a permanent policy edit (AUTH-04) | Missing |
| The operator can stop using the tool | No inverse of `init`; withdrawal is hand surgery (AUTH-05) | Missing |
| The operator's own files are the operator's | False for installed assets — reverted without asking (AUTH-03) | Failed |
| The engine reports rather than restricts where it can | True; context injection is a projection of state, and empty when silent (AUTH-06) | Pass |
| A broken engine loses enforcement rather than seizing control | True, deliberately, and documented (AUTH-07) | Pass |

## Part 1 — Where authority is taken

**AUTH-01 (high) — A blocking hook verdict cannot be overridden in band, and
never asks the operator.** Filed as [BUG_049](../backlog/BUG_049.md).

Seven rules evaluate at `pre-tool-use` and five at `stop`. A blocking verdict
there returns `permissionDecision: "deny"` or `decision: "block"` and there is
no input to that call — argument, marker, environment, prompt — that turns it
into a deliberate pass. The only relief is a manifest edit, which is a permanent
change to project policy made mid-task to get past a single call, or
`INVARIANT_DISABLE`, which a running host cannot pick up because the hook
subprocess inherits the environment the host was launched with.

Three details make it worse than a missing feature:

- `PipelineDomain.acknowledge` refuses a block-level rule and directs the caller
  to `pipeline advance --force --reason`. For a rule that fired on a *tool call*
  there is no transition to force, so the one message that discusses overriding
  points somewhere that cannot help.
- Acknowledgement never reaches the hook path at all. `acknowledged` evidence is
  read only in `PipelineDomain.evaluate_gates`; hooks call `evaluate_event` in
  `core/rules`, which does not consult it. Even a *warn*-level hook rule cannot
  be acknowledged into silence.
- The host's `PreToolUse` contract carries an `ask` decision alongside `allow`
  and `deny`, which routes the call to the human with the reason attached. This
  library only ever emits `deny`. The human is never offered the choice, and
  `Level` — already defined as "how a verdict is delivered, not a property of
  the rule's logic" — is the seam where that choice belongs.

**AUTH-02 (medium) — An enforcement override naming an unknown rule is accepted
and silently discarded.** Filed as [BUG_050](../backlog/BUG_050.md).

Verified by execution. `Manifest.validate()` iterates `enforcement.rules` and
parses each *value*; the *key* is never checked against the registry, and
`rule_level()` returns `Level.WARN` for anything absent. So `privacy_clean: off`
written beside a real `privacy.clean` loads without error, leaves the real rule
at BLOCK, and shows a provenance entry for the typo that reads as though the
override took. `doctor` is green; `rules list` shows only real rules, so the
rejected key appears nowhere. `INVARIANT_DISABLE` behaves identically —
`"privacy.klean,docs.read_architecture"` disables the second and ignores the
first without a word.

The library already holds the correct instinct twice: `core/rules.evaluate`
fails loudly on an id with no registered rule, *"A gate naming a rule that does
not exist would otherwise pass silently forever"*, and AUD-08 added exactly this
check for a mistyped methodology. Neither was extended to the ids an operator
uses to exercise authority — which is the one place where a silent no-op costs
the most.

**AUTH-03 (medium) — `assets install` destroys the local edits it has already
identified as local edits.** Filed as [BUG_051](../backlog/BUG_051.md).

`ClaudeCodeHost._stale_for` distinguishes `(edited locally)` from
`(installed from an older version)` — it knows which divergences are the
operator's own work. `install_assets` consults none of that and overwrites every
target unconditionally: no `--force`, no per-asset skip, no backup, and a
`--dry-run` that names nothing it would flatten. `doctor` reports a locally
edited agent as a **failed** check, so the repair the tool recommends is the
destructive one.

## Part 2 — Where authority is unexpressible

**AUTH-04 (medium) — There is no stand-down switch.** Filed as
[IDEA_003](../backlog/IDEA_003.md).

The position has two halves and only the permanent one is implementable today.
`loose` is not off — it still blocks `session.no_edit_in_main_checkout` and
`privacy.clean`, correctly, because it is an observation mode. `INVARIANT_DISABLE`
takes exact ids with no wildcard and no `all`, and does not reach a running
host. What is left is editing committed project policy for the duration of one
task and remembering to revert it — which either lands in a commit by accident
or quietly becomes the project's real policy.

The README already argues the adjacent half: *"a gate that fights legitimate
exceptions gets the whole tool switched off."* A stand-down is the supported,
recorded version of what an operator improvises instead.

**AUTH-05 (low) — There is no uninstall.** Filed as
[IDEA_004](../backlog/IDEA_004.md).

Adoption is one command. Withdrawal means deleting each shim by hand and
hand-editing the host settings file that `_wire_settings` merged its entries
into. A missed entry still fires; the shim then finds no binary and exits
silently by design, so the leftover is invisible. `doctor` reports a
half-removal as broken and offers `hooks install`, pushing back toward the state
the operator was leaving. The right to stop using a tool is part of authority
over one's own project, and it is currently the hardest thing to do.

## Part 3 — Checked and sound

**AUTH-06 — Context injection reports, it does not restrict.** `build_context`
runs on `SessionStart` and every `UserPromptSubmit` with no manifest key gating
it, which is worth knowing; but it is a projection of recorded state, returns
empty when there is nothing to say, and constrains nothing. Judged compliant and
deliberately not filed. Revisit if it ever grows directives.

**AUTH-07 — A broken engine loses enforcement rather than seizing control.**
The hook entry point catches every exception, prints a degraded response with a
machine-readable marker, and exits zero, because a `pre-tool-use` hook that
fails is a deny and would refuse the very calls that would repair it. This is
the position applied under failure, and it is the strongest evidence the
principle is real in this codebase rather than stated.

**AUTH-08 — Every transition gate is passable and every pass is recorded.**
`session transition`, `pipeline advance` and `item transition` all take
`--force --reason`, refuse a force with no stated reason, and append an override
naming the gates it went past. Structural edges the machine does not define are
forcible on the same flag and recorded as `structural`. Nothing here needs to
change; it is the standard the rest of this audit measures against.

## Recommendations, in order

1. **AUTH-01 first**, because it is the only finding where the operator is
   stopped with no way through. The `ask` delivery level and a one-shot override
   token are not alternatives — `ask` puts the human in the loop at
   `pre-tool-use`, and the token is what covers `stop` rules and leaves a
   record. Fix the misdirecting `acknowledge` message in the same change.
2. **AUTH-02 next**, and it is small: refuse an unknown rule id at manifest load
   naming the nearest real one, warn loudly for `INVARIANT_DISABLE` rather than
   refusing (an escape hatch reached in a hurry must not be wedged by a typo),
   and add the same check to `doctor` so existing projects hear about it.
3. **AUTH-04 and AUTH-01 share a seam** and could be built together, but they
   are different wants — "let this call through" and "stop asking for a while".
   Build the per-call one first; a stand-down is easier to justify once the
   narrow case exists.
4. **AUTH-03 and AUTH-05 are independent and cheap.** Both are inverse
   operations the install side already has the identity test for.
5. **State the position in ADR-001.** It is currently visible only as behaviour
   — `--force --reason` everywhere, fail-open hooks, `"Any of these can be
   dropped to warn or off"` — and one sentence of doctrine in `manifest.md`
   about a single gate. Written down as a decision, it becomes the thing the
   next feature is checked against, and AUTH-01 would not have shipped.
