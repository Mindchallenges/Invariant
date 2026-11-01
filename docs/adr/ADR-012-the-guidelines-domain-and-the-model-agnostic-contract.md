# ADR-012 — The guidelines domain, and the model-agnostic contract it rests on

- **Status:** Accepted. IDEA_031 deliverable A0. The user ruled on all
  thirteen questions on 2026-10-27 ("Rulings" below), overriding one
  recommendation: the ratchet is always pure, and there is no Boy-Scout switch
  (ruling 5, D6). A1–A8 build it in the order the consumption table gives,
  and each increment's as-built note records what landed. The check-kind
  amendment was accepted the same day, with its own rulings table (DN-A1 to
  DN-A14). A4 closed with increment 8, whose design and three rulings
  (a to c) are the user's. A5 closed with increment 4, built to the user's
  twelve A5 rulings (D1 to D12, under D7): all three layers of ruling 8 are
  built. A6 is built to the user's fourteen A6 rulings (under D8), in five
  increments; increments 1 and 2 are built.
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
      verdicts (A4 increment 5 lifted this; see "As built: A4 increment 5");
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

**As built: A3 increment 8, the edit-time advisory at `PostToolUse`.** Built
to ruling 9 (advisory at the edit, held at the landing), D7, D12's host row
for `guidelines.mechanical_clean`, `guidelines.injection.max_lines` (DN-C) and
DN-8. D7's `PreToolUse` wording is corrected to `PostToolUse` above, as
ruled. A3 does not touch `PreToolUse`.

- *The projection, in `core/rules` (DN-8).* `edit_advisory(manifest, target,
  context)` answers an `EditAdvisory`: the file's project-relative path, its
  `AdvisedFinding`s, and at most one `problem` line, or `None` for silence.
  It evaluates the `pattern` and `boundary` guidelines (`ADVISED_KINDS`)
  whose `applies_to` covers the one edited file, through `core/findings`
  with the reads `_finding_reads` hands the gate, and applies the ratchet
  with the file as the touched set. Only a finding the ratchet leaves `new`
  is advised, at either severity. `cochange` is never evaluated: it
  describes the whole change. It is never a deny, a rule event or a
  verdict, so no level applies, and it writes nothing.
- *Silence.* `None` when no ruleset is configured, when the edited file is
  outside the tree the gate reads (`guidelines_tree`: the session's
  worktree when it has one), when no advised guideline's `applies_to`
  covers it, and when nothing new is found.
- *Cost (DN-8).* The ruleset key is read from the manifest before anything
  else. With none configured, `context` is never called, so no rule context
  is built, no file is read and no `git` is asked.
- *One short line.* An unanswerable file names the guidelines that could
  not answer and why. A ruleset that does not load names its failure and
  `invariant check guidelines.ruleset_valid`. Any exception inside the
  projection is caught and becomes a line naming its type and that the
  landing gate still evaluates the edit.
- *The host only formats (`hosts/claude_code`).* `_post_tool_use` asks the
  projection for every edit tool, after the binding, the first-edit report
  and the actor are recorded, passing the rule-context builder as a
  callable. `render_advisory` writes a header naming the file and the gate,
  the problem line, then one line per finding. The text is
  `additionalContext`, never a `permissionDecision`. Past
  `guidelines.injection.max_lines` it keeps `max_lines - 2` items and ends
  with "- N more: `invariant check guidelines.mechanical_clean`", the shape
  of the managed block's overflow (DN-C). A context with no manifest is
  silent, and an exception anywhere in the step leaves the hook silent and
  every recording it made in place.
- *Model-agnostic (D1).* Each finding line is plain facts: rule id,
  severity, `path:line`, the matched line, and a fix naming the line or the
  referenced file and the finding's path and fingerprint. Nothing names a
  host, a model or a tier.
- *Small points the rulings left open, taken in the strict direction.*
  - A warn guideline's new findings are advised beside a blocker's, with
    the severity named, because the landing gate reports them too.
  - A live grant naming a guideline is not applied. A grant passes one
    evaluation of the landing gate and is spent there, so at the edit the
    finding is still new.
  - A baseline that does not load gets one short line, like a ruleset that
    does not load. Without it no finding can be told new from baselined.
  - "The session's tree" is the tree the gate reads. An edit in the main
    checkout while the session works in a worktree is silent, and so is any
    path that resolves outside the tree. With no session, that tree is the
    checkout the hook runs in.
  - The path is the edited file resolved against the resolved tree, with no
    trimming, so a name with whitespace at an end is read as itself
    (BUG_104 stays open for the gates). A relative path is read against the
    working directory.
  - A path that is no file, and a file that is not UTF-8 text, are silent.
    Neither holds a finding at landing.
  - An unanswerable guideline beside a new finding of another gives both:
    the problem line and the findings.
  - The matched line is quoted with its whitespace collapsed, as its
    fingerprint is, and cut at 160 characters ending in `...`.
  - The fix is the projection's, not the gate's general fix, so each line
    stands alone. It names what to change and the exception that would set
    the finding aside.
  - An exception inside the projection gives a line. One in the host's own
    step (building the response) gives silence, since nothing is left that
    could be trusted to say it.
- *Tests.* `tests/test_guidelines_advisory.py` (what is advised, the
  ratchet, exceptions, cochange left out, each silence including the
  no-ruleset cost, and each short line) and `tests/test_host_claude_code.py`
  (the format, the cap and its overflow line, each silence, an exception
  leaving the hook silent and the edit recorded, and against a real
  repository through `handle` and through `invariant hook post-tool-use`: a
  worktree edit advised, a main-checkout edit and a clean edit silent).

**As built: A3 increment 9, the dogfood ruleset.** D4 names this repository
as the first user of `boundary`, and the user ruled (option A) on how its
hand-written layering scans become ruleset entries.

- *The ruling.* The suite held five import scans. Four are about layers of
  this package, and two boundary rules cover them together:
  - `layers.only-the-cli-imports-the-oracle`, over
    `src/invariant/{core,domains,adapters,hosts}/*.py`, forbidding
    `src/invariant/oracle/*`. It covers the scans in `test_oracle_index.py`
    (core, domains and adapters) and `test_oracle_attention.py` (hosts).
  - `layers.oracle-never-imports-hosts`, over `src/invariant/oracle/*.py`,
    forbidding `src/invariant/hosts/*`. It covers the scans in
    `test_oracle_scan.py` and `test_oracle_watcher.py`, the second a subset
    of the first.

  The fifth, "only the dashboard module imports Textual"
  (`test_oracle_tui.py`), is about a third-party package. DN-A2 puts that
  outside the kind, so no rule expresses it and the scan stays as it is.
