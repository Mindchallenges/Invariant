# ADR-010 — Whole-suite and whole-scheme evidence move to a scheduled sweep

- **Status:** Accepted, partly implemented. The user ruled on all eleven
  decisions on 2026-10-26 ("Rulings" below), overriding two recommendations:
  sweep records are a committed store (D9), and the pre-merge full-run
  allowlist ships in v1 (D8). IDEA_032 builds it in three increments (see
  "Increments"). Increment 2 delivers D1, D2, D3 (all but item 5), D5, D8 and
  the run-seam extraction. Increment 3 delivers D4, D7 and D9 (`core/sweep`,
  `domains/sweep`, `invariant sweep run|status|show`, `paths.sweeps`,
  `failing_test_ids`); D6 (`sweep.fresh`), D3 item 5, the `doctor` row, the
  last sweep's figure in the advisory verdict and the agents' `--selector`
  guidance follow.
- **Date:** 2026-10-26
- **Supersedes:** nothing. Generalises the per-file advisory/held split
  IDEA_028 introduced for `methodology.gates.coverage_advisory_paths`, adds a
  state domain beside `stats` and `memorystore`, a declared committed store,
  and two rules. Settles IDEA_032's open questions.

## Context

A consuming project ruled on 2026-10-26 that a change never runs the whole
test suite or whole-scheme coverage, in any case. The full suite plus coverage
runs once a week, and every new red or floor drop becomes a follow-up work
item (IDEA_032).

The engine cannot express that today, for three reasons:

1. **Per-change coverage evidence is always whole-scheme.** `pipeline
   coverage` takes no selector; it runs `commands.coverage` as configured. A
   project whose floor is `block` with `coverage_scope: touched` therefore pays
   a whole-scheme run on every change that touches shared code, 45 minutes or
   more in that project.
2. **A scoped figure is a lower bound, not a measurement.** In the same project
   one file measured 65% on a scoped run against 97% whole-scheme. Holding a
   scoped figure to the floor would block on a number known to read low, which
   is the unsatisfiable-gate family this library keeps closing (BUG_026,
   BUG_036, BUG_039, BUG_085).
3. **The relief that exists is narrower.** `coverage_advisory_paths`
   (IDEA_028) makes the floor advisory under declared subtrees for a phase. It
   cannot say "advisory everywhere, because a scheduled run holds the floor",
   and nothing records whether that scheduled run happened.

The standing goal applies throughout: invariant is model-agnostic and
host-neutral. Every decision below is a gate judging recorded evidence, or a
CLI command any host (or a person, or a scheduler) can run. No decision reads
which model or host produced the evidence.

## Rulings (2026-10-26)

| # | Question | Ruling |
|---|---|---|
| 1 | Names | As proposed: `sweep.no_full_run`, `sweep.fresh`, `invariant sweep run\|status\|show`, `methodology.gates.schedule` with `cadence_days`, `grace_days`, `gates`, and `commands.coverage_one`. |
| 2 | Full-run detection level | `warn` in `standard` and `loose`, `block` in `strict`. The hook surface fires only on the adapter's definite `"full"`; any doubt gives no verdict. |
| 3 | Scheduled `test.all_pass` | Still fails on a red scoped run. |
| 4 | `coverage_advisory_paths` | Kept, as the sweep-less phase-scoped form; both go through one split; refused at load together with a scheduled floor. |
| 5 | Grace | 3 days by default, configurable through `grace_days`. |
| 6 | Where sweep records live | A **committed** store, overriding the state-root recommendation. Designed in D9. |
| 7 | First sweep on a branch | Baseline only; files nothing. |
| 8 | Flaky | One isolated re-run per new red; green alone is filed once as `flaky:<id>`, deduplicated; at most 20 re-runs, beyond that one `suite:red` item. |
| 9 | Pre-merge full-run allowlist `schedule.held_paths` | Ships in v1, empty by default, overriding the defer recommendation. Folded into validation, detection and the split (D8). |
| 10 | Where `sweep.fresh` is evaluated | Beside each scheduled gate, in `invariant check`, and as an informational `doctor` row; its level is capped at `warn` by validation. |
| 11 | Where `sweep run` may run | Refused outside the main checkout, on a detached HEAD, or with a dirty tree. The sweep's own record commit happens after the run and never counts as dirtying (D9). |

