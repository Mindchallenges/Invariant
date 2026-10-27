# ADR-012 — The guidelines domain, and the model-agnostic contract it rests on

- **Status:** Accepted. IDEA_031 deliverable A0. The user ruled on all
  thirteen questions on 2026-10-27 ("Rulings" below), overriding one
  recommendation: the ratchet is always pure, and there is no Boy-Scout switch
  (ruling 5, D6). Nothing is built yet; A1–A8 build it in the order the
  consumption table gives.
- **Date:** 2026-10-27
- **Supersedes:** nothing. Extends ADR-001 (scripted state over prose rules)
  from the workflow to the coding guidelines themselves, and ADR-003 (where an
  agent-role gate may sit) from "an agent ran" to "each rule received a cited
  verdict". Adds a domain beside `memorystore` and `sweep`, a ruleset file, a
  baseline file and a family of `guidelines.*` rules. Settles IDEA_031's open
  questions on vague verdicts and on where regex stops.

## Context

A project's coding guidelines are still prose. A downstream iOS project keeps
39 compliance items (a two-part Boy-Scout rule and rules 1–37) in one
reStructuredText checklist that two agents are told to read. The design agent
enforces the build-time subset; the review agent marks every rule `[BLOCKER]`,
`[WARN]`, `[OK]` or `[N/A]` in its report. Everything ADR-001 said about
prose-as-control-flow is true of that checklist:

1. **Nothing records that a rule was applied.** The report is text the parent
   relays. No artifact says which rules were evaluated against which lines.
2. **A verdict cannot be audited against the diff.** `[OK]` on "views never
   touch concrete services" carries no citation, so a reader cannot tell a
   checked rule from a skipped one.
3. **Rule edits are ungated.** The checklist is a document anyone edits on any
   lane. The agent copies that read it were edited by hand too, and BUG_051
   shows what an install did to those edits.
4. **The outcome depends on the model.** Which rules get applied, and how
   carefully, varies with whichever model reads the prose that day. The engine
   has no way to tell a careful run from a careless one.

The user's standing goal answers the fourth point, in their words: "my goal was
always to have a model agnostic tool that can follow the steps regardless of
the LLM behind". Determinism comes from the engine, the gates and the
evidence, never from pinning a model. The shipped `compliance-reviewer`
currently carries `model: sonnet` in its frontmatter, which is a model id a
shipped asset depends on. That pin contradicts the goal and is removed by A6
and A7 below.

### What the checklist is made of

Deliverable B1 sorted the downstream project's 39 items by the check each one
needs, with live hit counts taken read-only:

| Kind | Count | Items | What it showed |
|---|---|---|---|
| Already owned by the engine | 4 | Boy-Scout scope, coverage floor, risk assessment present, simulator destination | The checklist restates gates the engine already runs. |
| Pattern, regex is enough | 7 | inline backlog TODOs, destructive SQL in migrations, Application Support writes, test frameworks in production code, a tag prefix on one import path, emoji, "removed" comments | Needs comment and string awareness, path scoping and reasoned exceptions. One destructive-SQL hit is a legitimate final drop; one emoji is a real data value. |
| Pattern needing a syntax tree | 5 | views using singletons, bare debug guards, identity text that must be tappable, eager iteration over a collection, snapshots of nondeterministic views | Needs to know the enclosing declaration, not just the token. The singleton rule alone has about 73 hits in 56 files. |
| Co-change or registry | 5 | structural change needs docs, a container member needs its two mocks, a new screen needs a design handoff, a production edit needs a test diff, a file in an open duplication entry | "If X changes, Y must change too", or "if X is listed in registry R". The plan's three kinds had no home for these. |
| Judgement | 18 | data-safety patterns, store placement, cardinality invariants, reload and cache policy, robustness and design conformance, single source of truth, and others | Several reduce to "this invariant is backed by a test". |

Two lessons from B1 shape this record. First, the project is one Swift module
of about 1,070 files with one app target, so its layer boundaries are
folder-to-type references, not imports. A file-import scan cannot express
them; that needs a symbol index. Second, the ratchet is not optional: one rule
already has dozens of existing hits, and a gate that blocks on them on day one
gets switched off.

## Rulings (2026-10-27)

| # | Question | Ruling |
|---|---|---|
| 1 | Where the ruleset lives by default | `docs/guidelines/ruleset.yaml`, with the generated checklist beside it: project content, inside the `docs/` privacy scope, outside the default isolation exemptions (D3, D10). |
| 2 | Ruleset format | YAML, like the manifest and the machines. No new dependency (D3). |
| 3 | Check kinds and engines in v1 | The core set with no syntax tree: `engine`, `pattern` (regex), `cochange`, `judgement`, and `boundary` through the import graph where an adapter can answer. Syntax tree and symbol index are deferred, and the rules needing them run as cited judgement (D4). |
| 4 | A single guideline's level | `severity: blocker \| warn` per rule in the ruleset, the family gates' levels in the profiles, and `invariant override` may name a guideline id as well as a rule id (D12). |
| 5 | Does touching a file forfeit its baseline | **Always a pure ratchet**, overriding the recommended `guidelines.boy_scout` switch. Touching a file never forfeits its baseline and only new findings block. Stale entries in a touched file are still pruned in the same change, so the baseline only shrinks. No manifest switch (D6, D13). |
| 6 | A vague verdict | Refused at submission, naming the predicate that failed. It never enters evidence, and the landing gate sees the rule as unanswered (D5). |
| 7 | Default levels | Split in `standard`: `ruleset_valid`, `baseline_only_shrinks` and `edit_in_lane` block; `mechanical_clean`, `verdicts_complete` and `generated_current` warn. `strict` blocks all but `generated_current` (D12). |
| 8 | What reaches the agent | Layered and capped: the managed block holds the rule index plus `always` rules; per turn, the index for the current role; per edit, the matching rules' statements (D3, D7). |
| 9 | Deny a blocker pattern at `PreToolUse` | No in v1. Mechanical findings are advisory at the edit and held at the landing gate (D7). |
| 10 | Tier to model | Tier names only, plus an optional operator-owned `host.tier_map`, empty by default, rendered by the host adapter (D9, D13). |
| 11 | The ruleset-edit lane | The `docs` methodology plus `guidelines.edit_in_lane`, and a cited ADR for a mechanically large edit (D10). |
| 12 | The harness's second host | A scripted CLI driver (no model) beside Claude Code run under at least two models (D11). |
| 13 | Render and prune against the CLI's write-scope rule | A third write class in cli.md: generated files the manifest declares, writable only by these commands and never exempt from isolation (D3). |

