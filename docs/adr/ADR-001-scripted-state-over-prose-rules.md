# ADR-001 — Scripted state over prose rules

- **Status:** Accepted
- **Date:** 2026-08-08
- **Supersedes:** nothing (foundational)

## Context

An agent setup that has accumulated real operational discipline ends up
encoding that discipline as **prose**: a `CLAUDE.md` full of MUST rules, a
`UserPromptSubmit` hook that re-injects a pipeline reminder on every turn, and
a memory store of hard-won lessons that the model is expected to read, retain,
and apply.

Prose-as-control-flow has three failure modes, all of them observed in the
project this library was extracted from:

1. **Non-compliance is silent.** A rule that says "invoke the architect agent
   at the start of every coding task" is followed or not, and nothing records
   which. There is no artifact to inspect afterwards.
2. **Context pressure erodes it.** The rules compete for attention with the
   task. As a session grows, the earliest-injected instructions are the most
   likely to be summarized away — precisely the setup rules.
3. **It cannot be verified.** "Did the review gate run?" is unanswerable from
   the transcript alone, because the gate was never a thing that *ran* — it
   was a sentence that was *read*.

The accumulated evidence for (3) is the sharpest: the source project's memory
store records six vacuous green checks in a single session, a promised gate
that was never actually written, and a filed fix that turned out to be a
no-op. Every one of those is a case where a *described* control did not exist
as an *executed* control.

## Decision

Replace prose-as-control-flow with **scripted state**.

The library persists an explicit state machine on disk. Work advances by
calling a CLI that validates and records each transition. Host hooks enforce a
configurable subset of those transitions and inject the *current state* into
context instead of a static rulebook.

The inversion, stated plainly:

> Instead of reminding the model of all the rules on every turn, tell it what
> state it is in, which transitions are legal from here, and which gate is
> currently pending.

This makes three things true that were not true before:

- **Compliance is an artifact.** The state file records what happened. A skipped
  gate is a visible fact, not an absence.
- **Context is bounded and relevant.** The injected text is derived from state,
  so it is short and about *now*, rather than long and about everything.
- **Controls are executable.** A gate is a function with a fixture proving it
  fails on a known-positive input. "Does the gate exist" becomes a test.

## Scope: four state domains

| Domain | What it governs | Shape |
|---|---|---|
| **Session** | Isolation (worktree/branch), naming, the push barrier, teardown | FSM |
| **Pipeline** | The methodology for one task: design → test → implement → review → commit | FSM, pluggable |
| **Work item** | Ideas, bugs, release rows and their statuses | FSM per item type |
| **Memory** | The knowledge store: schema, dedup, generated index | Managed store + invariants |

The first three are genuine finite state machines. Memory is not — it is a
store with invariants over it — but it belongs in the same library because it
has the same failure mode: a hand-maintained index over hundreds of files
drifts, and drift is undetectable without a script that checks it.

## Decisions recorded

Each row is a decision taken deliberately, with the alternative that was
rejected and why.

### Packaging and distribution

| # | Decision | Rejected alternative | Rationale |
|---|---|---|---|
| D1 | Standalone repo, consumable as a git submodule **and** a published package | Build it inside the host project and extract later | Extraction-later reliably carries host-isms into the "generic" layer. A separate repo forces the seam to be real on day one. |
| D2 | PyPI, installed with `pipx` | npm; dual-publish; `curl \| sh` | The core is Python, so a Python channel is honest. npm would mean shipping Python inside a JS package. The submodule path covers projects that refuse a global install. |
| D3 | The name is `invariant` | `setsquare`, `vitruvius`, `masterplan` | It names the mechanism rather than a metaphor for it. The library's whole job is to hold invariants that do not drift between sessions. Verified unclaimed on the package index at the time of writing. |

### Architecture

| # | Decision | Rejected alternative | Rationale |
|---|---|---|---|
| D4 | Bash shims, Python core | Pure bash; pure Node | Hooks must start fast and run with no build step, which favors bash. State logic needs real data structures, JSON, and a test suite, which bash makes painful. The split puts each where it is strong. |
| D5 | Methodologies are **declarative** state-machine files | Python plugin classes | Adopting a methodology should not require writing code. A methodology is a graph plus gates; YAML expresses that. Gates that need real logic name a rule id, which is the escape hatch. |
| D6 | Project specifics come from a **manifest plus adapters** | Manifest only; auto-detection | A manifest alone cannot parse an ecosystem's test output or know its coverage tooling. Auto-detection is convenient and surprising. Adapters carry ecosystem smarts; the manifest carries project facts and overrides. |
| D7 | Host-agnostic core, Claude Code as the first **adapter** | Bind directly to Claude Code | The state engine has nothing to do with any particular agent host. Keeping hosts behind an interface costs little now and avoids a rewrite later. |
| D8 | Installed artifacts are **thin shims** that call the CLI | Vendored copies; regenerated files | Vendored copies drift and create merge burden on upgrade. Regeneration destroys local edits. A shim has nothing to drift: logic lives in the versioned library, customization lives in the manifest. |
| D9 | Runtime state is **local JSON, gitignored** | SQLite; committing work-item state | State is machine-local and session-scoped; committing it would produce constant conflicts between concurrent sessions. JSON stays hand-inspectable and hand-fixable when something wedges — which matters, because the recovery path for a corrupted state file is a human reading it. SQLite's transactions are not needed once writes are per-session files plus one locked registry. |

