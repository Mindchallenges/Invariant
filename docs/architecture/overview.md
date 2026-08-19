# Architecture overview

`invariant` turns agent-workflow discipline from prose the model is asked to
remember into state a script owns. See `docs/adr/ADR-001` for why.

This document is the authoritative map of the components and their
dependencies. Per the library's own docs-first rule, read it before changing
code, and update it when a structural relationship changes.

## Layers

```mermaid
graph TD
    subgraph host["hosts/ — agent-host adapters"]
        CC[claude_code]
        CCTEL[claude_code_telemetry]
    end

    subgraph cli["cli/ — entrypoint"]
        CLI[invariant CLI]
    end

    subgraph core["core/ — host-agnostic engine"]
        FSM[fsm]
        STORE[store]
        MANIFEST[manifest]
        RULES[rules]
        ITEMS[items]
        MEMORY[memory]
        PRIVACY[privacy]
        STATS[stats]
    end

    subgraph domains["domains/ — the five state domains"]
        SESSION[session]
        PIPELINE[pipeline]
        WORKITEM[workitem]
        MEMDOMAIN[memorystore]
        STATSDOMAIN[stats]
    end

    subgraph adapters["adapters/ — ecosystem knowledge"]
        GENERIC[generic]
        SWIFT[swift_xcode]
        PY[python]
        NODE[node]
    end

    subgraph data["shipped data"]
        METH[methodologies/*.yaml]
        PROF[profiles/*.yaml]
        AGENTS[agents/*.md]
    end

    CC --> CLI
    CLI --> SESSION
    CLI --> PIPELINE
    CLI --> WORKITEM
    CLI --> MEMDOMAIN
    CLI --> STATSDOMAIN
    CLI --> RULES
    CLI --> MANIFEST
    CLI -. injects .-> CCTEL

    SESSION --> FSM
    PIPELINE --> FSM
    WORKITEM --> FSM
    SESSION --> STORE
    PIPELINE --> STORE
    SESSION --> RULES
    PIPELINE --> RULES
    WORKITEM --> ITEMS
    MEMDOMAIN --> MEMORY
    STATSDOMAIN --> STATS
    STATSDOMAIN --> STORE
    STATSDOMAIN --> SESSION
    STATSDOMAIN --> PIPELINE
    SESSION --> STATS
    PIPELINE --> STATS
    CCTEL -- implements SampleSource --> STATS

    FSM --> METH
    RULES --> PROF
    RULES --> PRIVACY
    RULES --> MANIFEST
    RULES --> adapters
    MANIFEST --> adapters
    MANIFEST --> PRIVACY
    PIPELINE --> adapters
    CC --> AGENTS
```

The two `domains → core/rules` arrows are how a state machine's gates get
evaluated: a gate names a rule id, and the domain that owns the transition asks
the registry for the verdicts. `PipelineDomain.advance` has worked this way
from the start; `SessionDomain._advance` joined it when AUD-05 found the
session machine's gates were read by two hardcoded CLI call sites instead of by
the one function every transition passes through. `MANIFEST → PRIVACY` carries
exactly one name — `DEFAULT_PATTERNS`, the single authoritative default
pattern set — so the manifest's default and a bare `PrivacyScanner()` cannot
drift apart (AUD-15).

**Dependency rule:** arrows point down and never back up. `core/` knows
nothing about hosts or the CLI. `adapters/` know their ecosystem and nothing
about state. `hosts/` know their agent host, implement the protocols `core/`
defines, and reach the engine only through the CLI. This is what makes the
Claude Code binding replaceable.

`core/ → adapters/` is downward and therefore allowed, and it is how a rule
asks a question only an ecosystem can answer. `coverage.meets_floor` is the
case that shows the shape: an absent coverage row means either "never
measured" — the thing the gate exists to catch — or "nothing to measure",
which a bare protocol or an enum of cases can never escape. Only something
that knows the language can tell those apart, so the rule asks
`Adapter.carries_executable_code`, and an adapter with no opinion answers
`None`, which relaxes nothing. The rule keeps the policy; the adapter supplies
the fact. (`MANIFEST → adapters`, for command and rule-level defaults, is the
same arrow and predates it.)

