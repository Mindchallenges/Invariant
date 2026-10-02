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

## Install

One binary per machine, from the repository (it is not on PyPI yet). Python
3.10+, git, and [Claude Code](https://claude.com/claude-code) are the
requirements.

```bash
uv tool install git+https://github.com/Mindchallenges/invariant
# or: pipx install git+https://github.com/Mindchallenges/invariant
```

## Quick start

```bash
cd your-project
invariant init             # manifest, hook shims, host wiring, four agents,
                           # and a starter docs/architecture/overview.md if
                           # the project has none
git add -A && git commit -m "adopt invariant"
invariant doctor           # one line per check
```

Then open a new Claude Code session in the project. The agent is told what
state it is in and which transitions are legal; you say what you want done.
Driving it by hand looks like this:

```bash
invariant session start add-widget       # base = the branch you are on
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

It works on any repository. `init` detects Python, Node and Xcode projects
and falls back to `generic`, which runs whatever commands the manifest names;
nothing in the library assumes a language, a layout, or a default branch.

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

## You are the authority

The engine exists to make an agent deterministic. It does not exist to make
decisions about your repository over your objection. **Every gate has a route
past it, and every route past it is recorded** (ADR-001 D25).

| You want to | Type |
|---|---|
| change policy permanently | `enforcement.rules` in the manifest — a level per rule |
| be asked instead of stopped | set that rule to `ask`; the call comes to you |
| pass one gate, once | `invariant override <rule> --reason '<why>'` |
| pass a transition gate, once | `... --force --reason '<why>'` |
| stop being asked for a while | `invariant standdown --reason '<why>'` |
| stop using the tool | `invariant uninstall` |

Two things bound that rather than qualify it.

**Every route leaves a record.** A forced transition, a spent override token
and a stand-down all land on the session record with the reason you gave. An
override that leaves no trace is indistinguishable from the gate never having
run, and the whole point of the library is that the difference is visible.

**`privacy.clean` is the one gate a blanket route must name.** `standdown`
holds every other rule; folding this one in costs `--include privacy.clean`. It
is the only gate whose failure cannot be undone after the fact — nothing
un-writes an identifier out of a published history — so going past it stays a
separate sentence you wrote.

And when a rule you meant to relax names nothing, you are told. An
`enforcement.rules` key that matches no rule is refused at load with the
nearest real id, rather than accepted and silently discarded. A control handed
back that quietly does nothing is worse than one never offered.

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

**User guide** — `docs/guide/`, for anyone adopting the tool:

- [Installation](docs/guide/installation.md) · [Quickstart](docs/guide/quickstart.md)
- [Daily workflow](docs/guide/workflow.md) — sessions, pipelines, deliverable plans, resuming
- [The agents](docs/guide/agents.md) · [Configuration](docs/guide/configuration.md)
- [Work items and memory](docs/guide/backlog-and-memory.md)
- [Troubleshooting](docs/guide/troubleshooting.md) · [Extending it](docs/guide/extending.md) · [Uninstall](docs/guide/uninstall.md)

**Reference** — `docs/architecture/`, the documents the docs-first gate reads:

- [`overview.md`](docs/architecture/overview.md) — layers and the dependency rule
- [`state_model.md`](docs/architecture/state_model.md) — the machines and the storage layout
- [`manifest.md`](docs/architecture/manifest.md) — full schema and the adapter interface
- [`cli.md`](docs/architecture/cli.md) — the complete command surface

**Decisions** — `docs/adr/`; ADR-001 is why scripted state, with every design
decision and the alternative that was rejected.

Render it all as one site with `uv run --group docs sphinx-build -b html docs docs/_build/html`.

## Development

```bash
git clone https://github.com/Mindchallenges/invariant && cd invariant
uv sync --group dev
uv run pytest
uv tool install --editable .     # the clone becomes the installed tool
```

The library dogfoods what it ships: its own repository carries a manifest, and
its architecture docs are subject to the docs-first gate it enforces on others.
A control it will not run against itself is a control it should not ship.