## Decisions

### D1 — The model-agnostic contract

The guidelines domain is built on five statements. Every later decision is
checked against them.

1. **A gate's verdict is a function of the ruleset, the tree and the recorded
   evidence.** It is never a function of which model, which host or which
   capability tier produced that evidence.
2. **The model and the host are recorded for audit only.** Evidence carries
   them when the host can say (`agent_type` already travels this way), so a
   reader can see who did what. No rule reads them. A7 ships a test that
   enforces this: an import-and-attribute scan over `core/rules` and
   `core/guidelines`, plus a property test that permutes the recorded model and
   host fields over a fixed evidence set and asserts identical verdicts.
3. **What an agent needs from a model is a capability tier, never a model
   id.** A tier is a hint the host adapter may act on (D9). No shipped asset
   names a model.
4. **Judgement stays a model's, and becomes checkable.** A judgement rule is
   answered by a structured, cited verdict (D5). The engine never reads prose
   to decide anything. It checks the verdict's structure, its citations against
   the tree and the diff, and its completeness against the ruleset.
5. **Conformance is proved, not asserted.** A harness drives fixed scenarios
   through the pipeline under different models and hosts, and the gate
   outcomes must match a known answer on every run (D11).

This is ADR-001's inversion applied one level down. ADR-001 replaced "remind
the model of the workflow" with "record the workflow's state". This record
replaces "remind the model of the guidelines" with "record a verdict per
guideline that the engine can check".

### D2 — Ownership: invariant owns format, lifecycle and enforcement; the project owns content

| Owned by invariant | Owned by the project |
|---|---|
| The ruleset schema and its validation | Every rule's id, wording, rationale, scope and severity |
| The check kinds and their evaluators | The patterns, globs, co-change pairs and registry entries |
| The verdict schema and its validation | The verdicts themselves, written by the project's agents |
| Rendering: the human checklist and the managed instruction-file sections | Everything in the instruction file outside a managed section |
| The baseline format and the ratchet | The baseline's entries |
| The lane a ruleset edit must take | Whether to make the edit |
| Host-neutral agent definitions | Overlays on them, and project-local agents |

The manifest stays the operator's enforcement configuration: which ruleset,
how strict each `guidelines.*` gate is, which tiers the agents run at. Rule
content does not go in the manifest. That keeps one owner per file, which is
the reason the manifest says it is "the only project-aware artifact" for
configuration rather than for content.

### D3 — One machine-readable ruleset per project; everything a human reads is generated from it

**One file.** A project declares its guidelines in one YAML ruleset (ruling
2), by default at `docs/guidelines/ruleset.yaml` with the generated checklist
beside it (ruling 1). YAML because the manifest and the state machines are
YAML, it needs no new dependency, and it is diffable. Under `docs/` the
ruleset is project content reviewed like a doc, inside the `docs/` privacy
scope and outside the default isolation exemptions (D10). Schema version 1:

```yaml
version: 1
sections:
  - id: di
    title: Clean architecture and dependency injection
rules:
  - id: di.views-no-concrete-services     # stable, project-chosen, never reused
    section: di
    title: Views never touch concrete services or singletons
    statement: >
      A view never reaches a concrete service or a shared singleton. It takes
      what it needs through the injected dependency container.
    severity: blocker                     # blocker | warn
    phases: [design, review]              # state roles that see it (D7)
    applies_to: ["App/UI/**/*.swift"]     # globs, normalised by project_relative
    check:
      kind: pattern                       # engine | pattern | boundary | cochange | judgement
      engine: regex
      in: code                            # code | comments | strings | any
      match: '\.shared\b'
    exceptions:                           # reasoned, path-scoped, never silent
      - path: App/Composition/LiveDependencies.swift
        reason: the composition root is where concrete services are built
    rationale: >
      The view layer must be previewable and testable without live services.
    links: [docs/architecture/overview.rst]
    amended:
      - on: 2026-07-07
        note: widened to cover property wrappers
        adr: docs/adr/adr-012.rst
```

`rationale`, `links` and `amended` exist because B1 found the project's rules
carry amendment history and ADR references. The generated checklist keeps
them, so moving to a ruleset loses nothing a reader relied on.

**Generated, never hand-edited.** Two kinds of output are rendered from the
ruleset by `invariant guidelines render`:

- **The human checklist**, one file in the project's `docs.format` (rst or
  markdown), at a path the manifest names. It carries a header marker holding
  the digest of what was rendered, in the shape library assets already use
  (`# invariant-managed:` with a digest), so a hand-edit is detectable. This
  replaces the hand-written checklist and is what the docs-first read gate
  sees.
- **Managed sections in the host's instruction file.** For Claude Code that is
  `CLAUDE.md`. Another host names its own file through its host adapter, so the
  instruction file is a host question, not a library constant.

**Write scope (ruling 13).** The CLI's rule is that no command writes outside
the state root and the declared stores. Render and `baseline --prune` write
neither, so cli.md names a third write class: generated files the manifest
declares (`guidelines.checklist`, `guidelines.instruction_files`,
`guidelines.baseline`). Only these commands write that class, and it is never
exempt from isolation, so a render or a prune goes through a session's review
like any edit. Rendering to stdout for an agent to write would make fixing
drift a model's act; making them declared stores would exempt them from
isolation and let generated edits land unreviewed.

**How managed sections coexist with hand-written text.** A managed section is
a marker-delimited block:

```markdown
<!-- invariant:begin guidelines/index digest=3f2a9c1b7e44 -->
...rendered text...
<!-- invariant:end guidelines/index -->
```

- Everything outside a begin/end pair is the project's, and no command reads
  or rewrites it.
- Render rewrites only the text between a pair it finds. It never inserts a
  block unasked: the first insertion is an explicit `--insert <name>` that
  appends the block, and the operator moves it where they want it. After that
  the block is found by its name wherever it sits.
- The digest on the begin marker is the digest of the rendered body. A body
  that no longer matches it was edited by hand. The same three-way split as
  `classify_installed` applies (cli.md, "A managed file the operator has
  edited"): current, edited locally (skipped and named; `--overwrite-local`
  takes the rendered text back), or rendered from an older ruleset (rewritten).
