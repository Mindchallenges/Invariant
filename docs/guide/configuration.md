# Configuration

One file, `.claude/invariant.yaml`. Every key has a default, so a working
manifest is three lines; everything on this page is an *override*. The
complete schema with every default is in the
[manifest reference](../architecture/manifest.md).

```bash
invariant config show --provenance     # every resolved value and the layer it came from
invariant rules list                   # every rule, its hook event, its resolved level
```

Resolution order, lowest to highest: library defaults → the adapter for the
first ecosystem → the enforcement profile → your manifest. `--provenance`
labels each value with the layer that set it, which is how "why is this rule
blocking me" gets answered without reading library source.

## Enforcement

### Profiles

```yaml
enforcement:
  profile: standard        # strict | standard | loose
```

| profile | blocks | for |
|---|---|---|
| `loose` | edits in the main checkout; personal data in a document | adopting: watch what it *would* have said, then tighten from evidence |
| `standard` | the above, plus: isolate before editing; read the docs before designing; update them on a structural change; a bug reproduced before fixed; the push barrier; a green suite to land | the default |
| `strict` | everything the engine can prove — coverage floor, pipeline complete, memory index current, teardown done. Two heuristics stay advisory | a team already burned by each of these |

### Per rule

```yaml
enforcement:
  profile: standard
  rules:
    coverage.meets_floor: block     # block | ask | warn | off
    docs.diagrams_current: "off"    # quote off — bare `off` is YAML for false
```

A level is a property of how a verdict is *delivered*, not of the rule's
logic, so any rule can be set to any level with no code change. `ask`
delivers the verdict to you as a Claude Code permission prompt, with the
reasoning as its text — the override is in band. A key that names no rule is
refused at load with the nearest real id, so a typo cannot silently relax
nothing.

## Commands

The adapter supplies defaults for the ecosystem (`pytest`, `npm test`,
`xcodebuild test`); override any of them, especially when the tool is not on
PATH in the shell the hooks run in:

```yaml
commands:
  build: uv run python -m build
  test: uv run pytest
  test_one: uv run pytest {selector}
  lint: uv run ruff check .
  coverage: uv run pytest --cov --cov-report=term-missing
```

A command that cannot be found records exit 127 — command-not-found rendered
as a red suite — and every test gate then reads noise. `invariant doctor`
reports each command as configured; `invariant pipeline test` is the quick
way to see whether it actually runs.

Where an ecosystem measures and *reports* coverage with separate programs,
set `coverage_report` too; joining the two into one shell command is refused,
because a shell reports only the last command's status.

## Methodology and coverage

```yaml
methodology:
  pipeline: tdd              # default for `pipeline start`
  extra_machines:            # project-local machines; same id shadows a shipped one
    - .claude/machines/spike.yaml
  gates:
    coverage_min: 85
    coverage_scope: touched  # touched | affected | all
    coverage_exclude: ["src/generated/**"]   # measured, reported, not held to the floor
    non_source: ["fixtures/**", "design/**"] # no run could produce a row; not code
    coverage_baseline: .claude/coverage-baseline.txt  # ratchet: below the floor but not below its entry passes
    coverage_advisory_paths: ["web/**"]       # floor advisory here: reported, never blocks
```

`coverage_exclude` and `non_source` answer different questions — "measured but
not held" against "cannot be measured at all" — and every path either one
sets aside is named in the verdict. `coverage_baseline` is one
`<path> <pct>` per line; a file regresses below its entry and the gate blocks,
a file below the floor but at its entry does not.

`coverage_advisory_paths` (shown above as `["web/**"]`, empty by default) is
for a phase that lands new code on scoped test suites with one full coverage
pass owed at its end. A touched file under one of its globs is named in the
verdict with its figure, or as not measured, and never fails the gate; every
other file is held exactly as before. A change that touches only advisory paths
passes without a coverage run, and the verdict says so. `invariant doctor`
lists a configured list as an active exception. Ending the phase is deleting
the list, after the owed coverage run. Each of the three glob lists must be a
YAML list: a bare string is refused at load.

### A sweep cadence

A project that never runs the whole suite or whole-scheme coverage on a change
moves those gates to a scheduled sweep (ADR-010):

```yaml
commands:
  test_one: uv run pytest {selector}
  coverage_one: uv run pytest --cov --cov-report=term-missing {selector}
methodology:
  gates:
    schedule:
      cadence_days: 7          # null (default): no cadence
      grace_days: 3
      gates: [coverage.meets_floor, test.all_pass]
      held_paths: ["core/**"]  # touching these still owes a full run before merge
```

