---
id: IDEA_042
type: idea
title: Every session-scoped command takes the session as an argument
status: done
residue: []
history:
- from: proposed
  to: accepted
  reason: null
  forced: false
- from: accepted
  to: in_progress
  reason: null
  forced: false
- from: in_progress
  to: done
  reason: null
  forced: false
priority: high
effort: M
tier: '1'
area: cli/main, domains/session, oracle/recommend, hosts/claude_code
---

**Context:** reported by the user on 2026-11-06. When a pipeline or an agent has to name its session to an `invariant` command, it writes `INVARIANT_SESSION_ID=<id> invariant <command>`. A Claude Code permission rule matches a command by its prefix, so a line that starts with an environment assignment is not covered by `Bash(invariant:*)`, and every such call raises a permission prompt. The user wants to grant `Bash(invariant:*)` once and stop being asked.

The prefix form is not an agent habit; the engine recommends it, because several session-scoped commands cannot name their session any other way. Measured from `--help` at 5900105:

* No `--id`: `pipeline start`, `pipeline test`, `pipeline gates`, `pipeline advance`, `pipeline status`, `check`, and the session-resolving `guidelines` subcommands (`verdict`, `verdicts`, `baseline`).
* Has `--id`: `pipeline coverage`, `session plan show`, `session files add`, `session status`, `session isolate`, `session merge`, `session teardown`, `session transition`, `session sync`, `session handoff`, `context`, `standdown`, `override`.

Three places print the prefix form as advice:

* `domains/session.py`, the ambiguity message (near line 2165), lists `INVARIANT_SESSION_ID=<id> invariant <command>` next to `invariant <command> --id <id>`.
* `oracle/recommend.py`, module docstring (near line 23): where a command cannot take `--id` (`pipeline gates`, `pipeline test`), the recommendation carries `INVARIANT_SESSION_ID` in `env`.
* `hosts/claude_code.py`, the `_UNPROVEN_DISPATCH` hint (near line 2067), ends "or set INVARIANT_SESSION_ID".

**Proposal:** every command that resolves a session takes the session as an argument, with one spelling, and the engine recommends only that form.

* One flag on every session-resolving command. `--id` is already the spelling on 13 commands and the one `cli.md` documents, so it is the default choice unless the design settles on a global option.
* The argument outranks the environment variable, and the variable outranks working-directory and host-binding resolution, as today. If both are given and they disagree, the command refuses and names both, rather than picking one silently.
* `INVARIANT_SESSION_ID` stays a working fallback, so existing scripts and pipelines keep their behaviour. No hint, refusal, recommendation or shipped agent instruction prints it as a command line to copy.
* The ambiguity message, the oracle's recommendations and the dispatch hint name the argument form. The oracle's `env` route for `pipeline gates` and `pipeline test` goes away once both take `--id`.
* A test walks the CLI parser and fails when a command that resolves a session lacks the flag, so a new command cannot reopen the gap.

**Acceptance:**

* Every command that resolves a session accepts `--id <id>` and acts on that session from a directory that would otherwise resolve to another session or to none.
* No message, oracle recommendation or installed agent text contains a line beginning `INVARIANT_SESSION_ID=`. A grep of the shipped sources and assets for that string finds only the fallback's own resolution code and its documentation.
* A command run with `INVARIANT_SESSION_ID` exported and no `--id` behaves exactly as it does today.
* `--id A` with `INVARIANT_SESSION_ID=B` exported is refused with a message naming both, and records nothing.
* With only `Bash(invariant:*)` allowed in Claude Code, a full session (start, isolate, pipeline start, test, gates, advance, merge, teardown) driven by the shipped agents raises no permission prompt for an `invariant` call.

**Architecture sketch:**

* `cli/main` parser: the flag on the missing subcommands, or one shared parent parser or global option that every session-resolving subcommand inherits. A shared parent is what makes the parser-walk test possible.
* `domains/session` resolution already takes an explicit id. The change is passing it through, adding the conflict refusal, and rewriting the ambiguity message.
* `oracle/recommend`: steps carry `--id` in the argv instead of `env`. `oracle-panel.md` and `oracle.md` describe what a step carries.
* `hosts/claude_code`: rewrite `_UNPROVEN_DISPATCH`.
* `docs/architecture/cli.md` (Conventions): state the rule once, that every session-resolving command takes `--id` and the variable is a fallback.

**Risks / open questions:**

* Flag shape: per-subcommand `--id`, or a global `invariant --id <id> <command>`. The global form also matches `Bash(invariant:*)`, but argparse places globals before the subcommand, and existing `--id` flags would then exist in two places.
* Which commands count as session-resolving. `check` and the `guidelines` subcommands resolve from the working directory; the audit has to list each one rather than infer from the name.
* `_UNPROVEN_DISPATCH` is about a hook, which per BUG_003 never sees a variable put in front of a tool call. "Set INVARIANT_SESSION_ID" there may already be advice nobody can follow, so the fix may be to drop it rather than translate it into `--id`.
* Whether the recommended form should be configurable, given the preference for configurable behaviour with safe defaults. Proposed default: recommend `--id`, keep honouring the variable, and add no setting unless a host is found that needs the prefix form.
* `--id` is also a work-item and rule id in some commands (`--rule ID`, `item` ids). The flag's help text should say "session id" so the two are not confused.

**Why now / why this tier:** every pipeline step that has to name its session costs the user a permission prompt today, in every session and every project, and the fix is mostly mechanical in one layer (CLI flags, three message sites, one doc section). High value, effort M, so Tier 1. It also removes the hazard BUG_035 had to defend against (an exported variable inherited by a child suite), because an argument is not inherited.

**Related:** BUG_003 (the variable does not reach a hook; the ambiguity message was reworded to name `--id` first), df464a8 (the earlier `--id` dead-end fix BUG_003 cites), BUG_035 (an exported `INVARIANT_SESSION_ID` leaked into the spawned suite), BUG_028 (dispatch attribution; source of the `_UNPROVEN_DISPATCH` hint), BUG_023 (another session-identity route issue, unrelated mechanism), BUG_093 (oracle re-run steps carry discovery values in `env`; those are not session ids and stay), cli.md ("Conventions", "Session", "Pipeline"), oracle.md.