- A malformed pair (a begin with no end, two blocks with one name, a nested
  pair) is an error that names the line. Render writes nothing to that file.
- The managed block contains the rule index (id and title) plus the few rules
  marked `phases: [always]`, not the whole checklist (ruling 8). ADR-001's point was that a static rulebook in the
  instruction file is the failure mode. Full rule text arrives scoped to the
  phase instead (D7).

**Drift is a rule.** `guidelines.generated_current` compares each generated
file and managed block with what render would produce now. It is a `doctor`
row and a landing gate. It warns by default, because by the time it can see a
hand-edit the edit has happened. Its fix is always "move the change into the
ruleset, then render".

**A ruleset that does not validate fails.** `guidelines.ruleset_valid` refuses
an unknown key, a duplicate rule id, an unknown kind, a pattern that does not
compile, a glob list given as a bare string (the AUD-08 and coverage-glob
precedent), a `cochange` whose `then` names nothing, a judgement rule with no
`evidence` declaration, and an `engine` entry naming no registered rule (with
the nearest real id, as `enforcement.rules` does). It is a `doctor` row. Every
gate that needs the ruleset fails rather than skips when it does not load, as
a pinned machine that cannot load already does.

**No ruleset, no change.** With no ruleset configured every `guidelines.*`
rule answers N/A, nothing is injected and nothing is rendered. Every existing
project is unaffected until it opts in.

**The ruleset is pinned per pipeline.** Its content hash is recorded on the
pipeline at `start`, like the methodology (AUD-02, AUD-03). A mid-pipeline
ruleset edit does not change the running pipeline's obligations, and every
verdict and finding records the hash it was judged against (A4).

### D4 — Five check kinds; v1 ships four of them in full and one in part

| Kind | Question it answers | Evaluated by | v1 |
|---|---|---|---|
| `engine` | none of its own; it points at an existing registry rule | that rule | yes |
| `pattern` | does a match exist in the changed files, in scope? | `core/guidelines` with the adapter's lexical mask | regex: yes. Syntax tree: deferred (ruling 3) |
| `boundary` | does code under A reference code under B? | an adapter dependency question | import graph where the adapter can answer: yes. Symbol index: deferred (ruling 3) |
| `cochange` | if X changed (or is listed in registry R), did Y change too? | `core/guidelines` over the one changed set | yes |
| `judgement` | does the change honour this rule? | a model, through a cited verdict (D5) | yes |

**`engine`** removes duplication rather than adding a kind of check. Four of
the 39 items restate gates the engine already runs. The entry renders the
project's wording into the checklist and points at the rule
(`check: {kind: engine, rule: coverage.meets_floor}`). There is no second
implementation to drift, which is the single-source rule the checklist itself
states.

**`pattern`** runs a regex over the text of each changed file in
`applies_to`. Three properties come from B1:

- **Comment and string aware.** The adapter supplies a lexical mask that
  blanks comments and string literals, or keeps only them. The Swift adapter
  already has the string-literal reader `carries_executable_code` uses, so the
  mask reuses one reader rather than adding a second. A rule chooses
  `in: code | comments | strings | any`: an inline-TODO rule reads comments, an
  emoji rule reads everything. An adapter that cannot mask answers `None`, and
  a rule needing a mask then fails as unanswerable rather than matching raw
  text. A silent widening of scope is the defect the adapter seam exists to
  prevent.
- **Path scoped**, through the same `project_relative` and glob matcher every
  other declared glob uses, so the ruleset cannot disagree with the manifest
  about what a pattern names.
- **Reasoned exceptions.** An exception names a path (and optionally a line
  fingerprint, as in D6) and a reason. Every exception that applied is named
  in the verdict, in the way `non_source` and `coverage_exclude` already name
  what they set aside.

**Where regex stops** (IDEA_031's open question). Regex is the right engine
when the violation is a token sequence inside one file, after masking, and its
scope is a path glob. It stops when the rule needs any of three things:

1. **Structure**: which declaration encloses the match. "`Text(name)` inside a
   view body must be tappable" and "an eager loop over a full collection" need
   a syntax tree.
2. **Type resolution**: whether `.shared` belongs to a concrete service type.
3. **Cross-file reference**: which layer a referenced symbol is declared in.
   This is the downstream project's boundary case.

The first needs a syntax tree. The second and third need a symbol index. The
ruleset says which engine a rule uses (`engine: regex | syntax | imports |
symbols`), so a rule that has outgrown regex is visible in the file, not
hidden in a regex that approximates structure. `ruleset_valid` refuses an
engine the configured adapters do not offer, and in v1 that is every `syntax`
and `symbols` rule (ruling 3). Until a deferred engine ships, a rule needing
it is written as `judgement` with the same `applies_to`, so it is still
evaluated, with citations.

**`boundary`** in v1 is the import graph, answered by an adapter question
(`Adapter.references(path) -> set[str] | None`). The Python adapter can answer
from `import` statements. This repository's own layering ("nothing in `core/`
imports `oracle/`", "no `hosts` to `oracle` arrow") is checked today by
hand-written import scans in the suite; those become ruleset entries, so the
library uses this kind on itself first (ADR-001, Notes). A Swift module's
folder-to-type boundary needs `symbols` and is deferred (ruling 3).

**`cochange`** is the fourth kind B1 found. Two forms:

- **Pair:** `when` (a touched glob, or a pattern match added in a glob) and
  `then` (globs that must also be touched, or a path template derived from the
  trigger). It is evaluated over `core/rules._changed_set`, the one answer to
  "what changed" (BUG_012, BUG_047), and an unestablished changed set fails as
  it does for `docs.updated`. "A production edit needs a test diff" uses the
  adapter's `executable_text`, so a comment-only edit owes no test (BUG_026's
  seam).
- **Registry:** `when` a touched file is listed in a machine-readable registry
  file the ruleset names (YAML: path, status, scope), the outcome is chosen by
  the entry's fields, for example open and contained gives blocker, open and
  advisory gives warn, a scoped refactor gives "stop and ask". The engine does
  not parse a prose table; a project with its registry in prose converts it
  once, in the ruleset-edit lane.

**`judgement`** is D5.

### D5 — Judgement rules are answered by cited verdicts, and the engine checks the citations, never the prose