## Decisions

### D1 — A cadence moves named gates off the per-change path

New manifest block:

```yaml
methodology:
  gates:
    schedule:
      cadence_days: null       # null (default): no cadence, today's behaviour
                               # exactly. A positive integer: the sweep runs
                               # this often.
      grace_days: 3            # how late a sweep may be before sweep.fresh warns
      gates: []                # which gates move to the sweep; the only
                               # schedulable ids are coverage.meets_floor and
                               # test.all_pass
      held_paths: []           # globs whose touch still owes a full run before
                               # merge (D8)
commands:
  coverage_one: null           # scoped coverage, {selector} substituted, the
                               # sibling of test_one
```

With a cadence set, what each scheduled gate answers per change:

| Gate | Per change, when scheduled | Held by |
|---|---|---|
| `coverage.meets_floor` | Every touched source file outside `held_paths` is ADVISORY. The verdict names each with its scoped figure, "not measured", or "no coverage row" (and, once increment 3 lands, the last sweep's figure for it and that sweep's commit). It never fails on an advisory file. The claims about what a path IS (`non_source`, the built-in suffixes) still apply first and are still named. | the sweep (D4) |
| `test.all_pass` | Unchanged logic over the last recorded run, which is now a scoped run: a red scoped run still fails at the configured level, a zero-executed run still fails, a hazard still fails. A green scoped run passes with "the affected suites are green; the whole suite is on the sweep cadence", and a missing run's fix names `pipeline test --selector`. | the sweep (D4) |
| `test.new_test_fails` | Unchanged. RED-first still blocks. | the change |

Why `test.all_pass` keeps judging the scoped run rather than turning
advisory: the lower-bound argument is about coverage only. A scoped red is a
real red, not an under-reading, and passing a change over its own failing test
would be a silent pass on evidence the engine holds. What moves to the sweep is
only the claim "the WHOLE suite is green".

Every recorded run now says which it was. A `test_runs` entry and the
`coverage_run` record carry `selectors` (the list given, empty for a full run)
and `scope` (`"full"` or `"scoped"`), because D8's held paths need to know
whether the evidence on record is a full run, and a resolved command string
cannot be relied on to say so.

### D2 — One split, generalised; `coverage_advisory_paths` is kept and refused alongside a cadence

`_split_advisory` is the single place a touched file is classed HELD or
ADVISORY, and each advisory entry carries its reason: a `coverage_advisory_paths`
glob, or `"scheduled"`. `_with_advisory_report` and `_advisory_detail` read
that one result and word the verdict by reason, so the two policies cannot
drift. This is the generalisation IDEA_032 asks for: one mechanism, two ways to
arm it, and `held_paths` (D8) as the one way to hold a file back from the
second.

`coverage_advisory_paths` stays, because it answers a case a cadence does not:
a project with NO sweep that lands one phase's new code on scoped suites and
owes one coverage run when the phase ends. A manifest that sets both a cadence
scheduling `coverage.meets_floor` and a non-empty `coverage_advisory_paths` is
refused at load: under the cadence every file is already advisory, so the list
would load, do nothing and read as though it had taken effect (BUG_050's
shape). Retiring the key is a separate decision for when no project uses it.

### D3 — Load-time validation refuses a cadence that still demands whole-run evidence per change

`Manifest.validate()` refuses, naming the gate and the key, when:

1. A cadence is set and `schedule.gates` is empty, or names an id other than
   `coverage.meets_floor` or `test.all_pass` (nearest real id suggested, as for
   `enforcement.rules`).
2. A cadence is set, `coverage.meets_floor` is NOT scheduled and its resolved
   level is `block` or `ask`. Its only satisfying evidence is a coverage run,
   a scoped run is a lower bound that would block, so the gate still requires
   whole-scheme evidence per change. The message offers both exits: schedule
   it, or set it to `warn` or `off`.
3. `coverage.meets_floor` is scheduled and `coverage_scope` is not `touched`.
   `all` and `affected` both hold every measured row, which is whole-scheme
   evidence by definition.
