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