**The schema.** One verdict per applicable judgement rule, submitted through
the CLI, so any host, a script or a person can produce one:

```
invariant guidelines verdict <rule-id> --status pass|fail|na
    [--cite <path> <first-line> <last-line>]...
    [--test <selector>]...
    [--na-condition <condition-id>]
    [--note "<text>"]
invariant guidelines verdicts submit <file.json>     # the same, in bulk
```

```json
{
  "rule": "data.single-open-library",
  "status": "pass",
  "citations": [
    {"path": "App/Storage/LibrarySwitcher.swift", "lines": [42, 58], "blob": "9c41e07ab2d3"}
  ],
  "tests": ["AppTests/LibrarySwitcherTests"],
  "na_condition": null,
  "note": "close() runs before the next open on every path",
  "ruleset": "5be0c2d91f7a"
}
```

**What makes a verdict valid is mechanical.** The engine never reads `note`.
A verdict is accepted only when every one of these holds:

1. **The rule exists and applies.** It is in the pinned ruleset, its
   `applies_to` meets the change, and the current phase's role is in its
   `phases`.
2. **`pass` and `fail` rest on evidence.** At least one item of a kind the
   rule's `evidence` declaration allows: a citation, a test, or either.
3. **Each citation is real.** The path is in the change's touched set (or, for
   a rule declared `scope: file`, in the tree), the line range lies inside the
   file, and for a rule declared `scope: diff` at least one cited range
   intersects a line the diff adds or changes. The engine records the cited
   file's blob hash at submission.
4. **Each test is real.** It names a selector a recorded run in this pipeline
   included (`selectors` on run records, added by ADR-010 D1), and a `pass`
   resting on a test needs that run green.
5. **`na` is chosen, not written.** It must name one of the rule's declared
   `not_applicable_when` conditions. A condition is either `mechanical` (a
   glob or pattern test the engine evaluates itself, in which case the engine
   answers and no verdict is asked for) or a `claim` (a sentence the project
   wrote; the agent picks its id and the choice is recorded for audit and for
   the harness). There is no free-text N/A.
6. **The note is bounded and inert.** A length cap, privacy-scanned, recorded
   for audit, read by no gate.

**What "vague" means, mechanically.** A verdict is vague when it fails 2, 3, 4
or 5: no evidence, evidence that does not exist, evidence outside the change,
or an N/A the ruleset did not offer. That is the whole definition. The engine
does not judge whether the prose is convincing. That question belongs to the
conformance harness, which compares verdicts against known answers (D11).

**What happens to one.** It is refused at submission (exit 1, naming the
predicate that failed and the fix), so it never enters evidence (ruling 6). The
landing gate then sees the rule as unanswered. Refusing at the door keeps
evidence clean by construction, and the agent gets the refusal in the turn
where it can still fix it.

**Staleness.** A verdict whose cited file's blob has changed since submission
is stale, and the gate treats it as missing. The fix is a fresh verdict on the
new lines. A ruleset hash different from the pipeline's pin is the same case.

**The gate.** `guidelines.verdicts_complete` sits on the landing state's entry,
injected by role exactly as `agents.review_agent_ran` is (ADR-003, BUG_037).
It fails when any applicable judgement rule has no valid, current verdict, or
has a `fail` at blocker severity. A `fail` at warn severity is reported. The
gate does not ask which agent submitted a verdict. That is recorded, and
`agents.review_agent_ran` remains the rule that asks whether a review happened
at all. A verdict is evidence about the change, judged on its citations,
whoever wrote it.

**Mechanical kinds use the same record.** Pattern, boundary and co-change
findings are recorded in the same shape (rule, status, citations computed by
the engine), so a review report, the harness and an audit read one format.

### D6 — The ratchet: existing violations are baselined, new ones block, and the baseline can only shrink

**Format.** A baseline file the manifest names, one entry per line, with `#`
comments, modelled on `coverage_baseline` and the layering baseline idiom:

```
# rule-id                       path                       fingerprint   count
di.views-no-concrete-services   App/UI/Library/Grid.swift  3f2a9c1b7e44  2
```

The fingerprint is the first twelve hex digits (the BUG_090 length) of a hash
of the rule id and the matched line's text with whitespace collapsed. It is
deliberately not a line number: moving code does not create a new finding,
and editing the offending line does.

**Semantics.**

- A finding whose (rule, path, fingerprint) has a baseline entry with count
  left is pre-existing and does not fail the gate. The verdict names it as
  baselined.
- A finding with no entry is new and fails at the rule's severity.
- **Touching a file never forfeits its baseline** (ruling 5). An entry
  protects its finding whether or not the change touches the file; only new
  findings block. There is no mode in which a touched file's entries stop
  protecting it.
- A baseline entry for a touched file that no longer matches anything is
  stale. The change that fixed it must prune it in the same change
  (`invariant guidelines baseline --prune`); otherwise the gate fails. An entry
  left behind would later re-admit the same violation as "pre-existing".
  Pruning in the change that touched the file is what keeps the baseline
  shrinking under a pure ratchet: every fix retires its entry.
- **Only shrinks.** `guidelines.baseline_only_shrinks` compares the baseline
  in the change with the base branch's committed copy, read through `git` at a
  ref as `core/sweep` reads records. An added entry or a raised count fails.
  The one way to add entries is a ruleset edit that introduces or widens a
  rule, recording its initial baseline in the same change, through the
  ruleset lane (D10).
- **Read in full or not at all.** An unparseable line, an entry recorded twice
  or a configured file that does not exist is an error, never a smaller
  baseline, for the reason `coverage_baseline` gives: a record read in part
  stops enforcing and stops honouring with nothing to say so.

**Always a pure ratchet (ruling 5).** The downstream project's own rule is
"touch means own": a touched file must be fully compliant, pre-existing
violations included. The engine does not offer that as a mode, neither as a
default nor behind a manifest switch. A gate that turns a file's old findings
into blockers the moment anyone edits it punishes the edit, not the
violation, and makes the cheapest compliant change "do not touch the file".
The ratchet's guarantee is the one the engine can hold for every project:
nothing new gets in, and what is fixed stays fixed. A project that wants
Boy-Scout writes it as a cited judgement guideline in its own ruleset
(Consequences).

### D7 — Phase-scoped injection: a rule reaches the agent in the states whose role it names