4. `coverage.meets_floor` is scheduled and `coverage_advisory_paths` is
   non-empty (D2).
5. `sweep.fresh` resolves to `block` or `ask` (ruling 10: the engine holds that
   ruling rather than prose). Lands with the rule, in increment 3.
6. `cadence_days` is not null or a positive integer, `grace_days` is not a
   non-negative integer, `schedule` is not a mapping, or `gates` is not a list
   of strings.
7. `held_paths` is not a list of glob strings, or `held_paths` or `gates` is
   non-empty with no cadence set: with nothing scheduled there is nothing for
   either to act on, and a list that loads and does nothing is BUG_050's shape
   again (D8).

Levels are read RESOLVED (profile plus overrides), because that is what a gate
would actually do. An unscheduled `test.all_pass` is allowed: a scoped run
satisfies it, so it never requires whole-suite evidence. `INVARIANT_DISABLE`
keeps its existing behaviour; `doctor` reports a refused manifest as a failed
row, as it does today.

### D4 — The sweep: `invariant sweep run`

A new command group, `invariant sweep run | status | show`. `run`:

1. **Refuses before running** unless it is in the main checkout, on a branch
   (not a detached HEAD), with no tracked changes (ruling 11). Untracked files
   do not count. The commit range it records is only true of a committed tree,
   and a sweep inside a session worktree would be a whole-suite run on a change
   by another name.
2. **Runs one command.** `commands.coverage` when `coverage.meets_floor` is
   scheduled (it runs the suite too, so one 45-minute run yields both the reds
   and the figures), else `commands.test`. It reuses the pipeline domain's
   run-and-read seam (the subprocess, `child_env`, the result bundle, the
   chained-coverage refusal, `read_run`, `parse_coverage`), extracted in
   increment 2 to module-level functions both domains call, never a second
   copy. A run that executed zero tests, or produced no coverage rows when
   coverage was asked for, records nothing and files nothing, exactly as
   `run_coverage` refuses today.
3. **Reads failing test identities** through a new adapter question,
   `failing_test_ids(stdout, stderr, bundle) -> list[str] | None` (D5).
4. **Confirms each NEW red by one isolated re-run** through `commands.test_one`
   with that test's selector. Red again: a new red. Green alone: FLAKY (D7).
5. **Diffs against the last committed sweep record on the same branch**
   (`core/sweep`, pure arithmetic): new reds, flaky, floor drops, still broken,
   recovered. A floor drop is a held file (after `non_source`,
   `coverage_exclude` and a `coverage_baseline` ratchet entry) that is below
   the floor now and was not below it, or did not exist, at the last sweep.
6. **Files each new finding** through `domains/workitem.new` as a bug, with
   two fields: `sweep_key` (`test:<id>`, `flaky:<id>`, `floor:<path>`, or
   `suite:red` where identities are unreadable) and `sweep_range`
   (`<last sweep head>..<this head>`, full hashes). The body names the
   manifest command key, the counts, the figure and floor, and any hazard on
   the run.
7. **Never files a key twice.** Before filing, it reads the backlog for an
   OPEN item (the machine's non-terminal states, so `fixed` and `verified`
   count) carrying that `sweep_key`. Found: recorded as "still broken" against
   that item and not filed; an item in `fixed` that is still red is named as
   such in the output, since that is a regression signal. The backlog is the
   dedup source because it is shared: two machines cannot double-file.
8. **Writes and commits the sweep record** (D9), after the run and after
   filing.

The first sweep on a branch has no baseline: it records, reports every
failure and below-floor file, and files nothing (ruling 7). `--dry-run` runs
and diffs but writes no record, files nothing and commits nothing. `status`
prints the last sweep, its age and the freshness verdict; `show` prints one
record. Recovered keys are reported, never auto-closed: closing a bug goes
through its own lifecycle.

Scheduling the sweep is the project's: cron, a LaunchAgent or CI calls
`invariant sweep run`. The engine judges the evidence it leaves, not what
started it.

### D5 — Full-run detection: one rule, two surfaces, adapter-judged, no verdict on doubt

