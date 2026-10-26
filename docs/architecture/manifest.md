# The manifest

`.claude/invariant.yaml` is the only project-aware artifact. Everything the
library does that depends on *this* project is resolved from here or from an
adapter named here. If something about a project cannot be expressed in the
manifest, that is a gap in the library, not a reason to edit installed files.

## Full schema

```yaml
version: 1

project:
  name: example
  ecosystems: [swift]          # selects adapters; first is primary

paths:
  root: .                      # repo root, relative to the manifest's parent's parent
  state: .claude/invariant/state
  docs: docs/architecture      # the docs-first gate reads these
  memory: .claude/memory
  memory_index: MEMORY.md
  reports: docs/reports        # where `stats report --write` puts its artifact
  sweeps: docs/sweeps          # where `sweep run` commits one record per sweep

commands:                      # adapters supply defaults; these override
  build: null
  test: null
  test_one: null               # {selector} is substituted
  lint: null
  coverage: null                 # runs the suite; {result_bundle} substituted
  coverage_report: null          # optional: reads the measurement, separately
                                 # — a shell reports only the LAST command's
                                 # status, so the two must not be one command
  coverage_one: null             # scoped coverage, {selector} substituted: the
                                 # sibling of test_one, run by `pipeline
                                 # coverage --selector` (ADR-010)

docs:
  format: markdown             # markdown | sphinx
  diagrams: mermaid            # mermaid | plantuml | none
  index: docs/index.md
  require_diagram_update: true

methodology:
  pipeline: tdd                # id of a shipped or project-local FSM file
  extra_machines: []           # project-local FSM yaml paths
  gates:
    coverage_min: 90
    coverage_scope: touched    # touched | affected | all
    coverage_exclude: []       # globs held out of the FLOOR, not the measurement:
                               # the row is still required and the number still
                               # reported, and the verdict names every path it set
                               # aside. Empty by default — excluding anything from
                               # a floor is a policy decision, and it belongs in a
                               # diff someone can review.
    non_source: []             # globs the project declares are NOT code. A
                               # different question from coverage_exclude, and the
                               # pair is easy to confuse: that one means "measured,
                               # but not held to the floor"; this one means "no run
                               # in this project could produce a row for it at
                               # all", and is applied where the built-in prose and
                               # config suffixes are — before the missing-row
                               # check, not after it. `docs.updated` honours it
                               # too: a non_source path is not "source changed"
                               # there, and is named the same way. For versioned design
                               # bundles, fixtures, vendored assets. Every match is
                               # named in the verdict with the pattern that spent
                               # it, because this is the project asserting
                               # something the engine cannot check.
    coverage_baseline: null    # path to a recorded per-file shortfall the floor is
                               # RATCHETED against. One entry per line,
                               # `<repo-relative-path> <pct>`, `#` comments and
                               # blank lines ignored. Below the floor and at or
                               # above its entry does not block; below its entry is
                               # a regression and does; no entry fails exactly as
                               # before. A line that will not parse, an entry
                               # recorded twice, and a file this key names but no
                               # root holds are all ERRORS — a record read in part
                               # drops entries in both directions invisibly. Null
                               # by default.
    coverage_advisory_paths: []  # globs where the floor is ADVISORY. A touched
                               # file under one never fails the gate; the verdict
                               # names it with its figure, flagged when it is under
                               # the floor, or as "not measured" — never an
                               # invented number. Everything else is held exactly
                               # as before. A change touching ONLY advisory paths
                               # passes with no coverage run, and its message says
                               # so: the count, the key, and that a coverage run is
                               # still owed before the list is removed. `doctor`
                               # names a configured list as an ACTIVE EXCEPTION.
                               # For a phase that lands new, flag-off code on
                               # scoped suites; ending the phase is deleting the
                               # list. Empty by default.
    schedule:                  # a sweep cadence (ADR-010). See "A sweep cadence"
      cadence_days: null       # null: none, today's behaviour exactly. A positive
                               # whole number: the gates below are held by a
                               # scheduled full run instead of by each change
      grace_days: 3            # how late a sweep may be before it is called stale
      gates: []                # coverage.meets_floor and/or test.all_pass
      held_paths: []           # globs whose touch still owes a FULL run before
                               # merge. Empty by default

enforcement:
  profile: standard            # strict | standard | loose
  rules:                       # per-rule overrides on top of the profile
    session.own_file_set_only: block
    memory.index_current: warn
    privacy.clean: ask         # block | ask | warn | off — see "Levels" below

session:
  exempt_paths: [".claude/*", ".claude/**", "MEMORY.md"]
  worktree_prefix: null        # null derives it from the project directory name
  worktree_dir: null           # null puts worktrees beside the repository;
                               # a relative value resolves against the repo root,
                               # so worktrees may live INSIDE it
  push: false                  # is publishing to a remote a session step at all
  plan_stale_days: 7           # how long an OPEN deliverables plan may sit without
                               # moving before the closeout gate treats the branch as
                               # stranded rather than parked. A session that spans
                               # several agent-host sessions rests with commits on its
                               # branch BY DESIGN, so unmerged work stops being
                               # evidence of anything on its own; what still separates
                               # the two cases is whether the plan is being worked.
                               # Seven days, because the distinction being drawn is
                               # "paused over a weekend" against "abandoned".

items:
  backend: markdown            # markdown | sphinx_needs | json | none
  path: docs/backlog.md
  id_prefix: ""                # an ADDITIONAL prefix; the type supplies its own
  id_strategy: numeric         # numeric -> BUG_001 | slug -> BUG_DRAIN_STALLS
  closed_path: null            # where retired entries live, in the backend's own
                               # format (a file; a directory for markdown). Set,
                               # minting and an explicit id's uniqueness check
                               # read it too; declared but absent is an error.
                               # Read-only today — see "Backends for work items"
  exempt: true                 # work items are data; exempt from isolation

memory:
  managed: true
  index_generated: true

privacy:
  enabled: true
  # The default set is DEFAULT_PATTERNS in core/privacy — the ONE authoritative
  # list, shared with a bare PrivacyScanner() so the two construction paths
  # cannot disagree (AUD-15). `device_ids` is implemented but opt-in: its hex
  # shapes collide with git commit hashes, which documents cite routinely.
  patterns: [home_paths, emails, ip_addresses, tokens, clock_times, links]
  extra: []                    # project-specific regexes
  allow: []                    # literal strings exempted
  # Keywords resolve through paths.*; any other entry is a project-relative
  # path prefix. See "Privacy scope" below.
  scope: [docs, memory, reports, commit_messages]

agents:
  library: [feature-architect, compliance-reviewer, backlog-curator, bug-reproducer]
  local: []                    # project-owned agents the library leaves alone
  roles:                       # which agent answers for which pipeline role
    design: [feature-architect]
    review: [compliance-reviewer]

host:
  kind: claude_code