Each rule lists the state roles it belongs to: `design`, `review`, `landing`,
or `always`. Roles, not state names, for the reason the docs-first gates are
injected by role: a methodology this library has never seen still receives
them when it labels its states.

What is injected, and where (ruling 8: layered and capped):

- **At the turn** (the `UserPromptSubmit` projection, `invariant context`):
  when the pipeline's current state carries role R, the index of rules for R
  that apply to the session's declared or touched files, as ids and titles,
  capped at a line budget. `invariant guidelines show --phase R [--files ...]`
  prints the full text on demand.
- **Just in time, on an edit** (`PreToolUse` on an edit tool whose target
  matches `applies_to`): the statements of the matching rules for the current
  phase, as context, capped. This is the point where the rule can still change
  the code at no cost.
- **The design agent** asks `guidelines show --phase design` for the
  build-time subset it applies while designing. **The review agent** asks
  `--phase review` for every applicable rule and answers each judgement rule
  with a verdict.

The engine computes the set (`core/guidelines.select(role, paths)`), and the
host adapter only transports text. That keeps selection host-neutral and makes
the injected text a projection of state, reproducible with one command, as
ADR-001 asks of everything injected.

Mechanical checks are evaluated at the landing gate. On an edit they report
advisory context ("this edit adds a new finding of X"). In v1 no finding
denies an edit, at any severity (ruling 9): the edit is advised and the
landing gate holds. A `cochange` finding could not be judged at the edit
anyway, since its second file may come later.

### D8 — Host-neutral agent definitions, rendered per host, with project overlays (A6)

**The source is host-neutral.** A library agent is a markdown body with
neutral frontmatter: `name`, `description`, `roles` (design, review, intake,
repro), `tier` (D9), and `capabilities` (read, edit, shell, dispatch) rather
than a host's tool names. The body speaks only to the invariant CLI and to the
ruleset (`guidelines show`, `guidelines verdict`), never to "read the
checklist document", which is what lets the ruleset replace the prose the
agents read today.

**The host adapter renders it.** For Claude Code that is `.claude/agents/
<name>.md` with `tools:` mapped from capabilities. It never carries a model id
unless the operator's `host.tier_map` supplies one (ruling 10, D9). Rendering stays under
`hosts/`. The host-neutral model and the overlay merge are host-agnostic, so
they live in a new `core/agents` module.

**Overlays replace edited copies.** A project names an overlay per agent
(`agents.overlays.<name>`), an ordered list of operations against named
sections of the shipped agent: append to a section, insert after it, replace
it. Shipped agents carry stable section anchors so an overlay does not depend
on a heading's wording. The installed file's managed marker records two
digests, the shipped source's and the overlay's, which separates three cases
the single digest cannot:

- the library changed: re-render;
- the overlay changed: re-render;
- the installed file was edited by hand: drift. `doctor` names it, and the fix
  is to move the edit into the overlay.

BUG_051's rule carries over: a hand-edited installed file is skipped and
named, never flattened. Project-local agents (`agents.local`) are untouched,
so a publisher bound to one app store stays the project's.

### D9 — Capability tiers are hints, never gates (A7)

`agents.tiers` maps an agent to `light`, `standard` or `deep`. The tier is
rendered by the host adapter as a hint. It is read by no rule, and the D1 test
enforces that. Model and host are recorded on evidence when the host reports
them, for audit, and appear in reports and the harness output. They never
appear in a gate's inputs.

The shipped agents lose any model id. `compliance-reviewer`'s `model: sonnet`
becomes `tier: standard`. A tier becomes a host-specific model choice only
through the operator's optional `host.tier_map` (ruling 10), which the host
adapter renders into its own field. It is empty by default, so every agent
runs on the host's default model, and no host adapter ships a tier-to-model
table of its own.

### D10 — A ruleset edit goes through its own lane (A8)

A change touching the ruleset, the baseline's additions, the registry files a
ruleset names, or an overlay is a policy change, and it is gated as one:

- **It runs in the `docs` methodology** (ruling 11), which already carries the
  read, update, privacy and agent gates, plus `guidelines.edit_in_lane`, which
  fails when the changed set touches the ruleset in any other methodology.
- **The ruleset must validate, and every generated file must be re-rendered
  in the same change.** `ruleset_valid` and `generated_current` are its
  landing gates in that lane.
- **A large edit cites an ADR.** Large is mechanical: adding or removing a
  rule, changing a rule's kind, raising a severity to blocker, widening
  `applies_to`, or adding baseline entries. Such an edit must carry an
  `amended` entry whose `adr` names a file that exists under the project's ADR
  directory. The engine checks that the file exists, not what it argues.
- **The ruleset is not exempt from isolation.** Its default location,
  `docs/guidelines/ruleset.yaml` (ruling 1), sits outside
  `session.exempt_paths`, whose default `.claude/*` would otherwise let a
  ruleset edit land on the base branch unreviewed. The generated files are
  never exempt either (D3, ruling 13).

### D11 — The conformance harness proves the contract (A1)

**Scenarios.** Small fixture repositories, each with a ruleset, a seeded
change and a known answer: the set of gates that pass, the set that block,
and the verdict status each judgement rule must receive. The first set:

| Scenario | Seeded | Known answer |
|---|---|---|
| clean | a compliant change | every gate passes |
| seeded pattern | one new pattern violation | `guidelines.mechanical_clean` blocks, naming the rule and line |
| baselined | the same violation, recorded in the baseline | passes, naming it as baselined |
| missing test | a behaviour change with no test diff | the `cochange` rule blocks |
| docs-first breach | an edit before any architecture read | `docs.read_architecture` blocks |
| vague verdict | a judgement verdict citing a line outside the diff | refused at submission; `verdicts_complete` blocks |
| judgement fail | a change that breaks a declared invariant | the judgement rule receives `fail` |

**Two layers.**

1. **Engine layer, no model.** Each scenario's evidence is replayed through
   the CLI and the gate outcomes are compared with the known answer. This
   runs in the suite on every change. It proves the engine is deterministic
   and is where the D1 permutation test lives.
2. **Driven layer, with models.** A host drives each scenario from its brief,
   with the shipped agents, under a named model. The harness compares only the
   final gate outcomes and the verdict statuses, never the prose. Each
   scenario runs several times per model. Any divergence from the known answer,
   or between runs, is a conformance failure attributed to the scenario and
   the model in the report.