New rule `sweep.no_full_run`, armed only when a cadence is set. Whole-suite
detection is armed when `test.all_pass` is scheduled, whole-scheme detection
when `coverage.meets_floor` is. A whole-scheme coverage run also runs the
whole suite, so it is caught when either is armed.

- **CLI surface (host-neutral, exact).** `pipeline test` with no `--selector`
  is the configured whole-suite command, and `pipeline coverage` with no
  `--selector` is the whole-scheme command, by construction. The rule is
  evaluated before either spawns anything, with the surface named in the rule
  context (`extra["full_run"]`). At `block` or `ask` the command refuses
  (exit 1) having run and recorded nothing, unless an `invariant override`
  grant for the rule is held, which the command spends and records with
  surface `cli`; at `warn` it runs and prints the verdict. This surface has no
  false positives and works for every host, and for no host at all.
- **Hook surface (`pre-tool-use` on a shell call).** Catches a full run that
  bypasses the CLI. The shell command is tokenised (the `_shell_tokens` the
  git-commit readers use) and split at shell separators; leading `VAR=value`
  assignments are dropped. When the configured `commands.test` or
  `commands.coverage` template is a token prefix of a segment (a template
  token carrying a `{placeholder}` matches any one token), the remaining
  tokens go to a new adapter question, `test_scope_of(extra_tokens) -> "full"
  | "scoped" | None`. Only `"full"` fires. `None` gives no verdict, so a
  doubtful call is never refused. This needs `tool_input.command` named in
  `NORMALISED_PAYLOAD_KEYS` (the contract's third widening; `privacy.clean`
  already reads it through `tool_input`).

Held paths (D8) are the one exemption: when the session's changed set touches
a `held_paths` glob, the change owes a full run, so the rule answers clean and
says why.

Per-ecosystem answers:

| Adapter | `"full"` | `"scoped"` | `None` (no verdict) |
|---|---|---|---|
| `python` | no extra tokens, or only known non-narrowing options (`-q`, `-v`, `-x`, `-s`, `--tb=`, `-p <plugin>`, `--cov*`, `--no-header`) | any positional (path or node id), `-k`, `-m`, `--lf`, `--ff`, `--deselect` | any other option |
| `swift_xcode` | no extra tokens | any `-only-testing:` | `-skip-testing:` or any other token |
| `node` | no extra tokens | never claimed | anything after the script (`--` arguments are runner-specific) |
| `generic` | no extra tokens | never claimed | anything else |

Reading failing-test identities, per ecosystem (`failing_test_ids`, increment
3): `python` reads pytest's short summary `FAILED <nodeid>` lines (printed by
default); `swift_xcode` reads failed test identifiers from the result bundle,
else the console's failed test-case lines; `node` and `generic` answer `None`,
and the sweep falls back to one `suite:red` key per branch.

The asymmetry is stated because it decides the `None` column: a false "full"
refuses a legitimate scoped run (visible, one override away); a missed full
run costs time but never passes anything it should not. So "full" is claimed
only on positive evidence, and the misses are accepted: `python -m pytest`
when the template says `uv run pytest`, `npx jest` when it says `npm test`.

The escape for a deliberate full run is the existing grant, `invariant
override sweep.no_full_run --reason "<why>"`, now also spent by the CLI
surface: one grant, one run, recorded in `session.overrides` with the surface
named. No new flag.

### D6 — Freshness: `sweep.fresh` warns, never blocks

New rule `sweep.fresh`, N/A without a cadence. With one: no committed sweep
record on the base branch warns "no sweep recorded"; a last sweep older than
`cadence_days + grace_days` warns and names its age in days and its commit.
Otherwise clean, and the clean message names the age too. Age is today's date
minus the record's bare date, in whole days (D9).

Where it is evaluated (ruling 10): `core/machines` injects it beside every
scheduled gate a machine lists (by gate id, the way docs-first is injected by
role), so it is reported on every transition and `pipeline gates` call that
carries a scheduled gate, and in `invariant check`. `doctor` gains an
informational row naming the active cadence, the scheduled gates and the last
sweep's age, in the spirit of the parked-session and advisory-exception rows:
informational, never failing. Its level is capped at `warn` by D3 item 5.