```

### A sweep cadence

A consuming project ruled that a change never runs the whole test suite or
whole-scheme coverage: the full suite runs once a week, and whatever it finds
becomes a work item. `methodology.gates.schedule` is how a project says so
(ADR-010). With `cadence_days` set, the gates named in `gates` stop demanding
whole-run evidence per change:

| Gate | Per change, when scheduled |
|---|---|
| `coverage.meets_floor` | Every touched source file outside `held_paths` is ADVISORY: named with its scoped figure, "not measured" or "no coverage row", never failing. A scoped figure is a lower bound (one file read 65% scoped against 97% whole-scheme), so holding it to the floor would block on a number known to read low. A change with nothing held needs no coverage run at all. |
| `test.all_pass` | The same logic over the last recorded run, which is now a scoped one. A red scoped run still fails: it is a real red, not an under-reading. A green one passes, saying the whole suite is on the cadence. |

`held_paths` is the exception a project keeps for itself. A touched file under
one of its globs is held exactly as before, and its evidence must be a FULL
run: `coverage.meets_floor` fails on a scoped coverage run for it, and a
scheduled `test.all_pass` fails on a scoped green. A run record says which it
was — `selectors` and `scope` on every `test_runs` entry and on
`coverage_run` — and a record from before the field existed is not taken as
proof of a full run.

The advisory files go through the one split `coverage_advisory_paths` uses
(`core.rules._split_advisory`), each carrying its reason, so the two ways of
making the floor advisory cannot drift. `sweep.no_full_run` (`warn` in
`standard` and `loose`, `block` in `strict`) reports a full run made on a
change anyway, at `pipeline test` and `pipeline coverage` without a selector
and at the hook on a shell call; a change touching a held path owes one, and
is not reported. `invariant sweep run` holds the gates: it runs the whole
suite on the cadence and files what is new (cli.md, "Sweep"); its records are
committed under `paths.sweeps`. The freshness rule, `sweep.fresh`, is still to
come.

### Levels, and where each one can be delivered

A level is **how a verdict is delivered, not a property of the rule's logic**.
That is what makes D10 and D11 compose: a project retunes policy without
touching code.

| Level | At `pre-tool-use` | At `stop`, at a transition, in `invariant check` |
|---|---|---|
| `block` | the call is denied | work stops |
| `ask` | the call goes to the **human**, with the verdict as its reason | work stops |
| `warn` | the verdict is injected as context; the call proceeds | reported; nothing stops |
| `off` | the rule does not run | the rule does not run |

`ask` is the newest and the one with a seam worth stating. The agent host's
permission contract has always carried an *ask* decision alongside allow and
deny — it routes the call to the operator with the reason attached — and this
library only ever emitted `deny`, so a rule the project wanted to be *asked*
about had two settings, "stop the work" and "say nothing" (AUTH-01).

Its second row is the important one. **An `ask` delivered where there is
nobody to ask stops the work.** The `stop` hook's output is a decision the host
acts on, not a question it can put to anyone; a transition gate is evaluated by
a CLI command that may be running unattended. Passing there would mean a level
whose enforcement silently depended on which surface happened to evaluate it,
and this library's standing asymmetry decides that case: a spurious block is
visible and self-correcting, a silent pass is neither. What reaches those
surfaces instead is the override token below.

### The override token, and why it is a grant rather than a flag

Every gate the CLI evaluates can be passed deliberately — `session transition
--force --reason`, `pipeline advance --force --reason`, `item transition
--force --reason` — and each appends an override to the record. For a long time
the gates that actually stop an agent could not be: seven rules fire at
`pre-tool-use` and five at `stop`, and a blocking verdict there was final. No
flag, no marker, no prompt, no recorded exception. The only relief was editing
committed project policy, mid-task, to get past a single call (BUG_049).

```
invariant override <rule.id> --reason '<why>' [--calls N]
invariant override <rule.id> --clear
```

It is a **grant written down before the call**, and it has to be, because a
tool call is not a command the operator types: it carries no argument they can
add, and no environment a running host will re-read — a hook subprocess
inherits the environment the host was launched with, which is why
`INVARIANT_DISABLE` cannot reach one mid-session.

```mermaid
graph TD
    V["a verdict that stops the call"] --> A{"level"}
    A -- "ask, at pre-tool-use" --> H["the human decides, in band"]
    A -- "ask, anywhere else" --> S["work stops"]
    A -- block --> G{"an unspent grant<br/>for this rule?"}
    S --> G
    G -- no --> D["denied — the verdict is the reason"]
    G -- yes --> P["one call passes; the grant is spent"]
    P --> R["appended to session.overrides:<br/>rule, reason, surface"]
```

Four properties are deliberate:

- **Spending it is recorded**, in the same `overrides` list a forced transition
  writes to, naming the rule, the reason and the surface. An override that
  leaves no trace is indistinguishable from the gate never having run.
- **It is spent only where the verdict actually stops something.** A token
  burnt by a rule that was passing anyway would be gone at the moment the
  operator needed it.
- **It is consumed at the delivery surface, never inside `core/rules`.** A rule
  is a question about the world, and asking it must not change the world.
- **An unspent grant is loud** — `session status` prints it above the fold,
  because a held override is enforcement the operator turned off and may well
  have forgotten.

`ask` and the token are not alternatives. `ask` puts the human in the loop at
`pre-tool-use` and reaches nothing else; the token reaches `stop` too and
leaves a record, which a host-side approval does not — a human approval arrives
back through the host and is not observable to this library at all.

### Declared stores are writable stores

`items.path`, `paths.memory`, `paths.reports` and `paths.sweeps` are not merely locations —
declaring one is what makes the CLI's write-scope invariant cover it, and what
exempts it from session isolation. `cli.md` states the invariant as "the state
root and the stores the manifest declares" (ADR-002 D4) rather than as a list,
so a feature that needs somewhere to write adds a key here instead of adding
an exception there.

The consequence to be aware of: `paths.reports` defaults to `docs/reports`, so
a project that already keeps hand-written prose at that path would find it
exempt from isolation. Point the key somewhere else if so — the exemption
follows the declaration, which is the whole point. `paths.sweeps` defaults to
`docs/sweeps` on the same terms.

### A declared path means the same thing however it is spelled

The exemption follows the declaration, not the declaration's spelling. A
manifest path is used two ways, and only one of them used to be safe:

- **Joined onto the root** to find a store on disk (`path_for`, the item
  backends). Joining and resolving accepts every spelling, so this half was
  never affected.
- **Compared with a target** to decide whether that target is exempt, in
  scope, or held out of a gate. The target's side of that comparison is its
  project-relative spelling (`_project_relative_forms`), which never starts
  with `./`, never ends with `/` and never carries an empty or `..` segment.
  The declaration's side was compared as written. So `items.path:
  ./docs/backlog` exempted nothing: every write to the work-item store in the
  main checkout was refused by an isolation rule that named neither the path
  nor its spelling (BUG_071).

Every comparison now goes through one normaliser, `project_relative` in
`core/manifest`, applied to both declared stores and declared globs:

```mermaid
graph TD
    D["a declared path or glob"] --> A{"absolute?"}
    A -- no --> N["normalise: drop ./ and a trailing /,<br/>collapse //, resolve .. lexically"]
    A -- yes --> B{"under one of the project's checkouts?<br/>(manifest root, git top level, main checkout)"}
    B -- yes --> R["spell it relative to that checkout"] --> N
    B -- no --> X["names nothing in the project:<br/>DROPPED, matches nothing"]
    N --> C{"climbs out with ..,<br/>or is the root itself?"}
    C -- "climbs out" --> X
    C -- "the root" --> X
    C -- no --> M["compared with the target's<br/>project-relative spelling"]