- *The ruleset.* `docs/guidelines/ruleset.yaml`, one `layers` section, both
  rules `blocker` and `imports`, each with a rationale that says how to fix a
  finding and links `docs/architecture/overview.md` and ADR-007 (D6 for the
  hosts direction, D11 for the probe). The globs are `*.py` under each
  layer, not `**/*.py`: `*` crosses `/` in the one matcher, so nested
  modules are covered, and `**/` would miss each layer's top-level modules
  (A3.3's glob note).
- *The manifest.* `guidelines.ruleset` names it. `ecosystems: [python]`
  already gives the `imports` engine (DN-A14). No baseline is configured
  (DN-1): the whole tree has no finding under either rule and no file either
  rule cannot answer for, so there is nothing to record and every finding
  would be new.
- *The level.* `guidelines.mechanical_clean` stays at the `standard`
  profile's `warn` in this change. The build installed here runs the main
  checkout's code, which carries no such rule until this branch merges, and
  a manifest naming a rule the build lacks does not load (BUG_050). That
  would leave every hook re-rooted into this worktree unable to load its
  manifest until the merge. Raising it to `block` is a one-line manifest
  change once the rule is on the main checkout; a follow-up chore made it
  right after the merge, so here the rule blocks as DN-17 ruled.
- *Rendered.* `guidelines render` wrote `docs/guidelines/checklist.md`.
  This repository has no `CLAUDE.md`, and render inserts a block only on
  `--insert`, which would create the file, so no `guidelines/index` block
  was written. An absent block is no drift.
- *The scans are kept (DN-17).* Each of the four carries a comment naming
  the rule that now covers it. Their assertions are unchanged. Retiring them
  is a later step.
- *Tests.* `tests/test_dogfood_ruleset.py`: the manifest names the ruleset,
  it passes `ruleset_valid` and holds exactly the two boundary rules with
  the ruled globs and no `**`, no baseline is configured, every module
  under `src/invariant` gives no finding, and a fixture copy with a seeded
  import is found for each layer, for a nested core module and for the
  oracle importing a host, while the CLI importing the oracle is not.

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

**The A4 rulings (DN-1 to DN-12).** A design-only architect run drafted A4,
and the user ruled on its twelve decisions. The user took the recommendation
on DN-1 to DN-11 and overrode it on DN-12. A4 is built in eight increments,
recorded on its session's plan: the record and its submission; the
mechanical N/A conditions; the `guidelines.verdicts_complete` gate;
`guidelines verdicts show`; override by guideline id for judgement
guidelines; the landing's guidelines records in pipeline evidence; the
`vague verdict` and `judgement fail` scenarios (A1 increment 4); and the
host's stamp of the acting agent.

| # | Question | Ruling |
|---|---|---|
| DN-1 | Where the verdict logic lives | A new pure module, `core/verdicts`, beside `core/findings`. It holds the verdict record, the parsing of a submission and the pure checks behind D5's predicates. File bytes, added lines, test runs and the pin arrive as values or callables, and it does no I/O. |
| DN-2 | Where a verdict is accepted, and which rules owe one | Only in a review-role state. A rule is owed when its `phases` name `review`, `landing` or `always`, so a design-only rule owes none. |
| DN-3 | How a change meets a judgement rule's `applies_to` | Through a changed file that is present in the tree, under `applies_to`, and not path-excepted. A deleted file does not count. |
| DN-4 | Where a `scope: file` citation may point | Into a touched file only. |
| DN-5 | Whether a cited path must be under the rule's `applies_to` | No. Any touched file may be cited. |
| DN-6 | A ruleset edited mid-pipeline | Submission is refused while the tree's ruleset digest differs from the pipeline's pin, and the refusal names both. The verdict is stale at the gate, and nothing repins. A pin with a null digest, or no pin, compares against the tree's digest. |
| DN-7 | The landing's guidelines records | Saved into pipeline evidence (increment 6). |
| DN-8 | When a test counts | Only when its selector exactly equals an entry in the `selectors` of a `test_runs` record in this pipeline. For a pass, the latest run naming that selector must be green: passed, and not `read_as_red`. A full run never vouches for a named test. |
| DN-9 | What a grant naming a judgement guideline covers | All of that guideline's failures, a warn guideline's included (increment 5). |
| DN-10 | The review agent's text | Not edited until A6 renders the agents host-neutrally. |
| DN-11 | A bulk submission with an invalid entry | Refused whole. Every problem is named per entry, and nothing is recorded. |
| DN-12 | Who submitted a verdict | **The host stamps the acting agent onto each verdict**, overriding the recommendation to leave it unrecorded. It is increment 8. It is audit only: no gate may ever read it (D1 item 2). |

**As built: A4 increment 1, the verdict record and its submission.** Built
to D5, ruling 6 and DN-1 to DN-6, DN-8, DN-11 and DN-12. There is no gate
yet: `guidelines.verdicts_complete` is increment 3.

- *The commands.* `invariant guidelines verdict <rule-id> --status
  pass|fail|na [--cite <path> <first> <last>]... [--test <selector>]...
  [--na-condition <id>] [--note "<text>"]`, and `invariant guidelines
  verdicts submit <file.json>`, a JSON list of D5 objects. The single form
  builds a list of one, so both take one path through the checks. The
  session is the CLI's rule context's, resolved by working directory, which
  is why BUG_082 does not touch it. Both refuse without a running pipeline.
- *`core/verdicts` (DN-1).* `Submitted` and `Cited` are a submission as
  written; `parse_entry` and `parse_submission` read one, naming every
  problem of its shape. `Facts` carries every read the checks need, as values
  and callables. One function per predicate (`applies_problems`,
  `evidence_problems`, `citation_problems`, `selector_problems`,
  `na_problems`, `note_problems`) answers `Problem`s, each with its
  predicate and its fix. `check` runs them all over one entry, and
  `check_all` over a submission. It imports the ruleset model, `digest_of`,
  `project_relative`, and `core/findings`' `matches`, so a judgement rule's
  globs match as every other guideline's do.
- *The plan, `core.rules.plan_verdicts`.* It is the one validation, so A4.3's
  gate can ask the same plan, as `plan_render` serves render and the drift
  gate. It does every read from the tree in hand, as every guidelines gate
  reads it:
  - the ruleset, through `load_ruleset`;
  - the pin, from the pipeline record;
  - the state's roles, from the pinned methodology;
  - the changed set, through `_changed_set`, and its files present in the
    tree;
  - the cited files' bytes;
  - the lines the change added (`git.changed_lines` from the merge base);
  - the pipeline's `test_runs`;
  - the privacy scan of the note.

  It records nothing. `VerdictPlan.ok` holds only when there is an entry and
  no problem anywhere.
- *The predicates.* Every refusal exits 1, names its predicate and its fix,
  and writes nothing.
  - P1: the rule is a judgement guideline; the tree's digest equals the pin
    (DN-6); the change meets `applies_to` (DN-3); the state carries the
    `review` role and the rule's phases name review, landing or always
    (DN-2).
  - P2: a `pass` or `fail` rests on the evidence kinds `evidence` allows.
  - P3: each citation is spelled project-relative and names a UTF-8 text
    file in the touched set, present in the tree (DN-4, DN-5), with
    1 <= first <= last <= its line count. For `scope: diff`, at least one
    cited range meets a line in `git.changed_lines`. No base recorded, no
    merge base, or added lines git cannot give make the verdict unanswerable,
    and it is refused.
  - P4: tests, per DN-8.
  - P5: an `na` names a claim condition of the rule. Naming a mechanical one
    is refused, because the engine answers it (increment 2). An `na` carries
    no citations and no tests.
  - P6: the note is at most `guidelines.verdicts.note_max_chars` characters
    and privacy-scanned whatever `privacy.scope` says. A finding refuses.
- *The record.* `Pipeline.evidence["verdicts"]` is an append-only list,
  written by `PipelineDomain.record_verdicts`, with `domains/guidelines`
  performing the submit (`GDOM --> PIPELINE`, on the `stats --> session`
  precedent). Each entry holds `rule`, `status`, `citations` (`path`, `lines`
  `[first, last]`, `blob`), `tests`, `na_condition`, `note`, `ruleset`,
  `state` and `surface` (`cli`). `blob` is `digest_of` the cited file's
  bytes in the tree. `ruleset` is the pin, and `state` the state the verdict
  was checked in. There is no clock time. A later verdict on a rule is
  appended beside the earlier one; which answers the rule is the gate's
  question.
- *DN-12's extension point.* `VerdictRecord.agent` is optional and `None`
  in this increment. It is written only when set, so the record keeps its
  ruled shape until increment 8 sets it. No check reads it. *Amended by
  increment 8:* the record is reshaped once after all. The user ruled that
  the host may set `agent` once on an otherwise append-only record (ruling
  a), and each record also carries the submission's `token` (ruling b). See
  "As built: A4 increment 8".
- *The manifest.* `guidelines.verdicts` is accepted, with one key,
  `note_max_chars`, default 280. `injection.on_edit` is still refused by
  name.
- *The reading of DN-6 taken.* "A pin with a null digest, or no pin,
  compares against the tree's digest" is read as "the tree's digest stands
  as the pin". The digest check then passes, and the verdict records the
  tree's digest. The other reading, refusing every verdict, would wedge the
  ruleset lane's own repair, as A2 increment 6 found for `ruleset_valid`. A
  ruleset that does not load in the tree is refused whole, whatever the pin
  says.
- *Small points the rulings left open, taken in the strict direction.*
  - A kind of evidence the rule does not allow is refused, not ignored: a
    test on a `citation` rule, a citation on a `test` rule. It would be
    evidence on record that no check had weighed.
  - A submitted object may carry `ruleset` and a citation's `blob`, as D5's
    example does, and each must equal what the engine records. `state`,
    `surface` and `agent` are the engine's alone and are refused as unknown
    keys, and so is `token` since increment 8 (ruling b).
  - One rule answered twice in one submission is refused at its second
    entry. A submission that is not a list, or is empty, is refused whole.
    A null `citations` or `tests` is refused rather than read as an empty
    list; leaving the key out is how an entry says it has none.
  - A `pass` or `fail` naming an `na_condition` is refused, and so is an
    `na` on a rule that declares no condition.
  - The diff predicate is asked over the citations that held, and only when
    one did. A verdict with no valid citation is refused already, so git is
    not asked.
  - A path exception with a fingerprint excuses a finding, and a verdict has
    none, so it sets no file aside under DN-3.
  - A cited path must be its own canonical spelling. `./app/m.py` is refused
    naming `app/m.py`, and nothing is trimmed (BUG_104 stays open for the
    gates).
  - A file's lines are counted on line feeds, as findings count them. A last
    line without a line feed counts, and an empty file has no line to cite.
  - A `fail` resting on a test needs a run that named it, red or green; only
    a `pass` needs the latest run green. A run record whose `selectors` is
    not a list names nothing.
  - A note carrying the privacy exemption marker is refused. A note cannot
    exempt itself, since nothing it says is read by any gate. The scan runs
    whatever `privacy.enabled` says too, as render's does.
  - `note_max_chars` is a positive integer. Zero is refused.
  - `record_verdicts` refuses records checked in a state the pipeline has
    since left, with nothing written. The DN-2 check was made against a
    state the pipeline is no longer in.
  - A refusal of the whole (no session, no pipeline, no ruleset configured,
    a ruleset that does not load, a file that cannot be read or is not JSON)
    is the same `VerdictRefused`, exit 1, named `pipeline` or `submission`.
    A `--cite` line that is not an integer is refused by the plan, exit 1,
    not by the argument parser.
  - A mechanical guideline named in a verdict is refused at P1, naming the
    gate that answers it.
- *Tests.* `tests/test_guidelines_verdicts.py` covers the record and its
  blob, the agent extension point, the parsing, each predicate and each
  refusal, `plan_verdicts` over real repositories (scope diff, no base, an
  unknown base, a deleted file, test-run selectors, the pin, the roles, the
  privacy scan, the cap), the domain's append-only record, and bulk
  submission. `tests/test_guidelines_domain.py` covers both commands through
  `main`, and `tests/test_guidelines.py` the manifest key.

**As built: A4 increment 2, the mechanical N/A conditions.** Built to D5
item 5, the check-kind amendment's DN-A12 and the `judgement` table, and
DN-1, DN-3 and DN-11. The vocabulary was already fixed by DN-A12, so no new
ruling was needed: `no_added_match: {in, match}` and
`no_executable_change: true`. Still no gate reads verdicts; that is
increment 3.

- *`core/findings.na_answer` (DN-1, pure).* It answers one mechanical
  condition of one rule over the change as an `NaAnswer`: `holds` is true,
  false (the reason names each file and line that makes it false) or `None`,
  unanswerable (the reason names why). The reads arrive as they do for
  `guidelines.mechanical_clean`: `read`, `mask` and the cochange kind's
  `History`.
  - `no_added_match`: a line the change added to a rule file (git's
    account, untracked files whole) matches the regex, through the mask its
    `in` names, line by line as a pattern is (DN-2). Any such line makes it
    false.
  - `no_executable_change`: a rule file's `executable_text` differs between
    the merge base and the tree, a new file compared with an empty one. Any
    such file makes it false.
- *The rule's files are DN-3's.* `findings.meets` is the one reading: a
  changed file present in the tree, under `applies_to`, not set aside by a
  path exception. It moved from `core/verdicts` to `core/findings`, so P1
  and P5 read one set, and `CVERD --> CFIND` carries `meets` and `NaAnswer`
  where it carried `matches`. A deleted file is asked nothing, as it owes
  nothing.
- *One reading of the change.* The cochange `pair` trigger's reads of a file
  (did it exist at the merge base, its bytes there, its executable text, the
  added lines matching through a mask) were lifted out of `_Pairs` into
  `_ChangeFacts`, which both the trigger and `na_answer` use. "Its
  executable code changed" therefore means one thing in a cochange rule and
  in an N/A condition.
- *P5, wired.* `Facts.na` is the engine's answer, as a callable `core/rules`
  builds in `plan_verdicts` from the reads above. An `na` naming a mechanical
  condition is accepted only when the answer holds. False is refused, P5,
  naming the lines or files. Unanswerable is refused, P5, naming why. The
  fix in both cases is to answer pass or fail, or to name a true claim. With
  no changed set, P1 already refuses the verdict, and P5 asks the engine
  nothing. With no answer read, P5 refuses rather than passing. Increment 1
  refused every mechanical `na`; that refusal is gone.
- *The reading of D5 item 5 taken.* "The engine answers, and no verdict is
  asked for" is read as follows. Whether a rule is owed at all when one of
  its mechanical conditions holds is increment 3's question, and
  `na_answer` is what its gate will ask. A submitted `na` naming that
  condition is accepted when the engine agrees, since it then records only
  what the engine found. It is never accepted on the agent's word.
- *Validation (A3.1 deferred it here).* `guidelines.ruleset_valid` refuses a
  `no_added_match.in` of `code`, `comments` or `strings` unless a configured
  adapter offers `mask`, as it refuses a cochange `when.in`. `in: any` needs
  no mask.
- *Small points the rulings left open, taken in the strict direction.*
  - A false answer is definite. A file nothing could answer for, beside a
    file that makes the condition false, does not make it unanswerable. The
    reason names both.
  - Unanswerable is never true: no history, a merge base or an existence
    git cannot give, a base blob it cannot read, added lines it cannot give,
    no mask, a mask that moves lines, no executable text, and a file that
    cannot be read.
  - A file that is not UTF-8 has no added line to match, as for a pattern
    and a `when.match`, so `no_added_match` does not fail on it, and the
    answer names it. Under `no_executable_change` it is unanswerable, as an
    `executable` trigger on it is.
  - A file git shows unchanged added no line, so it cannot make
    `no_added_match` false.
  - A claim is never put to the engine. Asked of one, `na_answer` answers
    unanswerable.
  - A pass or fail on a rule whose mechanical condition holds is not
    refused here. Whether such a rule is owed is increment 3's question.
- *Tests.* `tests/test_guidelines_na.py`: both conditions and every
  unanswerable fact, the rule's files (applies_to, path exceptions, order),
  `meets` as the one reading, the answer's text, P5 over `Facts` (true,
  false, unanswerable, not read, no changed set, a claim never put to the
  engine) and `plan_verdicts` over real repositories (a comment-only change,
  a code change, an added handle, no base, a base git cannot find).
  `tests/test_guidelines_verdicts.py` and `tests/test_guidelines_kinds.py`
  carry the amended P5 and mask assertions.