What the adapter owes is a fact about the ECOSYSTEM: its language, and which
of its file kinds a compiler ever sees. The Swift one has been wrong about the
first half twice, in the same direction both times: it read a declaration as a
statement. A stored-property-only type was the first case
(BUG_025); a type-level constant — `static let name = Notification.Name("…")`
in an extension — was the second, and the file that exposed it is seven bare
protocols and four such constants, absent from all ten targets of the consuming
project's coverage report. The discriminator is not "is there an initialiser"
but WHERE the binding lives: a `static` or global binding is emitted as a
lazily initialised global and earns no row, while an INSTANCE stored property's
initialiser is emitted into every `init` the type has and does. Anything the
reader cannot resolve to a literal — a call to a lowercase member, a value read
from another symbol, arithmetic, a closure — goes on meaning "carries code",
because this answer's asymmetry runs the same way as the rule's: a spurious
block is visible, and a file wrongly called inert passes unmeasured code in
silence.

The second half is coarser and was simply missing. That reader answers about a
Swift file's CONTENTS, so every other path answered "cannot tell" — correctly
refusing to relax, and leaving a consuming project blocked over `Info.plist`,
a kind `xccov` cannot report on however it is written, in a change where the
property list was the load-bearing edit and the production Swift count was
zero (BUG_039). Both of the gate's own suggestions were dead ends: no test
clears it, and dropping the file from the declared set hides the change in
order to describe it. So the adapter now answers the kind question too, as an
**allowlist** of resource types — property lists and entitlements, strings and
string catalogs, storyboards and nibs, asset catalogs, build configuration
files — plus the directory bundles whose every payload is a resource, which is
the half that matters in practice: a touched path more often names a file
several components inside a catalog than the catalog itself.

Three properties keep it as narrow as the reader beside it. Compiled kinds are
listed explicitly and tested first, so neither a later allowlist entry nor the
directory rule can reach an `.m` or a `.h` that `xccov` really does measure.
An extension absent from the allowlist answers `None`, because inverting this
into "any extension I do not recognise is inert" is precisely the silent pass
the base `None` exists to prevent. And documentation and configuration
suffixes are deliberately absent from it: `core/rules` already holds `.md`,
`.rst`, `.json`, `.yaml` and `.toml` out of the changed-source set for every
ecosystem, and the same judgement written down in two layers is the
duplication that drifts.

An absent row has a *third* cause, and it is about the project rather than the
file: the reporter was configured never to look there. A Python project setting
`[tool.coverage.run] source` to its package directory can never produce a row
for a test file, so a session touching its own tests was handed a demand no
work could satisfy (BUG_019). That is the same seam and the same shape —
`Adapter.measured_scope` supplies the reporter's configured directories, `None`
means "cannot tell" and relaxes nothing, and `coverage.meets_floor` keeps the
policy of what to do about it.

The two questions must stay separate. A test file *does* carry executable code,
so answering `carries_executable_code` with `False` to relax the gate would be
a lie told to a rule — the exact failure the base `None` exists to prevent.
What is true of a test file is that nothing was ever pointed at it, which is a
different fact and now has its own question.

Note that `measured_scope`'s asymmetry runs the *opposite* way to
`carries_executable_code`'s, because its answer is used to shrink what the gate
demands rather than to explain one file. Overstating the scope costs a spurious
block, which is visible; understating it excuses a file the reporter really
does measure. So an adapter that cannot resolve every configured entry must
answer `None` rather than the part of the scope it worked out.

A *fourth* question is about neither the file nor the project but the CHANGE:
a diff that rewrote only comments altered no line a coverage run could measure,
so the floor is asking it for work that cannot move the number being demanded
(BUG_026). `Adapter.executable_text` reduces a revision to what a coverage tool
would count, the rule compares the base and working revisions through it, and
`None` relaxes nothing as before. It carries `measured_scope`'s asymmetry, for
the same reason — reducing too little costs a spurious block, reducing too much
calls a real code change cosmetic. The Swift implementation is where that has
teeth: it must copy string literals VERBATIM, because the reader that answers
`carries_executable_code` blanks their contents and would make `"a"` and `"b"`
compare equal.

