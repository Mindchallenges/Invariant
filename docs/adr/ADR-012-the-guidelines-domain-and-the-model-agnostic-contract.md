# ADR-012 — The guidelines domain, and the model-agnostic contract it rests on

- **Status:** Accepted. IDEA_031 deliverable A0. The user ruled on all
  thirteen questions on 2026-10-27 ("Rulings" below), overriding one
  recommendation: the ratchet is always pure, and there is no Boy-Scout switch
  (ruling 5, D6). A1–A8 build it in the order the consumption table gives,
  and each increment's as-built note records what landed. The check-kind
  amendment was accepted the same day, with its own rulings table (DN-A1 to
  DN-A14).
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
the nearest real id, as `enforcement.rules` does). The check-kind amendment
adds the refusals of the three kinds' sub-schemas and of the registry files a
ruleset names. It is a `doctor` row. Every
gate that needs the ruleset fails rather than skips when it does not load, as
a pinned machine that cannot load already does.

**No ruleset, no change.** With no ruleset configured every `guidelines.*`
rule answers N/A, nothing is injected and nothing is rendered. Every existing
project is unaffected until it opts in.

**The ruleset is pinned per pipeline.** Its content hash is recorded on the
pipeline at `start`, like the methodology (AUD-02, AUD-03). A mid-pipeline
ruleset edit does not change the running pipeline's obligations, and every
verdict and finding records the hash it was judged against (A4).

**As built: A2 increment 1, the ruleset core.** A2 is built in six
increments, recorded on its session's plan: the ruleset core; validation of
the `boundary`, `cochange` and `judgement` kinds; the generated checklist and
`guidelines render`; the managed instruction-file sections; the
`generated_current` gate; and the pin. The first one landed as follows.

- *`core/guidelines`.* The model (`Ruleset`, `Section`, `Guideline`, `Check`,
  `GuidelineException`, `Amendment`), schema version 1, and the validation.
  A ruleset is refused whole. Every problem is collected and named, and none
  is dropped, for the reason the coverage baseline gives. It imports nothing
  from `core/rules`, which calls it, so the registered ids an `engine` check
  must name are passed in as a parameter. The content hash is the first
  twelve hex digits of the SHA-256 of the file's bytes (`digest_of`, the
  BUG_090 length).
- *What validates in this increment.* `engine` (its `rule` must be
  registered, and a near miss is named) and `pattern` with the `regex`
  engine. `syntax` and `symbols` are refused as deferred (ruling 3).
  `boundary`, `cochange` and `judgement` are refused with the reason that
  their schemas are not validated yet. A rule nothing has checked is never
  accepted. Their sub-schemas need rulings before increment 2 can build
  them, because the example above does not show them.
- *Choices the example left open, taken in the strict direction.* Each one
  can be relaxed later without breaking a ruleset that loads today.
  - Required on every rule: `id`, `section`, `title`, `statement`,
    `severity`, `phases`, `applies_to` and `check`. The optional keys are
    `exceptions`, `rationale`, `links` and `amended`.
  - A pattern must state `engine` and `in`; neither has a default.
  - An empty `applies_to` or `phases` is refused, because such a rule
    applies nowhere.
  - A rule id may not contain whitespace, because a baseline line and a
    CLI argument are both split on whitespace (D6).
  - An exception's `fingerprint` is twelve lowercase hex digits.
- *YAML 1.1 and `amended[].on`.* PyYAML reads a bare `on:` key as the boolean
  true. The loader reads that key back as `on`, so the schema keeps D3's
  spelling.
- *The manifest.* `guidelines.ruleset` is the only `guidelines` key this build
  reads. It defaults to null. D13's other keys are refused at load, by name,
  until the increment that reads each one lands. Rule content stays out of
  the manifest (D2).
