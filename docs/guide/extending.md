# Extending it

Four seams, in the order you are likely to need them. Only the first two are
configuration; the other two are contributions to the library.

## A methodology of your own

A methodology is a YAML state machine — states, legal successors, and the
rule ids that gate each edge. Put one in the project and name it:

```yaml
methodology:
  pipeline: spike
  extra_machines:
    - .claude/machines/spike.yaml
```

```yaml
# .claude/machines/spike.yaml
id: spike
kind: pipeline
description: Throwaway exploration whose only deliverable is a written finding.

initial: exploring
terminal: [committed, abandoned]

states:
  exploring:
    description: Try things. Read the docs that own the area first.
    roles: [design]
    next: [writing_up, abandoned]
  writing_up:
    description: The finding, and what it rules in or out.
    roles: [review]
    next: [committed, exploring, abandoned]
  committed:
    roles: [landing]
    entry_gates: [privacy.clean]
  abandoned:
    roles: [abandon]
```

The rules:

- **`next` is structural.** A transition to a state not listed is rejected
  whatever the enforcement level; that is validity, not policy.
- **Gates are rule ids** from `invariant rules list`, and whether a failing
  gate blocks or warns is the manifest's decision at evaluation time. Prefer
  `entry_gates`: a gate belongs to the outcome it protects, and an exit gate
  fires on every edge out of a state, retreats included.
- **Roles are how the engine attaches what you cannot opt out of.** `design`
  carries the docs-first read gate; `landing` carries `docs.updated` and the
  agent-role checks; `abandon` makes the edge into that state ungated. A
  pipeline machine with a review state and no `landing` state is refused at
  load.
- **`red_phase`** names the state that does proof-of-red, when a machine
  declares `test.new_test_fails` and the state is not literally called `red`.
- **An id that matches a shipped machine shadows it.** Forking `tdd` to drop
  one state never means forking the library.

The complete format is in [state_model.md](../architecture/state_model.md#declarative-fsm-format),
and the shipped machines under `src/invariant/data/machines/` are the examples
to copy from.

## Commands for an ecosystem the library does not know

You do not need an adapter to use invariant on a Go, Rust, Elixir or any
other project. `ecosystems: [generic]` runs the literal commands in the
manifest and parses nothing — which is the honest behaviour for a toolchain
whose output format the library has not been taught:

```yaml
project:
  ecosystems: [generic]
commands:
  build: go build ./...
  test: go test ./...
  test_one: go test -run {selector} ./...
  lint: golangci-lint run
  coverage: go test -coverprofile=.build/cover.out ./...
  coverage_report: go tool cover -func=.build/cover.out
```

What you give up is the parsing: `coverage.meets_floor` needs per-file
numbers the generic adapter cannot extract, so set it to `warn` or `off`, and
the ecosystem's *hazards* — the ways its success signal can lie — are not
annotated. Everything else, every session, pipeline, docs, privacy and
work-item gate, works unchanged.

## An adapter

An adapter is a Python class that carries what the manifest should not have
to spell out for an ecosystem: how to detect it, the default commands, which
changed files are source and which are tests, how to parse a coverage report,
and `hazards()` — a list of ways that ecosystem's green can be false (a
scheme that builds nothing, a test target that was never linked). A hazard is
not a rule; it annotates a result so that a green is not mistaken for proof.

Adapters are registered in `src/invariant/adapters/__init__.py`, so adding
one is a contribution to the library rather than a project setting. The three
shipped ones — `python_.py`, `node.py`, `swift_xcode.py` — are the templates,
and the interface is documented in
[manifest.md § Adapters](../architecture/manifest.md#adapters). Tests live in
`tests/test_adapters.py`; the coverage floor applies to the adapter too.

## A work-item backend

`items.backend` selects how work items are stored: a directory of markdown
files, one JSON document, or `need` directives in a reStructuredText page. The
lifecycle machines live above the backend, so a new one only has to read and
write `Item` records — see `core/items.py`, where each shipped backend is a
few dozen lines against the `Backend` protocol. Also a contribution.

## A host

The core is host-agnostic and reaches the host only through the CLI: a host
binding knows which events the agent tool fires, how to read its payload and
how to answer it, and how to install shims and assets. Claude Code is the one
that ships (`hosts/claude_code.py`). A second host would implement the same
six-event surface — session start, prompt submit, before and after a tool
call, stop, session end — and `invariant hook <event>` would do the rest.

## Project-owned agents, commands and skills

Not an extension of the library so much as a thing it leaves alone: anything
under `.claude/agents/`, `.claude/commands/` or `.claude/skills/` that the
library did not ship is checked by `doctor` for *loadability* (does the host
parse it) and never for drift. Name an agent in `agents.local` and, to make it
answer for a pipeline role, in `agents.roles`. See [The agents](agents.md).
