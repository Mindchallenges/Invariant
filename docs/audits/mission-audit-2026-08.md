# Mission audit — August 2026

An in-depth audit of whether the current state of `invariant` matches its
stated mission and vision, conducted 2026-08-14. Sources: the README, ADR-001,
ADR-002, the four architecture documents, the full source tree, the shipped
data (machines, profiles, agents), the hooks, the test suite (instrumented,
not just read), the backlog, and this repository's own state root and git
history — the library's dogfooding record.

The mission, as the README states it: replace prose-as-control-flow with
state a script owns, so that **non-compliance is recorded rather than
silent**, gates are **functions with fixtures proving they fail on a
known-positive input**, and the library **runs every control against
itself**.

## Verdict

The architecture is real and most of the engine is genuinely sound: the FSM
core, the manifest/profile/provenance system, level-as-delivery, the
coverage gate's treatment of "unanswerable vs clean", redaction-by-default,
and the session isolation machinery all do what the documents claim, and the
docs are unusually honest about past failures. This audit's own session was
blocked from editing the main checkout, forced into a worktree, and had its
architecture-doc reads attributed to the correct gate — the core loop works.

But the audit found the mission's three sharpest claims each fail somewhere
that matters:

1. **"Non-compliance is silent" is still true of the enforcer itself.** A
   family of fail-open paths lets enforcement silently switch off: a wedged
   state file, a missing manifest at the hook's working directory, a git
   query that errors, an unloadable machine, an uninstalled binary. Each
   reads as a pass. One of these (the wedged pipeline) was reproduced live
   during this audit and disabled every gate for the host session.
2. **"A bug cannot be fixed before it is reproduced" was bypassed seven
   times on the project's own backlog** — by hand-editing the item store,
   which is exempt from isolation, so the machine was never asked.
3. **"Gates are functions with fixtures proving they fail on a
   known-positive input" is false for 5 of 27 rules**, and several gates are
   structurally dead: named in machine YAML but evaluated by no code path.

None of this is fatal; nearly all of it is fixable with the tools the
library already has. The prioritized recommendations are at the end.

## Scorecard

| Mission claim | Observed state | Grade |
|---|---|---|
| Work advances only through a validated, recorded CLI transition | True for pipeline; session machine gates are skipped on the generic transition path (AUD-05) | Partial |
| Hooks enforce and inject current state, not a rulebook | True when healthy; multiple silent fail-open paths (AUD-01…04) | Partial |
| Compliance is an artifact you can read | Session records: yes. Pipelines: only 4 of 13 enforced merges had a completed pipeline (AUD-11) | Partial |
| Gates have known-positive failing fixtures | 22 of 27 rules: yes. 5 rules can never fail in the suite (AUD-14) | Partial |
| Docs-first: diagrams are the authoritative dependency map | Exercised for real; but the map itself has 5 high-severity inaccuracies incl. two dependency-rule violations in code (AUD-20, AUD-21) | Partial |
| No fix without reproduction (structural, not policy) | Structurally true in the machine; bypassed 7× via the exempt item store; one shipped agent instructs an illegal edge (AUD-10, AUD-12, AUD-30) | Failed on itself |
| Personal data never reaches a document | Redaction sound; but MultiEdit/NotebookEdit content is never scanned, and the index/reports/audit paths are out of scope (AUD-06, AUD-15) | Partial |
| Extensible: methodologies, adapters, backends, hosts | Interfaces real and shipped; per-task methodology override wedges the pipeline (AUD-02); node adapter cannot satisfy the coverage gate (AUD-17) | Partial |
| Dogfooded: "a control it will not run against itself…" | Session domain fully; memory domain not at all; telemetry empty; no lint control exists; no CI (AUD-40…46) | Partial |

## Part 1 — Critical: the enforcer fails open

The library's founding complaint is that prose compliance fails silently.
The enforcement layer has its own silent-failure family. Every entry below
is a path where "the gate could not run" is delivered as "the gate passed".