Alongside it sits the one lever that is policy rather than fact —
`methodology.gates.coverage_exclude`, glob patterns a project states in its
manifest. Both hold paths out of the FLOOR and never out of the MEASUREMENT,
both run AFTER the no-row check so neither can hide a path-spelling mismatch,
and both name what they set aside in the verdict. An exclusion nobody can see
is indistinguishable from a gate that did not run.

A third lever answers a question neither of those can: the floor is right for
new code and wrong for old. A file months below it blocks a two-line, fully
covered, net-negative edit, so the gate refuses precisely the change that
improves it. `methodology.gates.coverage_baseline` names a recorded per-file
shortfall — the same idiom as `scripts/arch_layering_baseline.txt` — and the
floor is RATCHETED against it: at or above the record is a pre-existing
shortfall and does not block, below the record is a regression and gets its own
verdict rather than the floor's, and no record at all fails exactly as before,
because the relaxation must rest on evidence somebody wrote down. It is asked
LAST, of files the floor has already rejected, so a record cannot buy a pass for
a path with no coverage row and a stale entry cannot become a second, higher
floor for a file that already clears the first. A record read only in PART is an
error, never a smaller record: a dropped entry stops being enforced and stops
being honoured at the same time, with nothing in any verdict to say so.

The two labelled arrows around `stats` — `CLI -. injects .-> CCTEL`, dotted,
and `CCTEL -- implements SampleSource --> STATS`, solid — are the same rule
applied to a case that could easily have inverted it (ADR-002 D2). Reading a host's
transcripts is host knowledge, and the arithmetic over what is read is not —
so `core/stats` declares a `SampleSource` protocol, `hosts/` implements it, and
the CLI injects the implementation. Had the domain imported the reader
directly, the arrow would have run `domains → hosts` and the binding would have
stopped being replaceable. The precedent is `core/items`, which already defines
a model with pluggable backends.

`domains/stats → domains/session` and `→ domains/pipeline` are horizontal, not
upward. The join from a host session to an invariant session is knowledge of
*what a session is*, which this document reserves to a domain; putting it in
the CLI would have traded a documentation edit for an architecture violation.

## Component responsibilities

### `core/`

| Module | Owns | Never does |
|---|---|---|
| `fsm` | Loading a declarative state-machine definition; validating a proposed transition; computing legal next states and pending gates | Persist anything; know what a session or a bug is |
| `store` | Atomic, concurrency-safe JSON persistence under the state root; locking the cross-session registry | Interpret the contents |
| `manifest` | Parsing and validating the project manifest; merging profile defaults with per-rule overrides; resolving adapter selection | Read state; run commands |
| `rules` | The rule registry: each rule is an id, a bound host event, an evaluation function, and a level | Decide policy — levels come from the manifest |
| `items` | The work-item model and the pluggable store backends that read and write it | Know an item's lifecycle — that is `domains/workitem` |
| `memory` | Memory file schema, parsing, dedup detection, index generation, link validation | Decide what is worth remembering |
| `privacy` | Pattern-based detection of personal data in text; the exemption marker and how far it reaches | Know which files matter |
| `stats` | Telemetry record types, the price table, the arithmetic, and the `SampleSource` / `WatermarkSource` protocols | Read a transcript; know any host's on-disk format |

### `domains/`

Each domain binds a state machine to a store and exposes the operations that
advance it. They are the only place where "what a session is" is encoded.

- **`session`** — identity, isolation, the push barrier, teardown. Multiple
  sessions coexist on one machine, so this is the only domain with genuine
  cross-process concurrency, mediated by the locked registry.
- **`pipeline`** — one task's progress through the configured methodology.
  Scoped to a session; a session runs pipelines in sequence.
- **`workitem`** — ideas and bugs, their statuses, and the transitions between
  them. Persisted through `core/items` into whichever backend the project uses.
