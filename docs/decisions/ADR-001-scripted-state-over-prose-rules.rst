.. _adr-001:

ADR-001 — Scripted state over prose rules
=========================================

.. dec:: Scripted state over prose rules
   :id: DEC_ADR_001
   :status: accepted
   :affects: COMP_CORE_RULES, COMP_CORE_FSM, COMP_CORE_MANIFEST, COMP_CLI_MAIN

   - **Status:** Accepted
   - **Date:** 2026-08-08
   - **Supersedes:** nothing (foundational)

Context
-------

An agent setup that has accumulated real operational discipline ends up
encoding that discipline as **prose**: a ``CLAUDE.md`` full of MUST rules, a
``UserPromptSubmit`` hook that re-injects a pipeline reminder on every turn, and
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

Decision
--------

Replace prose-as-control-flow with **scripted state**.

The library persists an explicit state machine on disk. Work advances by
calling a CLI that validates and records each transition. Host hooks enforce a
configurable subset of those transitions and inject the *current state* into
context instead of a static rulebook.

The inversion, stated plainly:

   Instead of reminding the model of all the rules on every turn, tell it what
   state it is in, which transitions are legal from here, and which gate is
   currently pending.

This makes three things true that were not true before:

- **Compliance is an artifact.** The state file records what happened. A skipped
  gate is a visible fact, not an absence.
- **Context is bounded and relevant.** The injected text is derived from state,
  so it is short and about *now*, rather than long and about everything.
- **Controls are executable.** A gate is a function with a fixture proving it
  fails on a known-positive input. "Does the gate exist" becomes a test.

Scope: four state domains
-------------------------

+---------------+--------------------------+--------------------------+
| Domain        | What it governs          | Shape                    |
+===============+==========================+==========================+
| **Session**   | Isolation                | FSM                      |
|               | (worktree/branch),       |                          |
|               | naming, the push         |                          |
|               | barrier, teardown        |                          |
+---------------+--------------------------+--------------------------+
| **Pipeline**  | The methodology for one  | FSM, pluggable           |
|               | task: design → test →    |                          |
|               | implement → review →     |                          |
|               | commit                   |                          |
+---------------+--------------------------+--------------------------+
| **Work item** | Ideas, bugs, release     | FSM per item type        |
|               | rows and their statuses  |                          |
+---------------+--------------------------+--------------------------+
| **Memory**    | The knowledge store:     | Managed store +          |
|               | schema, dedup, generated | invariants               |
|               | index                    |                          |
+---------------+--------------------------+--------------------------+

The first three are genuine finite state machines. Memory is not — it is a
store with invariants over it — but it belongs in the same library because it
has the same failure mode: a hand-maintained index over hundreds of files
drifts, and drift is undetectable without a script that checks it.

Decisions recorded
------------------

Each row is a decision taken deliberately, with the alternative that was
rejected and why.

Packaging and distribution
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. dec:: Standalone repo, consumable as a git submodule and a published package
   :id: DEC_ADR_001_D1
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CLI_MAIN

   **Decision:** Standalone repo, consumable as a git submodule **and** a
   published package

   **Rejected alternative:** Build it inside the host project and extract later

   **Rationale:** Extraction-later reliably carries host-isms into the
   "generic" layer. A separate repo forces the seam to be real on day one.

.. dec:: PyPI, installed with pipx
   :id: DEC_ADR_001_D2
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CLI_MAIN

   **Decision:** PyPI, installed with ``pipx``

   **Rejected alternative:** npm; dual-publish; ``curl | sh``

   **Rationale:** The core is Python, so a Python channel is honest. npm would
   mean shipping Python inside a JS package. The submodule path covers projects
   that refuse a global install.

.. dec:: The name is invariant
   :id: DEC_ADR_001_D3
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CLI_MAIN

   **Decision:** The name is ``invariant``

   **Rejected alternative:** ``setsquare``, ``vitruvius``, ``masterplan``

   **Rationale:** It names the mechanism rather than a metaphor for it. The
   library's whole job is to hold invariants that do not drift between
   sessions. Verified unclaimed on the package index at the time of writing.


