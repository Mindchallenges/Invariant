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

## The Session Oracle

The oracle is the exception to "sessions in different repositories do not see
each other": a machine-wide, read-only view of every session in every
invariant project, which tells you when one is waiting on you and recommends
the next command for each. A project joins it by itself on `init` or its first
session start. `invariant oracle list` works with the plain install. The live
dashboard needs the `[oracle]` extra, which adds Textual:

```bash
pip install 'invariant[oracle]'
# installed as a uv tool: add Textual to that tool's environment
uv tool install --with textual git+https://github.com/Mindchallenges/invariant
```

Then, on macOS, start the background watcher, which also posts a notification
when a session needs you, and open the dashboard:

```bash
invariant oracle install      # a LaunchAgent that keeps `oracle watch` running
invariant oracle tui          # the dashboard; every action asks you to confirm it
invariant oracle status       # is the agent loaded, and is its snapshot fresh
```

Without the watcher the dashboard still works. It scans in-process and shows
a "watcher not running" banner. It never answers a permission prompt for
you. See [the oracle's architecture page](../architecture/oracle.md).

### The dashboard on your phone, over Tailscale

The oracle can serve its dashboard from the Mac to your phone, with the same
confirm-first actions as the TUI. The full setup, from installing Tailscale
to troubleshooting, is [The oracle on your phone](oracle-web.md). In short:

It listens only on the Mac's Tailscale address, so only devices on your
tailnet can reach it. Each device must be paired once:

```bash
invariant oracle install --web        # serve it always: a LaunchAgent that waits for Tailscale
invariant oracle web                  # or serve it by hand; Ctrl-C stops it
invariant oracle web pair --name phone  # a one-time code and link, valid 5 minutes
invariant oracle web devices          # the paired devices
invariant oracle web revoke <id>      # forget one device
invariant oracle uninstall --web      # stop serving it
```

By hand, `oracle web` refuses to start when Tailscale is not up. The agent
waits for it instead, and binds as soon as Tailscale connects.

Open the link `pair` prints on the phone and press "Pair this device". The
phone keeps a cookie from then on, and every other device gets only a "not
paired" page. The page refreshes itself every few seconds and turns its
"Live · updated N s ago" line red, "Stale", if the rows stop moving. It leads
with what needs you; the guide's [The oracle on your phone](oracle-web.md),
"Using the page", walks through it.

A card with something to run has a button named for its step, such as
"Re-run tests…". Pressing it opens a sheet from the bottom that says what
will happen, shows the command (with Copy) and, behind a toggle, the exact
folder and variables. Nothing runs until you press the button named for the
deed, such as "Run tests". The result appears in the same sheet when the
command ends, and your recent actions are listed under "Recent" in each
session's detail. A step that resumes a session opens a Terminal window on the Mac,
whether or not anyone is at it, running `claude --remote-control <name>`;
pick that session up from Claude Code's Remote Control on the phone. The page
refuses what the TUI refuses, and it never answers a permission prompt.
Every action is logged to `~/.invariant/web/actions.log`.

<!-- invariant: allow-privacy -->
**macOS may ask for Local Network permission.** The first time the Python that
runs invariant listens on the tailnet address, macOS may ask whether it may "find
devices on your local network". Allow it, or the phone cannot connect. You
can grant or revoke it later in System Settings > Privacy & Security > Local
Network, under the Python interpreter (or terminal app) that serves the page.
`--allow-loopback` serves on 127.0.0.1 instead when no Tailscale address is
found, for a test on the Mac itself; loopback needs no permission. The
decisions behind the listener and the pairing are in ADR-008.

**Closing a leftover window asks for Automation permission.** The page's
Leftovers screen can close a Claude Code window that outlived its session:
Claude is asked to exit (one SIGTERM, never a kill), then the Terminal tab is
closed if only a shell is left in it. The first close makes macOS ask whether
the app that runs the oracle may control Terminal. Allow it on the Mac; if
nobody answers, Claude still exits and the tab stays open for you to close by
hand. You can change it later in System Settings > Privacy & Security >
Automation. The same step is `invariant oracle close-host` on the command
line; the decision is ADR-009 D15, and the checks it makes before signalling
are in [the oracle's architecture page](../architecture/oracle.md).

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