**AUD-01 (high) — A wedged state file disables all enforcement for the host
session.** Reproduced live during this audit: with a pipeline state the
manifest's machine does not recognize, every hook catches the error and
returns an empty verdict — the user-prompt-submit hook announced
"enforcement is OFF for this call" and pre-tool-use stopped checking gates.
BUG_001 (filed, open) describes exactly this class. Fail-open at the hook is
a deliberate deadlock-avoidance decision and the degraded marker exists —
but a wedged pipeline is persistent, not transient, so the result is a
project with zero enforcement until someone happens to run `doctor`.

**AUD-02 (high) — `pipeline start --methodology <id>` wedges the pipeline it
starts.** The override is persisted in the pipeline record
(`methodology: docs`) but `PipelineDomain.__init__`
(`domains/pipeline.py:125`) always loads the manifest-default machine; the
in-memory swap at `pipeline.py:193-199` lives only for the `start` call.
Every later invocation errors ("machine 'tdd' has no state 'drafting'"),
which wedges `status`, `gates`, `advance`, **and** — via AUD-01 — turns all
hook enforcement off. Reproduced live in this repository during this audit;
the workaround (temporarily editing `methodology.pipeline` in the manifest)
is itself an unobserved gate-set swap (AUD-03). Fix: resolve the machine
from the persisted `pipeline.methodology`, falling back to the manifest.

**AUD-03 (medium) — The whole gate set is switchable by one exempt manifest
line.** `methodology.pipeline` selects which gates exist; `.claude/**` is
exempt from session isolation; so editing the manifest mid-pipeline swaps
`tdd`'s test/coverage gates for `docs`' single privacy gate with no gate
observing the edit. Recording the methodology on the pipeline at `start`
(the AUD-02 fix) also closes most of this.

**AUD-04 (high) — Hooks resolve the manifest from `cwd`, not the project.**
`cmd_hook` builds its context from the process working directory; the shim
knows `CLAUDE_PROJECT_DIR` but never passes it through. A hook firing with
cwd outside the project silently disables every gate (no manifest found →
`{}`, no degraded marker). This is also the confirmed mechanism of the
cross-repository recording wedge this repo's manifest warns about: evidence
(`agents_run`, `docs_read`) records into whichever repo the cwd resolves
to — or nowhere — while the real pipeline's gates stay unsatisfiable
(BUG_010 residue, BUG_021).

**AUD-05 (high) — Session-machine gates are not evaluated on the generic
transition path.** `SessionDomain._advance` validates only the edge; entry
and exit gates in `session.yaml` are read by exactly two hardcoded call
sites (`session review`, `session merge`). Consequences, all verified:
`invariant session transition reviewed` then `merged` walks the lifecycle
with zero gates (skipping `pipeline.complete`, `session.ff_merge_target`,
`privacy.clean`); `session.named_after_isolating` is a dead gate no code
path evaluates (and therefore has no failing fixture); `session.push_enabled`
is duplicated as a hardcoded `if` instead of read from the machine. Fix:
evaluate gates inside `_advance`, as `PipelineDomain.advance` already does.

**AUD-06 (high) — `MultiEdit` and `NotebookEdit` bypass the privacy gate.**
`privacy.clean` reads only `tool_input.content`/`new_string`; both tools are
in `EDIT_TOOLS` (isolation rules fire) but their payload shapes
(`edits[].new_string`, `new_source`) are never scanned. Verified live: the
same personal data that blocks a `Write` passes through a `MultiEdit`
untouched.

**AUD-07 (high) — Two git helpers report "could not answer" as a clean
pass.** `git.staged_files` returns `[]` on git failure, so
`session.own_file_set_only` reports "0 staged file(s), all declared";
`git.in_linked_worktree` returns `False` on error, so
`session.ff_merge_target` — the gate guarding the self-merge whose whole
point is that "both commands exit zero" — reports "running from the main
checkout" on an unreadable repository. The codebase already has the right
pattern (`branch_files` et al. return `None` and every caller treats `None`
as "do not relax"); these two predate it.