- *`guidelines.ruleset_valid`.* The rule is graded `block` in all three
  profiles (D12's table). With no ruleset it is N/A. A configured path that
  names nothing inside the project, a missing file, and an invalid ruleset
  all fail; none of them skips. It reads the tree in hand, which is the
  session's worktree when there is one, and it never falls back to another
  checkout's copy. `core/machines` injects it at the landing state's entry
  (`GUIDELINES_GATES`), and only when a ruleset is configured, on the
  `sweep.fresh` precedent. That way a project that has not opted in sees no
  change, not a `[skip:BLOCK]` on every landing. `doctor` prints the rule's
  verdict as the `guidelines ruleset` row.
- *A1 increment 2, in part.* The `malformed-ruleset` scenario is the clean
  change in a project whose ruleset gives an unknown severity.
  `guidelines.ruleset_valid` blocks the advance to `committed`, and every
  other landing gate passes. Ruleset fixtures for the kinds that increment 2
  validates are still owed.

**Rulings after increment 1 (DN-A to DN-D).** The user took each
recommendation.

- *DN-A.* The sub-schemas for `boundary`, `cochange` and `judgement` are
  drafted as an amendment to this record and ruled before increment 2 is
  built. They were ruled on 2026-10-27 ("Amendment: check-kind sub-schemas",
  below).
- *DN-B.* The checklist's first line is its marker:
  `<!-- invariant-managed: <digest> -->` in markdown,
  `.. invariant-managed: <digest>` in rst. This replaces the "`#
  invariant-managed:`" shape above, which is not a comment in either format.
  A null `guidelines.checklist` is `checklist.<md|rst>` beside the ruleset,
  following `docs.format`.
- *DN-C.* There is one managed block, `guidelines/index`, holding the index
  and the `always` rules. It is capped by `guidelines.injection.max_lines`
  (default 40), and past the cap it ends with "N more: invariant guidelines
  show".
- *DN-D.* An unloadable ruleset is recorded at pipeline start and fails at
  landing through `ruleset_valid`. Increment 6 records the hash only, and A4
  compares it.
- `guidelines render` takes `--dry-run`. There is no `guidelines check`
  verb, and D12's diagram no longer draws one.

**As built: A2 increment 2, the check-kind sub-schemas.** Built to the
amendment's rulings (DN-A1 to DN-A14).

- *Every v1 kind validates.* `VALIDATED_KINDS` is all five, and the
  "does not validate its schema yet" refusal is gone. `Check` gains each
  kind's fields, with three small value types: `Trigger` (`when`),
  `Requirement` (`then`) and `NaCondition`. A judgement's `scope` is held as
  `verdict_scope`, because `scope` already holds a pattern's `in`. The keys
  are checked per kind, and for a `cochange` per form, so an unknown key is
  refused as it is anywhere else.
- *Paths are project-relative.* Boundary targets, `then` globs, a
  template's derived path, a registry path and a registry entry's paths go
  through `project_relative`. An absolute path, or one that climbs out of the
  project, is refused. The value kept is the normalised spelling. A
  template's derived path is tested by putting `x` for every token. A
  registry entry's path that holds `*`, `?` or `[` is refused, because a
  registry names files.
- *A template is read by Python's format parser.* A malformed template, a
  token outside the five, and a token with a conversion or a format spec are
  each refused. Doubled braces are literal braces.
- *The registry is read with the ruleset (DN-A9).* `load` and `parse` take
  `root`, the tree a registry path is resolved against. `load_ruleset`
  passes the tree in hand, so a registry broken in the session cannot pass
  on another checkout's copy. Each registry is read once, however many rules
  name it, and its problems are named once. A registry is refused whole:
  - it is missing;
  - it cannot be read;
  - it is not YAML;
  - its version is not 1;
  - its envelope or an entry has a bad key or value;
  - an open entry has a class the naming rule's `outcomes` does not map.
  A resolved entry needs no outcome, because only an open entry produces a
  finding. With no `root`, a ruleset that names a registry is refused rather
  than accepted unread. A loaded ruleset carries its registries
  (`Ruleset.registries`), each with its digest, ready for A3.
- *Choices the amendment left open, taken in the strict direction.*
  - An `only` list must be non-empty, like `forbid`.
  - `no_executable_change` must be `true`. `false` is refused rather than
    read as "no condition".
  - A fix is folded to one line, as rendered text is elsewhere.
- *The checklist summarises every kind.* `render_checklist` names each
  boundary's polarity and targets, a pair's trigger and what must change, a
  registry rule's file, outcomes and fixes, and a judgement's evidence,
  scope and condition ids. The rst output was checked with docutils and
  gave no warnings.
- *A1 increment 2, the rest of the ruleset fixtures.* The `valid-ruleset`
  scenario is the clean change in a project whose ruleset uses all five
  kinds: an engine rule, a pattern, a `forbid` boundary, a pair cochange,
  a registry cochange with its registry file, and a judgement with a
  mechanical and a claimed N/A condition. `guidelines.ruleset_valid` passes,
  and every other landing gate answers as it does for `clean`.
- *What is not here.* No kind is evaluated. A3 evaluates `boundary` and
  `cochange`, including the check that a configured adapter offers
  `imports` (DN-A14). A4 evaluates verdicts and the mechanical N/A
  conditions. The ruleset's digest covers the ruleset's bytes only. A
  registry is read as it stands after the change (the registry form above),
  so the pin (increment 6) does not cover it.
- *Tests.* The kinds, the registry and the rule's reading of it are tested
  in `tests/test_guidelines_kinds.py`, and the scenario in
  `tests/test_conformance_engine.py`.

**As built: A2 increment 3, the generated checklist.**

- *`guidelines.checklist`.* It is the second `guidelines` key the manifest
  reads: null or a non-empty path, refused at load otherwise. Null means
  `checklist.md` (markdown) or `checklist.rst` (sphinx) in the ruleset's
  directory. `instruction_files`, `baseline`, `injection` and `verdicts` are
  still refused by name.
- *`core/guidelines` renders, and stays pure.* `render_checklist(ruleset,
  fmt, source=)` returns the whole file. The marker line holds `digest_of`
  of every byte after it. `split_managed(text, fmt)` reads the digest and
  body back, and answers `(None, text)` for a file that does not open with
  this format's marker. That is all a drift check needs (increment 5).
  - The body has a title, a header naming the ruleset's path and digest,
    then each section in declared order, and each rule under it: id and
    title as a heading, the statement's paragraphs, then severity, phases,
    `applies_to`, a summary of the check, the exceptions with their
    fingerprints, the rationale, the links and the amendments.
  - A section with no rules says so.
  - An unknown format raises naming the formats.
  - The rst output parses with docutils and no warnings.
- *`domains/guidelines`, the seventh domain.* It is not an FSM. `render`
  reuses `core/rules`' answers rather than copying them. `load_ruleset`
  means render refuses exactly what `ruleset_valid` fails.
  `guidelines_tree` and `guidelines_relpath` were lifted out of
  `load_ruleset` for it, so the checklist is written into the tree its
  ruleset was read from. `is_exempt_path` means a generated file can never
  sit on an exempt path. Refusals are `RenderRefused`, exit 1, and each
  names its fix. Nothing is written when render refuses:
  - a ruleset that does not load;
  - a checklist path outside the project, equal to the ruleset, or with a
    suffix that disagrees with `docs.format`;
  - a `docs.format` with no renderer;
  - an exempt target;
  - an existing file with no marker (render did not write it, so it is the
    project's);
  - a privacy finding in the rendered text. The scan runs before the status
    is decided, so `--dry-run` refuses it too. It always runs, whatever
    `privacy.scope` says, because the file is about to be written into the
    tree.
- *What is rewritten.* A file render wrote is rewritten in every state, and
  the state is named. It is `current`; `stale`, rendered from an older
  ruleset; or `edited`, where the body no longer matches the marker. Hand
  edits are replaced rather than skipped, unlike an edited library asset.
  The checklist has no other source than the ruleset. D3's fix for drift is
  "move the change into the ruleset, then render", and a render that skipped
  the edited file could never apply that fix. The file is never exempt from
  isolation, so the replaced text stays visible in the branch's diff.
  Managed blocks in an instruction file are different. Text outside them is
  the project's, so increment 4 keeps the three-way skip and
  `--overwrite-local` there.
- *The write class (ruling 13).* cli.md's Conventions name it: generated
  files the manifest declares, written only by the commands that generate
  them, never exempt from isolation. manifest.md says the same beside the
  key.
- *No ruleset.* `guidelines render` reports "nothing to render" and exits
  0, with status `no-ruleset`.
- *Tests.* Rendering and the marker are tested in `tests/test_guidelines.py`.
  The domain, every refusal and the command through `main` are tested in
  `tests/test_guidelines_domain.py`.

**As built: A2 increment 4, the managed instruction-file block.** Built to
D3, ruling 8 and DN-C.

- *The manifest.* `guidelines.instruction_files` is null or a non-empty list
  of paths, and a bare string is refused. `guidelines.injection` is a mapping
  whose one key is `max_lines`, default 40, an integer of at least 2. D13's
  `on_edit` is refused by name until A5 reads it, as are `baseline` and
  `verdicts`.
- *The default is the host's.* `hosts/claude_code` names `INSTRUCTION_FILES`
  (`CLAUDE.md`). A null `instruction_files` means that default. The CLI
  passes it to the domain, because no domain imports a host. A `host.kind`
  with no adapter has no default, so only configured files carry a block.
- *The block.* It has a header line naming the ruleset's path and digest,
  then one line per rule. The `always` rules come first, each with its
  statement folded to one line, and then every other rule's id and title in
  declared order. The cap counts every line between the markers. Past it,
  the block keeps `max_lines - 2` rule lines and ends with "- N more:
  `invariant guidelines show`", where N is the rules left out. One rule is
  one line, so N counts lines and rules alike. Two is the least cap that
  holds the header and the count, so the manifest refuses less.
- *The markers are D3's.* `<!-- invariant:begin <name> digest=<12 hex> -->`
  and `<!-- invariant:end <name> -->`. The digest is `digest_of` the body
  between them. A marker is a whole line, give or take spaces, so a marker
  quoted inside a sentence is prose. A line that opens like a marker and is
  not one is malformed, along with D3's three cases (no end, a second block
  of one name, a nested pair), an end with no begin and a crossed pair. Each
  is refused naming the file and line.
- *The three-way rule.* `block_state` is `classify_installed`'s split, with
  the begin marker's digest as the witness. A body that no longer matches its
  digest is `edited`: skipped and named, and `--overwrite-local` takes the
  rendered text back. A body that matches its digest but not today's
  rendering is `stale`, and is rewritten. One that matches both is
  `current`, and is rewritten too. A file with no block is `absent`, and is
  left alone. `--insert guidelines/index` appends the block after one blank
  line, creating the file if it is missing, and reports `new`. A block of
  another name is left alone.
- *Choices the rulings left open, taken in the strict direction.*
  - An instruction file must be a `.md` file, because the markers are
    markdown comments.
  - An instruction file may not be the ruleset or the checklist.
  - An instruction file may not be exempt from isolation (ruling 13).
  - Any refusal writes nothing to any file, the checklist included. Every
    output is decided, checked and scanned before the first write.
  - The privacy scan runs on the block with the same reach as the
    checklist's, whatever `privacy.enabled` and `privacy.scope` say. It
    refuses only when a block is about to be written. A finding is named by
    file, block and line within the block, counting the begin marker as
    line 1.
- *`guidelines show`, minimal.* The capped block points at it, so it landed
  here. It prints the checklist text in markdown, without its marker, from
  the ruleset in the tree in hand, and writes nothing. D7's `--phase` and
  `--files` arrive with A5.
- *What is not here.* The drift gate (`generated_current`, increment 5) and
  the pin (increment 6). invariant's own instruction file carries no block,
  because this repository configures no ruleset.
- *Tests.* The block, the markers and the manifest keys are tested in
  `tests/test_guidelines.py`. The domain, each refusal, `show`, and both
  commands through `main` are tested in `tests/test_guidelines_domain.py`.

**As built: A2 increment 5, the drift gate.** Built to D3 ("Drift is a
rule"), ruling 7 and ruling 13.

- *`guidelines.generated_current`.* The rule is graded `warn` in all three
  profiles, strict included. With no ruleset it is N/A. `core/machines`
  injects it beside `ruleset_valid` in `GUIDELINES_GATES`, so it rides the
  landing entry only when a ruleset is configured. It only reports. Nothing
  renders at the landing.
- *One plan, lifted into `core/rules`.* "Current" has to mean what
  `guidelines render --dry-run` reports, so the rule and the command must
  ask one plan. The planning lived in `domains/guidelines`, and `core/` may
  not import a domain. So it moved down into `core/rules` as `plan_render`,
  with `RenderRefused`, the same way `guidelines_tree` and
  `guidelines_relpath` were lifted in increment 3. `plan_render` covers
  where each output goes, every refusal, the privacy scans, and each
  output's state. Nothing is written. The domain now writes what the plan
  decided, and keeps `show`. `ruleset_or_refuse` is the one conversion of a
  ruleset that does not load into a refusal, used by both.
- *The findings.* A checklist that is `new` is reported as missing. A
  checklist that is stale or edited is a finding, and so is a block that is
  stale or edited. An absent block is not, because render never inserts one
  unasked. The detail of a passing verdict names each absent block. Each
  finding names its file, and a block finding names its block too. The fix
  is `invariant guidelines render`. When anything was edited by hand, the
  fix says to move the edit into the ruleset first. An edited block also
  gets `invariant guidelines render --overwrite-local`.
- *Choices the rulings left open, taken in the strict direction.*
  - A refusal of the plan fails the rule, naming the refusal and its fix.
    Drift that render refuses to fix cannot be fixed by the command the rule
    points at. This covers a ruleset that does not load, as D3 asks of every
    gate that needs the ruleset. It also covers an exempt or misplaced
    output, a file render did not write, a malformed pair, and a privacy
    finding in text about to be written.
  - An edited checklist's fix is a plain render, which replaces the edit
    (increment 3). `--overwrite-local` is named only for an edited block,
    because only a block is skipped without it.
- *The `doctor` row warns, by user ruling.* The row, `guidelines generated`,
  was first built to fail on drift. The user ruled that it must warn, as the
  gate does, and never fail: drift alone must not make `doctor` report a
  failure. So drift prints `warn`, names each output and the fix, and leaves
  the exit code alone. `--json` marks the row `"ok": true, "warn": true`.
  The ruling did not cover a plan refusal, so the choice consistent with it
  was taken: the row warns on a refusal too. The rule still fails on one;
  only the row's grading changed. A ruleset that does not load still fails
  `doctor`, through the `guidelines ruleset` row, which this ruling did not
  touch.
- *The host's default reaches the rule on the rule context.* With
  `guidelines.instruction_files` null, the files are the host adapter's
  (increment 4). Nothing below the CLI imports a host. So
  `RuleContext.host_instruction_files` carries them, and the CLI supplies
  it to:
  - every rule context it builds, which covers `render`, `check` and
    `doctor`;
  - `PipelineDomain`, which hands it to the landing gates;
  - the oracle's gate cache, through `evaluate_row`'s
    `instruction_files_for`.

  No manifest key was added. `GuidelinesDomain` lost its
  `instruction_files` parameter, so one carrier remains.
- *Never exempt from isolation (ruling 13).* The existing guarantee held.
  The plan refuses a checklist or an instruction file that
  `session.exempt_paths` or a declared store would exempt. It checks every
  configured target, on a dry run, and whether or not a block would be
  written. This increment adds tests for a checklist in each declared store
  (`items.path`, `paths.reports`, `paths.sweeps`) and for an instruction
  file in one. The rule fails on an exempt output as well. No path was
  missing the guarantee, so no code changed for it.
- *A1 increment 2, extended.* The `valid-ruleset` scenario now renders its
  checklist in the change, and `generated_current` passes at the landing.
  In `malformed-ruleset` it warns, because outputs cannot be judged against
  a ruleset that does not load. The landing refusal is still
  `ruleset_valid` alone.
- *Tests.* The rule, its agreement with `render --dry-run`, the landing
  injection, the pipeline and oracle hand-off, and the `doctor` row are
  tested in `tests/test_guidelines_current.py`. The store refusals are
  tested in `tests/test_guidelines_domain.py`.

**As built: A2 increment 6, the pin.** Built to D3 ("The ruleset is pinned
per pipeline") and DN-D, after the methodology pin (AUD-02, AUD-03).

- *The record.* `Pipeline.ruleset` is `{"path", "digest", "error"}`, or null
  when no ruleset was configured at `start`. `core.rules.ruleset_pin` builds
  it through `load_ruleset`, so the pin reads the tree `ruleset_valid` reads
  (the session's worktree when there is one) and refuses what it refuses. The
  CLI hands `start` the session and the working directory for that.
- *What the digest covers.* The ruleset's own bytes: `Ruleset.digest`, the
  hash every verdict records (D5). A registry is not in it, as increment 2
  said: it is read as it stands after the change. A registry that does not
  validate still makes the ruleset unloadable at start, because the pin reads
  what `load_ruleset` reads.
- *An unloadable ruleset (DN-D).* The pipeline starts. The record keeps the
  path (as configured when it names nothing inside the project), a null
  digest, and the failure with its detail as `error`. `pipeline start` names
  it and points at `invariant check guidelines.ruleset_valid`. The landing
  refuses it through `ruleset_valid`, which reads the ruleset the change
  lands. So a ruleset repaired inside the change lands, and the pin keeps the
  record that the pipeline started without one. The other reading, failing
  `ruleset_valid` on a pinned error whatever the tree holds, was not taken:
  it would refuse the ruleset lane's own repair (D10) until a fresh pipeline
  was started, and DN-D names `ruleset_valid` as the gate, which reads the
  tree.
  Confirmed by the user: a ruleset fixed inside the change lands.
- *A ruleset changed mid-pipeline.* No gate compares the pinned digest with
  the current one in this increment. DN-D gives that comparison to A4, where
  a verdict whose hash differs from the pin is stale (D5). The pin does not
  move when the ruleset is edited.
- *What the methodology precedent decided.* The gate set is pinned with the
  record. A pipeline that pinned a ruleset keeps the `guidelines.*` landing
  gates even if `guidelines.ruleset`, an isolation-exempt manifest line, is
  cleared later. `load_pipeline_machine` takes `guidelines=True` from
  `machine_for` for this. Their verdicts then report what they can see,
  which with the key cleared is N/A, shown as `[skip:BLOCK]` and not as a
  silent absence. A pipeline that pinned nothing, and a record older than
  the field, follow the manifest as before, so configuring a ruleset
  mid-pipeline still adds the gates. Adding enforcement mid-pipeline was
  allowed before this increment and still is. Only removing it is blocked.
  Confirmed by the user, both the pin of the gates and the skipped display.
- *Shown.* `pipeline status` prints `ruleset <path> @ <digest>`, or the
  reason it could not be loaded at start, and nothing when nothing was
  pinned. `--json` is the record, `ruleset` included.
- *No migration.* The field defaults to null, and records keep unknown keys
  (BUG_061), so an older record loads unchanged and an older build writes
  the field back. The state model needed no version bump.
- *Tests.* `tests/test_guidelines_pin.py`.

### D4 — Five check kinds; v1 ships four of them in full and one in part

| Kind | Question it answers | Evaluated by | v1 |
|---|---|---|---|
| `engine` | none of its own; it points at an existing registry rule | that rule | yes |
| `pattern` | does a match exist in the changed files, in scope? | `core/findings` (A3.3) with the adapter's lexical mask | regex: yes. Syntax tree: deferred (ruling 3) |
| `boundary` | does code under `applies_to` reference a project file its `forbid` list names, or one its `only` list leaves out? | an adapter dependency question | import graph where the adapter can answer: yes. Symbol index: deferred (ruling 3) |
| `cochange` | if X changed (or is listed in registry R), did Y change too? | `core/findings` (A3.4) over the one changed set, with git's account of the change | yes |
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
folder-to-type boundary needs `symbols` and is deferred (ruling 3). A rule
states one polarity: a `forbid` deny-list, or an allow-only `only` list, never
both and with no carve-out (DN-A3). The amendment below gives its keys.

**`cochange`** is the fourth kind B1 found. Two forms:

- **Pair:** `when` (a touched glob, or a pattern match added in a glob) and
  `then` (globs that must also be touched, or a path template derived from the
  trigger). The form is explicit (`form: pair | registry`, DN-A4), and so is
  the trigger's `when.change` (`any`, `added` or `executable`, DN-A6). It is evaluated over `core/rules._changed_set`, the one answer to
  "what changed" (BUG_012, BUG_047), and an unestablished changed set fails as
  it does for `docs.updated`. "A production edit needs a test diff" uses the
  adapter's `executable_text`, so a comment-only edit owes no test (BUG_026's
  seam).
- **Registry:** `when` a touched file is listed in an open entry of a
  machine-readable registry file the ruleset names, the outcome is chosen by
  the entry's `class`. The file is invariant's own versioned YAML, with
  entries `id`, `paths`, `status`, `class` and `note` (DN-A7). A class maps
  to `blocker` or `warn`, with an optional fix: open and contained gives
  blocker, open and advisory gives warn, and a scoped refactor gives a blocker
  whose fix says to stop and ask (DN-A8). The engine does not parse a prose
  table; a project with its registry in prose converts it once, in the
  ruleset-edit lane.

**`judgement`** is D5.

**As built: A3 increment 1, lexical masks and a path-aware
`executable_text`.** Built to D4 ("Comment and string aware") and to the A3
design rulings the user accepted (DN-1 to DN-17, option A each time). Those that bind this increment:

- *DN-9.* `node` and `generic` gain nothing here. A node lexer is IDEA_038.
- *DN-10.* Python's mask and `executable_text` go through `tokenize`. An
  f-string or a t-string is one string region, replacement fields included,
  on every interpreter from 3.10, so a verdict does not depend on which one
  ran. `executable_text` drops comments, blank lines and layout and keeps
  docstrings, which reduces too little rather than too much. A comment-only
  Python edit is therefore excused from `coverage.meets_floor` in every Python
  project, this repository included.
- *DN-11.* `executable_text` takes the file's path and answers `None` outside
  its adapter's ecosystem; `core/rules._executable_text` passes the path.
  That is the fix for BUG_103.

What landed:

- *The seam.* `Adapter.lexical_mask(relative, text, keep)` returns the same
  lines and columns with every region outside `keep` (`code`, `comments` or
  `strings`) blanked, and `None` for a file outside the adapter's ecosystem
  or one it cannot read to the end. `Adapter.guideline_engines` is a
  `ClassVar` subset of `{mask, imports}`. The base offers nothing and
  answers `None`, which relaxes nothing. `python` and `swift_xcode` offer
  `mask`. In a stacked project the first adapter with an answer wins, as
  with `is_test_file` (`core/rules._lexical_mask`, for A3.3 to consume).
- *One Swift reader.* `_swift_regions` is the one reader of Swift comments
  and literals. `_strip_comments_and_strings`, `_swift_executable_text` and
  the mask all consume it, so they cannot disagree. `executable_text` is
  restricted to `.swift`. The known limits stay: `#"raw"#` strings, and
  quotes inside a `\( )` interpolation.
- *Validation.* `guidelines.ruleset_valid` hands `core/guidelines.load` the
  engines the configured adapters offer, and refuses a `pattern` rule whose
  `in:` is not `any` unless one of them offers `mask`. `parse` without
  `engines` asks the schema question alone. A cochange `when.in` and a
  `no_added_match.in` are not refused here: the ruling names pattern rules,
  and those keys are evaluated by A3.4 and A4, where a file without a mask
  is unanswerable.
- *Text.* A file that does not decode as UTF-8 is not text, and no adapter
  is asked about it.
- *Tests.* `tests/test_adapters_lexical.py` (the seam, both lexers, the
  pre-3.14 t-string tokens, stacking), `tests/test_adapters.py` (the Swift
  reducer, now path-aware), `tests/test_guidelines_kinds.py` (the mask
  refusal) and `tests/test_rules.py` (BUG_103's repro, its marker removed,
  and the Python comment-only regression).

**As built: A3 increment 2, Python import references and the imports-offered
check.** Built to the check-kind amendment (DN-A2, DN-A14) and to the A3
rulings that bind it: ruling 3 for Swift, DN-9 for node and generic, and
DN-14 for Python's resolution.

- *The seam.* `Adapter.references(path, relative, root)` answers a list of
  `Reference(target, line, text)`: the referenced file, project-relative in
  POSIX spelling, the line the import starts on, and that line's text without
  its line end, which D6's fingerprint is taken from. It refines D4's
  `references(path) -> set[str] | None` as the amendment asked. An import
  that resolves outside the project is outside the kind and is not
  reported, so an empty list is a file that imports nothing of the
  project's. `None` is a file the adapter cannot answer for, and a boundary
  rule then fails rather than passes. The base answers `None`.
  `core/rules._references` is the stacking rule, the first answer winning,
  for A3.3 to consume.
- *Who answers.* `python` offers `imports` beside `mask`. `swift_xcode`
  gets no references, because a Swift module's boundaries are folder-to-type
  references that need `symbols` (ruling 3). `node` and `generic` get
  nothing (DN-9).
- *Python's resolution (DN-14), through `ast`.*
  - Every import statement counts, wherever it sits: in a function, under
    `if TYPE_CHECKING:`, in a `try`.
  - A relative import resolves by the file's package, its directory climbed
    once per extra dot. Climbing out of the project, or naming nothing, makes
    the file unanswerable.
  - An absolute import resolves against the roots the tree implies: the
    first ancestor of the file with no `__init__.py` (a src layout's `src`),
    then the project root. The first root where the first segment is a
    project package is the one used. A name whose first segment is a project
    package but which does not resolve to a file makes the file
    unanswerable.
  - `import a.b.c` references the file of `a.b.c` alone, not the packages
    it passes through, so an `only` list does not have to admit every
    parent `__init__.py`.
  - `from pkg import name` resolves to the submodule's file when `name` is
    one, and to the package's `__init__.py` otherwise. A statement naming
    one file twice reports it once.
  - A file with a syntax error, one that is not UTF-8, and one that cannot
    be read are unanswerable.
- *Choices the rulings left open, taken in the strict direction.*
  - A project package is a regular package, a module, or a directory holding
    Python directly, a namespace package. A namespace package's own name
    holds no file, so `import ns`, or `from ns import x` where `x` is no
    submodule, is unanswerable. A directory with no Python in it is not a
    project package, so an import that shares its name stays third-party.
  - A multi-line import is reported at its first line, with that line's
    text.
- *Validation (DN-A14).* `guidelines.ruleset_valid` refuses a `boundary`
  rule with the `imports` engine unless a configured adapter's
  `guideline_engines` offers `imports`. The refusal names the missing
  engine, and the fix: declare an ecosystem whose adapter offers it, or
  write the rule as judgement. `symbols` is still refused as deferred, by the
  schema, and not as unoffered. `parse` without `engines` asks the schema
  question alone, as in increment 1.
- *What is not here.* No boundary rule is evaluated. `mechanical_clean`'s
  evaluation of both polarities over `references` is increment 3.
- *Fixtures.* The kinds tests' gate fixture declares `ecosystems: [python]`,
  because its ruleset carries two boundary rules; the assertions are
  unchanged. Two lexical-seam assertions that read "python offers exactly
  `mask`" now read the ruled set.
- *Tests.* `tests/test_adapters_references.py` (the seam, relative,
  absolute, package `__init__`, submodule-from, outside-project,
  unresolvable project name, syntax error, src layout, every import
  counting, stacking) and `tests/test_guidelines_kinds.py` (the
  imports-offered refusal).

**As built: A3 increment 3, findings and `guidelines.mechanical_clean` over
`pattern` and `boundary`.** Built to D4, D5, D6, D11, D12 and the A3 rulings
that bind it: DN-2 (one finding per matching masked line), DN-3 (the boundary
fingerprint), DN-12 (the seeded-pattern scenario), DN-13 (a pure module beside
`core/guidelines`) and DN-16 (printed, kept nowhere).

- *`core/findings` (DN-13).* The working name is kept. It holds `Finding`,
  its record in D5's shape, `fingerprint`, and the `pattern` and `boundary`
  evaluators. It is pure: the bytes, the mask and the references arrive as
  callables, and it makes no file read and no `git` call. `core/rules` makes
  every read, from the tree in hand, and `core/guidelines` stays `git`-free.
  D12's diagram below drops its `CGUIDE --> GIT` arrow: the baseline and diff
  reads are `core/rules`' when they land.
- *The fingerprint.* The first twelve hex digits of SHA-256 over the rule id
  and a kind-specific key, the parts joined by a NUL so no two pairs can
  hash one text. A pattern's key is the matched raw line, whitespace
  collapsed; a boundary's is the referencing line, collapsed, and the target
  (DN-3). No line number is in it. An exception's `fingerprint` is this
  function's answer.
- *Pattern (DN-2).* The masked text and the raw text are split on line feeds
  and matched line by line with `re.search`, one finding per matching line.
  `in: any` is the raw text and asks no mask. A mask answer of `None` makes
  the file unanswerable, and so does a mask that does not keep the file's
  line count. A file that does not decode as UTF-8 is not text. No pattern
  applies to it, and the verdict names it.
- *Boundary (DN-A1 to DN-A3).* Each reference a changed file under
  `applies_to` makes is checked: under `forbid`, a target matching a glob is
  a finding; under `only`, a target matching none is. A references answer
  of `None` makes the file unanswerable.
- *The rule.* `guidelines.mechanical_clean` evaluates the changed files only
  (D4, `_changed_set`), in their `project_relative` spelling. It is N/A with
  no ruleset. It fails on a ruleset that does not load, on a changed set that
  cannot be established, and on a new or unanswerable finding of a blocker
  guideline. A warn guideline's findings, the exceptions that applied and the
  files that are not text are named in the detail of a passing verdict. The
  levels are D12's: `loose` warn, `standard` warn, `strict` block.
  `GUIDELINES_GATES` carries it at the landing state's entry beside the
  other two, only with a ruleset configured.
- *Printed, not kept (DN-16).* `Verdict` gains `findings`, the records, which
  only this rule sets. The CLI's verdict payload carries the key only when a
  rule set it, so `invariant check guidelines.mechanical_clean --json`, and
  the `--json` of `pipeline gates` and `pipeline advance`, print them. No
  record is written, and no `guidelines` verb was added.
- *Conformance (DN-12, A1 increment 3 in part).* The `seeded-pattern`
  scenario is the clean change with one `print(` added to `app.py`, under a
  ruleset whose blocker pattern forbids it in code. Its manifest sets
  `guidelines.mechanical_clean: block`, so the finding is the one refusal at
  `committed` (D11), and the refusal names `style.no-print app.py:2`.
  `valid-ruleset`'s known answer gains `mechanical_clean: pass`. A known
  answer is compared exactly, so `malformed-ruleset`'s gains
  `mechanical_clean: warn`: a ruleset that does not load fails it, and
  `standard` holds it at warn.
- *Small points the rulings left open, taken in the strict direction.*
  - The one matcher. `glob_matches` was lifted out of `core/rules`'
    `_first_matching_glob` into `core/manifest`, beside `project_relative`,
    so a guideline's globs match exactly as every other declared glob does:
    the path itself or anything beneath it, through `fnmatch`. Two
    consequences a ruleset author must know. `*` crosses `/`, so `app/*.py`
    names every Python file under `app`. `**/` needs at least one directory,
    so `src/invariant/core/**/*.py`, the amendment's own example, does not
    name `core`'s top-level modules. A rule over every module of a
    directory writes `src/invariant/core/*.py`, and A3.9's dogfood ruleset
    must.
  - A path exception with no fingerprint sets the whole file aside for its
    rule. The file is not read, and the verdict names it with the reason. A
    fingerprint exception passes the findings with that fingerprint alone,
    and never excuses an unanswerable file, which has no fingerprint.
  - A file that cannot be read is unanswerable, like one no adapter can
    answer for.
  - A changed set that cannot be established fails the rule even when the
    working tree has changes. `docs.updated` reads such a partial set, but
    a finding in a committed file nobody can list would pass unseen here.
  - A deleted file is read by nothing. It has no line to match and imports
    nothing.
  - A declared absolute path is spelled against the project's checkouts,
    and the tree in hand.
  - A finding's citation carries no blob. A finding is computed afresh at
    every evaluation and kept nowhere, so nothing can go stale against it.
  - `cochange` rules are not evaluated, and the verdict's rule count names
    the pattern and boundary rules alone (increment 4).
- *Tests.* `tests/test_guidelines_findings.py` (the fingerprint, the matcher,
  both kinds, exceptions, unanswerable and non-text files, the record, the
  rule and its levels, the injection, and `check --json` through `main`) and
  `tests/test_conformance_engine.py` (the seeded pattern and the two amended
  known answers).

**As built: A3 increment 4, `cochange` evaluation.** Built to D4, the
check-kind amendment's cochange rulings (DN-A4 to DN-A9) and the A3 rulings
that bind it: DN-4 (a cochange finding is never baselined), DN-12 (the
missing-test scenario) and DN-13 (`core/findings` stays pure).

- *New `git` queries, in `core/git` (D12, `RULES --> GIT`).* `core/rules`
  makes every call, and `core/findings` makes none.
  - `merge_base(cwd, base, tip)`: the commit the branch left its base at.
    Every other read is measured from it, as `_changed_set`'s commit leg is.
  - `exists_at(cwd, rev, path)`: `True`, `False`, or `None`, so "the path was
    absent" is kept apart from "the revision could not be read", the two
    answers `file_at` merges. A directory is not a file, and the path is read
    literally, never as a pathspec glob.
  - `blob_at(cwd, rev, path)`: the exact bytes, unstripped. `file_at` strips
    and decodes, which a reduction to executable text should not have to
    tolerate.
  - `changed_lines(cwd, base)`: every file the change touched, from the merge
    base to the working tree, each with the line numbers it added there.
    Committed, staged and unstaged work all count; what the base gained after
    the branch left does not. An untracked file is counted whole
    (`AddedLines.whole`), without being read. A file touched with no added
    line (deleted, binary, lines only removed) is a key with no lines, so the
    keys are git's own account of what changed. Any leg git cannot answer
    makes the whole answer `None`, never a smaller one.
- *`core/findings` evaluates both forms.* `EVALUATED_KINDS` is all three
  mechanical kinds. `History` carries what git knows as callables (whether a
  path existed at the merge base, its bytes there, the added lines, the
  adapters' `executable_text`), and `evaluate` takes the changed paths that
  are gone from the tree and the registries as the change leaves them.
  - *Pair (DN-A4 to DN-A6).* `any` is a path in the changed set; `added` is a
    file in the tree that was no file at the merge base; `executable` is the
    adapters' `executable_text` differing between the merge base and the tree,
    path-aware since A3.1 (DN-11), so a comment-only edit triggers nothing.
    `when.match` then narrows the trigger to a file where a line the change
    added matches, through the mask its `in` names, line by line as a pattern
    is (DN-2). `then` is `all_of`, `any_of` or `each`, and a finding names
    what did not change.
  - *Registry (DN-A7, DN-A8).* A changed file under `applies_to` listed in an
    open entry is one finding per entry, at the outcome the entry's class
    maps to, with the class's fix or a default naming the entry and the
    registry. The registry is the one `load_ruleset` read from the tree in
    hand, so the change that strikes the file, or resolves the entry, clears
    the finding.
  - *Unanswerable, never a pass.* No base recorded, a merge base git cannot
    find, an existence or a blob it cannot read, lines it cannot give, a mask
    or an `executable_text` answering `None`, a mask that moves lines, a file
    that cannot be read, a derived path naming nothing in the project, and a
    registry that was not read. Each is a failing finding that names why.
- *DN-4.* A cochange finding describes the change, not the tree. It has no
  line and no fingerprint (`null` in the record), so it sits outside the
  (rule, path, fingerprint) key a baseline entry is made of, and no baseline
  can hold it. Increment 5 refuses a baseline entry naming a cochange rule.
- *`guidelines.mechanical_clean`.* It counts cochange findings with the same
  semantics: a new or unanswerable finding at blocker fails it, a warn one
  is reported inside a pass. A registry finding's severity is its class's
  outcome. The pass message counts "mechanical guideline(s)". A blocking
  finding's fix joins the verdict's fixes. The record in D5's shape gains
  `fix`, empty for a pattern or boundary finding.
- *Validation (A3.1 deferred it here).* `guidelines.ruleset_valid` refuses a
  cochange `when.in` of `code`, `comments` or `strings` unless a configured
  adapter offers `mask`, naming the rule. `no_added_match.in` stays with A4.
- *Conformance (DN-12, A1 increment 3 in part).* The `missing-test`
  scenario is the clean change under a ruleset whose blocker pair says an
  executable change to `X.py` touches `tests/test_X.py`. Its manifest sets
  `guidelines.mechanical_clean: block`, so the finding is the one refusal at
  `committed`, naming `tests.module-has-test app.py`. `valid-ruleset` still
  passes: its warn pair finds `app.py` changed without `check.sh`, which is
  reported inside the pass, and its registry lists no changed file.
- *Small points the rulings left open, taken in the strict direction.*
  - A file satisfies `then` only when it is in the changed set AND git shows
    it changed (`changed_lines`' keys). The changed set holds declared
    files, and declaring a file is not changing it. An untracked file nobody
    declared is not in the changed set, so it satisfies nothing until it is
    declared, staged or committed.
  - The trigger side reads the changed set as ruled, unwidened. A changed
    path that is gone from the tree is still a change: `any` triggers on it,
    `executable` compares its executable text at the merge base with an empty
    file's, and a registry still listing it is a finding. A gone path that
    was never a file at the merge base triggers no `added`, `executable` or
    `when.match`; `any` still triggers on it, because it is in the changed
    set.
  - With no base recorded, every fact git would give is unanswerable. An
    `added` or `executable` trigger, or a `when.match`, is then unanswerable,
    and so is any triggered pair, because `then` needs git's account.
  - A file that is not UTF-8 text cannot be reduced, so an `executable`
    trigger on it is unanswerable. A `when.match` does not apply to it, and
    the verdict names it, as for a pattern.
  - `{dir}` is `.` for a file at the top, so `{dir}/test_{name}` names
    `test_m.py` beside `m.py`. A derived path is spelled canonically, and
    one naming nothing inside the project is unanswerable.
  - Renames are not followed. A moved file's every line is added at its new
    path, and the old path is a key with no lines.
  - A path git prints quoted is unquoted, octal escapes included. A patch
    header the reader cannot spell makes `changed_lines` answer `None`, not
    file lines under a wrong name.
  - `ruleset_valid` refuses a fingerprint exception on a cochange rule,
    because no cochange finding has a fingerprint and the exception could
    never apply. A path exception sets the trigger file aside, as for the
    other kinds.
  - A new cochange finding's fix names what to change. The fingerprint
    exception advice is given only for a pattern or boundary finding.
  - The queries run in the tree in hand, the session's worktree when there
    is one, from its `HEAD`.
- *Tests.* `tests/test_guidelines_cochange.py` (each trigger, each `then`
  shape, the template tokens, the registry, every unanswerable fact, the
  record, and the rule over real repositories), `tests/test_git.py` (the four
  queries, the patch reader, quoting, and every failing leg),
  `tests/test_guidelines_kinds.py` (the `when.in` mask refusal and the
  fingerprint-exception refusal), `tests/test_guidelines_findings.py` (the
  record's `fix`) and `tests/test_conformance_engine.py` (`missing-test`).

**As built: A3 increment 5, the baseline file and `guidelines baseline`.**
Built to D6, D13's `guidelines.baseline`, ruling 5 (always a pure ratchet),
ruling 13 (the generated-file write class) and the A3 rulings that bind it:
DN-1 (null is no baseline), DN-4 (no cochange entry), DN-5 (stale entries
are increment 6's to fail), DN-6 (admission of additions is increment 6's)
and DN-12 (the baselined scenario).

- *The manifest (DN-1).* `guidelines.baseline` is accepted: null or a
  non-empty path, refused at load otherwise. Null means no baseline, and every
  pattern or boundary finding is new. A configured path whose file does not
  exist is an error at every gate that reads it, and `--record` and
  `--prune` refuse until the key names a path. `verdicts` and
  `injection.on_edit` are still refused by name.
- *The format (D6), in `core/guidelines`.* `BaselineEntry`,
  `parse_baseline`, `render_baseline` and `baseline_rule_problems`. One entry
  per line, `rule-id  path  fingerprint  count`: the rule id is the first
  field, the fingerprint and count the last two, and everything between is
  the path. A line is a comment only when its first non-blank character is
  `#`, and blank lines are ignored. The writer sorts by path, rule and
  fingerprint under one header comment, and writes no managed marker. Read in
  full or not at all: fewer than four fields, a bad fingerprint, a count that
  is not a positive integer, a duplicate, a path outside the project, a rule
  the ruleset lacks, and a rule that is not pattern or boundary (DN-4) each
  fail every gate that reads the file. Every problem is collected and named
  by line, as a ruleset's are.
- *The ratchet, in `core/findings`.* `ratchet` takes the findings and the
  entries `core/rules` read. For each touched file and rule, F findings of
  one (rule, path, fingerprint) against N recorded give min(F, N)
  `baselined`, F - N new when F > N, and N - F stale when N > F. A
  baselined finding is never counted, so it does not block, and the
  baseline protects it whether or not its file is touched (ruling 5). Only
  changed files are scanned. `guidelines.mechanical_clean` reads the
  baseline after the ruleset, fails on one that does not read, names the
  baselined findings in its message and detail, and names the stale entries
  in its detail with `invariant guidelines baseline --prune` as the fix. A
  stale entry does not fail the rule here (DN-5). The record's `finding` is
  `baselined` and its status `pass`.
- *`invariant guidelines baseline`.* `--record [--rule <id>]...`, `--prune`
  and `--dry-run`. `--record` scans every file the index tracks
  (`core/git.tracked_files`) under each pattern and boundary rule's
  `applies_to`, with the reads `mechanical_clean` hands `core/findings`
  (`_finding_reads`, now shared), so a record holds exactly what the gate
  then finds. With `--rule` it replaces only those rules' entries. `--prune`
  recomputes the touched files: it lowers counts, and drops an entry whose
  count reaches zero, whose file is deleted, or whose rule is gone. The plan
  is `core.rules.plan_baseline`, the shape of `plan_render`, and
  `domains/guidelines` writes what it decided. Admission of what `--record`
  adds is increment 6's gate (DN-6); here it just writes.
- *The write class (ruling 13), landed.* The baseline is the third generated
  file. Nothing is written when the plan refuses: an exempt target, a target
  outside the project, a target that is the ruleset or the checklist, and a
  privacy finding in the text, scanned whatever `privacy.enabled` and
  `privacy.scope` say. The engine never writes the baseline at the landing;
  the gates only read it. cli.md's Conventions name the class as landed.
- *`doctor`.* A `guidelines baseline` row: "no guidelines ruleset
  configured" and "no baseline configured" pass, a baseline that reads
  passes with its counts (entries, findings, files, guidelines, path), and an
  unreadable or malformed one warns and never fails.
- *Conformance (DN-12, A1 increment 3 completed).* The `baselined` scenario:
  the base branch's `app.py` already prints once, and the baseline records
  that line. The change keeps it and adds a second print. The gate holds the
  recorded finding as baselined and blocks on the new one alone, named
  `style.no-print app.py:3`, so the one refusal at `committed` is
  `mechanical_clean`'s. The fixture baseline is the writer's own output.
- *Small points the rulings left open, taken in the strict direction.*
  - The format lives in `core/guidelines`; the arithmetic, which reads
    `Finding`, lives beside it in `core/findings`. D12's "baseline
    arithmetic" in `core/guidelines` is refined so, and the diagram's
    `CFIND` label says it. Both modules stay pure: `core/rules` reads the
    file.
  - A count is a positive integer in its plain spelling: `01`, `+1` and
    `1.0` are refused.
  - A path is kept in its canonical spelling, as `coverage_baseline`'s is,
    so `./x` and `x` recorded together are one entry twice and refused.
  - The writer refuses a path it cannot write as one line: one with a line
    break, or with whitespace at either end.
  - Of F findings sharing one fingerprint, the first N in file order are the
    baselined ones. They carry one fingerprint, so no other order can be
    told apart.
  - A (rule, path) with an unanswerable finding has no stale entries, since
    nothing can say what the file holds. `--record` refuses on any
    unanswerable file, because a record of part of the tree is no record,
    and `--prune` refuses on one that has entries.
  - An excepted finding consumes no entry, so an entry for it is stale.
  - A warn rule's findings are recorded and baselined like a blocker's.
  - The gates' read fails on a baseline isolation exempts, as well as the
    write refusing one: an exempt baseline could gain entries unreviewed.
  - The write also refuses a target that is an instruction file or a
    registry the ruleset names, beside the ruleset and the checklist.
  - `--prune` also drops an entry whose rule is no longer pattern or
    boundary, and one whose rule's `applies_to` no longer covers its file,
    wherever the file is. Neither can protect anything.
  - `--prune` refuses a missing file (it shrinks; `--record` creates), a
    file that does not parse, and a changed set that cannot be established.
  - `--record --rule` needs the existing file to read in full, because it
    keeps the other rules' entries; an entry whose rule is gone is dropped
    and named, since the file would not load with it. A full `--record`
    replaces whatever the file held.
  - `--rule` with `--prune` is refused rather than ignored.
  - `tracked_files` is the index (`ls-files --cached`): a staged new file
    counts, an untracked one does not, and a path deleted from the working
    tree but not the index is skipped.
  - The status is `new` (no file), `current` (text unchanged, still
    written, as render rewrites a current checklist) or `changed`.
  - The `doctor` row warns, rather than passes, on a ruleset that does not
    load ("cannot be checked"), as the `guidelines generated` row does.
- *Tests.* `tests/test_guidelines_baseline.py` (the format, the writer, the
  rules an entry may name, the ratchet, the rule, the `doctor` row, and a
  record the gate then honours), `tests/test_guidelines_domain.py` (record,
  prune, every refusal, and the command through `main`),
  `tests/test_guidelines.py` (the manifest key), `tests/test_git.py`
  (`tracked_files`) and `tests/test_conformance_engine.py` (`baselined`).

**As built: A3 increment 6, `guidelines.baseline_only_shrinks`.** Built to
D6 ("Only shrinks"), ruling 5 (always a pure ratchet), ruling 7 and the A3
rulings that bind it: DN-5 (a stale entry fails here), DN-6 (which growth is
admitted), DN-7 (renames) and DN-12 (two scenarios).

- *The rule.* `guidelines.baseline_only_shrinks` is graded `block` in
  `loose`, `standard` and `strict`. It is N/A with no ruleset and with no
  baseline. A ruleset or a baseline that does not load fails it, and so do a
  changed set that cannot be established, no base recorded, a merge base git
  cannot find and a rename listing git cannot give.
- *Injection.* `core/machines` adds `BASELINE_GATES` to the landing state's
  entry when the guidelines gates are injected (a ruleset configured, or
  pinned) and `guidelines.baseline` is configured, on the `sweep.fresh`
  precedent. The baseline key is read as the manifest stands, never pinned.
  Clearing it leaves no baseline to honour, which makes every pattern or
  boundary finding new (DN-1). That shrinks the record, so it is no amnesty.
- *The base copy.* It is read through git at the merge base of the
  session's base and `HEAD`, from the tree in hand. The path is the one the
  base's own `.claude/invariant.yaml` names, read there for its two
  `guidelines` paths and nothing else. Pointing the key at a new file
  therefore compares the new file with the old one.
  - A base with no manifest, or with no `guidelines.baseline`, has no base
    copy.
  - The base copy is read in full or not at all. Each of these fails the rule
    with a message naming the base branch, never admitting everything:
    - a base manifest that does not parse, or gives a `guidelines` value
      that is not a mapping or a path;
    - a configured baseline missing at the merge base;
    - one git cannot read;
    - one that is not UTF-8;
    - one with a line that does not parse.
  - The base copy is not checked against any ruleset. Its entries are only
    counted.
- *The arithmetic, in `core/findings`.* `growth(base, tip, renames)` keys
  both sides by where a path ends up after the change's renames and sums the
  counts. A key the tip holds more of is a `Growth`, either `added` (the
  base held none) or `raised`. Deleting entries and lowering counts are
  shrinking, and deleted comment or blank lines change nothing.
- *Admission (DN-6), in `core/rules`.* Growth is admitted when one of these
  holds:
  - the base configured no baseline;
  - the entry's rule is absent from the base's ruleset;
  - the entry's rule has a different `applies_to`, `check` or `exceptions`
    there.

  The base ruleset is read only when there is growth to judge. It is parsed
  from the merge base, with its registries read there too, through the new
  `read` parameter of `core/guidelines.parse`, for the schema question alone.
  A base with a baseline and no ruleset has no rules, so every rule is
  absent from it. A base ruleset that does not parse, or is missing, fails
  the rule. `definition_of` compares the validated model, not the text, so
  YAML style, quoting, key order and a `./` before a glob compare equal.
  Every admitted growth is named, with its reason, in the detail of a
  passing verdict. A8 adds the lane and ADR requirement; nothing of it is
  built here.
- *Stale entries (DN-5).* The ratchet's arithmetic is the one
  `mechanical_clean` reports in its detail. The pattern and boundary rules
  are evaluated over the changed files, and an entry of a touched file
  whose count exceeds what the file holds now is stale. Each stale entry is
  named, and the fix is `invariant guidelines baseline --prune`.
- *Renames (DN-7).* `core/git.renamed_files(cwd, base)` answers old path to
  new for every rename git detects (`-M`) from the merge base to the working
  tree. Committed, staged and unstaged renames all count. It answers `None`
  when git cannot say, or when the listing cannot be read, and never gives a
  smaller answer.
  - `only_shrinks` accepts a moved entry while rule, fingerprint and count do
    not grow.
  - `--prune` rewrites a moved entry's path. It merges the entry with one
    already recorded at the new path, then lowers or drops it there like any
    other. Each move is a change with `moved_to`, printed as "moved ... to
    ...", and `--json` carries the key on every change (null for the
    others).
- *Conformance (DN-12).* Two new scenarios, each run under the clean change's
  steps with `mechanical_clean` at block:
  - `baseline-grown`: the change adds a second print and writes its entry
    into the baseline by hand. `mechanical_clean` holds both prints as
    baselined and passes, and `baseline_only_shrinks` is the one refusal at
    `committed`, naming the added entry.
  - `fixed-not-pruned`: the change removes the recorded print and leaves the
    baseline as it was. The entry is stale, so `baseline_only_shrinks` is the
    one refusal, and it names `--prune` as its fix.

  `baselined`'s known answer gains `baseline_only_shrinks: pass`, because a
  known answer is compared exactly.
- *Small points the rulings left open, taken in the strict direction.*
  - Injection needs a ruleset (configured or pinned) and a baseline. With a
    baseline and no ruleset every `guidelines.*` rule is N/A (D3), and a
    `[skip:BLOCK]` on every landing would be noise.
  - The base copy is read at the merge base, not at the base branch's tip,
    so an entry the base gained after the branch left is still the change's
    growth if the change adds it too. This matches `changed_lines`.
  - A file the change renamed away counts as touched for the stale check,
    so an entry left at the old path is stale whatever `diff.renames` says.
    `_changed_set` lists only the new path when git's rename detection is
    on, so without this the answer would depend on configuration.
  - `growth` keys an entry left at the old path and one at the new path to
    one budget. Keeping both is never more than the base held.
  - An exception's `reason` is not part of the definition. An exception is
    its path and fingerprint, and rewording the reason sets nothing else
    aside. Wording, severity and phases are not part of it either. Globs,
    exceptions, boundary targets, `then` globs, outcomes and fixes compare
    as sets, because their order means nothing.
  - A base that configures a baseline its tree does not hold fails the rule
    rather than reading as empty, as D6 says of a configured file that does
    not exist. The fix names repairing the base, or `invariant override`
    for one evaluation.
  - An untracked destination is not a rename until it is staged, because
    git's diff does not see it. A move that is not detected reads as an
    added entry, which blocks.
  - `--prune` asks git about renames only when an entry's file is gone and
    a base is recorded. With no base it drops a gone file's entry as
    deleted, as before. With a base, an answer git cannot give refuses the
    prune rather than dropping a moved entry.
  - Every git path of the family is project-relative, so the project root is
    taken to be the repository's top, as every git read of the family since
    A3.4 assumes. The base manifest is read at `.claude/invariant.yaml`.
- *Tests.* `tests/test_guidelines_ratchet.py` (growth, `definition_of`,
  `parse` with a reader, the rule over real repositories, admission, stale
  entries, renames, every failure, the injection, the levels and `check
  --json`), `tests/test_git.py` (`renamed_files`),
  `tests/test_guidelines_domain.py` (`--prune` following a rename, merging,
  dropping out of scope, the refusal and `moved_to` through `main`),
  `tests/test_rules.py` (reachability through `BASELINE_GATES`) and
  `tests/test_conformance_engine.py` (`baseline-grown`, `fixed-not-pruned`
  and the amended `baselined`).

**As built: A3 increment 7, override by guideline id.** Built to ruling 4,
D12 ("A single guideline's level"), DN-15 and the four properties of the
override token in manifest.md. No new record shape: a guideline grant is held
in `session.pending_overrides` and spent into `session.overrides` in the
shape a rule's grant already has, with the guideline id as its `rule`.

- *DN-15.* `core/guidelines.parse` refuses a guideline whose id is a
  registered rule id, naming the collision. `guidelines.ruleset_valid` and
  every gate that loads the ruleset refuse it with it, so
  `invariant override <id>` never means both.
- *The grant.* `invariant override <guideline-id> --reason` is held when the
  id names no registered rule and names a guideline in the ruleset of the
  tree in hand, read through `load_ruleset` as the gate reads it
  (`core/rules.guideline_override_refusal`). An unknown id is refused with
  the nearest guideline id, and so is any id when no ruleset is configured
  or the ruleset does not load. `--reason` stays required.
- *The effect.* `guidelines.mechanical_clean` reads the live grants
  (`core/rules.guideline_grants`) and `core/findings.overridden` turns each
  granted guideline's `new` finding at `blocker` into `overridden`, after
  the ratchet, so a baselined finding stays baselined. An overridden finding
  is named and never counted. Its record is `status: pass`,
  `finding: overridden`, and its note carries the grant's reason. The
  message counts them, and the detail names each one with the grant and its
  reason. A gate that still fails on another guideline names them in its
  message too. A new blocker finding's fixes name
  `invariant override <guideline-id> --reason '<why>'`, and the refusal
  `pipeline ack` gives for the family gate names the same route beside the
  forced advance.
- *Spending (manifest.md's four properties).* `cli/main` spends the grant
  after a `pipeline advance` that succeeded, when the family gate's level
  blocks or asks and its verdict passed with the guideline's findings
  overridden. That is the one case where the grant decided the advance. The
  spend goes into `session.overrides` with surface `cli`, and the command
  prints what the gate would have blocked on. `pipeline gates`, `check`,
  `doctor` and the oracle's gate cache evaluate the gate and never reach the
  spend. A hook spends grants by rule id, and no guideline id is one, so no
  hook spends a guideline grant either. An unspent grant is loud:
  `session status` prints it as a guideline grant, and every evaluation of
  the family gate names it, used or unused.
- *`guidelines.baseline_only_shrinks` is not overridable by guideline id.*
  Its growth and stale failures concern the baseline, not a guideline, and it
  reads no grant. A3.6's fix text named `invariant override
  guidelines.baseline_only_shrinks` for a base copy that cannot be read. No
  surface spends a rule grant at a transition, so that advice could not
  help. It now names the route that can:
  `invariant pipeline advance <state> --force --reason '<why>'`, recorded as
  every forced advance is.
- *Small points the rulings left open, taken in the strict direction.*
  - A grant does not cover the guideline's unanswerable findings. D12 says a
    grant passes the guideline's findings. An unanswerable file is not a
    finding about the code: nothing could say what it holds, and "an
    unanswered file never passes" (A3.3).
  - A grant is refused when nothing could ever spend it, as an override
    naming no rule is (ADR-001 D25, BUG_050). That covers:
    - an `engine` guideline, whose refusal names the rule to override instead;
    - a `judgement` guideline, until A4 builds the gate that evaluates
      verdicts;
    - a `warn` guideline, since a registry class's outcome never exceeds its
      rule's severity, so its findings never block.
  - `--calls` above one is refused for a guideline. Ruling 4 makes the grant
    one-shot.
  - The grant is not spent when the gate's level is `warn` (nothing was
    stopped). It is not spent when the gate still fails on another
    guideline's finding either, because a forced advance went past it and
    the grant decided nothing. In both cases the command prints that the
    grant is held.
  - A grant whose guideline has no new blocker finding is named in the
    detail as unused, with `--clear` as the way to withdraw it.
  - A grant's guideline is matched against the ruleset as the gate reads it
    at each evaluation. A grant whose guideline was later removed from the
    ruleset is named as unused rather than refused after the fact.
  - The base branch's ruleset, read by `baseline_only_shrinks` for
    admission, is parsed under the same DN-15 refusal, so a base whose
    guideline collides with a rule id fails that read like any other base
    ruleset that does not parse.
- *Tests.* `tests/test_guidelines_override.py` covers the arithmetic, the
  gate reading the grant, the baseline gate ignoring it and its forced-advance
  fix, each grant refusal, spending at the CLI surface, the seeded-pattern
  scenario replayed with a grant (spent at `committed`, and held under
  `standard`), and the command and status text.
  `tests/test_guidelines_kinds.py` covers the DN-15 refusal, and
  `tests/test_authority.py` covers the family gate's override route and a
  hook leaving a guideline grant unspent.

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
| 2 | scenarios over the gates A2 adds; `malformed-ruleset` landed with A2 increment 1, and `valid-ruleset` (all five kinds) with A2 increment 2 | A2 |
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
        CLI["invariant CLI<br/>guidelines render | show | verdict | baseline"]
    end
    subgraph domains["domains/ (seven)"]
        GDOM["guidelines (NEW, not an FSM)"]
        PIPELINE[pipeline]
        SESSION[session]
    end
    subgraph core["core/"]
        CGUIDE["guidelines (NEW): ruleset model, validation,<br/>selection, rendering, baseline arithmetic,<br/>verdict schema and checks"]
        CFIND["findings (A3.3 to A3.6): Finding, fingerprint,<br/>pattern, boundary and cochange evaluators,<br/>the ratchet's and only-shrinks' arithmetic; pure"]
        CAGENTS["agents (NEW, A6): host-neutral model, overlay merge"]
        RULES["rules: guidelines.* family"]
        MANIFEST[manifest]
        GIT[git]
        STORE[store]
        PRIVACY[privacy]
        FSM["fsm / machines"]
    end
    subgraph adapters["adapters/"]
        AD["lexical_mask and guideline_engines (A3.1), references,<br/>executable_text, path-aware since A3.1"]
    end
    subgraph data["shipped data"]
        AGENTS["agents/*.md (host-neutral source)"]
        PROF["profiles/*.yaml (levels for guidelines.*)"]
    end

    CLI --> GDOM
    CLI --> CGUIDE
    CLI --> CAGENTS
    GDOM --> CGUIDE
    GDOM -- plan_render, plan_baseline --> RULES
    GDOM -- records verdicts and findings --> PIPELINE
    GDOM --> STORE
    GDOM --> GIT
    GDOM --> PRIVACY
    GDOM --> adapters
    RULES --> CGUIDE
    RULES --> adapters
    RULES -- evaluate, reads handed in as callables --> CFIND
    RULES -- base-branch baseline, diff lines, tracked files, renames --> GIT
    CFIND --> CGUIDE
    CFIND -- project_relative, glob_matches --> MANIFEST
    CGUIDE -- project_relative --> MANIFEST
    FSM -. injects guidelines.* by role .-> RULES
    PIPELINE --> RULES
    CC --> CGUIDE
    CC --> CAGENTS
    CCREN --> CAGENTS
    CC --> AGENTS
    CAGENTS --> AGENTS
    RULES --> PROF
```

- **`core/guidelines`** is pure apart from reading the ruleset and its
  registries, so `core/rules` can ask it questions without importing a
  domain. It makes no `git` call (A3 increment 3, DN-13). The two `git`
  reads the family needs, the baseline at the base ref and the line numbers
  a diff adds (a new `git` query, `changed_lines`), are `core/rules`'
  (`RULES --> GIT`), as every other read of a rule is.
- **`core/findings`** evaluates the mechanical kinds and is pure: `core/rules`
  hands it the bytes, the masks and the references as callables
  (`RULES --> CFIND`).
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
no guideline is minted as a registry rule. A3 increment 7 builds it for
`guidelines.mechanical_clean` (D4, "As built: A3 increment 7"); the verdict
half waits for A4's `guidelines.verdicts_complete`.

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

## Amendment: check-kind sub-schemas (accepted 2026-10-27)

**Status: accepted.** DN-A asked for concrete schemas for the three kinds A2
increment 1 refused (`boundary`, `cochange`, `judgement`), so that A2
increment 2 had something exact to validate. The amendment was drafted with
fourteen open choices. The user ruled on all of them on 2026-10-27 and took
the recommendation every time except DN-A3. There, a boundary has two
polarities and no carve-out. A2 increment 2 validates everything below (D3,
"As built: A2 increment 2"). A3 evaluates the `boundary` and `cochange`
kinds, and A4 evaluates `judgement`.

### Rulings (DN-A, 2026-10-27)

| # | Question | Ruling |
|---|---|---|
| DN-A1 | Where a boundary's source side is declared | The rule's `applies_to`. The check adds only the target list. |
| DN-A2 | What a boundary target names | Project-relative path globs only. Third-party imports are outside the kind in v1. |
| DN-A3 | A boundary's polarity | **Either a `forbid` deny-list or an allow-only `only` list, exactly one of the two per rule**, overriding the recommended deny-list only. There is no carve-out form. |
| DN-A4 | How a cochange says which form it is | An explicit `form: pair \| registry`, which selects the key set. |
| DN-A5 | The shapes of a pair's `then` | Exactly one of `all_of`, `any_of` and `each`. `each` is a path template over `{path}`, `{dir}`, `{name}`, `{stem}` and `{suffix}`. |
| DN-A6 | The trigger's `when.change` | Required, one of `any`, `added` and `executable`. |
| DN-A7 | The registry file's format | invariant's own versioned YAML, with entries `id`, `paths`, `status` (`open` or `resolved`), `class` and `note`. |
| DN-A8 | What a registry class can produce | `blocker` or `warn`, plus an optional fix. "Stop and ask" is a blocker whose fix says so. |
| DN-A9 | When a registry is validated | By `ruleset_valid`. It is read whole or refused, and a missing file fails. |
| DN-A10 | The judgement `evidence` values | `citation`, `test`, `either` or `both`. |
| DN-A11 | Judgement `scope` | Required, `diff` or `file`, with no default. |
| DN-A12 | Mechanical N/A conditions in v1 | `no_added_match: {in, match}` and `no_executable_change`. Claims stay allowed as declared `not_applicable_when` entries (D5). |
| DN-A13 | A `candidates` hint on judgement | Deferred. Not in v1. |
| DN-A14 | When `boundary` with `imports` is accepted | A2 increment 2 validates the schema. The check that a configured adapter offers `imports` lands in A3, beside `references`. `symbols` stays refused. |

### What the schemas must carry

The schemas are fitted to the downstream project's 39 items as B1 sorted them,
and to this repository's own layering scans:

| B1 kind | Items | How each is written under this amendment |
|---|---|---|
| Owned by the engine (4) | 0a Boy-Scout scope, 0b coverage floor, 2 risk assessment, 26 simulator destination | `engine`. Under the pure ratchet 0a has no engine gate, so the project decides whether to keep it as `judgement` (Consequences). |
| Pattern, regex (7) | 4, 8, 18, 27, 28, 31, 33 | `pattern`, as built in increment 1. |
| Pattern, syntax tree (5) | 5, 6, 19, 20, 25 | `judgement` over the same `applies_to` until a syntax engine ships (ruling 3). |
| Co-change or registry (5) | 1 docs, 7 two mocks, 23 design handoff, 24 test diff, 35 duplication registry | 1 is better written as `engine: docs.updated`, which already asks it. 7, 23 and 24 are the `cochange` pair form. 35 is the registry form. |
| Judgement (18) | 3, 9–17, 21, 22, 29, 30, 32, 34, 36, 37 | `judgement`. 36 and 37 each bundle several obligations. One verdict answers one rule, so they are written as several rules (for example 36a–36d), each with its own evidence. |
| Boundary | 5 and 37's structural half | A single-module Swift app's layers are folder-to-type references, which need `symbols`. In v1 they are `judgement`. The first user of `boundary` is this repository: "nothing in `core/` imports `oracle/`" and "no `hosts` to `oracle` arrow", which the suite checks today with hand-written import scans. |

Every kind keeps the common rule keys exactly as increment 1 validates them.
In particular, `applies_to` is always the set of files the rule is *about*. The
schemas below reuse it instead of adding a second "source" glob. One spelling
of "which files" goes through `project_relative` and one matcher, so a rule
cannot disagree with itself about scope.

### `boundary`

"Does a changed file under `applies_to` reference a project file this rule
does not let it reference?" A rule states that in one of two polarities
(DN-A3), and states exactly one:

- **`forbid`**, a deny-list. A reference to a file matching any of its globs
  is a finding.
- **`only`**, an allow-only list. A reference to a project file matching none
  of its globs is a finding. The list is read as written: a rule whose files
  may reference their own layer lists that layer, so nothing is allowed
  implicitly.

There is no carve-out form: no `allow` inside `forbid`, and no `forbid`
beside `only`. A rule that needs one names the narrower globs instead, or it
is written as two rules.

| Key | Type | Required | Validation |
|---|---|---|---|
| `kind` | `boundary` | yes | |
| `engine` | string | yes, no default | `imports` only. `symbols` is refused as deferred (ruling 3), and any other value is refused naming the boundary engines. |
| `forbid` | list of glob strings | exactly one of `forbid` and `only` | Non-empty. A bare string is refused (the AUD-08 precedent). Each glob must be a non-empty project-relative path glob (DN-A2): an absolute glob, or one whose `..` climbs out of the project, is refused. A glob identical to one in `applies_to` is refused, because every file would then forbid itself. |
| `only` | list of glob strings | exactly one of `forbid` and `only` | The same as `forbid`, except that a glob may repeat one in `applies_to`, which is how a layer allows references within itself. |

Both keys present, or neither, is refused.

```yaml
- id: layers.core-never-imports-oracle
  section: layers
  title: Nothing in core imports the oracle
  statement: >
    The engine works whether or not the oracle is ever used, so no module
    under core reaches into oracle.
  severity: blocker
  phases: [review, landing]
  applies_to: ["src/invariant/core/**/*.py"]
  check:
    kind: boundary
    engine: imports
    forbid: ["src/invariant/oracle/**"]

- id: layers.core-references-only-core
  section: layers
  title: core references nothing above core
  statement: A module under core imports only other modules under core.
  severity: blocker
  phases: [review, landing]
  applies_to: ["src/invariant/core/**/*.py"]
  check:
    kind: boundary
    engine: imports
    only: ["src/invariant/core/**"]
```

A finding is one (referencing file, referenced file) pair. The adapter's
`references` answer names project-relative files, and an import the adapter
resolves to nothing in the project (a third-party package) is outside the
kind. A file the adapter cannot answer for fails as unanswerable. It does not
pass, as with a pattern rule whose adapter has no mask (D4). Rule-level
`exceptions` exempt a *referencing* path, and they stay the only escape.
Fingerprinting a finding needs the referencing line's text (D6), so A3 refines
D4's `Adapter.references(path) -> set[str] | None` to return the line beside
each target.

### `cochange`, pair form

"If a file under `applies_to` changed in this way, did the files `then` names
change too?" It is evaluated over `core/rules._changed_set`, and an
unestablished changed set fails, as it does for `docs.updated`.

| Key | Type | Required | Validation |
|---|---|---|---|
| `kind` | `cochange` | yes | |
| `form` | `pair` | yes | `pair` or `registry`, and it selects the key set, so an unknown key is refused per form. |
| `when` | mapping | yes | Keys `change`, `match`, `in`. Unknown keys are refused. |
| `when.change` | string | yes, no default | `any` (the file is in the changed set), `added` (the file is new on the branch), or `executable` (the adapter's `executable_text` differs, so a comment-only edit triggers nothing: BUG_026's seam). |
| `when.match` | regex string | no | It narrows the trigger to a file where a line the diff adds matches. It must compile. Given with `in`, or not at all. |
| `when.in` | string | with `match` | `code`, `comments`, `strings` or `any`, as for `pattern`. |
| `then` | mapping | yes | Exactly one of `all_of`, `any_of`, `each`. None, or two, is refused: this is D3's "a `cochange` whose `then` names nothing". |
| `then.all_of` | list of glob strings | one of three | Non-empty, and not a bare string. Every glob must match at least one changed file. |
| `then.any_of` | list of glob strings | one of three | Non-empty, and not a bare string. At least one changed file must match one of them. |
| `then.each` | path template string | one of three | It is evaluated once per trigger file, and the derived path must be in the changed set. The tokens are `{path}`, `{dir}`, `{name}`, `{stem}` and `{suffix}`. An unknown token is refused, and so is a template with no token. |

```yaml
# Item 24: a behaviour change needs a test diff.
- id: tests.behaviour-change-has-test-diff
  section: tests
  title: Every behaviour change carries a test diff
  statement: A change to executable Swift code under App/ comes with a change under AppTests/.
  severity: blocker
  phases: [design, review, landing]
  applies_to: ["App/**/*.swift"]
  check:
    kind: cochange
    form: pair
    when: {change: executable}
    then: {any_of: ["AppTests/**/*.swift"]}

# Item 7: a container member needs its two mocks.
- id: di.container-member-updates-mocks
  section: di
  title: A new container member updates both mock containers
  statement: >
    When the dependency container gains a member, the dummy and the mock
    container gain it in the same change.
  severity: blocker
  phases: [design, review, landing]
  applies_to: ["App/Composition/PreviewDependencies.swift"]
  check:
    kind: cochange
    form: pair
    when: {change: any, in: code, match: '^\s*(var|let|func)\s'}
    then:
      all_of:
        - App/Composition/DummyPreviewDependencies.swift
        - App/Composition/MockPreviewDependencies.swift

# Item 23: a new screen needs a design handoff.
- id: ui.new-screen-has-handoff
  section: ui
  title: A new screen or sheet has a design handoff
  statement: A new screen or sheet lands with its handoff under design/.
  severity: blocker
  phases: [design, review, landing]
  applies_to: ["App/UI/**/*Screen.swift", "App/UI/**/*Sheet.swift"]
  check:
    kind: cochange
    form: pair
    when: {change: added}
    then: {any_of: ["design/**"]}

# This repository: a core module and its paired test move together.
- id: tests.core-module-has-paired-test
  section: tests
  title: A core module change touches its paired test file
  statement: A change to src/invariant/core/X.py touches tests/test_X.py.
  severity: warn
  phases: [landing]
  applies_to: ["src/invariant/core/*.py"]
  check:
    kind: cochange
    form: pair
    when: {change: executable}
    then: {each: "tests/test_{stem}.py"}
```

### `cochange`, registry form

"Is a changed file under `applies_to` listed in an open entry of a registry,
as the registry stands after this change?" Reading the registry *after* the
change makes it a co-change. The same change that fixes a file strikes it from
its entry, or marks the entry resolved, which clears the finding. That is the
downstream rule's own instruction ("update the registry in the same change").

| Key | Type | Required | Validation |
|---|---|---|---|
| `kind` | `cochange` | yes | |
| `form` | `registry` | yes | |
| `registry` | path string | yes | Project-relative, inside the project, never a bare `.`. The file must exist and validate, and `ruleset_valid` reads it (DN-A9). Its edits take the ruleset lane (D10). |
| `outcomes` | mapping, class to result | yes | Non-empty. Each value is `blocker` or `warn`. Every `class` an open entry carries must have an outcome. An entry with no outcome is refused, never read as passing. |
| `fixes` | mapping, class to string | no | It is shown as the finding's fix, for example "scope a refactor session; do not rewrite the family at commit". Each key must also be a key of `outcomes`. |

The registry file's format is owned by invariant (D2) and is versioned:

| Key | Type | Required | Validation |
|---|---|---|---|
| `version` | integer | yes | `1` |
| `entries` | list | yes | May be empty. |
| `entries[].id` | string | yes | Unique, no whitespace. |
| `entries[].paths` | list of path strings | yes | Non-empty, project-relative paths, not globs. A registry names files. |
| `entries[].status` | string | yes | `open` or `resolved`. Only `open` entries produce findings. |
| `entries[].class` | string | yes | Project-chosen, such as `contained`. It is mapped by the rule's `outcomes`. |
| `entries[].note` | string | no | Inert. It is rendered and read by no gate. |

```yaml
# In the ruleset. Item 35.
- id: quality.duplication-registry
  section: quality
  title: A touched file is not left in an open duplication entry
  statement: >
    A touched file listed in an open entry of the duplication registry is
    resolved in the same change, and struck from the entry.
  severity: blocker          # the ceiling; outcomes choose per class
  phases: [design, review, landing]
  applies_to: ["App/**/*.swift"]
  check:
    kind: cochange
    form: registry
    registry: docs/guidelines/duplication.yaml
    outcomes: {contained: blocker, scoped-refactor: blocker, advisory: warn}
    fixes:
      scoped-refactor: stop and scope a refactor session; do not rewrite the family at commit
```

```yaml
# docs/guidelines/duplication.yaml
version: 1
entries:
  - id: DUP-07
    paths: [App/Storage/MediaStore.swift, App/Storage/ThumbnailStore.swift]
    status: open
    class: contained
    note: two copies of the suffix-normalising reader; adopt the shared one
```

An outcome may not exceed the rule's `severity`. A `blocker` outcome under a
`warn` rule is refused, so one value still gives a rule's ceiling. A
`scoped-refactor` class maps to `blocker` with a fix that says to stop.
"Stop and ask" is a fix the finding states. It is not a separate engine
result (DN-A8).

### `judgement`

"Does the change honour this rule?" It is answered by a cited verdict (D5).
These keys are what D5's validity checks read. There is no `candidates`
hint in v1 (DN-A13), so a judgement check carries no regex of its own
outside a mechanical N/A condition.

| Key | Type | Required | Validation |
|---|---|---|---|
| `kind` | `judgement` | yes | |
| `evidence` | string | yes, no default | `citation`, `test`, `either` or `both`. It sets what a `pass` or `fail` must rest on (D5, check 2). Its absence is D3's refusal of "a judgement rule with no `evidence` declaration". |
| `scope` | string | yes, no default | `diff` (at least one cited range must meet a line the diff adds or changes) or `file` (a cited range may lie anywhere in a touched file) (D5, check 3). |
| `not_applicable_when` | list of conditions | no | Absent or empty means `na` is not available, and the verdict must be `pass` or `fail` (D5, check 5). |
| `[].id` | string | yes | Unique within the rule, with no whitespace, because `--na-condition` takes it as one argument. |
| `[].claim` | string | one of two | A sentence the project wrote. The agent picks its id, and the pick is recorded for audit and the harness. |
| `[].mechanical` | mapping | one of two | The engine answers it, and no verdict is asked for. Exactly one of: `no_added_match: {in, match}` (no line the diff adds in the rule's files matches; the regex must compile), or `no_executable_change: true` (no rule file's `executable_text` changed). |

```yaml
# Item 13, the cardinality invariant.
- id: data.single-open-library
  section: data
  title: Only one library is open at a time
  statement: >
    No code path holds two per-library database or blob-root handles at once.
    A switch closes the current library before it opens the next.
  severity: blocker
  phases: [design, review]
  applies_to: ["App/Storage/**/*.swift", "App/Library/**/*.swift"]
  check:
    kind: judgement
    evidence: both            # cite where it holds, and name the test that proves it
    scope: diff
    not_applicable_when:
      - id: no-handle-code
        mechanical:
          no_added_match: {in: code, match: '\b(openLibrary|DatabaseQueue|blobRoot)\b'}
      - id: read-only-path
        claim: The change only reads through an already-open library and opens nothing.

# Item 5 while the syntax engine is deferred: the regex rule, written as cited judgement.
- id: di.views-no-concrete-services
  section: di
  title: Views never touch concrete services or singletons
  statement: >
    A view never reaches a concrete service or a shared singleton. It takes
    what it needs through the injected dependency container.
  severity: blocker
  phases: [design, review]
  applies_to: ["App/UI/**/*.swift"]
  check:
    kind: judgement
    evidence: citation
    scope: diff
    not_applicable_when:
      - id: no-executable-change
        mechanical: {no_executable_change: true}
```

### Changes this makes elsewhere in the record

- **D3, the validation list** gains these refusals, which A2 increment 2
  makes:
  - a `boundary` with neither or both of `forbid` and `only`, or with an
    engine other than `imports`;
  - a `cochange` with no `form`, or a `then` naming zero or two shapes;
  - a template token outside the five;
  - a registry that is missing or does not validate, or an open class with
    no outcome;
  - a judgement with no `evidence` or no `scope`;
  - a duplicate condition id, and a condition that is neither or both of
    `claim` and `mechanical`.
- **D4, `boundary`.** The table row and the kind's paragraph name both
  polarities. `references` returns each target with its line (above).
  `ruleset_valid` checks that a configured adapter offers `imports` from A3
  onward (DN-A14).
- **D4, `cochange`.** The registry form's file format and its outcomes are
  the ones above (DN-A7, DN-A8).
- **D10.** A registry a ruleset names is already in the ruleset lane. This
  amendment makes "names" concrete: the `registry` key.
- **`core/guidelines`.** `Check` gains the fields of the three kinds, and
  `VALIDATED_KINDS` grows to all five.
- **The consumption table** below names what A3 and A4 take from this
  amendment.

## Increments, and the order in which A1–A8 consume these decisions

| Deliverable | Consumes | Can start when |
|---|---|---|
| A1 conformance harness | D1, D11; the scenario fixtures use D3's format and location (rulings 1, 2) and D5's schema (ruling 6); the driven layer's hosts (ruling 12) | A0 accepted. The engine layer grows as A2–A4 land; the driven layer needs A4 |
| A2 ruleset core | D2, D3 (format, location, validation, rendering, managed sections, drift, pin, the generated-file write class), D13's `guidelines.ruleset`, `checklist`, `instruction_files`; rulings 1, 2, 4 (`severity` in the schema), 8 (managed-block content), 13; the check-kind amendment's schemas and the registry file (DN-A1 to DN-A12, and DN-A14's schema half) | A0 accepted |
| A3 mechanical checkers | D4 (engine, regex pattern, import-graph boundary, cochange; where regex stops), D6 (baseline, pure ratchet, stale-entry prune), D12's override by guideline id, D13's `guidelines.baseline`; rulings 3, 4, 5, 7, 9, 13 (`baseline --prune`); from the check-kind amendment, evaluating `boundary` in both polarities with `references` returning each target's line, the adapter-offers-`imports` check (DN-A1 to DN-A3, DN-A14), and evaluating both `cochange` forms against the registry as it stands after the change (DN-A4 to DN-A9) | A2 |
| A4 verdict contract | D5 (schema, validity, vagueness, staleness, gate), the ruleset hash from D3; rulings 6, 7; from the check-kind amendment, the judgement keys a verdict is checked against (`evidence`, `scope`, declared claims) and the engine's own answer to the mechanical N/A conditions (DN-A10 to DN-A12) | A3, because mechanical findings share the record shape |
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
