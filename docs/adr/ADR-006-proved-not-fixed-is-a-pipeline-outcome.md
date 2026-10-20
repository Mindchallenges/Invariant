# ADR-006 — "Proved, not fixed" is a pipeline outcome, and a strict expected failure is red

- **Status:** Accepted
- **Date:** 2026-10-20
- **Supersedes:** nothing. Adds a terminal to the shipped `bugfix` machine
  beside the landing state ADR-003 places its gates on, and changes what the
  red-phase recorder writes into `test_runs`.

## Context

The `bug-reproducer` role proves a filed bug real and stops. It must commit its
red test wrapped in a strict expected-failure marker, so that the suite stays
green while the defect is live and turns red the moment anyone fixes it. The
work-item machine models that outcome (`filed → reproduced`); neither pipeline
machine did (BUG_066):

- From `bugfix`'s `reproducing`, every terminal reachable without fixing —
  `not_reproducible`, `abandoned` — is a false statement about a proof that
  fired. `pipeline.complete`, the entry gate of the session's `reviewed` and
  `merged` states, therefore refused a repro-only session, and its proof could
  not reach the base branch until its fix did. Proofs were stranded on
  unmerged session branches.
- The proof itself recorded as a PASS. pytest prints a strict expected failure
  that fails as `1 xfailed` and exits 0, so `test.new_test_fails` read "the run
  recorded in the reproducing phase passed" — satisfiable only once the fixer
  removed the wrapper.

The user decided on 2026-10-20 to fix both halves. This record holds the
decisions the implementation needed that the decision did not reach.

## Decisions recorded

### D1 — `reproduced` is a `bugfix` terminal, reached from `reproducing` only

`reproducing → reproduced` is a new edge, and `reproduced` is terminal. It is
not reachable from `fixing` or `verifying`: a pipeline that started fixing has
claimed more than a proof, and its honest exits are `committed` or
`abandoned`. `pipeline.complete` needed no change — it accepts every terminal
except the abandon state — so the session's review and merge boundary accepts
a repro-only pipeline by construction.

The `tdd` machine is not given a counterpart. A repro-only session is the
`bugfix` methodology's job (its `reproducing` state has stopping authority);
`tdd`'s `red` is the first half of a change that is going to be made.

### D2 — Its price is the proof and a clean privacy scan, not the landing role

`reproduced` carries `entry_gates: [test.new_test_fails, privacy.clean]` and
**no `landing` role**. That is the decision with ADR-003's boundary in it.

The landing role injects `docs.updated`, `docs.diagrams_current` and both
agent-role gates. None of them is a claim this outcome makes. A test-only
commit cannot honestly update an architecture diagram, so `docs.updated` would
be a gate no correct proof can clear — the BUG_037 / BUG_064 shape. The agent
gates ask whether a design and a review happened, and in a repro-only session
neither did: the reproducer is the actor. ADR-003 D1 stays true of the state it
was written for; it simply does not apply to an outcome that lands no change
to production code.

`privacy.clean` is carried explicitly because the proof does land on the base
branch, and the session machine's own `privacy.clean` is not a reason to omit
it here — every landing path in the shipped machines asks it at the pipeline
too. `test.new_test_fails` is the outcome's whole claim. Both resolve their
level from the manifest as every gate does.

### D3 — The follow-up fixer starts a fresh pipeline and records red against the proof on the base branch

`reproduced` is terminal, so `pipeline start` permits a fresh pipeline in the
same session or a later one. The fixer runs `triaging → reproducing`, records
red, and advances to `fixing`, exactly as before. Two recordings satisfy
`test.new_test_fails` there: the wrapper deleted first (a plain counted
failure, the order `feature-architect.md` prescribes), or the wrapper still on
under D4. Nothing carries the reproducer's evidence across pipelines; a proof
is re-run, not inherited, because the base branch may have moved under it.

### D4 — In the red phase, and only there, a strict expected failure that fails is a counted failure

The pipeline domain's recorder, not the rule, takes the reading, because
`_why_not_proof` asks for a parsed non-zero `failed` count and flipping
`passed` alone would not supply one (BUG_029). Conditions, all required:

1. The run is recorded in the phase the pipeline's machine names as its
   `red_phase` (`reproducing` for `bugfix`, `red` for `tdd`).
2. It exited clean with no failure counted, and the adapter counted one or more
   expected failures (`TestResult.expected_failures`, pytest's `N xfailed`).
3. It named a `--selector`. A whole-suite run cannot say which test xfailed, and
   every other committed repro in a suite is a strict expected failure too.
4. The adapter's `expected_failures_are_strict(root, selectors)` answers
   `True`.

The record then carries `passed: false`, `failed: <expected failures>`,
`read_as_red: true` and a note; `exit_code` stays the process's own. In every
other phase the exit status stands, so a green run carrying a committed repro
does not block `test.all_pass`: there, a strict expected failure still failing
is the tripwire holding. When conditions 1–2 hold and 3 or 4 does not, the
note says why, and `test.new_test_fails` quotes it.

### D5 — Strictness is read from the source, narrowly, because pytest's output cannot say

A strict and a non-strict `xfail` that fail are indistinguishable in pytest's
output — the same `x`, the same `XFAIL` line, the same `N xfailed`; strictness
shows only when the test passes. Three ways to observe it were weighed:

- **A pytest plugin injected through `PYTEST_PLUGINS` and `PYTHONPATH`.**
  Rejected: a configured command that sets its own `PYTHONPATH`
  (`PYTHONPATH=src pytest …` is common) would leave the plugin unimportable
  while `PYTEST_PLUGINS` still named it, and pytest refuses to start — every
  red run broken by the instrument meant to read it.
- **`--runxfail` appended to the command.** Rejected: `commands.test_one` is a
  project template that need not end in pytest, and the flag treats strict and
  non-strict markers alike, which is the distinction wanted.
- **The selected files' source.** Taken. The Python adapter parses the selected
  `.py` files (a node id is read through its file) and answers `True` only if
  every expected-failure marker in them is a `mark.xfail(...)` call with a
  literal `strict=True`. `strict=False`, no keyword (the ini `xfail_strict`
  default is not read), a non-literal, an uncalled mark and the imperative
  `pytest.xfail()` answer `False`; a selector that is not a Python file, an
  unparseable file, or files with no marker answer `None`.

The asymmetry is deliberate: reading a loose marker as strict manufactures
proof, which must never happen; reading a strict one as unknown costs the
reproducer one red run with the wrapper off.

### D6 — Other adapters say "cannot tell"

The base adapter, `node` and `swift_xcode` answer `None` and count no expected
failures. Jest's `test.failing` and XCTest's `XCTExpectFailure` have strict
semantics of their own, but neither adapter parses them, and an answer would be
a guess. In those ecosystems a reproducer records the red run with the wrapper
off, then wraps and commits. Teaching them is additive: count the expected
failures in `parse_test_output` and answer `expected_failures_are_strict`.

## Consequences

- A repro-only session ends `reproducing → reproduced`, passes the review
  boundary and merges its proof; the work item is `reproduced` and the test is
  on the base branch where the fixer will look for it.
- `test_runs` records gain `expected_failures` and `read_as_red`. A record with
  `read_as_red: true` is the one case where `passed` is not the exit status.
- A Python reproducer who writes `xfail` without `strict=True` is told so by the
  red-phase note rather than by a bare "the run passed".