**AUD-08 (medium) — `pipeline.complete` skips, non-blocking, when the
machine is unloadable.** The rule its own docstring calls "the gate that
makes every other pipeline gate mandatory" yields a non-blocking skip on a
mistyped `methodology.pipeline`, and `session.work_would_be_stranded` goes
silent the same way. A configuration error should fail, not step aside.
Related: `methodology.pipeline` is never validated by `Manifest.validate()`.

**AUD-09 (medium) — Missing-binary and missing-warn-delivery gaps.** An
uninstalled `invariant` yields `{}` from every shim forever, detectable only
by running `doctor` (recommend: the SessionStart shim should say
"enforcement is OFF" when the binary is absent). Warn-level pre-tool-use
verdicts are returned in a field the host may not render for that event; the
CLI's BUG_020 fix (surface a blocking gate that did not run) was never
applied to the hook delivery path, which is the one agents actually
experience. Commit-message scanning matches only exact `-m` forms —
`git commit -am` and `-F file` are not scanned — and evidence recording
failures inside the post-tool-use hook are swallowed without trace.

## Part 2 — The three policy opinions, applied to itself

**AUD-10 (high) — Seven bugs jumped `filed → fixed`.** BUG_002 through
BUG_008 each show `history: []`, `status: fixed`, and git confirms a
single-commit status flip with no `reproduced` in between — dated after the
bug machine and its blocking gate were already in place. The mechanism: the
item store is data, exempt from isolation (a deliberate, reasonable
decision — BUG_008), so a hand edit never asks the machine. The invariant is
structural in the FSM but the storage is not write-protected against
sincerity failures. Recommendation: a `stop`/`post-tool-use` consistency
rule (`item.history_matches_status`) that flags an item whose status is not
explained by its recorded history — drift detection, in the exact spirit of
the memory domain's index check.

**AUD-11 (high) — Only 4 of 13 enforced merges carried a completed
pipeline.** Four merged sessions had pipelines still in their *initial*
state; five had no pipeline record at all. `pipeline.complete` exists
precisely because (per its docstring) "two sessions merged and tore down
with their pipeline still in its initial state" — but it sits at `warn` in
the standard profile, and this repository's manifest, which raises two other
rules to `block` with written rationale, leaves this one silently advisory.
The flagship batch-fix commit (five bugs, one commit) is the clearest case:
its session record is pristine (`registered → … → torn_down`, no
overrides) while its pipeline never left `designing` — the compliance
artifact says the methodology was not followed, and nothing surfaced that.

**AUD-12 (high) — The five batch-fixed bugs were never red in a committed
state.** BUG_017/019/020/022/024 went `filed → fixed` in git in one commit,
with their `reproduced` history entries and named repro tests written
retroactively inside the fix commit. The named tests do exist and pass —
the proofs are real — but contrast BUG_009–BUG_016, which each carry a
standalone failing-repro commit before the fix. Reproduction is asserted,
not recorded. A batch fix is compatible with the lifecycle only if each
entry gets its own red commit first.

