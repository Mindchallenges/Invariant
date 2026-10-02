# The agents

`invariant init` installs four agents into `.claude/agents/`. They are
ordinary Claude Code subagents — markdown files with a frontmatter — written
to drive the CLI, so that the discipline the gates enforce is also the
discipline the agents practise. Claude Code picks them from their
descriptions; you rarely name one.

| Agent | Pipeline role | Invoked when you say |
|---|---|---|
| `feature-architect` | `design` | "add…", "refactor…", "fix this bug", any coding task |
| `compliance-reviewer` | `review` | "ship this", "commit this", "pre-commit", "review and commit" |
| `backlog-curator` | — | "we should…", "park this", "file a bug", "X is broken" |
| `bug-reproducer` | — | "reproduce BUG_<id>", "is this bug still real", "prep this for dev" |

## What each one does

### feature-architect — design and build

Reads the architecture docs first (the `docs.read_architecture` gate), plans
the change against the dependency diagrams rather than by file search,
implements with TDD to `methodology.gates.coverage_min` on every touched
file, applies the Boy-Scout rule to files it touches, and updates the
architecture docs so the diagrams stay true. It advances the pipeline through
`red`, `green`, `refactor` and stops at `reviewing`; it does not commit.

It **stops early** with a `STOP` report rather than guessing when the task
needs a UI design, an ADR-worthy decision, a cross-cutting refactor you must
scope, or a schema migration you must confirm.

### compliance-reviewer — review, commit, land

Reviews the session's declared files against the project's compliance
checklist and the engine's rules, resolves what it finds, builds, runs the
suite and coverage, writes a risk assessment, and commits. Then it asks the
engine whether the session's plan is complete:

- **open deliverables** → `VERDICT: INCREMENT COMMITTED`. The commit is on
  the session branch and the session stays open. This is a success and the
  designed resting state; do not re-invoke it "to finish".
- **plan complete (or no plan)** → it runs `session review`, `session merge`,
  and `session teardown`, and the work is on the base branch.

It never pushes unless `session.push` is on, and it reviews only the files
you name — see below.

### backlog-curator — ideas and bugs

Classifies what you said as an idea or a bug, interviews you one round at a
time for the fields the entry needs, checks the backlog for duplicates, links
the relevant architecture doc, scores it (value × effort for ideas, severity ×
priority for bugs) and writes it through `invariant item new`. Work items are
data, not code: the entry is committed immediately on the base branch, outside
any session.

### bug-reproducer — prove it before fixing it

Takes a filed bug, writes **one** test that fails on the reported symptom,
runs it, and returns exactly one verdict: `RED-CONFIRMED` (commits the failing
test and moves the item `filed → reproduced`), `ALREADY-FIXED` (names the
commit), `HARNESS-BLIND` (the test cannot see the surface — the attempt is at
fault, not the bug) or `DEVICE-ONLY` (real, but not expressible in the test
target). It never fixes the bug. The work-item machine has no edge from
`filed` to `fixed`, so this agent is how a bug becomes fixable at all.

## What they need from you

Each agent's description carries a *parent-agent instruction* — what the
calling Claude session must do around it. Two of them matter to you directly:

1. **Reports are pasted verbatim.** Every agent ends with a fenced report
   block, and the parent is told to paste it into the chat before any summary.
   If you are reading a paraphrase, something upstream ignored the instruction.
2. **The reviewer is told what it owns.** The parent passes `SESSION FILES:`
   (the files edited in this session) and `DELIVERABLES DONE: 1, 3` (plan
   entry numbers from `invariant session plan show`). Name no deliverables and
   it marks none — it will not guess which line of a checklist a diff
   discharged.

## How the engine sees them

Two pipeline states carry a role: the design state is owned by
`agents.roles.design` and the review state by `agents.roles.review`. When
Claude Code dispatches a subagent, the `PostToolUse` hook records which one
against the current pipeline, and `agents.design_agent_ran` /
`agents.review_agent_ran` check for it when the state is left. Both are
advisory in every shipped profile — set them to `block` once the project has
decided that coding always goes through the agents.

## Keeping them current, and making them yours

Library agents are **managed** files: the frontmatter carries a digest of
what was shipped, so `invariant doctor` can tell three states apart —
current, installed from an older release, or edited by you.

```bash
invariant agents list                      # installed? loadable? which role?
invariant agents install                   # rewrite stale copies; skip and NAME edited ones
invariant agents install --overwrite-local # take the shipped version back
```

An agent you have edited is yours: no install flattens it without
`--overwrite-local`, and `doctor` reports it as *edited locally* rather than
as broken. For agents that are entirely your own, list them under
`agents.local` in the manifest and the library leaves them alone; to make one
answer for a pipeline role, name it in `agents.roles`.

```yaml
agents:
  local: [api-reviewer]
  roles:
    design: [feature-architect]
    review: [compliance-reviewer, api-reviewer]
```

The same mechanism covers Claude Code commands and skills
(`invariant assets list`, `invariant assets install --kind command|skill`),
should a future release or your project ship any.