```

| Declaration | Where it is compared | `x` | `./x` | `x/` |
|---|---|---|---|---|
| `items.path` | `_is_exempt` (isolation, push barrier, a session's first edit) | exempt | **was not exempt**; now exempt | exempt (a trailing `/` was already stripped) |
| `paths.reports` | `_is_exempt` | exempt | **was not exempt**; now exempt | exempt |
| `session.exempt_paths` | `_is_exempt` | exempt | **was not exempt**; now exempt | **was not exempt**; now exempt |
| `methodology.gates.non_source` | `coverage.meets_floor`, `docs.updated` | held out | **was not held out**; now held out | **was not held out**; now held out |
| `methodology.gates.coverage_exclude` | `coverage.meets_floor` | held out | **was not held out**; now held out | **was not held out**; now held out |
| `methodology.gates.coverage_advisory_paths` | `coverage.meets_floor`, `doctor` | advisory | advisory (normalised from the start) | advisory |
| `methodology.gates.schedule.held_paths` | `coverage.meets_floor`, `test.all_pass`, `sweep.no_full_run` | held | held (normalised from the start) | held |
| `privacy.scope` entries and the `paths.*` its keywords name | `privacy.clean` | in scope | in scope (`Path()` already absorbed it) | in scope |
| `paths.docs` | `docs.read_architecture`, `docs.updated` | every tree | every tree (resolved on join) | every tree |
| `coverage_baseline` entries | `coverage.meets_floor` | matched | matched (`_same_file`); `x` and `./x` together are now refused as one file recorded twice | matched |

The spellings the first rows' columns do not show broke the same way and are
fixed by the same change: `x//y` and `x/z/../y` everywhere, and an absolute
in-project path in `privacy.scope`, in the stores and globs, and in `paths.docs`
read from a linked worktree, where the manifest root is the worktree and the
old relativisation answered "outside the project" for a path naming the main
checkout.

**Normalised, not refused at load.** Refusing would have turned a spelling
that already worked — a trailing `/` on `items.path` — into a manifest that no
longer loads, for every command and every hook, and a spelling the library
can read unambiguously is not an error the operator should be made to fix.
Refusal is the right answer only where reading is ambiguous, and nothing here
is.

**Nothing outside the project is exempted, scoped or excluded.** An absolute
path under none of the project's checkouts, or a relative one whose `..`
climbs out, is dropped from the comparison rather than matched against
anything. It used to be compared with the absolute spelling a path outside
every checkout falls back to, so `session.exempt_paths: ["/some/parent/**"]`
answered "exempt" for files in unrelated directories. Such a declaration can
still name a STORE outside the project — joining it onto the root still finds
it — and edits there are outside this project's isolation rules anyway
(`_is_outside_project`). **The project root itself is dropped too**: a store
declared as `.` used to exempt nothing, and normalising it must not quietly
make it exempt everything.

`paths.memory` is listed above among the declared stores, but `_is_exempt`
does not add it the way it adds `items.path` and `paths.reports`: the default,
`.claude/memory`, is exempt through `session.exempt_paths`' `.claude/*`, and a
memory store declared anywhere else is not exempt today. That is a gap between
this section and the code, recorded rather than closed here, because closing
it widens an exemption and is its own decision.

### Privacy scope

`privacy.scope` is a list of two kinds of entry:

- **Keywords**, which resolve through `paths.*` so the scope follows the
  declaration: `docs` → `paths.docs`; `memory` → `paths.memory` **and**
  `paths.memory_index` (the generated index is a concentrate of exactly the
  text the memory scope protects, and was the unscanned second copy —
  AUD-15); `reports` → `paths.reports` (the one rendering of the raw stats
  store that leaves the machine); `commit_messages`, which scans `git commit`
  messages in every spelling git accepts — `-m`, `--message[=]`, combined
  clusters like `-am`, and `-F <file>` by reading the file. A message the
  gate is configured to scan and cannot read (`-F` on an absent file, `-F -`
  from stdin) FAILS rather than passing unread, in the same do-not-relax
  convention as the git helpers (AUD-09). When the same command writes the
  `-F` file — a `>`, `>>` or `>|` redirect (a heredoc-fed `cat >` included)
  or a `tee [-a]` argument landing on the path git would read — the refusal
  names that cause: the hook runs before the command, so the file cannot
  exist yet, and the remedy is to write it in a separate call first, or use
  `-m`. Any write it does not recognise keeps the generic wording (BUG_067);
  `all`, which scans everything.
- **Literal project-relative path prefixes** — any entry that is not a
  keyword. This is how a project widens scope beyond what `paths.*` names:
  a repository whose `paths.docs` is `docs/architecture` can declare `docs/`
  so its audits and ADRs — committed prose where a leak is just as permanent —
  are scanned too (AUD-15). This library's own manifest does exactly that.

The gate reads every payload shape the edit tools produce: `content`
(Write), `new_string` (Edit), `edits[].new_string` (MultiEdit) and
`new_source` (NotebookEdit). The last two were unscanned while the isolation
rules fired on all four tools, so the same data that blocked a `Write` passed
through a `MultiEdit` untouched (AUD-06).

### Where the gate screens, and why it is not only the edit tools

Reading edit payloads can only ever cover writes that arrive as an edit
payload. A write made through the shell does not: a `cat` heredoc, a `sed -i`,
a `cp`, or a generator that emits a document all carry their content in a
`Bash` command, land in the same file, and used to reach history with the gate
reporting *"nothing in privacy scope was written here"* — a sentence that is
affirmatively false on that route, and indistinguishable from a pass. In one
consuming project whose harness edits Bash-first, that was **every** edit of a
long session (BUG_027).

So the gate screens at a second point, where every route converges:

```mermaid
graph TD
    W["a write"] --> E{"did an edit tool<br/>announce it?"}
    E -- yes --> P["scan the payload<br/>against privacy.scope"]
    E -- "no — shell, generator,<br/>a route nobody enumerated" --> U["not screened yet,<br/>and the verdict says so"]
    U --> C{"git commit"}
    P --> C
    C --> S["scan what is STAGED,<br/>path by path, against privacy.scope"]
    S --> H["history"]
    H --> V{"the review boundary"}
    U --> V
    V --> B["scan what this branch would put into its base<br/>— its commits, its index, its working tree"]
    B --> M["the base branch"]
```

The commit is the boundary that makes the answer route-independent: nothing in
a staged blob records which tool produced it, so nothing in the gate has to
enumerate them. Five properties of that scan are deliberate:

- **The whole staged blob, not the diff's added lines.** An exemption marker
  reaches a *block* (BUG_045), so a marker on an unchanged line above an added
  one is invisible to a scan of the addition alone — and the gate would block
  on a finding the project had already exempted, at a spot the author cannot
  see in the diff.