**AUD-13 (high) — The one gate this repo raises to `block` is unsatisfiable
by ordering (BUG_021, open).** `agents.design_agent_ran` is an *exit* gate
on the design state, satisfied by evidence the post-tool-use hook records
when the design agent's dispatch *returns* — but the shipped workflow has
the design agent itself advance the pipeline from inside. At that moment its
own dispatch is not yet recorded, so the gate blocks the very agent it
names. Three of the five forced overrides in this repo's recorded history
are this gate; this audit added a fourth. The shipped profiles keep it
advisory (correctly); the fix is to record the dispatch at pre-tool-use
time (or on the subagent's session start), and it applies equally to
`agents.review_agent_ran`, which wedges the compliance-reviewer the same
way. A completed fix for BUG_021 exists — uncommitted, in a stranded
worktree (AUD-40).

**AUD-14 (high) — Five rules have no known-positive failing fixture.**
Instrumenting all 1,110 tests: the four `memory.*` rules and
`session.named_after_isolating` never produce a failing verdict anywhere —
only skips. For `named_after_isolating` this is structural (AUD-05: no code
path evaluates it); for `memory.*` the rules are bound to no event and no
machine gate, and short-circuit to "not applicable" when `memory.managed`
is false — which it is, here (AUD-41). The README's fixtures claim needs
these five closed or withdrawn.

**AUD-15 (medium) — Privacy scope misses the second copies.** Scope
prefixes are built from `paths.docs` and `paths.memory` only. The generated
memory index (`paths.memory_index` — a concentrate of exactly the text the
memory scope protects), the committed telemetry report (`paths.reports`),
and everything in `docs/` outside `docs/architecture` — including this
audit — are not scanned. Also: the `device_ids` pattern is implemented but
absent from the default set (while the `docs` machine's own comment
advertises it), and a bare `PrivacyScanner()` enables all seven patterns
while the manifest path enables six — two construction paths that disagree.

**AUD-16 (low) — Privacy detection gaps.** Line-based scanning can never
match a multi-line PEM private-key block; the token pattern misses several
common credential shapes; `allow` entries match by substring, so one short
entry can suppress most findings; the clock-time false-alarm heuristic
drops real timestamps on lines containing words like "version".

## Part 3 — Enforcement machinery (engine-level)

**AUD-17 (medium) — The coverage gate is unsatisfiable for ecosystems
without a parser.** The node adapter inherits the base `parse_coverage`
returning `{}` (BUG_018, open, high) — under `strict`, a Node project can
only ever advance with `--force`, the exact dynamic BUG_019 was fixed to
remove for Python.

**AUD-18 (medium) — Test/coverage evidence never expires.** Recorded runs
carry no commit hash and no dirty-tree flag; measure green, edit, advance —
the gate answers about code that no longer exists.

**AUD-19 (medium) — There is still no lint control.** The celebrated lint
fix (declare ruff as a locked dev dependency) improved the *command string*;
no rule reads lint output, no code path runs it, and nothing gates on it.
Consistently: master currently carries 126 ruff findings — including 22
`subprocess.run` calls without `check` and two blind excepts — one commit
after the lint-gate commit. An unrunnable gate was replaced by a runnable
one that nothing runs. Recommend `invariant pipeline lint` (mirroring
`pipeline coverage`) plus a `lint.clean` rule wired into review states.

Also at engine level: a rule id absent from a profile silently defaults to
`warn` (a new rule ships advisory unless every profile is touched; nothing
tests code→profile completeness in that direction); `test.suite_runs` is
registered, graded in all three profiles, and named by no machine and no
event — enforcement levels are being reported for a rule that never runs;
`item.residue_matches_status` sits as an *exit* gate on the idea machine's
terminal `done` state, where it can never fire (an idea can close with
unresolved residue silently — the bug machine gets this right);
`coverage_scope` values are unvalidated (a typo silently means
project-wide); `_is_within` is defined twice in `rules.py`; and several
BLOCK-verdict remediation texts name commands that do not exist
(`invariant session abandon`) or misdescribe what they do (`advance` does
not run the suite; `pipeline test` does).

## Part 4 — Agents and hooks

Shipped agent files and installed copies are byte-identical (digest
machinery works; zero drift). But the prompts contradict the machines they
drive:

**AUD-30 (high) — `bug-reproducer` instructs an illegal transition.** Its
ALREADY-FIXED verdict routes to `item transition <id> fixed` — from `filed`,
an edge the bug machine deliberately does not have and its gate blocks. The
`backlog-curator` prompt correctly warns about this exact edge; two shipped
agents disagree about the flagship invariant.

**AUD-31 (high) — `compliance-reviewer`'s happy path dead-ends.** Its merge
phase runs `session merge` directly from `working`/`isolated`; the machine
requires passing through `reviewed` (via `session review`), which the
prompt never mentions. As written, every run must improvise at the exact
step the pipeline exists to script.

**AUD-32 (medium) — No agent records the evidence the test and coverage
gates read.** `pipeline test` and `pipeline coverage` appear in no agent
prompt; `advance` does not run suites; so the TDD sequence the
feature-architect is told to follow hits `test.new_test_fails` with zero
recorded runs. Also: both build agents unconditionally require reading a
"canonical compliance checklist" document that this repository does not
have (and reference a release-plan/dashboard and a `CLAUDE.md` that do not
exist here); the curator's `MARK IDEA done` shortcut is illegal from any
state but `in_progress`; and iOS/Swift-specific residue (device soaks,
gate-receipt paths) survives in agents shipped to every project.

Hook wiring itself is byte-exact against the generator, all six shims run,
and the SessionStart injection genuinely delivers "current state instead of
a static rulebook". The fail-open family is Part 1's AUD-01/04/09. One more
telemetry seam: the transcript reader counts a subagent launch only when
the tool block is named `Agent`, while the hook path accepts `Task` too —
`Task`-dispatched subagents are invisible to the stats agents view, and the
telemetry tests fixture only the name that works.

## Part 5 — Documentation accuracy

34 findings; the five high-severity ones:

- **`core/memory` does not exist.** The overview diagram, an arrow, and a
  responsibilities-table row all describe a module that was never written;
  everything it lists lives in `domains/memorystore`.
- **`core/` imports `domains/`** — `rules.py` imports `MemoryStore` from
  `domains.memorystore` (the comment even names the cycle it dodges),
  violating the stated "arrows never point back up" rule; the arrow is
  absent from the diagram.
- **`hosts/` does not reach the engine only through the CLI** — the host
  imports and calls `core.rules.evaluate_event` in-process.
- **`CC --> CLI` is backwards** as a module dependency; the CLI imports the
  host, not vice versa (the shim→CLI call is runtime flow, not an import).
- **Three documented CLI commands do not exist**: `memory dedup`,
  `memory link-check`, `state gc`.

The medium/low set (29 more): the manifest doc's "Full schema" shows five
defaults that disagree with code (`paths.state`, `docs.index`,
`items.path`, plus the never-parsed `paths.root`) and omits two load-bearing
keys (`vars`, `discover` — without which no Swift project can run at all);
the overview's at-rest tree shows the state root at a path the product no
longer uses (the real root is the git common dir — `state_model.md` has it
right, and this repo's own `.gitignore` still ignores the dead path); the
`tdd` example in `state_model.md` differs from the shipped `tdd.yaml` in
three ways; docs-first injects three gates, not "a pair"; the session and
bug lifecycle diagrams omit real states and edges (`wont_fix` entirely, and
the default `merged → torn_down` path — the diagram shows only the
exceptional push route); the session-invariants table omits the blocking
`session.work_would_be_stranded`; "the repro-proving agent is the only
actor allowed to make it" is stated but unenforced (prose-as-control-flow,
in the document defining the alternative); the README's governs-table and
methodology list omit the stats domain and the `docs` methodology, and
never links ADR-002; ADR-001 lacks an "amended by ADR-002" pointer; and a
dozen CLI flags/commands are undocumented (notably `session review` also
*advances* state, and `privacy scan --reveal` inverts the README's
redaction promise without being documented where that promise is made).
`cli.md` also files `pipeline coverage`/`ack` inside the Session block and
omits the sixth hook event (`stop`).

## Part 6 — Dogfooding gaps

**AUD-40 (high, operational) — A stranded worktree holds real unmerged
work.** Session `bb79e7a3` sits `isolated`, its pipeline at `reviewing`,
its worktree one commit behind master with 26 modified files — including
**ADR-003** (exists nowhere on master), a completed **BUG_021 fix** with a
corrected root cause, and the `commands.test/coverage` manifest repair for
a command-not-found failure that master still has (`pytest` recorded as
exit 127 — command-not-found rendered as a red suite — the exact
"unrunnable control" shape the lint incident documented). Two sessions
solved the same five bugs in parallel; the other one landed.
`session.work_would_be_stranded` is `block` and did not prevent it. This
needs a human decision: salvage (rebase and merge the ADR and the fixes) or
consciously abandon. There is also one stale `session/host-attribution`
branch with no registry entry.

**AUD-41 (medium) — The memory domain is not run at all.** One of the four
domains the README advertises: `memory.managed: false`, no memory
directory, no index, four rules permanently short-circuited (hence AUD-14),
and a privacy scope entry pointing at a nonexistent tree. Either dogfood it
(the project has real remembering-shaped needs: per-repo conventions,
lessons like the `BUG_BUG_001` prefix mistake) or the README should stop
counting it as exercised.

**AUD-42 (medium) — Telemetry has recorded essentially nothing here.**
Across 12 pipeline records: 37 transitions, zero carrying usage watermarks;
one stats record, unjoined (null session); six host sessions never
ingested; no rendered report at `paths.reports`. The diagnosed cause
(BUG_017) was fixed the same day as this audit, so this is explainable —
but ADR-002's feature has yet to produce a single per-state attribution in
its home repo. Run `stats refresh` and `stats report --write` post-fix and
check the asymmetry note holds.

**AUD-43 (medium) — Doctor is failing and idea-flow is unused.**
`commands.build` unset is a standing doctor FAIL (set it, or teach doctor
that a pure-library project legitimately has none); `invariant item new
idea` has never been used — the backlog is bugs-only, so the idea machine
(which has its own gate bug, Part 3) has zero self-exercise; `commands/`
and `skills/` asset classes ship empty and unexercised.

**AUD-44 (medium) — No CI.** Nothing runs the tests, the lint, or `doctor`
on push. For a library whose thesis is that unverified discipline decays,
the absence of any repository-level verification is the largest
uncontroversial gap. A minimal workflow running `uv run pytest`,
`uv run ruff check .`, `invariant doctor`, and a Sphinx build would also
have caught the 126 lint findings (AUD-19) and the docs drift (Part 5) at
the commit that introduced them.

**AUD-45 (low) — Repo hygiene.** The default session base branch is
hardcoded `master` (this repo happens to use master; any `main` repo
misfires silently — undocumented, see Part 5); `.gitignore` carries the
dead state-root path; `__main__.py` is uncovered (0%); BUG_016 is
done-but-open by its own residue accounting.

## Part 7 — What is demonstrably sound

Balance matters; these claims were verified true, not assumed:

- **The FSM core**: structural validation (unknown successors, unreachable
  states, dead ends), gate-name integrity across all nine machines,
  enforced by tests. The bug machine's `filed → reproduced → fixed`
  invariant is genuinely structural.
- **Level-as-delivery**: rules never see their level; `off` short-circuits;
  `ack` refuses blocking rules; block and warn cannot collapse. Profiles
  cover the registry exactly, three-for-three.
- **Provenance**: `config show --provenance` labels all five layers
  correctly (verified against the live manifest).
- **The coverage gate's epistemics** — empty, no-overlap, and
  partial-overlap all fail rather than passing vacuously; every relaxation
  is evidence-backed and named. The strongest code in the tree.
- **Privacy redaction** is default-on at every rendering point.
- **Session isolation in practice**: since hooks were installed, every
  `src/` commit reached master as a fast-forward merge from a session
  branch; 15 of 18 sessions ran the full lifecycle with zero overrides;
  this audit was itself correctly blocked, isolated, and attributed.
- **Asset management**: shipped agents/hooks/settings are byte-identical
  with digest-tracked currency; `doctor` executes every shim with a
  non-blocking probe.
- **Telemetry design discipline**: watermarks not timestamps, content
  fingerprints not mtimes, floors labelled as floors, unpriceable models
  poison the total instead of shrinking it.
- **The manifest's honesty**: every self-exemption carries written
  rationale — the audit's job was largely checking whether the code keeps
  the promises the comments make.
- **Bug lifecycle from BUG_009 onward** (until the batch commit): repro
  commit, machine-recorded history, honest residue.

## Part 8 — Recommendations

Priority 0 — close the fail-open family (the mission depends on it):

1. Resolve hook manifests from `CLAUDE_PROJECT_DIR`, fall back to cwd;
   mark the no-manifest response degraded (AUD-04).
2. Persist-and-honor the pipeline's own methodology (AUD-02/03).
3. Evaluate session-machine gates in `_advance`; delete the hardcoded
   per-command gate lists (AUD-05).
4. Return `None` (not `[]`/`False`) from `staged_files` and
   `in_linked_worktree` on git failure; treat `None` as "do not relax"
   (AUD-07). Make `pipeline.complete` fail on an unloadable machine and
   validate `methodology.pipeline` at manifest load (AUD-08).
5. Scan `MultiEdit`/`NotebookEdit` payloads and `-am`/`-F` commit forms in
   `privacy.clean` (AUD-06); add memory-index and reports paths to privacy
   scope (AUD-15).

Priority 1 — make the record match the discipline:

6. Salvage or consciously abandon the stranded `bb79e7a3` worktree — it
   holds ADR-003 and the BUG_021 fix (AUD-40). Human decision required.
7. Record agent dispatches at pre-tool-use time so the role gates are
   satisfiable by the agents they name (AUD-13 / BUG_021); fix the three
   agent-prompt contradictions (AUD-30/31/32).
8. Raise `pipeline.complete` to `block` in this repo's manifest — with the
   same written rationale the other overrides carry (AUD-11).
9. Add an `item.history_matches_status` drift check so a hand-edited item
   store is at least loudly inconsistent (AUD-10); move the idea machine's
   residue gate to `done`'s entry (Part 3).
10. Add the five missing known-positive fixtures, wire or retire the five
    orphaned rules, and make an absent profile entry loud (AUD-14, Part 3).

Priority 2 — closing the loop on the library's own claims:

11. Stand up CI: pytest, ruff, `invariant doctor`, sphinx build (AUD-44);
    burn down the 126 lint findings and add the missing lint control
    (`pipeline lint` + `lint.clean`) (AUD-19).
12. Correct the five high docs findings (Part 5) — the dependency diagram
    should either match the imports or name its two acknowledged
    exceptions — then sweep the schema/default mismatches.
13. Dogfood the memory domain, ingest telemetry (`stats refresh`,
    `stats report --write`), set or excuse `commands.build`, and use the
    idea lifecycle for the feature backlog (AUD-41/42/43).
14. Ship a node `parse_coverage` or make the gate honestly `na` for
    parser-less ecosystems (AUD-17); stamp test/coverage evidence with the
    tree state it measured (AUD-18).

## Appendix — Backlog inventory at audit time

24 items, all bugs. Open: BUG_001 (high — hook fails open), BUG_016 (low —
telemetry follow-ups; done-but-open), BUG_018 (high — node coverage
parser), BUG_021 (high — role gate unsatisfiable; fix stranded in
`bb79e7a3`), BUG_023 (low — host id reaches pipeline by two routes).
Fixed: 19, of which BUG_002–BUG_008 bypassed `reproduced` (AUD-10) and
BUG_017/019/020/022/024 were batch-fixed without committed red states
(AUD-12). BUG_009 and BUG_010 carry honest unfixed residue.

---

*Method note: four parallel read-only audit agents (documentation accuracy;
enforcement machinery — with the rule registry instrumented across the full
1,110-test suite; agents/hooks/machines; dogfooding and backlog state),
plus direct verification: full test run with coverage (93% total), ruff,
`doctor`, privacy scans, live hook behavior, and a live reproduction of
AUD-02. Facts were cross-checked against the running system, not only the
sources.*