Grace (ruling 5): **3 days**, so the warning starts after day 10 on a weekly
cadence. A weekly sweep that misses its slot because the machine was off over
a weekend runs three days late; one missed weekly run (14 days) still warns.

### D7 — A flaky test is filed once, as flaky, never as a new red

The isolated re-run in D4 step 4 separates "red under load, green alone"
(BUG_081's exact shape) from a real red. A flaky test is filed once under
`flaky:<id>` and deduplicated like any key, so a test that flakes every week
produces one open item, not one per sweep. A key already open under `test:<id>`
or `flaky:<id>` suppresses both forms, so an item filed by hand can be claimed
by adding `sweep_key` to its frontmatter; BUG_081 itself should carry
`sweep_key: flaky:tests/test_oracle_tui.py::TestItAlwaysRefreshes::test_the_timer_runs_from_mount_even_when_the_first_load_fails`
before the first sweep in this repository.

Re-runs are capped at 20 (ruling 8). Beyond the cap the sweep files no
per-test items, files one `suite:red` item naming the count, and says the run
looks systemic (a broken build, a fixture, an environment leak) rather than
many independent defects. Floor drops measured on a run with failures are
filed with the failure count in the body, because a test that aborts early
lowers coverage it did not cause.

### D8 — The pre-merge full-run allowlist ships in v1

`methodology.gates.schedule.held_paths` (ruling 9) is a list of globs whose
touch still owes a FULL run before merge, empty by default, so shipping it
changes nothing for a project that does not set it. It is read through the same
`_declared_in_project` and `_first_matching_glob` as every other coverage glob,
against the session's `_changed_set`, so it cannot disagree with them about
what a pattern names. It reaches exactly three places:

- **The split (D2).** A touched source file under a held glob is HELD even
  when `coverage.meets_floor` is scheduled: today's floor, unchanged, and its
  evidence must be a full coverage run. If the recorded `coverage_run` is
  scoped (D1), the gate fails naming the held files and `held_paths`, with
  `invariant pipeline coverage` as the fix, because a scoped figure is the
  lower bound the cadence exists to stop holding to the floor.
- **`test.all_pass`, when scheduled.** A change touching a held path owes a
  whole-suite run: a green scoped last run fails naming the held paths, with
  `invariant pipeline test` as the fix. Red, zero-executed and hazard
  failures keep their own wording.
- **Detection (D5).** A full run on a change touching a held path is the run
  that change owes, so `sweep.no_full_run` answers clean on both surfaces and
  names the held path. No override is spent.
- **Validation (D3 item 7).** A list of glob strings, refused when non-empty
  without a cadence.

The changed set is the one the floor reads, so its own refusal carries over: a
change whose touched set cannot be established is treated as touching no held
path for detection (the verdict still fires, and an override passes it), while
both gates refuse it as the floor already refuses an unestablished set. A
scheduled floor with nothing held otherwise owes no coverage run at all.

### D9 — Sweep records are a committed, declared store

Ruling 6 put the records in the repository rather than the state root, so
that freshness and the diff baseline are the same on every clone. That makes
the sweep the first engine command that commits, and each part of the design
below exists to keep that commit inside the guarantees the library already
gives.

- **Location: a declared store.** New key `paths.sweeps`, default
  `docs/sweeps`. Declaring it is what puts it inside the CLI's write-scope
  invariant ("the state root and the stores the manifest declares", ADR-002
  D4) and exempts it from session isolation through `_is_exempt`, beside
  `items.path` and `paths.reports` (manifest.md, "Declared stores are writable
  stores"). A project that keeps prose at that path points the key elsewhere.
- **Format: one JSON file per sweep**, `<paths.sweeps>/<date>-<short head>.json`
  (`date` is the bare UTC date, `short head` the first twelve hex digits), so
  two sweeps never write one file and a merge of two clones' records never
  conflicts. Version 1 fields: `version`, `seq` (one more than the previous
  record on the branch, 1 for the first), `ran_on` (bare date), `branch`,
  `head` and `previous` (full hashes; `previous` null on a first sweep),
  `command` (the manifest key that ran, `coverage` or `test`, never the
  resolved command), `executed`, `failed`, `passed` (added in
  implementation: an adapter that counts nothing reports `failed` null for a
  green run and a red one alike), `failing` (ids, or null where the
  adapter cannot read them), `flaky`, `files` (`{project-relative path: pct}`),
  `floor`, `hazards` (ids), `filed` (`[{key, item}]`), `still_broken`
  (`[{key, item, status}]`) and `recovered` (keys).
- **Privacy-clean by construction, then checked.** The record holds hashes, a
  bare date, project-relative paths, test identifiers and a manifest key: no
  clock time, no host or session identifier, no absolute path. The resolved
  command is excluded deliberately, because it carries the per-run result
  bundle under the state root, which is a home path. Before committing, the
  sweep scans the record and the commit message with the project's own
  `PrivacyScanner` and the project's patterns; a finding refuses the commit,
  leaves the record uncommitted, exits non-zero and prints the findings
  redacted. The engine runs the scan itself because the commit is a
  subprocess, not a host tool call, so no hook sees it and `privacy.clean`'s
  commit route is never consulted.
- **An engine-owned commit, after the run.** Once the run, the re-runs and the
  filing are done, the sweep stages exactly the record and the work-item files
  its filing changed (read as the store's `git status` before and after
  filing, so a file it did not write is never swept in) and runs `git commit
  --only -m "Sweep: <date> <short head> (<n> filed)" -- <those paths>` in the
  main checkout. `--only` commits those paths and nothing else that may be
  staged. It never pushes; publishing stays the project's decision
  (`session.push`, off by default).
- **Why it does not trip the session rules.** `session.no_edit_in_main_checkout`
  and the isolation rules screen edit-tool calls made by a host; the sweep is a
  CLI process writing a declared store, which is the case the declared-store
  exemption exists for, and a scheduler running it has no session at all. The
  precondition (ruling 11) is "no TRACKED changes": the record is new and the
  filed items are written after the precondition is checked, so the sweep's own
  output never counts against it. A record left uncommitted by a refused or
  failed commit is untracked and does not block the next sweep; a work-item
  file it left modified does, and the refusal names it.
- **Reading it: from git, not the working tree.** The baseline for the diff
  and the freshness verdict read committed records only, through `git ls-tree`
  and `git show` on a ref: `HEAD` for `sweep run`, the session's recorded base
  branch for `sweep.fresh`. A session worktree cut before the latest sweep
  therefore still sees it, and an uncommitted record is never a baseline.
  Latest means the highest `seq` among records whose `branch` matches.
- **Across machines.** A record reaches another clone when the base branch
  does. A clone that has not pulled sees an older last sweep and `sweep.fresh`
  warns, which is the right answer for evidence it does not hold, and safe,
  because the rule never blocks. Dedup is unaffected: it reads the backlog,
  which travels the same way.

The alternative recommended before the ruling, machine-local records under the
state root, needed no commit and no privacy scan but gave each clone its own
freshness and its own baseline; the ruling chose shared evidence.

## Increments

| Increment | Lands | Touched (source; tests and docs alongside) |
|---|---|---|
| 1 | This ADR, accepted with the rulings (docs only). | `docs/adr/ADR-010-…`, `docs/index.md` |
| 2 | D1 (both gates' cadence wording; the sweep figure waits for increment 3), D2 with D8's third reason, D3 items 1–4, 6, 7, D5 on both surfaces with `test_scope_of`, D8, `commands.coverage_one` and `pipeline coverage --selector`, `selectors`/`scope` on run records, the run-and-read seam extracted to module-level functions in `domains/pipeline` with `INVARIANT_HOST_SESSION_ID` and `INVARIANT_HANDOFF` stripped from the child environment, and the profile levels for `sweep.no_full_run`. | `core/rules`, `core/manifest`, `domains/pipeline`, `cli/main`, `adapters/base`, `adapters/python_`, `adapters/swift_xcode`, `adapters/node`, `data/profiles/*` |
| 3 | D4, D6, D7, D9: `core/sweep`, `domains/sweep`, `invariant sweep run\|status\|show`, `paths.sweeps` and its exemption, `failing_test_ids`, filing and dedup, flaky re-runs, the engine-owned commit, `sweep.fresh` with its machine injection, D3 item 5, the `doctor` row, the last sweep's figure in the advisory verdict, and the design and review agents learning to record with `--selector` under a cadence. | new `core/sweep`, new `domains/sweep`, `core/rules`, `core/machines`, `core/manifest`, `cli/main`, `adapters/*`, `data/agents/*` |

## Component plan

```mermaid
graph TD
    CLI[cli] --> SWEEPD["domains/sweep (NEW, increment 3)"]
    CLI --> PIPELINE[domains/pipeline]
    SWEEPD --> PIPELINE
    SWEEPD --> WORKITEM[domains/workitem]
    SWEEPD --> CSWEEP["core/sweep (NEW, increment 3)"]
    SWEEPD --> STORE[core/store]
    SWEEPD --> GIT[core/git]
    SWEEPD --> PRIVACY[core/privacy]
    SWEEPD --> adapters
    RULES[core/rules] --> CSWEEP
    CSWEEP --> GIT
    FSM["core/fsm (machines)"] -. injects sweep.fresh beside scheduled gates .-> RULES
    PIPELINE --> RULES
    PIPELINE --> adapters
```

- `core/sweep` (increment 3): the sweep record type, reading the latest
  committed record through `core/git`, the diff (new, flaky, floor drop,
  still broken, recovered) and the age arithmetic. Pure apart from those git
  reads, like `core/plan`, so `core/rules` can ask freshness without importing
  a domain.
- `domains/sweep` (increment 3, not an FSM, like `stats`): preconditions, the
  run, the re-runs, the dedup read and filing through `domains/workitem`, the
  record, the privacy scan and the commit. `SWEEPD → PIPELINE` is the
  extracted run-and-read seam and `SWEEPD → WORKITEM` is `list` and `new`; both
  horizontal, with the `stats → session` precedent.
- `domains/pipeline` (increment 2): the run-and-read seam extracted
  (`run_command`, `child_env`, `measure_coverage`, `joined_selectors`); `run_coverage` gains
  selectors (`commands.coverage_one`); both run paths record `selectors` and
  `scope`. The CLI asks `sweep.no_full_run` before either spawns.
- `core/rules` (increment 2): the generalised split, the held paths, the
  scheduled `test.all_pass` wording, `sweep.no_full_run`. `core/manifest`:
  the schema and D3.
- `adapters/*`: `test_scope_of` (increment 2), `failing_test_ids`
  (increment 3).
- `cli` (increment 2): `pipeline coverage --selector`, the CLI detection
  surface and its override spend; (increment 3) `sweep run|status|show`, the
  `doctor` row.
- `data/profiles`: levels for the new rules. `data/agents` (increment 3): the
  design and review agents learn to record with `--selector` when a cadence is
  active.

"The five state domains" becomes six in increment 3.

## Consequences

- A project with no cadence sees no change: every new key defaults off or
  empty, `sweep.no_full_run` answers N/A, and the split returns today's result.
  The child environment of a recorded run loses two host variables, which only
  ever made a suite exercising this library's own sessions misbehave.
- A cadence project lands a change touching shared code on scoped evidence
  alone, keeps RED-first and a green scoped run as blocking, and learns of
  whole-suite regressions within one cadence, each as one work item carrying
  its commit range.
- The sweep inherits every adapter's reading defects: BUG_018 (the node
  adapter cannot parse coverage, so a node sweep has no floor drops) and
  BUG_038 (a swift-testing run scrapes as zero executed, which the sweep
  refuses as "ran nothing") stay open and limit those ecosystems.
- A sweep in this repository runs the suite as a child process. BUG_058 and
  BUG_077 show what an inherited `INVARIANT_HOST_SESSION_ID` or
  `INVARIANT_HANDOFF` does to this suite; a sweep started from a host shell
  would file those false reds as bugs. The extracted seam strips both, for
  `pipeline test` and `pipeline coverage` too.
- The sweep is the first command that commits. Its commit is confined to the
  record and the items it filed, screened by the project's own privacy
  scanner first, and never pushed.
