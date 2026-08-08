# invariant

Deterministic, scripted state for agent workflows.

## The problem

An agent setup that accumulates real operational discipline ends up encoding it
as **prose**: a rules file full of MUSTs, a hook that re-injects a pipeline
reminder every turn, a pile of remembered lessons. Prose-as-control-flow fails
three ways:

- **Non-compliance is silent.** "Invoke the review agent before committing" is
  followed or not, and nothing records which.
- **Context pressure erodes it.** The rules compete with the task, and the
  earliest-injected instructions are the first to be summarised away.
- **It cannot be verified.** "Did the gate run?" is unanswerable, because the
  gate was never something that *ran* — it was a sentence that was *read*.

## The idea

Replace prose-as-control-flow with state a script owns.

> Instead of reminding the model of all the rules every turn, tell it what
> state it is in, which transitions are legal from here, and which gate is
> currently pending.

Work advances by calling a CLI that validates and records each transition.
Hooks enforce a configurable subset and inject the *current state* instead of a
static rulebook. Compliance becomes an artifact you can read; gates become
functions with fixtures proving they fail on a known-positive input.

## Quick start

```bash
pipx install invariant     # or: uv tool install invariant
cd your-project
invariant init             # writes a manifest, installs hook shims, wires the host
git add -A && git commit -m "adopt invariant"
invariant doctor           # verify the install
```

Then, per task:

```bash
invariant session start add-widget
invariant session isolate --execute      # own worktree, own branch
cd ../your-project-wt-<id>
invariant pipeline start --task "add the widget"
invariant pipeline gates                 # what is owed right now
invariant pipeline advance red           # validated, gated, recorded
```

A gate that is not satisfied refuses the transition and says why:

```
[BLOCK] docs.read_architecture: no architecture doc has been read in this pipeline
        The diagrams are the authoritative dependency map; reading them is what
        makes the plan trustworthy.
        try: read the relevant doc under docs/architecture
```

## What it governs

| Domain | What it holds |
|---|---|
| **Session** | Worktree isolation, the one-push barrier, merge safety, teardown |
| **Pipeline** | One task's progress through a methodology (`tdd`, `bugfix`, `spec-first`, `chore`, `none`) |
| **Work items** | Ideas and bugs — including that a bug cannot reach `fixed` without passing through `reproduced` |
| **Memory** | A knowledge store with a *generated* index, schema validation, and dead-link detection |

## Configuration

One file, `.claude/invariant.yaml`. Everything has a default; a usable manifest
is three lines.

```yaml
version: 1
project: { name: my-app, ecosystems: [python] }
```

Tune enforcement with a profile plus per-rule overrides:

```yaml
enforcement:
  profile: standard          # strict | standard | loose
  rules:
    coverage.meets_floor: block
    memory.no_duplicates: "off"
```

Each rule is `block`, `warn`, or `off`. Level is a property of how a verdict is
*delivered*, not of the rule's logic — which is why the same rule can block in
one project and advise in another with no code change.

`invariant config show --provenance` prints every resolved value and the layer
it came from, because "why is this rule blocking me" has to be answerable
without reading library source.

## Opinions it holds

Most of this library is mechanism. Three things are policy, taken deliberately:

- **Docs-first is core, not optional.** You may not leave a design state until
  you have read the architecture docs, and a structural change must update
  them. Dependency knowledge derived from file search is guesswork that goes
  stale silently. A project without architecture docs is told to create them.
- **A bug cannot be fixed before it is reproduced.** The lifecycle has no edge
  from `filed` to `fixed`. A fix for an unreproduced defect proves nothing.
- **Personal data never reaches a document.** The privacy gate ships enabled,
  scanning docs, memories, and commit messages. Findings redact by default: a
  report that quotes what it found is a second copy of it.

Any of these can be dropped to `warn` or `off` in the manifest. A rule you keep
overriding is a bug report against the default.

## Extending it

- **A methodology** is a YAML state machine — states, transitions, and gates.
  Point `methodology.extra_machines` at your own; a project machine shadows a
  shipped one, so forking a methodology never means forking the library.
- **An adapter** carries ecosystem knowledge: how to build, how to test, and
  which of that ecosystem's success signals are unreliable. That last part —
  `hazards()` — is why the abstraction earns its place. A hazard is not a rule;
  it annotates a result so a green is not mistaken for proof.
- **A backend** stores work items as markdown files, JSON, or directives in a
  reStructuredText page. Lifecycle semantics live above it, so switching
  formats changes nothing about what a status means.
- **A host** is the binding to an agent tool. Claude Code ships; the core is
  host-agnostic, reaching it only through the CLI.

## Documentation

- `docs/adr/ADR-001` — why scripted state, and every design decision with the
  alternative that was rejected
- `docs/architecture/overview.md` — layers and the dependency rule
- `docs/architecture/state_model.md` — the machines and the storage layout
- `docs/architecture/manifest.md` — full schema and the adapter interface
- `docs/architecture/cli.md` — the complete command surface

## Development

```bash
uv sync --group dev
uv run pytest
```

The library dogfoods what it ships: its own repository carries a manifest, and
its architecture docs are subject to the docs-first gate it enforces on others.
A control it will not run against itself is a control it should not ship.