- **`privacy.scope` decides which staged paths are read**, by the same
  project-relative prefix rules as an edit's target. A commit typed in a
  subdirectory names the same blobs, because staged paths are spelled against
  the repository toplevel — but a commit that *redirects git* does not, and
  the two look alike until you ask which index. See below.
- **A staged set or a staged blob that cannot be read FAILS**, in the same
  do-not-relax convention as `-F` on an unreadable message (AUD-09). This is
  the one gate whose subject cannot be recovered after the fact.
- **A deleted path is not an unreadable one.** Deletions are excluded from the
  list before any blob is read, so removing a document is not a fail-closed.
- **Bytes that do not decode carry nothing that can leak in text form** — the
  same answer `PrivacyScanner.scan_file` and `invariant privacy scan` already
  give a binary file. A commit containing a diagram is not a gate no change
  can clear.

#### Which index — a subdirectory and a `-C` are not the same case

A commit typed in a subdirectory and a commit typed as `git -C <other> commit`
both run somewhere other than where the hook is standing, and only the first
one names the same blobs. `-C` changes git's working directory for the invoked
command, so it names a *different repository's index*; the gate read `ctx.cwd`'s
and vouched for the commit anyway, reporting a scan of blobs with no relation
to what was landing (BUG_048). The message half had the smaller version of the
same blindness: a relative `-F` name resolved against the wrong directory.

So the repository is resolved from the command before either half runs:

```mermaid
graph TD
    K["git … commit"] --> R{"does the command<br/>redirect git?"}
    R -- "no" --> W["the call's own directory"]
    R -- "-C, repeatable<br/>and cumulative" --> D["resolve each against<br/>the one before"]
    R -- "--git-dir / --work-tree" --> F["FAIL, naming the option<br/>it will not follow"]
    D --> O{"inside this project?"}
    W --> S["scan that index"]
    O -- yes --> S
    O -- "no — an unrelated checkout" --> A["scan the MESSAGE;<br/>say the content was not"]
```

Three properties of that resolution are deliberate:

- **`-C` is repeatable and its paths are cumulative**, each resolved against
  the one before, so `git -C a -C b` runs in `a/b`. A reader that took the
  first and stopped would land one directory up — usually a real directory
  with a real index, which makes the mistake a confident scan rather than an
  error anyone notices.
- **`--git-dir` and `--work-tree` are not followed, and say so.** Resolving
  them means reproducing git's own gitdir/worktree pairing, and a half-right
  resolution reports a scan of a tree nobody asked about — the failure being
  fixed. Naming the option and failing closed is the honest floor (AUD-09).
- **A commit into a repository outside this project abstains rather than
  refusing.** `privacy.scope` is spelled project-relative, so it describes no
  path in an unrelated checkout and a scan there would report a scan of
  nothing. Refusing instead is what makes a project turn the rule off — which
  turns it off for the project it was meant to govern (`_is_outside_project`).
  The commit *message* is still scanned, and the verdict states that the
  content was not: a pass naming one route and silent about the other is the
  reading the bug was filed against.

The skip sentences were rewritten in the same change, because the false one is
what let the gap survive a long session unnoticed. Each route now states the
limit that actually applies to it: an edit outside scope still says nothing in
scope was written; a commit says no staged path is inside scope; a shell call
that is not a commit says it is not read as it runs and that what it writes is
screened when it is committed. A skip and a pass are different facts, and a
skip that misstates its own reason is the harder of the two to notice
(BUG_020).

### The third point: the review boundary

`privacy.clean` is named by six machines at that boundary — as `session`'s
exit gate on `reviewed`, and as the entry gate of the landing state
`committed` in `tdd`, `bugfix`, `spec-first`, `docs` and `chore` — and for a
long time it screened nothing in any of them. Which side of the edge it sits
on moved with BUG_037 and does not matter here: both are evaluated on a
TRANSITION, and a transition evaluates a rule with an **empty payload** — no
tool name, no edit, no command that could be a commit. Every route the gate
had needed a payload a transition does not have, so the gate that the review
boundary advertises as its privacy check had never read a byte there
(BUG_047). Its abstention was at
least *visible* by then — a block-level skip renders as `[skip:BLOCK]` and is
counted apart (BUG_020) — but visible inertness is still inertness.

A review asks a different question from a commit, which is why it needs its own
subject rather than the commit's. A commit is one act with one index; a review
is a claim about a whole branch, and by the time the claim is made the work is
committed by construction — so the index, the only thing the commit route can
see, is normally empty there. The subject is therefore **everything this
session would put into its base branch**:

- the branch's own commits against its base (`git.branch_files`),
- the index and the working tree, for what is not committed yet,
- and whatever file set the session declared.

That union is `_changed_set`, the same one `docs.updated`,
`docs.diagrams_current` and `coverage.meets_floor` already read at their own
end-of-phase gates. Reading it through the one helper is the point rather than
an economy: BUG_012 was a single insight implemented in one rule and not
propagated to the three beside it, and a fifth spelling of the same query is
how that recurs. It carries the refusal with it — when no base is recorded,
nothing was declared, and the branch's commits cannot be read, the changed set
is *unestablished* and the gate FAILS rather than skipping, because an
unanswered question is not a negative answer.

Three limits are deliberate, and stated because a control's edges are the part
a reader has to be able to see. Content is read from the **working tree**,
which is what the merge publishes; a path the branch added and later removed
still sits in the branch's history and is not re-read here, having been
screened at the commit that added it — the boundary that could still have
refused it. A path that no longer exists is skipped rather than failed closed,
so a review that deleted a document is not a gate nobody can clear. And a file
that is **untracked** — never staged, never committed — is in none of the three
sources, which is correct rather than a hole: nothing untracked goes into the
base branch, and the moment it is added the index carries it here and the
commit boundary screens it.

**Deliberately unchanged:** `session.isolated_before_edit`,
`session.no_edit_in_main_checkout` and `pipeline.started_before_edit` share the
same "is this an edit tool" predicate and are therefore blind to the same
route. They now *say* so instead of returning a reason-less N/A, but their
enforcement is untouched: teaching them to refuse shell commands is a
behaviour change that could wedge a live session, and it is its own scoped
decision rather than a rider on this one.

### What `links` flags

Of the default patterns, `links` is the one whose concept is easiest to state
too broadly, so it is stated here: **a link is a URI whose authority names a
network host.** The authority is the axis, not the scheme.

```mermaid
graph TD
    U["a candidate URI"] --> A{"is there an authority<br/>between :// and the path?"}
    A -- "no — bare or three-slash form" --> N["not a link"]
    A -- yes --> H{"does that authority<br/>name a network host?"}
    H -- "a dotted name with a<br/>TLD-like suffix" --> L["a link — flagged<br/>whatever the scheme is"]
    H -- "an IP literal, v4 or bracketed v6" --> L
    H -- "a single label with no dot,<br/>or a placeholder" --> N
```