- **`memorystore`** — validation, dedup, and index regeneration over the
  memory directory. Not an FSM; a set of invariants plus repair operations.
- **`stats`** — what a session took and what it cost. Not an FSM either, for
  the same reason `memorystore` is not: there is no lifecycle to advance, but
  there is a store with invariants over it and operations that repair them.
  Owns the join from a host session to an invariant session, the rebuildable
  raw store, and the rendered report. See ADR-002.

### `adapters/`

An adapter supplies ecosystem knowledge the manifest should not have to spell
out: how to build, how to test a single unit, how to test SEVERAL, how to read
a coverage number, what that coverage run was even pointed at, how to tell a
real failure from a tooling failure. Adapters are selected in the manifest and
may be stacked; `generic` is the always-present fallback that executes literal
commands from the manifest and parses nothing.

`join_selectors` is the newest of those questions and carries the layer's
usual asymmetry. Whether several test selectors are a space-separated list of
positional arguments or a repeated FLAG is a fact about the tool the manifest's
`commands.test_one` template invokes, and the template is a project's to
rewrite — so an adapter with no confident answer returns `None`, the domain
refuses the request, and the caller runs them one at a time. Guessing would
build a well-formed command against the wrong scope, which is the exact defect
the question exists to close: a repeated `--selector` used to keep only the
last, and report a healthy green for a scope nobody asked for (BUG_031).

The `swift_xcode` adapter exists first because the originating project needs
it, and because it carries the most instructive ecosystem traps — a build tool
whose exit codes can silently drop whole test suites, and a destination
argument whose convenient form resolves to the wrong thing.

### `hosts/`

A host adapter translates between an agent host's extension points and the
CLI. For Claude Code that means hook payload parsing, the hook JSON response
shape (`permissionDecision`, `additionalContext`), agent and command file
installation, and `settings.json` wiring.

`hosts/` own hook *transport* — payload parsing, response shape, settings
wiring. A normalised payload dict travels upward, and `cli/` and `core/rules`
read documented keys from it; no layer above `hosts/` knows the hook
*protocol*. The keys are the contract, and they are listed in exactly one
place — `core.rules.NORMALISED_PAYLOAD_KEYS` — so that this document and the
host adapters cannot drift apart about what a host owes the engine.

This rule replaces a stronger one, "Nothing above `hosts/` is aware of hook
schemas", which the code had already departed from. `Context.session_for_payload`
reads `tool_input.file_path` and `RuleContext` reads `tool_name`, `tool_input`
and `target_path`; all are above `hosts/`. The departure was deliberate and a
shipped fix depends on it — BUG_010's fix (faa4de0) attributes a hook's
evidence to the right project *by the path the call itself names*, which is
only available from the payload. Leaving the old sentence standing meant the
authoritative map stated a rule a reader enforcing it would have used to
"correct" a working fix by reverting it (BUG_022). What a host adapter owes is
now an explicit normalisation obligation rather than an implied one, which is
what a second agent host would need in order to be written at all. The
restatement is recorded as ADR-003 D4.

`claude_code_telemetry` is a second, deliberately separate module for the same
host. It is a different relationship: `claude_code` *drives* the host through
hooks and installed assets, while this one *reads back* what the host already
wrote down — its per-project JSONL transcripts. A reader has no business in a
module about hook payload schemas, and the split keeps a change to one from
touching the other.

It is also the library's only dependency on another program's **undocumented**
format, and that shapes the code: every field access tolerates absence, an
unparseable record is skipped rather than fatal, and the file is a cache keyed
on a content fingerprint. A format change degrades a report and never breaks a
session.

Narrowing those transcripts to one project is where that format bites hardest.
The host names a project directory after the path it ran in, with separators
replaced — a **lossy** mangling, in which `<root>/app/v2` and `<root>/app-v2`
are one string. A worktree of the project must be counted and an unrelated
sibling checkout must not, so the reader resolves the ambiguity with evidence
rather than with the name: the filesystem for paths below the project, and
`core/git` for the worktrees placed beside it. Both are downward reads for a
fact only they hold, the same shape as `core/ → adapters/` above.