Architecture
~~~~~~~~~~~~

.. dec:: Bash shims, Python core
   :id: DEC_ADR_001_D4
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN

   **Decision:** Bash shims, Python core

   **Rejected alternative:** Pure bash; pure Node

   **Rationale:** Hooks must start fast and run with no build step, which
   favors bash. State logic needs real data structures, JSON, and a test suite,
   which bash makes painful. The split puts each where it is strong.

.. dec:: Methodologies are declarative state-machine files
   :id: DEC_ADR_001_D5
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_FSM, COMP_CORE_MACHINES, COMP_MACHINE_TDD

   **Decision:** Methodologies are **declarative** state-machine files

   **Rejected alternative:** Python plugin classes

   **Rationale:** Adopting a methodology should not require writing code. A
   methodology is a graph plus gates; YAML expresses that. Gates that need real
   logic name a rule id, which is the escape hatch.

.. dec:: Project specifics come from a manifest plus adapters
   :id: DEC_ADR_001_D6
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_MANIFEST, COMP_ADAPTERS_BASE, COMP_MANIFEST_FILE

   **Decision:** Project specifics come from a **manifest plus adapters**

   **Rejected alternative:** Manifest only; auto-detection

   **Rationale:** A manifest alone cannot parse an ecosystem's test output or
   know its coverage tooling. Auto-detection is convenient and surprising.
   Adapters carry ecosystem smarts; the manifest carries project facts and
   overrides.

.. dec:: Host-agnostic core, Claude Code as the first adapter
   :id: DEC_ADR_001_D7
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_CLI_MAIN

   **Decision:** Host-agnostic core, Claude Code as the first **adapter**

   **Rejected alternative:** Bind directly to Claude Code

   **Rationale:** The state engine has nothing to do with any particular agent
   host. Keeping hosts behind an interface costs little now and avoids a
   rewrite later.

.. dec:: Installed artifacts are thin shims that call the CLI
   :id: DEC_ADR_001_D8
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_HOSTS_CLAUDE_CODE, COMP_HOSTS_ASSETS

   **Decision:** Installed artifacts are **thin shims** that call the CLI

   **Rejected alternative:** Vendored copies; regenerated files

   **Rationale:** Vendored copies drift and create merge burden on upgrade.
   Regeneration destroys local edits. A shim has nothing to drift: logic lives
   in the versioned library, customization lives in the manifest.

.. dec:: Runtime state is local JSON, gitignored
   :id: DEC_ADR_001_D9
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_STORE

   **Decision:** Runtime state is **local JSON, gitignored**

   **Rejected alternative:** SQLite; committing work-item state

   **Rationale:** State is machine-local and session-scoped; committing it
   would produce constant conflicts between concurrent sessions. JSON stays
   hand-inspectable and hand-fixable when something wedges — which matters,
   because the recovery path for a corrupted state file is a human reading it.
   SQLite's transactions are not needed once writes are per-session files plus
   one locked registry.


Policy
~~~~~~

.. dec:: Enforcement is profiles plus per-rule overrides
   :id: DEC_ADR_001_D10
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_RULES, COMP_DATA_PROFILES, COMP_CORE_MANIFEST

   **Decision:** Enforcement is **profiles plus per-rule overrides**

   **Rejected alternative:** Named profiles only; per-rule only

   **Rationale:** Profiles make the common case one line. Per-rule overrides
   mean a project that disagrees with exactly one rule does not have to abandon
   the preset.

.. dec:: Enforcement is hybrid: block a critical few, advise the rest
   :id: DEC_ADR_001_D11
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_RULES, COMP_DATA_PROFILES

   **Decision:** Enforcement is **hybrid**: block a critical few, advise the
   rest

   **Rejected alternative:** Block everything; advise everything

   **Rationale:** Blocking everything fights legitimate exceptions and trains
   the user to disable the tool. Advising everything is what prose already
   does, and is the problem being solved.