An authority names a network host when it is a dotted name ending in a
TLD-like suffix, or an IP literal — v4, or v6 in brackets. Optional userinfo
before it and a port after it change nothing. That is a finding whatever
scheme carries it, which is the half worth keeping: a private scheme aimed at
a real host leaks exactly as much as a web address does, and narrowing the
pattern to an allowlist of the web's own schemes would have stopped catching
it.

An authority names no network host when it is **absent** — the bare `file://`
form, or the three-slash local-file form — or when it is a single label with
no dot: `applephotos://asset/<lid>`, `scan://<uuid>/document`,
`encrypted-media://local/...`. These are documented identifier *formats*
written with placeholders. They address nothing reachable and can leak
nothing, and every one of them used to be a finding, because the pattern
matched anything before a `://`. On two architecture documents of one
consuming project that came to 26 findings, 20 of them false — which is the
shape the module's own rule warns about: a noisy privacy gate is one that gets
disabled wholesale rather than tuned (BUG_044).

Two consequences of the test being structural, both deliberate:

- A **single-label host** — a loopback name, or a container-network service
  name — is not a finding, with or without a port. Those name the reader's own
  machine or its private network and identify nobody. The rest of that class is
  the accepted residue: a bare intranet hostname parses the same way, and a
  machine name can carry its owner's. It is accepted rather than unnoticed —
  a personal machine reaches a document far more often through its home path,
  which `home_paths` still catches.
- A **reverse-DNS identifier** in the authority position *is* a finding.
  Nothing in the text distinguishes it from a hostname, and where the two are
  indistinguishable this pattern errs noisy rather than silent.

The schemeless `www.` form and the scp-style `user@host:path` a git remote is
written in are separate arms and are unaffected by any of the above.

### What `ip_addresses` flags

`links` is the pattern whose concept is easiest to state too broadly. This one
is the pattern whose concept is easiest to mistake for a *syntax*: **an address,
and not merely four numbers with dots between them.** A dotted quad is a shape
many things wear, and a release plan that numbers its rows four levels deep
wears it exactly — `#10.14.6.1` is four integers, every one at or below 255.
Measured in one consuming project's corpus: 55 matches across three documents,
zero of them addresses (BUG_053).

Nothing was broken in the regex, which is the part worth stating plainly. The
pattern matched what it said it matched. What was wrong was its breadth,
against this module's own standing instruction — *a noisy privacy gate is one
that gets disabled wholesale rather than tuned* — and the report is that
sentence coming true: the project had begun suppressing the false matches with
exemption markers, and because a marker reaches to the end of its block
(BUG_045), 58 lines were exempt from a blocking gate to silence four findings.

Two things settle a candidate locally, and only two:

```mermaid
graph TD
    Q["four dotted octets"] --> L{"is it part of a<br/>LONGER dotted run?"}
    L -- yes --> N["not an address"]
    L -- no --> M{"is a reference marker<br/>written hard against it?"}
    M -- "#, § or ¶ with no space" --> N
    M -- "no marker, or a space after one" --> A["an address"]
```

- **Length.** A run of more than four segments is not an address. `\b` could
  not see this: the dot that follows a longer run *satisfies* a word boundary,
  so a five-segment row number yielded its first four segments as a finding.
  This half is structural and is not a judgement call. The guard is spelled
  around the digit rather than around the dot — reject a trailing `.` only when
  a digit follows it — because a trailing dot with nothing after it is how
  prose ends a sentence, and the other spelling would have cost every address
  written at the end of one.
- **A reference marker, written with no space.** `#`, `§` or `¶` hard against
  the digits marks the number as a row or a section. **Adjacency is the whole
  of the rule**, and it is what keeps this narrow: a marker with a space after
  it opens a heading or a shell comment, either of which may be quoting a real
  address, so a spaced marker leaves the finding standing. The test is anchored
  to the text immediately left of the match rather than searched over the whole
  line, which makes it strictly tighter than the `_TIME_FALSE_ALARM` precedent
  it is modelled on.

Worked both ways, in the documentation ranges RFC 5737 reserves. This block
needs the exemption marker for the reason every example here needs one: a
document about what an address looks like cannot state it without writing one
down, and the must-MATCH column is address-shaped by construction.

<!-- invariant: allow-privacy -->

| written as | verdict | why |
|---|---|---|
| `#10.14.6.1` | not an address | a marker, hard against the digits |
| `#10.14.6.1.2` | not an address | five segments; the marker is not needed |
| `1.2.3.4.5` | not an address | five segments, settled by length alone |
| `192.0.2.10` | an address | nothing local argues otherwise |
| `# 192.0.2.10` | an address | the space makes the `#` a heading, not a number |
| `reachable at 192.0.2.10.` | an address | the trailing dot ends the sentence |
| `192.0.2.444` | not an address | `444` is not an octet |
| `v1.2.3.4` | not an address | held by the boundary before the run |

**The accepted residue, stated rather than discovered later.** A four-segment
row number written BARE — in a table cell, a field name, a section title — is
indistinguishable from an address by any local rule, and this pattern still
flags it. That is the same call `links` makes about a reverse-DNS identifier:
where the text cannot tell two things apart, this gate errs noisy rather than
silent. A project that needs those quiet has `privacy.allow`; teaching the
engine a project-declared row-id shape is a separate decision and is not
folded in here.

**`_IPV4` is shared, and the guards are not.** The octet grammar has one
spelling because two patterns need the same notion of an IP literal — this one,
and `links` asking whether a URI's authority is one. The narrowing lives on the
`ip_addresses` entry alone: under a scheme, the authority position has already
proved the number is an address, and a guard that reached `_IPV4` would have
cost `links` a real class of finding to fix a false one here.

### The exemption marker, and how far it reaches

Every gate needs an escape hatch for the case it gets wrong, and this one is a
string written into the text being scanned: `invariant: allow-privacy`. The
spelling lives in exactly one place — `EXEMPTION_MARKER` in `core/privacy` —
read both by the scanner and by the rule that prints the advice, so the two
cannot come to disagree about it.

**The rule, in one sentence:** a marker exempts its own line; and when nothing
but comment punctuation precedes it on that line, it also exempts the rest of
the block it opens and the whole of the next block, where a block is a run of
consecutive non-blank lines.

```mermaid
graph TD
    M["a line carrying the marker"] --> Q{"anything alphanumeric<br/>to its LEFT?"}
    Q -- yes --> INLINE["INLINE — that line, and no other"]
    Q -- no --> BLOCK["BLOCK — that line,<br/>the rest of its own block,<br/>and the whole of the NEXT block"]
    BLOCK --> STOP["the blank line after the next block<br/>ends the reach; nothing beyond it"]
```

The inline half is the older of the two and is unchanged. It is the right form
in source code, where every language has an inline comment, and it is what the
`allow` list is not: `privacy.allow` exempts a literal string everywhere it
appears, and a marker exempts a *place*.

The block half exists because the inline form is unwritable in the markup this
gate mostly scans. `privacy.scope` defaults to documents, memories and reports
— all prose — and **reStructuredText and Markdown have no inline comment**: a
comment there owns its line. So the only way to exempt a finding in prose was
to append the marker to the prose itself, where it renders into the published
output. One consuming project shipped six of those, visible in its built HTML
as parentheticals, before this was fixed (BUG_045).