**As built: A4 increment 3, `guidelines.verdicts_complete`.** Built to D5
("The gate", "Staleness", item 5), ruling 7 and DN-2, DN-3, DN-6, DN-8 and
DN-12. It reuses the plan's reads and the submission's checks rather than
deriving either again.

- *The gate (`core/rules`).* `guidelines.verdicts_complete` reads the
  ruleset through `load_ruleset`, the changed set through `_changed_set`,
  and every other fact through `_verdict_facts`, the reads `plan_verdicts`
  makes, so a verdict is checked at the landing against exactly what
  submission read. It asks `core/verdicts.standings` over the pipeline's
  `evidence["verdicts"]`. It is N/A with no ruleset or no running pipeline,
  and fails on a ruleset that does not load and on a changed set that
  cannot be established, as `mechanical_clean` does.
- *Owed (`core/verdicts.owed`, DN-2, DN-3).* A judgement rule whose phases
  name review, landing or always, met through `findings.meets` by a changed
  file present in the tree, under `applies_to` and not path-excepted. A
  design-only rule and a deleted file owe nothing.
- *The engine's N/A (D5 item 5).* For each owed rule, `na_answer` is asked
  of its mechanical conditions in declared order, through `Facts.na`. The
  first that holds answers the rule: no verdict is owed, and the detail
  names the condition and the engine's reason. False, unanswerable, or no
  answer read leaves the rule owed. A claim is never put to the engine.