### Policy

| # | Decision | Rejected alternative | Rationale |
|---|---|---|---|
| D10 | Enforcement is **profiles plus per-rule overrides** | Named profiles only; per-rule only | Profiles make the common case one line. Per-rule overrides mean a project that disagrees with exactly one rule does not have to abandon the preset. |
| D11 | Enforcement is **hybrid**: block a critical few, advise the rest | Block everything; advise everything | Blocking everything fights legitimate exceptions and trains the user to disable the tool. Advising everything is what prose already does, and is the problem being solved. |
| D12 | **Docs-first is core and always on** | Ship it as an opt-in profile; leave it to the host project | This is the strongest opinion the library holds, taken deliberately. A project without architecture docs is told to create them rather than allowed to skip the gate. The rationale: dependency knowledge derived from file search is guesswork that goes stale silently, whereas a maintained diagram is checkable. Projects that reject this premise are not the audience for this library. |
| D13 | The **privacy gate ships enabled** with configurable patterns | Ship it disabled; leave it project-local | A redaction gate that is off by default protects nobody, because the people who need it most do not know to look for it. Defaults cover home paths, emails, addresses, tokens, clock times, and links; a project extends or trims the set. |
| D14 | Four agents move into the library; host-specific agents stay local | Move all; move none | Design, review, work-item intake, and repro-proving are generic once paths and commands come from the manifest. A publisher agent bound to one app store is not. Supporting both proves the library composes with project-local agents rather than replacing them. |
| D15 | Library agents are **ported near-verbatim**, with paths parameterized | Rewrite generically from scratch | The existing agent prose encodes years of specific lessons — the exact ways a check can pass vacuously, the exact ways a subagent's "green" can be a lie. A clean rewrite would produce tidier text and quietly drop that knowledge. |
| D16 | Agent provenance is a comment **inside** the frontmatter, and **loadability is checked separately from currency** | Keep the marker above the frontmatter; drop the marker; hold provenance in a sidecar file | Shipped as a header above the frontmatter, the marker pushed the block off line 1 and the host skipped all four agents silently — while `verify` reported success, because what it checked for was the marker that caused the skip. The lesson generalises past this bug: a check that looks for the artifact of an installation is not a check that the installation works. The two questions are now separate rows in `doctor`, and neither may stand in for the other. |
| D17 | Agent-role gates are **advisory in every profile**, `strict` included | Block in `strict`; omit the gates entirely | Installing an agent and dispatching one are different events, so the gates are worth having. But the engine can prove an agent *ran*, never that one was *needed* — that is a judgement, and `strict` already keeps its heuristics advisory on the grounds that a blocking heuristic is a false positive with authority. A gate that legitimate one-line work must override routinely is how `--force` becomes a reflex on the gates that do matter. |

## Consequences

**Good.** Compliance becomes inspectable. Context injection shrinks and stays
relevant. Gates become testable units with fixtures. A second project gets the
whole discipline by writing one manifest. The host project's setup stops being
a bespoke pile of scripts and becomes a configuration of a versioned library.

**Costs.** There is now a dependency between the host project and this library,
and a version skew hazard with it. Two languages are maintained instead of one.
The state store is a new thing that can wedge, so it needs recovery commands
(`invariant state show`, `invariant state repair`) as first-class surface, not
afterthoughts.

**Accepted risk.** A state machine can be *wrong* — it can insist on a
transition that does not fit a real situation. The mitigation is D10 and D11
together: any rule can be dropped to `warn` or `off` in the manifest without
forking the library. A rule that a project keeps overriding is a bug report
against the default, and the override is the evidence.

## Notes

The library is expected to dogfood every rule it ships: its own repository
carries a manifest, runs its own pipeline, and its own architecture docs are
subject to the docs-first gate it enforces on others. A control the library
will not run against itself is a control it should not ship.