The driven layer is run on demand or on a cadence, not on every change: it
costs model time, and it measures agents and rulesets, not the engine. Its
report records model and host for audit, consistent with D1.

**Hosts (ruling 12).** The acceptance criterion "at least two models and
hosts" is met by a scripted CLI driver beside Claude Code run under at least
two models. The driver has no model: it replays the agents' commands from the
scenario's brief, which proves the engine reaches the same outcomes with no
host-specific path. A second real host adapter can join later and is not a
precondition for A1.

**As built: A1 increment 1, the engine layer over today's gates.**

- *Where it lives (ruling DN1).* Scenarios ship as package data at
  `src/invariant/data/conformance/<scenario>/`: a `scenario.yaml` and a
  `tree/`. The hatch wheel and sdist already carry everything under
  `src/invariant`, so no packaging key changed. A scenario's manifest lives in
  `scenario.yaml`, not in `tree/`. A replay writes it into the seeded
  repository, so no nested project manifest sits inside this repository for a
  hook to resolve a path against.
- *The split.* `core/conformance` holds the scenario model (`Scenario`,
  `Step`, `Expected`), the loader and its refusals, the reduction of what a
  replay printed to an `Outcome`, the comparison and the rendering. It reads
  files and does nothing else. It never evaluates a rule: every status it
  sees comes from the `--json` payloads of `pipeline gates` and
  `pipeline advance`, so the gates are still evaluated in `PipelineDomain`
  alone. The replay drives the CLI in-process, and `core/` may not import the
  CLI, so the replay lives in `cli/conformance`, with the CLI entry point
  passed in as a parameter.
- *A replay.* The replay copies `tree/` into a fresh directory, writes the
  manifest and a `.gitignore` for the state root, runs `git init` and
  commits. It then runs the steps in order: `cli` (argv through
  `invariant.cli.main.main`, with `--json` added), `hook` (an event and a
  payload through `invariant hook <event>`), `write` and `commit`. A step
  runs `in: root` or `in: worktree`, the one linked worktree that a
  `session isolate --execute` step made. Payload strings may name `{root}`
  and `{worktree}`. The outcome is `{destination: {rule_id:
  pass|block|warn|skip}}`, plus the steps that were refused and the blocking
  rules each one named. `ask` reads as `block`. Each destination keeps its
  last observation. A known answer is compared exactly for every destination
  it names, and its refusals are compared whole.
- *Isolation.* For the replay's duration, every `INVARIANT_*` and `CLAUDE_*`
  variable is stripped. That covers `INVARIANT_HOST_SESSION_ID` and
  `INVARIANT_HANDOFF` (stripped for the same reason `child_env` strips
  them), `INVARIANT_STATE_ROOT`, and the host's markers. `INVARIANT_HOME`
  points into the replay's own directory, and git reads no global or system
  configuration. The caller's environment, working directory and stdin are
  restored afterwards, even when a step raises.
- *The first set.* Three scenarios are real against today's gates:
  - `clean` lands through every gate on `committed`. That set is seven gates,
    not six: `docs.diagrams_current` rides beside `docs.updated` in the
    landing role.
  - `docs-first-breach`: `docs.read_architecture` blocks designing to red,
    and the advance is refused.
  - `red-first-breach`: `test.new_test_fails` warns on the way to green. The
    standard profile holds it at warn, so the advance goes through.

  The fixture suite is a shell script that prints pytest's summary words, so
  the python adapter reads it. The change adds the fixture's only `.py` file
  through a `write` step, so the package data carries no Python source for
  this repository's own coverage floor to ask about.
- *Wording, against the table above.* The docs-first seed is really "no
  read". The scenario reads nothing, and makes no edit before the advance.
  The D1 permutation test belongs in the engine-layer module
  (`tests/test_conformance_engine.py`) from A4 or A7 onward, whichever lands
  it, because only then is there a recorded model or host field to permute.
- *Tests.* `tests/test_conformance_engine.py` holds:
  - a known-answer test per scenario;
  - the determinism test, which replays each scenario in two fresh trees and
    requires identical outcomes;
  - the loader's refusals (unknown key, unknown step kind, an expect naming
    an unregistered rule, among others);
  - the reduction, the comparison and the rendering.

  It runs in the ordinary suite.

**Rulings for the driven layer (DN2, DN2b, DN3).** The driven layer gets its
own command group: `invariant conformance run [--scenario …] --driver
script|claude_code [--model …] [--runs N]` and `invariant conformance show`.
The operator names the model, and no shipped asset names one (D1 item 3).
Driven-run reports are written under the machine-local state root and are
never committed. In v1 it runs on demand only:

- 3 runs per scenario per model;
- two models, named by the operator;
- a turn cap per run;
- divergences reported, never filed.

None of this was built in increment 1.

**Increments 2–6** are tracked on IDEA_031, not in a session plan:

| Increment | Adds | After |
|---|---|---|
| 2 | scenarios over the gates A2 adds | A2 |
| 3 | `seeded pattern`, `baselined` and `missing test`, over A3's checkers and baseline | A3 |
| 4 | `vague verdict` and `judgement fail`; the D1 permutation test | A4, and A7 for the permutation test |
| 5 | the scripted driver, `conformance run --driver script` and `conformance show` | the engine layer |
| 6 | Claude Code as the driver under at least two models | A4 and A6 |

### D12 — Where the domain sits

```mermaid
graph TD
    subgraph host["hosts/"]
        CC[claude_code]
        CCREN["agent rendering + injection transport (A5, A6)"]
    end
    subgraph cli["cli/"]
        CLI["invariant CLI<br/>guidelines render | show | verdict | baseline | check"]
    end
    subgraph domains["domains/ (seven)"]
        GDOM["guidelines (NEW, not an FSM)"]
        PIPELINE[pipeline]
        SESSION[session]
    end
    subgraph core["core/"]
        CGUIDE["guidelines (NEW): ruleset model, validation,<br/>selection, rendering, baseline arithmetic,<br/>verdict schema and checks"]
        CAGENTS["agents (NEW, A6): host-neutral model, overlay merge"]
        RULES["rules: guidelines.* family"]
        MANIFEST[manifest]
        GIT[git]
        STORE[store]
        PRIVACY[privacy]
        FSM["fsm / machines"]
    end
    subgraph adapters["adapters/"]
        AD["lexical mask, references,<br/>executable_text (existing)"]
    end
    subgraph data["shipped data"]
        AGENTS["agents/*.md (host-neutral source)"]
        PROF["profiles/*.yaml (levels for guidelines.*)"]
    end

    CLI --> GDOM
    CLI --> CGUIDE
    CLI --> CAGENTS
    GDOM --> CGUIDE
    GDOM -- records verdicts and findings --> PIPELINE
    GDOM --> STORE
    GDOM --> GIT
    GDOM --> PRIVACY
    GDOM --> adapters
    RULES --> CGUIDE
    RULES --> adapters
    CGUIDE -- project_relative --> MANIFEST
    CGUIDE -- base-branch baseline, diff lines --> GIT
    FSM -. injects guidelines.* by role .-> RULES
    PIPELINE --> RULES
    CC --> CGUIDE
    CC --> CAGENTS
    CCREN --> CAGENTS
    CC --> AGENTS
    CAGENTS --> AGENTS
    RULES --> PROF
```

