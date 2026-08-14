# The manifest

`.claude/invariant.yaml` is the only project-aware artifact. Everything the
library does that depends on *this* project is resolved from here or from an
adapter named here. If something about a project cannot be expressed in the
manifest, that is a gap in the library, not a reason to edit installed files.

## Full schema

```yaml
version: 1

project:
  name: example
  ecosystems: [swift]          # selects adapters; first is primary

paths:
  root: .                      # repo root, relative to the manifest's parent's parent
  state: .claude/invariant/state
  docs: docs/architecture      # the docs-first gate reads these
  memory: .claude/memory
  memory_index: MEMORY.md
  reports: docs/reports        # where `stats report --write` puts its artifact

commands:                      # adapters supply defaults; these override
  build: null
  test: null
  test_one: null               # {selector} is substituted
  lint: null
  coverage: null                 # runs the suite; {result_bundle} substituted
  coverage_report: null          # optional: reads the measurement, separately
                                 # — a shell reports only the LAST command's
                                 # status, so the two must not be one command

docs:
  format: markdown             # markdown | sphinx
  diagrams: mermaid            # mermaid | plantuml | none
  index: docs/index.md
  require_diagram_update: true

methodology:
  pipeline: tdd                # id of a shipped or project-local FSM file
  extra_machines: []           # project-local FSM yaml paths
  gates:
    coverage_min: 90
    coverage_scope: touched    # touched | affected | all

enforcement:
  profile: standard            # strict | standard | loose
  rules:                       # per-rule overrides on top of the profile
    session.own_file_set_only: block
    memory.index_current: warn

session:
  exempt_paths: [".claude/*", ".claude/**", "MEMORY.md"]
  worktree_prefix: null        # null derives it from the project directory name
  worktree_dir: null           # null puts worktrees beside the repository;
                               # a relative value resolves against the repo root,
                               # so worktrees may live INSIDE it
  push: false                  # is publishing to a remote a session step at all

items:
  backend: markdown            # markdown | sphinx_needs | json | none
  path: docs/backlog.md
  id_prefix: ""                # an ADDITIONAL prefix; the type supplies its own
  id_strategy: numeric         # numeric -> BUG_001 | slug -> BUG_DRAIN_STALLS
  exempt: true                 # work items are data; exempt from isolation

memory:
  managed: true
  index_generated: true

privacy:
  enabled: true
  patterns: [home_paths, emails, ip_addresses, tokens, clock_times, links]
  extra: []                    # project-specific regexes
  allow: []                    # literal strings exempted
  scope: [docs, memory, commit_messages]

agents:
  library: [feature-architect, compliance-reviewer, backlog-curator, bug-reproducer]
  local: []                    # project-owned agents the library leaves alone
  roles:                       # which agent answers for which pipeline role
    design: [feature-architect]
    review: [compliance-reviewer]

host:
  kind: claude_code
```

### Declared stores are writable stores

`items.path`, `paths.memory` and `paths.reports` are not merely locations —
declaring one is what makes the CLI's write-scope invariant cover it, and what
exempts it from session isolation. `cli.md` states the invariant as "the state
root and the stores the manifest declares" (ADR-002 D4) rather than as a list,
so a feature that needs somewhere to write adds a key here instead of adding
an exception there.

The consequence to be aware of: `paths.reports` defaults to `docs/reports`, so
a project that already keeps hand-written prose at that path would find it
exempt from isolation. Point the key somewhere else if so — the exemption
follows the declaration, which is the whole point.

### Where worktrees live

`session.worktree_dir` exists for hosts that trust one location and confirm
every other. Claude Code, for one, asks before relocating its permission root
into a worktree outside `.claude/worktrees/`, and that confirmation is
deliberately not suppressible by any permission rule — so a project whose
worktrees sit beside the repository is asked once per session, forever. Pointing
this at the trusted directory ends that, and the trust is decided by path: a
worktree created there by plain `git worktree add` is entered without a prompt.

Placing worktrees inside the repository has one consequence worth stating,
because it fails silently rather than loudly. Exempt patterns are matched with
`fnmatch`, whose `*` spans path separators, so `.claude/*` matches
`.claude/worktrees/wt-1/src/app.py` — every source file in a worktree placed
there. The isolation rule consults the exemption first and skips on a match, so
the result would not be an error but an absence of enforcement. The library
therefore resolves a path inside a linked worktree against **that worktree
alone**, which is the more accurate reading anyway: a file's project-relative
identity is where it sits in its own checkout, not where its checkout sits. A
directory prefix cannot exempt what is underneath it.

The directory should also be ignored by git, or the main checkout is dirtied by
every live session. Claude Code writes `.claude/worktrees/` into
`.git/info/exclude`, which is local to one clone — a project that wants this to
hold on every machine commits the entry to `.gitignore` instead.

Every key has a default. A minimal usable manifest is three lines:

```yaml
version: 1
project: { name: example, ecosystems: [python] }
```

## Resolution order

For any value, later wins:

1. Library defaults
2. Primary adapter defaults (from `project.ecosystems[0]`)
3. Additional adapters, in listed order
4. Enforcement profile (for rule levels only)
5. Explicit manifest values
6. Environment overrides (`INVARIANT_*`), for one-off escapes

`invariant config show` prints the fully resolved configuration with the
provenance of each value, because "why is this rule blocking me" must be
answerable without reading the library's source.

## Adapters

An adapter is a Python class exposing ecosystem knowledge:

```python
class Adapter:
    id: str
    def detect(self, root: Path) -> bool: ...
    def default_commands(self) -> dict[str, str | None]: ...
    def parse_test_output(self, stdout: str, stderr: str, code: int) -> TestResult: ...
    def parse_coverage(self, raw: str) -> dict[str, float]: ...
    def source_files(self, changed: list[Path]) -> list[Path]: ...
    def carries_executable_code(self, path: Path) -> bool | None: ...
    def hazards(self) -> list[Hazard]: ...
```

`hazards()` is the interesting one. It returns known ecosystem traps that the
engine surfaces as context or as pre-flight checks — cases where a command's
exit code does not mean what it appears to mean. The `swift_xcode` adapter
ships with several: an exit status that indicates whole test suites were
silently dropped rather than run, a destination argument whose name-based form
resolves to a device that may not exist, and a debug device build whose main
binary carries no symbols so that symbol lookups return empty for every query.

A hazard is not a rule. Rules gate transitions; hazards annotate results so
that a green is not mistaken for proof. `parse_test_output` returning
`passed=True` alongside a hazard flag is how the library says "this looks
green, and here is the specific reason that might be a lie."

`default_commands` has a quieter failure, and it is the one that costs most
because nothing reports it. A default names a tool; it does not guarantee the
tool is installed. The Python adapter defaults `lint` to a bare `ruff check .`,
which needs `ruff` on PATH — where it is absent the command does not fail the
check, it fails to *spawn*, and "the linter could not run" is indistinguishable
in a report from "the linter found nothing". Five consecutive sessions in this
repository recorded lint as NOT RUN and moved on before one of them tried an
invocation that resolved. The node adapter's `npm run lint` has the same shape:
it assumes a script the project may never have defined.

The rule is the one `parse_coverage` states from the other direction — a
reading that can come back clean for the wrong reason is the one to refuse. A
project overrides the default with an invocation that resolves through a
declared, locked dependency rather than through whatever happens to be on PATH;
pinning is part of the fix, because an unpinned linter moves the finding
baseline without a change to a line of source. An unrunnable gate is worse than
an absent one: an absent gate is visibly absent, while an unrunnable one reads
as a pass.

`parse_coverage` has a trap of its own, and it is the quiet kind. A reporter's
table layout belongs to the reporter, so a parser keyed to that layout breaks
whenever the layout legitimately varies — and it breaks by returning `{}`,
which is indistinguishable from "nobody has measured yet". The Python adapter
required exactly two numeric columns, which is coverage.py's non-branch table;
every project setting `branch = true` got four, matched nothing, and could
never satisfy a coverage floor at all (BUG_011). The rule is therefore to
anchor on what the adapter does own — the source extension — and to read a
table ROW rather than a column count: the name, then whatever numeric cells
this version emits, then the first percentage. Not "anything up to the first
percentage", which reads `pytest -v`'s per-test progress line as a
measurement; per `Hazard.can_overstate`, a reading that can come back HIGH is
the one a parser must refuse.

`carries_executable_code` is the other three-valued answer, and it is asked
only to explain a coverage row that is **absent**. Reporters emit nothing for
a file with no executable lines — a bare protocol, an enum of cases, a
typealias — and that silence is indistinguishable from a file the run never
measured. `True` and `False` are claims; `None` means "cannot tell", and a
caller deciding whether to *relax* must treat it as "do not relax", exactly as
with `git.branch_files`. The generic adapter knows no language, so it always
answers `None`. The two wrong answers are not symmetrical and an
implementation must prefer the first: calling an inert file live costs a
spurious block, which is visible and sends someone to look; calling a live
file inert passes unmeasured code silently.

## Backends for work items

The item model is fixed; storage is pluggable, so a project keeps whatever
format it already uses.

| Backend | Stores as | Notes |
|---|---|---|
| `markdown` | One file per item, frontmatter fields | Default; git-native, no tooling |
| `sphinx_needs` | Directives in a reStructuredText file | For projects already rendering a needs graph |
| `json` | One JSON document | For machine consumption |
| `none` | Nothing | Work-item domain disabled |

A backend implements read, write, list, and transition-persist. The lifecycle
FSM and the id scheme live above it in `domains/workitem`, so switching
backends does not change item semantics.