**A block and not a line**, because in RST the marker cannot be put next to
what it covers. A comment inserted mid-paragraph *splits the paragraph*; a
comment inserted between list items *ends the list* and starts a second one.
Both change the rendered output, which is the thing the marker exists to leave
alone — so the comment can only go before the whole block, and in practice the
finding is mid-block every time. A one-line reach would exempt nothing that
needs exempting.

**Two blocks and not the rest of the file, or of the section**, because the
reach is the whole risk. A marker is a place a real leak can be parked, so its
cover has to be something a reviewer reading the diff can see the end of. Its
own block is included so that a comment wrapped over two lines covers what
follows it rather than only itself, and the reach runs FORWARD only — a line
above the marker in the same block is still scanned. Blank lines between the
marker and the block it covers are skipped, because RST needs one to close the
comment; what follows them is still exactly one block.

A file can be exempted only by marking every block in it, which is a diff full
of markers rather than a single line nobody notices. That is the intended
trade: the escape hatch is cheap to use honestly and conspicuous to abuse.

Two things the engine deliberately does not know. It does not parse markup, so
whether the marker's line is *really* a comment is the author's problem — a
bullet beginning with the marker is block-scoped and also renders. And a
comment is still source text: Sphinx keeps the marker out of every rendered
page, but a published `_sources` mirror or a machine-readable export of the
directive bodies carries the source verbatim, as it does for the inline form.

### Where worktrees live

`session.worktree_dir` exists for hosts that trust one location and confirm
every other. Claude Code, for one, asks before relocating its permission root
into a worktree outside `.claude/worktrees/`, and that confirmation is
deliberately not suppressible by any permission rule — so a project whose
worktrees sit beside the repository is asked once per session, forever. Pointing
this at the trusted directory ends that, and the trust is decided by path: a
worktree created there by plain `git worktree add` is entered without a prompt.

Placing worktrees inside the repository has one consequence worth stating,
because it fails silently rather than loudly. Exempt patterns are matched with
`fnmatch`, whose `*` spans path separators, so `.claude/*` matches
`.claude/worktrees/wt-1/src/app.py` — every source file in a worktree placed
there. The isolation rule consults the exemption first and skips on a match, so
the result would not be an error but an absence of enforcement. The library
therefore resolves a path inside a linked worktree against **that worktree
alone**, which is the more accurate reading anyway: a file's project-relative
identity is where it sits in its own checkout, not where its checkout sits. A
directory prefix cannot exempt what is underneath it.

The directory should also be ignored by git, or the main checkout is dirtied by
every live session. Claude Code writes `.claude/worktrees/` into
`.git/info/exclude`, which is local to one clone — a project that wants this to
hold on every machine commits the entry to `.gitignore` instead.

Every key has a default. A minimal usable manifest is three lines:

```yaml
version: 1
project: { name: example, ecosystems: [python] }
```

## Validation

`Manifest.validate()` runs on every load and refuses, with a named reason: an
unsupported `version`, an unparseable enforcement level (`block`, `ask`, `warn`
or `off`), an `enforcement.rules` KEY naming no registered rule, a non-list
`privacy.scope`, an unknown `items.backend`, and — because several gates used
to skip, silently and non-blocking, exactly when this was mistyped (AUD-08) —
a `methodology.pipeline` that names no shipped or `extra_machines` machine.
The three glob lists under `methodology.gates` — `coverage_exclude`,
`non_source` and `coverage_advisory_paths` — must each be a list of strings:
every one of them is a relaxation, and a bare string given for one was iterated
character by character downstream, so `"web/**"` contributed a lone `*` that
matches every path in the project.
A sweep cadence is checked too, because each way it can be wrong is a
setting that loads and quietly keeps demanding the full run it was meant to
retire (ADR-010 D3). `schedule` must be a mapping, `cadence_days` null or a
positive whole number, `grace_days` a whole number of 0 or more, `gates` a list
naming only `coverage.meets_floor` or `test.all_pass` (a near miss is named),
and `held_paths` a list of globs. With a cadence, `gates` may not be empty; an
unscheduled `coverage.meets_floor` may not be at `block` or `ask`, since its
only satisfying evidence would still be a whole-scheme run; and a scheduled
floor refuses a `coverage_scope` other than `touched` and a non-empty
`coverage_advisory_paths`. Without one, `gates` and `held_paths` must be empty,
since they would do nothing. Levels are read resolved — profile, overrides and
`INVARIANT_DISABLE` — because that is what the gate would do.
Existence is checked at load; whether the file parses as a machine of the
right *kind* is still the loader's question, and a machine that exists but
cannot load as a pipeline now **fails** the gates that need it
(`pipeline.complete`, `session.work_would_be_stranded`) rather than being
stepped past. The session machine is read the same way and answers the same
way: `session.work_would_be_stranded`, `session.deliverables_complete` and
`session.branch_is_current` each need its terminal set to know whether the
session is still in flight, and each FAILS when it cannot be loaded, because a
skip on a rule a project holds at `block` reads as a pass.

### An override either takes effect or says it did not

The rule ids in `enforcement.rules` are checked against the registry, and a key
naming nothing is a **refusal at load** carrying the nearest real id.

Only the *value* used to be parsed. `rule_level()` answers `warn` for any id it
does not hold, so `privacy_clean: off` written beside a real `privacy.clean`
loaded without error, left the real rule at `block`, and showed a provenance
entry for the typo that read as though the override had taken. `doctor` was
green; `rules list` shows only real rules, so the discarded key appeared
nowhere (BUG_050).

This is the library's own central claim pointed at its own configuration.
*"Non-compliance is silent"* is the failure it exists to remove, and a
discarded override is that failure aimed at the operator instead of at the
agent — in the one place it costs most, because an override is the single
control this library hands back. The instinct was already written down twice:
`core.rules.evaluate` fails loudly on a gate naming a rule that does not exist,
*"A gate naming a rule that does not exist would otherwise pass silently
forever"*, and AUD-08 added exactly this check for a mistyped methodology.

Three properties of the check are deliberate:

- **Refused at load, not warned.** A manifest is read on every command, so a
  warning here is printed hundreds of times and read none of them.
- **`INVARIANT_DISABLE` warns instead, and proceeds.** It is an escape hatch
  reached in a hurry by someone already blocked; refusing to load the whole
  manifest because one token was mistyped would wedge exactly that caller. The
  unknown token is dropped, named on stderr with the nearest real id, and the
  rest of the list applies.
- **`doctor` survives it.** `doctor` is the command someone runs *because* the
  manifest is wrong, and it resolved the manifest before it could report
  anything — so a refusal at load took out the one command that would explain
  it. An unloadable manifest is now a failed `manifest valid` row carrying the
  reason.

One consequence worth naming: editing `methodology.pipeline` mid-pipeline does
**not** retune the pipeline already running. The methodology is recorded on
the pipeline at `start` and the machine is resolved from that record ever
after, so the manifest edit takes effect from the next `pipeline start`
(AUD-02/AUD-03).