.. dec:: Docs-first is core and always on
   :id: DEC_ADR_001_D12
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_RULES, COMP_CORE_MACHINES

   **Decision:** **Docs-first is core and always on**

   **Rejected alternative:** Ship it as an opt-in profile; leave it to the host
   project

   **Rationale:** This is the strongest opinion the library holds, taken
   deliberately. A project without architecture docs is told to create them
   rather than allowed to skip the gate. The rationale: dependency knowledge
   derived from file search is guesswork that goes stale silently, whereas a
   maintained diagram is checkable. Projects that reject this premise are not
   the audience for this library.

.. dec:: The privacy gate ships enabled with configurable patterns
   :id: DEC_ADR_001_D13
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_PRIVACY, COMP_CORE_RULES

   **Decision:** The **privacy gate ships enabled** with configurable patterns

   **Rejected alternative:** Ship it disabled; leave it project-local

   **Rationale:** A redaction gate that is off by default protects nobody,
   because the people who need it most do not know to look for it. Defaults
   cover home paths, emails, addresses, tokens, clock times, and links; a
   project extends or trims the set.

.. dec:: Four agents move into the library; host-specific agents stay local
   :id: DEC_ADR_001_D14
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_DATA_AGENTS

   **Decision:** Four agents move into the library; host-specific agents stay
   local

   **Rejected alternative:** Move all; move none

   **Rationale:** Design, review, work-item intake, and repro-proving are
   generic once paths and commands come from the manifest. A publisher agent
   bound to one app store is not. Supporting both proves the library composes
   with project-local agents rather than replacing them.

.. dec:: Library agents are ported near-verbatim, with paths parameterized
   :id: DEC_ADR_001_D15
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_DATA_AGENTS, COMP_CORE_AGENTS

   **Decision:** Library agents are **ported near-verbatim**, with paths
   parameterized

   **Rejected alternative:** Rewrite generically from scratch

   **Rationale:** The existing agent prose encodes years of specific lessons —
   the exact ways a check can pass vacuously, the exact ways a subagent's
   "green" can be a lie. A clean rewrite would produce tidier text and quietly
   drop that knowledge.

.. dec:: Agent provenance is a comment inside the frontmatter, and loadability is checked separately from currency
   :id: DEC_ADR_001_D16
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_HOSTS_ASSETS, COMP_CORE_RULES

   **Decision:** Agent provenance is a comment **inside** the frontmatter, and
   **loadability is checked separately from currency**

   **Rejected alternative:** Keep the marker above the frontmatter; drop the
   marker; hold provenance in a sidecar file

   **Rationale:** Shipped as a header above the frontmatter, the marker pushed
   the block off line 1 and the host skipped all four agents silently — while
   ``verify`` reported success, because what it checked for was the marker that
   caused the skip. The lesson generalises past this bug: a check that looks
   for the artifact of an installation is not a check that the installation
   works. The two questions are now separate rows in ``doctor``, and neither
   may stand in for the other.

.. dec:: Agent-role gates are advisory in every profile, strict included
   :id: DEC_ADR_001_D17
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_RULES, COMP_DATA_PROFILES

   **Decision:** Agent-role gates are **advisory in every profile**, ``strict``
   included

   **Rejected alternative:** Block in ``strict``; omit the gates entirely

   **Rationale:** Installing an agent and dispatching one are different events,
   so the gates are worth having. But the engine can prove an agent *ran*,
   never that one was *needed* — that is a judgement, and ``strict`` already
   keeps its heuristics advisory on the grounds that a blocking heuristic is a
   false positive with authority. A gate that legitimate one-line work must
   override routinely is how ``--force`` becomes a reflex on the gates that do
   matter.