- **`core/guidelines`** is pure apart from file and `git` reads, like
  `core/plan` and `core/sweep`, so `core/rules` can ask it questions without
  importing a domain. `CGUIDE --> GIT` is two reads: the baseline at the base
  ref, and the line numbers a diff adds (a new `git` query, `changed_lines`).
- **`domains/guidelines`** owns the effects: render (writes the generated
  files), baseline prune, verdict submission. `GDOM --> PIPELINE` is the
  verdict write into the pipeline record, horizontal, on the `stats --> session`
  and `sweep --> pipeline` precedent. `GDOM --> PRIVACY` scans the note and
  everything rendered before it is written.
- **`hosts/`** gains rendering of host-neutral agents and the transport of
  injected text. `CC --> CGUIDE` is downward, the same shape as `CC --> PLAN`
  and `CC --> RULES`: the turn projection asks `select` directly.
- **`core/machines`** injects the landing gates by role, as it injects
  docs-first and the agent-role gates.

**The rule family**, levels per profile (ruling 7). A project moves
`mechanical_clean` and `verdicts_complete` from warn to block once its
baseline is recorded, as IDEA_031's adoption plan does:

| Rule | Asks | Where | loose | standard | strict |
|---|---|---|---|---|---|
| `guidelines.ruleset_valid` | does the ruleset load and validate? | doctor, every gate that needs it | block | block | block |
| `guidelines.generated_current` | do the generated files match the ruleset? | doctor, landing entry | warn | warn | warn |
| `guidelines.mechanical_clean` | no new pattern, boundary or cochange finding over the baseline? | landing entry; advisory at PostToolUse | warn | warn | block |
| `guidelines.baseline_only_shrinks` | did the baseline only shrink? | landing entry | block | block | block |
| `guidelines.verdicts_complete` | does every applicable judgement rule have a valid, current verdict, and no blocker `fail`? | landing entry | warn | warn | block |
| `guidelines.edit_in_lane` | was a ruleset edit made in the ruleset lane? | landing entry | warn | block | block |

Every rule is N/A with no ruleset configured. The override route (ADR-001 D25)
is unchanged: `enforcement.rules` retunes a level, `invariant override` passes
one evaluation, and a stand-down holds them.

**A single guideline's level (ruling 4).** A guideline's weight is its
`severity: blocker | warn` in the ruleset; the family gate's level in the
profile decides what a blocker does. `invariant override` may name a
guideline id as well as a rule id, and a grant naming a guideline passes that
guideline's findings or verdict in one evaluation of the family gate, leaving
every other guideline under that gate in force. The rule registry stays fixed:
no guideline is minted as a registry rule.

### D13 — Manifest keys

```yaml
guidelines:
  ruleset: null              # path of the ruleset; null = no guidelines, every
                             # guidelines.* rule N/A. The documented location is
                             # docs/guidelines/ruleset.yaml (ruling 1). Never
                             # exempt from isolation (D10)
  baseline: null             # path of the ratchet baseline (D6); a configured
                             # file that does not exist is an error. Always a
                             # pure ratchet: there is no Boy-Scout key (ruling 5)
  checklist: null            # where render writes the human checklist, in docs.format
  instruction_files: null    # files carrying managed sections; null = the host
                             # adapter's default (CLAUDE.md for claude_code)
                             # checklist, instruction_files and baseline are the
                             # generated-file write class (D3, ruling 13)
  injection:
    max_lines: 40            # cap on injected guideline text per turn or edit (D7)
    on_edit: true            # just-in-time statements at PreToolUse
  verdicts:
    note_max_chars: 280      # the inert note's cap (D5)

agents:
  overlays: {}               # agent name -> overlay path (D8)
  tiers: {}                  # agent name -> light | standard | deep (D9); a hint,
                             # read by no gate

host:
  tier_map: {}               # tier -> the host's own model choice; read only by
                             # hosts/ when rendering, never by a gate (ruling 10)
```

Validation follows the manifest's existing rules: glob lists must be lists of
strings, a path that names nothing in the project is dropped rather than
matched, and a key the schema does not know is refused at load (BUG_050).
`config show` prints every value with its provenance.

## Increments, and the order in which A1–A8 consume these decisions

| Deliverable | Consumes | Can start when |
|---|---|---|
| A1 conformance harness | D1, D11; the scenario fixtures use D3's format and location (rulings 1, 2) and D5's schema (ruling 6); the driven layer's hosts (ruling 12) | A0 accepted. The engine layer grows as A2–A4 land; the driven layer needs A4 |
| A2 ruleset core | D2, D3 (format, location, validation, rendering, managed sections, drift, pin, the generated-file write class), D13's `guidelines.ruleset`, `checklist`, `instruction_files`; rulings 1, 2, 4 (`severity` in the schema), 8 (managed-block content), 13 | A0 accepted |
| A3 mechanical checkers | D4 (engine, regex pattern, import-graph boundary, cochange; where regex stops), D6 (baseline, pure ratchet, stale-entry prune), D12's override by guideline id, D13's `guidelines.baseline`; rulings 3, 4, 5, 7, 9, 13 (`baseline --prune`) | A2 |
| A4 verdict contract | D5 (schema, validity, vagueness, staleness, gate), the ruleset hash from D3; rulings 6, 7 | A3, because mechanical findings share the record shape |
| A5 phase-scoped injection | D7, D13's `injection`; rulings 8, 9 | after A4 |
| A6 host-neutral agents and overlays | D8, D13's `agents.overlays` | after A4; the agent bodies switch to `guidelines show` and `guidelines verdict` |
| A7 capability tiers | D1 item 2 (the no-gate-reads-it test), D9, D13's `agents.tiers` and `host.tier_map`; ruling 10 | after A4; the test can land with A4 |
| A8 gated ruleset edits | D10, D13's location rule; rulings 1, 11 | after A4 |