## Resolution order

For any value, later wins:

1. Library defaults
2. Primary adapter defaults (from `project.ecosystems[0]`)
3. Additional adapters, in listed order
4. Enforcement profile (for rule levels only)
5. Explicit manifest values
6. Environment overrides (`INVARIANT_*`), for one-off escapes

Layers 5 and 6 are where the operator exercises authority, and they are held to
a different standard than the rest because of it: a rule id either resolves or
is refused by name. See "An override either takes effect or says it did not"
above.

`invariant config show` prints the fully resolved configuration with the
provenance of each value, because "why is this rule blocking me" must be
answerable without reading the library's source.

## Adapters

An adapter is a Python class exposing ecosystem knowledge:

```python
class Adapter:
    id: str
    def detect(self, root: Path) -> bool: ...
    def default_commands(self) -> dict[str, str | None]: ...
    def parse_test_output(self, stdout: str, stderr: str, code: int) -> TestResult: ...
    def parse_coverage(self, raw: str) -> dict[str, float]: ...
    def source_files(self, changed: list[Path]) -> list[Path]: ...
    def carries_executable_code(self, path: Path) -> bool | None: ...
    def expected_failures_are_strict(self, root: Path, selectors) -> bool | None: ...
    def test_scope_of(self, extra_tokens) -> str | None: ...
    def failing_test_ids(self, stdout: str, stderr: str, bundle) -> list[str] | None: ...
    def hazards(self) -> list[Hazard]: ...
```

`test_scope_of` is asked by `sweep.no_full_run` at the hook, about a shell
call led by the project's own `commands.test` or `commands.coverage` template:
are the tokens after it the whole suite (`"full"`), a subset (`"scoped"`), or
"cannot tell" (`None`)? Only `"full"` fires the rule, so it is claimed only on
positive evidence — the base answer is "full" for nothing extra and doubt for
anything else. Python reads pytest's narrowing arguments (a positional, `-k`,
`-m`, `--lf`, `--ff`, `--deselect`) and its known non-narrowing options;
Swift reads `-only-testing:` and doubts `-skip-testing:`; node claims nothing
past the bare script (ADR-010 D5).

`failing_test_ids` is asked by `sweep run`: which tests failed, each named as
a selector `commands.test_one` takes back, so a new red can be re-run alone
and filed under that identity. Python reads pytest's short summary (`FAILED`
and `ERROR` lines); Swift reads the result bundle's failed test cases, else
the console's failed case lines, as `<bundle>/<Class>/<method>`; node and
generic answer `None`, and the sweep files one `suite:red` for a red run
instead of guessing at identities (ADR-010 D5).

`expected_failures_are_strict` is asked only by the red-phase recorder, about
a run that exited clean with expected failures counted (`TestResult.
expected_failures`). `True` lets it record the run as red; only the Python
adapter answers, from a literal `strict=True` on the selected files' `xfail`
marks, and every other adapter says "cannot tell" (ADR-006 D5–D6).

`hazards()` is the interesting one. It returns known ecosystem traps that the
engine surfaces as context or as pre-flight checks — cases where a command's
exit code does not mean what it appears to mean. The `swift_xcode` adapter
ships with several: an exit status that indicates whole test suites were
silently dropped rather than run, a destination argument whose name-based form
resolves to a device that may not exist, and a debug device build whose main
binary carries no symbols so that symbol lookups return empty for every query.

A hazard is not a rule. Rules gate transitions; hazards annotate results so
that a green is not mistaken for proof. `parse_test_output` returning
`passed=True` alongside a hazard flag is how the library says "this looks
green, and here is the specific reason that might be a lie."

`default_commands` has a quieter failure, and it is the one that costs most
because nothing reports it. A default names a tool; it does not guarantee the
tool is installed. The Python adapter defaults `lint` to a bare `ruff check .`,
which needs `ruff` on PATH — where it is absent the command does not fail the
check, it fails to *spawn*, and "the linter could not run" is indistinguishable
in a report from "the linter found nothing". Five consecutive sessions in this
repository recorded lint as NOT RUN and moved on before one of them tried an
invocation that resolved. The node adapter's `npm run lint` has the same shape:
it assumes a script the project may never have defined.

The rule is the one `parse_coverage` states from the other direction — a
reading that can come back clean for the wrong reason is the one to refuse. A
project overrides the default with an invocation that resolves through a
declared, locked dependency rather than through whatever happens to be on PATH;
pinning is part of the fix, because an unpinned linter moves the finding
baseline without a change to a line of source. An unrunnable gate is worse than
an absent one: an absent gate is visibly absent, while an unrunnable one reads
as a pass.

`parse_coverage` has a trap of its own, and it is the quiet kind. A reporter's
table layout belongs to the reporter, so a parser keyed to that layout breaks
whenever the layout legitimately varies — and it breaks by returning `{}`,
which is indistinguishable from "nobody has measured yet". The Python adapter
required exactly two numeric columns, which is coverage.py's non-branch table;
every project setting `branch = true` got four, matched nothing, and could
never satisfy a coverage floor at all (BUG_011). The rule is therefore to
anchor on what the adapter does own — the source extension — and to read a
table ROW rather than a column count: the name, then whatever numeric cells
this version emits, then the first percentage. Not "anything up to the first
percentage", which reads `pytest -v`'s per-test progress line as a
measurement; per `Hazard.can_overstate`, a reading that can come back HIGH is
the one a parser must refuse.

`carries_executable_code` is the other three-valued answer, and it is asked
only to explain a coverage row that is **absent**. Reporters emit nothing for
a file with no executable lines — a bare protocol, an enum of cases, a
typealias — and that silence is indistinguishable from a file the run never
measured. `True` and `False` are claims; `None` means "cannot tell", and a
caller deciding whether to *relax* must treat it as "do not relax", exactly as
with `git.branch_files`. The generic adapter knows no language, so it always
answers `None`. The two wrong answers are not symmetrical and an
implementation must prefer the first: calling an inert file live costs a
spurious block, which is visible and sends someone to look; calling a live
file inert passes unmeasured code silently.

## Backends for work items

The item model is fixed; storage is pluggable, so a project keeps whatever
format it already uses.

| Backend | Stores as | Notes |
|---|---|---|
| `markdown` | One file per item, frontmatter fields | Default; git-native, no tooling |
| `sphinx_needs` | Directives in a reStructuredText file | For projects already rendering a needs graph |
| `json` | One JSON document | For machine consumption |
| `none` | Nothing | Work-item domain disabled |

A backend implements read, write, list, transition-persist, `add` (a new item,
with an optional placement it honours or refuses) and `taken_ids` (every id it
has spent, closed store included). The lifecycle FSM, and the rules for what
`item new` may set, live above it in `domains/workitem`, so switching backends
does not change item semantics. The id scheme (`items.id_strategy`) is shared
by every backend in `core/items`, and reads `taken_ids`.