.. dec:: Publishing is optional and off by default; a session ends at the merge, with its worktree and branch removed
   :id: DEC_ADR_001_D18
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_DOMAINS_SESSION, COMP_MACHINE_SESSION, COMP_CORE_RULES

   **Decision:** **Publishing is optional and off by default**; a session ends
   at the merge, with its worktree *and branch* removed

   **Rejected alternative:** Keep the push mandatory; drop the push state
   entirely

   **Rationale:** A session's job is to get reviewed work onto the base branch.
   Worktrees share one object database, so peers see that work the moment it
   lands — a push is never needed for sharing, only for publishing, and
   publishing costs a CI run and is a decision about the project rather than
   about whichever session happened to finish last. Making it mandatory put
   that decision in the hands of an agent and produced a barrier that blocked
   commits it could never let through. Held by a gate on the ``pushed`` state
   rather than by deleting the edge, so enabling it is one manifest line and
   never a forked state machine. Teardown now deletes the branch too, with
   ``-d`` and never ``-D``: a safe delete refuses a branch whose commits are
   not in the base, and that refusal is evidence the merge did not do what it
   appeared to. **One clause, added by ADR-004: this says WHERE a session ends,
   not WHEN.** Read beside D19's "one host session, one invariant session" it
   implied the merge happens in the sitting that started the session. It does
   not. The merge happens when the deliverable set the session recorded is
   done, which may be several host sessions later;
   ``session.deliverables_complete`` is what holds the landing there until it
   is.

.. dec:: A host session and an invariant session are bound 1:1, automatically
   :id: DEC_ADR_001_D19
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_DOMAINS_SESSION, COMP_HOSTS_CLAUDE_CODE

   **Decision:** A host session and an invariant session are bound **1:1**,
   automatically

   **Rejected alternative:** Leave them independent; bind by a flag the caller
   passes

   **Rationale:** Two namespaces drifted, and the drift was visible as an
   ambiguity banner offering six candidates to a caller for whom five were
   provably wrong. A flag only binds when someone remembers it, which is
   exactly when it is least likely to happen. ``PostToolUse`` has the host
   identifier and brackets the call that registered the session, so the binding
   is made where both halves are in hand. Enforced both ways, because a silent
   rebind hands a pipeline and a file set to an owner that never did the work.
   Bound host-to-session and never host-to-worktree: registering without
   isolating is legitimate, and conflating them would have invalidated every
   session that owns no code. **Scope refinement (ADR-004): 1:1 at any moment,
   N over a session's lifetime.** Everything above is about a binding that is
   LIVE, and of that it still holds — replacing one is refused without
   ``--force``, because there is an incumbent whose pipeline and file set would
   change hands behind its back. A binding whose host has ENDED is SPENT:
   ``SessionEnd`` fired, and there is nobody on the other end to take anything
   away from. Claiming a spent binding is a continuation rather than a
   hand-over, and ``session resume`` is the named route for it. The scope went
   unstated because until a session could outlive its host there was no second
   case; stating it now rather than leaving the row to be read against the
   shipped code is D16's own lesson — a rule the codebase does not keep is
   worse than a weaker one it does, because a reader enforcing it would
   "correct" the working fix by reverting it.

.. dec:: An advisory gate can be acknowledged with a stated reason, once per pipeline; a blocking one cannot
   :id: DEC_ADR_001_D20
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_DOMAINS_PIPELINE, COMP_CORE_RULES

   **Decision:** An advisory gate can be **acknowledged with a stated reason**,
   once per pipeline; a blocking one cannot

   **Rejected alternative:** Let a session silence gates by config; leave the
   warning repeating

   **Rationale:** A warning that repeats forever cannot tell "considered and
   declined" from "never read" — that is the real defect, more than the noise.
   An acknowledgement costs an explicit act, records a reason visible in
   ``pipeline status``, and dies with the pipeline so it cannot drift into
   project policy. Refused for blocking gates because allowing it would make
   ``block`` and ``warn`` the same level spelled differently; a blocking gate
   is satisfied, or overridden on the transition where the override is recorded
   against that transition rather than standing for the run.