A change then records its affected suites — `invariant pipeline test
--selector <tests>` and `invariant pipeline coverage --selector <tests>`. The
floor reports each touched file's scoped figure without holding it, a red
scoped run still fails `test.all_pass`, and `sweep.no_full_run` warns (blocks
under `strict`) when a full run is made on a change anyway. `invariant
override sweep.no_full_run --reason "<why>"` lets one deliberate full run
through. The manifest refuses a cadence that would still demand a full run per
change, such as an unscheduled `coverage.meets_floor` at `block`.

## Sessions

### Where worktrees live

```yaml
session:
  worktree_dir: null           # default: beside the repository, ../<project>-wt-<id>
  worktree_dir: .claude/worktrees   # inside it; add this path to .gitignore
  worktree_prefix: null        # null derives it from the directory name
```

Inside the repository is the better default for Claude Code specifically: the
host trusts the directory it already manages and stops confirming every entry
into a new one. `init` does not choose it for you because it needs a
`.gitignore` line a reader should see.

### Publishing

```yaml
session:
  push: false                  # true makes `session push` legal, once, after the merge
```

### What a session need not own

```yaml
session:
  exempt_paths: [".claude/*", ".claude/**", "MEMORY.md"]
  plan_stale_days: 7
```

Paths matching `exempt_paths` are never held to `session.own_file_set_only`
or to isolation — the manifest, hooks and the memory index are edited in
place. `plan_stale_days` is how long an open plan may sit before unmerged
work stops reading as parked and starts reading as stranded.

## Documentation

```yaml
paths:
  docs: docs/architecture      # what the docs-first gate reads
docs:
  format: markdown             # markdown | sphinx
  diagrams: mermaid            # mermaid | plantuml | none
  require_diagram_update: true
```

Point `paths.docs` at wherever your architecture documents live. The gate
needs a *read* of something under it before a design state can be left, and
an *update* to something under it when the change is structural;
`docs.diagrams_current` additionally wants a diagram touched when the
structure changed, and `diagrams: none` turns that off honestly.

## Work items

```yaml
items:
  backend: markdown            # markdown | json | sphinx_needs | none
  path: docs/backlog           # a directory of files, or one file, by backend
  id_prefix: ""                # ADDITIONAL to the type's own: "" mints BUG_001
  id_strategy: numeric         # numeric → BUG_001 | slug → BUG_DRAIN_STALLS
  exempt: true                 # items are data; written outside any session
```

Lifecycle semantics live above the backend. A bug reaches `fixed` only
through `reproduced` whatever format stores it; `backend: none` keeps the
machine and the agents while an external tracker holds the records.

## Memory

```yaml
paths:
  memory: .claude/memory
  memory_index: MEMORY.md
memory:
  managed: true                # false: the library never writes here
  index_generated: true        # MEMORY.md is regenerated from the directory
```

With `managed: true` the `Stop` hook validates the store — schema, dead
`[[links]]`, index currency, candidate duplicates — and `invariant memory
write` is the way entries are added. A project that keeps its own memory
convention sets `managed: false` and nothing is touched.

## Privacy

```yaml
privacy:
  enabled: true
  patterns: [home_paths, emails, ip_addresses, tokens, clock_times, links]
  extra: ['ACME-[0-9]{6}']     # project-specific regexes
  allow: ["github.com/acme/widgets"]   # literal strings exempt wherever they appear
  scope: [docs, memory, reports, commit_messages, "docs/"]
```

`scope` keywords resolve through `paths.*`; any other entry is a
project-relative prefix, which is how to widen the scan beyond what the paths
name (here, to all of `docs/` rather than only `docs/architecture`). `allow`
is for a *value* that is legitimately public — your own repository's URL;
the in-text marker `invariant: allow-privacy` is for a *place* and reaches
only the block it precedes. `device_ids` exists but is opt-in: hex device ids
collide with git hashes, which documents cite constantly.

`privacy.clean` is held out of `standdown` by design, and `loose` keeps it at
`block`. It is the one gate whose failure cannot be undone after the fact.

## Agents

```yaml
agents:
  library: [feature-architect, compliance-reviewer, backlog-curator, bug-reproducer]
  local: [api-reviewer]        # yours; checked for loadability, never for drift
  roles:
    design: [feature-architect]
    review: [compliance-reviewer, api-reviewer]
```

## A complete example

A Python service that wants worktrees inside the repo, a hard coverage floor,
and a tracker elsewhere:

```yaml
version: 1

project:
  name: widgets-api
  ecosystems: [python]

commands:
  test: uv run pytest
  test_one: uv run pytest {selector}
  lint: uv run ruff check .
  coverage: uv run pytest --cov --cov-report=term-missing

methodology:
  gates:
    coverage_min: 90

enforcement:
  profile: standard
  rules:
    coverage.meets_floor: block
    agents.design_agent_ran: block
    agents.review_agent_ran: block

session:
  worktree_dir: .claude/worktrees

items:
  backend: none

privacy:
  allow: ["github.com/acme/widgets-api"]
  scope: [docs, memory, reports, commit_messages, "docs/"]
```