**A transition never drops or rewrites body text**, on any backend: it writes
the status and the history rows and nothing else the item holds. Each backend
carries a round-trip test on the ruled closure path that proves it — the
markdown body byte for byte, the JSON `body` value, and on `sphinx_needs` a
diff in which the status line is the only line that changes (IDEA_019). A
ruling's `ruling` and `evidence` keys travel in the history rows, so they are
kept wherever history is: in the markdown frontmatter and the JSON document,
and not yet on `sphinx_needs` (below).

### The ids a store has spent, and the closed store

`items.closed_path` declares a **closed store**: where a project keeps the
entries it has retired out of the open backlog, in the backend's own format —
a reStructuredText file for `sphinx_needs`, a JSON document for `json`, a
directory of item files for `markdown` — read by the same backend class. It is
unset by default, so a project that keeps one store grows no archive. Agreed
once for both of its readers, BUG_074 (c) and IDEA_019's retirement.

Set, the backend's `taken_ids()` is the union of the ids in both stores, and
both questions that need "has this id been spent" ask it: numeric and slug
minting (`next_id`), and the uniqueness check on an explicit id given to `item
new` (below). A retired id is therefore never minted again and never accepted
again. A declared closed store that does not exist is an **error**, not an
empty store: read as empty, a mistyped path would let minting hand out a retired
id with nothing to say the scan saw nothing.

Nothing writes the closed store yet. Reading it is the whole of what the key
does today; `item list`, `show` and `transition` still read the open store
alone, and the store is not one of the declared writable stores above.
Retiring an entry into it on a closing transition is still deferred — see the
state model, "Retirement is deferred" — and when it lands the key joins
`items.path` as a declared store.

### What `item new` writes

`item new` files an item in its machine's initial state, and every `--field`
it is given is either honoured or refused — never dropped, never written twice
(BUG_074 (a)):

```mermaid
graph TD
    F["item new --field k=v ..."] --> P{"each pair key=value,<br/>no key given twice?"}
    P -- no --> R1["refused, nothing filed"]
    P -- yes --> K{"key is status / type /<br/>title / residue / history?"}
    K -- yes --> R2["refused, naming the surface that sets it<br/>(status: item transition)"]
    K -- no --> I{"key is id?"}
    I -- no --> M["minted id from taken_ids()<br/>(open + closed store)"]
    I -- yes --> U{"well-formed, and unspent in<br/>taken_ids(), case-insensitively?"}
    U -- no --> R3["refused, naming the clash<br/>and the store holding it"]
    U -- yes --> X["explicit id replaces the minted one"]
    M --> A["backend.add(item, section, after)"]
    X --> A
    A --> S{"placement named?"}
    S -- "no" --> D["sphinx_needs: after the last entry of its type (BUG_006);<br/>markdown, json: saved as before"]
    S -- "yes, sphinx_needs" --> N{"names exactly one<br/>section / entry?"}
    N -- yes --> W["written at the end of that section's own content,<br/>or directly after that entry"]
    N -- no --> R4["refused, nothing filed"]
    S -- "yes, markdown / json" --> R5["refused: no sections to place in"]
```

`status` is refused rather than honoured because a status is reached through a
transition and its gates — `filed → reproduced` needs evidence a new item does
not carry — and `item new` choosing one would be a route around them. The
others name keys the item model already has its own surface for. The explicit
id's case-insensitive comparison is for the markdown backend, which names a
file after the id: on a case-insensitive filesystem `bug_001` and `BUG_001` are
one file, and the second save would overwrite the first.

### The `sphinx_needs` round trip

A reStructuredText backlog is usually written by hand, so the backend's
guarantee is about bytes, not just fields: **a save rewrites only the lines
holding a value that changed.** A transition replaces the `:status:` line (or
inserts one after `:id:` if there was none) and nothing else — a wrapped
option, its continuation indentation, inline roles, trailing whitespace, the
blank lines around the entry, neighbouring entries and the file's line endings
survive byte for byte. A changed field rewrites that option's lines, a removed
one deletes them, a new one is inserted after the last option; a changed body
replaces the body and keeps the separation from whatever follows. Re-rendering
an entry from its parsed fields is what reflowed wrapped values and dropped the
blank line before the next heading (BUG_065).

**A save never removes or rewrites a line outside the spans it means to
change.** The lines an edit replaces are exactly the lines the read assigned
to that option, the title or the body; a line the read cannot classify belongs
to none of them and is never touched. Before writing, the backend re-reads its
own result: if the entry is not found again, the entry count moved, or the
title, status or a field would read back differently from the item being
saved, it refuses with an error and the file is unchanged — a stray line is
worse than no write. An item without an id is refused outright. A value or
title holding a line break is written on one line, joined the way a re-read
joins a wrapped value.

Reading follows indentation rather than leading characters. The option block
ends at the first blank line (BUG_007). Inside it, each line is one of three
things:

- **An option** — `:name:` at the option column, followed by the value. The
  space after the marker is optional (`:area:value` reads as `area` =
  `value`, as this backend always has), with one exception: a backtick right
  after the closing colon makes the line an inline role (`` :doc:`x` ``,
  `` :ref:`y` ``, `` :py:func:`z` ``), never an option.
- **A continuation** — any line indented deeper than the option column. It
  continues the line above it whatever it opens with, so a wrapped value
  whose next line begins with an inline role stays in that value.
- **Unclassified** — any other line at the option column (an inline role,
  stray prose). It joins no value and is preserved verbatim by every save,
  and so is a deeper line continuing it.

Lines are split on CRLF, a lone CR and LF, and each keeps its own terminator
on write, so a CR-only file stays CR-only and a file with mixed endings keeps
them line by line. A form feed, `\x85` and the Unicode line and paragraph
separators are not line breaks here: `str.splitlines` would split a title on
them and write it back joined by a newline that was never in the file.

A new entry lands after the last entry of its own type, separated from what
precedes and follows it by a blank line (BUG_006); with no entry of that type
it is appended to the end. That is the rule when the caller names no
placement, and it does not parse the document's structure.

A caller can name one (BUG_074 (b)): `--section "<title>"` puts the entry at
the end of the section with that title, of any level, and `--after <id>`
directly after that entry. A **title** is a line at column 0 underlined — and
optionally overlined with the same line — by one punctuation character repeated
at least as long as the title, docutils' own condition for a title it accepts
without complaint; an indented line never is, which keeps directive bodies out.
"The end of the section" is the end of its **own** content — after its last
non-blank line, before the next title of any level — because in
reStructuredText everything after a subsection's title belongs to that
subsection, and the wider reading would file the entry under whichever
subsection came last. A title or id that names nothing, a title two sections
share, and both options at once are refused with the file unchanged; none of
them falls back to the default, because an entry filed in the wrong section is
the commit-shaped repair job the option exists to remove. The markdown and
json backends have no sections and refuse either option.

**`history` is not persisted by this backend.** The directive has no
representation for a transition log yet, so `item.history_matches_status`
cannot be satisfied on a `sphinx_needs` project for any item past its initial
status, and an item's audit trail — a ruled closure's ruling and evidence
commit included — lives only in version control (BUG_073).
