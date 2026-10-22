# Quickstart

Ten minutes from an unmodified repository to a governed Claude Code session.
This page assumes `invariant` is already installed on your machine
([Installation](installation.md)); nothing here is specific to the language
or framework of your project.

## 1. Adopt it into a project

```bash
cd ~/code/my-app
invariant init
```

`init` detects the ecosystem from what is in the directory (`pyproject.toml` →
`python`, `package.json` → `node`, an `.xcodeproj`/`.xcworkspace` → `swift`;
anything else → `generic`) and writes:

| Path | What it is |
|---|---|
| `.claude/invariant.yaml` | the manifest — three lines, everything else defaulted |
| `.claude/hooks/invariant-*.sh` | seven shims, one per Claude Code hook event, each a few lines that `exec invariant hook <event>` |
| `.claude/settings.json` | hook wiring, merged into whatever is already there |
| `.claude/agents/*.md` | four library agents (design, review, backlog, bug repro) |
| `docs/architecture/overview.md` | a starter architecture doc — only when that directory does not already exist |
| `.gitignore` | one line for the machine-local state root |

Pass `--ecosystem`, `--name` or `--profile strict|standard|loose` to override
the detection; `--dry-run` prints the list and writes nothing.

Then commit, and check:

```bash
git add -A && git commit -m "adopt invariant"
invariant doctor
```

Committing is not ceremony: a session works in a linked worktree, a worktree
checks out a commit, and an uncommitted manifest is invisible from inside one.
`doctor` prints one line per check and exits 1 if anything is wrong; the
[Troubleshooting](troubleshooting.md) page is organised by its output.

Start a **new** Claude Code session in the project. Hooks are read when a
session starts, so the one that was open while you ran `init` does not have
them.

## 2. What changes inside Claude Code

Nothing is required of you. The first thing the agent sees in the new session
is injected by the `SessionStart` hook:

```
invariant: this host session owns no session yet.
  Start one before editing: invariant session start <task-name>
```

From here the agent is told, every turn, *where it is* rather than what the
rules are — `invariant context` prints the same text on demand:

```
invariant — session 5f0e8f75 (add-widget-5f0e8f75) is isolated
  session may go to: working, abandoned
  pipeline tdd: designing  (task: add the widget)
  may advance to: red, abandoned
  reachable with nothing owed: red, abandoned
```

Under the default `standard` profile three things block and the rest advise:
editing in the shared checkout rather than a session worktree, leaving the
design state without having read the architecture docs, and writing personal
data into a document or commit message. `invariant rules list` shows every
rule with its resolved level.

## 3. One task, end to end

You can drive it yourself from a terminal — the agent runs exactly these
commands. The project here is on `main`; the base branch is whatever is
checked out when the session starts.

```bash
invariant session start add-widget
#  session b9c169e2 (add-widget-b9c169e2) registered
#    next: invariant session isolate --execute
#          worktree ../my-app-wt-b9c169e2  branch session/b9c169e2

invariant session isolate --execute
cd ../my-app-wt-b9c169e2

invariant pipeline start --task "add the widget"
#  pipeline tdd started in designing

invariant pipeline gates
#  to red:
#    [BLOCK] docs.read_architecture: no architecture doc has been read in this pipeline
#          try: read the relevant doc under docs/architecture
```

In Claude Code the agent reads `docs/architecture/overview.md` with its Read
tool, the `PostToolUse` hook records that against the pipeline, and the gate
clears. The evidence comes from the host — a `cat` in a terminal is not seen
by any hook — so when you drive a pipeline by hand, pass this one gate
explicitly and say why:

```bash
invariant pipeline advance red --force --reason "read overview.md by hand"
```

The override lands on the session record with that reason. From here on:

```bash
invariant pipeline test              # in red: runs commands.test, records the failure
invariant pipeline advance green     # make it pass
invariant pipeline advance refactor
invariant pipeline advance reviewing
invariant pipeline coverage          # per-file numbers, read by coverage.meets_floor
invariant pipeline advance committed
```

Each `advance` validates the edge against the methodology's state machine,
evaluates the gates on it, and records the result. A gate that is not
satisfied refuses and says why, including what to try.

## 4. Land it

```bash
invariant session files add src/widget.py tests/test_widget.py
git commit -am "Add the widget"
invariant session review             # review gates → reviewed
cd ~/code/my-app                     # the merge runs from the main checkout
invariant session merge              # rebase + fast-forward onto main
invariant session teardown           # worktree and branch removed
```

Pushing is deliberately not in that list. A session ends at the merge; publishing
is a separate step with a separate cost, off by default (`session.push: false`).

## 5. Let the agents do it

In practice you rarely type the above. The four agents `init` installed are
wired to these commands and Claude Code picks them by description:

| You say | Agent | What it does |
|---|---|---|
| "add a widget", "refactor the parser" | `feature-architect` | reads the architecture docs, plans against the diagrams, implements with TDD to the coverage floor, updates the docs |
| "ship this", "commit this", "review and commit" | `compliance-reviewer` | reviews the session's files, runs build/test/coverage, commits, and merges when the session's plan is complete |
| "we should…", "file a bug: X is broken" | `backlog-curator` | interviews you for the missing fields and writes a work item |
| "reproduce BUG_012", "is this bug still real" | `bug-reproducer` | writes one failing test on the symptom and returns a verdict |

See [The agents](agents.md) for what each one needs from you.

## Where to go next

- [Daily workflow](workflow.md) — sessions, pipelines, deliverable plans,
  picking work up again tomorrow
- [Configuration](configuration.md) — profiles, per-rule levels, commands,
  worktree placement, privacy
- [Troubleshooting](troubleshooting.md) — every refusal you are likely to
  meet, and the route past it