.. dec:: The coverage path is wired end to end, and a scope mismatch fails rather than passes
   :id: DEC_ADR_001_D21
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_RULES, COMP_DOMAINS_PIPELINE, COMP_ADAPTERS_BASE

   **Decision:** The coverage path is **wired end to end**, and a scope
   mismatch **fails** rather than passes

   **Rejected alternative:** Wire only the missing command; drop the gate

   **Rationale:** ``coverage.meets_floor`` blocked on evidence nothing could
   produce: ``record_coverage`` had no callers, ``parse_coverage`` was called
   only by its own tests, and the advice named a command no code path ran. A
   blocking gate that cannot be satisfied does not enforce a standard, it
   teaches people to reach for ``--force``. Wiring it exposed two more:
   ``coverage_scope: touched`` was configured and never implemented, and
   reporters emit absolute paths where git emits relative ones — so a naive fix
   would have produced an empty intersection read as "nothing below the floor",
   a gate passing vacuously, which is worse than the blocking it replaced. No
   overlap is therefore a failure that names the mismatch.

.. dec:: A hazard declares whether it can bend a measurement upward; unclassified means it can
   :id: DEC_ADR_001_D22
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_ADAPTERS_BASE, COMP_CORE_RULES

   **Decision:** A hazard declares whether it can bend a measurement
   **upward**; unclassified means it can

   **Rejected alternative:** Treat all hazards alike; annotate everything

   **Rationale:** Bent low and bent high are not symmetrical failures. A
   measurement bent low blocks work that was fine — visible, annoying,
   self-correcting, because being blocked is what sends someone to look. A
   measurement bent high passes work that was never covered, silently, and
   nobody looks. So the first is annotated into the verdict and the second
   blocks. The default for an unclassified hazard is "can overstate", because a
   hazard added without thought would otherwise inherit "annotate" and go
   quiet.

.. dec:: Coverage paths resolve against the session's worktree first, with suffix matching behind it, and an …
   :id: DEC_ADR_001_D23
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_RULES, COMP_DOMAINS_PIPELINE

   **Decision:** Coverage paths resolve against the **session's worktree**
   first, with suffix matching behind it, and an unmatched changed file
   **fails**

   **Rejected alternative:** Resolve against the repository root only; match
   leniently and pass on what matched

   **Rationale:** An isolated session has two roots: state lives in the main
   checkout because worktrees share one ``.git``, while compilation happens in
   the worktree, so measured paths carry the worktree prefix while hooks run
   from the checkout. Stripping the root the rule happens to see removes
   nothing and the intersection is empty for a reason unrelated to coverage —
   invisible in a single-checkout repo, which is why it survived until a
   session isolated. Suffix matching backs it up, and is deliberately paired
   with failing on a partial intersection: a loose matcher's bug produces
   partial overlaps rather than empty ones, and answering only for the files it
   matched would report a pass for the ones it never found.

.. dec:: The privacy exemption marker is block-scoped when it owns its line, and line-scoped when it does not
   :id: DEC_ADR_001_D24
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_PRIVACY

   **Decision:** The privacy **exemption marker is block-scoped** when it owns
   its line, and line-scoped when it does not

   **Rejected alternative:** Keep it same-line only; let it exempt the next
   line; let it exempt the rest of the file or section

   **Rationale:** Same-line only is unusable in the markup the gate mostly
   scans: ``privacy.scope`` defaults to prose, and reStructuredText and
   Markdown have no inline comment, so the marker had to be written into the
   prose and rendered into the published output — six of them shipped that way
   in one consuming project. The next line is not enough either, because RST
   will not let a comment stand next to what it covers: mid-paragraph it splits
   the paragraph, between list items it ends the list, and the real cases are
   mid-block every time. The rest of the file or section was rejected for the
   reason the hatch is dangerous at all — a marker is where a real leak gets
   parked, so its cover must be something a reviewer reading the diff can see
   the end of. Two adjacent blocks is the smallest reach that covers the shapes
   prose actually takes, and exempting a whole file costs a marker on every
   block of it.

