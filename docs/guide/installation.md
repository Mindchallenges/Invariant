# Installation

`invariant` is one command-line program, installed once per machine. Each
project then adopts it with `invariant init`, which writes a few committed
files into the project's `.claude/` directory and nothing else.

## Requirements

- Python 3.10 or newer
- git — sessions are linked worktrees
- Claude Code, the one agent host that ships;
  the CLI works on its own, but the hooks and agents are what make a session
  governed rather than merely recorded

## Install the CLI

The package is not on PyPI yet; install it from the repository with any
tool that handles a git URL. `uv` and `pipx` both put the binary in
`~/.local/bin`, which is one of the places the hook shims look.

**uv**

```bash
uv tool install git+https://github.com/Mindchallenges/invariant
```

**pipx**

```bash
pipx install git+https://github.com/Mindchallenges/invariant
```

**From a clone** — the clone becomes the installed tool, so edits in it are
live in every project:

```bash
git clone https://github.com/Mindchallenges/invariant
uv tool install --editable ./invariant
```

Check it:

```bash
invariant --help
command -v invariant        # should print a path; ~/.local/bin/invariant is typical
```

If `command -v` prints nothing, `~/.local/bin` is not on PATH in this shell.
The shims will still find the binary there, but you will want it for yourself:
`uv tool update-shell` or `pipx ensurepath` adds the line.

## Adopt it in a project

```bash
cd <project>
invariant init [--ecosystem python|node|swift|generic] [--profile standard|strict|loose]
git add -A && git commit -m "adopt invariant"
invariant doctor
```

What `init` writes, and why each file is committed, is in the
[Quickstart](quickstart.md#1-adopt-it-into-a-project). Then start a **new**
Claude Code session in that project: hooks are read at session start.

Run `init` once per repository, not per worktree — the configuration lives at
the repository root and every linked worktree checks it out.

### How the hooks find the CLI

Each shim under `.claude/hooks/` tries, in order:

1. `invariant` on PATH
2. `~/.local/bin/invariant`
3. `<project>/.claude/invariant/bin/invariant` — a project-local install, for
   a machine where nothing can go on PATH

and, finding none, prints an empty verdict and exits zero. A shim never carries
an absolute path with a username in it, because it is committed. The resolved
binary is the same for every project on the machine, so upgrading the CLI
upgrades every project at once.

## Upgrade

```bash
uv tool upgrade invariant          # pipx: pipx upgrade invariant
```

Nothing in a project needs rewriting after an upgrade — the shims are three
lines that `exec` the CLI. `invariant doctor` reports when the installed
*agents* are from an older release, and `invariant agents install` refreshes
them while skipping, by name, any you have edited. See
[Uninstall and upgrade](uninstall.md).

## Several projects, one machine

Runtime state is per repository, in its git common directory
(`.git/invariant/state/`) — a place every linked worktree resolves to the same
way and git never commits — so sessions in different repositories do not see
each other, and sessions in one repository all see the same registry.
`invariant session list` lists the sessions of the repository you are
standing in. The one thing shared across repositories is the binary.

## Working on invariant itself

```bash
git clone https://github.com/Mindchallenges/invariant && cd invariant
uv sync --group dev
uv run pytest
uv run --group docs sphinx-build -b html docs docs/_build/html
```

With `uv tool install --editable .` the clone *is* the installed tool, so a
change is live in every project as soon as it is on the branch the clone has
checked out — which is also the thing to remember when it is not: a fix on a
session branch is live nowhere until it is merged. The repository dogfoods
what it ships; its own manifest is `.claude/invariant.yaml` and reads as a
worked example of the [configuration](configuration.md).