- *A pass or fail on such a rule (increment 2's open point), ruled by the
  user.* D5 puts the gate over "any applicable judgement rule", and a
  `not_applicable_when` condition that holds is the ruleset saying the rule
  does not apply to this change. So the engine's answer stands: a pass or
  fail recorded on the rule is set aside, never counted, and named in the
  detail ("the fail recorded on it is set aside"). A recorded `na` on it is
  not named, since it agrees. This reading is the strict one for the
  record, not for the change: a fail is never silently dropped.
  This is the user's ruling for the increment, on top of the D5 reading:
  the engine's N/A answers the rule and the recorded fail does not block.
- *Which record answers (D5, increment 1's "the gate's question").* The
  latest record on the rule. It is read back as the submission it was
  (`resubmitted`, dropping the engine's own `state`, `surface` and `agent`)
  and put through `check` again, with the recorded `ruleset` and blobs,
  under `submitting=False`. An earlier record is never brought back over a
  latest one gone stale: the latest is the submitter's current word.
- *Staleness, re-checked at gate time.* Every predicate submission ran,
  P1 to P6, except P1's review-role predicate, which is submission's alone
  (DN-2; the landing is not a review state, and `record_verdicts` already
  refused a record from a state the pipeline had left). So a verdict is
  stale when a cited file's blob changed (P3), a cited file is no longer in
  the touched set or a cited range no longer meets an added line under
  `scope: diff` (P3), the tree's ruleset is not the pin or not the digest
  the record holds (P1, DN-6; with a null pin the tree's digest stands, as
  increment 1 built it, so an edited ruleset still makes it stale), a test's
  latest run naming it is not green (P4, DN-8), or a mechanical `na` is no
  longer true (P5). A record that no longer parses is stale too. Stale is
  missing (D5).
- *The verdict.* It fails on a missing or stale verdict, at any severity,
  and on a current `fail` at blocker severity, naming each rule ("no
  verdict", "the latest verdict no longer holds (P3: ...)", "fail at
  blocker") and a fix per rule (`invariant guidelines verdict <rule>`, or
  the cited code for a fail). A current `fail` at warn severity is reported
  in the detail of a pass (D5). Nothing is recorded, and no gate reads
  `agent` (D1 item 2, DN-12).
- *Severity (ruling 7).* `warn` in `loose` and `standard`, `block` in
  `strict`, as `mechanical_clean`. It rides `GUIDELINES_GATES` at the
  landing entry, injected by role. This repository's manifest is left at
  the profile's level: naming the gate in `enforcement.rules` before the
  installed build registers it would fail to load (BUG_050), so raising it
  is a chore after this lands.
- *Left for later increments.* `guidelines verdicts show` (A4.4, built
  below), a grant
  naming a judgement guideline (A4.5; the override refusal now says no
  grant passes the gate yet), and the landing's records in pipeline
  evidence (A4.6).
- *Conformance.* The seven scenarios with a ruleset carry the gate in their
  known answer: `pass` where no judgement rule is owed, `warn` in
  `malformed-ruleset`. `valid-ruleset` owes its judgement rule a verdict
  (`app.py` is new, so `no-executable-change` is false), and now submits a
  cited pass once the change is committed, so it still lands like the clean
  change with the gate passing. It is placed after the commit because the
  changed set does not list an untracked file the scenario wrote without
  staging, which is `_changed_set`'s existing reading, not this gate's.
- *Tests.* `tests/test_guidelines_verdicts_complete.py`: owed (phases,
  applies_to, path exceptions, no changed set), missing, current, the
  latest answering, a stale latest not rescued, every staleness predicate,
  the engine's N/A with a pass or fail set aside, warn and blocker fails,
  and the gate over real repositories (levels per profile, the injection,
  N/A and failing preconditions, a deleted file, a comment-only change, an
  edit after the verdict, a ruleset edited mid-pipeline).
  `tests/test_conformance_engine.py` carries the new gate.

**As built: A4 increment 4, `guidelines verdicts show`.** Built to D5 ("The
gate", "Staleness", item 5), the user's ruling on a pass or fail the
engine's N/A sets aside (A4.3), and DN-2, DN-3 and DN-12. The ADR sketched no
shape for the command, so it follows cli.md's conventions and the other
guidelines commands.

- *One read, not two (`core/rules`).* The gate's body became two functions:
  `read_standings` makes every read the gate made (`load_ruleset`,
  `_changed_set`, `_verdict_facts`, `evidence["verdicts"]`) and answers a
  `StandingsRead` (`standings`, `no-ruleset`, `no-pipeline` or
  `unanswerable` with the gate's failure); `standings_verdict` is the gate's
  verdict over it, unchanged in wording. `guidelines.verdicts_complete` is
  `standings_verdict(read_standings(ctx))`, and the command prints the same
  read and renders that same verdict, so the two cannot disagree. A test
  pins the gate's verdict equal to `standings_verdict` over the read.
- *Why a rule is not owed (`core/verdicts`, DN-2, DN-3).* `not_owed` answers
  the reason, or `None` for an owed rule: not a judgement guideline, phases
  naming neither review, landing nor always, no changed set, or no changed
  file meeting it through `findings.meets`. `owed` is now every rule
  `not_owed` finds no reason for, so the gate and the listing read one
  predicate. `unowed` lists each rule with a recorded verdict the change no
  longer owes, in first-recorded order, with its latest status, how many were
  recorded and the reason (or that the rule is gone from the ruleset). The
  gate never reads it.
- *The command (`domains/guidelines`, the CLI).* `GuidelinesDomain.
  verdict_standings` resolves the session as a submission does and returns
  the read, raising `StandingsRefused` (exit 1) with the gate's message,
  detail and "try:" fixes where the gate fails with no standings to show,
  or with no session. The text is one line per owed rule led by its
  standing (current, stale, missing, engine) and `Standing.describe`, then
  the rules no longer owed, then the gate's line as a gate renders it at
  the manifest's level. `--json` carries the same, per cli.md.
- *Readings taken where the ADR is silent.*
  - It exits 0 whenever it shows the standings, a failing gate included, as
    `pipeline gates` does. It is a read; `invariant check
    guidelines.verdicts_complete` is the gate's exit, and a script wanting
    "would the landing pass" asks that.
  - Where the gate is N/A (no ruleset, no running pipeline) it says no
    verdict is owed and exits 0, as `guidelines show` does with no ruleset.
    Where the gate fails without standings (a ruleset that does not load,
    a changed set that cannot be established) it refuses, exit 1, since it
    has nothing true to show beyond the gate's failure.
  - It answers in any state. The gate re-checks without the review-role
    predicate (DN-2), so the standing does not depend on where the pipeline
    is.
  - It does not show `agent` (DN-12). Nothing sets it before increment 8,
    and showing it is not owed; no logic reads it. Increment 8 sets it and
    still does not show it here.
- *Boy-Scout, the gate's fix for an edited citation.* At the gate a cited
  file whose blob changed was named with submission's fix, "leave the blob
  out; the engine records it", which is wrong advice for a recorded verdict.
  `citation_problems` now takes `submitting`, and at the gate names both
  blobs and asks for the rule again. Submission's wording is unchanged.
- *Left for later increments.* A grant naming a judgement guideline
  (A4.5), the landing's records in pipeline evidence (A4.6), the conformance
  scenarios (A4.7) and the agent stamp (A4.8).
- *Tests.* `tests/test_guidelines_verdicts_show.py`: `not_owed` for each
  reason and as the one reading `owed` asks, `unowed` (owed rules left out,
  a rule gone, the latest status and count, order, records naming no rule),
  the gate-time citation fix, `read_standings` for every status and equal
  to the gate's verdict, and the command through `main` (each standing, the
  set-aside fail, the rules no longer owed, exit 0 on a failing gate,
  `--json`, any state, no session, no pipeline, no ruleset, a ruleset that
  does not load, and that it writes nothing).

**As built: A4 increment 5, override by guideline id for judgement
guidelines.** Built to ruling 4, D12 ("A single guideline's level"), DN-9
and DN-12, the user's A4.3 ruling on the engine's N/A, and A3 increment 7's
mechanism. There is no second override path and no new record shape: a
judgement grant is the A3.7 grant, held in `session.pending_overrides` and
spent into `session.overrides` with the guideline id as its `rule`.

- *A note on numbering.* The session brief called the coverage ruling DN-10.
  In the table above it is DN-9 ("What a grant naming a judgement guideline
  covers"), and DN-10 is the review agent's text. The ruling is the same.
- *The grant (`core/rules.guideline_override_refusal`).* A judgement
  guideline is granted at either severity, because a missing or stale
  verdict fails the gate whatever the rule's severity, so a warn guideline
  can be stopped too. A judgement guideline whose phases name neither
  review, landing nor always is refused: it is never owed a verdict, so
  nothing could ever spend the grant (A3.7's rule, ADR-001 D25). The other
  refusals are unchanged: an engine guideline names its rule, and a warn
  mechanical guideline can never block. `--calls` above one is refused,
  naming the family gate. `guideline_family_gate` is the one reading of
  which gate a guideline's grant passes, and `guideline_gate_of` reads it
  from the ruleset of the tree in hand for the CLI's text and the session's
  refusal.
- *Each family gate reads its own grants.* `guideline_grants(ctx, ruleset,
  judgement=...)` splits the live grants by the guideline's kind.
  `mechanical_clean` reads every grant that does not name a judgement
  guideline, so it still names a grant whose guideline left the ruleset as
  unused (A3.7). `verdicts_complete` reads the judgement ones. Neither names
  the other's grant.
- *What a grant covers (DN-9, `core/verdicts`).* `standings` takes the
  grants. A granted rule whose standing is missing, stale or a current fail,
  at blocker or at warn, keeps that standing and is marked with the grant's
  reason (`Standing.override`). It neither blocks nor warns. `failing` is
  what the rule's answer alone would do, and `describe` adds "passed by
  override, granted with `invariant override <rule>` (reason: ...)". The
  engine's mechanical N/A is asked first and answers the rule, so a grant
  on it passes nothing. That is the user's A4.3 ruling: the engine's answer
  stands over any record, and over any grant. A current pass or `na` leaves
  the grant passing nothing. `unused_grants` names each such grant and why:
  its latest verdict holds, the engine answers it, or the change does not
  owe it a verdict.
- *The gate and the command agree.* `read_standings` reads the grants with
  every other fact, and `StandingsRead` carries `grants` and `unused`.
  `standings_verdict` counts "N passed by override" in its message. Its
  detail names each passed rule with the grant, and names each unused
  grant. `guidelines verdicts show` prints the same standings, so the
  overridden rule shows on its own line with the grant's clause, and an
  unused grant shows under its own heading. `--json` adds `override` and
  `overridden` to each owed rule, and `unused_grants`. A blocking rule's
  fixes name `invariant override <rule> --reason '<why>'` beside the
  verdict, and `pipeline ack`'s refusal for the gate names the guideline
  route beside the forced advance, as it does for `mechanical_clean`.
- *One spend path.* `Verdict.overridden` is new: each guideline a grant
  passed where it would otherwise have failed the gate, with what it would
  have failed on. Both family gates set it. `mechanical_clean` names the
  finding's places, and `verdicts_complete` names the standing without the
  grant's clause. `cli/main._spend_guideline_overrides` now reads it for any
  gate, instead of parsing `mechanical_clean`'s findings, so both gates are
  spent by one path, under A3.7's conditions: after an advance that
  succeeded, where the gate blocks or asks and passed. Otherwise the command
  says the grant is held.
- *Readings taken where the rulings are silent.*
  - A fail at warn passed by a grant is covered (DN-9). It is named in the
    detail and not reported as a warn. It is not in `Verdict.overridden`,
    since a fail at warn never blocked and the grant decided nothing, so no
    advance spends the grant for it. That is manifest.md's property that a
    grant is consumed only where the verdict actually stops the work. The
    detail says so.
  - One grant names one guideline, as in A3.7 and ruling 4. A grant on a
    rule the change does not owe now is accepted and named as unused, as a
    mechanical grant on a guideline with no new finding is.
  - A later fresh pass does not retire a grant. The grant is then named as
    unused, with `--clear` as the way to withdraw it, as in A3.7.
  - The profiles do not differ in what a grant may name. Under `strict` the
    gate blocks, so a grant that decided the landing is spent there. Under
    `loose` and `standard` the gate warns, so it is never spent.
- *Carry-over (A3.7, unchanged and now documented).* A held guideline grant
  stays in `pending_overrides` for the rest of the session. It passes its
  guideline at every later evaluation, in this pipeline and the next, until
  an advance spends it or `--clear` withdraws it. A judgement grant makes
  this visible: under `loose` and `standard` nothing spends it, so it holds
  for the session. It is loud throughout. `session status` names it with
  its family gate, the gate names it on every evaluation, and `override`'s
  output names the gate and its level. cli.md and state_model.md say so.
- *DN-12.* No check reads the acting agent. A test pins the gate's verdict
  equal with and without an `agent` on the records.
- *Tests.* `tests/test_guidelines_verdicts_override.py` covers each standing
  under a grant (missing, stale, a fail at blocker, a fail at warn, a pass,
  the engine's N/A, another rule untouched) and the unused grants. It also
  covers the gate over real repositories (passes, still failing on another
  rule, the fixes, each gate ignoring the other's grants, the agreement with
  `read_standings`, DN-12), the family gate, granting and its refusals,
  spending through the one path for both gates, the commands (`override`,
  `session status`, `verdicts show` text and `--json`, and that `check` and
  `show` never spend), and `pipeline ack`'s route. The `valid-ruleset`
  scenario is replayed with its verdict replaced by a grant: spent at the
  landing under `strict`, refused without it, and held under `standard`.
  `tests/test_guidelines_override.py` carries the lifted judgement refusal
  and the spend helper's `overridden`. `tests/test_guidelines_verdicts_show.py`
  carries the two new JSON keys.

**As built: A4 increment 6, the landing's guidelines records.** Built to
DN-7 ("Saved into pipeline evidence"), D1 items 1 and 2, D5 ("Mechanical
kinds use the same record"), ruling 4 and DN-12. DN-7 names the place and
nothing else, so the shape follows the session brief: what the family
decided at the landing, copied from that advance's own verdicts, with no
clock time and no migration.

- *Where (`domains/pipeline`).* `PipelineDomain.advance`, after the gates
  passed (or a forced advance went past them) and before the one save that
  moves the state, appends one entry to `evidence["guidelines_landings"]`
  when the target carries the `landing` role (`core/machines.LANDING_ROLE`,
  beside `ABANDON_ROLE`) and at least one `guidelines.*` gate was evaluated.
  `guidelines_landing` builds it from the `Evaluated` list the advance holds,
  so the record is what the gates decided at that advance, never a later
  re-derivation. A list, append-only, because a forced advance can land a
  pipeline twice.
- *What (one entry).* `from`, `to`, `forced` (as the history row has it),
  `gates` and `grants`.
  - Each `guidelines.*` gate: `rule`, `level`, `ok`, `skipped`, `blocks` and
    `message`, then, only where the gate computed them, `ruleset` (the digest
    it judged against), `findings` (`mechanical_clean`'s, in D5's record
    shape, warn and baselined ones included) and `standings`
    (`verdicts_complete`'s, one per owed rule, `Standing.record`). Every
    family gate on the landing is kept, `ruleset_valid`,
    `generated_current` and `baseline_only_shrinks` included, since each
    was part of why the landing was allowed. `detail` and `fixes` are not:
    they are advice derived from the same facts.
  - Each grant either family gate read or relied on: `guideline`, `gate`,
    `reason`, `outcome` (`spent` or `held`), `passed` (what the gate would
    have failed on, or null) and `why` it is held.
- *The gates hand their decision over (`core/rules`).* `Verdict` gains
  `standings`, `ruleset` and `grants`, set by the two family gates only.
  `mechanical_clean` sets its digest and the grants it read beside its
  `findings`; `standings_verdict` sets the standings, the digest and the
  grants whenever it judged standings (an empty list when nothing is owed),
  and none of them where it is N/A or cannot judge. No other rule sets any.
  `Standing.record` (`core/verdicts`) is the one plain-data form of a
  standing, which `guidelines verdicts show --json` now prints too, so the
  command's `owed` and the record cannot drift apart.
- *Spent or held is one decision (`core/rules.grant_outcomes`).* The CLI's
  spend asks it and the record copies it: spent where the gate blocks or
  asks, passed, and the grant passed a guideline that would have failed it;
  held otherwise, naming why (the gate is at warn, still failed on another
  guideline, or the grant passed nothing). The CLI consumes the grant after
  the save, so the record says what the advance decided, and
  `session.overrides` says what was consumed.
- *An acknowledged gate keeps what it computed.* `apply_acknowledgements`
  turned a warn verdict into a fresh N/A, dropping its findings. It now
  keeps the verdict's records (findings, standings, digest, grants,
  overridden) under the acknowledgement's text, so a landing past an
  acknowledged `mechanical_clean` still records the findings acknowledged.
  The text, detail and fixes a gate prints are unchanged.
- *Readings taken where DN-7 is silent.*
  - A forced landing records the same shape, `forced: true`, and the gate
    it went past shows `blocks: true`. Recording it differently, or not at
    all, would leave the least ordinary landings with the thinnest record.
    The forced advance's own row stays in `overrides`.
  - A refused advance records nothing. It is not a landing, and a refused
    transition writes nothing anywhere today.
  - No new command. `invariant pipeline status --json` prints the record
    with the rest of the evidence. A dedicated read can come with an audit
    surface if one is wanted.
  - With no ruleset configured or pinned no guidelines gate is injected, so
    nothing is recorded. A family gate set to `off` is recorded as the skip
    it ran as.
- *DN-12 and D1 item 2.* No rule reads the record, and the record copies no
  `agent`: standings carry none, and it is never read in logic.
- *Tests.* `tests/test_guidelines_landing_record.py`: `Standing.record` and
  the command's JSON over it, what each family gate carries (and that no
  other rule does), the acknowledgement keeping it, `grant_outcomes` for
  each outcome and ordering, the domain's write (the shape, a reload, a
  forced landing, a refused one, a non-landing target, no family gate, a
  disabled one, a second landing, no clock time), and the `valid-ruleset`
  scenario end to end: a spent grant under `strict`, a held one under
  `standard`, the cited pass answered, and the record equal to the
  advance's printed verdicts.

**As built: A4 increment 7, the vague-verdict and judgement-fail scenarios
(A1 increment 4).** Built to D5 ("What vague means", "What happens to one",
"Staleness", "The gate", item 5), D11 ("vague verdict", "judgement fail"),
ruling 7, DN-7, DN-11 and the user's A4.3 ruling on the engine's N/A. No
ruling was open. The D1 permutation test D11's increment table puts beside
these scenarios is left where that table and D1 item 2 put it, with A7.

- *A refusal names its predicate in data (`core/verdicts`, `core/rules`, the
  CLI).* A refused submission printed nothing on stdout under `--json`, so
  which predicate failed could be read only from the prose on stderr, and
  the harness never reads prose. `Refusal` is one line of a refusal with its
  `predicate`, `entry` and `rule` apart, and `PREDICATES` is every name one
  gives: P1 to P6, `shape`, and the whole's two, `pipeline` (nothing to
  check against) and `submission` (a file that cannot be read or is not
  JSON). `Checked.refusals` names each entry's problems, `VerdictPlan.whole`
  holds `Refusal`s, and `VerdictRefused` carries `refusals` beside the
  `problems` lines it always had, with `precondition` and `unreadable` for
  the refusals of the whole the domains and the CLI raise. The text, the
  lines and the exit are unchanged. Under `--json` both commands now print
  `{recorded: [], refused: [{entry, rule, predicate, problem}]}` before the
  exit 1, as `pipeline advance` prints the gates it was refused on.
- *The harness (`core/conformance`, `cli/conformance`).* Three additions,
  each host-neutral:
  - A `verdicts` step: its `entries` (any list; a malformed entry is the
    engine's to refuse) are written as JSON beside the replay's repository,
    never into it, and handed to `guidelines verdicts submit`. The file is
    therefore never part of the change it answers for, so BUG_105 does not
    touch it.
  - A refusal's names are the blocking rules of a refused advance and, for a
    refused submission, the predicates its document names. A known answer's
    refusal may name either; a name that is neither a registered rule nor a
    predicate is refused at load.
  - `expect.verdicts`, D11's "the verdict status each judgement rule must
    receive": per step that runs `guidelines verdicts show`, each owed
    guideline's answer, which is a current verdict's status or the standing
    (`missing`, `stale`, `engine`). A step it names that does not show the
    verdicts, or an answer outside that set, is refused at load. Like the
    gates, a step it does not name is not compared, and two replays are held
    to each other on every step. `CONF --> CVERD` is the new arrow, for the
    vocabulary alone.
- *Five scenarios.* Each seeds `app.py` on the base branch through a write and
  a commit (the package data still carries no Python), and owes the blocker
  judgement guideline `app.answer-is-stable` (`evidence: either`,
  `scope: diff`, a mechanical `no-executable-change` and a claimed
  `constant-only`). Every verdict is submitted in review after the change is
  committed, where valid-ruleset submits its own.
  - `vague-verdict` (strict): ten vague answers, each refused exit 1 naming
    the predicate: a pass with nothing behind it (P2); a citation of an
    untouched file, past the end of the file, and of lines the change did
    not add (P3, the last D11's own seed); a test no run named (P4); an `na`
    on the mechanical condition the engine finds false, and one naming a
    condition the rule does not declare (P5); a note over a cap of 40, and a
    note the privacy scan flags through a scenario-only `privacy.extra`
    pattern (P6); and a bulk file of one valid entry and one naming no
    guideline, refused whole (DN-11, P1). Nothing is recorded, so the rule
    stands `missing`, and `verdicts_complete` refuses the landing.
  - `judgement-fail` (strict): a cited `fail` is accepted and current, and
    blocks the landing. The advance is then forced. The landing record
    (A4.6) keeps the standing as the gate decided it, `forced: true`.
  - `judgement-fail-standard`: the same fail warns, the landing goes through
    unforced, and its record still keeps the fail (ruling 7: `warn` in
    `standard`).
  - `stale-verdict` (strict): a cited pass stands current, then the cited
    line is edited and committed, so the verdict is stale on P3, and stale is
    missing at the landing.
  - `engine-answers` (strict): a comment-only change, so
    `no-executable-change` holds. A cited `fail` is accepted (A4.2), the
    engine answers the rule, the fail is set aside, and the gate passes.
    `coverage.meets_floor` skips, since the change alters no code.
- *Readings taken where the rulings are silent.*
  - D11's table says a vague verdict makes `verdicts_complete` block. It
    blocks under `strict` only (ruling 7), so the scenario names that
    profile rather than raising the one gate.
  - A grant spent under `strict` and held under `standard` gets no scenario
    of its own. A4.5's and A4.6's tests already replay `valid-ruleset` with
    its verdict replaced by a grant under both profiles, through the same
    replay. A shipped scenario would add a second copy of that proof and no
    new answer.
  - A forced advance in a fixture is the engine's own forced landing, which
    DN-7's reading records; nothing outside the replay's repository is
    forced.
- *Tests.* `tests/test_conformance_engine.py`: each scenario's known answer
  and its two-replay agreement; per scenario, the refusal documents (every
  refused step exits 1 and records nothing, the bulk refusal names its
  second entry), the standings, the landing record kept under both profiles
  and its `forced`, the stale verdict's P3, and the set-aside fail; the
  `verdicts` step against a recording CLI (written outside the tree,
  placeholders filled, a malformed entry passed through); the loader's new
  refusals; the reduction of refused documents and shown standings; the
  comparison and the exhaustive comparison of `verdicts`.
  `tests/test_guidelines_verdicts.py` covers `Refusal`, the refusal's names
  in order, the whole's predicates and `PREDICATES`, and
  `tests/test_guidelines_domain.py` the `--json` refusal document through
  `main` for each kind of refusal, and that the text form prints none.

**As built: A4 increment 8, the host stamps the acting agent.** Built to
DN-12, D1 items 1 and 2, D5 ("The gate") and the user's design ruling for the
increment (Option A below). It closes A4: all eight increments on the
session's plan have landed.

- *The problem.* A verdict is recorded in the CLI child of a shell call, and
  a shell call carries no hook payload, so the CLI cannot see who submitted
  it. Only the hooks know `agent_type`, which Claude Code stamps on every
  `PreToolUse` and `PostToolUse` payload, a subagent's own shell calls
  included. The environment cannot carry it: a subagent shares its parent's
  environment, and a command prefix could forge it.
- *The design, chosen by the user (Option A).* Three others were rejected: a
  pending actor left by `PreToolUse` (B), rewriting the command's
  environment (C), and reading transcripts (D).
  - Each accepted submission puts one random token on every record it
    writes and prints it.
  - The `PostToolUse` hook reads the token back out of the command's output
    for a verdict command whose payload names the agent. It places the
    session as an actor row is placed and asks the pipeline domain to stamp
    the agent.
  - The stamp is set once, only where the token matches and no agent is
    recorded yet.
  - It is host-neutral: core and the domain take an opaque agent string, and
    a host without `agent_type` stamps nothing.
- *The user's rulings for this increment.*

  | # | Question | Ruling |
  |---|---|---|
  | a | May a host set a field on the append-only verdict record? | Yes: a set-once host stamp of `agent` is allowed on an otherwise append-only record. It never overwrites. This amends increment 1's "no record is reshaped" and state_model.md's matching sentence. |
  | b | Who owns the token? | The engine. The per-submission token is an engine-owned key, and a submitter supplying the token or the agent is still refused under the unknown-key rule. |
  | c | What links a shell call to the records it wrote? | The command's output, as the session binding already uses it (`_bind_claimed_session`). |

- *The token (`domains/guidelines`, `core/verdicts`).*
  `GuidelinesDomain.submit_verdicts` asks `new_submission_token`, which is
  `secrets.token_hex(16)` (32 lowercase hex digits), once per accepted
  submission. It sets the token on every `VerdictRecord` the plan accepted,
  so one bulk submission has one token. `VerdictsRecorded.token` returns it.
  `core/verdicts` stays pure. `VerdictRecord.token` is a field written only
  when set, and `token` joins `state`, `surface` and `agent` as a key the
  engine writes and `resubmitted` drops. The gate's re-check therefore never
  sees it, and a submitted `token` is an unknown key (ruling b). A refused
  submission has no token and prints none.
- *The output (the CLI).* The text line is now `recorded N verdicts
  (ruleset <digest>, state <state>, token <hex>)`, and `--json` adds a
  top-level `token`, which each record in `recorded` also carries. This
  spelling is a contract with the host's reader, and a test feeds the real
  command's output, text and JSON, to the reader.
- *The stamp (`domains/pipeline.stamp_verdict_agent(session, token,
  agent)`).* Every record in the current pipeline's `evidence["verdicts"]`
  carrying exactly that token, with no `agent` or a null one, gets the agent.
  Nothing else on any record changes. A record that already has an agent
  keeps it (ruling a). It returns the count and saves only when it stamped.
  An empty or non-string token or agent, no pipeline, no verdicts, or no
  matching record stamps nothing, writes nothing and raises nothing: the
  verdict was accepted already.
- *The hook (`hosts/claude_code`).* `_post_tool_use` calls
  `_stamp_verdict_agent` for a `Bash` call, after `_record_acting_agent`. It
  acts only when:
  - the payload's `agent_type` is a non-empty string;
  - the command matches `_VERDICT_SUBMISSION`, which is `invariant
    guidelines verdict` or `invariant guidelines verdicts submit` reached
    through any `cd ... &&`, and never `verdicts show`;
  - the output holds a token (`_submission_tokens`, every distinct token in
    order, so two submissions chained in one call are both stamped).

  The session is `_actor_session` over `resolution_for_payload`, the place
  check an actor row clears. A guessed route stamps nothing. On the host
  route, an isolated session is stamped only when the call lands in its
  worktree. An unplaced cross-project call returns before this, as for every
  other record. An `InvariantError` is swallowed. `_response_text` is now
  the one reading of a shell call's output, shared with
  `_bind_claimed_session`. Nothing is added to `NORMALISED_PAYLOAD_KEYS`:
  `agent_type` is already there, and `tool_response` is read only inside
  `hosts/`, as the session claim reads it.
- *DN-12 and D1 item 2.* No gate, check, standing, staleness test or landing
  decision reads `agent` or `token`. A test pins three things equal across
  records with neither, an agent, a token, and both: the gate's verdict,
  `read_standings`, and `guidelines_landing` built from that verdict. It
  does so for a current pass, a current fail and a missing verdict. The
  landing record copies neither, and `guidelines verdicts show` shows
  neither. `invariant pipeline status --json` prints them with the rest of
  the evidence, which is where an audit reads them. No model identity or
  capability tier is recorded (A7).
- *Readings taken where the rulings are silent.*
  - *A main-loop submission stays unstamped.* A main-loop turn names no
    agent. A later call cannot stamp it either, because it would need the
    token, which only that command printed.
  - *The output link is trusted as far as ruling (c) trusts it.* A call
    that prints another submission's token from a verdict command could
    stamp that submission, if that submission is still unstamped. The token
    is unguessable and printed only to the submitting call, so this needs
    the token in hand. The stamp is audit only, so it can mislead a reader
    but can change no gate.
  - *Only the current pipeline is stamped.* The `PostToolUse` event brackets
    the command that recorded into it.
- *Tests.* `tests/test_guidelines_verdict_agent.py` covers:
  - the record (the token written only when set, both keys dropped when read
    back, both refused from a submitter);
  - the token through `main` (one per submission, distinct across two, in
    the text and `--json`, absent on a refusal, and parsed back by the host
    from the real output);
  - `stamp_verdict_agent` (matching records only, set once, a null agent,
    nothing else changed, an unknown token, empty inputs, no pipeline, no
    verdicts, a non-object record);
  - the token reader;
  - the hook through `_post_tool_use` and `_stamp_verdict_agent` with
    fixture payloads only: text, JSON and both response spellings, two
    tokens, no or a malformed agent, other commands, no token, a guessed
    session, a domain refusal, the actor row beside it, an unplaced call,
    and the worktree place check;
  - the DN-12 pin.

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
- **Just in time, on an edit** (`PostToolUse` on an edit tool whose target
  matches `applies_to`, once the edit has landed): the statements of the
  matching rules for the current phase, as context, capped. This is the point
  where the rule can still change the code at no cost. The record first said
  `PreToolUse` here. Ruling 9 and DN-8 place everything said at an edit after
  it lands, so it is never a deny and a host that cannot deny can still carry
  it; A3 increment 8 built the mechanical half there, and A5 adds the
  statements on the same event.
- **The design agent** asks `guidelines show --phase design` for the
  build-time subset it applies while designing. **The review agent** asks
  `--phase review` for every applicable rule and answers each judgement rule
  with a verdict.

The engine computes the set (`core/guidelines.select(role, paths)`), and the
host adapter only transports text. That keeps selection host-neutral and makes
the injected text a projection of state, reproducible with one command, as
ADR-001 asks of everything injected.

Mechanical checks are evaluated at the landing gate. On an edit they report
advisory context at `PostToolUse` ("this edit adds a new finding of X"). In
v1 no finding denies an edit, at any severity (ruling 9): the edit is advised
and the landing gate holds. A `cochange` finding could not be judged at the
edit anyway, since its second file may come later, so the advisory never
covers one (A3 increment 8, below).

**A5 rulings (the user's).** The user ruled on twelve questions before A5 was
built. They bind every A5 increment.

| # | Question | Ruling |
|---|---|---|
| D1 | What a state with no role sees | A state with no role (tdd's red, green and refactor, chore's doing, and other machines' working states) is a build state and sees design-phase plus always rules. This is decided from the machine alone, with no history. |
| D2 | What the review phase sees | Review, landing and always rules. |
| D3 | The turn index's file scope | Declared plus changed files. When both are empty, it is the whole role index, capped. |
| D4 | What is injected at an edit | Only judgement rules' statements. |
| D5 | Whether the edit layer remembers what it said | No: stateless. Every matching edit carries them. |
| D6 | The cap at an edit | One shared cap, findings first, one overflow line. |
| D7 | `guidelines.injection.on_edit` | Accepted, default true. |
| D8 | Where the renderers live | Pure renderers live in `core/guidelines`; the host only wraps them. `render_advisory` moves down beside them in A5.3. |
| D9 | Subagents | They wait for A6, getting only the edit layer until then. |
| D10 | Verdict standings in the turn index | None. |
| D11 | A `CLAUDE.md` block in this repository | None yet; revisit when an always rule is added. |
| D12 | Always rules at the turn | The turn index always lists always rules by id and title, and `doctor` and `generated_current` say when always rules exist but no instruction file carries the block. |

A5 is built in four increments, recorded on its session's plan: selection
and `guidelines show --phase` (A5.1), the turn layer (A5.2), the edit layer
and `on_edit` (A5.3), and the always-layer warning and closure (A5.4).

**As built: A5 increment 1, selection and `guidelines show --phase`.** Built
to D7, ruling 8 and the A5 rulings D1 to D3.

- *`core/guidelines.select(ruleset, roles, paths)`.* D7 sketched
  `select(role, paths)`; the ruleset is a parameter so the function stays
  pure, and it takes a state's roles, plural, since a state may carry
  several. `ROLE_PHASES` maps `design` to design and always rules, `review`
  to review, landing and always (D2), and `landing` and `abandon` to none.
  `phases_for(roles)` is the union, in `PHASES` order. A state carrying none
  of those four roles is a build state and sees `BUILD_PHASES`, design and
  always (D1). That reads the machine alone, never history. A rule is
  selected when its phases meet those, and, when `paths` is not empty, when
  at least one path meets it. Empty paths are no file filter. The result is
  in declared order.
- *One reading of "this file brings the rule into play".* `matches`,
  `meets` and the path exception moved down from `core/findings` into
  `core/guidelines`, because `core/findings` imports `core/guidelines` and
  selection could not ask it from there. `core/findings` and `core/verdicts`
  now import them, so the findings, the verdicts owed and the selection
  read one predicate. A path exception with no fingerprint sets a file
  aside; one naming a fingerprint excuses one finding and sets no file
  aside. Nothing else changed in either module's behaviour.
- *`render_selected`.* It is a pure renderer beside the others (D8). It
  writes one header line naming the ruleset's path and digest, the phases,
  how many rules were selected out of how many, and the file scope. Then
  each rule follows through the checklist's own per-rule writer, so a rule
  reads the same in both texts. An empty selection says so.
- *`invariant guidelines show --phase R [--files ...] [--touched]`.* The
  domain's `select` resolves the file scope and asks `core/guidelines` the
  rest. A named file is read against the working directory and spelled
  project-relative. `--touched` adds `core/rules`' `touched_paths`: the
  session's declared plus changed files through `_changed_set` (D3), the
  same union the gates read. The session is resolved from the working
  directory. With both, the scope is the union. `--json` carries the phase,
  its phases, the selected ids, the ruleset digest, the files and the text.
  Plain `guidelines show` is unchanged.
- *Refusals.* Each is `SelectionRefused`, exit 1, and writes nothing:
  - an unknown phase, naming the four;
  - `--touched` with no session;
  - and, taken in the strict direction, `--files` or `--touched` without
    `--phase`, and a named file that names nothing inside the project. A
    misspelt path dropped silently would widen the selection to every rule
    of the phase with nothing to say so.
- *Readings taken where the rulings are silent.*
  - A role the mapping does not know contributes no phases. So a state with
    only such roles is a build state, the direction that shows more rules.
  - `--phase` names a role, as D7 says. There is no `build` value: a build
    state sees what `design` does, so `--phase design` is what it is shown.
  - `touched_paths` keeps a path that is not, or no longer, a file in the
    tree. A declared file the session is about to create is the one whose
    rules it most needs before it exists. The verdicts' DN-3 reading, where
    a deleted file owes nothing, is unchanged: it asks `meets` over present
    files only.
  - A changed set nothing could establish gives an empty scope. That is no
    file filter, which hides no rule, so it is not refused.
- *What is not here.* The turn layer (A5.2), the edit layer and
  `guidelines.injection.on_edit` (A5.3, still refused by name at load), and
  the always-layer warning in `doctor` and `generated_current` (A5.4).
- *Tests.* `tests/test_guidelines_select.py`: `phases_for` for every role
  combination and every shipped tdd and chore state, `select` (order, the
  union, the file filter, a path exception, no filter), the moved predicate
  read by `core/findings`, `render_selected`, the domain's selection and each
  refusal, `--touched` over a real repository, and the command through
  `main`.

**As built: A5 increment 2, the turn layer.** Built to D7, ruling 8 and the
A5 rulings D3, D10 and D12.

- *`core/rules.turn_guidelines(manifest, context)`.* It returns plain data,
  a `core/guidelines.TurnIndex`, or `None` for silence. It reads
  `guidelines.ruleset` first; with none it never calls `context` and reads
  no file and no git, the shape of `edit_advisory`. Otherwise it reads the
  pipeline's current state roles from the machine the pipeline was started
  under, `phases_for` them, and returns `None` when that is empty (a landing
  or abandon state). The file scope is `touched_paths`'s union (D3), which
  now shares `_touched` with it so the changed set is read once and its
  `answerable` flag is kept. The ruleset is read from the tree in hand and
  `core/guidelines.select` chooses over the scope. The pinned digest is
  carried as `pin` only when it is set and differs from the tree's (DN-6's
  reading: a null pin, or none, compares against the tree's).
- *`core/guidelines.render_turn_index(index, max_lines)`.* The pure renderer
  (D8). A header naming the phases, the ruleset's path and digest, how many
  rules are listed out of how many, and the file scope; then the pin line,
  when there is one; then one line per rule, `- id: title`, an always rule
  marked `(always)` as the managed block marks it. The existing index format
  carries neither severity nor kind, so neither is added. Past the cap the
  last line counts the rules left out and names the reproducing command,
  `invariant guidelines show --phase R --touched`, R being the state's first
  role that selects anything, or `design` for a build state. With no
  pipeline it names `invariant guidelines show`. No statement and no
  verdict standing (D10).
- *`build_context` appends it,* indented as the projection's other lines,
  so `UserPromptSubmit`, the `SessionStart` greeting and `invariant context`
  carry the same text; a test pins the hook's text equal to the command's.
  The rule context is built from the session and pipeline the projection
  already holds, rather than resolved again through `Context.rule_context`,
  so the index reads exactly the session the projection shows. The cap is
  `guidelines.injection.max_lines`.
- *Readings taken.*
  - D12 is read as "listed unconditionally": the always rules are on every
    turn index, whatever the phase and whatever the file scope, ahead of the
    state's own rules. They are the standing instructions the managed block
    also carries, so a file filter does not drop them. The state's other
    rules are filtered as `select` filters them. So the overflow command
    reproduces what the cap cut, which is never an always rule unless the
    always rules alone pass the cap.
  - No pipeline gives the always rules alone; a session with none bound gets
    no turn index, since its projection is about starting one.
  - A ruleset that does not load is one line, its failure plus
    `see invariant check guidelines.ruleset_valid`. A moved pin is one line
    above the rules, which are still listed from the tree. An exception
    inside the projection, a pinned machine that does not load included, is
    one line; an exception in the host's own step is silence, and the rest
    of the projection still stands.
  - Nothing selected and no pin is silence: a header counting zero rules on
    every turn says nothing the managed block does not.
  - The A5.1 reviewer's note fits the header: when the scope is empty
    because the changed set could not be established, it ends "with no file
    filter, since the changed files could not be established".
- *What is not here.* The edit layer and `guidelines.injection.on_edit`
  (A5.3), and the always-layer warning in `doctor` and `generated_current`
  (A5.4). No subagent change (D9).
- *Tests.* `tests/test_guidelines_turn.py`: every state of the shipped tdd
  machine, no pipeline, the file scope, the unestablished scope, each
  problem line, the pin, the renderer's cap and silence, and, through the
  host against a real repository, the prompt hook, the greeting, the
  manifest's cap, silence on a failure in the host's step, and the hook's
  text equal to `invariant context`'s.

**As built: A5 increment 3, the edit layer and `guidelines.injection.on_edit`.**
Built to D7, rulings 8 and 9, D13 and the A5 rulings D4 to D9.

- *`core/rules.edit_guidelines(manifest, target, context)`.* It returns
  plain data, a `core/guidelines.EditGuidelines`, or `None` for silence. It
  reads `guidelines.ruleset` and then `guidelines.injection.on_edit` first;
  with no ruleset, or with `on_edit` false, it never calls `context`, the
  shape of `edit_advisory`. Otherwise the edited file is placed in the tree
  the gates read, as A3.8 places it, the current state's roles are read from
  the machine the pipeline was started under and `phases_for` them (D1, D2),
  and `core/guidelines.select` chooses over the one file, so `applies_to`
  and the path exception are read through `meets` as everywhere else. Of
  what it selects, only `judgement` rules are kept (D4). Nothing is
  remembered between edits (D5). The overflow command is
  `invariant guidelines show --phase R --files <path>`, R read as the turn
  index reads it (`_show_phase`, now shared with `turn_guidelines`, as is
  `_state_roles`), the path shell-quoted.
- *Silence.* No ruleset, `on_edit` false, an edit outside the session's
  tree, a path that is no file, a landing or abandon state, and a file no
  judgement rule of the phase covers.
- *One line.* A ruleset that does not load gives the line the findings half
  gives, word for word (`_unloaded_at_edit`, shared by both), so the
  renderer says it once. An exception inside is one line naming its type
  and `invariant guidelines show`.
- *The renderers (D6, D8).* `AdvisedFinding`, `EditAdvisory` and
  `render_advisory` moved down from `core/rules` and `hosts/claude_code`
  into `core/guidelines`, beside `TurnIndex`. `render_edit(advisory,
  guidance, max_lines)` writes the findings exactly as A3.8 did, then a
  header naming the file and the phases and one line per carried rule, its
  id, severity and statement with its whitespace collapsed onto one line.
  One cap is shared. Past it the last line counts the items left out and
  names `invariant check guidelines.mechanical_clean`, the show command, or
  both joined by `; `, by which halves were cut. `render_advisory` is
  `render_edit` with no statements, so the findings-only output is
  unchanged, and its tests pass with only their imports moved.
- *The host only wraps.* `_edit_advisory` asks both projections through one
  rule-context builder built at most once, has `render_edit` write the
  lines, and returns them as `additionalContext`. Never a
  `permissionDecision` (ruling 9). An exception in the host's step is
  silence. The A3.8 findings are unchanged: the ratchet applies and no
  cochange is evaluated.
- *`guidelines.injection.on_edit`.* Accepted at load, default true (D7). A
  value that is not a boolean is refused by name: a string `"no"` would read
  as true and null as false. It turns off the statements alone; the findings
  are advised either way. This repository's manifest does not name it
  (BUG_050; the default is what it would say).
- *Readings taken where the rulings are silent.*
  - No pipeline carries the always judgement rules alone, as the turn index
    lists the always rules alone; its show command names `--phase design`,
    the nearest phase that prints them, since `--files` needs a phase.
  - A header the cap leaves with nothing under it is dropped, unless it is
    the first line, so a cap of 2 still names the file. The response may
    then be one line short of the cap.
  - A statement is collapsed, not cut: the rule's statement is what this
    layer exists to carry.
- *Subagents (D9).* A subagent's edits reach the same `PostToolUse`, so the
  edit layer is what they get until A6. No agent text changed.
- *What is not here.* The always-layer warning in `doctor` and
  `generated_current` (A5.4).
- *Tests.* `tests/test_guidelines_edit_layer.py`: every tdd state, the
  path exception, D4's engine rule left out, no pipeline, statelessness,
  each silence including the no-ruleset and `on_edit` cost, each line, the
  renderer's shared cap, its overflow commands and dropped header, the
  findings-only output equal to `render_advisory`'s, the host wrapping one
  response with the context built once, and against a real repository
  through `handle`. `tests/test_guidelines.py`: the key's default, a kept
  value beside the default cap, and each refusal.

**As built: A5 increment 4, the always-layer warning, and A5 closed.** Built
to D3, D7, ruling 8 and the A5 rulings D11 and D12.

- *Status.* A5 is closed. Its four increments are built: selection and
  `guidelines show --phase` (A5.1), the turn layer (A5.2), the edit layer and
  `on_edit` (A5.3), and this one. All three of ruling 8's layers stand: the
  `guidelines/index` block (A2.4), the turn index (A5.2) and what an edit
  carries (A3.8, A5.3).
- *One reading, from the render plan.* `RenderPlan` now carries `always`,
  the ids of the ruleset's `always` rules. `core/rules._always_layer_gap`
  reads the plan: always rules, and no planned block whose status is other
  than `absent`, is the gap. A block that is present counts whatever its
  state, since a stale or edited block is the drift gate's own finding. It
  answers a message (the count, the files looked in, and that the turn index
  still lists them on every turn) and a fix,
  `invariant guidelines render --insert guidelines/index`.
- *Host-neutral.* The files are the plan's: `guidelines.instruction_files`,
  or with that key null the host adapter's default, which the CLI already
  hands every rule context as `RuleContext.host_instruction_files` (A2.5).
  Nothing in `core/` names `CLAUDE.md`. With no file to look in (the key null
  and a host kind with no adapter) the message says no instruction file is
  configured, and the fix names `guidelines.instruction_files` before the
  render command.
- *`guidelines.generated_current` notes it (D12).* The note joins the detail,
  beside the absent blocks a passing verdict already named, and never changes
  the verdict: a passing rule stays passing. A failing verdict carries it
  after its own detail, and its fixes are unchanged.
- *`doctor` warns (D12).* `core.rules.always_layer_summary` is the row's
  text, or `None` for no row: no ruleset, no always rules, a block present,
  or a refused plan (the `guidelines generated` row already warns on a
  refusal, and the `guidelines ruleset` row fails a ruleset that does not
  load). The row, `guidelines always layer`, warns and never sets the exit
  code, as the drift row does. `--json` marks it `"ok": true, "warn": true`.
- *D11.* This repository gets no `CLAUDE.md`. Its ruleset has no always
  rules, so neither the row nor the note appears here.
- *Tests.* `tests/test_guidelines_always_layer.py`: the note with one and
  with several always rules, beside the absent-block detail, none with the
  block inserted, with a hand-edited block, with no always rules, or with a
  block in one of several files; the configured files named over the host's;
  no instruction file at all; a failing verdict that keeps its fixes;
  `always_layer_summary` for no ruleset, a refused plan, a gap and a present
  block; and the `doctor` row through `main`, its `--json` warning, the exit
  code unchanged, and no row with the block, without always rules or
  without a ruleset.

### D8 — Host-neutral agent definitions, rendered per host, with project overlays (A6)

**The source is host-neutral.** A library agent is a markdown body with
neutral frontmatter: `name`, `description`, `tier` (D9), and `capabilities`
(read, edit, shell, dispatch) rather than a host's tool names. The record
first listed `roles` (design, review, intake, repro) among them; the A6
ruling 7 drops it, because the manifest's `agents.roles` already owns which
agent answers for which role. The body speaks only to the invariant CLI and to the
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
on a heading's wording. The installed file's managed marker records three
digests (amended by the A6 ruling 1; the record first said two): the shipped
source's, the overlay's, and the rendered body's own, its witness. The
witness is what tells an edit from an older release; the other two say what
to re-render. Together they separate three cases the single digest cannot:

- the library changed: re-render;
- the overlay changed: re-render;
- the installed file was edited by hand: drift. `doctor` names it, and the fix
  is to move the edit into the overlay.

BUG_051's rule carries over: a hand-edited installed file is skipped and
named, never flattened. Project-local agents (`agents.local`) are untouched,
so a publisher bound to one app store stays the project's.

**A6 rulings (the user's).** The user accepted all fourteen A6 design
recommendations before A6 was built. They bind every A6 increment.

| # | Question | Ruling |
|---|---|---|
| 1 | What the managed marker records | Three digests: the source's, the overlay's, and the body's own witness. This amends D8, which said two. |
| 2 | BUG_106, an edit to an older release's copy rewritten | Filed, reproduced, and fixed combined in A6.1, since A6.1 rewrites the classifier. |
| 3 | Where rendered agents sit among the write classes | They stay host assets, exempt from session isolation as `.claude/*` is today. They are not generated files. |
| 4 | An overlay's format | Versioned YAML operations. |
| 5 | What an overlay may change | Body sections, plus `description_append`. |
| 6 | Where overlays live | By convention at `docs/agents/<name>.yaml`. |
| 7 | `roles` in the neutral frontmatter | Dropped. The manifest's `agents.roles` owns it. This amends D8. |
| 8 | `compliance-reviewer`'s `model: sonnet` | Dropped in A6.2. |
| 9 | How the bodies reach the ruleset | Literal `guidelines show --phase` commands, plus a fallback for a project with no ruleset, and a test that every command a body carries parses. |
| 10 | `agents.rendered_current` | Warns in every profile, plus a `doctor` row. |
| 11 | `agents.library` and `agents.local` | Become real keys the renderer reads. |
| 12 | Host adapters | Claude Code is the only one, plus a lint for host vocabulary in the neutral source. |
| 13 | A migration helper for the downstream project | None. It is filed as an idea. |
| 14 | A gate on the design agent reading the guidelines | None. |

A6 is built in five increments, recorded on its session's plan: one asset
classifier and one install plan (A6.1), the neutral format and the Claude
Code renderer (A6.2), overlays and the `agents.*` keys (A6.3), the
`agents.rendered_current` drift rule (A6.4), and the bodies onto the engine
with A6's closure (A6.5).

**As built: A6 increment 1, one asset classifier and one install plan.**
Built to D8 ("BUG_051's rule carries over"), the A6 rulings 1, 2, 3 and 12,
and BUG_106. No format changed: the marker still records one digest, the
source's, which is the rendered text's own digest while there is no
overlay, so the witness of ruling 1 is already on disk in every copy any
release has written.

- *The witness rule (BUG_106, fixed).* `hosts/assets.classify` is the one
  classifier, for every asset kind. An absent file is `absent`; no
  frontmatter on line 1 is `unreadable`; a body that reconstructs to the
  shipped text is `current`. Otherwise a body that no longer hashes to its
  own marker's digest, or has no marker, is `edited locally`, skipped and
  named whatever release installed it. A body that matches its own digest
  but not the shipped text is `installed from an older version`, and is
  rewritten. The old rule asked whether the marker held today's digest, so a
  copy an earlier release installed and the operator edited went to "older"
  and was rewritten unnamed. BUG_051's same-release case still lands on
  `edited locally`. A marker-less file under a shipped name was "older" and
  rewritten; it is now the project's and kept. This is the managed block's
  rule (A2.4's `block_state`), so the two now agree.
- *One plan.* `ClaudeCodeHost.plan_assets` is read by `doctor`'s currency
  rows (`_stale_for`), `assets install`, `agents install`, `init` (through
  `install`) and `uninstall --remove-assets`. `install_agent`, the second
  writer, is gone, and `agents install` is the plan and `install_assets`
  narrowed to agents. `agent_checks` is joined into
  `asset_checks(only, repair)`, whose agent rows `doctor` and `agents
  install` both print, each naming its own repair command. A failing
  loadable row whose every shipped asset is absent now names its repair,
  as `agent_checks` did.
- *One set of helpers.* The frontmatter split and parse, the marker
  constants and reader, `reconstruct_source`, the digest and loadability
  live only in `hosts/assets`. Their copies in `hosts/claude_code`
  (`_split_frontmatter`, `agent_frontmatter`, `_marker_digest`,
  `_reconstruct_source`, a two-argument `loadability_problem`, `MARKER_*`,
  `FENCE`) are deleted with no re-export. `agents list` asks the agent kind's
  `loadability_problem`, so a mismatched name reads "is installed as X", as
  `assets list` already said.
- *The host is the manifest's (ruling 12).* `assets list`, `assets
  install`, `agents list` and `agents install` resolve the adapter through
  `host.kind` (`_asset_host` in the CLI). A kind with no adapter is refused
  by name with exit 2, before anything is read or written.
- *The write class (ruling 3).* cli.md's Conventions name host assets as
  their own write class, beside the state root, the declared stores and the
  generated files, exempt from isolation as before.
- *Tests.* `tests/test_authority.py`: the BUG_106 repro, its strict xfail
  removed and its assertions unchanged, for both install commands; its
  control; `doctor`, `--dry-run`, `--overwrite-local`, `uninstall
  --remove-assets` and `init --force` on an edited older-release copy; and
  BUG_051's stale-copy test, which replaced a marker spelling this library
  never wrote and accepted either `doctor` exit, rebuilt to watch `doctor`
  name the copy stale, the install rewrite it unnamed, and the row turn
  current. `tests/test_assets.py`: each state of `classify` as a known case,
  the witness's round trip, the plan across all three kinds with a library
  that ships each (skip, rewrite, the currency row, uninstall), the narrowed
  rows naming their repair, the `host.kind` refusal for all four commands,
  and `agents list` read off the plan. `tests/test_host_claude_code.py`
  reads the canonical helpers, and refuses a frontmatter-less shipped
  source through `install_assets`.

**As built: A6 increment 2, the neutral agent format and the Claude Code
renderer.** Built to D8 ("The source is host-neutral", "The host adapter
renders it") and the A6 rulings 7, 8 and 12. The marker still records one
digest, now of the rendered text; the three-digest marker is A6.3's, with
overlays.

- *D8, amended by ruling 7.* The neutral frontmatter is `name`,
  `description` and `capabilities`. D8 first listed `roles` among them, and
  ruling 7 drops it: the manifest's `agents.roles` owns which agent answers
  for which role, and a second statement of it in the source would be a
  second answer that could disagree. D8 also lists `tier` (D9). It is
  refused by name until A7 builds it, so a source cannot carry a hint
  nothing reads.
- *`core/agents` (new, pure).* `parse` reads a neutral source into an
  `AgentSource`: the frontmatter, a `preamble`, and `sections`, each running
  from a whole-line anchor `<!-- invariant:section <id> -->` to the next.
  `capabilities` is a bracketed list drawn from read, edit, shell and
  dispatch. A source is refused whole (`AgentSourceError`), naming every
  problem as the ruleset loader does: no frontmatter on line 1, a missing
  key, an unknown or repeated key, a line that is not `key: value`, a
  capability list that is not bracketed, empty, unknown or repeated, a name
  that is not the file's, and a malformed or repeated anchor. `tier`,
  `model`, `tools` and `roles` are refused by name with the reason. It
  imports nothing from `hosts/` and does no I/O: the caller hands it the
  text. It keeps its own frontmatter reader rather than `hosts/assets`',
  which is the host's reading rule and may not be imported from `core/`.
- *The four sources.* Each declares `capabilities: [read, edit, shell]`,
  which is what its `tools:` line gave, and carries anchors at the body's
  start (`intro`) and above each top-level section, plus `phase-1b` in
  `feature-architect`. `compliance-reviewer` loses `model: sonnet` (ruling
  8). The two `AskUserQuestion` mentions in `backlog-curator` are reworded
  host-neutrally with their meaning kept: the parent relays the interview
  questions verbatim, as a multiple-choice question where its host offers
  one. The `MODEL:` hint prose in the architect's and the reproducer's
  descriptions is carried unchanged for A7. No other body text changed.
- *The renderer (`hosts/claude_code.render_agent`).* Capabilities become a
  `tools:` line (read gives Read, Grep, Glob; edit gives Edit, Write; shell
  gives Bash; dispatch gives the host's agent tool, Agent), listed in one
  fixed order, which is the order every shipped file carried, so the render
  is the same bytes. The anchors are dropped and no model line is written.
  `plan_assets` renders every agent through it, so each `AssetPlan` now
  carries the host's `text`, and the classifier judges an installed copy
  against the render rather than the source. A source that does not parse
  is refused naming its problems before anything is written.
- *`invariant agents show <name> [--sections]`.* Prints the rendered text,
  without the marker an install adds, and writes nothing. `--sections` lists
  the anchor ids an overlay can target. An unknown name is refused naming the
  shipped agents, and the host is the manifest's `host.kind`, as for the
  other agent commands.
- *An install from before A6.2.* A copy A6.1 wrote still hashes to its own
  marker, so it reads as stale, never as edited: `compliance-reviewer` and
  `backlog-curator` as `installed from an older version`, and rewritten;
  `feature-architect` and `bug-reproducer` as current, since their render is
  A6.1's text. A copy edited since reads as `edited locally` and is kept.
  This repository's own installed copies are reinstalled after the merge.
- *Tests.* `tests/test_agents.py` covers the model and every refusal, and
  scans the module to show it imports no host and nothing that does I/O.
  `tests/test_agent_rendering.py` holds the golden: each shipped source,
  rendered with no overlay, is A6.1's installed text byte for byte (checked
  in under `tests/fixtures/agents_a6_1/`), minus the model line, with the
  two `backlog-curator` rewrites declared in the test. Beside it are the
  anchor-set pin per agent, the host-vocabulary lint over the sources (the
  adapter supplies `HOST_VOCABULARY`: its tool names and `AskUserQuestion`;
  a word such as "Read" counts only where it is used as a tool), the
  capability mapping, the plan writing the render, the A6.1-copy cases, and
  `agents show`. `tests/test_assets.py` ships a neutral agent in its
  every-kind library.

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
| 4 | `vague verdict` and `judgement fail`; the D1 permutation test | A4, and A7 for the permutation test. The scenarios are delivered with A4 increment 7: `vague-verdict`, `judgement-fail`, `judgement-fail-standard`, `stale-verdict` and `engine-answers`. The permutation test is still owed, with A7 |
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
        CLI["invariant CLI<br/>guidelines render | show | verdict | baseline<br/>verdicts submit | verdicts show"]
    end
    subgraph domains["domains/ (seven)"]
        GDOM["guidelines (NEW, not an FSM)"]
        PIPELINE[pipeline]
        SESSION[session]
    end
    subgraph core["core/"]
        CGUIDE["guidelines (NEW): ruleset model, validation,<br/>selection, rendering, baseline format"]
        CVERD["verdicts (A4.1, DN-1): the verdict record,<br/>submission parsing, D5's checks; pure"]
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
    GDOM -- plan_render, plan_baseline, plan_verdicts, read_standings --> RULES
    GDOM -- record_verdicts --> PIPELINE
    GDOM --> STORE
    GDOM --> GIT
    GDOM --> PRIVACY
    GDOM --> adapters
    RULES --> CGUIDE
    RULES --> adapters
    RULES -- evaluate, reads handed in as callables --> CFIND
    RULES -- plan_verdicts asks check_all, read_standings asks standings and unowed, the reads handed in as Facts --> CVERD
    CVERD -- meets, NaAnswer --> CFIND
    CVERD -- Guideline, Ruleset, digest_of --> CGUIDE
    CVERD -- project_relative --> MANIFEST
    RULES -- base-branch baseline, diff lines, tracked files, renames --> GIT
    CFIND --> CGUIDE
    CFIND -- project_relative, glob_matches --> MANIFEST
    CGUIDE -- project_relative --> MANIFEST
    FSM -. injects guidelines.* by role .-> RULES
    PIPELINE -- evaluate, grant_outcomes, the family gate records kept at the landing (A4.6) --> RULES
    CC --> CGUIDE
    CC -- edit_advisory, A3.8; turn_guidelines, A5.2 --> RULES
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
  and `CC --> RULES`. The turn projection was first drawn asking `select`
  directly; A5 increment 2 routes the decision through `CC --> RULES`
  (`turn_guidelines`), because the roles, the file scope and the pin are
  reads `core/rules` already owns, and keeps `CC --> CGUIDE` for the pure
  renderer, `render_turn_index`. The edit-time advisory (A3 increment 8) is
  `CC --> RULES` too: `PostToolUse` asks `edit_advisory` and only formats
  what it answers.
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
`guidelines.mechanical_clean` (D4, "As built: A3 increment 7"), and A4
increment 5 builds the verdict half, for `guidelines.verdicts_complete` (D5,
"As built: A4 increment 5").

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
    on_edit: true            # just-in-time statements at PostToolUse (D7)
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