This is the order IDEA_031 already records: A1 alongside the downstream
classification, then A2, A3, A4, with A5, A7 and A8 after A4 in any order, and
the downstream agent migration after A6. The downstream adoption (its ruleset,
baseline and overlays) is filed in that project's own backlog from a host
rooted there.

## Alternatives rejected

| Alternative | Why not |
|---|---|
| Pin a model per agent for determinism | Contrary to the user's standing goal. Determinism from a model is determinism nobody can verify, and it breaks the day the model changes. |
| Keep the prose checklist as the source and parse it | A prose table has no schema to validate, and the engine would be parsing a document format it does not own. Converting once is cheaper than parsing forever. |
| Put rule content in the manifest | Mixes the operator's enforcement configuration with the project's guidelines, and puts content under `.claude/`, which is exempt from isolation by default. |
| One registry rule per guideline, minted from the ruleset | Makes the rule registry project-dynamic and multiplies `rules list` by every project's guideline count. A family gate with per-guideline severity, and an override that may name a guideline, keeps the registry fixed (ruling 4). |
| A `guidelines.boy_scout` manifest switch, off by default | The recommendation, overruled (ruling 5). A switch is an engine mode that turns a file's old findings into blockers the moment it is edited, which punishes the edit rather than the violation and rewards leaving a file alone. The pure ratchet already guarantees nothing new gets in and every fix is pruned. |
| Always touch-means-own | Stricter still: every project would owe a full clean-up of every file it edits, so a gate that strict is the one that gets switched off on day one (Context). A project that wants it writes it as a cited judgement guideline. |
| Accept a vague verdict and record it as `vague`, or with a warning | Puts invalid evidence on record for a gate or the harness to filter later. Refusing at the door keeps evidence clean by construction (ruling 6). |
| Deny a blocker pattern finding at `PreToolUse` | Blocks mid-edit on a state the edit may be about to fix, and a `cochange` cannot be judged before its second file. Advisory at the edit, held at landing (ruling 9). |
| A default tier-to-model table shipped by each host adapter | Puts model ids back into shipped assets, which D1 item 3 forbids. The operator's `host.tier_map` is the only route (ruling 10). |
| Render to stdout and let the agent write the files, or make generated files declared stores | The first makes fixing drift a model's act; the second exempts generated files from isolation, so they land unreviewed. A third write class keeps both reviewable (ruling 13). |
| A syntax tree or symbol index in v1 | An external tool per ecosystem before any project has adopted. The rules needing them run as cited judgement until the engines ship (ruling 3). |
| Let the engine judge whether a verdict's prose is adequate | That is the engine judging prose, which is a model by another name, inside a gate. Adequacy is measured by the harness against known answers. |
| Accept free-text N/A | It is the uncited `[N/A]` the current checklist already produces. A declared condition is the smallest change that makes it auditable. |
| Baseline by line number | Every unrelated edit above a finding would make it "new". |
| Inject the whole checklist into the instruction file | Recreates the static rulebook ADR-001 retired. |
| Regex for every boundary | Cannot see a Swift module's folder-to-type references; it would pass violations silently. |
| A new methodology for ruleset edits | The `docs` lane already carries every gate a policy edit needs. One rule is cheaper than one more machine to keep in step (ruling 11). |
| Claude Code only, under several models | Host neutrality would be argued, not tested. A scripted driver tests it today at no model cost (ruling 12). |

## Consequences

- A project with no ruleset sees no change. Every `guidelines.*` rule is N/A
  and nothing is injected or rendered.
- A project that adopts a ruleset gets its checklist and its managed
  instruction-file sections generated, drift reported, new mechanical findings
  held at the landing gate, and every judgement rule answered with a verdict
  the engine can check against the diff.
- The ratchet is always pure (ruling 5). Pre-existing findings never block,
  whether or not the change touches their file, and the baseline shrinks only
  as fixes prune it. A project that wants Boy-Scout ("touch means own") can
  express it as a cited judgement guideline in its own ruleset, for example
  a rule over its touched files whose verdict cites the baseline entries the
  change retired. It is not an engine mode. B1 counted the downstream
  project's Boy-Scout scope among the items the engine already owns; under a
  pure ratchet its touch-means-own half has no engine gate to point at, so
  that project's adoption decides whether to keep it as a judgement
  guideline.
- A review report stops being the only record. Verdicts and findings are
  pipeline evidence with citations and the ruleset hash, readable by an audit
  and by the harness.
- Judgement is still made by a model. The engine guarantees it was made, per
  rule, about real lines in the change, under the ruleset that was in force.
  It does not guarantee it was made well; the harness measures that, per model.
- The shipped agents change shape (host-neutral source, rendered per host, no
  model id), and edited agent copies in a consuming project become overlays.
  The first install after A6 reports each edited copy as drift with a fix.
- "The six state domains" becomes seven, and the overview, state model,
  manifest and CLI documents gain their sections as each increment lands, not
  in this record.
- Costs: a ruleset, a baseline and overlay files to maintain; a mask and a
  references question per ecosystem adapter; a driven conformance run that
  costs model time. A syntax tree and a symbol index are deferred, so until
  they land five pattern rules and the downstream boundary rules run as cited
  judgement rules.

## Siblings in the backlog

- IDEA_031: this record is its A0.
- BUG_051 (fixed) and BUG_021 (fixed, two items owed): background for D8.
  Overlays are the structural answer to the edited copies BUG_051 protected.
- BUG_043 (filed): reviewer prose about commit provenance. It is a fix to one
  agent's text and stands on its own. A6 will carry it into the host-neutral
  source unchanged.
- BUG_082 (filed): a subagent's doc reads credited to the parent's bound
  session. Verdicts are submitted through the CLI, resolved by working
  directory rather than by hook payload, so A4 does not depend on it.
- IDEA_033 and IDEA_034: unrelated to this record (selector joining and
  baseline filing in the sweep).