**The narrowing runs in both directions, and the origin is resolved upward
first.** A session worktree is a *place the project is being worked on*, not a
project, so its report is the project's: `ClaudeCodeTranscripts.roots()`
resolves the origin up to the repository's main checkout before narrowing.
Without that, an origin inside a worktree narrowed to the worktree — whose
mangled name is *longer* than the parent's — so the parent's transcripts failed
the prefix test and nothing above the worktree was visible at all. Since the
methodology mandates that a session run isolated, per-state attribution had
effectively never functioned under the only workflow the library permits
(BUG_017). Resolving upward hands the narrowing the main checkout's prefix,
which is the input the main-checkout case always got, so a sibling checkout
stays excluded by the same evidence (BUG_015).

One asymmetry survives deliberately. A torn-down worktree placed by the default
sibling convention (`<root>-wt-<id>`) loses its attribution permanently: the
path is gone, git no longer lists it, and its single-dash name is not the
hidden-directory spelling the naming heuristic recognises. A torn-down worktree
under the hidden worktrees directory stays attributed. That is evidence-honest
rather than a second defect — the alternative is admitting a name no evidence
supports, which is how BUG_015 happened.

## The two flows that matter

### Enforcing a transition

```mermaid
sequenceDiagram
    participant H as Claude Code
    participant S as shim (bash)
    participant C as CLI
    participant R as rules
    participant ST as store

    H->>S: PreToolUse (Edit) + payload on stdin
    S->>C: invariant hook pre-tool-use
    C->>ST: read session + pipeline state
    C->>R: evaluate rules bound to this event
    R-->>C: verdicts, each with a level
    C-->>H: hook JSON — deny (block) / context (warn) / silent (off)
```

A rule at `block` returns a denial the host enforces. The same rule at `warn`
returns the same reasoning as injected context and lets the call through. This
is why D10 and D11 compose: level is a property of the verdict's *delivery*,
not of the rule's logic, so a project retunes policy without touching code.

### Injecting state instead of rules

```mermaid
sequenceDiagram
    participant H as Claude Code
    participant C as CLI
    participant P as pipeline
    participant F as fsm

    H->>C: UserPromptSubmit
    C->>P: current pipeline state
    P->>F: legal transitions + pending gates from here
    F-->>C: {state, next[], blocked_by[]}
    C-->>H: additionalContext — where you are, what is legal, what is owed
```

The injected text is a projection of state. It is short because it describes
one node of a graph rather than the whole rulebook, and it cannot go stale
relative to what actually happened, because it is read from the record of what
actually happened.

## Where things live at rest

```
<project>/
  .claude/
    invariant.yaml          the manifest — the only project-aware artifact
    invariant/
      state/                gitignored; per-session JSON + locked registry
        stats/              one raw telemetry record per host session;
                            REBUILDABLE from the host's transcripts, except
                            where `stats archive` has promoted it
    hooks/                  thin shims that exec the CLI
    agents/                 \
    commands/                > managed host assets: library copies carry an
    skills/<name>/SKILL.md  /  in-frontmatter content hash; project-owned files
                               are checked for LOADABILITY but never for drift
  docs/                     architecture docs the docs-first gate reads
    conf.py                 Sphinx config (MyST + mermaid); renders docs/ as
                            one site — architecture, ADRs, audits. `docs/_build/`
                            is gitignored: the site is derived, not committed.
    reports/                paths.reports — the rendered, committed telemetry
                            report. Scrubbed: session names and bare dates,
                            never a host identifier (ADR-002 D7)
```

Configuration and knowledge are committed. Runtime state never is — it is
machine-local, session-scoped, and would conflict constantly between
concurrent sessions.

The report store is the one place those two meet, which is why it is declared
in the manifest rather than assumed: the raw records stay in the private state
root and the *rendering* of them is committed, so what leaves the machine is
decided by a renderer that can be read rather than by a directory somebody
remembered to gitignore.