.. dec:: The operator is the final authority, and may override any rule temporarily or permanently. Every gate …
   :id: DEC_ADR_001_D25
   :status: accepted
   :part_of: DEC_ADR_001
   :affects: COMP_CORE_RULES, COMP_CORE_MANIFEST, COMP_CMD_RULES

   **Decision:** **The operator is the final authority, and may override any
   rule temporarily or permanently.** Every gate carries a route past it, and
   every route past it is recorded

   **Rejected alternative:** Leave the position as behaviour and infer it; make
   the engine the last word on a project it does not own

   **Rationale:** This was the strongest opinion in the codebase that had never
   been written down, and the cost of that was measurable: the override
   machinery was complete on the surface reached through the CLI and absent on
   the surface that actually stops an agent. Twelve rules evaluate at hooks,
   and a blocking verdict there was final — no flag, no marker, no prompt, no
   recorded exception — while ``--force --reason`` existed on every transition.
   Nothing caught it, because there was no stated position for a review to
   check a feature against; the doctrine existed as one sentence about one gate
   ("Every gate needs an escape hatch for the case it gets wrong") and as a
   pattern a reader had to infer from ``--force --reason``, fail-open hooks,
   and "any of these can be dropped to warn or off". Stated as a decision, it
   has three parts, and each has a mechanism. **Permanently**: profiles pin
   nothing and ``enforcement.rules`` overrides every level — and an override
   naming no rule is REFUSED rather than discarded, because a control handed
   back that silently does nothing is worse than one never offered.
   **Temporarily**: ``ask`` puts the human in the loop where there is one,
   ``invariant override`` passes one call at a hook, ``invariant standdown``
   holds every gate for an interval. **Entirely**: ``invariant uninstall`` is
   the inverse of ``init``, because the authority to leave is part of the
   authority. Two constraints bound it rather than qualify it — every route
   past a gate is RECORDED, since an override that leaves no trace is
   indistinguishable from the gate never having run; and ``privacy.clean`` is
   the one gate a blanket route must NAME to pass, because nothing un-writes an
   identifier out of a published history.


Consequences
------------

**Good.** Compliance becomes inspectable. Context injection shrinks and stays
relevant. Gates become testable units with fixtures. A second project gets the
whole discipline by writing one manifest. The host project's setup stops being
a bespoke pile of scripts and becomes a configuration of a versioned library.

**Costs.** There is now a dependency between the host project and this library,
and a version skew hazard with it. Two languages are maintained instead of one.
The state store is a new thing that can wedge, so it needs recovery commands
(``invariant state show``, ``invariant state repair``) as first-class surface, not
afterthoughts.

**Accepted risk.** A state machine can be *wrong* — it can insist on a
transition that does not fit a real situation. The mitigation is D10, D11 and
D25 together: any rule can be dropped to ``warn`` or ``off`` in the manifest
without forking the library, passed once at the surface it fires on, or held
for an interval. A rule that a project keeps overriding is a bug report against
the default, and the override — recorded, always — is the evidence.

D25 is the one to check a new feature against, and it is written down because
it was not. The failure it names is not "a gate was too strict"; it is a gate
shipped with **no route past it at all**, on a surface nobody thought of as a
surface. The question to ask of anything that can refuse work is: what does the
operator type to proceed anyway, and where does that decision get written down?
A feature with no answer to the second half is as much a defect as one with no
answer to the first — a silent hole and a locked door are the two ways to take
authority away, and this library must ship neither.

Notes
-----

The library is expected to dogfood every rule it ships: its own repository
carries a manifest, runs its own pipeline, and its own architecture docs are
subject to the docs-first gate it enforces on others. A control the library
will not run against itself is a control it should not ship.
